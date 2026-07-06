#!/usr/bin/env python3
"""Vérification candidat XRPUSDT — exchangeInfo, minNotional, volatilité, depth, comparatif 4 colonnes."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m_xrp_candidate_check.json"
REST = os.getenv("BINANCE_SPOT_REST_BASE", "https://demo-api.binance.com")
SYMBOL = "XRPUSDT"
COMPARE = ["BTCUSDT", "SOLUSDT", "HYPERUSDT", "XRPUSDT"]
CAPITAL = 5000.0
NUM_LEVELS = 20
STEP_PCT = 0.4
CYCLE_TRIGGER = 15.0
NOTIONAL_PER_BUY_LEVEL = (CAPITAL / 2) / (NUM_LEVELS / 2)


def load_viability():
    spec = importlib.util.spec_from_file_location(
        "viability", ROOT / "bot" / "ultiumgrid" / "engine" / "viability.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute_viability


def klines(symbol: str, interval: str, limit: int = 100) -> list:
    r = requests.get(
        f"{REST}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def volatility_pct(kl: list) -> dict:
    if not kl:
        return {}
    closes = [float(k[4]) for k in kl]
    hi, lo = max(closes), min(closes)
    mid = (hi + lo) / 2
    return {
        "high": hi,
        "low": lo,
        "range_pct": ((hi - lo) / mid * 100) if mid else None,
        "bars": len(closes),
    }


def exchange_info(symbol: str) -> dict:
    r = requests.get(f"{REST}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=30)
    if r.status_code >= 400:
        return {"error": r.status_code, "body": r.text[:500]}
    sym = next(s for s in r.json()["symbols"] if s["symbol"] == symbol)
    filters = {f["filterType"]: f for f in sym.get("filters") or []}
    tick = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    mn = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    min_notional = float(mn.get("minNotional") or mn.get("notional") or 0)
    return {
        "status": sym.get("status"),
        "baseAsset": sym.get("baseAsset"),
        "quoteAsset": sym.get("quoteAsset"),
        "tickSize": tick.get("tickSize"),
        "stepSize": lot.get("stepSize"),
        "minNotional": min_notional,
        "filters_present": list(filters.keys()),
    }


def depth_summary(symbol: str) -> dict:
    r = requests.get(f"{REST}/api/v3/depth", params={"symbol": symbol, "limit": 100}, timeout=30)
    if not r.ok:
        return {"error": r.status_code}
    d = r.json()
    bids, asks = d.get("bids") or [], d.get("asks") or []
    bid_qty = sum(float(b[1]) for b in bids[:20])
    ask_qty = sum(float(a[1]) for a in asks[:20])
    return {
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "top20_bid_qty": bid_qty,
        "top20_ask_qty": ask_qty,
        "book_empty": len(bids) == 0 and len(asks) == 0,
    }


def main() -> int:
    compute_viability = load_viability()
    proof: dict = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "method": "verification directe demo-api.binance.com — meme compte que HYPER (.env.hyper)",
        "target_symbol": SYMBOL,
        "capital_usdt": CAPITAL,
        "num_levels": NUM_LEVELS,
        "step_pct": STEP_PCT,
        "cycle_trigger_usd": CYCLE_TRIGGER,
        "notional_per_buy_level_usdt": NOTIONAL_PER_BUY_LEVEL,
        "rest_base": REST,
        "transition_note": "Remplacement instance 3 : HYPERUSDT terminee → XRPUSDT (meme stack Docker, ports 18200/18280)",
    }

    xrp_info = exchange_info(SYMBOL)
    proof["exchange_info"] = xrp_info
    if "error" in xrp_info:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        print(json.dumps({"written": str(OUT), "ok": False}, indent=2))
        return 1

    min_notional = xrp_info["minNotional"]
    proof["min_notional_check"] = {
        "notional_per_level": NOTIONAL_PER_BUY_LEVEL,
        "min_notional": min_notional,
        "ratio": NOTIONAL_PER_BUY_LEVEL / min_notional if min_notional else None,
        "ok": NOTIONAL_PER_BUY_LEVEL >= min_notional * 3,
    }
    proof["viability"] = compute_viability(
        CAPITAL, NUM_LEVELS, STEP_PCT, CYCLE_TRIGGER, bnb_fee_discount=True, bnb_balance=1.0
    )
    proof["depth_top100"] = depth_summary(SYMBOL)

    vol: dict = {}
    for sym in COMPARE:
        vol[f"{sym}_1h_24bars"] = volatility_pct(klines(sym, "1h", 24))
        vol[f"{sym}_1d_7bars"] = volatility_pct(klines(sym, "1d", 7))
    proof["volatility"] = vol

    comparison_table: dict = {}
    for sym in COMPARE:
        info = exchange_info(sym) if sym != SYMBOL else xrp_info
        cap = 5000 if sym in ("BTCUSDT", "HYPERUSDT", "XRPUSDT") else 4000
        notional = (cap / 2) / (NUM_LEVELS / 2)
        mn = info.get("minNotional", 0) or 0
        comparison_table[sym] = {
            "status": info.get("status"),
            "tickSize": info.get("tickSize"),
            "stepSize": info.get("stepSize"),
            "minNotional": mn,
            "capital_usdt": cap,
            "notional_per_buy_level": notional,
            "ratio_notional_to_min": notional / mn if mn else None,
            "ok_min_notional_3x": notional >= mn * 3 if mn else None,
            "vol_1h_24bars_range_pct": vol.get(f"{sym}_1h_24bars", {}).get("range_pct"),
            "vol_1d_7bars_range_pct": vol.get(f"{sym}_1d_7bars", {}).get("range_pct"),
            "depth_top20_bid_qty": depth_summary(sym).get("top20_bid_qty"),
            "depth_top20_ask_qty": depth_summary(sym).get("top20_ask_qty"),
        }
    proof["comparison_table_btc_sol_hyper_xrp"] = comparison_table

    env_path = ROOT / ".env.hyper"
    proof["keys"] = {"env_hyper_exists": env_path.exists(), "account_check": None}
    if env_path.exists():
        env_vars = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()
        api_key = (env_vars.get("BINANCE_SPOT_TESTNET_API_KEY") or "").strip()
        api_secret = (env_vars.get("BINANCE_SPOT_TESTNET_API_SECRET") or "").strip()
        if api_key and api_secret:
            import hashlib
            import hmac
            import time as _time
            from urllib.parse import urlencode

            params = {"timestamp": int(_time.time() * 1000), "recvWindow": 5000}
            qs = urlencode(params)
            sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
            try:
                ar = requests.get(
                    f"{REST}/api/v3/account?{qs}&signature={sig}",
                    headers={"X-MBX-APIKEY": api_key},
                    timeout=15,
                )
                if ar.status_code == 200:
                    acc = ar.json()
                    usdt = next((float(b["free"]) for b in acc["balances"] if b["asset"] == "USDT"), 0.0)
                    bnb = next((float(b["free"]) for b in acc["balances"] if b["asset"] == "BNB"), 0.0)
                    hyper = next(
                        (float(b["free"]) + float(b["locked"]) for b in acc["balances"] if b["asset"] == "HYPER"),
                        0.0,
                    )
                    proof["keys"]["account_check"] = {
                        "canTrade": acc.get("canTrade"),
                        "bnb_free": bnb,
                        "usdt_free": usdt,
                        "hyper_remaining": hyper,
                        "ok_bnb": bnb > 0,
                        "ok_usdt": usdt >= 4500,
                        "ok_no_hyper_dust": hyper < 1.0,
                    }
                else:
                    proof["keys"]["account_check"] = {"error": ar.status_code, "body": ar.text[:300]}
            except Exception as exc:
                proof["keys"]["account_check"] = {"error": str(exc)}

    proof["ok_to_deploy_structure"] = proof["min_notional_check"]["ok"]
    ac = proof["keys"].get("account_check") or {}
    proof["ok_to_start_bot"] = (
        proof["min_notional_check"]["ok"]
        and xrp_info.get("status") == "TRADING"
        and proof["keys"].get("env_hyper_exists")
        and ac.get("ok_bnb")
        and ac.get("ok_usdt")
        and ac.get("ok_no_hyper_dust", True)
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "written": str(OUT),
                "ok_precheck": proof["min_notional_check"]["ok"],
                "ok_to_start": proof["ok_to_start_bot"],
                "net_at_gross_threshold": proof["viability"]["net_at_gross_threshold"],
            },
            indent=2,
        )
    )
    return 0 if proof["ok_to_start_bot"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
