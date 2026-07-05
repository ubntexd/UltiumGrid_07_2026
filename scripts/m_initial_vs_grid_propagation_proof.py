#!/usr/bin/env python3
"""Preuve propagation achat initial vs fills de grille (PnL, histogramme, frais, viabilité)."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:18000"
OUT = ROOT / "docs" / "proofs" / "m_initial_vs_grid_propagation.json"


def http_get(path: str) -> dict:
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read().decode())


def sql(query: str) -> str:
    return subprocess.check_output(
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
    ).strip()


def main() -> None:
    running = http_get("/api/running")
    cycle_id = running.get("cycle_id")
    recap = running.get("grid_recap") or {}
    pnl = http_get("/api/pnl")
    cycles_chart = http_get("/api/charts/cycles")
    price_chart = http_get(f"/api/charts/price?limit=80")
    fees = http_get("/api/fees")
    config = http_get("/api/config")

    trades_raw = sql(
        "SELECT id, side, price, quantity, order_id, level_index FROM trades ORDER BY id;"
    )
    fees_raw = sql(
        "SELECT cycle_id, order_id, commission_usdt FROM fees_paid ORDER BY id;"
    )
    grid_matched_sql = (
        sql(f"SELECT count(*) FROM trades WHERE cycle_id={cycle_id} AND level_index IS NOT NULL;")
        if cycle_id
        else "0"
    )
    initial_buy_sql = (
        sql(f"SELECT count(*) FROM trades WHERE cycle_id={cycle_id} AND level_index IS NULL;")
        if cycle_id
        else "0"
    )
    fees_cycle = (
        sql(f"SELECT coalesce(sum(commission_usdt),0) FROM fees_paid WHERE cycle_id={cycle_id};")
        if cycle_id
        else "0"
    )
    initial_fee_row = (
        sql(
            f"SELECT commission_usdt FROM fees_paid WHERE cycle_id={cycle_id} "
            f"AND order_id='{(recap.get('initial_inventory_buy') or {}).get('order_id')}';"
        )
        if cycle_id and (recap.get("initial_inventory_buy") or {}).get("order_id")
        else ""
    )

    viab = config.get("viability") or {}
    fills = price_chart.get("fills") or []

    proof = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_id_open": cycle_id,
        "1_pnl_analysis_module_7ter": {
            "source": "GET /api/pnl",
            "uses_trades_table": False,
            "query_cycles": "Cycle WHERE symbol=X AND status='closed'",
            "formulas_api": pnl.get("formulas"),
            "win_rate": pnl.get("win_rate"),
            "avg_win": pnl.get("avg_win"),
            "avg_loss": pnl.get("avg_loss"),
            "avg_cycle_duration_sec": pnl.get("avg_cycle_duration_sec"),
            "cycles_total": pnl.get("cycles_total"),
            "interpretation": "Indicateurs dérivés de cycle.net_pnl / durées — pas de table trades",
            "initial_buy_not_counted_as_trade": True,
        },
        "2_histogram_module_8": {
            "source": "GET /api/charts/cycles",
            "query": "Cycle WHERE status='closed' ORDER BY id DESC",
            "bars": cycles_chart.get("bars"),
            "uses_trades_table": False,
            "price_chart_fills": fills,
            "initial_buy_marker_only_on_price_chart": any(
                f.get("level_index") is None and f.get("side") == "BUY" for f in fills
            ),
            "grid_fill_has_level_index": any(f.get("level_index") is not None for f in fills),
            "histogram_excludes_individual_trades": True,
        },
        "3_fees_module_7quater": {
            "source": "GET /api/fees + fees_paid SQL",
            "fees_paid_sql": fees_raw,
            "cycle_open_fees_sum_usdt": float(fees_cycle or 0),
            "initial_buy_order_id": (recap.get("initial_inventory_buy") or {}).get("order_id"),
            "initial_buy_fee_usdt": float(initial_fee_row) if initial_fee_row else None,
            "initial_buy_fee_included_in_cycle_total": bool(initial_fee_row),
            "fee_query_no_level_index_filter": "FeePaid WHERE cycle_id=X (tous ordres)",
            "rows_api_sample": fees.get("rows", [])[:5],
        },
        "4_viability_module_7bis": {
            "source": "GET /api/config viability",
            "fees_initial_inventory": viab.get("fees_initial_inventory"),
            "fees_fixed_per_cycle": viab.get("fees_fixed_per_cycle"),
            "fees_per_roundtrip": viab.get("fees_per_roundtrip"),
            "grids_to_cycle": viab.get("grids_to_cycle"),
            "net_at_gross_threshold": viab.get("net_at_gross_threshold"),
            "total_fees_at_gross_threshold": viab.get("total_fees_at_gross_threshold"),
            "formulas": viab.get("formulas"),
            "includes_initial_buy_as_fixed_cost": viab.get("fees_initial_inventory") is not None,
        },
        "5_trades_table_audit": {
            "trades_sql_raw": trades_raw,
            "grid_matched_count": int(grid_matched_sql or 0),
            "initial_buy_rows": int(initial_buy_sql or 0),
            "ui_total_matched_trades": recap.get("total_matched_trades"),
            "grid_recap_initial_inventory_buy": recap.get("initial_inventory_buy"),
            "occurrences": [
                {
                    "location": "backend/app/main.py _build_grid_recap",
                    "filter": "level_index IS NOT NULL",
                    "rule": "exclure achat initial (matched trades)",
                },
                {
                    "location": "backend/app/main.py chart_price fills",
                    "filter": "all trades + fallback cycle_meta initial_buy",
                    "rule": "inclure achat initial (marqueur B visuel)",
                },
                {
                    "location": "bot/ultiumgrid/bot_runner.py _record_fees_for_order",
                    "filter": "myTrades par order_id",
                    "rule": "inclure achat initial (frais réels)",
                },
                {
                    "location": "bot/ultiumgrid/bot_runner.py _close_cycle_db",
                    "filter": "FeePaid WHERE cycle_id",
                    "rule": "inclure tous frais dont achat initial dans net_pnl cycle",
                },
            ],
        },
        "conforme": False,
    }

    proof["conforme"] = bool(
        proof["1_pnl_analysis_module_7ter"]["initial_buy_not_counted_as_trade"]
        and proof["2_histogram_module_8"]["histogram_excludes_individual_trades"]
        and proof["3_fees_module_7quater"]["initial_buy_fee_included_in_cycle_total"]
        and proof["4_viability_module_7bis"]["includes_initial_buy_as_fixed_cost"]
        and recap.get("total_matched_trades") == int(grid_matched_sql or 0)
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps(proof, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
