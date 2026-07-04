"""Étage 2 — coupe progressive paliers 10 / 14 + réarmement.

La détection de franchissement de palier s'appuie sur le PRIX de marché
(WebSocket / ticker), jamais sur la présence d'un ordre au niveau.

La quantité coupée = % de la position RÉELLE (positionRisk), jamais théorique.
"""

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

    def observe_mark_price(self, mark_price: float) -> int:
        """Profondeur atteinte selon le prix (indépendant des ordres placés).

        BUY levels : index 0..mid-1, prix croissant avec l'index.
        Si mark <= price(level_i), le palier i est franchi en baisse.
        depth = mid - i pour le plus bas i franchi.
        """
        mid = self.cfg.num_levels // 2
        depth = 0
        for lv in self.engine.state.levels:
            if lv.index >= mid:
                continue
            if mark_price <= float(lv.price):
                depth = max(depth, mid - lv.index)
        if depth > self.state.lowest_level_reached:
            self.state.lowest_level_reached = depth
            self.state.recovery_levels = 0
        elif 0 < depth < self.state.lowest_level_reached:
            self.state.recovery_levels = self.state.lowest_level_reached - depth
        return self.state.lowest_level_reached

    def observe_level(self, buy_level_index: int) -> None:
        """Compat : profondeur depuis un index BUY fillé."""
        mid = self.cfg.num_levels // 2
        depth = mid - buy_level_index
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

    def evaluate(
        self,
        real_position_qty: float,
        entry_avg: float,
        incomplete_indices: list[int] | None = None,
    ) -> dict[str, Any] | None:
        """Coupe sur position RÉELLE uniquement.

        incomplete_indices : paliers grid_level_incomplete au moment de la coupe.
        """
        self.check_rearm()
        if not self.state.armed:
            return None
        incomplete_indices = incomplete_indices or []
        depth = self.state.lowest_level_reached
        theoretical = self.engine.theoretical_buy_qty()

        if depth >= self.cfg.cut_level_2 and self.state.last_cut_level != self.cfg.cut_level_2:
            return self._cut(
                level=self.cfg.cut_level_2,
                real_qty=abs(real_position_qty),
                pct=self.cfg.cut_pct_2,
                entry=entry_avg,
                incomplete_indices=incomplete_indices,
                theoretical_qty=theoretical,
            )
        if depth >= self.cfg.cut_level_1 and (
            self.state.last_cut_level is None or self.state.last_cut_level < self.cfg.cut_level_1
        ):
            return self._cut(
                level=self.cfg.cut_level_1,
                real_qty=abs(real_position_qty),
                pct=self.cfg.cut_pct_1,
                entry=entry_avg,
                incomplete_indices=incomplete_indices,
                theoretical_qty=theoretical,
            )
        return None

    def _cut(
        self,
        level: int,
        real_qty: float,
        pct: float,
        entry: float,
        incomplete_indices: list[int],
        theoretical_qty: float,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        # Ne jamais couper plus que la position réelle
        qty = min(real_qty * (pct / 100.0), real_qty)
        theoretical_cut = theoretical_qty * (pct / 100.0) if theoretical_qty > 0 else 0.0
        gap_pct = 0.0
        if theoretical_cut > 0:
            gap_pct = abs(theoretical_cut - qty) / theoretical_cut * 100.0

        action: dict[str, Any] = {
            "level": level,
            "qty": qty,
            "real_qty_available": real_qty,
            "theoretical_qty": theoretical_qty,
            "theoretical_cut": theoretical_cut,
            "gap_pct": gap_pct,
            "entry_price": entry,
            "pct": pct,
            "at": now.isoformat(),
            "incomplete_levels": list(incomplete_indices),
            "tag": "cut_with_incomplete_grid" if incomplete_indices else "cut",
            "alert_gap": gap_pct > 10.0 and bool(incomplete_indices),
        }
        self.state.last_cut_level = level
        self.state.last_cut_at = now
        self.state.armed = False
        self.state.pending_rearm_after = now + timedelta(minutes=self.cfg.rearm_delay_min)
        self.state.cuts.append(action)
        logger.info("Cut triggered: %s", action)
        return action
