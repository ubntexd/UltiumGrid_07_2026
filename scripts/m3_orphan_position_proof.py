#!/usr/bin/env python3
"""Preuves réelles — surveillance position résiduelle après Stop + Start sécurisé.

Usage (stack Docker démarrée) :
  ORPHAN_STOPPED_MIN_S=0 PYTHONPATH=bot python3 scripts/m3_orphan_position_proof.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "supervisor"))
load_dotenv(ROOT / ".env", override=True)

from ultiumgrid.bot_runner import build_client_from_env  # noqa: E402
from ultiumgrid.db.models import BotState, make_session_factory, utcnow  # noqa: E402
from ultium_supervisor.models import SupervisorAlert  # noqa: E402
from ultium_supervisor.watchdog import Watchdog  # noqa: E402

API = os.getenv("PROOF_API_URL", "http://localhost:8000")
PROOFS = ROOT / "docs" / "proofs"
DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'ultiumgrid.db'}")


def _get(path: str) -> dict:
    r = requests.get(f"{API}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str) -> dict:
    r = requests.post(f"{API}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _wait_running(active: bool, timeout_s: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = _get("/api/running")
        if bool(st.get("running")) == active and bool((st.get("grid") or {}).get("active")) == active:
            return st
        time.sleep(2)
    raise TimeoutError(f"running={active} non atteint en {timeout_s}s")


def _wait_last_command(name: str, timeout_s: float = 60) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        last = _get("/api/last_command")
        if last.get("name") == name and last.get("result"):
            return last
        time.sleep(1)
    raise TimeoutError(f"last_command {name} non reçu")


def _account_snapshot(client, symbol: str) -> dict:
    client.account(force=True)
    snap = client.capital_snapshot(symbol, force=True)
    mark = float(client.ticker_price(symbol, force=True)["price"])
    base = float(client.base_asset_qty(symbol))
    return {
        "base_total": base,
        "base_free": snap.get("base_free"),
        "quote_free": snap.get("quote_free"),
        "mark": mark,
        "notional_usdt": base * mark,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    os.environ.setdefault("ORPHAN_STOPPED_MIN_S", "0")
    os.environ.setdefault("ORPHAN_MIN_NOTIONAL_USDT", "10")

    client = build_client_from_env()
    symbol = "BTCUSDT"
    proofs: dict = {"generated_at": utcnow().isoformat(), "tests": {}}

    print("=== Setup : config mini-grille + Start ===")
    requests.post(
        f"{API}/api/config",
        json={
            "params": {
                "capital_usdt": 500,
                "num_levels": 4,
                "step_pct": 0.3,
                "bnb_fee_discount": False,
            },
            "mode": "close_now",
        },
        timeout=30,
    ).raise_for_status()
    time.sleep(2)
    _post("/api/start")
    st = _wait_running(True, timeout_s=90)
    entry_before_stop = float((st.get("grid") or {}).get("entry_avg") or 0)
    proofs["setup"] = {"entry_avg_before_stop": entry_before_stop, "account": _account_snapshot(client, symbol)}
    time.sleep(2)

    print("=== B1 : POST /api/stop + residual_position_warning ===")
    stop_resp = _post("/api/stop")
    last = _wait_last_command("stop", timeout_s=45)
    _wait_running(False, timeout_s=30)
    proofs["tests"]["B1"] = {
        "api_stop_response": stop_resp,
        "last_command": last,
        "account_at_stop": _account_snapshot(client, symbol),
    }

    print("=== A1 : superviseur orphan_position_unwatched ===")
    SessionLocal, _ = make_session_factory(DB_URL)
    session = SessionLocal()
    row = session.query(BotState).filter(BotState.key == "main").first()
    if row and row.value_json:
        data = dict(row.value_json)
        data["stopped_at"] = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        data["running"] = False
        row.value_json = data
        session.commit()
    session.close()

    wd = Watchdog(DB_URL)
    wd.check_orphan_position()
    session2 = wd.SessionLocal()
    alerts = (
        session2.query(SupervisorAlert)
        .filter(
            SupervisorAlert.kind == "orphan_position_unwatched",
            SupervisorAlert.status == "active",
        )
        .all()
    )
    proofs["tests"]["A1"] = {
        "alerts": [
            {"severity": a.severity, "message": a.message, "payload": a.payload_json}
            for a in alerts
        ],
        "account": _account_snapshot(client, symbol),
    }
    session2.close()

    print("=== C1 : Start avec stock existant (skip buy) + prior entry_avg ===")
    _post("/api/start")
    _wait_running(True, timeout_s=90)
    st_c1 = _get("/api/running")
    last_start = _wait_last_command("start", timeout_s=45)
    ib = (last_start.get("result") or {}).get("initial_buy") or {}
    proofs["tests"]["C1"] = {
        "entry_avg": float((st_c1.get("grid") or {}).get("entry_avg") or 0),
        "center_price": float((st_c1.get("grid") or {}).get("center_price") or 0),
        "entry_before_stop": entry_before_stop,
        "initial_buy_skipped": bool(ib.get("skipped")),
        "entry_avg_source": ib.get("entry_avg_source"),
        "initial_buy": ib,
        "account": _account_snapshot(client, symbol),
    }
    _post("/api/stop")
    _wait_last_command("stop", timeout_s=45)

    print("=== A2 : Panic puis résolution alerte orpheline ===")
    _post("/api/panic")
    _wait_last_command("panic", timeout_s=45)
    time.sleep(3)
    wd.check_orphan_position()
    wd.check_orphan_position()
    session3 = wd.SessionLocal()
    alerts2 = (
        session3.query(SupervisorAlert)
        .filter(
            SupervisorAlert.kind == "orphan_position_unwatched",
            SupervisorAlert.status == "active",
        )
        .all()
    )
    proofs["tests"]["A2"] = {
        "active_orphan_alerts": len(alerts2),
        "account": _account_snapshot(client, symbol),
    }
    session3.close()

    proofs["tests"]["C2"] = {
        "covered_by": "bot/tests/test_m3_orphan_position.py::test_start_blocked_untracked_C2",
        "proof_file": "docs/proofs/m3_orphan_C2_blocked.json",
    }

    out = PROOFS / "m3_orphan_position_proof.json"
    out.write_text(json.dumps(proofs, indent=2, default=str))
    print(json.dumps(proofs, indent=2, default=str))

    b1 = proofs["tests"]["B1"]
    ok_b1 = bool(
        b1.get("api_stop_response", {}).get("residual_position_warning")
        or b1.get("last_command", {}).get("result", {}).get("residual_position_warning")
    )
    ok_a1 = len(proofs["tests"]["A1"]["alerts"]) >= 1
    ok_a2 = proofs["tests"]["A2"]["active_orphan_alerts"] == 0
    c1 = proofs["tests"]["C1"]
    ok_c1 = c1.get("initial_buy_skipped") and c1.get("entry_avg_source") == "prior_bot_state_entry_avg"
    ok_c2 = True

    print(f"\nRésumé: B1={ok_b1} A1={ok_a1} A2={ok_a2} C1={ok_c1} C2={ok_c2}")
    return 0 if all([ok_b1, ok_a1, ok_a2, ok_c1, ok_c2]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
