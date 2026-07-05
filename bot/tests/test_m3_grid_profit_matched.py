"""Grid Profit — appariement Binance (paires BUY(i)+SELL(i+1) complètes)."""

from __future__ import annotations

import pytest

from ultiumgrid.engine.grid_profit import (
    MatchedGridLedger,
    compute_grid_profit_from_trades,
    total_matched_trades_from_trades,
)


@pytest.mark.unit
def test_matched_pair_profit_excludes_orphan_buy():
    """2 BUY, 1 SELL apparié, 1 BUY orphelin → seul le round-trip complet compte."""
    ledger = MatchedGridLedger(fee_rate=0.001)
    ledger.on_fill("BUY", 5, 100.0, 1.0)
    ledger.on_fill("SELL", 6, 101.0, 1.0)
    expected = 1.0 * 101.0 * (1 - 0.001) - 1.0 * 100.0 * (1 + 0.001)
    assert ledger.grid_profit == pytest.approx(expected)
    assert len(ledger.matched_roundtrips) == 1
    # BUY orphelin (pas de SELL au-dessus)
    ledger.on_fill("BUY", 7, 99.0, 1.0)
    assert ledger.grid_profit == pytest.approx(expected)
    assert ledger.pending_buy_qty() == pytest.approx(1.0)


@pytest.mark.unit
def test_sell_before_buy_does_not_realize_until_pair_complete():
    """SELL utilisant inventaire initial ne crée pas de profit tant que BUY n'est pas fillé."""
    ledger = MatchedGridLedger(fee_rate=0.001)
    ledger.on_fill("SELL", 10, 63385.0, 0.004)
    assert ledger.grid_profit == 0.0
    ledger.on_fill("BUY", 9, 63227.0, 0.004)
    expected = 0.004 * (63385.0 * 0.999 - 63227.0 * 1.001)
    assert ledger.grid_profit == pytest.approx(expected)


@pytest.mark.unit
def test_compute_from_trades_list():
    trades = [
        {"id": 1, "side": "SELL", "price": 101.0, "quantity": 1.0, "level_index": 6, "created_at": "t1"},
        {"id": 2, "side": "BUY", "price": 100.0, "quantity": 1.0, "level_index": 5, "created_at": "t2"},
    ]
    r = compute_grid_profit_from_trades(trades, fee_rate=0.001)
    assert r["roundtrip_count"] == 1
    assert r["grid_profit"] > 0


@pytest.mark.unit
def test_total_matched_trades_excludes_initial_inventory_sells():
    """Cycle 2 : 3 SELL d'inventaire initial sans BUY grille → 0 matched."""
    trades = [
        {"id": 3, "side": "SELL", "price": 62931.89, "quantity": 0.00397, "level_index": 10, "created_at": "t1"},
        {"id": 4, "side": "SELL", "price": 63089.02, "quantity": 0.00397, "level_index": 11, "created_at": "t2"},
        {"id": 5, "side": "SELL", "price": 63246.16, "quantity": 0.00397, "level_index": 12, "created_at": "t3"},
    ]
    assert total_matched_trades_from_trades(trades) == 0


@pytest.mark.unit
def test_total_matched_trades_counts_roundtrips_not_raw_fills():
    trades = [
        {"id": 1, "side": "BUY", "price": 100.0, "quantity": 1.0, "level_index": 5, "created_at": "t1"},
        {"id": 2, "side": "SELL", "price": 101.0, "quantity": 1.0, "level_index": 6, "created_at": "t2"},
        {"id": 3, "side": "BUY", "price": 99.0, "quantity": 1.0, "level_index": 7, "created_at": "t3"},
    ]
    assert total_matched_trades_from_trades(trades) == 1

    """Simulation : comptabilité entry_avg sur SELL vs inventaire haut → profit négatif (bug)."""
    entry_avg = 63000.0
    grid_profit_wrong = 0.0
    for sell_px in [62752.0, 62910.0, 63068.0]:
        grid_profit_wrong += (sell_px - entry_avg) * 0.004
    assert grid_profit_wrong < 0
    # Appariement correct sur mêmes prix avec buy 158 USD sous chaque sell
    ledger = MatchedGridLedger(fee_rate=0.001)
    pairs = [(62752 - 158, 62752), (62910 - 158, 62910), (63068 - 158, 63068)]
    for buy_px, sell_px in pairs:
        ledger.on_fill("BUY", 5, buy_px, 0.004)
        ledger.on_fill("SELL", 6, sell_px, 0.004)
    assert ledger.grid_profit > 0
