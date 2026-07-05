#!/usr/bin/env python3
"""Preuve conformité visuelle Binance — graphique niveaux + tableau récap."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:18000"


def http_get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read().decode())


def sql_json(query: str) -> list:
    raw = subprocess.check_output(
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
        text=True,
    )
    return raw.strip()


def main() -> None:
    running = http_get("/api/running")
    chart = http_get(f"/api/charts/price?symbol={running['symbol']}&limit=120")
    recap = running.get("grid_recap")
    db_levels = running.get("grid", {}).get("levels") or []

    level_compare = []
    chart_by_idx = {lv["index"]: lv for lv in chart.get("levels") or []}
    for lv in db_levels:
        idx = lv.get("index")
        ch = chart_by_idx.get(idx, {})
        visual = ch.get("visual")
        if lv.get("status") in ("grid_level_incomplete", "error") or (
            lv.get("status") == "pending" and not lv.get("order_id")
        ):
            expected_visual = "inactive"
        else:
            expected_visual = "active"
        level_compare.append(
            {
                "index": idx,
                "side_db": lv.get("side"),
                "price_db": float(lv["price"]) if lv.get("price") is not None else None,
                "qty_db": float(lv["quantity"]) if lv.get("quantity") is not None else None,
                "status_db": lv.get("status"),
                "order_id_db": lv.get("order_id"),
                "price_chart_api": ch.get("price"),
                "qty_chart_api": ch.get("quantity"),
                "visual_chart_api": visual,
                "expected_visual": expected_visual,
                "price_match": ch.get("price") == float(lv["price"])
                if ch.get("price") is not None and lv.get("price") is not None
                else False,
                "visual_match": visual == expected_visual,
                "color_rule": "green BUY" if lv.get("side") == "BUY" else "red SELL Limit",
            }
        )

    cycle_id = running.get("cycle_id")
    grid_matched_sql = 0
    if cycle_id:
        grid_matched_sql = int(
            sql_json(
                f"SELECT count(*) FROM trades WHERE cycle_id={cycle_id} AND level_index IS NOT NULL;"
            )
            or "0"
        )

    recap_compare = {}
    if recap:
        recap_compare = {
            "pair": {"ui": recap.get("pair"), "running": running.get("symbol")},
            "total_investment": {
                "ui": recap.get("total_investment"),
                "config": running.get("config", {}).get("capital_usdt"),
            },
            "grid_profit": {
                "ui": recap.get("grid_profit"),
                "running_grid": running.get("grid", {}).get("grid_profit"),
            },
            "floating_profit": {
                "ui": recap.get("floating_profit"),
                "running_grid": running.get("grid", {}).get("floating_profit"),
            },
            "total_profit": {
                "ui": recap.get("total_profit"),
                "running_gross": running.get("grid", {}).get("gross_pnl"),
            },
            "total_matched_trades": {
                "ui": recap.get("total_matched_trades"),
                "sql_grid_matched": grid_matched_sql,
                "note": "COUNT(level_index IS NOT NULL) — exclut achat initial",
            },
            "number_of_grids": {
                "ui": recap.get("number_of_grids"),
                "config": running.get("config", {}).get("num_levels"),
                "levels_count": len(db_levels),
            },
        }

    proof = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "running": running.get("running"),
        "cycle_id": cycle_id,
        "chart_levels_count": len(chart.get("levels") or []),
        "db_levels_count": len(db_levels),
        "fills_on_chart": chart.get("fills") or [],
        "level_compare": level_compare,
        "all_levels_price_match": all(r["price_match"] for r in level_compare),
        "all_levels_visual_match": all(r["visual_match"] for r in level_compare),
        "recap_compare": recap_compare,
        "recap_columns_ok": bool(
            recap
            and recap.get("total_matched_trades") == grid_matched_sql
            and recap.get("grid_profit") == running.get("grid", {}).get("grid_profit")
            and recap.get("number_of_grids") == len(db_levels)
        ),
        "conforme": False,
    }
    proof["conforme"] = bool(
        proof["all_levels_price_match"]
        and proof["all_levels_visual_match"]
        and proof["chart_levels_count"] == proof["db_levels_count"] == 20
        and proof["recap_columns_ok"]
    )

    out = ROOT / "docs" / "proofs" / "m8_binance_visual.json"
    out.write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps({"conforme": proof["conforme"], "levels": proof["chart_levels_count"]}, indent=2))
    print("WROTE", out)


if __name__ == "__main__":
    main()
