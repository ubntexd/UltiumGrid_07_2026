#!/usr/bin/env python3
"""Lancement run HYPERUSDT — après .env.hyper + precheck OK."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_hyper_instance_v1"
API = "http://127.0.0.1:18200"
UI = "http://127.0.0.1:18280"
COMPOSE = ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml"]

TARGET_PARAMS = {
    "symbol": "HYPERUSDT",
    "capital_usdt": 5000,
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
        COMPOSE + ["exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid_hyper", "-t", "-A", "-c", q],
        cwd=ROOT,
        text=True,
    ).strip()


def main() -> int:
    env_hyper = ROOT / ".env.hyper"
    if not env_hyper.exists():
        print("ERREUR: .env.hyper manquant — copier depuis .env.hyper.example et ajouter les clés Demo HYPER.")
        return 1

    precheck = OUT / "precheck.json"
    if not precheck.exists():
        print("Lancer d'abord: python3 scripts/m_hyper_instance_precheck.py")
        return 1

    pre = json.loads(precheck.read_text(encoding="utf-8"))
    if not pre.get("ok_to_start_bot"):
        print("ERREUR: precheck ok_to_start_bot=false — vérifier BNB, USDT, exchangeInfo")
        print(json.dumps(pre.get("keys"), indent=2))
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    proof: dict = {
        "ts_utc": started.isoformat(),
        "target_params": TARGET_PARAMS,
        "symbol_clarification": "HYPERUSDT (HYPER) — pas HYPE/Hyperliquid",
        "expected_net_at_gross_threshold": pre.get("viability", {}).get("net_at_gross_threshold"),
        "comparison_window": {
            "btc_run_v2_start": "2026-07-05T19:23:00+00:00",
            "btc_run_v2_target_end": "2026-07-06T19:23:00+00:00",
            "sol_start": "2026-07-05T22:02:00+00:00",
            "hyper_start": started.isoformat(),
            "note": "Fenêtres inégales — signaler dans le rapport comparatif",
        },
        "checks": {},
    }

    inst = requests.get(f"{API}/api/instance", timeout=15).json()
    proof["checks"]["instance"] = inst
    if inst.get("instance_id") != "hyper":
        print("ERREUR: API ne répond pas comme instance HYPER — stack démarrée ?")
        return 1
    if inst.get("trading_symbol") != "HYPERUSDT":
        print("ERREUR: trading_symbol attendu HYPERUSDT")
        return 1

    ui = requests.get(UI, timeout=15)
    proof["checks"]["ui_first_load"] = {
        "status": ui.status_code,
        "has_instance_symbol_badge": "instance-symbol-badge" in ui.text,
    }

    running = requests.get(f"{API}/api/running", timeout=30).json()
    cap = running.get("capital") or {}
    proof["checks"]["pre_start"] = {
        "running": running.get("running"),
        "symbol": running.get("symbol"),
        "quote_free": cap.get("quote_free"),
        "open_cycles": sql("SELECT COUNT(*) FROM cycles WHERE status='open'"),
    }

    if proof["checks"]["pre_start"]["open_cycles"] != "0":
        print("ERREUR: cycle open existant sur instance HYPER")
        return 1

    cfg_params = dict(TARGET_PARAMS)
    r1 = requests.post(f"{API}/api/config", json={"params": cfg_params, "mode": "close_now"}, timeout=60)
    if r1.status_code >= 400:
        detail = r1.json() if r1.headers.get("content-type", "").startswith("application/json") else r1.text
        print(f"ERREUR: POST /api/config rejeté ({r1.status_code}): {detail}")
        proof["checks"]["config_error"] = detail
        (OUT / "pre_launch_proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        return 1
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
        "cycle_trigger_usd": (after.get("config") or {}).get("cycle_trigger_usd"),
    }

    symbol_ok = after.get("symbol") == "HYPERUSDT"
    proof["checks"]["ui_data_not_wrong_instance"] = {
        "expected_symbol": "HYPERUSDT",
        "actual_symbol": after.get("symbol"),
        "ok": symbol_ok,
        "note": "Test anti-bug SOL : premier /api/running doit être HYPERUSDT, pas BTCUSDT/SOLUSDT",
    }
    if not symbol_ok:
        print(f"ERREUR: symbole post-start = {after.get('symbol')} (attendu HYPERUSDT)")
        (OUT / "pre_launch_proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        return 1

    (OUT / "pre_launch_proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"ok": after.get("running"), "symbol": after.get("symbol"), "proof": str(OUT / "pre_launch_proof.json")}, indent=2))
    return 0 if after.get("running") else 1


if __name__ == "__main__":
    raise SystemExit(main())
