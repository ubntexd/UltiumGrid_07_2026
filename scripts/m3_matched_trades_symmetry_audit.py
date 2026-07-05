#!/usr/bin/env python3
"""Audit symétrie BUY initial / SELL inventaire — Total Matched Trades cycle 2."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

_gp_path = ROOT / "bot" / "ultiumgrid" / "engine" / "grid_profit.py"
_spec = importlib.util.spec_from_file_location("grid_profit", _gp_path)
_gp = importlib.util.module_from_spec(_spec)
sys.modules["grid_profit"] = _gp
_spec.loader.exec_module(_gp)
total_matched_trades_from_trades = _gp.total_matched_trades_from_trades
compute_grid_profit_from_trades = _gp.compute_grid_profit_from_trades

OUT = ROOT / "docs" / "proofs" / "m3_matched_trades_symmetry_correction.json"


def sql(query: str) -> str:
    return subprocess.check_output(
        [
            "docker", "compose", "exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid",
            "-t", "-A", "-F", "|", "-c", query,
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def fetch_cycle_trades(cycle_id: int) -> list[dict]:
    raw = sql(
        f"SELECT id, side, price, quantity, level_index, order_id, "
        f"to_char(created_at,'YYYY-MM-DD HH24:MI:SS') "
        f"FROM trades WHERE cycle_id={cycle_id} ORDER BY id;"
    )
    trades = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        p = line.split("|")
        trades.append(
            {
                "id": int(p[0]),
                "side": p[1],
                "price": float(p[2]),
                "quantity": float(p[3]),
                "level_index": int(p[4]) if p[4] else None,
                "order_id": p[5],
                "created_at": p[6],
            }
        )
    return trades


def main() -> None:
    cycle_id = 2
    all_trades = fetch_cycle_trades(cycle_id)
    grid_trades = [t for t in all_trades if t["level_index"] is not None]
    old_count = len(grid_trades)
    new_count = total_matched_trades_from_trades(grid_trades)
    matched = compute_grid_profit_from_trades(grid_trades)

    proof = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_id": cycle_id,
        "incoherence_confirmed": old_count == 3 and new_count == 0,
        "sql_trades": all_trades,
        "old_rule": {
            "method": "COUNT(trades WHERE level_index IS NOT NULL)",
            "total_matched_trades": old_count,
            "problem": "3 SELL d'inventaire initial comptés alors que BUY initial exclu",
        },
        "new_rule": {
            "method": "total_matched_trades_from_trades() = roundtrip_count (matched_ledger)",
            "total_matched_trades": new_count,
            "roundtrip_count": matched["roundtrip_count"],
            "grid_fills_raw": matched["buy_fills"] + matched["sell_fills"],
        },
        "delta": new_count - old_count,
        "action": "rule_corrected_in_backend" if old_count != new_count else "non_concerne",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
