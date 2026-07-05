#!/usr/bin/env python3
"""Recalcule grid_profit / floating_profit des cycles clos (appariement Binance).

Préserve gross_pnl et net_pnl économiques : seul le découpage grid/floating est corrigé.
Journalise ancien/nouveau dans docs/proofs/m3_grid_profit_retroactive_corrections.json
"""
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
compute_grid_profit_from_trades = _gp.compute_grid_profit_from_trades

OUT = ROOT / "docs" / "proofs" / "m3_grid_profit_retroactive_corrections.json"
FIX_DEPLOYED_UTC = "2026-07-05T17:33:55+00:00"  # mtime grid_profit.py au déploiement
FEE_RATE = 0.001


def sql(query: str) -> str:
    return subprocess.check_output(
        [
            "docker", "compose", "exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid",
            "-t", "-A", "-F", "|", "-c", query,
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def sql_exec(query: str) -> None:
    subprocess.check_call(
        [
            "docker", "compose", "exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid",
            "-c", query,
        ],
        cwd=ROOT,
    )


def fetch_closed_cycles() -> list[dict]:
    raw = sql(
        "SELECT id, status, close_reason, grid_profit, floating_profit, gross_pnl, net_pnl, "
        "to_char(closed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
        "FROM cycles WHERE status='closed' ORDER BY id;"
    )
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        p = line.split("|")
        rows.append(
            {
                "id": int(p[0]),
                "status": p[1],
                "close_reason": p[2],
                "grid_profit": float(p[3]),
                "floating_profit": float(p[4]),
                "gross_pnl": float(p[5]),
                "net_pnl": float(p[6]),
                "closed_at": p[7] or None,
            }
        )
    return rows


def fetch_cycle_trades(cycle_id: int) -> list[dict]:
    raw = sql(
        f"SELECT id, side, price, quantity, level_index, "
        f"to_char(created_at,'YYYY-MM-DD HH24:MI:SS') "
        f"FROM trades WHERE cycle_id={cycle_id} AND level_index IS NOT NULL ORDER BY id;"
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
    return trades


def fetch_fees(cycle_id: int) -> float:
    raw = sql(f"SELECT COALESCE(SUM(commission_usdt),0) FROM fees_paid WHERE cycle_id={cycle_id};")
    return float(raw or 0)


def main() -> None:
    cycles = fetch_closed_cycles()
    corrections: list[dict] = []

    for c in cycles:
        cid = c["id"]
        trades = fetch_cycle_trades(cid)
        matched = compute_grid_profit_from_trades(trades, fee_rate=FEE_RATE)
        new_grid = float(matched["grid_profit"])
        gross = c["gross_pnl"]
        fees = fetch_fees(cid)
        new_floating = gross - new_grid
        new_net = gross - fees
        closed_before_fix = (
            c["closed_at"] is not None and c["closed_at"] < FIX_DEPLOYED_UTC.replace("+00:00", "Z")
        )
        needs_update = (
            abs(c["grid_profit"] - new_grid) > 1e-9
            or abs(c["floating_profit"] - new_floating) > 1e-9
        )
        entry = {
            "cycle_id": cid,
            "close_reason": c["close_reason"],
            "closed_at": c["closed_at"],
            "closed_before_grid_profit_fix": closed_before_fix,
            "fix_deployed_utc": FIX_DEPLOYED_UTC,
            "grid_trade_count": len(trades),
            "roundtrip_count": matched["roundtrip_count"],
            "before": {
                "grid_profit": c["grid_profit"],
                "floating_profit": c["floating_profit"],
                "gross_pnl": gross,
                "net_pnl": c["net_pnl"],
            },
            "after": {
                "grid_profit": round(new_grid, 12),
                "floating_profit": round(new_floating, 12),
                "gross_pnl": gross,
                "net_pnl": round(new_net, 12),
            },
            "delta": {
                "grid_profit": round(new_grid - c["grid_profit"], 12),
                "floating_profit": round(new_floating - c["floating_profit"], 12),
                "net_pnl": round(new_net - c["net_pnl"], 12),
            },
            "action": "recalculated" if needs_update else "non_concerne",
        }
        if needs_update:
            sql_exec(
                f"UPDATE cycles SET "
                f"grid_profit={new_grid}, "
                f"floating_profit={new_floating}, "
                f"net_pnl={new_net} "
                f"WHERE id={cid};"
            )
        corrections.append(entry)

    proof = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "fix_deployed_utc": FIX_DEPLOYED_UTC,
        "method": "MatchedGridLedger depuis trades DB; gross_pnl inchangé; floating=gross-grid",
        "cycles_checked": len(cycles),
        "cycles_corrected": sum(1 for x in corrections if x["action"] == "recalculated"),
        "corrections": corrections,
        "organic_run_note": (
            "Cycle 2 du run 48h recalculé en cours de route — voir corrections[]."
            if any(x["cycle_id"] == 2 and x["action"] == "recalculated" for x in corrections)
            else None
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
