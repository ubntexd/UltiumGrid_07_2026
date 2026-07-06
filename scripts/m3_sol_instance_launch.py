#!/usr/bin/env python3
"""Lancement run SOL — après .env.sol + precheck OK."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_sol_instance_v1"
API = "http://127.0.0.1:18100"
COMPOSE = ["docker", "compose", "-p", "ultiumgrid_sol", "-f", "docker-compose.sol.yml"]

TARGET_PARAMS = {
    "symbol": "SOLUSDT",
    "capital_usdt": 4000,
    "num_levels": 20,
    "step_pct": 0.4,
    "cycle_trigger_usd": 15.0,
    "bnb_fee_discount": True,
    "idle_recenter_min": 20.0,
    "stuck_sell_min": 15.0,
    "cut_level_1": 10,
    "cut_pct_1": 50.0,
    "cut_level_2": 14,
    "cut_pct_2": 100.0,
    "rearm_levels": 2,
    "rearm_delay_min": 20,
    "hard_stop_pct": -8.0,
    "daily_circuit_breaker_usd": -40.0,
    "bags_capital_threshold_pct": 40.0,
}


def sql(q: str) -> str:
    return subprocess.check_output(
        COMPOSE + ["exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid_sol", "-t", "-A", "-c", q],
        cwd=ROOT,
        text=True,
    ).strip()


def main() -> int:
    env_sol = ROOT / ".env.sol"
    if not env_sol.exists():
        print("ERREUR: .env.sol manquant — copier depuis .env.sol.example et ajouter les clés Demo SOL.")
        return 1

    precheck = ROOT / "docs" / "proofs" / "m_sol_instance_precheck.json"
    if not precheck.exists():
        print("Lancer d'abord: python3 scripts/m_sol_instance_precheck.py")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    proof: dict = {
        "ts_utc": started.isoformat(),
        "target_params": TARGET_PARAMS,
        "comparison_window": {
            "btc_run_v2_start": "2026-07-05T19:23:00+00:00",
            "btc_run_v2_target_end": "2026-07-06T19:23:00+00:00",
            "sol_start": started.isoformat(),
            "note": "Comparer durées effectives — signaler si inégales",
        },
        "checks": {},
    }

    inst = requests.get(f"{API}/api/instance", timeout=15).json()
    proof["checks"]["instance"] = inst
    if inst.get("instance_id") != "sol":
        print("ERREUR: API ne répond pas comme instance SOL — stack démarrée ?")
        return 1

    running = requests.get(f"{API}/api/running", timeout=30).json()
    cap = running.get("capital") or {}
    proof["checks"]["pre_start"] = {
        "running": running.get("running"),
        "quote_free": cap.get("quote_free"),
        "open_cycles": sql("SELECT COUNT(*) FROM cycles WHERE status='open'"),
    }

    if proof["checks"]["pre_start"]["open_cycles"] != "0":
        print("ERREUR: cycle open existant sur instance SOL")
        return 1

    cfg_params = dict(TARGET_PARAMS)
    r1 = requests.post(f"{API}/api/config", json={"params": cfg_params, "mode": "close_now"}, timeout=60)
    if r1.status_code >= 400:
        cfg_params["bnb_fee_discount"] = False
        r1 = requests.post(f"{API}/api/config", json={"params": cfg_params, "mode": "close_now"}, timeout=60)
        proof["checks"]["config_fallback"] = {"bnb_fee_discount": False, "detail": r1.json() if r1.status_code >= 400 else "ok"}
    r1.raise_for_status()
    proof["checks"]["config_close_now"] = r1.json()
    time.sleep(2)
    r2 = requests.post(f"{API}/api/config", json={"params": cfg_params, "mode": "wait_cycle"}, timeout=30)
    r2.raise_for_status()
    proof["checks"]["config_wait_cycle"] = r2.json()
    time.sleep(1)
    start_res = requests.post(f"{API}/api/start", timeout=120).json()
    proof["checks"]["start"] = start_res
    time.sleep(5)
    after = requests.get(f"{API}/api/running", timeout=30).json()
    proof["checks"]["post_start"] = {
        "running": after.get("running"),
        "cycle_id": after.get("cycle_id"),
        "symbol": after.get("symbol"),
        "step_pct": (after.get("config") or {}).get("step_pct"),
    }

    (OUT / "pre_launch_proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"ok": after.get("running"), "proof": str(OUT / "pre_launch_proof.json")}, indent=2))
    return 0 if after.get("running") else 1


if __name__ == "__main__":
    raise SystemExit(main())
