"""Boucle principale du bot — grille, coupe, sacs, garde-fous, reprise."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ultiumgrid.bags.manager import BagManager
from ultiumgrid.connector.binance_spot import BinanceSpotClient
from ultiumgrid.connector.binance_spot import RetryExhaustedError
from datetime import datetime, timezone

from ultiumgrid.db.models import (
    AlertEvent,
    BotState,
    Configuration,
    Cycle,
    FeePaid,
    OrderAttempt,
    PnlSnapshot,
    PriceTick,
    Trade,
    utcnow,
)
from ultiumgrid.engine.config import StrategyConfig
from ultiumgrid.engine.fees import commission_to_usdt
from ultiumgrid.engine.grid import GridEngine, GridLevel
from ultiumgrid.engine.orphan_position import UntrackedInventoryError, residual_position_warning
from ultiumgrid.guards.safety import SafetyGuards
from ultiumgrid.risk.cuts import ProgressiveCutManager

logger = logging.getLogger(__name__)


class BotRunner:
    def __init__(self, client: BinanceSpotClient, session: Session, cfg: StrategyConfig | None = None):
        self.client = client
        self.session = session
        self.cfg = cfg or self._load_active_config()
        self.client.set_order_log_callback(self._persist_order_attempt)
        self.engine = GridEngine(client, self.cfg, on_level_incomplete=self._on_level_incomplete)
        self.cuts = ProgressiveCutManager(self.engine, self.cfg)
        self.bags = BagManager(client, session, self.cfg)
        self.guards = SafetyGuards(client, session, self.cfg)
        self.running = False
        self.cycle_id: int | None = None
        self._pending_config: StrategyConfig | None = None
        self._pending_config_mode: str | None = None  # wait_cycle | close_now
        self._out_of_range_since: datetime | None = None
        self._last_fill_at: datetime | None = None
        self._stuck_sell_since: dict[int, datetime] = {}
        self._live_mark: float | None = None
        self._last_live_persist: float = 0.0
        self._ws_stop = False
        self._ws_thread: Any = None
        self._ws_stream_symbol: str | None = None
        self._session_factory = None  # set by main_loop for thread-safe WS writes
        self._opening_cycle = False  # lock séquence ouverture (anti double achat marché)
        self._stopped_at: str | None = None

    def _persist_order_attempt(self, entry: dict) -> None:
        row = OrderAttempt(
            symbol=entry.get("symbol") or "",
            side=entry.get("side") or "",
            order_type=entry.get("order_type") or "",
            purpose=entry.get("purpose") or "normal",
            client_order_id=entry.get("client_order_id") or "",
            attempt_no=int(entry.get("attempt_no") or 0),
            outcome=entry.get("outcome") or "",
            http_status=entry.get("http_status"),
            binance_code=entry.get("binance_code"),
            binance_msg=entry.get("binance_msg"),
            order_id=entry.get("order_id"),
            request_json=entry.get("request_json"),
            response_json=entry.get("response_json"),
            verify_json=entry.get("verify_json"),
        )
        self.session.add(row)
        self.session.commit()

    def _critical_alert(self, kind: str, message: str, payload: dict | None = None) -> None:
        ev = AlertEvent(level="critical", kind=kind, message=message, payload_json=payload)
        self.session.add(ev)
        self.session.commit()
        logger.critical("[%s] %s", kind, message)

    def _on_level_incomplete(self, level: GridLevel, exc: RetryExhaustedError) -> None:
        ts = level.incomplete_since or utcnow().isoformat()
        msg = (
            f"Palier {level.index} de la grille non placé après 5 tentatives "
            f"— grille incomplète depuis {ts}"
        )
        self._critical_alert(
            "grid_level_incomplete",
            msg,
            {
                "level": level.index,
                "price": str(level.price),
                "quantity": str(level.quantity),
                "side": level.side,
                "used_client_ids": exc.used_client_ids,
                "since": ts,
            },
        )
        self.save_state()

    def _load_active_config(self) -> StrategyConfig:
        row = (
            self.session.query(Configuration)
            .filter(Configuration.is_active.is_(True))
            .order_by(Configuration.id.desc())
            .first()
        )
        if row:
            return StrategyConfig.from_dict(row.params_json)
        cfg = StrategyConfig()
        self._persist_config(cfg, active=True)
        return cfg

    def _persist_config(self, cfg: StrategyConfig, active: bool = False) -> Configuration:
        if active:
            for c in self.session.query(Configuration).filter(Configuration.is_active.is_(True)):
                c.is_active = False
        row = Configuration(
            symbol=cfg.symbol,
            params_json=cfg.to_dict(),
            is_active=active,
            applied_at=utcnow() if active else None,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def _store_command_result(self, name: str, result: dict[str, Any]) -> None:
        row = self.session.query(BotState).filter(BotState.key == "last_command").first()
        payload = {"name": name, "result": result, "ts": utcnow().isoformat()}
        if not row:
            self.session.add(BotState(key="last_command", value_json=payload))
        else:
            row.value_json = payload
            row.updated_at = utcnow()
        self.session.commit()

    def save_state(self) -> None:
        payload = {
            "running": self.running,
            "cycle_id": self.cycle_id,
            "grid": {
                "center_price": str(self.engine.state.center_price),
                "position_qty": self.engine.state.position_qty,
                "entry_avg": self.engine.state.entry_avg,
                "grid_profit": self.engine.state.grid_profit,
                "floating_profit": self.engine.state.floating_profit,
                "levels": self.engine.levels_as_dict(),
                "active": self.engine.state.active,
                "deepest_buy_index": self.engine.state.deepest_buy_index,
            },
            "cuts": {
                "armed": self.cuts.state.armed,
                "last_cut_level": self.cuts.state.last_cut_level,
                "lowest_level_reached": self.cuts.state.lowest_level_reached,
                "recovery_levels": self.cuts.state.recovery_levels,
            },
            "guards": {
                "daily_pnl": self.guards.state.daily_pnl,
                "day": self.guards.state.day.isoformat() if self.guards.state.day else None,
                "panic": self.guards.state.panic,
            },
            "config": self.cfg.to_dict(),
            "stopped_at": self._stopped_at if not self.running else None,
        }
        row = self.session.query(BotState).filter(BotState.key == "main").first()
        if not row:
            row = BotState(key="main", value_json=payload)
            self.session.add(row)
        else:
            row.value_json = payload
            row.updated_at = utcnow()
        self.session.commit()

    def restore_state(self) -> bool:
        row = self.session.query(BotState).filter(BotState.key == "main").first()
        if not row or not row.value_json:
            return False
        data = row.value_json
        self.cfg = StrategyConfig.from_dict(data.get("config") or {})
        self.engine = GridEngine(
            self.client, self.cfg, on_level_incomplete=self._on_level_incomplete
        )
        self.cuts = ProgressiveCutManager(self.engine, self.cfg)
        self.bags = BagManager(self.client, self.session, self.cfg)
        self.guards = SafetyGuards(self.client, self.session, self.cfg)
        g = data.get("grid") or {}
        self.engine.state.center_price = Decimal(g.get("center_price") or "0")
        self.engine.state.position_qty = float(g.get("position_qty") or 0)
        self.engine.state.entry_avg = float(g.get("entry_avg") or 0)
        self.engine.state.grid_profit = float(g.get("grid_profit") or 0)
        self.engine.state.floating_profit = float(g.get("floating_profit") or 0)
        self.engine.state.active = bool(g.get("active"))
        self.engine.state.deepest_buy_index = int(g.get("deepest_buy_index") or -1)
        levels = []
        for lv in g.get("levels") or []:
            levels.append(
                GridLevel(
                    index=lv["index"],
                    price=Decimal(lv["price"]),
                    side=lv["side"],
                    quantity=Decimal(lv["quantity"]),
                    order_id=lv.get("order_id"),
                    status=lv.get("status", "open"),
                    incomplete_since=lv.get("incomplete_since"),
                )
            )
        self.engine.state.levels = levels
        self.cycle_id = data.get("cycle_id")
        self.running = bool(data.get("running"))
        self._stopped_at = data.get("stopped_at")
        # Un seul cycle open par symbole (ferme les orphelins laissés par un start sans close)
        self._ensure_single_open_cycle(reason="orphan_on_restore")
        # Réconciliation ordres ouverts Binance vs DB
        self._reconcile_orders_after_crash()
        self._recompute_grid_profit_from_db()
        logger.info("State restored cycle_id=%s running=%s", self.cycle_id, self.running)
        return True

    def _recompute_grid_profit_from_db(self) -> None:
        """Aligne grid_profit sur les trades DB (appariement Binance)."""
        if not self.cycle_id:
            return
        rows = (
            self.session.query(Trade)
            .filter(Trade.cycle_id == self.cycle_id, Trade.level_index.isnot(None))
            .order_by(Trade.created_at.asc())
            .all()
        )
        if not rows:
            return
        trades = [
            {
                "id": t.id,
                "side": t.side,
                "price": t.price,
                "quantity": t.quantity,
                "level_index": t.level_index,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ]
        self.engine.recompute_grid_profit_from_trades(trades)

    def _reconcile_orders_after_crash(self) -> None:
        try:
            open_orders = self.client.open_orders(self.cfg.symbol)
        except Exception as exc:
            logger.error("reconcile open_orders failed: %s", exc)
            return
        live_ids = {o["orderId"] for o in open_orders}
        for lv in self.engine.state.levels:
            if lv.order_id and lv.order_id not in live_ids and lv.status == "open":
                # Ordre disparu — marquer filled ou cancelled selon historique
                try:
                    got = self.client.get_order(self.cfg.symbol, lv.order_id)
                    lv.status = got.get("status", "unknown").lower()
                    if lv.status == "filled":
                        self.engine.on_fill(lv.index, float(got.get("avgPrice") or lv.price), float(got.get("executedQty") or lv.quantity))
                except Exception:
                    lv.status = "unknown"
            elif lv.order_id is None and lv.status == "open":
                lv.status = "cancelled"
        # Ordres live non trackés : les enregistrer pour ne pas les perdre
        tracked = {lv.order_id for lv in self.engine.state.levels if lv.order_id}
        for o in open_orders:
            if o["orderId"] not in tracked:
                logger.warning("Untracked live order kept as-is: %s", o["orderId"])

    def start(self) -> dict[str, Any]:
        if self.guards.state.circuit_breaker_triggered:
            return {"ok": False, "error": "Bot bloqué par circuit breaker"}
        if getattr(self.cfg, "bnb_fee_discount", False):
            try:
                bnb = self.client.balance_free("BNB")
                if bnb <= 0:
                    return {
                        "ok": False,
                        "error": (
                            "bnb_fee_discount activé : solde BNB requis pour démarrer "
                            f"(actuel={bnb})"
                        ),
                    }
            except Exception as exc:
                return {"ok": False, "error": f"impossible de vérifier BNB: {exc}"}
        # Panic précédent : autoriser un redémarrage explicite (clear du flag)
        if self.guards.state.panic:
            self.guards.state.panic = False
        if self._opening_cycle:
            return {
                "ok": True,
                "cycle_id": self.cycle_id,
                "already_running": True,
                "message": "Ouverture de cycle déjà en cours — pas de second achat",
            }
        if self.running and self.engine.state.active and self.cycle_id:
            self.save_state()
            return {
                "ok": True,
                "cycle_id": self.cycle_id,
                "already_running": True,
                "message": "Cycle déjà actif — aucun nouveau cycle créé",
            }
        self.running = True
        self._stopped_at = None
        self.restart_price_stream()
        # Toujours réconcilier les cycles open en DB avant d'en créer un nouveau
        self._ensure_single_open_cycle(reason="orphan_on_start")
        try:
            if not self.engine.state.active:
                self._open_new_cycle()
        except UntrackedInventoryError as exc:
            self.running = False
            self.save_state()
            return {
                "ok": False,
                "blocked": True,
                "error": "untracked_inventory",
                "message": str(exc),
            }
        self.save_state()
        return {
            "ok": True,
            "cycle_id": self.cycle_id,
            "already_running": False,
            "message": "Démarré",
            "initial_buy": (self.engine.state.initial_buy or None),
        }

    def stop(self) -> dict[str, Any]:
        """Arrêt propre : annule les ordres ouverts, ferme le cycle en DB, ne vend PAS la position."""
        self.running = False
        cancelled = 0
        if self.engine.state.active:
            before_ids = [
                lv.order_id for lv in self.engine.state.levels if lv.order_id and lv.status == "open"
            ]
            try:
                self.engine.cancel_all_grid_orders()
                cancelled = len(before_ids)
            except Exception as exc:
                logger.error("stop cancel_all failed: %s", exc)
        closed_cycle_id = self.cycle_id
        if self.cycle_id:
            cycle = self.session.get(Cycle, self.cycle_id)
            if cycle and cycle.status == "open":
                cycle.status = "closed"
                cycle.closed_at = utcnow()
                cycle.close_reason = "user_stop"
                cycle.grid_profit = self.engine.state.grid_profit
                cycle.floating_profit = self.engine.state.floating_profit
                cycle.gross_pnl = self.engine.state.gross_pnl
                fees = (
                    self.session.query(FeePaid)
                    .filter(FeePaid.cycle_id == self.cycle_id)
                    .all()
                )
                cycle.net_pnl = float(cycle.gross_pnl) - sum(f.commission_usdt for f in fees)
                self.session.commit()
        self.engine.state.active = False
        self._out_of_range_since = None
        self._stuck_sell_since.clear()
        self._stopped_at = utcnow().isoformat()
        warning = residual_position_warning(
            self.client,
            self.cfg.symbol,
            self.bags.bags_qty(),
            entry_avg=self.engine.state.entry_avg,
        )
        self.save_state()
        result: dict[str, Any] = {
            "ok": True,
            "cancelled_orders": cancelled,
            "cycle_closed": closed_cycle_id,
            "message": "Arrêté — ordres annulés, position conservée",
            "stopped_at": self._stopped_at,
        }
        if warning:
            result["residual_position_warning"] = warning
        return result

    def panic(self) -> dict[str, Any]:
        """Panic : annule ordres, vend 100 % du base libre réel, clôture cycle + sacs."""
        result = self.guards.panic_close(self.bags, self.engine)
        self.running = False
        if self.cycle_id:
            self._close_cycle_db(
                {
                    "grid_profit": self.engine.state.grid_profit,
                    "floating_profit": self.engine.state.floating_profit,
                    "gross_pnl": self.engine.state.gross_pnl,
                },
                "panic_close",
            )
        # No-op propre si rien à vendre (solde < min_qty, pas de sacs)
        sold = result.get("sold_orders") or []
        bags = result.get("bags") or []
        base_before = float(result.get("base_before") or 0)
        try:
            min_qty = float(self.client.get_symbol_filters(self.cfg.symbol).min_qty)
        except Exception:
            min_qty = 0.0
        if not sold and not bags and base_before < min_qty:
            self.guards.state.panic = False
            result["noop"] = True
            result["message"] = "Rien à fermer"
        else:
            result["noop"] = False
            result["message"] = "Panic close exécuté"
        self.save_state()
        return result

    def _ensure_single_open_cycle(self, *, reason: str = "orphan_superseded") -> None:
        """Ferme tout cycle open du symbole sauf celui à conserver (cycle_id courant ou le plus récent)."""
        opens = (
            self.session.query(Cycle)
            .filter(Cycle.symbol == self.cfg.symbol, Cycle.status == "open")
            .order_by(Cycle.id.asc())
            .all()
        )
        if not opens:
            return
        open_ids = [c.id for c in opens]
        keep_id = self.cycle_id if self.cycle_id in open_ids else open_ids[-1]
        closed = 0
        for c in opens:
            if c.id == keep_id:
                continue
            c.status = "closed"
            c.closed_at = utcnow()
            c.close_reason = reason
            closed += 1
        if closed:
            self.session.commit()
            logger.warning(
                "Closed %s orphan open cycle(s) for %s keep_id=%s reason=%s",
                closed,
                self.cfg.symbol,
                keep_id,
                reason,
            )
        self.cycle_id = keep_id

    def _open_new_cycle(self) -> None:
        """Séquence unique : réserve le cycle en DB AVANT l'achat marché (anti-doublon)."""
        if self._opening_cycle:
            logger.warning("open_new_cycle ignored — already in progress cycle_id=%s", self.cycle_id)
            return
        self._opening_cycle = True
        cycle = None
        try:
            # Fermer tout open existant AVANT l'achat inventaire
            opens = (
                self.session.query(Cycle)
                .filter(Cycle.symbol == self.cfg.symbol, Cycle.status == "open")
                .all()
            )
            for c in opens:
                c.status = "closed"
                c.closed_at = utcnow()
                c.close_reason = "superseded_by_new_cycle"
            if opens:
                self.session.commit()
                logger.warning(
                    "Superseded %s open cycle(s) before opening new one: %s",
                    len(opens),
                    [c.id for c in opens],
                )

            # Réservation unique (index partiel) avant étape 2 marché
            cycle = Cycle(
                symbol=self.cfg.symbol,
                status="open",
                center_price=0.0,
                levels_json=[],
            )
            self.session.add(cycle)
            try:
                self.session.commit()
            except Exception as exc:
                self.session.rollback()
                logger.error("cycle reservation failed (anti-doublon): %s", exc)
                return
            self.session.refresh(cycle)
            self.cycle_id = cycle.id

            prior_entry = float(self.engine.state.entry_avg or 0)
            try:
                state = self.engine.open_grid(prior_entry_avg=prior_entry)
            except Exception:
                cycle.status = "closed"
                cycle.closed_at = utcnow()
                cycle.close_reason = "open_failed"
                self.session.commit()
                self.cycle_id = None
                raise

            cycle.center_price = float(state.center_price)
            cycle.levels_json = self.engine.levels_as_dict()
            # Coût achat initial rattaché au cycle (métadonnée dans levels_json via bot_state)
            self.session.commit()

            initial_buy = state.initial_buy
            if initial_buy and initial_buy.get("orderId"):
                self._record_fees_for_order(int(initial_buy["orderId"]))
                self.session.add(
                    Trade(
                        cycle_id=cycle.id,
                        symbol=self.cfg.symbol,
                        side="BUY",
                        price=float(initial_buy.get("avg_price") or state.center_price),
                        quantity=float(initial_buy.get("executedQty") or 0),
                        order_id=str(initial_buy["orderId"]),
                        level_index=None,
                    )
                )
                self.session.commit()
                # Persister le détail d'achat initial
                meta = self.session.query(BotState).filter(
                    BotState.key == f"cycle_meta_{cycle.id}"
                ).first()
                payload = {"initial_buy": initial_buy, "cycle_id": cycle.id}
                if not meta:
                    self.session.add(BotState(key=f"cycle_meta_{cycle.id}", value_json=payload))
                else:
                    meta.value_json = payload
                    meta.updated_at = utcnow()
                self.session.commit()

            self._last_fill_at = None
            self._out_of_range_since = None
            self._stuck_sell_since.clear()
            self.save_state()
        finally:
            self._opening_cycle = False

    def tick(self) -> dict[str, Any]:
        """Un cycle de surveillance (appelé en boucle)."""
        if not self.running:
            return {"running": False}
        symbol = self.cfg.symbol
        mark = float(self.client.ticker_price(symbol)["price"])
        # Position réelle Binance pour floating / garde-fous (pas de théorique)
        try:
            real_pos = self.engine.real_position_qty()
            # La part grille = réel - sacs (sacs déjà isolés virtuellement)
            bags_q = self.bags.bags_qty()
            # Si sacs long, position réelle inclut sacs ; grille = réel - sacs
            self.engine.state.position_qty = real_pos - bags_q
        except Exception as exc:
            logger.warning("real_position_qty unavailable: %s", exc)
        self._bootstrap_entry_avg_if_needed()
        # Prefer live WS mark when fresher than REST
        if self._live_mark:
            mark = self._live_mark
        self.engine.update_floating(mark)
        self._persist_live_pnl(mark)

        # Sync fills via open orders delta
        self._sync_fills()

        # Coupe progressive — franchissement par PRIX, qty = position RÉELLE
        self.cuts.observe_mark_price(mark)
        incomplete = self.engine.state.incomplete_indices()
        cut = self.cuts.evaluate(
            real_position_qty=self.engine.state.position_qty,
            entry_avg=self.engine.state.entry_avg,
            incomplete_indices=incomplete,
        )
        if cut and cut["qty"] > 0:
            if cut.get("tag") == "cut_with_incomplete_grid":
                self.client._log_attempt(
                    {
                        "symbol": symbol,
                        "side": "CUT",
                        "order_type": "TRANSFER",
                        "purpose": "risk_cut",
                        "client_order_id": f"cut-{cut['level']}-{cut['at']}",
                        "attempt_no": 1,
                        "outcome": "cut_with_incomplete_grid",
                        "http_status": None,
                        "binance_code": None,
                        "binance_msg": None,
                        "order_id": None,
                        "request_json": cut,
                        "response_json": None,
                        "verify_json": {
                            "incomplete_levels": cut.get("incomplete_levels"),
                            "real_qty": cut.get("qty"),
                            "theoretical_cut": cut.get("theoretical_cut"),
                            "gap_pct": cut.get("gap_pct"),
                        },
                        "grid_level": cut["level"],
                    }
                )
            if cut.get("alert_gap"):
                self._critical_alert(
                    "cut_gap_over_10pct",
                    (
                        f"Écart coupe théorique/réel {cut['gap_pct']:.1f}% > 10% "
                        f"(réel={cut['qty']}, théorique={cut['theoretical_cut']})"
                    ),
                    cut,
                )
            self.bags.create_bag(
                cut["qty"],
                cut["entry_price"],
                cut["level"],
                incomplete_levels=cut.get("incomplete_levels"),
                cycle_id_origin=self.cycle_id,
                market_price_at_creation=float(mark),
            )
            sign = 1 if self.engine.state.position_qty >= 0 else -1
            self.engine.state.position_qty -= sign * cut["qty"]
            if cut["level"] == self.cfg.cut_level_2:
                self.engine.cancel_all_grid_orders()
                self.engine.open_grid(
                    Decimal(str(mark)),
                    prior_entry_avg=self.engine.state.entry_avg,
                )

        # Garde-fous sur position totale RÉELLE
        try:
            total_qty = self.engine.real_position_qty()
        except Exception:
            total_qty = self.engine.state.position_qty + self.bags.bags_qty()
        total_entry = self._total_entry_avg()
        if self.guards.check_hard_stop(total_entry, mark, total_qty):
            self.guards.panic_close(self.bags, self.engine)
            self.running = False
        if self.guards.check_circuit_breaker():
            self.running = False

        if self.bags.open_bags():
            try:
                self.bags.maybe_snapshot_floating(float(mark))
            except Exception as exc:
                logger.debug("bag floating snapshot skipped: %s", exc)

        # Cycle +15
        if self.engine.should_close_cycle():
            result = self.engine.close_cycle()
            self._close_cycle_db(result, "trigger_15")
            self.guards.add_realized(result["gross_pnl"])
            # Appliquer config en attente ?
            if self._pending_config and self._pending_config_mode == "wait_cycle":
                self._apply_pending_config()
            if self.running:
                self._open_new_cycle()

        # Section 2bis — hors fourchette / SELL bloqué
        self._check_idle_recenter(mark)
        self._check_stuck_sells(mark)

        # Réconciliation
        recon = self.bags.reconcile(self.engine.state.position_qty)
        self._snapshot_pnl(mark)
        self.save_state()
        return {
            "running": self.running,
            "mark": mark,
            "gross_pnl": self.engine.state.gross_pnl,
            "recon": recon,
        }

    def _total_entry_avg(self) -> float:
        grid_qty = abs(self.engine.state.position_qty)
        grid_entry = self.engine.state.entry_avg
        bags = self.bags.open_bags()
        bags_qty = sum(b.quantity for b in bags)
        if grid_qty + bags_qty == 0:
            return 0.0
        bags_cost = sum(b.quantity * b.entry_price for b in bags)
        return (grid_qty * grid_entry + bags_cost) / (grid_qty + bags_qty)

    def _sync_fills(self) -> None:
        try:
            open_orders = self.client.open_orders(self.cfg.symbol)
        except Exception as exc:
            logger.error("sync fills: %s", exc)
            return
        live = {o["orderId"] for o in open_orders}
        for lv in self.engine.state.levels:
            if lv.status == "open" and lv.order_id and lv.order_id not in live:
                try:
                    got = self.client.get_order(self.cfg.symbol, lv.order_id)
                except Exception:
                    continue
                status = got.get("status")
                if status == "FILLED":
                    px = float(got.get("avgPrice") or lv.price)
                    qty = float(got.get("executedQty") or lv.quantity)
                    self.engine.on_fill(lv.index, px, qty)
                    self._last_fill_at = utcnow()
                    self._out_of_range_since = None
                    trade = Trade(
                        cycle_id=self.cycle_id,
                        symbol=self.cfg.symbol,
                        side=lv.side,
                        price=px,
                        quantity=qty,
                        order_id=str(lv.order_id),
                        level_index=lv.index,
                    )
                    self.session.add(trade)
                    self.session.commit()
                    self._record_fees_for_order(lv.order_id)

        self._recompute_grid_profit_from_db()

    def _grid_price_bounds(self) -> tuple[float | None, float | None]:
        prices = [float(lv.price) for lv in self.engine.state.levels]
        if not prices:
            return None, None
        return min(prices), max(prices)

    def _check_idle_recenter(self, mark: float) -> None:
        """Cas A §2bis : hors fourchette + aucun fill grille → fermer et rouvrir (séquence complète)."""
        if not self.engine.state.active or not self.running:
            return
        low, high = self._grid_price_bounds()
        if low is None or high is None:
            return
        now = utcnow()
        out = mark < low or mark > high
        if not out:
            self._out_of_range_since = None
            return
        if self._out_of_range_since is None:
            self._out_of_range_since = now
            return
        elapsed_min = (now - self._out_of_range_since).total_seconds() / 60.0
        if elapsed_min < float(self.cfg.idle_recenter_min):
            return
        # Aucun fill de grille depuis l'ouverture (l'inventaire SELL initial ne compte pas)
        if self._last_fill_at is not None:
            return
        try:
            filters = self.client.get_symbol_filters(self.cfg.symbol)
            base_total = self.client.balance_total(filters.base_asset, force=True)
        except Exception as exc:
            logger.error("idle_recenter balance check failed: %s", exc)
            return
        proof = {
            "mark": mark,
            "range_low": low,
            "range_high": high,
            "out_of_range_min": elapsed_min,
            "base_total": base_total,
            "note": "inventaire SELL initial autorisé; recentrage si aucun fill grille",
        }
        logger.warning("idle_recenter_no_fill %s", proof)
        result = self.engine.close_cycle()
        # Aplatir l'inventaire résiduel pour forcer un nouvel achat initial à la réouverture
        try:
            filters = self.client.get_symbol_filters(self.cfg.symbol, force=True)
            free = float(self.client.balance_free(filters.base_asset, force=True))
            bags_q = self.bags.bags_qty()
            sell_amt = free - bags_q
            if sell_amt >= float(filters.min_qty):
                from decimal import Decimal, ROUND_DOWN

                step = filters.step_size
                qty = (Decimal(str(sell_amt)) / step).to_integral_value(rounding=ROUND_DOWN) * step
                if qty >= filters.min_qty:
                    px = Decimal(str(mark))
                    if qty * px >= filters.min_notional:
                        flat = self.client.place_order(
                            symbol=self.cfg.symbol,
                            side="SELL",
                            order_type="MARKET",
                            quantity=qty,
                            purpose="idle_recenter_flatten",
                        )
                        result["flatten_order"] = flat
                        proof["flatten_qty"] = float(qty)
        except Exception as exc:
            logger.warning("idle_recenter flatten failed: %s", exc)
        self._close_cycle_db(result, "idle_recenter_no_fill")
        self.client._log_attempt(
            {
                "symbol": self.cfg.symbol,
                "side": "NONE",
                "order_type": "RECENTER",
                "purpose": "idle_recenter_no_fill",
                "client_order_id": f"idle-{int(now.timestamp())}",
                "attempt_no": 1,
                "outcome": "idle_recenter_no_fill",
                "request_json": proof,
                "response_json": result,
                "verify_json": proof,
            }
        )
        self._out_of_range_since = None
        self._last_fill_at = None
        if self.running:
            self._open_new_cycle()

    def _check_stuck_sells(self, mark: float) -> None:
        """Cas B §2bis : SELL open, prix déjà au-dessus depuis trop longtemps → market sell."""
        if not self.engine.state.active or not self.running:
            return
        now = utcnow()
        threshold = float(self.cfg.stuck_sell_min)
        for lv in self.engine.state.levels:
            if lv.side != "SELL" or lv.status != "open" or not lv.order_id:
                self._stuck_sell_since.pop(lv.index, None)
                continue
            if mark + 1e-12 < float(lv.price):
                self._stuck_sell_since.pop(lv.index, None)
                continue
            since = self._stuck_sell_since.get(lv.index)
            if since is None:
                self._stuck_sell_since[lv.index] = now
                continue
            elapsed_min = (now - since).total_seconds() / 60.0
            if elapsed_min < threshold:
                continue
            qty = float(lv.quantity)
            proof = {
                "level": lv.index,
                "sell_price": float(lv.price),
                "mark": mark,
                "stuck_min": elapsed_min,
                "qty": qty,
                "order_id": lv.order_id,
            }
            try:
                self.client.cancel_order(self.cfg.symbol, order_id=lv.order_id)
            except Exception as exc:
                logger.warning("stuck sell cancel failed: %s", exc)
            try:
                order = self.client.place_order(
                    symbol=self.cfg.symbol,
                    side="SELL",
                    order_type="MARKET",
                    quantity=qty,
                    purpose="forced_sell_stuck_level",
                    grid_level=lv.index,
                )
                fill_px = float(order.get("fills", [{}])[0].get("price") or mark) if order.get("fills") else mark
                if order.get("fills"):
                    fill_px = sum(
                        float(f["price"]) * float(f["qty"]) for f in order["fills"]
                    ) / sum(float(f["qty"]) for f in order["fills"])
                self.engine.on_fill(lv.index, fill_px, qty)
                self._last_fill_at = utcnow()
                trade = Trade(
                    cycle_id=self.cycle_id,
                    symbol=self.cfg.symbol,
                    side="SELL",
                    price=fill_px,
                    quantity=qty,
                    order_id=str(order.get("orderId")),
                    level_index=lv.index,
                )
                self.session.add(trade)
                self.session.commit()
                if order.get("orderId"):
                    self._record_fees_for_order(int(order["orderId"]))
                proof["market_order"] = order
                self.client._log_attempt(
                    {
                        "symbol": self.cfg.symbol,
                        "side": "SELL",
                        "order_type": "MARKET",
                        "purpose": "forced_sell_stuck_level",
                        "client_order_id": order.get("clientOrderId") or f"stuck-{lv.index}",
                        "attempt_no": 1,
                        "outcome": "forced_sell_stuck_level",
                        "order_id": str(order.get("orderId")),
                        "request_json": proof,
                        "response_json": order,
                        "verify_json": proof,
                        "grid_level": lv.index,
                    }
                )
            except Exception as exc:
                logger.error("forced_sell_stuck_level failed: %s", exc)
                self._critical_alert("forced_sell_stuck_level_failed", str(exc), proof)
            self._stuck_sell_since.pop(lv.index, None)

    def _record_fees_for_order(self, order_id: int | None) -> None:
        if not order_id:
            return
        try:
            trades = self.client.my_trades(self.cfg.symbol, limit=50, order_id=int(order_id))
        except Exception as exc:
            logger.warning("myTrades failed order_id=%s: %s", order_id, exc)
            return
        for t in trades:
            tid = str(t.get("id"))
            exists = self.session.query(FeePaid).filter(FeePaid.trade_id == tid).first()
            if exists:
                continue
            commission = float(t.get("commission") or 0)
            asset = str(t.get("commissionAsset") or "")
            price = float(t.get("price") or 0)
            qty = float(t.get("qty") or 0)
            bnb_px = None
            if asset.upper() == "BNB":
                try:
                    bnb_px = float(self.client.ticker_price("BNBUSDT", force=True)["price"])
                except Exception:
                    bnb_px = None
            commission_usdt = commission_to_usdt(
                commission, asset, trade_price=price, bnb_usdt_price=bnb_px
            )
            row = FeePaid(
                symbol=self.cfg.symbol,
                order_id=str(order_id),
                trade_id=tid,
                commission=commission,
                commission_asset=asset,
                commission_usdt=commission_usdt,
                cycle_id=self.cycle_id,
                is_buyer=t.get("isBuyer"),
                price=price,
                qty=qty,
            )
            self.session.add(row)
        self.session.commit()

    def _close_cycle_db(self, result: dict, reason: str) -> None:
        if not self.cycle_id:
            return
        cycle = self.session.get(Cycle, self.cycle_id)
        if not cycle:
            return
        cycle.status = "closed"
        cycle.grid_profit = result.get("grid_profit", 0.0)
        cycle.floating_profit = result.get("floating_profit", 0.0)
        cycle.funding_pnl = 0.0  # Spot : pas de funding
        fees = (
            self.session.query(FeePaid)
            .filter(FeePaid.cycle_id == self.cycle_id)
            .all()
        )
        fees_usdt = sum(f.commission_usdt for f in fees)
        cycle.gross_pnl = result.get("gross_pnl", 0.0)
        cycle.net_pnl = float(cycle.gross_pnl) - fees_usdt
        cycle.closed_at = utcnow()
        cycle.close_reason = reason
        self.session.commit()

    def _snapshot_pnl(self, mark: float) -> None:
        closed = (
            self.session.query(Cycle)
            .filter(Cycle.symbol == self.cfg.symbol, Cycle.status == "closed")
            .all()
        )
        closed_pnl = sum(c.net_pnl for c in closed)
        bags_pnl = 0.0
        for b in self.bags.open_bags():
            bags_pnl += (mark - b.entry_price) * b.quantity
        snap = PnlSnapshot(
            symbol=self.cfg.symbol,
            grid_pnl=self.engine.state.gross_pnl,
            bags_pnl=bags_pnl,
            closed_cycles_pnl=closed_pnl,
            cumulative_pnl=closed_pnl + self.engine.state.gross_pnl + bags_pnl,
        )
        levels = self.engine.levels_as_dict()
        placed = [
            float(lv["price"])
            for lv in levels
            if lv.get("status") in ("open", "pending", "filled")
        ]
        tick = PriceTick(
            symbol=self.cfg.symbol,
            price=mark,
            range_low=min(placed) if placed else None,
            range_high=max(placed) if placed else None,
        )
        self.session.add(snap)
        self.session.add(tick)
        self.session.commit()

    def _rebind_subsystems(self) -> None:
        """Recrée moteur/coupe/sacs/garde-fous après changement de config (symbole inclus)."""
        self.engine = GridEngine(
            self.client, self.cfg, on_level_incomplete=self._on_level_incomplete
        )
        self.cuts = ProgressiveCutManager(self.engine, self.cfg)
        self.bags = BagManager(self.client, self.session, self.cfg)
        self.guards = SafetyGuards(self.client, self.session, self.cfg)

    def request_config_change(self, new_cfg: StrategyConfig, mode: str) -> dict[str, Any]:
        errors = new_cfg.validate()
        if errors:
            return {"ok": False, "errors": errors}
        if self.engine.state.active and self.running:
            if mode not in ("wait_cycle", "close_now"):
                return {"ok": False, "errors": ["mode requis: wait_cycle|close_now"]}
            self._pending_config = new_cfg
            self._pending_config_mode = mode
            self._persist_config(new_cfg, active=False)
            if mode == "close_now":
                result = self.engine.close_cycle()
                self._close_cycle_db(result, "config_change")
                self._apply_pending_config()
                if self.running:
                    self._open_new_cycle()
            return {"ok": True, "pending": mode == "wait_cycle", "applied": mode == "close_now"}
        self.cfg = new_cfg
        self._rebind_subsystems()
        self._persist_config(new_cfg, active=True)
        self.restart_price_stream()
        return {"ok": True, "applied": True}

    def _apply_pending_config(self) -> None:
        if not self._pending_config:
            return
        old_symbol = self.cfg.symbol
        self.cfg = self._pending_config
        self._rebind_subsystems()
        self._persist_config(self.cfg, active=True)
        self._pending_config = None
        self._pending_config_mode = None
        if old_symbol != self.cfg.symbol:
            self._live_mark = None
            self.client._last_ticker.pop(old_symbol, None)
            self.restart_price_stream()

    def status(self) -> dict[str, Any]:
        mark = None
        mark_stale = False
        mark_error = None
        try:
            mark = float(self.client.ticker_price(self.cfg.symbol)["price"])
            self.engine.update_floating(mark)
        except Exception as exc:
            mark_error = str(exc)
            mark = self.client.last_ticker_price(self.cfg.symbol)
            mark_stale = mark is not None
            if mark is not None:
                self.engine.update_floating(mark)
        account = self.client.capital_snapshot(self.cfg.symbol)
        levels = self.engine.levels_as_dict()
        # Range / marge : ignorer les paliers incomplets (pas d'ordre = pas de réservation)
        placed_prices = [
            float(lv["price"])
            for lv in levels
            if lv.get("status") not in ("grid_level_incomplete", "error", "pending")
        ]
        incomplete = [lv for lv in levels if lv.get("status") == "grid_level_incomplete"]
        return {
            "running": self.running,
            "symbol": self.cfg.symbol,
            "mark_price": mark,
            "mark_stale": mark_stale,
            "mark_error": mark_error,
            "grid": {
                "active": self.engine.state.active,
                "center_price": float(self.engine.state.center_price) if self.engine.state.center_price else None,
                "range_low": min(placed_prices) if placed_prices else None,
                "range_high": max(placed_prices) if placed_prices else None,
                "position_qty": self.engine.state.position_qty,
                "entry_avg": self.engine.state.entry_avg,
                "grid_profit": self.engine.state.grid_profit,
                "floating_profit": self.engine.state.floating_profit,
                "gross_pnl": self.engine.state.gross_pnl,
                "levels": levels,
                "incomplete_levels": incomplete,
                "incomplete_count": len(incomplete),
            },
            "bags": [
                {
                    "id": b.id,
                    "quantity": b.quantity,
                    "entry_price": b.entry_price,
                    "status": b.status,
                    "cut_level": b.cut_level,
                    "realized_pnl": b.realized_pnl,
                }
                for b in self.bags.open_bags()
            ],
            "capital": account,
            "margin": account,  # alias rétrocompat UI
            "guards": {
                "daily_pnl": self.guards.state.daily_pnl,
                "hard_stop": self.guards.state.hard_stop_triggered,
                "circuit_breaker": self.guards.state.circuit_breaker_triggered,
                "panic": self.guards.state.panic,
            },
            "config": self.cfg.to_dict(),
            "cycle_id": self.cycle_id,
        }


    def _bootstrap_entry_avg_if_needed(self) -> None:
        """Si solde réel sans entry_avg (ex. position hors grille), inférer depuis myTrades."""
        if self.engine.state.position_qty <= 0 or self.engine.state.entry_avg:
            return
        try:
            trades = self.client.my_trades(self.cfg.symbol, limit=30)
            buys = [t for t in trades if t.get("isBuyer")]
            if not buys:
                return
            # moyenne pondérée des achats récents
            qty = sum(float(t.get("qty") or 0) for t in buys)
            cost = sum(float(t.get("qty") or 0) * float(t.get("price") or 0) for t in buys)
            if qty > 0:
                self.engine.state.entry_avg = cost / qty
                logger.info("bootstrapped entry_avg=%.4f from myTrades", self.engine.state.entry_avg)
        except Exception as exc:
            logger.warning("bootstrap entry_avg failed: %s", exc)

    def _persist_live_pnl(self, mark: float) -> None:
        """Écrit le PnL flottant recalculé (source WS ou tick) pour l'API/UI."""
        now = time.time()
        if now - self._last_live_persist < 0.1:
            return
        self._last_live_persist = now
        payload = {
            "mark": mark,
            "floating_profit": self.engine.state.floating_profit,
            "grid_profit": self.engine.state.grid_profit,
            "gross_pnl": self.engine.state.gross_pnl,
            "position_qty": self.engine.state.position_qty,
            "entry_avg": self.engine.state.entry_avg,
            "ts": utcnow().isoformat(),
            "source": "ws" if self._live_mark == mark else "rest",
        }
        # Session dédiée : le thread WS ne partage pas self.session
        factory = self._session_factory
        if factory is None:
            return
        s = factory()
        try:
            row = s.query(BotState).filter(BotState.key == "live_pnl").first()
            if not row:
                s.add(BotState(key="live_pnl", value_json=payload))
            else:
                row.value_json = payload
                row.updated_at = utcnow()
            main = s.query(BotState).filter(BotState.key == "main").first()
            if main and isinstance(main.value_json, dict):
                data = dict(main.value_json)
                grid = dict(data.get("grid") or {})
                grid["floating_profit"] = payload["floating_profit"]
                grid["grid_profit"] = payload["grid_profit"]
                grid["position_qty"] = payload["position_qty"]
                grid["entry_avg"] = payload["entry_avg"]
                data["grid"] = grid
                main.value_json = data
                main.updated_at = utcnow()
            s.commit()
        except Exception:
            s.rollback()
            logger.exception("persist live_pnl failed")
        finally:
            s.close()

    def on_ws_price(self, data: dict) -> None:
        """Appelé à chaque bookTicker — recalcule le floating immédiatement."""
        try:
            stream_sym = (data.get("s") or "").upper()
            if stream_sym and stream_sym != self.cfg.symbol.upper():
                logger.warning(
                    "WS price ignored: stream=%s cfg=%s p=%s",
                    stream_sym,
                    self.cfg.symbol,
                    data.get("p"),
                )
                return
            mark = float(data.get("p") or 0)
            if mark <= 0:
                return
            self._live_mark = mark
            self.client._last_ticker[self.cfg.symbol] = mark
            if self.engine.state.position_qty > 0 and not self.engine.state.entry_avg:
                self._bootstrap_entry_avg_if_needed()
            self.engine.update_floating(mark)
            self._persist_live_pnl(mark)
        except Exception:
            logger.exception("on_ws_price failed")

    def stop_price_stream(self, *, join_timeout: float = 3.0) -> None:
        """Arrête le thread WS bookTicker (ex. changement de symbole)."""
        self._ws_stop = True
        t = self._ws_thread
        if t and t.is_alive():
            t.join(timeout=join_timeout)
        self._ws_thread = None
        self._ws_stream_symbol = None

    def restart_price_stream(self) -> None:
        """Relance le WS sur self.cfg.symbol (no-op si déjà sur le bon flux)."""
        sym = self.cfg.symbol
        if self._ws_thread and self._ws_thread.is_alive() and self._ws_stream_symbol == sym:
            return
        self.stop_price_stream()
        self._ws_stop = False
        self._live_mark = None
        self.start_price_stream()

    def start_price_stream(self) -> None:
        """Thread daemon : WS bookTicker → floating à chaque tick."""
        import threading

        sym = self.cfg.symbol

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            stop = asyncio.Event()

            async def _guard() -> None:
                while not self._ws_stop:
                    await asyncio.sleep(0.5)
                stop.set()

            async def _main() -> None:
                await asyncio.gather(
                    self.client.stream_mark_price(sym, self.on_ws_price, stop_event=stop),
                    _guard(),
                )

            try:
                loop.run_until_complete(_main())
            except Exception:
                logger.exception("price stream stopped")
            finally:
                loop.close()

        self._ws_stream_symbol = sym
        t = threading.Thread(target=_run, name=f"ultium-ws-price-{sym.lower()}", daemon=True)
        t.start()
        self._ws_thread = t
        logger.info("WS price stream thread started for %s", sym)


def build_client_from_env() -> BinanceSpotClient:
    # Accepte BINANCE_SPOT_* ou anciennes variables Futures (migration)
    key = (
        os.getenv("BINANCE_SPOT_TESTNET_API_KEY")
        or os.getenv("BINANCE_FUTURES_TESTNET_API_KEY")
        or ""
    ).strip()
    secret = (
        os.getenv("BINANCE_SPOT_TESTNET_API_SECRET")
        or os.getenv("BINANCE_FUTURES_TESTNET_API_SECRET")
        or ""
    ).strip()
    if not key or not secret:
        raise KeyError("BINANCE_SPOT_TESTNET_API_KEY / _SECRET manquants dans .env")
    return BinanceSpotClient(api_key=key, api_secret=secret)


def main_loop(database_url: str, poll_seconds: float = 5.0) -> None:
    from ultiumgrid.control import pop_commands
    from ultiumgrid.db.models import make_session_factory
    from ultiumgrid.engine.config import StrategyConfig

    SessionLocal, _ = make_session_factory(database_url)
    session = SessionLocal()
    client = build_client_from_env()
    bot = BotRunner(client, session)
    bot._session_factory = SessionLocal
    bot.restore_state()
    bot.restart_price_stream()
    logger.info("Bot started, running=%s symbol=%s", bot.running, bot.cfg.symbol)

    def _write_heartbeat() -> None:
        from ultiumgrid.db.models import BotState, utcnow

        row = session.query(BotState).filter(BotState.key == "heartbeat").first()
        payload = {
            "ts": utcnow().isoformat(),
            "running": bot.running,
            "cycle_id": bot.cycle_id,
            "pid": os.getpid(),
        }
        if not row:
            session.add(BotState(key="heartbeat", value_json=payload))
        else:
            row.value_json = payload
            row.updated_at = utcnow()
        session.commit()

    while True:
        try:
            _write_heartbeat()
            for cmd in pop_commands(session):
                name = cmd.get("name")
                payload = cmd.get("payload") or {}
                logger.info("Command received: %s", name)
                if name == "start":
                    result = bot.start()
                    bot._store_command_result("start", result)
                elif name == "stop":
                    result = bot.stop()
                    bot._store_command_result("stop", result)
                elif name == "panic":
                    result = bot.panic()
                    bot._store_command_result("panic", result)
                elif name == "config":
                    cfg = StrategyConfig.from_dict(payload.get("params") or {})
                    bot.request_config_change(cfg, payload.get("mode") or "wait_cycle")
                elif name == "sell_bag":
                    bot.bags.sell_bag(
                        int(payload["bag_id"]),
                        payload.get("order_type") or "MARKET",
                        payload.get("limit_price"),
                    )
            if bot.running:
                bot.tick()
            else:
                # Même hors trading : synchroniser position + floating (WS complète entre les polls)
                try:
                    mark = float(client.ticker_price(bot.cfg.symbol)["price"])
                    if bot._live_mark:
                        mark = bot._live_mark
                    real_pos = bot.engine.real_position_qty()
                    bags_q = bot.bags.bags_qty()
                    bot.engine.state.position_qty = max(0.0, real_pos - bags_q)
                    bot._bootstrap_entry_avg_if_needed()
                    bot.engine.update_floating(mark)
                    bot._persist_live_pnl(mark)
                    from ultiumgrid.db.models import PriceTick

                    session.add(
                        PriceTick(symbol=bot.cfg.symbol, price=mark, range_low=None, range_high=None)
                    )
                    session.commit()
                except Exception:
                    pass
                bot.save_state()
        except Exception:
            logger.exception("tick failed")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db_url = os.getenv("DATABASE_URL", "sqlite:////data/ultiumgrid.db")
    main_loop(db_url)
