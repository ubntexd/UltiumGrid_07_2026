"""Étage 3 — stop dur, circuit breaker journalier, panic close, alertes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Any, Callable

from sqlalchemy.orm import Session

from ultiumgrid.connector.binance_futures import BinanceFuturesClient
from ultiumgrid.db.models import AlertEvent, utcnow
from ultiumgrid.engine.config import StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class GuardState:
    daily_pnl: float = 0.0
    day: date | None = None
    hard_stop_triggered: bool = False
    circuit_breaker_triggered: bool = False
    panic: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)


class SafetyGuards:
    def __init__(
        self,
        client: BinanceFuturesClient,
        session: Session,
        cfg: StrategyConfig,
        on_alert: Callable[[str, str, str, dict | None], None] | None = None,
    ):
        self.client = client
        self.session = session
        self.cfg = cfg
        self.state = GuardState(day=datetime.now(timezone.utc).date())
        self.on_alert = on_alert or self._default_alert

    def _default_alert(self, level: str, kind: str, message: str, payload: dict | None = None) -> None:
        ev = AlertEvent(level=level, kind=kind, message=message, payload_json=payload)
        self.session.add(ev)
        self.session.commit()
        logger.log(
            logging.CRITICAL if level == "critical" else logging.WARNING if level == "warn" else logging.INFO,
            "[%s] %s: %s",
            level,
            kind,
            message,
        )
        self.state.events.append({"level": level, "kind": kind, "message": message, "payload": payload})

    def reset_day_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self.state.day != today:
            self.state.day = today
            self.state.daily_pnl = 0.0
            self.state.circuit_breaker_triggered = False

    def add_realized(self, pnl: float) -> None:
        self.reset_day_if_needed()
        self.state.daily_pnl += pnl

    def check_hard_stop(self, entry_avg: float, mark_price: float, position_qty: float) -> bool:
        """Stop dur sur position/PnL RÉELS uniquement (jamais reconstruction théorique grille)."""
        if position_qty == 0 or entry_avg <= 0:
            return False
        pct = ((mark_price - entry_avg) / entry_avg) * 100.0
        if position_qty < 0:
            pct = -pct
        if pct <= self.cfg.hard_stop_pct:
            self.state.hard_stop_triggered = True
            self.on_alert(
                "critical",
                "hard_stop",
                f"Stop dur déclenché: pnl%={pct:.3f} seuil={self.cfg.hard_stop_pct}",
                {"pct": pct, "entry_avg": entry_avg, "mark_price": mark_price, "qty": position_qty},
            )
            return True
        return False

    def check_circuit_breaker(self) -> bool:
        self.reset_day_if_needed()
        if self.state.daily_pnl <= self.cfg.daily_circuit_breaker_usd:
            self.state.circuit_breaker_triggered = True
            self.on_alert(
                "critical",
                "circuit_breaker",
                f"Circuit breaker journalier: daily_pnl={self.state.daily_pnl} seuil={self.cfg.daily_circuit_breaker_usd}",
                {"daily_pnl": self.state.daily_pnl},
            )
            return True
        return False

    def panic_close(self, bag_manager, grid_engine) -> dict[str, Any]:
        """Clôture immédiate de la position RÉELLE (lecture positionRisk juste avant)."""
        self.state.panic = True
        self.on_alert("critical", "panic_close", "Panic close demandé", None)
        symbol = grid_engine.cfg.symbol
        # Lecture fraîche — ignore l'état théorique / paliers incomplets en DB
        try:
            positions_before = self.client.position_risk(symbol)
        except Exception as exc:
            positions_before = []
            logger.error("panic positionRisk failed: %s", exc)
        real_before = sum(float(p.get("positionAmt", 0)) for p in positions_before)
        grid_result = grid_engine.close_cycle()
        bags_closed = []
        for bag in list(bag_manager.open_bags()):
            try:
                bags_closed.append(bag_manager.sell_bag(bag.id, order_type="MARKET"))
            except Exception as exc:
                logger.error("panic sell bag %s: %s", bag.id, exc)
        try:
            positions_after = self.client.position_risk(symbol)
            real_after = sum(float(p.get("positionAmt", 0)) for p in positions_after)
        except Exception:
            positions_after = []
            real_after = None
        return {
            "grid": grid_result,
            "bags": bags_closed,
            "position_before": real_before,
            "position_after": real_after,
            "positions_before_raw": positions_before,
            "at": utcnow().isoformat(),
        }
