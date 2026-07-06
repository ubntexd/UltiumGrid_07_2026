"""Moteur de grille — étage 1.

Formules documentées pour reproductibilité :

- Niveaux arithmétiques (num_levels), centrés sur center_price :
  level_i = center_price * (1 + step_pct/100 * offset)
  pour i = 0..num_levels-1 (i < mid = BUY, i >= mid = SELL)

- Grid Profit = somme des profits des paires BUY(i)+SELL(i+1) **complètes** uniquement
  (formule Binance : sell*q*(1-fee) - buy*q*(1+fee) sur qty appariée)
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
from ultiumgrid.engine.fees import commission_to_usdt
from ultiumgrid.engine.grid_profit import MatchedGridLedger, compute_grid_profit_from_trades

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
    # Achat marché initial (inventaire pour les SELL) — traçabilité frais/slippage
    initial_buy: dict | None = None
    matched_ledger: MatchedGridLedger = field(default_factory=MatchedGridLedger)

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
    """Spot pur : moitié du capital → inventaire SELL (achat marché), moitié → limites BUY."""
    notional = Decimal(str(cfg.capital_usdt)) / Decimal("2")
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

    def open_grid(
        self,
        center_price: Decimal | None = None,
        prior_entry_avg: float = 0.0,
    ) -> GridState:
        """Séquence unique d'ouverture :
        1) niveaux 2) BUY marché inventaire SELL 3) limites BUY+SELL 4) état.
        """
        symbol = self.cfg.symbol
        filters = self.client.get_symbol_filters(symbol, force=True)
        if center_price is None:
            center_price = Decimal(self.client.ticker_price(symbol, force=True)["price"])
        center_price = filters.round_price(center_price)
        theoretical_center = float(center_price)

        qty = qty_per_level(self.cfg, center_price, filters)
        levels = compute_levels(center_price, self.cfg, filters, qty)
        sell_levels = [lv for lv in levels if lv.side == "SELL"]
        sell_qty_total = filters.round_qty(
            sum((lv.quantity for lv in sell_levels), Decimal("0"))
        )
        if sell_qty_total * center_price < filters.min_notional:
            sell_qty_total = filters.round_qty(
                filters.min_notional / center_price * Decimal("1.05")
            )
        if sell_qty_total < filters.min_qty:
            sell_qty_total = filters.min_qty

        # Étape 2 — inventaire pour les SELL (achat marché si besoin)
        free_base = Decimal(str(self.client.balance_free(filters.base_asset, force=True)))
        need = sell_qty_total - free_base
        from ultiumgrid.engine.orphan_position import resolve_entry_avg_existing

        initial_buy: dict | None = None
        entry_avg = 0.0
        position_qty = float(free_base)

        if need >= filters.min_qty:
            # Buffer frais éventuels en base asset + minNotional
            need = filters.round_qty(need + filters.step_size * 3)
            if need * center_price < filters.min_notional:
                need = filters.round_qty(
                    filters.min_notional / center_price * Decimal("1.05")
                )
            order = self.client.place_order(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                quantity=need,
                purpose="initial_inventory_buy",
            )
            executed = Decimal(str(order.get("executedQty") or "0"))
            quote = Decimal(str(order.get("cummulativeQuoteQty") or "0"))
            if executed <= 0:
                raise RuntimeError(
                    f"initial_inventory_buy non fillé: order={order.get('orderId')} status={order.get('status')}"
                )
            # Confirmation myTrades (retry) — fallback fills de la réponse ordre
            import time as _time

            trades: list = []
            for _ in range(6):
                trades = self.client.my_trades(
                    symbol, limit=20, order_id=int(order["orderId"])
                )
                if trades:
                    break
                _time.sleep(0.25)
            confirm_source = "myTrades"
            if not trades:
                fills = order.get("fills") or []
                if not fills:
                    raise RuntimeError(
                        f"initial_inventory_buy sans myTrades/fills orderId={order.get('orderId')}"
                    )
                trades = fills
                confirm_source = "order.fills"
            entry_avg = float(quote / executed) if executed else theoretical_center
            slippage_pct = (
                (entry_avg - theoretical_center) / theoretical_center * 100.0
                if theoretical_center
                else 0.0
            )
            fees_usdt = 0.0
            bnb_px = None
            for t in trades:
                comm = float(t.get("commission") or 0)
                asset = str(t.get("commissionAsset") or "")
                px = float(t.get("price") or entry_avg)
                if asset.upper() == "BNB" and bnb_px is None:
                    try:
                        bnb_px = float(self.client.ticker_price("BNBUSDT", force=True)["price"])
                    except Exception:
                        bnb_px = None
                fees_usdt += commission_to_usdt(
                    comm, asset, trade_price=px, bnb_usdt_price=bnb_px
                )
            # Base nette après frais éventuels en base asset
            free_after = Decimal(
                str(self.client.balance_free(filters.base_asset, force=True))
            )
            if free_after < sell_qty_total:
                shortfall = sell_qty_total - free_after
                max_shortfall = max(
                    filters.step_size * 10,
                    sell_qty_total * Decimal("0.02"),
                )
                if shortfall > max_shortfall:
                    raise RuntimeError(
                        f"inventaire insuffisant après buy: free={free_after} need={sell_qty_total}"
                    )
                sell_qty_total = filters.round_qty(free_after)
            initial_buy = {
                "orderId": order.get("orderId"),
                "clientOrderId": order.get("clientOrderId"),
                "executedQty": float(executed),
                "cummulativeQuoteQty": float(quote),
                "avg_price": entry_avg,
                "theoretical_center": theoretical_center,
                "slippage_pct": slippage_pct,
                "fees_usdt": fees_usdt,
                "confirm_source": confirm_source,
                "myTrades_count": len(trades),
                "purpose": "initial_inventory_buy",
            }
            position_qty = float(free_after)
            logger.info(
                "initial_inventory_buy orderId=%s qty=%s avg=%s fees_usdt=%s src=%s",
                order.get("orderId"),
                executed,
                entry_avg,
                fees_usdt,
                confirm_source,
            )
        else:
            if free_base >= filters.min_qty:
                entry_avg, src = resolve_entry_avg_existing(
                    self.client,
                    symbol,
                    float(free_base),
                    theoretical_center,
                    prior_entry_avg if prior_entry_avg > 0 else None,
                )
                initial_buy = {
                    "skipped": True,
                    "reason": "free_base_sufficient",
                    "entry_avg_source": src,
                    "free_base": float(free_base),
                    "avg_price": entry_avg,
                }
            logger.info(
                "initial_inventory_buy skipped — free_base=%s >= sell_qty=%s entry_avg=%s src=%s",
                free_base,
                sell_qty_total,
                entry_avg,
                (initial_buy or {}).get("entry_avg_source"),
            )

        self.state = GridState(
            symbol=symbol,
            center_price=center_price,
            levels=levels,
            active=True,
            position_qty=position_qty,
            entry_avg=entry_avg,
            initial_buy=initial_buy,
        )
        self.state.matched_ledger = MatchedGridLedger()

        # Étape 3 — limites BUY et SELL (inventaire garanti pour les SELL)
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
            logger.warning("cancel_all_orders: %s — fallback per-order", exc)
            for level in self.state.levels:
                if level.order_id and level.status == "open":
                    try:
                        self.client.cancel_order(symbol, level.order_id)
                    except Exception as exc2:
                        logger.warning("cancel order %s: %s", level.order_id, exc2)
        # Vérifier openOrders réel et annuler tout résidu
        try:
            live = self.client.open_orders(symbol, force=True)
            for o in live:
                try:
                    self.client.cancel_order(symbol, int(o["orderId"]))
                except Exception as exc3:
                    logger.warning("cancel residual %s: %s", o.get("orderId"), exc3)
        except Exception as exc:
            logger.warning("open_orders after cancel: %s", exc)
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
        if abs(new_qty) < 1e-12:
            self.state.entry_avg = 0.0

        self.state.position_qty = new_qty
        # Grid Profit : appariement Binance BUY(level) + SELL(level+1), pas entry_avg global
        fee_rate = 0.001
        if hasattr(self.cfg, "bnb_fee_discount") and self.cfg.bnb_fee_discount:
            fee_rate = 0.00075
        self.state.matched_ledger.fee_rate = fee_rate
        self.state.matched_ledger.on_fill(level.side, level_index, fill_price, fill_qty)
        self.state.grid_profit = self.state.matched_ledger.grid_profit
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

    def recompute_grid_profit_from_trades(self, trades: list[dict]) -> float:
        """Recalcule grid_profit (appariement Binance) depuis les fills DB."""
        fee_rate = 0.00075 if getattr(self.cfg, "bnb_fee_discount", False) else 0.001
        result = compute_grid_profit_from_trades(trades, fee_rate=fee_rate)
        self.state.matched_ledger.reset()
        for rt in result["matched_roundtrips"]:
            self.state.matched_ledger.matched_roundtrips.append(rt)
        self.state.matched_ledger.grid_profit = result["grid_profit"]
        self.state.grid_profit = result["grid_profit"]
        return result["grid_profit"]

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
