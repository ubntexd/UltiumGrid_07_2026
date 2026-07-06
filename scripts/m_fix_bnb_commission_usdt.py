#!/usr/bin/env python3
"""Corrige fees_paid.commission_usdt pour commissions BNB (× prix BNB, pas prix trade)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import hmac
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m_bnb_fees_retroactive_fix.json"


def bnb_usdt_price() -> float:
    r = requests.get(
        "https://demo-api.binance.com/api/v3/ticker/price",
        params={"symbol": "BNBUSDT"},
        timeout=15,
    )
    r.raise_for_status()
    return float(r.json()["price"])


def sql(project: str, compose_file: str, db: str, query: str) -> str:
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
            "-F",
            "|",
            "-c",
            query,
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def sql_exec(project: str, compose_file: str, db: str, query: str) -> None:
    subprocess.check_call(
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
            "-c",
            query,
        ],
        cwd=ROOT,
    )


def fix_instance(label: str, project: str, compose_file: str, db: str) -> dict:
    bnb_px = bnb_usdt_price()
    rows_raw = sql(
        project,
        compose_file,
        db,
        "SELECT id, cycle_id, commission, price, commission_usdt FROM fees_paid "
        "WHERE UPPER(commission_asset)='BNB' ORDER BY id;",
    )
    corrections = []
    if rows_raw:
        for line in rows_raw.split("\n"):
            if not line.strip():
                continue
            fid, cid, comm, price, old = line.split("|")
            comm_f, price_f, old_f = float(comm), float(price), float(old)
            new = comm_f * bnb_px
            corrections.append(
                {
                    "id": int(fid),
                    "cycle_id": int(cid) if cid else None,
                    "old_usdt": old_f,
                    "new_usdt": round(new, 8),
                }
            )
            sql_exec(
                project,
                compose_file,
                db,
                f"UPDATE fees_paid SET commission_usdt = {new} WHERE id = {fid};",
            )

    cycle_ids = sorted({c["cycle_id"] for c in corrections if c["cycle_id"]})
    net_updates = []
    for cid in cycle_ids:
        fees = float(
            sql(
                project,
                compose_file,
                db,
                f"SELECT COALESCE(SUM(commission_usdt),0) FROM fees_paid WHERE cycle_id={cid};",
            )
            or 0
        )
        gross = float(
            sql(project, compose_file, db, f"SELECT gross_pnl FROM cycles WHERE id={cid};") or 0
        )
        net = gross - fees
        sql_exec(
            project,
            compose_file,
            db,
            f"UPDATE cycles SET net_pnl = {net} WHERE id = {cid};",
        )
        net_updates.append({"cycle_id": cid, "fees_usdt": fees, "gross_pnl": gross, "net_pnl": net})

    return {
        "instance": label,
        "bnb_usdt_price_used": bnb_px,
        "rows_corrected": len(corrections),
        "corrections": corrections,
        "net_pnl_updates": net_updates,
    }


def main() -> int:
    proof = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "fix": "commission_usdt = commission * bnb_usdt (was commission * trade_price)",
        "instances": [],
    }
    proof["instances"].append(
        fix_instance("btc", "ultiumgrid_07_2026", "docker-compose.yml", "ultiumgrid")
    )
    try:
        proof["instances"].append(
            fix_instance("sol", "ultiumgrid_sol", "docker-compose.sol.yml", "ultiumgrid_sol")
        )
    except Exception as exc:
        proof["instances"].append({"instance": "sol", "error": str(exc)})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(OUT), "rows": sum(i.get("rows_corrected", 0) for i in proof["instances"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
