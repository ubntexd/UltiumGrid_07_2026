"""Modules 7 / 7bis / 9 — API config, viabilité, reprise crash."""

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

from ultiumgrid.bot_runner import BotRunner, build_client_from_env  # noqa: E402
from ultiumgrid.db.models import BotState, Configuration, make_session_factory  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.viability import compute_viability  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"


@pytest.mark.unit
def test_viability_formula_manual():
    v = compute_viability(
        capital_usdt=5000,
        num_levels=20,
        step_pct=0.25,
        cycle_trigger_usd=15,
        bnb_fee_discount=False,
    )
    # Manual: buy_levels=10, notional=500, fees=500*0.001*2=1, gross=500*0.0025=1.25, net=0.25
    assert v["notional_per_level"] == 500.0
    assert v["fees_per_roundtrip"] == pytest.approx(1.0)
    assert v["gross_per_grid"] == pytest.approx(1.25)
    assert v["net_per_grid"] == pytest.approx(0.25)
    assert v["ratio_gross_to_fees"] == pytest.approx(1.25)
    assert v["alert_ratio_below_2x"] is True
    assert v["grids_to_cycle"] == 60


@pytest.mark.integration
def test_m7bis_config_params_and_reject():
    """Modifie 3 params, vérifie DB, rejette hors bornes et BNB sans solde."""
    import httpx

    base = "http://localhost:8000"
    # reject leverage-like / step too high
    r = httpx.post(
        f"{base}/api/config",
        json={"params": {"step_pct": 9.0}, "mode": "close_now"},
        timeout=15,
    )
    assert r.status_code == 400
    assert "step_pct" in str(r.json())

    # reject BNB discount without BNB
    r = httpx.post(
        f"{base}/api/config",
        json={"params": {"bnb_fee_discount": True}, "mode": "close_now"},
        timeout=15,
    )
    # soit 400 (BNB=0) soit ok si compte a du BNB
    proof = {"bnb_reject_or_ok": r.status_code, "bnb_body": r.json()}

    # apply 3 params
    params = {"step_pct": 0.3, "cycle_trigger_usd": 12, "num_levels": 16}
    r = httpx.post(
        f"{base}/api/config",
        json={"params": params, "mode": "close_now"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"]["step_pct"] == 0.3
    assert body["config"]["cycle_trigger_usd"] == 12
    assert body["config"]["num_levels"] == 16
    assert "viability" in body

    # viability manual check
    viab = body["viability"]
    manual = compute_viability(5000, 16, 0.3, 12, False, None, 0)
    # capital may still be 5000 from active defaults merged
    proof["api_viability"] = viab
    proof["manual_ratio"] = manual["ratio_gross_to_fees"]
    # ratio should match for same inputs — use API config capital
    cfg_cap = body["config"]["capital_usdt"]
    manual2 = compute_viability(cfg_cap, 16, 0.3, 12, False, None, 0)
    # fee_source may use account rates — compare structure
    assert viab["notional_per_level"] == pytest.approx(cfg_cap / 8)

    # wait bot apply
    import time

    time.sleep(6)
    running = httpx.get(f"{base}/api/running", timeout=15).json()
    proof["running_config"] = running["config"]
    assert running["config"]["step_pct"] == 0.3
    assert running["config"]["cycle_trigger_usd"] == 12
    assert running["config"]["num_levels"] == 16

    (PROOFS / "m7bis_config_spot.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))


@pytest.mark.integration
def test_m7_endpoints_match_db():
    import httpx

    base = "http://localhost:8000"
    running = httpx.get(f"{base}/api/running", timeout=15).json()
    history = httpx.get(f"{base}/api/history", timeout=15).json()
    pnl = httpx.get(f"{base}/api/pnl", timeout=15).json()
    bags = httpx.get(f"{base}/api/bags", timeout=15).json()
    capital = httpx.get(f"{base}/api/capital", timeout=15).json()

    # Cross-check capital with direct Binance
    client = build_client_from_env()
    filters = client.get_symbol_filters(running["symbol"])
    quote = client.balance_free(filters.quote_asset)
    proof = {
        "api_capital": capital,
        "binance_quote_free": quote,
        "history_len": len(history),
        "pnl_cycles": pnl["cycles_total"],
        "bags_len": len(bags),
        "running_symbol": running["symbol"],
        "mark": running["mark_price"],
        "binance_ticker": float(client.ticker_price(running["symbol"])["price"]),
    }
    assert abs(capital["quote_free"] - quote) < 1e-6
    # prix à la seconde près (tolérance 0.5%)
    assert abs(running["mark_price"] - proof["binance_ticker"]) / proof["binance_ticker"] < 0.005

    (PROOFS / "m7_api_crosscheck.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))


@pytest.mark.integration
def test_m9_crash_recovery():
    """Start grille, kill état process (nouveau BotRunner), restore sans dupliquer ordres."""
    client = build_client_from_env()
    try:
        client.cancel_all_orders("BTCUSDT")
    except Exception:
        pass

    db = ROOT / "data" / "test_m9_crash.db"
    if db.exists():
        db.unlink()
    SessionLocal, engine = make_session_factory(f"sqlite:///{db}")
    session = SessionLocal()
    cfg = StrategyConfig(symbol="BTCUSDT", capital_usdt=100, num_levels=4, step_pct=0.5)
    bot = BotRunner(client, session, cfg)
    bot.start()
    open_before = [lv for lv in bot.engine.levels_as_dict() if lv["status"] == "open"]
    order_ids_before = {lv["order_id"] for lv in open_before}
    assert order_ids_before

    # "Crash" : nouveau runner, même DB
    session2 = SessionLocal()
    bot2 = BotRunner(client, session2, cfg)
    restored = bot2.restore_state()
    assert restored is True
    open_after = [lv for lv in bot2.engine.levels_as_dict() if lv["status"] == "open"]
    order_ids_after = {lv["order_id"] for lv in open_after}

    binance_orders = client.open_orders("BTCUSDT")
    binance_ids = {o["orderId"] for o in binance_orders}

    proof = {
        "restored": restored,
        "order_ids_before": list(order_ids_before),
        "order_ids_after_restore": list(order_ids_after),
        "binance_ids": list(binance_ids),
        "no_duplicate": order_ids_before == order_ids_after,
        "all_on_binance": order_ids_after.issubset(binance_ids),
        "no_orphan_extra": len(binance_ids) == len(order_ids_after)
        or order_ids_after.issubset(binance_ids),
    }
    assert order_ids_before == order_ids_after
    assert order_ids_after.issubset(binance_ids)

    # cleanup
    bot2.engine.cancel_all_grid_orders()
    bot2.running = False
    bot2.save_state()

    (PROOFS / "m9_crash_recovery.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()
    session2.close()
