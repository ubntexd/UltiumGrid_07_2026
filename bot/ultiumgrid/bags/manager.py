"""Système de sacs — registres virtuels + réconciliation Binance.

Quantités toujours RÉELLES (positionRisk / transfert mesuré), jamais théoriques.
Traçabilité complète pour futur module de vente indépendant (lecture seule côté bot).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ultiumgrid.connector.binance_spot import BinanceSpotClient
from ultiumgrid.db.models import Bag, BagFloatingSnapshot, utcnow
from ultiumgrid.engine.config import StrategyConfig
from ultiumgrid.engine.trade_journal import creation_reason_from_cut_level

logger = logging.getLogger(__name__)

SNAPSHOT_MIN_INTERVAL_S = 3600.0
# Sacs encore détenus en position réelle (non vendus)
ACTIVE_BAG_STATUSES = frozenset({"open", "trailing_active", "journal_only"})


def bag_to_dict(bag: Bag, *, include_snapshots: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": bag.id,
        "symbol": bag.symbol,
        "quantity": bag.quantity,
        "entry_price": bag.entry_price,
        "status": bag.status,
        "source": bag.source,
        "cut_level": bag.cut_level,
        "realized_pnl": bag.realized_pnl,
        "creation_reason": bag.creation_reason,
        "cycle_id_origin": bag.cycle_id_origin,
        "incomplete_levels_at_creation": bag.incomplete_levels_at_creation,
        "market_price_at_creation": bag.market_price_at_creation,
        "sold_price": bag.sold_price,
        "sold_at": bag.closed_at.isoformat() if bag.closed_at else None,
        "sold_by": bag.sold_by,
        "trailing_order_id": bag.trailing_order_id,
        "trailing_delta_bips": bag.trailing_delta_bips,
        "trailing_limit_price": bag.trailing_limit_price,
        "activation_stop_price": bag.activation_stop_price,
        "hard_stop_price": bag.hard_stop_price,
        "max_exit_at": bag.max_exit_at.isoformat() if bag.max_exit_at else None,
        "created_at": bag.created_at.isoformat() if bag.created_at else None,
    }
    if include_snapshots:
        snaps = sorted(bag.floating_snapshots or [], key=lambda s: s.ts)
        out["floating_history"] = [
            {
                "ts": s.ts.isoformat() if s.ts else None,
                "mark_price": s.mark_price,
                "floating_pnl": s.floating_pnl,
            }
            for s in snaps
        ]
    return out


class BagManager:
    def __init__(self, client: BinanceSpotClient, session: Session, cfg: StrategyConfig):
        self.client = client
        self.session = session
        self.cfg = cfg
        self.last_reconciliation: dict[str, Any] | None = None
        self._last_snapshot_at: dict[int, datetime] = {}

    def create_bag(
        self,
        quantity: float,
        entry_price: float,
        cut_level: int | None,
        source: str = "cut",
        incomplete_levels: list[int] | None = None,
        *,
        cycle_id_origin: int | None = None,
        market_price_at_creation: float | None = None,
    ) -> Bag:
        """quantity = quantité RÉELLE transférée (jamais théorique grille pleine)."""
        if market_price_at_creation is None:
            try:
                market_price_at_creation = float(
                    self.client.ticker_price(self.cfg.symbol, force=True)["price"]
                )
            except Exception:
                market_price_at_creation = entry_price
        bag = Bag(
            symbol=self.cfg.symbol,
            quantity=quantity,
            entry_price=entry_price,
            status="open",
            source=source,
            cut_level=cut_level,
            creation_reason=creation_reason_from_cut_level(cut_level, source),
            cycle_id_origin=cycle_id_origin,
            incomplete_levels_at_creation=incomplete_levels or None,
            market_price_at_creation=market_price_at_creation,
        )
        self.session.add(bag)
        self.session.commit()
        self.session.refresh(bag)
        self._record_floating_snapshot(bag, market_price_at_creation, force=True)
        if incomplete_levels:
            logger.info(
                "Bag %s créé qty=%s reason=%s cycle=%s incomplete=%s mark=%s",
                bag.id,
                quantity,
                bag.creation_reason,
                cycle_id_origin,
                incomplete_levels,
                market_price_at_creation,
            )
        return bag

    def open_bags(self, symbol: str | None = None) -> list[Bag]:
        symbol = symbol or self.cfg.symbol
        return (
            self.session.query(Bag)
            .filter(Bag.symbol == symbol, Bag.status.in_(ACTIVE_BAG_STATUSES))
            .all()
        )

    def all_bags(self, symbol: str | None = None, status: str | None = None) -> list[Bag]:
        symbol = symbol or self.cfg.symbol
        q = self.session.query(Bag).filter(Bag.symbol == symbol)
        if status and status != "all":
            q = q.filter(Bag.status == status)
        return q.order_by(Bag.id.desc()).all()

    def bags_qty(self, symbol: str | None = None) -> float:
        return sum(b.quantity for b in self.open_bags(symbol))

    def _record_floating_snapshot(
        self, bag: Bag, mark_price: float, *, force: bool = False
    ) -> None:
        if bag.status not in ACTIVE_BAG_STATUSES:
            return
        now = utcnow()
        last = self._last_snapshot_at.get(bag.id)
        if not force and last and (now - last).total_seconds() < SNAPSHOT_MIN_INTERVAL_S:
            return
        floating = (mark_price - bag.entry_price) * bag.quantity
        self.session.add(
            BagFloatingSnapshot(
                bag_id=bag.id,
                mark_price=mark_price,
                floating_pnl=floating,
                ts=now,
            )
        )
        self._last_snapshot_at[bag.id] = now

    def maybe_snapshot_floating(self, mark_price: float) -> None:
        """Snapshot horaire du flottant pour chaque sac ouvert."""
        for bag in self.open_bags():
            self._record_floating_snapshot(bag, mark_price)
        self.session.commit()

    def sell_bag(
        self,
        bag_id: int,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        sold_by: str = "sold_manual",
    ) -> dict[str, Any]:
        bag = self.session.get(Bag, bag_id)
        if not bag or bag.status not in ACTIVE_BAG_STATUSES:
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
        bag.sold_price = fill_price
        bag.sold_by = sold_by
        bag.status = "sold_manual" if sold_by == "sold_manual" else sold_by
        bag.closed_at = utcnow()
        self.session.commit()
        return {"bag": bag_to_dict(bag), "order": order}

    def close_bags_via_panic(self, mark_price: float | None = None) -> list[dict[str, Any]]:
        """Marque les sacs ouverts comme vendus via panic (vente globale base)."""
        closed = []
        for bag in list(self.open_bags()):
            bag.status = "sold_panic"
            bag.sold_by = "sold_panic"
            bag.closed_at = utcnow()
            if mark_price is not None:
                bag.sold_price = mark_price
                bag.realized_pnl = (mark_price - bag.entry_price) * bag.quantity
            closed.append(
                {
                    "bag_id": bag.id,
                    "status": bag.status,
                    "sold_price": bag.sold_price,
                    "sold_by": bag.sold_by,
                }
            )
        self.session.commit()
        return closed

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
