#!/usr/bin/env python3
"""Connexion Binance USDT-M Futures Testnet.

Base URL par défaut : https://demo-fapi.binance.com
Docs : https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
Surcharge : BINANCE_FUTURES_REST_BASE dans .env
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

TIMEOUT = 15


def _load_env() -> None:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")


def _base_url() -> str:
    return os.getenv("BINANCE_FUTURES_REST_BASE", "https://demo-fapi.binance.com").strip().rstrip("/")


def _signed_get(path: str, api_key: str, api_secret: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    url = f"{_base_url()}{path}?{query}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def ping() -> bool:
    r = requests.get(f"{_base_url()}/fapi/v1/ping", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() == {}


def server_time() -> int:
    r = requests.get(f"{_base_url()}/fapi/v1/time", timeout=TIMEOUT)
    r.raise_for_status()
    return int(r.json()["serverTime"])


def exchange_info_symbols(limit: int = 5) -> list[str]:
    r = requests.get(f"{_base_url()}/fapi/v1/exchangeInfo", timeout=TIMEOUT)
    r.raise_for_status()
    symbols = [s["symbol"] for s in r.json().get("symbols", []) if s.get("status") == "TRADING"]
    return symbols[:limit]


def account(api_key: str, api_secret: str) -> dict:
    return _signed_get("/fapi/v2/account", api_key, api_secret)


def main() -> int:
    _load_env()
    print("=== Binance Futures Testnet ===")
    print(f"Base URL : {_base_url()}")

    try:
        ok = ping()
        ts = server_time()
        symbols = exchange_info_symbols()
        print(f"Ping     : OK ({ok})")
        print(f"Time     : {ts}")
        print(f"Symbols  : {', '.join(symbols)}")
    except requests.RequestException as exc:
        print(f"ERREUR public API : {exc}", file=sys.stderr)
        return 1

    api_key = os.getenv("BINANCE_FUTURES_TESTNET_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_FUTURES_TESTNET_API_SECRET", "").strip()

    if not api_key or not api_secret:
        print("Auth     : clés absentes (.env) — connexion publique seule OK")
        print("Renseignez BINANCE_FUTURES_TESTNET_API_KEY / _SECRET pour le compte.")
        return 0

    try:
        acc = account(api_key, api_secret)
        balances = [
            b
            for b in acc.get("assets", [])
            if float(b.get("walletBalance", 0)) != 0
        ]
        print(f"Auth     : OK (canTrade={acc.get('canTrade')})")
        print(f"Assets   : {len(balances)} non nuls")
        for b in balances[:8]:
            print(
                f"  - {b.get('asset')}: wallet={b.get('walletBalance')} "
                f"available={b.get('availableBalance')}"
            )
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        print(f"ERREUR auth API : {exc} {body}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERREUR auth API : {exc}", file=sys.stderr)
        return 1

    print("Connexion Binance Futures Testnet : SUCCES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
