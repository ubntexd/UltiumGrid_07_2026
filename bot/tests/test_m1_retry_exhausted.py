"""Tests retry_exhausted + grid_level_incomplete.

- unit : logique RetryExhaustedError / marquage palier
- integration (forcé) : 5× timeout simulés, vérifications réelles openOrders,
  journal DB, alerte, état bot, PnL/marge ignorant le palier manquant.
  Scénario explicitement FORCÉ (POST toujours -1007) — documenté comme tel.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env")

from ultiumgrid.bot_runner import BotRunner  # noqa: E402
from ultiumgrid.connector.binance_spot import (  # noqa: E402
    BinanceSpotClient,
    RetryExhaustedError,
    SymbolFilters,
)
from ultiumgrid.db.models import AlertEvent, make_session_factory  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.grid import GridEngine, GridLevel  # noqa: E402
from ultiumgrid.risk.cuts import ProgressiveCutManager  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"
PROOFS.mkdir(parents=True, exist_ok=True)


@pytest.mark.unit
def test_retry_exhausted_raises_and_logs():
    client = BinanceSpotClient(api_key="k", api_secret="s")
    client._hedge_mode = False
    client._filters_cache["BTCUSDT"] = SymbolFilters(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("50"),
        price_precision=1,
        quantity_precision=3,
    )
    ids = (f"id{i}" for i in range(10))
    client.new_client_order_id = lambda prefix="ug": next(ids)  # type: ignore

    def fake_raw(method, path, params=None, signed=False):
        if method == "POST":
            return 408, {"code": -1007, "msg": "timeout"}, '{"code":-1007}'
        raise AssertionError(path)

    client._raw_request = fake_raw  # type: ignore
    client.find_order_by_client_order_id = lambda s, c: (None, {"source": None})  # type: ignore

    import ultiumgrid.connector.binance_spot as mod

    original = mod.BACKOFF_BASE_S
    mod.BACKOFF_BASE_S = 0.001
    try:
        with pytest.raises(RetryExhaustedError) as ei:
            client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity="0.002",
                price="40000",
                grid_level=3,
                max_attempts=5,
            )
    finally:
        mod.BACKOFF_BASE_S = original

    assert ei.value.grid_level == 3
    outcomes = [a["outcome"] for a in client.attempt_log]
    assert outcomes.count("timeout_not_found") == 5
    assert outcomes[-1] == "retry_exhausted"
    assert client.attempt_log[-1]["request_json"]["grid_level"] == 3


@pytest.mark.unit
def test_cut_uses_real_qty_with_incomplete():
    cfg = StrategyConfig(num_levels=20, cut_level_1=10, cut_pct_1=50.0)
    engine = GridEngine(MagicMock(), cfg)
    engine.state.levels = [
        GridLevel(index=i, price=Decimal(60000 - i * 10), side="BUY" if i < 10 else "SELL", quantity=Decimal("0.01"))
        for i in range(20)
    ]
    engine.state.levels[2].status = "grid_level_incomplete"
    mgr = ProgressiveCutManager(engine, cfg)
    # mark bas → profondeur 10
    mid = 10
    mark = float(engine.state.levels[mid - 10].price)  # level 0
    mgr.observe_mark_price(mark)
    assert mgr.state.lowest_level_reached >= 10
    action = mgr.evaluate(real_position_qty=0.4, entry_avg=60000.0, incomplete_indices=[2])
    assert action is not None
    assert action["qty"] == 0.2  # 50% de 0.4 réel, pas théorique
    assert action["tag"] == "cut_with_incomplete_grid"
    assert action["theoretical_qty"] == pytest.approx(0.1)  # 10 * 0.01


@pytest.mark.integration
def test_retry_exhausted_forced_full_chain():
    """Scénario FORCÉ : POST toujours -1007 ; verify openOrders/allOrders RÉELS.

    Prouve : retry_exhausted en DB, grid_level_incomplete, alerte critique,
    incomplete dans status API, PnL/range ignorent le palier manquant.
    """
    from ultiumgrid.bot_runner import build_client_from_env

    db_path = ROOT / "data" / "test_retry_exhausted.db"
    db_path.parent.mkdir(exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    SessionLocal, engine_db = make_session_factory(f"sqlite:///{db_path}")
    session = SessionLocal()

    try:
        client = build_client_from_env()
        client.account()
    except Exception:
        pytest.skip("Clés Spot Testnet manquantes ou invalides")
    real_raw = client._raw_request
    real_find = client.find_order_by_client_order_id

    def forced_timeout_post(method, path, params=None, signed=False):
        if method == "POST" and path == "/api/v3/order":
            body = {
                "code": -1007,
                "msg": "Timeout waiting for response from backend server. Send status unknown; execution status unknown.",
            }
            return 408, body, json.dumps(body)
        # leverage etc. : laisser passer ou ignorer
            return 200, {"leverage": 5}, '{"leverage":5}'
        return real_raw(method, path, params, signed)

    client._raw_request = forced_timeout_post  # type: ignore
    # find reste RÉEL (openOrders / allOrders / get)

    import ultiumgrid.connector.binance_spot as mod

    original_backoff = mod.BACKOFF_BASE_S
    mod.BACKOFF_BASE_S = 0.01

    bot = BotRunner(client, session, StrategyConfig(num_levels=4, capital_usdt=200))
    # Ouvrir une mini-grille (4 paliers) — tous vont échouer en retry_exhausted
    try:
        bot.engine.open_grid()
    finally:
        mod.BACKOFF_BASE_S = original_backoff
        client._raw_request = real_raw  # type: ignore

    bot.save_state()
    proof: dict = {"scenario": "forced_post_always_1007", "steps": []}

    incomplete = [lv for lv in bot.engine.state.levels if lv.status == "grid_level_incomplete"]
    proof["steps"].append(
        {
            "action": "LEVELS_AFTER_OPEN",
            "levels": bot.engine.levels_as_dict(),
            "incomplete_count": len(incomplete),
        }
    )
    assert len(incomplete) == 4, "tous les paliers doivent être incomplets en scénario forcé"

    # Journal order_attempts
    with engine_db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT outcome, request_json, verify_json FROM order_attempts "
                "WHERE outcome IN ('retry_exhausted','timeout_not_found') ORDER BY id"
            )
        ).all()
    def _as_dict(val):
        if isinstance(val, str):
            return json.loads(val)
        return val or {}

    exhausted = [r for r in rows if r[0] == "retry_exhausted"]
    proof["steps"].append(
        {
            "action": "DB_ORDER_ATTEMPTS",
            "timeout_not_found": sum(1 for r in rows if r[0] == "timeout_not_found"),
            "retry_exhausted": len(exhausted),
            "sample_exhausted": [
                {
                    "outcome": r[0],
                    "request_json": _as_dict(r[1]),
                    "verify_json": _as_dict(r[2]),
                }
                for r in exhausted[:2]
            ],
        }
    )
    assert len(exhausted) >= 4
    req0 = _as_dict(exhausted[0][1])
    assert req0.get("grid_level") is not None
    assert req0.get("price") is not None
    assert req0.get("quantity") is not None
    assert req0.get("last_attempt_at") is not None

    # Alertes critiques
    alerts = session.query(AlertEvent).filter(AlertEvent.kind == "grid_level_incomplete").all()
    proof["steps"].append(
        {
            "action": "ALERTS",
            "count": len(alerts),
            "messages": [a.message for a in alerts],
        }
    )
    assert len(alerts) >= 4
    assert "non placé après 5 tentatives" in alerts[0].message

    # bot_state
    from ultiumgrid.db.models import BotState

    st = session.query(BotState).filter(BotState.key == "main").one()
    levels_state = st.value_json["grid"]["levels"]
    incomplete_state = [lv for lv in levels_state if lv["status"] == "grid_level_incomplete"]
    proof["steps"].append(
        {
            "action": "BOT_STATE",
            "incomplete_in_state": incomplete_state,
        }
    )
    assert len(incomplete_state) == 4

    # Status / PnL : paliers incomplets exclus du range placé
    status = bot.status()
    proof["steps"].append(
        {
            "action": "STATUS",
            "incomplete_count": status["grid"]["incomplete_count"],
            "range_low": status["grid"]["range_low"],
            "range_high": status["grid"]["range_high"],
            "position_qty": status["grid"]["position_qty"],
            "gross_pnl": status["grid"]["gross_pnl"],
        }
    )
    assert status["grid"]["incomplete_count"] == 4
    # Aucun palier placé → range null (pas de faux range théorique)
    assert status["grid"]["range_low"] is None
    assert status["grid"]["range_high"] is None
    # Position / PnL ne comptent pas les paliers manquants comme ouverts
    assert status["grid"]["position_qty"] == 0.0
    assert status["grid"]["gross_pnl"] == 0.0

    # UI badge : le frontend mappe status grid_level_incomplete → badge "non placé"
    app_js = (ROOT / "frontend" / "app.js").read_text()
    assert "badge-missing" in app_js
    assert "non placé" in app_js
    proof["steps"].append(
        {
            "action": "UI_BADGE_SOURCE",
            "badge_missing_in_app_js": True,
            "api_incomplete_levels": status["grid"]["incomplete_levels"],
        }
    )

    # openOrders réel toujours vide pour ces paliers
    opens = client.open_orders("BTCUSDT")
    proof["steps"].append({"action": "BINANCE_OPEN_ORDERS", "raw": opens})
    assert opens == [] or all(
        o.get("clientOrderId") not in [a.get("client_order_id") for a in client.attempt_log]
        for o in opens
    )

    (PROOFS / "m1_retry_exhausted.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()
