"""Tests unitaires Module 3 — logique pure (pas d'API)."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from ultiumgrid.connector.binance_spot import SymbolFilters  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.grid import compute_levels, qty_per_level  # noqa: E402


def _filters() -> SymbolFilters:
    return SymbolFilters(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.0001"),
        min_qty=Decimal("0.0001"),
        min_notional=Decimal("50"),
        price_precision=2,
        quantity_precision=4,
    )


def test_compute_20_levels_arithmetic():
    cfg = StrategyConfig(num_levels=20, step_pct=0.25)
    filters = _filters()
    center = Decimal("62500.00")
    qty = qty_per_level(cfg, center, filters)
    levels = compute_levels(center, cfg, filters, qty)
    assert len(levels) == 20
    buys = [lv for lv in levels if lv.side == "BUY"]
    sells = [lv for lv in levels if lv.side == "SELL"]
    assert len(buys) == 10
    assert len(sells) == 10
    # Ordre croissant des prix
    prices = [lv.price for lv in levels]
    assert prices == sorted(prices)
    # Pas approximatif 0.25%
    step = (prices[1] - prices[0]) / prices[0]
    assert abs(step - Decimal("0.0025")) < Decimal("0.0005")


def test_config_bounds_reject():
    cfg = StrategyConfig(step_pct=5.0, capital_usdt=1.0)
    errors = cfg.validate()
    assert any("step_pct" in e for e in errors)
    assert any("capital_usdt" in e for e in errors)
