"""Journal de trades + traçabilité sacs — tests réels."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env", override=True)

from ultiumgrid.db.models import (  # noqa: E402
    Bag,
    FeePaid,
    OrderAttempt,
    Trade,
    make_session_factory,
    utcnow,
)
from ultiumgrid.engine.trade_journal import (  # noqa: E402
    build_trade_journal_entries,
    classify_trade_row,
)

PROOFS = ROOT / "docs" / "proofs"


@pytest.fixture
def db_url(tmp_path):
    p = ROOT / "data" / "test_journal.db"
    if p.exists():
        p.unlink()
    url = f"sqlite:///{p}"
    make_session_factory(url)
    return url


def test_classify_trade_categories():
    forced = {"999"}
    assert classify_trade_row({"level_index": None}, forced) == "initial_inventory_buy"
    assert classify_trade_row({"level_index": 2, "order_id": "1"}, forced) == "grid_fill"
    assert classify_trade_row({"level_index": 2, "order_id": "999"}, forced) == "forced_sell_stuck_level"


def test_journal_roundtrip_pnl():
    trades = [
        {"id": 1, "cycle_id": 1, "side": "BUY", "level_index": 0, "price": 100.0, "quantity": 1.0, "order_id": "a", "created_at": "t1"},
        {"id": 2, "cycle_id": 1, "side": "SELL", "level_index": 1, "price": 101.0, "quantity": 1.0, "order_id": "b", "created_at": "t2"},
    ]
    entries = build_trade_journal_entries(trades, {}, set(), fee_rate=0.0)
    sell = [e for e in entries if e["side"] == "SELL"][0]
    assert sell["roundtrip_ref"] is not None
    assert sell["trade_pnl"] is not None
    assert sell["trade_pnl"] > 0


@pytest.mark.integration
def test_journal_count_matches_db(db_url):
    SessionLocal, engine = make_session_factory(db_url)
    session = SessionLocal()
    session.add(
        Trade(
            cycle_id=1,
            symbol="BTCUSDT",
            side="BUY",
            price=62000.0,
            quantity=0.01,
            order_id="111",
            level_index=None,
        )
    )
    session.add(
        Trade(
            cycle_id=1,
            symbol="BTCUSDT",
            side="BUY",
            price=61900.0,
            quantity=0.01,
            order_id="222",
            level_index=0,
        )
    )
    session.add(
        Trade(
            cycle_id=1,
            symbol="BTCUSDT",
            side="SELL",
            price=62100.0,
            quantity=0.01,
            order_id="333",
            level_index=1,
        )
    )
    session.add(
        FeePaid(
            symbol="BTCUSDT",
            order_id="222",
            trade_id="t222",
            commission=0.001,
            commission_asset="BNB",
            commission_usdt=0.5,
            cycle_id=1,
        )
    )
    session.commit()

    db_count = session.execute(text("SELECT COUNT(*) FROM trades WHERE cycle_id=1")).scalar()
    rows = session.query(Trade).filter(Trade.cycle_id == 1).all()
    trade_dicts = [
        {
            "id": t.id,
            "cycle_id": t.cycle_id,
            "symbol": t.symbol,
            "side": t.side,
            "price": t.price,
            "quantity": t.quantity,
            "order_id": t.order_id,
            "level_index": t.level_index,
            "created_at": t.created_at,
        }
        for t in rows
    ]
    fees = {"222": [{"commission_usdt": 0.5, "commission_asset": "BNB"}]}
    entries = build_trade_journal_entries(trade_dicts, fees, set())
    filtered = [e for e in entries if e["category"] == "grid_fill"]

    proof = {
        "test": "journal_count_matches_db",
        "db_count": db_count,
        "journal_total": len(entries),
        "grid_fill_count": len(filtered),
        "filter_cycle_1": len(entries),
    }
    (PROOFS / "m_journal_trades_filter.json").write_text(json.dumps(proof, indent=2, default=str))
    assert db_count == len(entries)
    assert len(filtered) == 2
    session.close()


@pytest.mark.integration
def test_bag_traceability_fields(db_url):
    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    bag = Bag(
        symbol="BTCUSDT",
        quantity=0.05,
        entry_price=61000.0,
        status="open",
        source="cut",
        cut_level=10,
        creation_reason="cut_level_10",
        cycle_id_origin=7,
        incomplete_levels_at_creation=[3, 4],
        market_price_at_creation=60800.0,
    )
    session.add(bag)
    session.commit()
    session.refresh(bag)

    from ultiumgrid.bags.manager import bag_to_dict

    d = bag_to_dict(bag)
    proof = {
        "test": "bag_traceability_fields",
        "bag": d,
        "sql": session.execute(text("SELECT creation_reason, cycle_id_origin, market_price_at_creation FROM bags WHERE id=:id"), {"id": bag.id}).fetchone(),
    }
    (PROOFS / "m_bags_traceability.json").write_text(json.dumps(proof, indent=2, default=str))
    assert d["creation_reason"] == "cut_level_10"
    assert d["cycle_id_origin"] == 7
    assert d["incomplete_levels_at_creation"] == [3, 4]
    assert d["market_price_at_creation"] == 60800.0
    session.close()
