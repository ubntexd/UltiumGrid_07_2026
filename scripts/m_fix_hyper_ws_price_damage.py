#!/usr/bin/env python3
"""Correction rétroactive cycles HYPER — PnL absurdes (WS btcusdt sur instance HYPERUSDT)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_hyper_instance_v1" / "ws_price_bug_fix.json"
COMPOSE = ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml"]
THRESHOLD = 1000.0  # gross_pnl absurde sur capital 5000


def sql(q: str) -> str:
    return subprocess.check_output(
        COMPOSE + ["exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid_hyper", "-t", "-A", "-c", q],
        cwd=ROOT,
        text=True,
    ).strip()


def main() -> int:
    proof: dict = {"ts_utc": datetime.now(timezone.utc).isoformat(), "corrections": []}

    rows_raw = sql(
        "SELECT id, symbol, status, close_reason, gross_pnl, grid_profit, net_pnl, floating_profit "
        "FROM cycles ORDER BY id;"
    )
    cycles = []
    for line in rows_raw.split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        cycles.append(
            {
                "id": int(parts[0]),
                "symbol": parts[1],
                "status": parts[2],
                "close_reason": parts[3] or None,
                "gross_pnl": float(parts[4]) if parts[4] else 0,
                "grid_profit": float(parts[5]) if parts[5] else 0,
                "net_pnl": float(parts[6]) if parts[6] else 0,
                "floating_profit": float(parts[7]) if parts[7] else 0,
            }
        )

    absurd = [c for c in cycles if abs(c["gross_pnl"]) > THRESHOLD]
    proof["cycles_total"] = len(cycles)
    proof["cycles_absurd"] = len(absurd)
    proof["absurd_ids"] = [c["id"] for c in absurd]

    for c in absurd:
        old = dict(c)
        corrected_gross = c["grid_profit"]
        corrected_net = c["grid_profit"]
        entry = {
            "cycle_id": c["id"],
            "before": old,
            "after": {
                "gross_pnl": corrected_gross,
                "net_pnl": corrected_net,
                "floating_profit": 0.0,
                "note": "Recalcul: grid_profit seul (floating gonflé par mark BTC erroné)",
            },
        }
        sql(
            f"UPDATE cycles SET gross_pnl={corrected_gross}, net_pnl={corrected_net}, "
            f"floating_profit=0 WHERE id={c['id']};"
        )
        proof["corrections"].append(entry)

    open_c = next((c for c in cycles if c["status"] == "open"), None)
    if open_c and abs(open_c.get("gross_pnl", 0)) > THRESHOLD:
        sql(f"UPDATE cycles SET gross_pnl=0, net_pnl=0, floating_profit=0 WHERE id={open_c['id']};")

    bad_ticks = sql("SELECT COUNT(*) FROM price_ticks WHERE symbol='HYPERUSDT' AND price > 1;")
    proof["price_ticks_btc_contaminated"] = int(bad_ticks)
    sql("DELETE FROM bot_state WHERE key='live_pnl';")
    sql(
        "INSERT INTO bot_state (key, value_json, updated_at) VALUES "
        "('ws_price_bug_correction', "
        f"'{json.dumps({'corrected_cycles': proof['absurd_ids'], 'ts': proof['ts_utc']})}'::jsonb, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value_json=EXCLUDED.value_json, updated_at=NOW();"
    )

    proof["post_fix_cycle_sum_net"] = sql("SELECT COALESCE(SUM(net_pnl),0) FROM cycles WHERE status='closed';")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(OUT), "corrected": len(absurd)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
