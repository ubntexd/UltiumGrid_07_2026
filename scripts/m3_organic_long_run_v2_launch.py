#!/usr/bin/env python3
"""Pre-lancement run organique v2 — vérifs compte, config cible, Start."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_organic_long_run_v2"
API = "http://127.0.0.1:8000"

TARGET_PARAMS = {
    "symbol": "BTCUSDT",
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
        [
            "docker", "compose", "exec", "-T", "db",
            "psql", "-U", "ultium", "-d", "ultiumgrid", "-t", "-A", "-c", q,
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    proof: dict = {"ts_utc": datetime.now(timezone.utc).isoformat(), "checks": {}}

    # 1. Compte + running
    running = requests.get(f"{API}/api/running", timeout=30).json()
    cap = running.get("capital") or {}
    proof["checks"]["account"] = {
        "quote_free": cap.get("quote_free"),
        "base_free": cap.get("base_free"),
        "base_total": cap.get("base_total"),
        "running": running.get("running"),
        "open_cycles_sql": sql("SELECT COUNT(*) FROM cycles WHERE status='open'"),
    }
    ok_account = (
        float(cap.get("base_total") or 0) < 1e-8
        and float(cap.get("quote_free") or 0) > 4000
        and proof["checks"]["account"]["open_cycles_sql"] == "0"
    )

    # BNB direct
    sys.path.insert(0, str(ROOT / "bot"))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from ultiumgrid.bot_runner import build_client_from_env
    client = build_client_from_env()
    bnb = float(client.balance_free("BNB", force=True))
    proof["checks"]["bnb_free"] = bnb
    ok_bnb = bnb > 0

    # 2. Garde BNB — viabilité avec discount true
    viab = requests.post(
        f"{API}/api/config/viability",
        json={"params": TARGET_PARAMS},
        timeout=30,
    )
    proof["checks"]["viability_status"] = viab.status_code
    proof["checks"]["viability"] = viab.json() if viab.ok else viab.text

    # 3. Appliquer config
    cfg_resp = requests.post(
        f"{API}/api/config",
        json={"params": TARGET_PARAMS, "mode": "close_now"},
        timeout=30,
    )
    proof["checks"]["config_apply"] = cfg_resp.json() if cfg_resp.ok else {"error": cfg_resp.text}
    cfg_resp.raise_for_status()
    time.sleep(3)

    # 4. Confirmer GET /api/config
    got = requests.get(f"{API}/api/config", timeout=30).json()
    active = got.get("active") or {}
    proof["checks"]["config_active"] = {
        "step_pct": active.get("step_pct"),
        "bnb_fee_discount": active.get("bnb_fee_discount"),
        "capital_usdt": active.get("capital_usdt"),
        "num_levels": active.get("num_levels"),
        "cycle_trigger_usd": active.get("cycle_trigger_usd"),
    }
    ok_cfg = (
        float(active.get("step_pct") or 0) == 0.4
        and active.get("bnb_fee_discount") is True
        and int(active.get("num_levels") or 0) == 20
        and float(active.get("capital_usdt") or 0) == 5000
    )

    # 5. Start
    if not running.get("running"):
        start = requests.post(f"{API}/api/start", timeout=30)
        proof["checks"]["start"] = start.json() if start.ok else start.text
        start.raise_for_status()
        for _ in range(30):
            time.sleep(2)
            r = requests.get(f"{API}/api/running", timeout=30).json()
            if r.get("running") and (r.get("grid") or {}).get("active"):
                proof["checks"]["running_after_start"] = {
                    "running": True,
                    "cycle_id": r.get("cycle_id"),
                    "step_pct": (r.get("config") or {}).get("step_pct"),
                    "bnb_fee_discount": (r.get("config") or {}).get("bnb_fee_discount"),
                }
                break
    else:
        proof["checks"]["start"] = {"skipped": "already running"}

    proof["ok"] = ok_account and ok_bnb and ok_cfg
    proof["simulation_ref"] = "docs/proofs/m7bis_target_config_simulation.json"
    proof["protocol"] = "docs/m3_organic_long_run_v2_protocol.md"

    out = OUT / "pre_launch_proof.json"
    out.write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
