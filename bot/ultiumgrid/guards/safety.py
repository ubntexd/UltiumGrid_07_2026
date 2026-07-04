"""Étage 3 — stop dur, circuit breaker journalier, panic close, alertes."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Any, Callable

from sqlalchemy.orm import Session

from ultiumgrid.connector.binance_spot import BinanceSpotClient
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
        client: BinanceSpotClient,
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
        """Vend la quantité base RÉELLE (lecture balances juste avant)."""
        self.state.panic = True
        self.on_alert("critical", "panic_close", "Panic close demandé", None)
        symbol = grid_engine.cfg.symbol
        filters = self.client.get_symbol_filters(symbol)
        try:
            real_before = self.client.balance_total(filters.base_asset)
            free_before = self.client.balance_free(filters.base_asset)
        except Exception as exc:
            real_before = 0.0
            free_before = 0.0
            logger.error("panic account balances failed: %s", exc)
        # Annuler ordres puis vendre tout le free base
        grid_engine.cancel_all_grid_orders()
        sold = []
        if free_before >= float(filters.min_qty):
            try:
                sold.append(
                    self.client.place_order(
                        symbol=symbol,
                        side="SELL",
                        order_type="MARKET",
                        quantity=free_before,
                        purpose="panic_close",
                    )
                )
            except Exception as exc:
                logger.error("panic sell failed: %s", exc)
        bags_closed = []
        for bag in list(bag_manager.open_bags()):
            bag.status = "closed"
            bag.closed_at = utcnow()
            bags_closed.append({"bag_id": bag.id, "note": "closed_via_panic_full_sell"})
        bag_manager.session.commit()
        grid_engine.state.active = False
        grid_engine.state.position_qty = 0.0
        try:
            real_after = self.client.balance_total(filters.base_asset)
        except Exception:
            real_after = None
        return {
            "sold_orders": sold,
            "bags": bags_closed,
            "base_before": real_before,
            "base_after": real_after,
            "at": utcnow().isoformat(),
        }
