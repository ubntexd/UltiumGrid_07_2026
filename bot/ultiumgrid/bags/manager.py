"""Système de sacs — registres virtuels + réconciliation Binance."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ultiumgrid.connector.binance_futures import BinanceFuturesClient
from ultiumgrid.db.models import Bag, utcnow
from ultiumgrid.engine.config import StrategyConfig

logger = logging.getLogger(__name__)


class BagManager:
    def __init__(self, client: BinanceFuturesClient, session: Session, cfg: StrategyConfig):
        self.client = client
        self.session = session
        self.cfg = cfg

    def create_bag(self, quantity: float, entry_price: float, cut_level: int | None, source: str = "cut") -> Bag:
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
            "position_side": "LONG" if self.client.is_hedge_mode() else None,
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
        """position Binance = sacs + grille active."""
        symbol = symbol or self.cfg.symbol
        positions = self.client.position_risk(symbol)
        binance_qty = 0.0
        for p in positions:
            # En hedge mode, sommer LONG (positif) et SHORT (négatif)
            amt = float(p.get("positionAmt", 0))
            binance_qty += amt
        bags_qty = self.bags_qty(symbol)
        expected = bags_qty + grid_position_qty
        delta = binance_qty - expected
        ok = abs(delta) < 1e-8
        result = {
            "symbol": symbol,
            "binance_qty": binance_qty,
            "bags_qty": bags_qty,
            "grid_qty": grid_position_qty,
            "expected": expected,
            "delta": delta,
            "ok": ok,
        }
        if not ok:
            logger.warning("Reconciliation mismatch: %s", result)
        return result

    def bags_margin_ratio(self, available_balance: float, mark_price: float) -> float:
        """Marge notionnelle des sacs / balance disponible, en %."""
        notional = self.bags_qty() * mark_price / max(self.cfg.leverage, 1)
        if available_balance <= 0:
            return 100.0
        return (notional / available_balance) * 100.0

    def should_reduce_grid(self, available_balance: float, mark_price: float) -> bool:
        return self.bags_margin_ratio(available_balance, mark_price) >= self.cfg.bags_margin_threshold_pct
