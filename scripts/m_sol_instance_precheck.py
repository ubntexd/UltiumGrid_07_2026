#!/usr/bin/env python3
"""Précheck SOL/USDT — exchangeInfo, minNotional, volatilité (sans clés pour partie publique)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "proofs" / "m_sol_instance_precheck.json"
REST = os.getenv("BINANCE_SPOT_REST_BASE", "https://demo-api.binance.com")
SYMBOL = "SOLUSDT"
CAPITAL = 4000.0
NUM_LEVELS = 20
NOTIONAL_PER_BUY_LEVEL = (CAPITAL / 2) / (NUM_LEVELS / 2)  # 100 USDT


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
    hi = max(closes)
    lo = min(closes)
    mid = (hi + lo) / 2
    return {
        "high": hi,
        "low": lo,
        "range_pct": ((hi - lo) / mid * 100) if mid else None,
        "bars": len(closes),
    }


def main() -> int:
    proof: dict = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "capital_usdt": CAPITAL,
        "num_levels": NUM_LEVELS,
        "notional_per_buy_level_usdt": NOTIONAL_PER_BUY_LEVEL,
        "rest_base": REST,
    }

    r = requests.get(f"{REST}/api/v3/exchangeInfo", params={"symbol": SYMBOL}, timeout=30)
    r.raise_for_status()
    info = r.json()
    sym = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)
    filters = {f["filterType"]: f for f in sym.get("filters") or []}
    tick = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    mn = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    min_notional = float(mn.get("minNotional") or mn.get("notional") or 0)

    proof["exchange_info"] = {
        "status": sym.get("status"),
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
        "note": "ratio >= 3x recommandé (100 USDT/palier vs minNotional)",
    }

    proof["volatility"] = {
        "SOLUSDT_1h_24bars": volatility_pct(klines(SYMBOL, "1h", 24)),
        "SOLUSDT_1d_7bars": volatility_pct(klines(SYMBOL, "1d", 7)),
        "BTCUSDT_1h_24bars": volatility_pct(klines("BTCUSDT", "1h", 24)),
        "BTCUSDT_1d_7bars": volatility_pct(klines("BTCUSDT", "1d", 7)),
        "note": "Contexte comparatif — pas d'ajustement config avant lancement",
    }

    env_path = ROOT / ".env.sol"
    proof["keys"] = {"env_sol_exists": env_path.exists(), "account_check": None}
    if env_path.exists():
        load_dotenv(env_path, override=True)
        key = (os.getenv("BINANCE_SPOT_TESTNET_API_KEY") or "").strip()
        if key:
            sys.path.insert(0, str(ROOT / "bot"))
            try:
                from ultiumgrid.bot_runner import build_client_from_env

                c = build_client_from_env()
                acc = c.account(force=True)
                bnb = c.balance_free("BNB", force=True)
                usdt = c.balance_free("USDT", force=True)
                proof["keys"]["account_check"] = {
                    "canTrade": acc.get("canTrade"),
                    "bnb_free": bnb,
                    "usdt_free": usdt,
                    "ok_bnb": bnb > 0,
                    "ok_usdt": usdt >= 3500,
                }
            except Exception as exc:
                proof["keys"]["account_check"] = {"error": str(exc)}

    proof["ok_to_deploy_structure"] = proof["min_notional_check"]["ok"]
    proof["ok_to_start_bot"] = (
        proof["min_notional_check"]["ok"]
        and proof["keys"].get("env_sol_exists")
        and isinstance(proof["keys"].get("account_check"), dict)
        and proof["keys"]["account_check"].get("ok_bnb")
        and proof["keys"]["account_check"].get("ok_usdt")
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"written": str(OUT), "ok_precheck": proof["min_notional_check"]["ok"]}, indent=2))
    return 0 if proof["min_notional_check"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
