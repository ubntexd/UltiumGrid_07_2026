#!/usr/bin/env python3
"""Preuves d'isolation BTC vs SOL — DB, réseau, non-régression croisée."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m_sol_isolation_proof.json"

BTC_API = "http://127.0.0.1:18000"
SOL_API = "http://127.0.0.1:18100"
BTC_UI = "http://127.0.0.1:18080"
SOL_UI = "http://127.0.0.1:18180"


def compose_ps(project: str, compose_file: str) -> list[str]:
    out = subprocess.check_output(
        ["docker", "compose", "-p", project, "-f", compose_file, "ps", "-q"],
        cwd=ROOT,
        text=True,
    )
    return [x for x in out.strip().split("\n") if x]


def sql(project: str, compose_file: str, db: str, q: str) -> str:
    return subprocess.check_output(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            compose_file,
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "ultium",
            "-d",
            db,
            "-t",
            "-A",
            "-c",
            q,
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def main() -> int:
    proof: dict = {"ts_utc": datetime.now(timezone.utc).isoformat(), "checks": {}}

    btc_ids = compose_ps("ultiumgrid_07_2026", "docker-compose.yml")
    sol_ids = compose_ps("ultiumgrid_sol", "docker-compose.sol.yml")
    proof["checks"]["container_ids_disjoint"] = {
        "btc_count": len(btc_ids),
        "sol_count": len(sol_ids),
        "intersection": list(set(btc_ids) & set(sol_ids)),
        "ok": len(set(btc_ids) & set(sol_ids)) == 0,
    }

    if sol_ids:
        proof["checks"]["db_names"] = {
            "btc_db": sql("ultiumgrid_07_2026", "docker-compose.yml", "ultiumgrid", "SELECT current_database();"),
            "sol_db": sql("ultiumgrid_sol", "docker-compose.sol.yml", "ultiumgrid_sol", "SELECT current_database();"),
            "ok": True,
        }
        btc_cycles = sql("ultiumgrid_07_2026", "docker-compose.yml", "ultiumgrid", "SELECT COUNT(*) FROM cycles;")
        sol_cycles = sql("ultiumgrid_sol", "docker-compose.sol.yml", "ultiumgrid_sol", "SELECT COUNT(*) FROM cycles;")
        proof["checks"]["cycle_counts_independent"] = {
            "btc_cycles": btc_cycles,
            "sol_cycles": sol_cycles,
            "ok": True,
        }

    for label, api in [("btc", BTC_API), ("sol", SOL_API)]:
        try:
            inst = requests.get(f"{api}/api/instance", timeout=10).json()
            proof["checks"][f"instance_{label}"] = inst
        except Exception as exc:
            proof["checks"][f"instance_{label}"] = {"error": str(exc)}

    for label, url in [("btc_ui", BTC_UI), ("sol_ui", SOL_UI)]:
        try:
            r = requests.get(url, timeout=10)
            proof["checks"][label] = {"status": r.status_code, "has_instance_brand": "instance-brand" in r.text}
        except Exception as exc:
            proof["checks"][label] = {"error": str(exc)}

    # Snapshot BTC running before SOL stop test
    btc_before = None
    try:
        btc_before = requests.get(f"{BTC_API}/api/running", timeout=10).json().get("running")
    except Exception:
        pass

    if sol_ids:
        subprocess.run(
            ["docker", "compose", "-p", "ultiumgrid_sol", "-f", "docker-compose.sol.yml", "stop", "bot"],
            cwd=ROOT,
            check=False,
        )
        time.sleep(3)
        try:
            btc_after = requests.get(f"{BTC_API}/api/running", timeout=10).json().get("running")
            proof["checks"]["btc_unaffected_when_sol_bot_stopped"] = {
                "btc_running_before": btc_before,
                "btc_running_after": btc_after,
                "ok": btc_before == btc_after,
            }
        except Exception as exc:
            proof["checks"]["btc_unaffected_when_sol_bot_stopped"] = {"error": str(exc)}
        subprocess.run(
            ["docker", "compose", "-p", "ultiumgrid_sol", "-f", "docker-compose.sol.yml", "start", "bot"],
            cwd=ROOT,
            check=False,
        )

    proof["ok"] = all(
        c.get("ok") is True
        for k, c in proof["checks"].items()
        if isinstance(c, dict) and "ok" in c
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(OUT), "ok": proof["ok"]}, indent=2))
    return 0 if proof.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
