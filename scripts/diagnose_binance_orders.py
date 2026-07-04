#!/usr/bin/env python3
"""Diagnostic placement d'ordres Binance Spot Testnet."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env")

from ultiumgrid.bot_runner import build_client_from_env  # noqa: E402
from ultiumgrid.connector.binance_spot import DEFAULT_REST, DEFAULT_WS  # noqa: E402


def main() -> int:
    try:
        client = build_client_from_env()
    except Exception as exc:
        print(f"Client: {exc}")
        return 1

    report = {
        "DEFAULT_REST": DEFAULT_REST,
        "DEFAULT_WS": DEFAULT_WS,
        "client.rest_base": client.rest_base,
        "client.ws_base": client.ws_base,
        "steps": [],
    }

    status, body, raw = client._raw_request("GET", "/api/v3/ping")
    report["steps"].append({"step": "ping", "status": status, "raw": raw})

    try:
        acc = client.account()
        balances = [
            b for b in acc.get("balances", []) if float(b.get("free", 0)) + float(b.get("locked", 0)) > 0
        ]
        report["steps"].append(
            {
                "step": "account",
                "status": 200,
                "canTrade": acc.get("canTrade"),
                "balances_nonzero": balances[:10],
            }
        )
    except Exception as exc:
        report["steps"].append({"step": "account", "error": str(exc)[:300]})
        out = ROOT / "docs" / "proofs" / "spot_order_diagnosis.json"
        out.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        print("\nBLOQUANT: clés Spot invalides. Créer sur https://testnet.binance.vision")
        return 2

    status, body, raw = client._raw_request(
        "POST",
        "/api/v3/order/test",
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": "0.001",
            "price": "40000",
        },
        signed=True,
    )
    report["steps"].append({"step": "order_test", "status": status, "raw": raw[:300]})

    client.attempt_log.clear()
    try:
        placed = client.place_order(
            symbol="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            quantity="0.001",
            price="40000",
            max_attempts=2,
        )
        report["steps"].append({"step": "order_real", "ok": True, "order": placed})
        client.cancel_order("BTCUSDT", placed["orderId"])
        report["steps"].append({"step": "cancel", "ok": True})
    except Exception as exc:
        report["steps"].append(
            {
                "step": "order_real",
                "ok": False,
                "error": str(exc)[:300],
                "outcomes": [a["outcome"] for a in client.attempt_log],
            }
        )

    order_real = next(s for s in report["steps"] if s["step"] == "order_real")
    report["diagnosis"] = (
        "Placement Spot OK."
        if order_real.get("ok")
        else "Échec placement — voir steps."
    )
    out = ROOT / "docs" / "proofs" / "spot_order_diagnosis.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print("\n===", report["diagnosis"])
    return 0 if order_real.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
