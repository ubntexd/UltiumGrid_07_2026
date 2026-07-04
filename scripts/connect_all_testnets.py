#!/usr/bin/env python3
"""Lance les deux scripts de connexion testnet (Binance Futures + Hyperliquid)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = [
    "connect_binance_spot_testnet.py",
    "connect_hyperliquid_testnet.py",
]


def main() -> int:
    here = Path(__file__).resolve().parent
    codes = []
    for name in SCRIPTS:
        print("\n" + "=" * 60)
        proc = subprocess.run([sys.executable, str(here / name)], check=False)
        codes.append(proc.returncode)
    print("\n" + "=" * 60)
    if any(codes):
        print(f"Terminé avec erreurs : {codes}")
        return 1
    print("Tous les testnets : OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
