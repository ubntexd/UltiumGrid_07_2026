#!/usr/bin/env python3
"""Non-régression mark HYPER — après fix WS, sans relancer le trading."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_hyper_instance_v1" / "ws_price_regression.json"
COMPOSE = ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml"]
API = "http://127.0.0.1:18200"


def main() -> int:
    hyper_rest = float(
        requests.get("https://demo-api.binance.com/api/v3/ticker/price", params={"symbol": "HYPERUSDT"}, timeout=10).json()["price"]
    )
    time.sleep(8)
    running = requests.get(f"{API}/api/running", timeout=15).json()
    mark = running.get("mark_price")
    ratio = abs(mark - hyper_rest) / hyper_rest if mark and hyper_rest else None
    logs = subprocess.check_output(COMPOSE + ["logs", "bot"], cwd=ROOT, text=True)
    ws_hyper = "hyperusdt@bookticker" in logs.lower()
    mark_ok = mark is not None and ratio is not None and ratio < 0.05
    proof = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "hyper_rest": hyper_rest,
        "api_mark": mark,
        "mark_source": running.get("mark_source"),
        "ratio_error": ratio,
        "ws_hyperusdt_connected_in_logs": ws_hyper,
        "ok": mark_ok and ws_hyper,
        "running": running.get("running"),
        "symbol": running.get("symbol"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
