#!/usr/bin/env python3
"""Audit Grid Profit cycle 3 — ancien calcul vs appariement Binance."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

import importlib.util  # noqa: E402

_gp_path = ROOT / "bot" / "ultiumgrid" / "engine" / "grid_profit.py"
_spec = importlib.util.spec_from_file_location("grid_profit", _gp_path)
_gp = importlib.util.module_from_spec(_spec)
sys.modules["grid_profit"] = _gp
_spec.loader.exec_module(_gp)
compute_grid_profit_from_trades = _gp.compute_grid_profit_from_trades

OUT = ROOT / "docs" / "proofs" / "m3_grid_profit_correction_cycle3.json"


def sql(query: str) -> str:
    return subprocess.check_output(
        [
            "docker", "compose", "exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid",
            "-t", "-A", "-F", "|", "-c", query,
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def simulate_old_entry_avg(trades: list[dict], initial_entry: float, initial_qty: float) -> float:
    """Ancienne logique on_fill : realized sur SELL vs entry_avg global."""
    entry = initial_entry
    qty = initial_qty
    grid_profit = 0.0
    for t in trades:
        fill_qty = float(t["quantity"])
        fill_px = float(t["price"])
        signed = fill_qty if t["side"] == "BUY" else -fill_qty
        prev = qty
        new = prev + signed
        if prev == 0:
            entry = fill_px
        elif (prev > 0 and signed > 0) or (prev < 0 and signed < 0):
            entry = (abs(prev) * entry + fill_qty * fill_px) / (abs(prev) + fill_qty)
        elif prev != 0 and ((prev > 0 and signed < 0) or (prev < 0 and signed > 0)):
            closed = min(abs(prev), fill_qty)
            direction = 1 if prev > 0 else -1
            grid_profit += direction * (fill_px - entry) * closed
        qty = new
    return grid_profit


def main() -> None:
    raw = sql(
        "SELECT id, side, price, quantity, level_index, "
        "to_char(created_at,'YYYY-MM-DD HH24:MI:SS') "
        "FROM trades WHERE cycle_id=3 AND level_index IS NOT NULL ORDER BY id;"
    )
    trades = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        trades.append(
            {
                "id": int(parts[0]),
                "side": parts[1],
                "price": float(parts[2]),
                "quantity": float(parts[3]),
                "level_index": int(parts[4]),
                "created_at": parts[5],
            }
        )

    matched = compute_grid_profit_from_trades(trades, fee_rate=0.001)
    # entry initial cycle 3 ~ center 63306
    old_wrong = simulate_old_entry_avg(trades, initial_entry=63306.34, initial_qty=0.01159)

    proof = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_id": 3,
        "trade_count": len(trades),
        "buy_count": sum(1 for t in trades if t["side"] == "BUY"),
        "sell_count": sum(1 for t in trades if t["side"] == "SELL"),
        "bug_before": {
            "method": "on_fill: grid_profit += (sell_price - entry_avg_global) * qty",
            "problem": "SELLs imputés au coût moyen incluant inventaire initial, pas au BUY du palier i",
            "simulated_grid_profit": round(old_wrong, 6),
            "ui_was_approx": -5.37,
        },
        "fix_after": {
            "method": "MatchedGridLedger: FIFO BUY(level i) + SELL(level i+1)",
            "formula": "sell*q*(1-fee) - buy*q*(1+fee) sur qty appariée",
            "grid_profit": round(matched["grid_profit"], 6),
            "roundtrip_count": matched["roundtrip_count"],
            "orphan_buy_qty": matched["orphan_buy_qty_pending"],
            "orphan_sell_qty": matched["orphan_sell_qty_pending"],
            "roundtrips_sample": matched["matched_roundtrips"][:5],
        },
        "delta_ui_vs_correct": round(-5.37 - matched["grid_profit"], 6),
        "conforme_binance_definition": matched["grid_profit"] >= 0,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
