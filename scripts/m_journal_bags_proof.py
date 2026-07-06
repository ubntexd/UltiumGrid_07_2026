#!/usr/bin/env python3
"""Preuve live — journal API + sacs traçabilité."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PROOFS = ROOT / "docs" / "proofs"
API = "http://localhost:8000"


def _sql_count(query: str) -> int:
    r = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "ultium",
            "-d",
            "ultiumgrid",
            "-t",
            "-A",
            "-c",
            query,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)
    return int(r.stdout.strip().split("\n")[0])


def main() -> int:
    proof: dict = {}
    j = requests.get(f"{API}/api/trades/journal?page_size=500", timeout=30).json()
    sql_count = _sql_count("SELECT COUNT(*) FROM trades")
    proof["journal_api"] = {
        "total": j.get("total"),
        "db_trade_count": j.get("db_trade_count"),
        "sample_rows": (j.get("rows") or [])[:3],
    }
    proof["sql_trade_count"] = sql_count
    proof["count_match"] = j.get("db_trade_count") == sql_count == j.get("total")

    cycle_id = None
    if j.get("rows"):
        cycle_id = j["rows"][0].get("cycle_id")
    if cycle_id:
        jf = requests.get(
            f"{API}/api/trades/journal?cycle_id={cycle_id}&page_size=500",
            timeout=30,
        ).json()
        sql_cycle = _sql_count(f"SELECT COUNT(*) FROM trades WHERE cycle_id={cycle_id}")
        proof["filter_cycle"] = {
            "cycle_id": cycle_id,
            "api_total": jf.get("total"),
            "sql_count": sql_cycle,
            "match": jf.get("total") == sql_cycle,
        }

    j_buy = requests.get(f"{API}/api/trades/journal?side=BUY&page_size=500", timeout=30).json()
    sql_buy = _sql_count("SELECT COUNT(*) FROM trades WHERE side='BUY'")
    proof["filter_side_buy"] = {
        "api_total": j_buy.get("total"),
        "sql_count": sql_buy,
        "match": j_buy.get("total") == sql_buy,
    }

    bags = requests.get(f"{API}/api/bags?status=all&include_snapshots=true", timeout=30).json()
    proof["bags_api_count"] = len(bags)
    proof["bags_sample"] = bags[:2]
    if bags:
        b0 = bags[0]
        proof["bag_fields_present"] = {
            k: k in b0
            for k in [
                "creation_reason",
                "cycle_id_origin",
                "incomplete_levels_at_creation",
                "market_price_at_creation",
                "sold_price",
                "sold_by",
                "sold_at",
            ]
        }
    else:
        proof["bag_fields_note"] = "aucun sac en DB — champs vérifiés via test_m_journal_bags.py"

    out = PROOFS / "m_journal_bags_live_proof.json"
    out.write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))
    ok = proof.get("count_match") and proof.get("filter_side_buy", {}).get("match", True)
    if cycle_id:
        ok = ok and proof.get("filter_cycle", {}).get("match", False)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
