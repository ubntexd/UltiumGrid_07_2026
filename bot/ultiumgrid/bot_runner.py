"""Boucle principale du bot — grille, coupe, sacs, garde-fous, reprise."""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ultiumgrid.bags.manager import BagManager
from ultiumgrid.connector.binance_futures import BinanceFuturesClient
from ultiumgrid.db.models import BotState, Configuration, Cycle, PnlSnapshot, Trade, utcnow
from ultiumgrid.engine.config import StrategyConfig
from ultiumgrid.engine.grid import GridEngine
from ultiumgrid.guards.safety import SafetyGuards
from ultiumgrid.risk.cuts import ProgressiveCutManager

logger = logging.getLogger(__name__)


class BotRunner:
    def __init__(self, client: BinanceFuturesClient, session: Session, cfg: StrategyConfig | None = None):
        self.client = client
        self.session = session
        self.cfg = cfg or self._load_active_config()
        self.engine = GridEngine(client, self.cfg)
        self.cuts = ProgressiveCutManager(self.engine, self.cfg)
        self.bags = BagManager(client, session, self.cfg)
        self.guards = SafetyGuards(client, session, self.cfg)
        self.running = False
        self.cycle_id: int | None = None
        self._pending_config: StrategyConfig | None = None
        self._pending_config_mode: str | None = None  # wait_cycle | close_now

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
                "funding_pnl": self.engine.state.funding_pnl,
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
        self.engine = GridEngine(self.client, self.cfg)
        self.cuts = ProgressiveCutManager(self.engine, self.cfg)
        self.bags = BagManager(self.client, self.session, self.cfg)
        self.guards = SafetyGuards(self.client, self.session, self.cfg)
        g = data.get("grid") or {}
        self.engine.state.center_price = Decimal(g.get("center_price") or "0")
        self.engine.state.position_qty = float(g.get("position_qty") or 0)
        self.engine.state.entry_avg = float(g.get("entry_avg") or 0)
        self.engine.state.grid_profit = float(g.get("grid_profit") or 0)
        self.engine.state.floating_profit = float(g.get("floating_profit") or 0)
        self.engine.state.funding_pnl = float(g.get("funding_pnl") or 0)
        self.engine.state.active = bool(g.get("active"))
        self.engine.state.deepest_buy_index = int(g.get("deepest_buy_index") or -1)
        # Reconstruire niveaux depuis DB ; ordres réels via reconcile Binance
        from ultiumgrid.engine.grid import GridLevel

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
                )
            )
        self.engine.state.levels = levels
        self.cycle_id = data.get("cycle_id")
        self.running = bool(data.get("running"))
        # Réconciliation ordres ouverts Binance vs DB
        self._reconcile_orders_after_crash()
        logger.info("State restored cycle_id=%s running=%s", self.cycle_id, self.running)
        return True

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
        if self.guards.state.panic or self.guards.state.circuit_breaker_triggered:
            return {"ok": False, "error": "Bot bloqué par garde-fou"}
        self.running = True
        if not self.engine.state.active:
            self._open_new_cycle()
        self.save_state()
        return {"ok": True, "cycle_id": self.cycle_id}

    def stop(self) -> dict[str, Any]:
        self.running = False
        self.save_state()
        return {"ok": True}

    def _open_new_cycle(self) -> None:
        state = self.engine.open_grid()
        cycle = Cycle(
            symbol=self.cfg.symbol,
            status="open",
            center_price=float(state.center_price),
            levels_json=self.engine.levels_as_dict(),
        )
        self.session.add(cycle)
        self.session.commit()
        self.session.refresh(cycle)
        self.cycle_id = cycle.id
        self.save_state()

    def tick(self) -> dict[str, Any]:
        """Un cycle de surveillance (appelé en boucle)."""
        if not self.running:
            return {"running": False}
        symbol = self.cfg.symbol
        mark = float(self.client.ticker_price(symbol)["price"])
        self.engine.update_floating(mark)

        # Sync fills via open orders delta
        self._sync_fills()

        # Coupe progressive
        if self.engine.state.deepest_buy_index >= 0:
            self.cuts.observe_level(self.engine.state.deepest_buy_index)
        cut = self.cuts.evaluate(self.engine.state.position_qty, self.engine.state.entry_avg)
        if cut and cut["qty"] > 0:
            bag = self.bags.create_bag(cut["qty"], cut["entry_price"], cut["level"])
            # Réduire position grille virtuelle (la position réelle reste, transférée en sac)
            sign = 1 if self.engine.state.position_qty >= 0 else -1
            self.engine.state.position_qty -= sign * cut["qty"]
            if cut["level"] == self.cfg.cut_level_2:
                # Recentrage après coupe totale
                self.engine.cancel_all_grid_orders()
                self.engine.open_grid(Decimal(str(mark)))

        # Garde-fous
        total_qty = self.engine.state.position_qty + self.bags.bags_qty()
        total_entry = self._total_entry_avg()
        if self.guards.check_hard_stop(total_entry, mark, total_qty):
            self.guards.panic_close(self.bags, self.engine)
            self.running = False
        if self.guards.check_circuit_breaker():
            self.running = False

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

    def _close_cycle_db(self, result: dict, reason: str) -> None:
        if not self.cycle_id:
            return
        cycle = self.session.get(Cycle, self.cycle_id)
        if not cycle:
            return
        cycle.status = "closed"
        cycle.grid_profit = result["grid_profit"]
        cycle.floating_profit = result["floating_profit"]
        cycle.funding_pnl = result["funding_pnl"]
        cycle.gross_pnl = result["gross_pnl"]
        cycle.net_pnl = result["gross_pnl"]  # frais non séparés ici
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
        self.session.add(snap)
        self.session.commit()

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
        self.engine.cfg = new_cfg
        self._persist_config(new_cfg, active=True)
        return {"ok": True, "applied": True}

    def _apply_pending_config(self) -> None:
        if not self._pending_config:
            return
        self.cfg = self._pending_config
        self.engine = GridEngine(self.client, self.cfg)
        self.cuts = ProgressiveCutManager(self.engine, self.cfg)
        self.bags = BagManager(self.client, self.session, self.cfg)
        self.guards = SafetyGuards(self.client, self.session, self.cfg)
        self._persist_config(self.cfg, active=True)
        self._pending_config = None
        self._pending_config_mode = None

    def status(self) -> dict[str, Any]:
        mark = None
        try:
            mark = float(self.client.ticker_price(self.cfg.symbol)["price"])
            self.engine.update_floating(mark)
        except Exception:
            pass
        account = {}
        try:
            acc = self.client.account()
            account = {
                "availableBalance": acc.get("availableBalance"),
                "totalWalletBalance": acc.get("totalWalletBalance"),
                "totalUnrealizedProfit": acc.get("totalUnrealizedProfit"),
            }
        except Exception as exc:
            account = {"error": str(exc)}
        levels = self.engine.levels_as_dict()
        prices = [float(lv["price"]) for lv in levels] if levels else []
        return {
            "running": self.running,
            "symbol": self.cfg.symbol,
            "mark_price": mark,
            "grid": {
                "active": self.engine.state.active,
                "center_price": float(self.engine.state.center_price) if self.engine.state.center_price else None,
                "range_low": min(prices) if prices else None,
                "range_high": max(prices) if prices else None,
                "position_qty": self.engine.state.position_qty,
                "entry_avg": self.engine.state.entry_avg,
                "grid_profit": self.engine.state.grid_profit,
                "floating_profit": self.engine.state.floating_profit,
                "funding_pnl": self.engine.state.funding_pnl,
                "gross_pnl": self.engine.state.gross_pnl,
                "levels": levels,
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
            "margin": account,
            "guards": {
                "daily_pnl": self.guards.state.daily_pnl,
                "hard_stop": self.guards.state.hard_stop_triggered,
                "circuit_breaker": self.guards.state.circuit_breaker_triggered,
                "panic": self.guards.state.panic,
            },
            "config": self.cfg.to_dict(),
            "cycle_id": self.cycle_id,
        }


def build_client_from_env() -> BinanceFuturesClient:
    return BinanceFuturesClient(
        api_key=os.environ["BINANCE_FUTURES_TESTNET_API_KEY"],
        api_secret=os.environ["BINANCE_FUTURES_TESTNET_API_SECRET"],
    )


def main_loop(database_url: str, poll_seconds: float = 5.0) -> None:
    from ultiumgrid.control import pop_commands
    from ultiumgrid.db.models import make_session_factory
    from ultiumgrid.engine.config import StrategyConfig

    SessionLocal, _ = make_session_factory(database_url)
    session = SessionLocal()
    client = build_client_from_env()
    bot = BotRunner(client, session)
    bot.restore_state()
    logger.info("Bot started, running=%s", bot.running)
    while True:
        try:
            for cmd in pop_commands(session):
                name = cmd.get("name")
                payload = cmd.get("payload") or {}
                logger.info("Command received: %s", name)
                if name == "start":
                    bot.start()
                elif name == "stop":
                    bot.stop()
                elif name == "panic":
                    bot.guards.panic_close(bot.bags, bot.engine)
                    bot.running = False
                    bot.save_state()
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
                bot.save_state()
        except Exception:
            logger.exception("tick failed")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db_url = os.getenv("DATABASE_URL", "sqlite:////data/ultiumgrid.db")
    main_loop(db_url)
