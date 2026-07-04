"""Tests unitaires Modules 4 et 6 — logique pure."""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.grid import GridEngine  # noqa: E402
from ultiumgrid.risk.cuts import ProgressiveCutManager  # noqa: E402
from ultiumgrid.guards.safety import SafetyGuards  # noqa: E402


def test_cut_at_level_10_and_14():
    cfg = StrategyConfig()
    engine = GridEngine(MagicMock(), cfg)
    mgr = ProgressiveCutManager(engine, cfg)
    mid = cfg.num_levels // 2
    # profondeur 10 : buy index = mid - 10
    mgr.observe_level(mid - 10)
    action = mgr.evaluate(position_qty=1.0, entry_avg=60000.0)
    assert action is not None
    assert action["level"] == 10
    assert action["qty"] == 0.5  # 50%
    assert mgr.state.armed is False

    # pas de re-coupe tant que non réarmé
    assert mgr.evaluate(1.0, 60000.0) is None

    # réarmement par délai
    mgr.state.pending_rearm_after = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert mgr.check_rearm() is True

    mgr.observe_level(mid - 14)
    action2 = mgr.evaluate(position_qty=0.5, entry_avg=60000.0)
    assert action2 is not None
    assert action2["level"] == 14
    assert action2["qty"] == 0.5  # 100% de 0.5


def test_hard_stop_and_circuit_breaker():
    cfg = StrategyConfig(hard_stop_pct=-8.0, daily_circuit_breaker_usd=-40.0)
    session = MagicMock()
    guards = SafetyGuards(MagicMock(), session, cfg, on_alert=lambda *a, **k: None)
    # -8% sous entrée
    assert guards.check_hard_stop(entry_avg=100.0, mark_price=92.0, position_qty=1.0) is True
    assert guards.check_hard_stop(entry_avg=100.0, mark_price=95.0, position_qty=1.0) is False
    guards.add_realized(-41.0)
    assert guards.check_circuit_breaker() is True
