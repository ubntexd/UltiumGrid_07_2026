"""Indicateur de viabilité économique (spec §1bis).

Formules (reproductibles) :
- notional_per_level = capital_usdt / (num_levels / 2)
- fee_rate_taker = 0.001 (0.1%) sans BNB, 0.00075 (0.075%) avec BNB (taux standard Spot ;
  si commissionRates dispo sur le compte, on les utilise)
- fees_per_roundtrip = notional_per_level * fee_rate_taker * 2  (buy + sell)
- gross_per_grid = notional_per_level * (step_pct / 100)
- net_per_grid = gross_per_grid - fees_per_roundtrip
- ratio = gross_per_grid / fees_per_roundtrip
- grids_to_cycle = ceil(cycle_trigger_usd / net_per_grid) si net_per_grid > 0
"""

from __future__ import annotations

import math
from typing import Any


# Taux Spot standards Binance (fallback si account sans commissionRates)
DEFAULT_TAKER = 0.001
BNB_TAKER = 0.00075


def fee_rate_from_account(account: dict | None, bnb_discount: bool) -> tuple[float, str]:
    """Retourne (taker_rate, source)."""
    if account and isinstance(account.get("commissionRates"), dict):
        rates = account["commissionRates"]
        taker = float(rates.get("taker") or DEFAULT_TAKER)
        if bnb_discount:
            # Réduction BNB typique 25 %
            return taker * 0.75, "account.commissionRates.taker * 0.75 (BNB)"
        return taker, "account.commissionRates.taker"
    if bnb_discount:
        return BNB_TAKER, "default_bnb_taker_0.075pct"
    return DEFAULT_TAKER, "default_taker_0.1pct"


def compute_viability(
    capital_usdt: float,
    num_levels: int,
    step_pct: float,
    cycle_trigger_usd: float,
    bnb_fee_discount: bool = False,
    account: dict | None = None,
    bnb_balance: float = 0.0,
) -> dict[str, Any]:
    buy_levels = max(num_levels // 2, 1)
    notional_per_level = capital_usdt / buy_levels
    fee_rate, fee_source = fee_rate_from_account(account, bnb_fee_discount)
    fees_per_roundtrip = notional_per_level * fee_rate * 2.0
    gross_per_grid = notional_per_level * (step_pct / 100.0)
    net_per_grid = gross_per_grid - fees_per_roundtrip
    ratio = (gross_per_grid / fees_per_roundtrip) if fees_per_roundtrip > 0 else float("inf")
    grids_to_cycle = (
        int(math.ceil(cycle_trigger_usd / net_per_grid)) if net_per_grid > 0 else None
    )
    alert = ratio < 2.0
    bnb_ok = (not bnb_fee_discount) or (bnb_balance > 0)
    return {
        "notional_per_level": notional_per_level,
        "fee_rate": fee_rate,
        "fee_source": fee_source,
        "fees_per_roundtrip": fees_per_roundtrip,
        "gross_per_grid": gross_per_grid,
        "net_per_grid": net_per_grid,
        "ratio_gross_to_fees": ratio,
        "grids_to_cycle": grids_to_cycle,
        "alert_ratio_below_2x": alert,
        "bnb_fee_discount": bnb_fee_discount,
        "bnb_balance": bnb_balance,
        "bnb_sufficient": bnb_ok,
        "formulas": {
            "notional_per_level": "capital_usdt / (num_levels / 2)",
            "fees_per_roundtrip": "notional_per_level * fee_rate * 2",
            "gross_per_grid": "notional_per_level * (step_pct / 100)",
            "net_per_grid": "gross_per_grid - fees_per_roundtrip",
            "ratio": "gross_per_grid / fees_per_roundtrip",
            "grids_to_cycle": "ceil(cycle_trigger_usd / net_per_grid)",
        },
    }
