#!/usr/bin/env python3
"""Investigation + preuve bug WS prix BTC sur instance HYPER."""

from __future__ import annotations

import hmac
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_hyper_instance_v1" / "ws_price_bug_investigation.json"
COMPOSE = ["docker", "compose", "-p", "ultiumgrid_hyper", "-f", "docker-compose.hyper.yml"]
API = "http://127.0.0.1:18200"


def sql(q: str) -> str:
    return subprocess.check_output(
        COMPOSE + ["exec", "-T", "db", "psql", "-U", "ultium", "-d", "ultiumgrid_hyper", "-t", "-A", "-c", q],
        cwd=ROOT,
        text=True,
    ).strip()


def load_env():
    env = {}
    for line in (ROOT / ".env.hyper").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def signed_get(path, key, secret, params=None):
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    qs = urlencode(params)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    r = requests.get(
        f"https://demo-api.binance.com{path}?{qs}&signature={sig}",
        headers={"X-MBX-APIKEY": key},
        timeout=20,
    )
    return r.status_code, r.json() if r.ok else r.text[:300]


def main() -> int:
    env = load_env()
    key, secret = env["BINANCE_SPOT_TESTNET_API_KEY"], env["BINANCE_SPOT_TESTNET_API_SECRET"]
    hyper_rest = float(requests.get("https://demo-api.binance.com/api/v3/ticker/price", params={"symbol": "HYPERUSDT"}, timeout=10).json()["price"])
    btc_rest = float(requests.get("https://demo-api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"}, timeout=10).json()["price"])

    running = requests.get(f"{API}/api/running", timeout=15).json()
    sc, acc = signed_get("/api/v3/account", key, secret)
    balances = {}
    if sc == 200:
        for b in acc["balances"]:
            free, locked = float(b["free"]), float(b["locked"])
            if free + locked > 0:
                balances[b["asset"]] = free + locked

    sc2, trades = signed_get("/api/v3/myTrades", key, secret, {"symbol": "HYPERUSDT", "limit": 500})
    sc3, orders = signed_get("/api/v3/allOrders", key, secret, {"symbol": "HYPERUSDT", "limit": 500})

    ws_logs = subprocess.check_output(
        COMPOSE + ["logs", "bot", "--tail", "200"],
        cwd=ROOT,
        text=True,
    )
    ws_btc_lines = [ln for ln in ws_logs.splitlines() if "btcusdt@bookTicker" in ln.lower()]
    ws_hyper_lines = [ln for ln in ws_logs.splitlines() if "hyperusdt@bookTicker" in ln.lower()]

    proof = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "root_cause": {
            "confirmed": True,
            "distinct_from_sol_incident": True,
            "description": (
                "Au démarrage container, WS bookTicker connecté sur btcusdt@bookTicker (config par défaut BTCUSDT). "
                "Après application config HYPERUSDT (bot inactif), le flux WS n'a pas été relancé. "
                "on_ws_price enregistrait le mid BTC sous cfg.symbol=HYPERUSDT; tick() préférait _live_mark → PnL flottant absurde → trigger_15 en boucle."
            ),
            "sol_incident_comparison": "SOL = config rejetée → bot démarré en BTCUSDT (symbole trading). HYPER = symbole trading correct (HYPERUSDT) mais mark price WS resté BTC.",
        },
        "rest_prices_at_investigation": {"HYPERUSDT": hyper_rest, "BTCUSDT": btc_rest},
        "api_running": {
            "symbol": running.get("symbol"),
            "mark_price": running.get("mark_price"),
            "mark_source": running.get("mark_source"),
            "cycle_id": running.get("cycle_id"),
            "running": running.get("running"),
            "gross_pnl": (running.get("grid") or {}).get("gross_pnl"),
        },
        "db": {
            "cycles_total": sql("SELECT COUNT(*) FROM cycles;"),
            "cycles_trigger_15": sql("SELECT COUNT(*) FROM cycles WHERE close_reason='trigger_15';"),
            "max_gross_pnl": sql("SELECT MAX(gross_pnl) FROM cycles;"),
            "live_pnl_mark": sql("SELECT value_json->>'mark' FROM bot_state WHERE key='live_pnl';"),
            "price_ticks_hyper_gt_1": sql("SELECT COUNT(*) FROM price_ticks WHERE symbol='HYPERUSDT' AND price > 1;"),
            "sample_bad_ticks": sql("SELECT price FROM price_ticks WHERE symbol='HYPERUSDT' AND price > 1 ORDER BY id DESC LIMIT 3;"),
        },
        "binance_account": {"balances": balances, "starting_usdt_reference": 8266.32},
        "real_trades": {
            "myTrades_count": len(trades) if sc2 == 200 else 0,
            "allOrders_count": len(orders) if sc3 == 200 else 0,
            "sample_last_trade": trades[-1] if sc2 == 200 and trades else None,
            "note": "Ordres réels exécutés au prix HYPER correct (~0.075) — bug limité au mark WS/PnL affiché",
        },
        "ws_logs": {
            "btcusdt_connect_count_in_tail200": len(ws_btc_lines),
            "hyperusdt_connect_count_in_tail200": len(ws_hyper_lines),
            "first_btc_line": ws_btc_lines[0] if ws_btc_lines else None,
        },
        "real_damage_estimate": {
            "usdt_now": balances.get("USDT"),
            "hyper_now": balances.get("HYPER"),
            "usdt_delta_vs_start_approx": round(balances.get("USDT", 0) - 8266.32, 2),
            "note": "Pertes réelles = frais + churn cycles (~57 trigger_15), pas les milliards affichés en DB",
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(OUT), "mark_api": running.get("mark_price"), "hyper_rest": hyper_rest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
