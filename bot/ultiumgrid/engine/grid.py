"""Moteur de grille — étage 1.

Formules documentées pour reproductibilité :

- Niveaux arithmétiques (num_levels), centrés sur center_price :
  level_i = center_price * (1 - step_pct/100 * (num_levels/2 - i))
  pour i = 0..num_levels-1 (i < mid = BUY, i >= mid = SELL)

- Grid Profit = somme des PnL réalisés des paires buy/sell de la grille active
- Floating Profit = (mark_price - entry_avg) * position_qty  (signe selon sens)
- Funding PnL = cumul des funding payments observés sur la période du cycle
- Gross PnL = Grid Profit + Floating Profit + Funding PnL
- Déclenchement cycle si Gross PnL >= cycle_trigger_usd
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ultiumgrid.connector.binance_futures import BinanceFuturesClient, SymbolFilters
from ultiumgrid.engine.config import StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class GridLevel:
    index: int
    price: Decimal
    side: str  # BUY | SELL
    quantity: Decimal
    order_id: int | None = None
    status: str = "pending"  # pending|open|filled|cancelled


@dataclass
class GridState:
    symbol: str
    center_price: Decimal
    levels: list[GridLevel] = field(default_factory=list)
    grid_profit: float = 0.0
    floating_profit: float = 0.0
    funding_pnl: float = 0.0
    position_qty: float = 0.0
    entry_avg: float = 0.0
    active: bool = False
    deepest_buy_index: int = -1  # plus bas niveau BUY fillé (pour coupe)

    @property
    def gross_pnl(self) -> float:
        return self.grid_profit + self.floating_profit + self.funding_pnl


def compute_levels(
    center_price: Decimal,
    cfg: StrategyConfig,
    filters: SymbolFilters,
    qty_per_level: Decimal,
) -> list[GridLevel]:
    """20 niveaux arithmétiques, pas = step_pct %."""
    levels: list[GridLevel] = []
    n = cfg.num_levels
    mid = n // 2
    step = Decimal(str(cfg.step_pct)) / Decimal("100")
    for i in range(n):
        # i=0 plus bas, i=n-1 plus haut
        offset = i - mid + (Decimal("0.5") if n % 2 == 0 else 0)
        # offset négatif sous le centre
        price = center_price * (Decimal("1") + step * Decimal(offset))
        price = filters.round_price(price)
        side = "BUY" if i < mid else "SELL"
        qty = filters.round_qty(qty_per_level)
        levels.append(GridLevel(index=i, price=price, side=side, quantity=qty))
    return levels


def qty_per_level(cfg: StrategyConfig, center_price: Decimal, filters: SymbolFilters) -> Decimal:
    """Répartit le capital notionnel (capital * levier) sur les niveaux BUY."""
    notional = Decimal(str(cfg.capital_usdt)) * Decimal(str(cfg.leverage))
    buy_levels = max(cfg.num_levels // 2, 1)
    per = notional / Decimal(buy_levels) / center_price
    qty = filters.round_qty(per)
    # Respect MIN_NOTIONAL
    if qty * center_price < filters.min_notional:
        qty = filters.round_qty(filters.min_notional / center_price * Decimal("1.05"))
    if qty < filters.min_qty:
        qty = filters.min_qty
    return qty


class GridEngine:
    def __init__(self, client: BinanceFuturesClient, cfg: StrategyConfig):
        self.client = client
        self.cfg = cfg
        self.state = GridState(symbol=cfg.symbol, center_price=Decimal("0"))

    def open_grid(self, center_price: Decimal | None = None) -> GridState:
        symbol = self.cfg.symbol
        filters = self.client.get_symbol_filters(symbol)
        if center_price is None:
            center_price = Decimal(self.client.ticker_price(symbol)["price"])
        center_price = filters.round_price(center_price)
        try:
            self.client.set_leverage(symbol, self.cfg.leverage)
        except Exception as exc:
            logger.warning("set_leverage failed: %s", exc)

        qty = qty_per_level(self.cfg, center_price, filters)
        levels = compute_levels(center_price, self.cfg, filters, qty)
        self.state = GridState(symbol=symbol, center_price=center_price, levels=levels, active=True)

        for level in levels:
            try:
                order = self.client.place_order(
                    symbol=symbol,
                    side=level.side,
                    order_type="LIMIT",
                    quantity=level.quantity,
                    price=level.price,
                )
                level.order_id = int(order["orderId"])
                level.status = "open"
            except Exception as exc:
                logger.error("place level %s failed: %s", level.index, exc)
                level.status = "error"
        return self.state

    def cancel_all_grid_orders(self) -> None:
        symbol = self.cfg.symbol
        try:
            self.client.cancel_all_orders(symbol)
        except Exception as exc:
            logger.warning("cancel_all_orders: %s", exc)
        for level in self.state.levels:
            if level.status == "open":
                level.status = "cancelled"
                level.order_id = None

    def sync_open_orders(self) -> list[dict]:
        return self.client.open_orders(self.cfg.symbol)

    def update_floating(self, mark_price: float) -> float:
        qty = self.state.position_qty
        entry = self.state.entry_avg
        if qty == 0 or entry == 0:
            self.state.floating_profit = 0.0
        else:
            # Long si qty > 0
            self.state.floating_profit = (mark_price - entry) * qty
        return self.state.floating_profit

    def on_fill(self, level_index: int, fill_price: float, fill_qty: float) -> GridLevel | None:
        """Après fill : place l'ordre opposé au niveau adjacent."""
        levels = self.state.levels
        if level_index < 0 or level_index >= len(levels):
            return None
        level = levels[level_index]
        level.status = "filled"
        filters = self.client.get_symbol_filters(self.cfg.symbol)

        # MAJ position
        signed_qty = fill_qty if level.side == "BUY" else -fill_qty
        prev_qty = self.state.position_qty
        new_qty = prev_qty + signed_qty
        if prev_qty == 0:
            self.state.entry_avg = fill_price
        elif (prev_qty > 0 and signed_qty > 0) or (prev_qty < 0 and signed_qty < 0):
            # ajout même sens
            self.state.entry_avg = (
                abs(prev_qty) * self.state.entry_avg + fill_qty * fill_price
            ) / (abs(prev_qty) + fill_qty)
        # réduction : PnL réalisé
        elif prev_qty != 0 and ((prev_qty > 0 and signed_qty < 0) or (prev_qty < 0 and signed_qty > 0)):
            closed = min(abs(prev_qty), fill_qty)
            direction = 1 if prev_qty > 0 else -1
            realized = direction * (fill_price - self.state.entry_avg) * closed
            self.state.grid_profit += realized

        self.state.position_qty = new_qty
        if level.side == "BUY":
            self.state.deepest_buy_index = max(self.state.deepest_buy_index, level_index)

        # Replacement : BUY fillé → SELL au-dessus ; SELL fillé → BUY en-dessous
        if level.side == "BUY" and level_index + 1 < len(levels):
            target = levels[level_index + 1]
            if target.status in ("pending", "cancelled", "filled", "error"):
                self._place_level(target, "SELL", filters)
                return target
        elif level.side == "SELL" and level_index - 1 >= 0:
            target = levels[level_index - 1]
            if target.status in ("pending", "cancelled", "filled", "error"):
                self._place_level(target, "BUY", filters)
                return target
        return None

    def _place_level(self, level: GridLevel, side: str, filters: SymbolFilters) -> None:
        level.side = side
        level.price = filters.round_price(level.price)
        try:
            order = self.client.place_order(
                symbol=self.cfg.symbol,
                side=side,
                order_type="LIMIT",
                quantity=level.quantity,
                price=level.price,
            )
            level.order_id = int(order["orderId"])
            level.status = "open"
        except Exception as exc:
            logger.error("replacement level %s failed: %s", level.index, exc)
            level.status = "error"

    def should_close_cycle(self) -> bool:
        return self.state.gross_pnl >= self.cfg.cycle_trigger_usd

    def close_cycle(self) -> dict[str, Any]:
        """Ferme toutes les positions grille (market reduce) et annule ordres."""
        self.cancel_all_grid_orders()
        symbol = self.cfg.symbol
        positions = self.client.position_risk(symbol)
        closed = []
        for pos in positions:
            amt = float(pos.get("positionAmt", 0))
            if amt == 0:
                continue
            side = "SELL" if amt > 0 else "BUY"
            ps = pos.get("positionSide") or ("LONG" if amt > 0 else "SHORT")
            try:
                order = self.client.place_order(
                    symbol=symbol,
                    side=side,
                    order_type="MARKET",
                    quantity=abs(amt),
                    position_side=ps if self.client.is_hedge_mode() else None,
                    purpose="cycle_close",
                    reduce_only=True,
                )
                closed.append(order)
            except Exception as exc:
                logger.error("close position failed: %s", exc)
        result = {
            "grid_profit": self.state.grid_profit,
            "floating_profit": self.state.floating_profit,
            "funding_pnl": self.state.funding_pnl,
            "gross_pnl": self.state.gross_pnl,
            "closed_orders": closed,
        }
        self.state.active = False
        self.state.position_qty = 0.0
        return result

    def levels_as_dict(self) -> list[dict]:
        return [
            {
                "index": lv.index,
                "price": str(lv.price),
                "side": lv.side,
                "quantity": str(lv.quantity),
                "order_id": lv.order_id,
                "status": lv.status,
            }
            for lv in self.state.levels
        ]
