"""Collecte analyse trades UltiumGrid — BTC / SOL / XRP."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import requests

INSTANCES: list[dict[str, str]] = [
    {"id": "btc", "label": "BTC", "api": "http://127.0.0.1:18000", "symbol": "BTCUSDT"},
    {"id": "sol", "label": "SOL", "api": "http://127.0.0.1:18100", "symbol": "SOLUSDT"},
    {"id": "xrp", "label": "XRP", "api": "http://127.0.0.1:18200", "symbol": "XRPUSDT"},
]


def _get(base: str, path: str, timeout: int = 25) -> dict | list:
    r = requests.get(f"{base}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def analyze_instance(api_base: str, symbol: str, instance_id: str, label: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "instance_id": instance_id,
        "label": label,
        "symbol": symbol,
        "ok": False,
    }
    try:
        inst = _get(api_base, "/api/instance")
        running = _get(api_base, "/api/running")
        hist = _get(api_base, "/api/history")
        journal = _get(api_base, "/api/trades/journal", timeout=40)
        pnl_chart = _get(api_base, f"/api/charts/pnl?symbol={symbol}&limit=120")

        rows = journal.get("rows") or []
        g = running.get("grid") or {}
        gr = running.get("grid_recap") or {}
        guards = running.get("guards") or {}
        cap = running.get("capital") or {}

        closed = [c for c in hist if c.get("status") == "closed"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        closed_today = [c for c in closed if (c.get("closed_at") or "").startswith(today)]

        cid = running.get("cycle_id")
        cycle_trades = [t for t in rows if t.get("cycle_id") == cid]
        rt_pnls = [float(t["trade_pnl"]) for t in cycle_trades if t.get("trade_pnl") is not None]

        by_cat = Counter(t.get("category") for t in rows)
        fees = sum(float(t.get("fees_usdt") or 0) for t in rows)

        out.update(
            {
                "ok": True,
                "instance_meta": inst,
                "running": {
                    "active": running.get("running"),
                    "symbol": running.get("symbol"),
                    "cycle_id": cid,
                    "mark_price": running.get("mark_price"),
                    "mark_source": running.get("mark_source"),
                },
                "pnl_open": {
                    "grid_profit": g.get("grid_profit"),
                    "floating_profit": g.get("floating_profit"),
                    "gross_total": g.get("gross_pnl"),
                    "matched_trades": gr.get("total_matched_trades"),
                    "daily_pnl_guard": guards.get("daily_pnl"),
                },
                "capital": {
                    "quote_free": cap.get("quote_free"),
                    "base_total": cap.get("base_total"),
                    "base_asset": cap.get("base_asset"),
                },
                "realized": {
                    "closed_cycles": len(closed),
                    "sum_gross": sum(float(c.get("gross_pnl") or 0) for c in closed),
                    "sum_net": sum(float(c.get("net_pnl") or 0) for c in closed),
                    "closed_today": len(closed_today),
                    "net_today": sum(float(c.get("net_pnl") or 0) for c in closed_today),
                },
                "trades": {
                    "total": len(rows),
                    "fees_usdt": round(fees, 4),
                    "categories": dict(by_cat),
                    "roundtrips_cycle_open": len(rt_pnls),
                    "roundtrip_pnl_cycle_open": round(sum(rt_pnls), 4),
                    "buy_count": sum(1 for t in rows if t.get("side") == "BUY"),
                    "sell_count": sum(1 for t in rows if t.get("side") == "SELL"),
                },
                "pnl_curve": pnl_chart.get("points") or [],
                "last_closed_cycles": closed[:5],
            }
        )
    except Exception as exc:
        out["error"] = str(exc)
    return out


def collect_full_report(api_map: dict[str, str] | None = None) -> dict[str, Any]:
    """Rapport complet — api_map optionnel pour override URLs (depuis Docker n8n)."""
    api_map = api_map or {}
    ts = datetime.now(timezone.utc)
    instances_out = []
    for spec in INSTANCES:
        base = api_map.get(spec["id"], spec["api"])
        instances_out.append(
            analyze_instance(base, spec["symbol"], spec["id"], spec["label"])
        )
    return {
        "ts_utc": ts.isoformat(),
        "hour_key": ts.strftime("%Y-%m-%dT%H:00:00Z"),
        "instances": instances_out,
        "summary": {
            "total_net_realized": sum(
                i.get("realized", {}).get("sum_net", 0) for i in instances_out if i.get("ok")
            ),
            "total_net_today": sum(
                i.get("realized", {}).get("net_today", 0) for i in instances_out if i.get("ok")
            ),
            "total_gross_open": sum(
                i.get("pnl_open", {}).get("gross_total", 0) or 0 for i in instances_out if i.get("ok")
            ),
            "total_grid_open": sum(
                i.get("pnl_open", {}).get("grid_profit", 0) or 0 for i in instances_out if i.get("ok")
            ),
            "instances_ok": sum(1 for i in instances_out if i.get("ok")),
        },
    }
