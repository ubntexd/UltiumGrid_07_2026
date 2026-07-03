#!/usr/bin/env python3
"""Connexion Hyperliquid Testnet.

API Info     : https://api.hyperliquid-testnet.xyz/info
API Exchange : https://api.hyperliquid-testnet.xyz/exchange
Docs         : https://hyperliquid.gitbook.io/hyperliquid-docs
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.hyperliquid-testnet.xyz"
TIMEOUT = 15


def _load_env() -> None:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env")


def post_info(payload: dict) -> dict | list:
    r = requests.post(f"{BASE_URL}/info", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def meta() -> dict:
    return post_info({"type": "meta"})


def all_mids() -> dict:
    return post_info({"type": "allMids"})


def clearinghouse_state(address: str) -> dict:
    return post_info({"type": "clearinghouseState", "user": address})


def address_from_private_key(private_key: str) -> str:
    from eth_account import Account

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    return Account.from_key(private_key).address


def main() -> int:
    _load_env()
    print("=== Hyperliquid Testnet ===")
    print(f"Base URL : {BASE_URL}")

    try:
        m = meta()
        universe = m.get("universe", [])
        mids = all_mids()
        sample = list(mids.items())[:5]
        print(f"Meta     : OK ({len(universe)} marchés perp)")
        print("Mids     : " + ", ".join(f"{k}={v}" for k, v in sample))
    except requests.RequestException as exc:
        print(f"ERREUR public API : {exc}", file=sys.stderr)
        return 1

    private_key = os.getenv("HL_TESTNET_PRIVATE_KEY", "").strip()
    account_address = os.getenv("HL_TESTNET_ACCOUNT_ADDRESS", "").strip()

    if not private_key and not account_address:
        print("Auth     : clé/adresse absentes (.env) — connexion publique seule OK")
        print("Renseignez HL_TESTNET_PRIVATE_KEY (ou HL_TESTNET_ACCOUNT_ADDRESS).")
        return 0

    try:
        if private_key:
            address = address_from_private_key(private_key)
            print(f"Wallet   : {address} (dérivé de la clé)")
        else:
            address = account_address
            print(f"Wallet   : {address} (adresse fournie)")

        state = clearinghouse_state(address)
        margin = state.get("marginSummary", {})
        positions = [
            p for p in state.get("assetPositions", []) if float(p.get("position", {}).get("szi", 0)) != 0
        ]
        print(f"Account  : accountValue={margin.get('accountValue')}")
        print(f"Positions: {len(positions)} ouvertes")
        for item in positions[:8]:
            pos = item.get("position", {})
            print(
                f"  - {pos.get('coin')}: szi={pos.get('szi')} "
                f"entry={pos.get('entryPx')} uPnl={pos.get('unrealizedPnl')}"
            )
    except Exception as exc:  # noqa: BLE001 — script CLI
        print(f"ERREUR compte : {exc}", file=sys.stderr)
        return 1

    print("Connexion Hyperliquid Testnet : SUCCES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
