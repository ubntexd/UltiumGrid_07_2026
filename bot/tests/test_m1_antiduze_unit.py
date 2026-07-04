"""Tests unitaires anti-doublon post -1007 (mocks labellisés unit)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))

from ultiumgrid.connector.binance_futures import BinanceFuturesClient, SymbolFilters  # noqa: E402
from decimal import Decimal


@pytest.mark.unit
def test_1007_duplicate_avoided_does_not_resend():
    client = BinanceFuturesClient(api_key="k", api_secret="s")
    client._hedge_mode = False
    client._filters_cache["BTCUSDT"] = SymbolFilters(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("50"),
        price_precision=1,
        quantity_precision=3,
    )

    existing = {
        "orderId": 42,
        "clientOrderId": "ugFIXEDID",
        "status": "NEW",
        "symbol": "BTCUSDT",
    }
    post_calls = {"n": 0}

    def fake_raw(method, path, params=None, signed=False):
        if method == "POST" and path == "/fapi/v1/order":
            post_calls["n"] += 1
            return 408, {"code": -1007, "msg": "Timeout waiting for response from backend server."}, '{"code":-1007}'
        raise AssertionError(f"unexpected {method} {path}")

    def fake_find(symbol, client_order_id):
        assert client_order_id == "ugFIXEDID"
        return existing, {"source": "openOrders", "open_orders_match": existing}

    client._raw_request = fake_raw  # type: ignore
    client.find_order_by_client_order_id = fake_find  # type: ignore
    client.new_client_order_id = lambda prefix="ug": "ugFIXEDID"  # type: ignore

    result = client.place_order(
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity="0.002",
        price="40000",
    )
    assert result["orderId"] == 42
    assert post_calls["n"] == 1  # un seul POST, pas de renvoi
    assert client.attempt_log[-1]["outcome"] == "duplicate_avoided"
    assert client.attempt_log[-1]["verify_json"]["source"] == "openOrders"


@pytest.mark.unit
def test_1007_not_found_retries_with_new_client_ids():
    client = BinanceFuturesClient(api_key="k", api_secret="s")
    client._hedge_mode = False
    client._filters_cache["BTCUSDT"] = SymbolFilters(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("50"),
        price_precision=1,
        quantity_precision=3,
    )

    ids = iter(["idA", "idB", "idC"])
    client.new_client_order_id = lambda prefix="ug": next(ids)  # type: ignore

    def fake_raw(method, path, params=None, signed=False):
        if method == "POST":
            return 408, {"code": -1007, "msg": "timeout"}, '{"code":-1007}'
        raise AssertionError(path)

    client._raw_request = fake_raw  # type: ignore
    client.find_order_by_client_order_id = lambda s, c: (None, {"source": None, "client_order_id": c})  # type: ignore

    # accélérer backoff
    import ultiumgrid.connector.binance_futures as mod

    original = mod.BACKOFF_BASE_S
    mod.BACKOFF_BASE_S = 0.01
    try:
        with pytest.raises(Exception):
            client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity="0.002",
                price="40000",
                max_attempts=3,
            )
    finally:
        mod.BACKOFF_BASE_S = original

    outcomes = [a["outcome"] for a in client.attempt_log]
    assert outcomes == ["timeout_not_found", "timeout_not_found", "timeout_not_found"]
    ids_used = [a["client_order_id"] for a in client.attempt_log]
    assert ids_used == ["idA", "idB", "idC"]
    assert len(set(ids_used)) == 3


@pytest.mark.unit
def test_1008_on_priority_close_is_anomaly():
    client = BinanceFuturesClient(api_key="k", api_secret="s")
    client._hedge_mode = False
    client._filters_cache["BTCUSDT"] = SymbolFilters(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("50"),
        price_precision=1,
        quantity_precision=3,
    )
    client.new_client_order_id = lambda prefix="ug": "close1"  # type: ignore

    def fake_raw(method, path, params=None, signed=False):
        return 503, {"code": -1008, "msg": "throttled"}, '{"code":-1008}'

    client._raw_request = fake_raw  # type: ignore
    import ultiumgrid.connector.binance_futures as mod

    original = mod.BACKOFF_BASE_S
    mod.BACKOFF_BASE_S = 0.01
    try:
        with pytest.raises(Exception):
            client.place_order(
                symbol="BTCUSDT",
                side="SELL",
                order_type="MARKET",
                quantity="0.002",
                purpose="panic_close",
                reduce_only=True,
                max_attempts=1,
            )
    finally:
        mod.BACKOFF_BASE_S = original

    assert client.attempt_log[-1]["outcome"] == "anomaly_1008_priority"
