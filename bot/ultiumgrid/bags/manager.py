"""Système de sacs — registres virtuels + réconciliation Binance.

Quantités toujours RÉELLES (positionRisk / transfert mesuré), jamais théoriques.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ultiumgrid.connector.binance_spot import BinanceSpotClient
from ultiumgrid.db.models import Bag, utcnow
from ultiumgrid.engine.config import StrategyConfig

logger = logging.getLogger(__name__)


class BagManager:
    def __init__(self, client: BinanceSpotClient, session: Session, cfg: StrategyConfig):
        self.client = client
        self.session = session
        self.cfg = cfg
        self.last_reconciliation: dict[str, Any] | None = None

    def create_bag(
        self,
        quantity: float,
        entry_price: float,
        cut_level: int | None,
        source: str = "cut",
        incomplete_levels: list[int] | None = None,
    ) -> Bag:
        """quantity = quantité RÉELLE transférée (jamais théorique grille pleine)."""
        bag = Bag(
            symbol=self.cfg.symbol,
            quantity=quantity,
            entry_price=entry_price,
            status="open",
            source=source,
            cut_level=cut_level,
        )
        self.session.add(bag)
        self.session.commit()
        self.session.refresh(bag)
        if incomplete_levels:
            logger.info(
                "Bag %s créé avec qty réelle=%s (paliers incomplets=%s)",
                bag.id,
                quantity,
                incomplete_levels,
            )
        return bag

    def open_bags(self, symbol: str | None = None) -> list[Bag]:
        symbol = symbol or self.cfg.symbol
        return (
            self.session.query(Bag)
            .filter(Bag.symbol == symbol, Bag.status == "open")
            .all()
        )

    def bags_qty(self, symbol: str | None = None) -> float:
        return sum(b.quantity for b in self.open_bags(symbol))

    def sell_bag(self, bag_id: int, order_type: str = "MARKET", limit_price: float | None = None) -> dict[str, Any]:
        bag = self.session.get(Bag, bag_id)
        if not bag or bag.status != "open":
            raise ValueError(f"Bag {bag_id} introuvable ou déjà fermé")
        side = "SELL"
        kwargs: dict[str, Any] = {
            "symbol": bag.symbol,
            "side": side,
            "order_type": order_type.upper(),
            "quantity": bag.quantity,
            "purpose": "bag_sell",
        }
        if order_type.upper() == "LIMIT":
            if limit_price is None:
                raise ValueError("limit_price requis pour LIMIT")
            kwargs["price"] = limit_price
        order = self.client.place_order(**kwargs)
        fill_price = float(order.get("avgPrice") or order.get("price") or limit_price or bag.entry_price)
        if fill_price == 0:
            fill_price = float(self.client.ticker_price(bag.symbol)["price"])
        bag.realized_pnl = (fill_price - bag.entry_price) * bag.quantity
        bag.status = "closed"
        bag.closed_at = utcnow()
        self.session.commit()
        return {"bag": bag, "order": order}

    def reconcile(self, grid_position_qty: float, symbol: str | None = None) -> dict[str, Any]:
        """solde base réel = sacs + grille active — quantités RÉELLES uniquement."""
        symbol = symbol or self.cfg.symbol
        try:
            binance_qty = self.client.base_asset_qty(symbol)
        except Exception as exc:
            result = {
                "symbol": symbol,
                "status": "reconciliation_unavailable",
                "at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "ok": False,
            }
            self.last_reconciliation = result
            logger.warning("reconciliation_unavailable: %s", result)
            return result

        bags_qty = self.bags_qty(symbol)
        expected = bags_qty + grid_position_qty
        delta = binance_qty - expected
        ok = abs(delta) < 1e-8
        result = {
            "symbol": symbol,
            "status": "ok" if ok else "mismatch",
            "binance_qty": binance_qty,
            "bags_qty": bags_qty,
            "grid_qty": grid_position_qty,
            "expected": expected,
            "delta": delta,
            "ok": ok,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.last_reconciliation = result
        if not ok:
            logger.warning("Reconciliation mismatch: %s", result)
        return result

    def bags_capital_ratio(self, available_quote: float, mark_price: float) -> float:
        """Capital immobilisé en sacs (valeur quote) / capital quote disponible, en %."""
        notional = self.bags_qty() * mark_price
        if available_quote <= 0:
            return 100.0
        return (notional / available_quote) * 100.0

    def should_reduce_grid(self, available_quote: float, mark_price: float) -> bool:
        return (
            self.bags_capital_ratio(available_quote, mark_price)
            >= self.cfg.bags_capital_threshold_pct
        )
