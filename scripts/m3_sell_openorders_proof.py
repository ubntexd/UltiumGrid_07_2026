#!/usr/bin/env python3
"""Preuve M3 : 10 SELL avec order_id réel dans openOrders + chart UI + matched trades."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:18000"
UI = "http://127.0.0.1:18080"


def http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> None:
    running = http_get(f"{API}/api/running")
    chart = http_get(f"{API}/api/charts/price?limit=10")
    client = __import__("os").environ
    # openOrders brut via bot container
    oo_raw = subprocess.check_output(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "bot",
            "python",
            "-c",
            "import json; from ultiumgrid.bot_runner import build_client_from_env; "
            "c=build_client_from_env(); print(json.dumps(c.open_orders('BTCUSDT', force=True)))",
        ],
        cwd=ROOT,
        text=True,
    )
    open_orders = json.loads(oo_raw)

    levels = running.get("grid", {}).get("levels") or []
    sells = [lv for lv in levels if lv.get("side") == "SELL"]
    buys = [lv for lv in levels if lv.get("side") == "BUY"]
    oo_by_id = {str(o["orderId"]): o for o in open_orders}
    oo_sells = [o for o in open_orders if o.get("side") == "SELL"]
    oo_buys = [o for o in open_orders if o.get("side") == "BUY"]

    sell_proof = []
    all_sell_in_open = True
    for lv in sorted(sells, key=lambda x: x.get("index", 0)):
        oid = lv.get("order_id")
        oid_s = str(oid) if oid is not None else None
        ex = oo_by_id.get(oid_s) if oid_s else None
        row = {
            "index": lv.get("index"),
            "price_db": lv.get("price"),
            "qty_db": lv.get("quantity"),
            "status_db": lv.get("status"),
            "order_id_db": oid,
            "in_openOrders": ex is not None,
            "openOrders_status": ex.get("status") if ex else None,
            "openOrders_side": ex.get("side") if ex else None,
            "openOrders_price": ex.get("price") if ex else None,
            "openOrders_qty": ex.get("origQty") if ex else None,
        }
        if not row["in_openOrders"] or row["status_db"] != "open":
            all_sell_in_open = False
        sell_proof.append(row)

    trades_raw = subprocess.check_output(
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
            f"SELECT id, side, price, quantity, order_id, level_index FROM trades WHERE cycle_id={running.get('cycle_id')};",
        ],
        cwd=ROOT,
        text=True,
    ).strip()

    recap = running.get("grid_recap") or {}
    ib = recap.get("initial_inventory_buy") or {}

    # UI chart presence via API proxy
    chart_levels = len(chart.get("levels") or [])
    chart_fills = chart.get("fills") or []

    proof = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_id": running.get("cycle_id"),
        "running": running.get("running"),
        "element1_chart_on_page": {
            "api_levels_count": chart_levels,
            "api_fills_count": len(chart_fills),
            "chart_endpoint": f"{API}/api/charts/price",
            "ui_url": UI,
            "screenshot": "docs/proofs/m3_element1_full_page_screenshot.png",
            "note": "20 lignes H dans le plugin ultiumGridOverlay si levels>=20",
        },
        "element2_sell_openorders": {
            "sell_levels_db": len(sells),
            "sell_with_order_id_db": sum(1 for lv in sells if lv.get("order_id")),
            "openOrders_total": len(open_orders),
            "openOrders_sell_count": len(oo_sells),
            "openOrders_buy_count": len(oo_buys),
            "per_sell_level": sell_proof,
            "all_10_sell_in_openOrders": all_sell_in_open and len(sells) == 10,
        },
        "matched_trades_semantics": {
            "total_matched_trades_ui": recap.get("total_matched_trades"),
            "grid_matched_trades": recap.get("grid_matched_trades"),
            "initial_inventory_buy": ib,
            "trades_sql_raw": trades_raw,
            "interpretation": (
                "total_matched_trades=0 attendu si seul l'achat initial est enregistré "
                "(level_index NULL, exclu du compteur grille)"
            ),
            "grid_matched_is_zero_not_initial_buy": recap.get("total_matched_trades") == 0,
        },
        "conforme": False,
    }
    proof["conforme"] = bool(
        proof["element1_chart_on_page"]["api_levels_count"] == 20
        and proof["element2_sell_openorders"]["all_10_sell_in_openOrders"]
        and proof["matched_trades_semantics"]["grid_matched_is_zero_not_initial_buy"]
    )

    out = ROOT / "docs" / "proofs" / "m3_sell_openorders_proof.json"
    out.write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps({"conforme": proof["conforme"], "sells_ok": proof["element2_sell_openorders"]["all_10_sell_in_openOrders"]}, indent=2))
    print("WROTE", out)


if __name__ == "__main__":
    main()
