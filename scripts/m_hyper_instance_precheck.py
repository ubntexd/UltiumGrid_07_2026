#!/usr/bin/env python3
"""Précheck HYPERUSDT — exchangeInfo, minNotional, volatilité, viabilité Module 7bis."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_a, **_k):
        return False

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m3_hyper_instance_v1" / "precheck.json"
REST = os.getenv("BINANCE_SPOT_REST_BASE", "https://demo-api.binance.com")
SYMBOL = "HYPERUSDT"
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


def main() -> int:
    compute_viability = load_viability()
    proof: dict = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "symbol_requested": SYMBOL,
        "symbol_note": "HYPERUSDT (actif HYPER) — pas HYPE/Hyperliquid",
        "capital_usdt": CAPITAL,
        "num_levels": NUM_LEVELS,
        "step_pct": STEP_PCT,
        "cycle_trigger_usd": CYCLE_TRIGGER,
        "notional_per_buy_level_usdt": NOTIONAL_PER_BUY_LEVEL,
        "rest_base": REST,
    }

    r = requests.get(f"{REST}/api/v3/exchangeInfo", params={"symbol": SYMBOL}, timeout=30)
    if r.status_code >= 400:
        proof["exchange_info"] = {"error": r.status_code, "body": r.text[:500]}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        print(json.dumps({"written": str(OUT), "ok": False}, indent=2))
        return 1

    info = r.json()
    sym = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
    filters = {f["filterType"]: f for f in sym.get("filters") or []}
    tick = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    mn = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    min_notional = float(mn.get("minNotional") or mn.get("notional") or 0)

    proof["exchange_info"] = {
        "status": sym.get("status"),
        "baseAsset": sym.get("baseAsset"),
        "quoteAsset": sym.get("quoteAsset"),
        "tickSize": tick.get("tickSize"),
        "stepSize": lot.get("stepSize"),
        "minNotional": min_notional,
        "filters_present": list(filters.keys()),
    }
    proof["min_notional_check"] = {
        "notional_per_level": NOTIONAL_PER_BUY_LEVEL,
        "min_notional": min_notional,
        "ratio": NOTIONAL_PER_BUY_LEVEL / min_notional if min_notional else None,
        "ok": NOTIONAL_PER_BUY_LEVEL >= min_notional * 3,
    }

    proof["viability"] = compute_viability(
        CAPITAL, NUM_LEVELS, STEP_PCT, CYCLE_TRIGGER, bnb_fee_discount=True, bnb_balance=1.0
    )

    proof["volatility"] = {
        f"{SYMBOL}_1h_24bars": volatility_pct(klines(SYMBOL, "1h", 24)),
        f"{SYMBOL}_1d_7bars": volatility_pct(klines(SYMBOL, "1d", 7)),
        "BTCUSDT_1h_24bars": volatility_pct(klines("BTCUSDT", "1h", 24)),
        "SOLUSDT_1h_24bars": volatility_pct(klines("SOLUSDT", "1h", 24)),
        "note": "Donnees Demo realistes != marche reel",
    }

    dep = requests.get(f"{REST}/api/v3/depth", params={"symbol": SYMBOL, "limit": 100}, timeout=30)
    if dep.ok:
        d = dep.json()
        bids, asks = d.get("bids") or [], d.get("asks") or []
        proof["depth_top100"] = {
            "bid_levels": len(bids),
            "ask_levels": len(asks),
            "book_empty": len(bids) == 0 and len(asks) == 0,
        }

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
                    proof["keys"]["account_check"] = {
                        "canTrade": acc.get("canTrade"),
                        "bnb_free": bnb,
                        "usdt_free": usdt,
                        "ok_bnb": bnb > 0,
                        "ok_usdt": usdt >= 4500,
                    }
                else:
                    proof["keys"]["account_check"] = {"error": ar.status_code, "body": ar.text[:300]}
            except Exception as exc:
                proof["keys"]["account_check"] = {"error": str(exc)}

    proof["ok_to_deploy_structure"] = proof["min_notional_check"]["ok"]
    proof["ok_to_start_bot"] = (
        proof["min_notional_check"]["ok"]
        and proof["exchange_info"].get("status") == "TRADING"
        and proof["keys"].get("env_hyper_exists")
        and isinstance(proof["keys"].get("account_check"), dict)
        and proof["keys"]["account_check"].get("ok_bnb")
        and proof["keys"]["account_check"].get("ok_usdt")
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "written": str(OUT),
                "ok_precheck": proof["min_notional_check"]["ok"],
                "net_at_gross_threshold": proof["viability"]["net_at_gross_threshold"],
                "ok_to_start": proof["ok_to_start_bot"],
            },
            indent=2,
        )
    )
    return 0 if proof["min_notional_check"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
