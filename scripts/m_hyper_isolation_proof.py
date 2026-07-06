#!/usr/bin/env python3
"""Preuves d'isolation BTC vs SOL vs HYPER — DB, réseau, non-régression croisée."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_hyper_instance_v1" / "isolation_check.json"

INSTANCES = [
    ("btc", "ultiumgrid_07_2026", "docker-compose.yml", "ultiumgrid", "http://127.0.0.1:18000", "http://127.0.0.1:18080"),
    ("sol", "ultiumgrid_sol", "docker-compose.sol.yml", "ultiumgrid_sol", "http://127.0.0.1:18100", "http://127.0.0.1:18180"),
    ("hyper", "ultiumgrid_hyper", "docker-compose.hyper.yml", "ultiumgrid_hyper", "http://127.0.0.1:18200", "http://127.0.0.1:18280"),
]


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
            "docker", "compose", "-p", project, "-f", compose_file,
            "exec", "-T", "db", "psql", "-U", "ultium", "-d", db, "-t", "-A", "-c", q,
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def main() -> int:
    proof: dict = {"ts_utc": datetime.now(timezone.utc).isoformat(), "checks": {}}

    all_ids: dict[str, list[str]] = {}
    for label, project, compose_file, *_ in INSTANCES:
        all_ids[label] = compose_ps(project, compose_file)

    intersections = []
    labels = list(all_ids.keys())
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            inter = set(all_ids[labels[i]]) & set(all_ids[labels[j]])
            intersections.append({labels[i]: labels[j], "intersection": list(inter), "ok": len(inter) == 0})
    proof["checks"]["container_ids_disjoint"] = {"instances": {k: len(v) for k, v in all_ids.items()}, "pairs": intersections}

    if all_ids.get("hyper"):
        proof["checks"]["db_names"] = {
            label: sql(project, cf, db, "SELECT current_database();")
            for label, project, cf, db, *_ in INSTANCES
            if all_ids.get(label)
        }
        proof["checks"]["cycle_counts"] = {
            label: sql(project, cf, db, "SELECT COUNT(*) FROM cycles;")
            for label, project, cf, db, *_ in INSTANCES
            if all_ids.get(label)
        }

    for label, *_rest, api, ui in INSTANCES:
        try:
            inst = requests.get(f"{api}/api/instance", timeout=10).json()
            proof["checks"][f"instance_{label}"] = inst
        except Exception as exc:
            proof["checks"][f"instance_{label}"] = {"error": str(exc)}
        try:
            r = requests.get(ui, timeout=10)
            proof["checks"][f"ui_{label}"] = {"status": r.status_code, "has_instance_brand": "instance-brand" in r.text}
        except Exception as exc:
            proof["checks"][f"ui_{label}"] = {"error": str(exc)}

    snapshots = {}
    for label, *_rest, api, _ in INSTANCES:
        try:
            snapshots[label] = requests.get(f"{api}/api/running", timeout=10).json().get("running")
        except Exception:
            snapshots[label] = None

    if all_ids.get("hyper"):
        subprocess.run(
            ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml", "stop", "bot"],
            cwd=ROOT, check=False,
        )
        time.sleep(3)
        after = {}
        for label, *_rest, api, _ in INSTANCES:
            if label == "hyper":
                continue
            try:
                after[label] = requests.get(f"{api}/api/running", timeout=10).json().get("running")
            except Exception as exc:
                after[label] = {"error": str(exc)}
        proof["checks"]["btc_sol_unaffected_when_hyper_bot_stopped"] = {
            "before": {k: snapshots[k] for k in ("btc", "sol")},
            "after": after,
            "ok": snapshots.get("btc") == after.get("btc") and snapshots.get("sol") == after.get("sol"),
        }
        subprocess.run(
            ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml", "start", "bot"],
            cwd=ROOT, check=False,
        )

    proof["ok"] = all(
        p.get("ok") is True for p in intersections
    ) and proof["checks"].get("btc_sol_unaffected_when_hyper_bot_stopped", {}).get("ok") is not False

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(OUT), "ok": proof["ok"]}, indent=2))
    return 0 if proof.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
