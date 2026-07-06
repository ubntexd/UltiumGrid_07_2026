#!/usr/bin/env python3
"""Lancement run XRPUSDT — reconfiguration instance 3 (ex-HYPER, meme stack)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_xrp_instance_v1"
API = "http://127.0.0.1:18200"
UI = "http://127.0.0.1:18280"
REST = "https://demo-api.binance.com"
COMPOSE = ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml"]

TARGET_PARAMS = {
    "symbol": "XRPUSDT",
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
    precheck = ROOT / "docs" / "proofs" / "m_xrp_candidate_check.json"
    if not precheck.exists():
        print("Lancer d'abord: python3 scripts/m_xrp_instance_precheck.py")
        return 1

    pre = json.loads(precheck.read_text(encoding="utf-8"))
    if not pre.get("ok_to_start_bot"):
        print("ERREUR: ok_to_start_bot=false")
        print(json.dumps(pre.get("keys"), indent=2))
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    proof: dict = {
        "ts_utc": started.isoformat(),
        "target_params": TARGET_PARAMS,
        "transition": "HYPERUSDT → XRPUSDT (meme compose ultiumgrid_hyper, ports 18200/18280)",
        "expected_net_at_gross_threshold": pre.get("viability", {}).get("net_at_gross_threshold"),
        "checks": {},
    }

    inst = requests.get(f"{API}/api/instance", timeout=15).json()
    proof["checks"]["instance"] = inst
    if inst.get("instance_id") != "xrp":
        print(f"ERREUR: instance_id attendu xrp, obtenu {inst.get('instance_id')}")
        return 1
    if inst.get("trading_symbol") != "XRPUSDT":
        print("ERREUR: trading_symbol attendu XRPUSDT")
        return 1

    ui = requests.get(UI, timeout=15)
    proof["checks"]["ui"] = {
        "status": ui.status_code,
        "has_instance_xrp_label": "Instance XRP" in ui.text,
        "has_symbol_badge": "instance-symbol-badge" in ui.text,
    }

    running = requests.get(f"{API}/api/running", timeout=30).json()
    cap = running.get("capital") or {}
    proof["checks"]["pre_start"] = {
        "running": running.get("running"),
        "symbol": running.get("symbol"),
        "quote_free": cap.get("quote_free"),
        "base_total": cap.get("base_total"),
        "base_asset": cap.get("base_asset"),
        "open_cycles": sql("SELECT COUNT(*) FROM cycles WHERE status='open'"),
    }

    cfg_params = dict(TARGET_PARAMS)
    r1 = requests.post(f"{API}/api/config", json={"params": cfg_params, "mode": "close_now"}, timeout=120)
    if r1.status_code >= 400:
        detail = r1.json() if r1.headers.get("content-type", "").startswith("application/json") else r1.text
        print(f"ERREUR: POST /api/config close_now ({r1.status_code}): {detail}")
        proof["checks"]["config_error"] = detail
        (OUT / "launch_proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        return 1
    proof["checks"]["config_close_now"] = r1.json()
    time.sleep(3)

    r2 = requests.post(f"{API}/api/config", json={"params": cfg_params, "mode": "wait_cycle"}, timeout=30)
    r2.raise_for_status()
    proof["checks"]["config_wait_cycle"] = r2.json()
    time.sleep(1)

    start_res = requests.post(f"{API}/api/start", timeout=120).json()
    proof["checks"]["start"] = start_res
    time.sleep(6)

    after = requests.get(f"{API}/api/running", timeout=30).json()
    xrp_rest = float(requests.get(f"{REST}/api/v3/ticker/price", params={"symbol": "XRPUSDT"}, timeout=10).json()["price"])
    mark = after.get("mark_price")
    ratio = abs(mark - xrp_rest) / xrp_rest if mark and xrp_rest else None

    logs = subprocess.check_output(COMPOSE + ["logs", "--tail", "80", "bot"], cwd=ROOT, text=True)
    ws_xrp = "xrpusdt@bookticker" in logs.lower()
    ws_hyper_stale = "hyperusdt@bookticker" in logs.lower() and "xrpusdt@bookticker" not in logs.lower()

    proof["checks"]["post_start"] = {
        "running": after.get("running"),
        "cycle_id": after.get("cycle_id"),
        "symbol": after.get("symbol"),
        "mark_price": mark,
        "mark_source": after.get("mark_source"),
        "xrp_rest": xrp_rest,
        "mark_vs_rest_ratio_error": ratio,
        "mark_ok": ratio is not None and ratio < 0.02,
        "ws_xrpusdt_in_logs": ws_xrp,
        "ws_not_stuck_hyper_only": not ws_hyper_stale,
        "position_qty": (after.get("grid") or {}).get("position_qty"),
        "base_asset": (after.get("capital") or {}).get("base_asset"),
    }

    symbol_ok = after.get("symbol") == "XRPUSDT"
    proof["checks"]["non_regression"] = {
        "symbol_ok": symbol_ok,
        "mark_ok": proof["checks"]["post_start"]["mark_ok"],
        "ws_ok": ws_xrp and not ws_hyper_stale,
        "no_hyper_position": (after.get("capital") or {}).get("base_asset") == "XRP",
        "ok": symbol_ok and proof["checks"]["post_start"]["mark_ok"] and ws_xrp,
    }

    (OUT / "launch_proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")

    if not proof["checks"]["non_regression"]["ok"]:
        print("ERREUR: non-régression échouée")
        print(json.dumps(proof["checks"]["non_regression"], indent=2))
        return 1

    print(
        json.dumps(
            {
                "ok": after.get("running"),
                "symbol": after.get("symbol"),
                "mark": mark,
                "xrp_rest": xrp_rest,
                "proof": str(OUT / "launch_proof.json"),
            },
            indent=2,
        )
    )
    return 0 if after.get("running") else 1


if __name__ == "__main__":
    raise SystemExit(main())
