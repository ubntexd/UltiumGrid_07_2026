"""Surveillance position résiduelle après Stop + sécurisation Start (tests réels)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "supervisor"))
load_dotenv(ROOT / ".env", override=True)

from ultiumgrid.bot_runner import BotRunner, build_client_from_env  # noqa: E402
from ultiumgrid.db.models import BotState, make_session_factory, utcnow  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.grid import GridEngine  # noqa: E402
from ultiumgrid.engine.orphan_position import (  # noqa: E402
    UntrackedInventoryError,
    entry_avg_from_my_trades,
    orphan_qty,
    resolve_entry_avg_existing,
    residual_position_warning,
)
from ultium_supervisor.models import SupervisorAlert  # noqa: E402
from ultium_supervisor.watchdog import Watchdog  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"


def _mock_trades_fifo():
    return [
        {"isBuyer": True, "qty": "0.02", "price": "62000", "time": 3},
        {"isBuyer": True, "qty": "0.03", "price": "63000", "time": 2},
        {"isBuyer": False, "qty": "0.01", "price": "64000", "time": 1},
    ]


def test_entry_avg_from_my_trades_fifo():
    client = MagicMock()
    client.my_trades.return_value = _mock_trades_fifo()
    avg, src = entry_avg_from_my_trades(client, "BTCUSDT", 0.04)
    assert src == "myTrades_fifo"
    assert 62000 < avg < 63000
    proof = {"avg": avg, "source": src, "qty": 0.04}
    (PROOFS / "m3_orphan_C1_mytrades_fifo.json").write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof, indent=2))


def test_resolve_entry_avg_prior_bot_state():
    client = MagicMock()
    avg, src = resolve_entry_avg_existing(client, "BTCUSDT", 0.05, 65000.0, prior_entry_avg=62800.0)
    assert src == "prior_bot_state_entry_avg"
    assert avg == 62800.0
    client.my_trades.assert_not_called()


def test_resolve_entry_avg_untracked_raises_C2():
    client = MagicMock()
    client.my_trades.return_value = []
    with pytest.raises(UntrackedInventoryError):
        resolve_entry_avg_existing(client, "BTCUSDT", 0.05, 65000.0, prior_entry_avg=None)


@pytest.mark.integration
def test_supervisor_orphan_alert_A1(db_url):
    """A1 — alerte orphan_position_unwatched après délai avec position réelle."""
    os.environ["ORPHAN_STOPPED_MIN_S"] = "0"
    os.environ["ORPHAN_MIN_NOTIONAL_USDT"] = "10"

    client = build_client_from_env()
    symbol = os.getenv("SYMBOL", "BTCUSDT")
    base = float(client.base_asset_qty(symbol))
    mark = float(client.ticker_price(symbol)["price"])
    notional = base * mark
    if notional < 10:
        pytest.skip(f"Solde base insuffisant pour test A1: {base} (~{notional:.2f} USDT)")

    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    stopped = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    session.add(
        BotState(
            key="main",
            value_json={
                "running": False,
                "stopped_at": stopped,
                "config": StrategyConfig(symbol=symbol).to_dict(),
                "grid": {"position_qty": base, "entry_avg": 62000.0, "active": False},
                "guards": {},
            },
        )
    )
    session.commit()

    wd = Watchdog(db_url)
    wd.check_orphan_position()

    alerts = (
        wd.SessionLocal()
        .query(SupervisorAlert)
        .filter(
            SupervisorAlert.kind == "orphan_position_unwatched",
            SupervisorAlert.status == "active",
        )
        .all()
    )
    account = client.capital_snapshot(symbol)
    proof = {
        "test": "A1_orphan_alert_after_stop",
        "alerts": [
            {"kind": a.kind, "severity": a.severity, "message": a.message, "payload": a.payload_json}
            for a in alerts
        ],
        "account_snapshot": account,
        "binance_base": base,
        "mark": mark,
        "stopped_at": stopped,
    }
    (PROOFS / "m3_orphan_A1_alert.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    assert len(alerts) >= 1
    assert alerts[0].severity == "alert"
    p = alerts[0].payload_json or {}
    assert p.get("orphan_qty", 0) > 0
    assert p.get("notional_usdt", 0) >= 10
    session.close()


@pytest.mark.integration
def test_supervisor_no_orphan_alert_A2(db_url):
    """A2 — pas d'alerte si position nulle ou sous seuil."""
    os.environ["ORPHAN_STOPPED_MIN_S"] = "0"
    os.environ["ORPHAN_MIN_NOTIONAL_USDT"] = "10"

    client = build_client_from_env()
    symbol = os.getenv("SYMBOL", "BTCUSDT")
    base = float(client.base_asset_qty(symbol))

    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    stopped = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    session.add(
        BotState(
            key="main",
            value_json={
                "running": False,
                "stopped_at": stopped,
                "config": StrategyConfig(symbol=symbol).to_dict(),
                "grid": {"position_qty": 0.0, "entry_avg": 0.0, "active": False},
                "guards": {},
            },
        )
    )
    session.commit()

    wd = Watchdog(db_url)
    wd.check_orphan_position()

    alerts = (
        wd.SessionLocal()
        .query(SupervisorAlert)
        .filter(
            SupervisorAlert.kind == "orphan_position_unwatched",
            SupervisorAlert.status == "active",
        )
        .all()
    )
    proof = {
        "test": "A2_no_alert_zero_position",
        "binance_base": base,
        "active_orphan_alerts": len(alerts),
        "resolved": base * float(client.ticker_price(symbol)["price"]) < 10,
    }
    (PROOFS / "m3_orphan_A2_no_alert.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    if base * float(client.ticker_price(symbol)["price"]) < 10:
        assert len(alerts) == 0
    session.close()


@pytest.mark.integration
def test_stop_residual_warning_B1(db_url):
    """B1 — stop() renvoie residual_position_warning si position significative."""
    client = build_client_from_env()
    symbol = os.getenv("SYMBOL", "BTCUSDT")
    base = float(client.base_asset_qty(symbol))
    mark = float(client.ticker_price(symbol)["price"])
    if base * mark < 10:
        pytest.skip(f"Pas assez de BTC pour B1: {base}")

    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    cfg = StrategyConfig(symbol=symbol, capital_usdt=100, num_levels=4, bnb_fee_discount=False)
    bot = BotRunner(client, session, cfg)
    bot.engine.state.active = True
    bot.engine.state.entry_avg = 62000.0
    bot.engine.state.position_qty = base
    bot.running = True
    bot.cycle_id = None
    result = bot.stop()

    proof = {
        "test": "B1_stop_residual_warning",
        "stop_result": result,
        "account_base": base,
        "mark": mark,
    }
    (PROOFS / "m3_orphan_B1_stop_warning.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    assert "residual_position_warning" in result
    w = result["residual_position_warning"]
    assert w["qty"] > 0
    assert w["notional_usdt"] >= 10
    session.close()


@pytest.mark.integration
def test_start_skipped_buy_uses_prior_entry_C1(db_url):
    """C1 — open_grid saute l'achat mais utilise prior_entry_avg, pas center_price."""
    client = build_client_from_env()
    symbol = os.getenv("SYMBOL", "BTCUSDT")
    base = float(client.base_asset_qty(symbol))
    mark = float(client.ticker_price(symbol)["price"])
    if base * mark < 50:
        pytest.skip(f"Stock insuffisant pour C1: {base}")

    cfg = StrategyConfig(symbol=symbol, capital_usdt=5000, num_levels=20)
    engine = GridEngine(client, cfg)
    prior = 61234.56
    state = engine.open_grid(prior_entry_avg=prior)
    proof = {
        "test": "C1_start_prior_entry_avg",
        "entry_avg": state.entry_avg,
        "center_price": float(state.center_price),
        "initial_buy": state.initial_buy,
        "prior_entry_avg": prior,
    }
    (PROOFS / "m3_orphan_C1_start_entry.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    assert state.initial_buy and state.initial_buy.get("skipped")
    assert state.entry_avg == prior
    assert state.entry_avg != float(state.center_price) or prior == float(state.center_price)


@pytest.mark.integration
def test_start_blocked_untracked_C2(db_url):
    """C2 — Start refuse stock sans historique traçable."""
    client = MagicMock()
    client.get_symbol_filters.return_value = build_client_from_env().get_symbol_filters("BTCUSDT")
    client.balance_free.return_value = "0.05"
    client.ticker_price.return_value = {"price": "65000"}
    client.my_trades.return_value = []
    client.place_order = MagicMock()

    cfg = StrategyConfig(symbol="BTCUSDT", capital_usdt=5000, num_levels=20)
    engine = GridEngine(client, cfg)
    with pytest.raises(UntrackedInventoryError):
        engine.open_grid(prior_entry_avg=0.0)

    SessionLocal, _ = make_session_factory(db_url)
    session = SessionLocal()
    bot = BotRunner(client, session, cfg)
    bot.running = False
    bot.engine.state.active = False
    result = bot.start()
    proof = {
        "test": "C2_start_blocked_untracked",
        "start_result": result,
    }
    (PROOFS / "m3_orphan_C2_blocked.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    assert result.get("blocked") is True
    assert result.get("error") == "untracked_inventory"
    session.close()


@pytest.fixture
def db_url(tmp_path):
    p = ROOT / "data" / "test_orphan_position.db"
    p.parent.mkdir(exist_ok=True)
    if p.exists():
        p.unlink()
    url = f"sqlite:///{p}"
    make_session_factory(url)
    return url
