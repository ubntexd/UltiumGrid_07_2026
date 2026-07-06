"""Conversion commission exchange → USDT."""

from __future__ import annotations

QUOTE_FEE_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD"})


def commission_to_usdt(
    commission: float,
    commission_asset: str,
    *,
    trade_price: float = 0.0,
    bnb_usdt_price: float | None = None,
) -> float:
    """Convertit une commission myTrades en USDT."""
    if commission <= 0:
        return 0.0
    asset = (commission_asset or "").upper()
    if asset in QUOTE_FEE_ASSETS:
        return commission
    if asset == "BNB":
        if bnb_usdt_price and bnb_usdt_price > 0:
            return commission * bnb_usdt_price
        return 0.0
    if trade_price > 0:
        return commission * trade_price
    return 0.0
