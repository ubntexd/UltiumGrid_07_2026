"""Étage 2 — coupe progressive paliers 10 / 14 + réarmement."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from ultiumgrid.engine.config import StrategyConfig
from ultiumgrid.engine.grid import GridEngine

logger = logging.getLogger(__name__)


@dataclass
class CutState:
    armed: bool = True
    last_cut_level: int | None = None
    last_cut_at: datetime | None = None
    lowest_level_reached: int = -1
    recovery_levels: int = 0
    pending_rearm_after: datetime | None = None
    cuts: list[dict[str, Any]] = field(default_factory=list)


class ProgressiveCutManager:
    def __init__(self, engine: GridEngine, cfg: StrategyConfig):
        self.engine = engine
        self.cfg = cfg
        self.state = CutState()

    def observe_level(self, buy_level_index: int) -> None:
        """buy_level_index : index du niveau BUY le plus profond fillé (0 = plus bas)."""
        # On mappe : plus l'index est bas, plus on est profond.
        # Palier k = nombre de niveaux BUY fillés depuis le centre.
        mid = self.cfg.num_levels // 2
        depth = mid - buy_level_index  # 1..mid
        if depth > self.state.lowest_level_reached:
            self.state.lowest_level_reached = depth
            self.state.recovery_levels = 0
        elif depth < self.state.lowest_level_reached:
            self.state.recovery_levels = self.state.lowest_level_reached - depth

    def check_rearm(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if self.state.armed:
            return True
        if self.state.recovery_levels >= self.cfg.rearm_levels:
            self.state.armed = True
            self.state.pending_rearm_after = None
            logger.info("Rearm by recovery levels=%s", self.state.recovery_levels)
            return True
        if self.state.pending_rearm_after and now >= self.state.pending_rearm_after:
            self.state.armed = True
            self.state.pending_rearm_after = None
            logger.info("Rearm by delay")
            return True
        return False

    def evaluate(self, position_qty: float, entry_avg: float) -> dict[str, Any] | None:
        """Retourne une action de coupe si déclenchée."""
        self.check_rearm()
        if not self.state.armed:
            return None
        depth = self.state.lowest_level_reached
        if depth >= self.cfg.cut_level_2 and self.state.last_cut_level != self.cfg.cut_level_2:
            qty = abs(position_qty) * (self.cfg.cut_pct_2 / 100.0)
            return self._cut(level=self.cfg.cut_level_2, qty=qty, entry=entry_avg, pct=self.cfg.cut_pct_2)
        if depth >= self.cfg.cut_level_1 and (
            self.state.last_cut_level is None or self.state.last_cut_level < self.cfg.cut_level_1
        ):
            qty = abs(position_qty) * (self.cfg.cut_pct_1 / 100.0)
            return self._cut(level=self.cfg.cut_level_1, qty=qty, entry=entry_avg, pct=self.cfg.cut_pct_1)
        return None

    def _cut(self, level: int, qty: float, entry: float, pct: float) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        action = {
            "level": level,
            "qty": qty,
            "entry_price": entry,
            "pct": pct,
            "at": now.isoformat(),
        }
        self.state.last_cut_level = level
        self.state.last_cut_at = now
        self.state.armed = False
        self.state.pending_rearm_after = now + timedelta(minutes=self.cfg.rearm_delay_min)
        self.state.cuts.append(action)
        logger.info("Cut triggered: %s", action)
        return action
