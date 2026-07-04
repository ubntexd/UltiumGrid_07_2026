"""Module 3 — grille Spot réelle : placement, vérif croisée Binance↔DB, annulation."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env", override=True)

from ultiumgrid.bot_runner import BotRunner, build_client_from_env  # noqa: E402
from ultiumgrid.db.models import Cycle, make_session_factory  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"
PROOFS.mkdir(parents=True, exist_ok=True)


@pytest.mark.integration
def test_open_grid_cross_check_binance_db():
    client = build_client_from_env()
    # Mini-grille pour limiter le nombre d'ordres
    cfg = StrategyConfig(
        symbol="BTCUSDT",
        capital_usdt=100.0,
        num_levels=4,
        step_pct=0.5,
        cycle_trigger_usd=15.0,
    )
    db_path = ROOT / "data" / "test_m3_grid.db"
    db_path.parent.mkdir(exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    SessionLocal, engine = make_session_factory(f"sqlite:///{db_path}")
    session = SessionLocal()

    # Nettoyage préalable
    try:
        client.cancel_all_orders("BTCUSDT")
    except Exception:
        pass

    bot = BotRunner(client, session, cfg)
    result = bot.start()
    proof = {"start": result, "steps": []}

    assert result.get("ok") is True
    levels = bot.engine.levels_as_dict()
    proof["levels"] = levels

    open_ok = [lv for lv in levels if lv["status"] == "open"]
    incomplete = [lv for lv in levels if lv["status"] == "grid_level_incomplete"]
    proof["open_count"] = len(open_ok)
    proof["incomplete_count"] = len(incomplete)

    # Au moins un ordre placé (idéalement tous)
    assert len(open_ok) >= 1, f"aucun ordre placé: {levels}"

    # Vérif croisée Binance
    binance_orders = client.open_orders("BTCUSDT")
    binance_ids = {o["orderId"] for o in binance_orders}
    proof["binance_open_orders"] = binance_orders
    for lv in open_ok:
        assert lv["order_id"] in binance_ids, f"order_id {lv['order_id']} absent de Binance"

    # Vérif DB cycle
    cycles = session.query(Cycle).all()
    proof["db_cycles"] = [
        {"id": c.id, "status": c.status, "center_price": c.center_price, "symbol": c.symbol}
        for c in cycles
    ]
    assert len(cycles) == 1
    assert cycles[0].status == "open"
    assert cycles[0].symbol == "BTCUSDT"

    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, center_price FROM cycles WHERE id=:id"), {"id": cycles[0].id}).one()
    proof["sql_direct"] = {"status": row[0], "center_price": row[1]}
    assert row[0] == "open"

    # bot_state
    from ultiumgrid.db.models import BotState

    st = session.query(BotState).filter(BotState.key == "main").one()
    proof["bot_state_running"] = st.value_json.get("running")
    proof["bot_state_levels"] = len(st.value_json.get("grid", {}).get("levels") or [])

    # Fermeture propre : annuler tous les ordres grille
    bot.engine.cancel_all_grid_orders()
    bot.running = False
    bot.save_state()
    after = client.open_orders("BTCUSDT")
    proof["open_after_cancel"] = after
    # Nos order_ids ne doivent plus être ouverts
    remaining = [o for o in after if o["orderId"] in {lv["order_id"] for lv in open_ok}]
    proof["remaining_our_orders"] = remaining
    assert remaining == []

    (PROOFS / "m3_grid_integration.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    session.close()
