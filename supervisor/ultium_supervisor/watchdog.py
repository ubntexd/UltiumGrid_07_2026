"""Boucle de supervision — process séparé du bot.

Lit Binance et la DB en lecture seule (tables métier), écrit uniquement
dans supervisor_alerts / supervisor_metrics / supervisor_state.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _as_dict(val: Any) -> dict:
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


def _as_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None

# Client Spot partagé en lecture seule (instance séparée du bot)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from ultiumgrid.bot_runner import build_client_from_env  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.orphan_position import (  # noqa: E402
    ORPHAN_MIN_NOTIONAL_USDT,
    ORPHAN_STOPPED_MIN_S,
    floating_pnl_vs_entry,
    orphan_qty,
)

from ultium_supervisor.models import (  # noqa: E402
    SupervisorAlert,
    SupervisorMetric,
    SupervisorState,
    make_session_factory,
    utcnow,
)

logger = logging.getLogger(__name__)

class Watchdog:
    def __init__(self, database_url: str):
        self.SessionLocal, self.engine = make_session_factory(database_url)
        # Engine lecture seule tables bot (même DB, pas d'écriture métier)
        self.bot_engine = create_engine(database_url, pool_pre_ping=True)
        self.client = build_client_from_env()
        self._active_kinds: set[str] = set()
        self.heartbeat_timeout_s = float(os.getenv("SUPERVISOR_HEARTBEAT_TIMEOUT_S", "90"))
        self.recon_threshold_usdt = float(os.getenv("SUPERVISOR_RECON_THRESHOLD_USDT", "1.0"))
        self.market_anomaly_pct = float(os.getenv("SUPERVISOR_MARKET_ANOMALY_PCT", "0.5"))
        self.poll_s = float(os.getenv("SUPERVISOR_POLL_S", "20"))
        self.bot_health_url = os.getenv(
            "SUPERVISOR_BOT_HEALTH_URL", "http://backend:8000/health"
        )
        self.emergency_action = os.getenv("SUPERVISOR_EMERGENCY_ACTION", "false").lower() in (
            "1",
            "true",
            "yes",
        )

    def _session(self):
        return self.SessionLocal()

    def emit(
        self,
        severity: str,
        kind: str,
        message: str,
        payload: dict | None = None,
    ) -> None:
        session = self._session()
        try:
            # éviter spam : une seule active par kind
            existing = (
                session.query(SupervisorAlert)
                .filter(
                    SupervisorAlert.kind == kind,
                    SupervisorAlert.status == "active",
                )
                .first()
            )
            if existing:
                existing.message = message
                existing.payload_json = payload
                existing.created_at = utcnow()
            else:
                session.add(
                    SupervisorAlert(
                        severity=severity,
                        kind=kind,
                        message=message,
                        payload_json=payload,
                        status="active",
                    )
                )
            session.commit()
            self._active_kinds.add(kind)
            log = logger.critical if severity == "alert" else logger.info
            log("[%s] %s: %s", severity, kind, message)
            if severity == "emergency_action" and self.emergency_action:
                self._emergency_panic(payload)
        finally:
            session.close()

    def resolve(self, kind: str) -> None:
        session = self._session()
        try:
            for a in (
                session.query(SupervisorAlert)
                .filter(SupervisorAlert.kind == kind, SupervisorAlert.status == "active")
                .all()
            ):
                a.status = "resolved"
                a.resolved_at = utcnow()
            session.commit()
            self._active_kinds.discard(kind)
        finally:
            session.close()

    def metric(self, kind: str, value: float, payload: dict | None = None) -> None:
        session = self._session()
        try:
            session.add(
                SupervisorMetric(kind=kind, value=value, payload_json=payload)
            )
            session.commit()
        finally:
            session.close()

    def set_state(self, key: str, value: dict) -> None:
        session = self._session()
        try:
            row = session.query(SupervisorState).filter(SupervisorState.key == key).first()
            if not row:
                session.add(SupervisorState(key=key, value_json=value))
            else:
                row.value_json = value
                row.updated_at = utcnow()
            session.commit()
        finally:
            session.close()

    def _emergency_panic(self, payload: dict | None) -> None:
        """Désactivé par défaut — uniquement si SUPERVISOR_EMERGENCY_ACTION=true."""
        logger.critical("EMERGENCY_ACTION activé — tentative panic via backend")
        try:
            requests.post(
                self.bot_health_url.replace("/health", "/api/panic"),
                timeout=10,
            )
        except Exception as exc:
            logger.error("emergency panic failed: %s", exc)

    # --- Checks ---

    def check_heartbeat(self) -> None:
        # 1) HTTP /health backend
        http_ok = False
        http_payload: dict[str, Any] = {}
        try:
            t0 = time.time()
            r = requests.get(self.bot_health_url, timeout=5)
            latency_ms = (time.time() - t0) * 1000
            http_ok = r.status_code == 200 and r.json().get("ok") is True
            http_payload = {"status": r.status_code, "body": r.json(), "latency_ms": latency_ms}
        except Exception as exc:
            http_payload = {"error": str(exc)}

        # 2) Heartbeat bot en DB (écrit par le bot)
        hb_age = None
        hb_raw = None
        try:
            with self.bot_engine.connect() as conn:
                row = conn.execute(
                    text("SELECT value_json, updated_at FROM bot_state WHERE key='heartbeat'")
                ).first()
            if row:
                hb_raw = _as_dict(row[0])
                updated = _as_dt(row[1])
                if updated is not None:
                    hb_age = (datetime.now(timezone.utc) - updated).total_seconds()
                elif hb_raw.get("ts"):
                    ts = _as_dt(hb_raw["ts"])
                    if ts:
                        hb_age = (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception as exc:
            http_payload["hb_error"] = str(exc)

        self.set_state(
            "heartbeat",
            {
                "http_ok": http_ok,
                "http": http_payload,
                "bot_heartbeat": hb_raw,
                "bot_heartbeat_age_s": hb_age,
                "checked_at": utcnow().isoformat(),
            },
        )

        unresponsive = (not http_ok) or (
            hb_age is not None and hb_age > self.heartbeat_timeout_s
        )
        # Si pas encore de heartbeat bot (démarrage), se fier à HTTP seulement
        if hb_age is None:
            unresponsive = not http_ok

        if unresponsive:
            self.emit(
                "alert",
                "bot_unresponsive",
                f"Bot non responsive (http_ok={http_ok}, hb_age_s={hb_age})",
                {"http": http_payload, "hb_age_s": hb_age},
            )
        else:
            self.resolve("bot_unresponsive")

    def check_reconciliation(self) -> None:
        try:
            with self.bot_engine.connect() as conn:
                state_row = conn.execute(
                    text("SELECT value_json FROM bot_state WHERE key='main'")
                ).first()
                bags = conn.execute(
                    text(
                        "SELECT COALESCE(SUM(quantity),0) FROM bags "
                        "WHERE status IN ('open','trailing_active','journal_only')"
                    )
                ).scalar()
            state = _as_dict(state_row[0] if state_row else {})
            cfg = StrategyConfig.from_dict(state.get("config") or {})
            grid_qty = float((state.get("grid") or {}).get("position_qty") or 0)
            bags_qty = float(bags or 0)
            expected = grid_qty + bags_qty
            binance_qty = self.client.base_asset_qty(cfg.symbol)
            mark = float(self.client.ticker_price(cfg.symbol)["price"])
            delta = binance_qty - expected
            delta_usdt = abs(delta) * mark
            payload = {
                "symbol": cfg.symbol,
                "binance_qty": binance_qty,
                "grid_qty": grid_qty,
                "bags_qty": bags_qty,
                "expected": expected,
                "delta": delta,
                "delta_usdt": delta_usdt,
                "threshold_usdt": self.recon_threshold_usdt,
            }
            self.set_state("reconciliation", {**payload, "at": utcnow().isoformat()})
            self.metric("reconciliation_delta_usdt", delta_usdt, payload)
            if delta_usdt > self.recon_threshold_usdt:
                self.emit(
                    "alert",
                    "reconciliation_mismatch_watchdog",
                    f"Écart réconciliation indépendante {delta_usdt:.4f} USDT > {self.recon_threshold_usdt}",
                    payload,
                )
            else:
                self.resolve("reconciliation_mismatch_watchdog")
        except Exception as exc:
            self.emit(
                "alert",
                "reconciliation_mismatch_watchdog",
                f"Réconciliation superviseur impossible: {exc}",
                {"error": str(exc)},
            )

    def check_exchange_connectivity(self) -> None:
        try:
            t0 = time.time()
            status, body, raw = self.client._raw_request("GET", "/api/v3/ping")
            latency_ms = (time.time() - t0) * 1000
            self.metric("exchange_latency_ms", latency_ms, {"status": status})
            self.set_state(
                "exchange",
                {"latency_ms": latency_ms, "status": status, "at": utcnow().isoformat()},
            )
            if status != 200 or latency_ms > 5000:
                self.emit(
                    "alert",
                    "exchange_connectivity_degraded",
                    f"Connectivité Exchange dégradée status={status} latency_ms={latency_ms:.0f}",
                    {"status": status, "latency_ms": latency_ms},
                )
            else:
                self.resolve("exchange_connectivity_degraded")
        except Exception as exc:
            self.emit(
                "alert",
                "exchange_connectivity_degraded",
                f"Ping Exchange échoué: {exc}",
                {"error": str(exc)},
            )

    def check_market_anomaly(self) -> None:
        try:
            with self.bot_engine.connect() as conn:
                state_row = conn.execute(
                    text("SELECT value_json FROM bot_state WHERE key='main'")
                ).first()
            state = _as_dict(state_row[0] if state_row else {})
            symbol = (state.get("config") or {}).get("symbol") or "BTCUSDT"
            rest_price = float(self.client.ticker_price(symbol)["price"])
            bot_mark = (state.get("grid") or {}).get("center_price")
            # Comparaison REST ticker vs depth mid
            depth = self.client.depth(symbol, limit=5)
            bid = float(depth["bids"][0][0])
            ask = float(depth["asks"][0][0])
            mid = (bid + ask) / 2
            pct = abs(rest_price - mid) / rest_price * 100.0
            payload = {
                "symbol": symbol,
                "rest_price": rest_price,
                "depth_mid": mid,
                "pct_diff": pct,
                "threshold_pct": self.market_anomaly_pct,
                "bot_mark": bot_mark,
            }
            self.metric("market_price_diff_pct", pct, payload)
            self.set_state("market", {**payload, "at": utcnow().isoformat()})
            if pct > self.market_anomaly_pct:
                self.emit(
                    "alert",
                    "market_data_anomaly",
                    f"Écart prix REST vs depth mid {pct:.3f}% > {self.market_anomaly_pct}%",
                    payload,
                )
            else:
                self.resolve("market_data_anomaly")
        except Exception as exc:
            logger.warning("market anomaly check failed: %s", exc)

    def check_guardrails(self) -> None:
        """Double calcul stop dur / circuit breaker indépendant du bot."""
        try:
            with self.bot_engine.connect() as conn:
                state_row = conn.execute(
                    text("SELECT value_json FROM bot_state WHERE key='main'")
                ).first()
                # portable sqlite/postgres
                daily = conn.execute(
                    text(
                        "SELECT COALESCE(SUM(net_pnl),0) FROM cycles "
                        "WHERE status='closed'"
                    )
                ).scalar()
            state = _as_dict(state_row[0] if state_row else {})
            cfg = StrategyConfig.from_dict(state.get("config") or {})
            symbol = cfg.symbol
            filters = self.client.get_symbol_filters(symbol)
            base_qty = self.client.base_asset_qty(symbol)
            mark = float(self.client.ticker_price(symbol)["price"])
            entry = float((state.get("grid") or {}).get("entry_avg") or 0)
            should_stop = False
            pct = None
            if base_qty > 0 and entry > 0:
                pct = ((mark - entry) / entry) * 100.0
                should_stop = pct <= cfg.hard_stop_pct

            daily_pnl = float(daily or 0)
            guards = state.get("guards") or {}
            bot_daily = float(guards.get("daily_pnl") or daily_pnl)
            should_breaker = bot_daily <= cfg.daily_circuit_breaker_usd

            bot_panic = bool(guards.get("panic"))
            bot_hard = bool(guards.get("hard_stop") or guards.get("hard_stop_triggered"))
            bot_breaker = bool(
                guards.get("circuit_breaker") or guards.get("circuit_breaker_triggered")
            )

            payload = {
                "base_qty": base_qty,
                "mark": mark,
                "entry": entry,
                "pnl_pct": pct,
                "should_stop": should_stop,
                "should_breaker": should_breaker,
                "bot_daily": bot_daily,
                "bot_hard": bot_hard,
                "bot_breaker": bot_breaker,
                "bot_panic": bot_panic,
            }
            self.set_state("guardrails", {**payload, "at": utcnow().isoformat()})

            if (should_stop and not bot_hard and not bot_panic) or (
                should_breaker and not bot_breaker and not bot_panic
            ):
                self.emit(
                    "alert",
                    "guardrail_not_triggered_by_bot",
                    "Garde-fou aurait dû se déclencher selon calcul superviseur",
                    payload,
                )
            else:
                self.resolve("guardrail_not_triggered_by_bot")
        except Exception as exc:
            logger.warning("guardrail check failed: %s", exc)

    def check_orphan_position(self) -> None:
        """Position résiduelle non surveillée quand le bot est arrêté (running=false ou grille inactive)."""
        try:
            with self.bot_engine.connect() as conn:
                state_row = conn.execute(
                    text("SELECT value_json FROM bot_state WHERE key='main'")
                ).first()
                bags = conn.execute(
                    text(
                        "SELECT COALESCE(SUM(quantity),0) FROM bags "
                        "WHERE status IN ('open','trailing_active','journal_only')"
                    )
                ).scalar()
                lc_row = conn.execute(
                    text("SELECT value_json FROM bot_state WHERE key='last_command'")
                ).first()
            state = _as_dict(state_row[0] if state_row else {})
            running = bool(state.get("running"))
            grid_active = bool((state.get("grid") or {}).get("active"))

            if running and grid_active:
                self.resolve("orphan_position_unwatched")
                return

            cfg = StrategyConfig.from_dict(state.get("config") or {})
            bags_qty = float(bags or 0)
            self.client.account(force=True)
            binance_qty = self.client.base_asset_qty(cfg.symbol)
            mark = float(self.client.ticker_price(cfg.symbol, force=True)["price"])
            oq = orphan_qty(binance_qty, bags_qty)
            notional = oq * mark
            threshold = float(os.getenv("ORPHAN_MIN_NOTIONAL_USDT", str(ORPHAN_MIN_NOTIONAL_USDT)))
            min_stopped_s = float(os.getenv("ORPHAN_STOPPED_MIN_S", str(ORPHAN_STOPPED_MIN_S)))
            entry = float((state.get("grid") or {}).get("entry_avg") or 0)

            stopped_at = state.get("stopped_at")
            lc = _as_dict(lc_row[0] if lc_row else {})
            if entry <= 0 and lc.get("name") == "stop":
                rw = (lc.get("result") or {}).get("residual_position_warning") or {}
                entry = float(rw.get("entry_avg") or 0)
            if not stopped_at:
                if lc.get("name") == "stop":
                    stopped_at = (lc.get("result") or {}).get("stopped_at") or lc.get("ts")

            stopped_duration_s = None
            dt = _as_dt(stopped_at)
            if dt:
                stopped_duration_s = (datetime.now(timezone.utc) - dt).total_seconds()

            payload = {
                "orphan_qty": oq,
                "notional_usdt": round(notional, 4),
                "mark_price": mark,
                "entry_avg": entry if entry > 0 else None,
                "floating_pnl": (
                    round(floating_pnl_vs_entry(oq, entry, mark), 6) if entry > 0 else None
                ),
                "stopped_at": stopped_at,
                "stopped_duration_s": stopped_duration_s,
                "running": running,
                "grid_active": grid_active,
                "threshold_usdt": threshold,
                "min_stopped_s": min_stopped_s,
                "bags_qty": bags_qty,
                "binance_qty": binance_qty,
            }
            self.set_state("orphan_position", {**payload, "at": utcnow().isoformat()})

            if notional < threshold:
                self.resolve("orphan_position_unwatched")
                return

            if stopped_duration_s is not None and stopped_duration_s < min_stopped_s:
                return

            msg = (
                f"Position orpheline non surveillée : {oq:.8f} (~{notional:.2f} USDT)"
                + (
                    f" — arrêt depuis {int(stopped_duration_s)}s"
                    if stopped_duration_s is not None
                    else ""
                )
            )
            self.emit("alert", "orphan_position_unwatched", msg, payload)
        except Exception as exc:
            logger.warning("orphan position check failed: %s", exc)

    def run_once(self) -> None:
        self.check_heartbeat()
        self.check_exchange_connectivity()
        self.check_reconciliation()
        self.check_market_anomaly()
        self.check_guardrails()
        self.check_orphan_position()

    def run_forever(self) -> None:
        logger.info(
            "Supervisor started poll=%ss heartbeat_timeout=%ss emergency=%s",
            self.poll_s,
            self.heartbeat_timeout_s,
            self.emergency_action,
        )
        while True:
            try:
                self.run_once()
            except Exception:
                logger.exception("supervisor loop error")
            time.sleep(self.poll_s)




def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db_url = os.getenv("DATABASE_URL", "sqlite:////data/ultiumgrid.db")
    Watchdog(db_url).run_forever()


if __name__ == "__main__":
    main()
