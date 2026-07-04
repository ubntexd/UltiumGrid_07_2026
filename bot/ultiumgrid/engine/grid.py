"""Moteur de grille — étage 1.

Formules documentées pour reproductibilité :

- Niveaux arithmétiques (num_levels), centrés sur center_price :
  level_i = center_price * (1 + step_pct/100 * offset)
  pour i = 0..num_levels-1 (i < mid = BUY, i >= mid = SELL)

- Grid Profit = somme des PnL réalisés des paires buy/sell de la grille active
- Floating Profit = (mark_price - entry_avg) * position_qty  (solde base RÉEL uniquement)
- Gross PnL = Grid Profit + Floating Profit  (pas de funding en Spot)
- Déclenchement cycle si Gross PnL >= cycle_trigger_usd

Les paliers `grid_level_incomplete` ne sont JAMAIS comptés comme placés
dans le PnL, la marge ou la position théorique.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from ultiumgrid.connector.binance_spot import (
    BinanceSpotClient,
    RetryExhaustedError,
    SymbolFilters,
)
from ultiumgrid.engine.config import StrategyConfig

logger = logging.getLogger(__name__)


@dataclass
class GridLevel:
    index: int
    price: Decimal
    side: str  # BUY | SELL
    quantity: Decimal
    order_id: int | None = None
    status: str = "pending"
    # pending|open|filled|cancelled|grid_level_incomplete|error
    incomplete_since: str | None = None


@dataclass
class GridState:
    symbol: str
    center_price: Decimal
    levels: list[GridLevel] = field(default_factory=list)
    grid_profit: float = 0.0
    floating_profit: float = 0.0
    position_qty: float = 0.0  # qty base RÉELLE grille (fills), jamais théorique
    entry_avg: float = 0.0
    active: bool = False
    deepest_buy_index: int = -1

    @property
    def gross_pnl(self) -> float:
        return self.grid_profit + self.floating_profit

    def incomplete_indices(self) -> list[int]:
        return [lv.index for lv in self.levels if lv.status == "grid_level_incomplete"]

    def placed_levels(self) -> list[GridLevel]:
        return [lv for lv in self.levels if lv.status in ("open", "filled")]


def compute_levels(
    center_price: Decimal,
    cfg: StrategyConfig,
    filters: SymbolFilters,
    qty_per_level: Decimal,
) -> list[GridLevel]:
    levels: list[GridLevel] = []
    n = cfg.num_levels
    mid = n // 2
    step = Decimal(str(cfg.step_pct)) / Decimal("100")
    for i in range(n):
        offset = i - mid + (Decimal("0.5") if n % 2 == 0 else 0)
        price = center_price * (Decimal("1") + step * Decimal(offset))
        price = filters.round_price(price)
        side = "BUY" if i < mid else "SELL"
        qty = filters.round_qty(qty_per_level)
        levels.append(GridLevel(index=i, price=price, side=side, quantity=qty))
    return levels


def qty_per_level(cfg: StrategyConfig, center_price: Decimal, filters: SymbolFilters) -> Decimal:
    """Spot pur : capital USDT réparti sur les paliers BUY (pas de levier)."""
    notional = Decimal(str(cfg.capital_usdt))
    buy_levels = max(cfg.num_levels // 2, 1)
    per = notional / Decimal(buy_levels) / center_price
    qty = filters.round_qty(per)
    if qty * center_price < filters.min_notional:
        qty = filters.round_qty(filters.min_notional / center_price * Decimal("1.05"))
    if qty < filters.min_qty:
        qty = filters.min_qty
    return qty


class GridEngine:
    def __init__(
        self,
        client: BinanceSpotClient,
        cfg: StrategyConfig,
        on_level_incomplete: Callable[[GridLevel, RetryExhaustedError], None] | None = None,
    ):
        self.client = client
        self.cfg = cfg
        self.state = GridState(symbol=cfg.symbol, center_price=Decimal("0"))
        self.on_level_incomplete = on_level_incomplete

    def open_grid(self, center_price: Decimal | None = None) -> GridState:
        symbol = self.cfg.symbol
        filters = self.client.get_symbol_filters(symbol)
        if center_price is None:
            center_price = Decimal(self.client.ticker_price(symbol)["price"])
        center_price = filters.round_price(center_price)

        qty = qty_per_level(self.cfg, center_price, filters)
        levels = compute_levels(center_price, self.cfg, filters, qty)
        self.state = GridState(symbol=symbol, center_price=center_price, levels=levels, active=True)

        for level in levels:
            self._place_level_order(level)
        return self.state

    def _place_level_order(self, level: GridLevel) -> None:
        try:
            order = self.client.place_order(
                symbol=self.cfg.symbol,
                side=level.side,
                order_type="LIMIT",
                quantity=level.quantity,
                price=level.price,
                grid_level=level.index,
                purpose="normal",
            )
            level.order_id = int(order["orderId"])
            level.status = "open"
            level.incomplete_since = None
        except RetryExhaustedError as exc:
            level.order_id = None
            level.status = "grid_level_incomplete"
            level.incomplete_since = datetime.now(timezone.utc).isoformat()
            logger.critical(
                "Palier %s de la grille non placé après 5 tentatives — grille incomplète depuis %s",
                level.index,
                level.incomplete_since,
            )
            if self.on_level_incomplete:
                self.on_level_incomplete(level, exc)
        except Exception as exc:
            logger.error("place level %s failed: %s", level.index, exc)
            level.status = "error"

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
        """Floating sur position RÉELLE uniquement (pas de paliers incomplets inventés)."""
        qty = self.state.position_qty
        entry = self.state.entry_avg
        if qty == 0 or entry == 0:
            self.state.floating_profit = 0.0
        else:
            self.state.floating_profit = (mark_price - entry) * qty
        return self.state.floating_profit

    def theoretical_buy_qty(self) -> float:
        """Quantité théorique si tous les paliers BUY étaient placés (audit écart coupe)."""
        mid = self.cfg.num_levels // 2
        return sum(float(lv.quantity) for lv in self.state.levels if lv.index < mid)

    def on_fill(self, level_index: int, fill_price: float, fill_qty: float) -> GridLevel | None:
        levels = self.state.levels
        if level_index < 0 or level_index >= len(levels):
            return None
        level = levels[level_index]
        if level.status == "grid_level_incomplete":
            return None
        level.status = "filled"
        filters = self.client.get_symbol_filters(self.cfg.symbol)

        signed_qty = fill_qty if level.side == "BUY" else -fill_qty
        prev_qty = self.state.position_qty
        new_qty = prev_qty + signed_qty
        if prev_qty == 0:
            self.state.entry_avg = fill_price
        elif (prev_qty > 0 and signed_qty > 0) or (prev_qty < 0 and signed_qty < 0):
            self.state.entry_avg = (
                abs(prev_qty) * self.state.entry_avg + fill_qty * fill_price
            ) / (abs(prev_qty) + fill_qty)
        elif prev_qty != 0 and ((prev_qty > 0 and signed_qty < 0) or (prev_qty < 0 and signed_qty > 0)):
            closed = min(abs(prev_qty), fill_qty)
            direction = 1 if prev_qty > 0 else -1
            realized = direction * (fill_price - self.state.entry_avg) * closed
            self.state.grid_profit += realized

        self.state.position_qty = new_qty
        if level.side == "BUY":
            self.state.deepest_buy_index = max(self.state.deepest_buy_index, level_index)

        if level.side == "BUY" and level_index + 1 < len(levels):
            target = levels[level_index + 1]
            if target.status in ("pending", "cancelled", "filled", "error"):
                self._place_replacement(target, "SELL", filters)
                return target
        elif level.side == "SELL" and level_index - 1 >= 0:
            target = levels[level_index - 1]
            if target.status in ("pending", "cancelled", "filled", "error"):
                self._place_replacement(target, "BUY", filters)
                return target
        return None

    def _place_replacement(self, level: GridLevel, side: str, filters: SymbolFilters) -> None:
        level.side = side
        level.price = filters.round_price(level.price)
        try:
            order = self.client.place_order(
                symbol=self.cfg.symbol,
                side=side,
                order_type="LIMIT",
                quantity=level.quantity,
                price=level.price,
                grid_level=level.index,
            )
            level.order_id = int(order["orderId"])
            level.status = "open"
            level.incomplete_since = None
        except RetryExhaustedError as exc:
            level.order_id = None
            level.status = "grid_level_incomplete"
            level.incomplete_since = datetime.now(timezone.utc).isoformat()
            if self.on_level_incomplete:
                self.on_level_incomplete(level, exc)
        except Exception as exc:
            logger.error("replacement level %s failed: %s", level.index, exc)
            level.status = "error"

    def should_close_cycle(self) -> bool:
        return self.state.gross_pnl >= self.cfg.cycle_trigger_usd

    def real_position_qty(self) -> float:
        """Solde réel de l'actif de base (GET /api/v3/account balances) — source de vérité Spot."""
        return self.client.base_asset_qty(self.cfg.symbol)

    def close_cycle(self) -> dict[str, Any]:
        """Vend la quantité base RÉELLE lue juste avant l'action (pas de reconstruction théorique)."""
        self.cancel_all_grid_orders()
        symbol = self.cfg.symbol
        # Quantité grille = solde base total - sacs (appelant doit avoir synchronisé)
        # Ici on vend uniquement la qty grille trackée, plafonnée au solde libre réel
        filters = self.client.get_symbol_filters(symbol)
        free_base = self.client.balance_free(filters.base_asset)
        amt = min(abs(self.state.position_qty), free_base)
        closed = []
        if amt >= float(filters.min_qty):
            try:
                order = self.client.place_order(
                    symbol=symbol,
                    side="SELL",
                    order_type="MARKET",
                    quantity=amt,
                    purpose="cycle_close",
                )
                closed.append(order)
            except Exception as exc:
                logger.error("close cycle sell failed: %s", exc)
        result = {
            "grid_profit": self.state.grid_profit,
            "floating_profit": self.state.floating_profit,
            "gross_pnl": self.state.gross_pnl,
            "closed_orders": closed,
            "real_base_before_close": free_base,
            "sold_qty": amt,
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
                "incomplete_since": lv.incomplete_since,
            }
            for lv in self.state.levels
        ]
