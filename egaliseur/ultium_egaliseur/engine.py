"""Moteur Bot Égaliseur — trailing stop, stop dur logiciel, sortie temporelle.

Décisions documentées :
- Stop dur : surveillance logicielle (pas d'OCO) — Binance Spot ne permet pas
  deux ordres SELL sur la même quantité ; on annule le trailing puis MARKET.
- Pause : les trailing stops déjà posés restent actifs (cancel_orders_on_pause=false).
- Activation trailing : stopPrice = entrée × (1 + activation_recovery_pct/100)
  pour ne suivre qu'après reprise +1 % au-dessus de l'entrée.
- Prix limite : activation_stop × (1 - limit_margin_pct/100) — marge 0,15 % par défaut.
- Sortie temporelle : défaut 24 h (CDC/prompt v2.2) — compromis rotation capital vs récupération ;
  voir docs/questions_ouvertes.md Q11 pour le point de vigilance empirique (24 h vs 7 j).
- Interdiction absolue d'ordre BUY : aucun appel side=BUY dans ce module.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ultiumgrid.bags.manager import ACTIVE_BAG_STATUSES, bag_to_dict
from ultiumgrid.connector.binance_spot import BinanceSpotClient
from ultiumgrid.db.models import AlertEvent, Bag, EgaliseurAction, EgaliseurState, utcnow
from ultiumgrid.engine.fees import commission_to_usdt

from ultium_egaliseur.config import EgaliseurConfig, pct_to_bips

logger = logging.getLogger(__name__)

SOLD_STATUSES = frozenset(
    {"sold_auto", "sold_forced_stop", "sold_forced_time", "sold_manual", "sold_panic", "closed"}
)
FORCED_LOSS_STATUSES = frozenset({"sold_forced_stop", "sold_forced_time"})


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class EgaliseurEngine:
    def __init__(self, client: BinanceSpotClient, session: Session):
        self.client = client
        self.session = session

    def load_config(self) -> EgaliseurConfig:
        row = self.session.query(EgaliseurState).filter(EgaliseurState.key == "main").first()
        return EgaliseurConfig.from_dict(row.value_json if row else None)

    def save_config(self, cfg: EgaliseurConfig) -> None:
        row = self.session.query(EgaliseurState).filter(EgaliseurState.key == "main").first()
        if not row:
            row = EgaliseurState(key="main", value_json=cfg.to_dict())
            self.session.add(row)
        else:
            row.value_json = cfg.to_dict()
            row.updated_at = utcnow()
        self.session.commit()

    def ensure_config_initialized(self) -> EgaliseurConfig:
        cfg = self.load_config()
        if not self.session.query(EgaliseurState).filter(EgaliseurState.key == "main").first():
            self.save_config(cfg)
        return cfg

    def log_action(
        self,
        action: str,
        message: str,
        *,
        bag_id: int | None = None,
        payload: dict | None = None,
        alert_level: str = "info",
    ) -> None:
        self.session.add(
            EgaliseurAction(
                bag_id=bag_id,
                action=action,
                message=message,
                payload_json=payload,
            )
        )
        self.session.add(
            AlertEvent(
                level=alert_level,
                kind=f"egaliseur_{action}",
                message=message,
                payload_json={"bag_id": bag_id, **(payload or {})},
            )
        )
        self.session.commit()
        logger.info("egaliseur %s bag=%s %s", action, bag_id, message)

    def set_paused(self, paused: bool, reason: str = "manual") -> EgaliseurConfig:
        cfg = self.load_config()
        cfg.paused = paused
        self.save_config(cfg)
        self.log_action(
            "pause" if paused else "resume",
            f"Bot Égaliseur {'en pause' if paused else 'repris'} ({reason})",
            payload={"paused": paused, "reason": reason},
            alert_level="warn" if paused else "info",
        )
        if paused and cfg.cancel_orders_on_pause:
            self._cancel_all_trailing_orders(cfg.symbol)
        return cfg

    def _cancel_all_trailing_orders(self, symbol: str) -> None:
        bags = (
            self.session.query(Bag)
            .filter(Bag.symbol == symbol, Bag.status == "trailing_active")
            .all()
        )
        for bag in bags:
            if bag.trailing_order_id:
                try:
                    self.client.cancel_order(symbol, int(bag.trailing_order_id))
                    self.log_action(
                        "trailing_cancelled_pause",
                        f"Trailing annulé sac {bag.id} (pause)",
                        bag_id=bag.id,
                    )
                except Exception as exc:
                    logger.warning("cancel trailing bag %s: %s", bag.id, exc)

    def daily_forced_loss_usd(self, symbol: str) -> float:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        bags = (
            self.session.query(Bag)
            .filter(
                Bag.symbol == symbol,
                Bag.status.in_(FORCED_LOSS_STATUSES),
                Bag.closed_at >= start,
            )
            .all()
        )
        return sum(float(b.realized_pnl or 0) for b in bags if (b.realized_pnl or 0) < 0)

    def check_daily_loss_cap(self, cfg: EgaliseurConfig) -> bool:
        loss = self.daily_forced_loss_usd(cfg.symbol)
        if loss <= cfg.daily_loss_cap_usd:
            if not cfg.paused:
                self.set_paused(True, reason="daily_loss_cap")
                self.log_action(
                    "daily_loss_cap",
                    f"Plafond perte quotidien atteint: {loss:.2f} USD <= {cfg.daily_loss_cap_usd}",
                    payload={"daily_forced_loss_usd": loss},
                    alert_level="alert",
                )
            return True
        return False

    def tick(self) -> dict[str, Any]:
        cfg = self.ensure_config_initialized()
        symbol = cfg.symbol
        summary: dict[str, Any] = {
            "at": utcnow().isoformat(),
            "paused": cfg.paused,
            "operation_mode": cfg.operation_mode,
            "mode_label": cfg.mode_label(),
            "test_armed_bag_ids": list(cfg.test_armed_bag_ids or []),
            "processed": 0,
        }

        if self.check_daily_loss_cap(cfg):
            summary["daily_loss_cap_triggered"] = True
            cfg = self.load_config()

        try:
            mark = float(self.client.ticker_price(symbol, force=True)["price"])
        except Exception as exc:
            summary["error"] = f"ticker: {exc}"
            return summary

        active = (
            self.session.query(Bag)
            .filter(Bag.symbol == symbol, Bag.status.in_(ACTIVE_BAG_STATUSES))
            .order_by(Bag.id.asc())
            .all()
        )

        for bag in active:
            summary["processed"] += 1
            if bag.status == "open":
                self._handle_new_bag(bag, cfg, mark)
            elif bag.status == "journal_only":
                self._handle_journal_only(bag, cfg)
            elif bag.status == "trailing_active":
                self._monitor_trailing_bag(bag, cfg, mark)

        self._write_heartbeat(cfg)
        return summary

    def _write_heartbeat(self, cfg: EgaliseurConfig) -> None:
        row = self.session.query(EgaliseurState).filter(EgaliseurState.key == "heartbeat").first()
        payload = {
            "ts": utcnow().isoformat(),
            "paused": cfg.paused,
            "operation_mode": cfg.operation_mode,
            "mode_label": cfg.mode_label(),
        }
        if not row:
            self.session.add(EgaliseurState(key="heartbeat", value_json=payload))
        else:
            row.value_json = payload
            row.updated_at = utcnow()
        self.session.commit()

    def _handle_journal_only(self, bag: Bag, cfg: EgaliseurConfig) -> None:
        if cfg.paused:
            return
        if not cfg.may_place_orders_on_bag(bag.id):
            return
        bag.status = "open"
        self.session.commit()
        self.log_action(
            "bag_ready_for_action",
            f"Sac {bag.id} repasse en open — autorisé en mode {cfg.operation_mode}",
            bag_id=bag.id,
        )

    def _handle_new_bag(self, bag: Bag, cfg: EgaliseurConfig, mark: float) -> None:
        if cfg.paused:
            return
        if not cfg.may_place_orders_on_bag(bag.id):
            bag.status = "journal_only"
            self.session.commit()
            self.log_action(
                "bag_detected_test_only",
                f"Sac {bag.id} détecté — mode test_only (journalisé, pas d'ordre sans armement)",
                bag_id=bag.id,
                payload=bag_to_dict(bag),
            )
            return
        self._activate_trailing(bag, cfg, mark)

    def arm_test_bag(self, bag_id: int) -> EgaliseurConfig:
        """Autorise un sac pour tests ponctuels réels (mode test_only)."""
        cfg = self.load_config()
        armed = list(cfg.test_armed_bag_ids or [])
        if bag_id not in armed:
            armed.append(bag_id)
        cfg.test_armed_bag_ids = armed
        self.save_config(cfg)
        bag = self.session.get(Bag, bag_id)
        if bag and bag.status == "journal_only":
            bag.status = "open"
            self.session.commit()
        self.log_action(
            "test_bag_armed",
            f"Sac {bag_id} armé pour test ponctuel réel",
            bag_id=bag_id,
            payload={"operation_mode": cfg.operation_mode, "armed": armed},
            alert_level="warn",
        )
        return cfg

    def disarm_test_bag(self, bag_id: int | None = None) -> EgaliseurConfig:
        cfg = self.load_config()
        if bag_id is None:
            cfg.test_armed_bag_ids = []
        else:
            cfg.test_armed_bag_ids = [x for x in (cfg.test_armed_bag_ids or []) if x != bag_id]
        self.save_config(cfg)
        self.log_action(
            "test_bag_disarmed",
            f"Désarmement sac(s) test: {bag_id if bag_id else 'tous'}",
            bag_id=bag_id,
            payload={"armed": cfg.test_armed_bag_ids},
        )
        return cfg

    def set_operation_mode(self, mode: str) -> EgaliseurConfig:
        if mode not in ("test_only", "continuous"):
            raise ValueError("operation_mode doit être test_only ou continuous")
        cfg = self.load_config()
        cfg.operation_mode = mode
        if mode == "test_only":
            cfg.test_armed_bag_ids = []
        self.save_config(cfg)
        self.log_action(
            "operation_mode_changed",
            f"Mode d'exploitation → {mode}",
            payload={"operation_mode": mode},
            alert_level="warn" if mode == "continuous" else "info",
        )
        return cfg

    def _activate_trailing(self, bag: Bag, cfg: EgaliseurConfig, mark: float) -> None:
        filters = self.client.get_symbol_filters(bag.symbol)
        errors = cfg.validate(
            trail_min_bips=filters.trailing_delta_min_bips,
            trail_max_bips=filters.trailing_delta_max_bips,
        )
        if errors:
            self.log_action(
                "config_invalid",
                f"Config invalide sac {bag.id}: {'; '.join(errors)}",
                bag_id=bag.id,
                alert_level="warn",
            )
            return

        activation = bag.entry_price * (1.0 + cfg.activation_recovery_pct / 100.0)
        limit_px = activation * (1.0 - cfg.limit_margin_pct / 100.0)
        hard_stop = bag.entry_price * (1.0 + cfg.hard_stop_pct / 100.0)
        max_exit = utcnow() + timedelta(days=cfg.max_hold_days)
        bips = pct_to_bips(cfg.trailing_delta_pct)
        bips = max(filters.trailing_delta_min_bips, min(bips, filters.trailing_delta_max_bips))

        try:
            order = self.client.place_trailing_stop_sell(
                bag.symbol,
                bag.quantity,
                trailing_delta_bips=bips,
                limit_price=limit_px,
                activation_stop_price=activation,
                purpose="egaliseur_trailing",
            )
        except Exception as exc:
            self.log_action(
                "trailing_failed",
                f"Échec trailing sac {bag.id}: {exc}",
                bag_id=bag.id,
                alert_level="warn",
            )
            return

        bag.status = "trailing_active"
        bag.trailing_order_id = str(order.get("orderId"))
        bag.trailing_delta_bips = bips
        bag.trailing_limit_price = float(limit_px)
        bag.activation_stop_price = float(activation)
        bag.hard_stop_price = float(hard_stop)
        bag.max_exit_at = max_exit
        self.session.commit()

        self.log_action(
            "trailing_placed",
            f"Trailing posé sac {bag.id} order={bag.trailing_order_id} delta={bips}bps",
            bag_id=bag.id,
            payload={
                "order_id": bag.trailing_order_id,
                "trailing_delta_bips": bips,
                "limit_price": limit_px,
                "activation_stop_price": activation,
                "hard_stop_price": hard_stop,
                "max_exit_at": max_exit.isoformat(),
                "mark_at_placement": mark,
                "open_orders_proof": [
                    {
                        "orderId": o.get("orderId"),
                        "type": o.get("type"),
                        "trailingDelta": o.get("trailingDelta"),
                        "price": o.get("price"),
                        "stopPrice": o.get("stopPrice"),
                    }
                    for o in self.client.open_orders(bag.symbol, force=True)
                    if str(o.get("orderId")) == bag.trailing_order_id
                ],
            },
        )

    def _monitor_trailing_bag(self, bag: Bag, cfg: EgaliseurConfig, mark: float) -> None:
        if bag.trailing_order_id:
            filled = self._check_order_filled(bag)
            if filled:
                self._finalize_sale(bag, "sold_auto", filled)
                return

        if bag.hard_stop_price and mark <= bag.hard_stop_price:
            self._force_market_sell(bag, "sold_forced_stop", mark, cfg)
            return

        if bag.max_exit_at and utcnow() >= _as_utc(bag.max_exit_at):
            self._force_market_sell(bag, "sold_forced_time", mark, cfg)
            return

    def _check_order_filled(self, bag: Bag) -> dict | None:
        if not bag.trailing_order_id:
            return None
        try:
            order = self.client.get_order(bag.symbol, int(bag.trailing_order_id))
        except Exception:
            return None
        status = (order.get("status") or "").upper()
        if status == "FILLED":
            return order
        if status in ("CANCELED", "EXPIRED", "REJECTED"):
            self.log_action(
                "trailing_order_gone",
                f"Ordre trailing sac {bag.id} status={status}",
                bag_id=bag.id,
                alert_level="warn",
            )
        return None

    def _cancel_trailing_if_open(self, bag: Bag) -> None:
        if not bag.trailing_order_id:
            return
        try:
            order = self.client.get_order(bag.symbol, int(bag.trailing_order_id))
            if (order.get("status") or "").upper() in ("NEW", "PARTIALLY_FILLED"):
                self.client.cancel_order(bag.symbol, int(bag.trailing_order_id))
        except Exception as exc:
            logger.warning("cancel trailing before force sell bag %s: %s", bag.id, exc)

    def _force_market_sell(
        self, bag: Bag, sold_status: str, mark: float, cfg: EgaliseurConfig
    ) -> None:
        # Stop dur et sortie temporelle s'exécutent même en pause (sacs déjà sous gestion).
        self._cancel_trailing_if_open(bag)
        purpose = (
            "egaliseur_forced_stop"
            if sold_status == "sold_forced_stop"
            else "egaliseur_forced_time"
        )
        try:
            order = self.client.place_order(
                bag.symbol,
                "SELL",
                "MARKET",
                bag.quantity,
                purpose=purpose,
            )
        except Exception as exc:
            self.log_action(
                "force_sell_failed",
                f"Vente forcée échouée sac {bag.id}: {exc}",
                bag_id=bag.id,
                alert_level="alert",
            )
            return
        self._finalize_sale(bag, sold_status, order, mark_fallback=mark)

    def _finalize_sale(
        self,
        bag: Bag,
        sold_status: str,
        order: dict,
        *,
        mark_fallback: float | None = None,
    ) -> None:
        order_id = order.get("orderId")
        fill_price = float(order.get("avgPrice") or order.get("price") or 0)
        if fill_price <= 0 and mark_fallback:
            fill_price = mark_fallback

        fees_usdt = 0.0
        if order_id:
            try:
                trades = self.client.my_trades(bag.symbol, order_id=int(order_id), limit=20)
                bnb_px = None
                for t in trades:
                    comm = float(t.get("commission") or 0)
                    asset = (t.get("commissionAsset") or "").upper()
                    if asset == "BNB" and bnb_px is None:
                        try:
                            bnb_px = float(self.client.ticker_price("BNBUSDT", force=True)["price"])
                        except Exception:
                            bnb_px = None
                    fees_usdt += commission_to_usdt(
                        comm,
                        asset,
                        trade_price=float(t.get("price") or fill_price),
                        bnb_usdt_price=bnb_px,
                    )
                if trades:
                    qty_sum = sum(float(t.get("qty") or 0) for t in trades)
                    quote_sum = sum(
                        float(t.get("qty") or 0) * float(t.get("price") or 0) for t in trades
                    )
                    if qty_sum > 0:
                        fill_price = quote_sum / qty_sum
            except Exception as exc:
                logger.warning("myTrades bag %s: %s", bag.id, exc)

        gross_pnl = (fill_price - bag.entry_price) * bag.quantity
        bag.realized_pnl = gross_pnl - fees_usdt
        bag.sold_price = fill_price
        bag.sold_by = "bot_egaliseur"
        bag.status = sold_status
        bag.closed_at = utcnow()
        bag.trailing_order_id = None
        self.session.commit()

        base_before = None
        try:
            base_before = self.client.base_asset_qty(bag.symbol)
        except Exception:
            pass

        self.log_action(
            "bag_sold",
            f"Sac {bag.id} vendu ({sold_status}) pnl={bag.realized_pnl:.4f}",
            bag_id=bag.id,
            payload={
                "sold_status": sold_status,
                "fill_price": fill_price,
                "realized_pnl": bag.realized_pnl,
                "fees_usdt": fees_usdt,
                "order_id": str(order_id) if order_id else None,
                "base_qty_after": base_before,
            },
            alert_level="info",
        )

        self._verify_balance_after_sale(bag)

    def _verify_balance_after_sale(self, bag: Bag) -> None:
        try:
            remaining = (
                self.session.query(Bag)
                .filter(Bag.symbol == bag.symbol, Bag.status.in_(ACTIVE_BAG_STATUSES))
                .all()
            )
            expected_bags_qty = sum(b.quantity for b in remaining)
            binance_qty = self.client.base_asset_qty(bag.symbol)
            self.log_action(
                "balance_verify",
                f"Vérif post-vente sac {bag.id}: binance={binance_qty}",
                bag_id=bag.id,
                payload={
                    "binance_qty": binance_qty,
                    "bags_qty_remaining": expected_bags_qty,
                },
            )
        except Exception as exc:
            logger.warning("balance verify bag %s: %s", bag.id, exc)
