#!/usr/bin/env python3
"""Diagnostic placement d'ordres Binance Futures Demo.

Prouve :
- URL REST réellement utilisée
- account / listenKey / order/test / order réel
- Si order/test=200 et order=-1007 → clés/matching, pas le format de requête.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env")

from ultiumgrid.connector.binance_futures import (  # noqa: E402
    DEFAULT_REST,
    DEFAULT_WS,
    BinanceFuturesClient,
)


def main() -> int:
    key = os.getenv("BINANCE_FUTURES_TESTNET_API_KEY", "").strip()
    secret = os.getenv("BINANCE_FUTURES_TESTNET_API_SECRET", "").strip()
    if not key or not secret:
        print("Clés absentes dans .env")
        return 1

    client = BinanceFuturesClient(api_key=key, api_secret=secret)
    report: dict = {
        "DEFAULT_REST": DEFAULT_REST,
        "DEFAULT_WS": DEFAULT_WS,
        "client.rest_base": client.rest_base,
        "client.ws_base": client.ws_base,
        "env_REST": os.getenv("BINANCE_FUTURES_REST_BASE"),
        "official_docs_REST": "https://demo-fapi.binance.com",
        "key_source_required": "https://demo.binance.com → API Management (clés demo, pas l'ancien testnet)",
        "steps": [],
    }

    # ping
    status, body, raw = client._raw_request("GET", "/fapi/v1/ping")
    report["steps"].append({"step": "ping", "status": status, "raw": raw})

    # account
    try:
        acc = client.account()
        report["steps"].append(
            {
                "step": "account",
                "status": 200,
                "canTrade": acc.get("canTrade"),
                "availableBalance": acc.get("availableBalance"),
            }
        )
    except Exception as exc:
        report["steps"].append({"step": "account", "error": str(exc)[:300]})

    # order/test (ne touche PAS le matching engine)
    status, body, raw = client._raw_request(
        "POST",
        "/fapi/v1/order/test",
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": "0.002",
            "price": "40000",
            "positionSide": "LONG",
        },
        signed=True,
    )
    report["steps"].append({"step": "order_test", "status": status, "raw": raw[:300]})

    # order réel
    client.attempt_log.clear()
    try:
        placed = client.place_order(
            symbol="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity="0.002",
            price="40000",
            max_attempts=2,
        )
        report["steps"].append({"step": "order_real", "ok": True, "order": placed})
        client.cancel_order("BTCUSDT", placed["orderId"])
    except Exception as exc:
        report["steps"].append(
            {
                "step": "order_real",
                "ok": False,
                "error": str(exc)[:300],
                "outcomes": [a["outcome"] for a in client.attempt_log],
                "last_binance": client.attempt_log[-2]["response_json"]
                if len(client.attempt_log) >= 2
                else None,
            }
        )

    # Interprétation
    order_test_ok = any(
        s.get("step") == "order_test" and s.get("status") == 200 for s in report["steps"]
    )
    order_real = next(s for s in report["steps"] if s.get("step") == "order_real")
    if order_test_ok and not order_real.get("ok"):
        report["diagnosis"] = (
            "Requête et signature VALIDES (order/test HTTP 200), mais le matching engine "
            "ne répond pas (order réel -1007). Cause documentée : clés de l'ancien testnet "
            "ou compte demo non provisionné pour le trading. Solution non négociable : "
            "créer de NOUVELLES clés sur https://demo.binance.com (API Management), "
            "les mettre dans .env, relancer ce script."
        )
    elif order_real.get("ok"):
        report["diagnosis"] = "Placement d'ordres OK sur demo-fapi."
    else:
        report["diagnosis"] = "Échec avant matching — vérifier clés / URL."

    out = ROOT / "docs" / "proofs" / "m1_order_diagnosis.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print("\n=== DIAGNOSIS ===")
    print(report["diagnosis"])
    return 0 if order_real.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
