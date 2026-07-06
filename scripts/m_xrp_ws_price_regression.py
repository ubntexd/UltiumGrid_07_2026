#!/usr/bin/env python3
"""Non-régression mark XRP — après changement symbole HYPER→XRP (fix restart_price_stream)."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_xrp_instance_v1" / "ws_price_regression.json"
COMPOSE = ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml"]
API = "http://127.0.0.1:18200"


def main() -> int:
    xrp_rest = float(
        requests.get("https://demo-api.binance.com/api/v3/ticker/price", params={"symbol": "XRPUSDT"}, timeout=10).json()["price"]
    )
    time.sleep(5)
    running = requests.get(f"{API}/api/running", timeout=15).json()
    mark = running.get("mark_price")
    ratio = abs(mark - xrp_rest) / xrp_rest if mark and xrp_rest else None
    logs = subprocess.check_output(COMPOSE + ["logs", "--tail", "120", "bot"], cwd=ROOT, text=True)
    ws_xrp = "xrpusdt@bookticker" in logs.lower()
    ws_hyper_stale = logs.lower().count("hyperusdt@bookticker") > logs.lower().count("xrpusdt@bookticker")
    mark_ok = mark is not None and ratio is not None and ratio < 0.02
    proof = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "xrp_rest": xrp_rest,
        "api_mark": mark,
        "mark_source": running.get("mark_source"),
        "ratio_error": ratio,
        "ws_xrpusdt_connected_in_logs": ws_xrp,
        "ws_not_dominated_by_hyper": not ws_hyper_stale,
        "ok": mark_ok and ws_xrp and not ws_hyper_stale,
        "running": running.get("running"),
        "symbol": running.get("symbol"),
        "scenario": "post HYPER→XRP symbol change — restart_price_stream validation",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
