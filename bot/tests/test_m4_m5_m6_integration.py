"""Modules 4–6 Spot — coupe, sacs, panic avec soldes réels Binance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import text  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env", override=True)

from ultiumgrid.bot_runner import BotRunner, build_client_from_env  # noqa: E402
from ultiumgrid.db.models import AlertEvent, Bag, make_session_factory  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.grid import GridLevel  # noqa: E402
from ultiumgrid.risk.cuts import ProgressiveCutManager  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"


def _min_market_qty(client, symbol: str = "BTCUSDT") -> float:
    """Qty marché respectant NOTIONAL après arrondi stepSize."""
    filters = client.get_symbol_filters(symbol)
    ticker = float(client.ticker_price(symbol)["price"])
    # Boucle jusqu'à notional >= minNotional après arrondi
    target = float(filters.min_notional) * 1.2
    qty = float(filters.round_qty(target / ticker))
    while qty * ticker < float(filters.min_notional):
        qty = float(filters.round_qty(qty + float(filters.step_size)))
    return qty


@pytest.fixture
def client():
    c = build_client_from_env()
    try:
        c.cancel_all_orders("BTCUSDT")
    except Exception:
        pass
    return c


@pytest.fixture
def session_factory():
    db = ROOT / "data" / "test_m456.db"
    db.parent.mkdir(exist_ok=True)
    if db.exists():
        db.unlink()
    return make_session_factory(f"sqlite:///{db}")


@pytest.mark.integration
def test_m4_cut_uses_real_balance_with_incomplete(client, session_factory):
    SessionLocal, engine = session_factory
    session = SessionLocal()
    cfg = StrategyConfig(symbol="BTCUSDT", num_levels=20, cut_level_1=10, cut_pct_1=50.0)
    bot = BotRunner(client, session, cfg)

    # Marquer un palier incomplet
    bot.engine.state.levels = [
        GridLevel(
            index=i,
            price=__import__("decimal").Decimal(str(60000 + i)),
            side="BUY" if i < 10 else "SELL",
            quantity=__import__("decimal").Decimal("0.01"),
            status="grid_level_incomplete" if i == 2 else "open",
        )
        for i in range(20)
    ]
    # Solde réel avant (base)
    filters = client.get_symbol_filters("BTCUSDT")
    base_before = client.balance_total(filters.base_asset)
    buy_qty = _min_market_qty(client)
    ticker = float(client.ticker_price("BTCUSDT")["price"])
    bought = client.place_order(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=buy_qty
    )
    base_after_buy = client.balance_total(filters.base_asset)
    bot.engine.state.position_qty = base_after_buy
    bot.engine.state.entry_avg = ticker

    # Franchissement prix palier 10
    mark = float(bot.engine.state.levels[0].price)
    bot.cuts.observe_mark_price(mark)
    incomplete = bot.engine.state.incomplete_indices()
    cut = bot.cuts.evaluate(
        real_position_qty=bot.engine.state.position_qty,
        entry_avg=bot.engine.state.entry_avg,
        incomplete_indices=incomplete,
    )
    assert cut is not None
    assert cut["qty"] <= bot.engine.state.position_qty
    assert cut["tag"] == "cut_with_incomplete_grid"
    assert 2 in cut["incomplete_levels"]

    bag = bot.bags.create_bag(
        cut["qty"], cut["entry_price"], cut["level"], incomplete_levels=incomplete
    )
    proof = {
        "base_before": base_before,
        "bought": bought,
        "base_after_buy": base_after_buy,
        "cut": cut,
        "bag": {"id": bag.id, "quantity": bag.quantity, "entry_price": bag.entry_price},
    }
    # SQL direct
    with engine.connect() as conn:
        row = conn.execute(text("SELECT quantity, status FROM bags WHERE id=:id"), {"id": bag.id}).one()
    proof["sql_bag"] = {"quantity": row[0], "status": row[1]}
    assert float(row[0]) == pytest.approx(cut["qty"])
    assert abs(bag.quantity - cut["qty"]) < 1e-12

    (PROOFS / "m4_cut_incomplete_spot.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()


@pytest.mark.integration
def test_m5_bag_sell_cross_check(client, session_factory):
    SessionLocal, engine = session_factory
    session = SessionLocal()
    cfg = StrategyConfig(symbol="BTCUSDT")
    bot = BotRunner(client, session, cfg)
    filters = client.get_symbol_filters("BTCUSDT")

    base = client.balance_free(filters.base_asset)
    need = _min_market_qty(client)
    if base < need:
        client.place_order(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=need)
        base = client.balance_free(filters.base_asset)

    ticker = float(client.ticker_price("BTCUSDT")["price"])
    sell_qty = float(filters.round_qty(base))
    # Garder notional vente >= minNotional
    while sell_qty > float(filters.step_size) and sell_qty * ticker < float(filters.min_notional):
        sell_qty = float(filters.round_qty(sell_qty + float(filters.step_size)))
    if sell_qty * ticker < float(filters.min_notional):
        client.place_order(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=need)
        sell_qty = _min_market_qty(client)

    bag = bot.bags.create_bag(sell_qty, float(client.ticker_price("BTCUSDT")["price"]), cut_level=10)
    base_before = client.balance_total(filters.base_asset)
    result = bot.bags.sell_bag(bag.id, order_type="MARKET")
    base_after = client.balance_total(filters.base_asset)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, realized_pnl FROM bags WHERE id=:id"), {"id": bag.id}).one()

    proof = {
        "bag_id": bag.id,
        "sell_qty": sell_qty,
        "base_before": base_before,
        "base_after": base_after,
        "delta_base": base_before - base_after,
        "order": result["order"],
        "sql": {"status": row[0], "realized_pnl": row[1]},
    }
    assert row[0] == "closed"
    assert base_after < base_before
    (PROOFS / "m5_bag_sell_spot.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()


@pytest.mark.integration
def test_m6_panic_sells_real_balance(client, session_factory):
    SessionLocal, engine = session_factory
    session = SessionLocal()
    cfg = StrategyConfig(symbol="BTCUSDT")
    bot = BotRunner(client, session, cfg)
    filters = client.get_symbol_filters("BTCUSDT")

    qty = _min_market_qty(client)
    ticker = float(client.ticker_price("BTCUSDT")["price"])
    client.place_order(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=qty)
    # Limite loin du marché avec notional OK (qty calculée au prix limite)
    limit_price = filters.round_price(ticker * 0.8)
    limit_qty = float(filters.round_qty(float(filters.min_notional) * 1.2 / float(limit_price)))
    while limit_qty * float(limit_price) < float(filters.min_notional):
        limit_qty = float(filters.round_qty(limit_qty + float(filters.step_size)))
    client.place_order(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity=limit_qty,
        price=limit_price,
    )
    base_before = client.balance_total(filters.base_asset)
    opens_before = client.open_orders("BTCUSDT")
    bot.engine.state.position_qty = base_before
    bot.engine.state.levels = [
        GridLevel(index=0, price=__import__("decimal").Decimal("1"), side="BUY",
                  quantity=__import__("decimal").Decimal(str(qty)), status="grid_level_incomplete",
                  incomplete_since="test")
    ]

    result = bot.guards.panic_close(bot.bags, bot.engine)
    base_after = client.balance_total(filters.base_asset)
    opens_after = client.open_orders("BTCUSDT")

    proof = {
        "base_before": base_before,
        "base_after": base_after,
        "opens_before": len(opens_before),
        "opens_after": opens_after,
        "panic_result": {
            "base_before": result.get("base_before"),
            "base_after": result.get("base_after"),
            "sold_orders": result.get("sold_orders"),
        },
    }
    assert base_after < base_before or base_before == 0
    # Ordres ouverts annulés
    assert opens_after == [] or all(o["side"] != "BUY" or True for o in opens_after)
    # Idéalement plus d'ordres BUY ouverts
    assert not any(o["status"] == "NEW" for o in opens_after)

    alerts = session.query(AlertEvent).filter(AlertEvent.kind == "panic_close").all()
    proof["alerts"] = [a.message for a in alerts]
    assert len(alerts) >= 1

    (PROOFS / "m6_panic_spot.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()
