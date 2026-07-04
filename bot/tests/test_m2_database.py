"""Tests Module 2 — persistance réelle vérifiée par SQL direct."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from ultiumgrid.db.models import Bag, BotState, Cycle, Trade, make_session_factory  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"
PROOFS.mkdir(parents=True, exist_ok=True)
DB_URL = f"sqlite:///{ROOT / 'data' / 'test_m2.db'}"


@pytest.mark.integration
def test_db_insert_read_update_via_sql():
    Path(ROOT / "data").mkdir(exist_ok=True)
    db_path = ROOT / "data" / "test_m2.db"
    if db_path.exists():
        db_path.unlink()

    SessionLocal, engine = make_session_factory(DB_URL)
    session = SessionLocal()

    cycle = Cycle(symbol="BTCUSDT", status="open", center_price=62500.0, grid_profit=1.5)
    session.add(cycle)
    session.commit()
    session.refresh(cycle)
    cycle_id = cycle.id

    trade = Trade(cycle_id=cycle_id, symbol="BTCUSDT", side="BUY", price=62000.0, quantity=0.01)
    bag = Bag(symbol="BTCUSDT", quantity=0.05, entry_price=61000.0, status="open", cut_level=10)
    state = BotState(key="main", value_json={"running": False, "cycle_id": cycle_id})
    session.add_all([trade, bag, state])
    session.commit()

    # Vérification SQL directe (pas via ORM)
    with engine.connect() as conn:
        row = conn.execute(text("SELECT symbol, status, grid_profit FROM cycles WHERE id = :id"), {"id": cycle_id}).one()
        assert row[0] == "BTCUSDT"
        assert row[1] == "open"
        assert float(row[2]) == 1.5

        # Update
        conn.execute(text("UPDATE cycles SET status='closed', net_pnl=12.5 WHERE id=:id"), {"id": cycle_id})
        conn.commit()
        row2 = conn.execute(text("SELECT status, net_pnl FROM cycles WHERE id=:id"), {"id": cycle_id}).one()
        assert row2[0] == "closed"
        assert float(row2[1]) == 12.5

        trades = conn.execute(text("SELECT side, price, quantity FROM trades WHERE cycle_id=:id"), {"id": cycle_id}).all()
        bags = conn.execute(text("SELECT quantity, entry_price, status FROM bags")).all()
        states = conn.execute(text("SELECT key, value_json FROM bot_state WHERE key='main'")).all()

    proof = {
        "cycle_after_update": {"status": row2[0], "net_pnl": float(row2[1])},
        "trades": [list(t) for t in trades],
        "bags": [list(b) for b in bags],
        "bot_state": [{"key": s[0], "value_json": s[1]} for s in states],
    }
    (PROOFS / "m2_database_sql.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))

    assert trades[0][0] == "BUY"
    assert float(bags[0][0]) == 0.05
    assert states[0][0] == "main"
    session.close()
