"""Tests conversion commission → USDT."""

from ultiumgrid.engine.fees import commission_to_usdt


def test_commission_usdt_quote_assets():
    assert commission_to_usdt(0.5, "USDT") == 0.5
    assert commission_to_usdt(0.5, "USDC") == 0.5


def test_commission_bnb_uses_bnb_price_not_trade_price():
    # Bug corrigé : 0.0003 BNB × 63000 (BTC) ≠ 0.0003 × 600 (BNB)
    fee = commission_to_usdt(0.0003, "BNB", trade_price=63000.0, bnb_usdt_price=600.0)
    assert abs(fee - 0.18) < 1e-9


def test_commission_base_asset_uses_trade_price():
    fee = commission_to_usdt(0.00001, "BTC", trade_price=63000.0)
    assert abs(fee - 0.63) < 1e-9


def test_commission_bnb_without_price_returns_zero():
    assert commission_to_usdt(0.001, "BNB", trade_price=63000.0) == 0.0
