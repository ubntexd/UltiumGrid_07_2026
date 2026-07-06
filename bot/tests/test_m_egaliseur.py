"""Tests Bot Égaliseur — logique sans ordres réels."""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT / "egaliseur"))

from ultium_egaliseur.config import EgaliseurConfig, pct_to_bips  # noqa: E402
from ultium_egaliseur.engine import EgaliseurEngine  # noqa: E402
from ultiumgrid.db.models import Bag, EgaliseurState, make_session_factory  # noqa: E402


def test_no_buy_in_engine_source():
    """Test 5 — aucun chemin BUY dans le module egaliseur."""
    engine_path = ROOT / "egaliseur" / "ultium_egaliseur" / "engine.py"
    tree = ast.parse(engine_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "place_order":
                for kw in node.keywords:
                    if kw.arg == "side" and isinstance(kw.value, ast.Constant):
                        assert kw.value.value != "BUY"
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    assert node.args[1].value != "BUY"
    src = inspect.getsource(EgaliseurEngine)
    assert '"BUY"' not in src
    assert "'BUY'" not in src


def test_test_only_journals_without_order():
    SessionLocal, engine = make_session_factory("sqlite:///:memory:")
    session = SessionLocal()
    bag = Bag(
        symbol="BTCUSDT",
        quantity=0.01,
        entry_price=50000.0,
        status="open",
    )
    session.add(bag)
    session.commit()

    client = MagicMock()
    filters = MagicMock()
    filters.trailing_delta_min_bips = 10
    filters.trailing_delta_max_bips = 2000
    client.get_symbol_filters.return_value = filters
    client.ticker_price.return_value = {"price": "51000"}

    eg = EgaliseurEngine(client, session)
    cfg = EgaliseurConfig(operation_mode="test_only", paused=False)
    session.add(EgaliseurState(key="main", value_json=cfg.to_dict()))
    session.commit()

    summary = eg.tick()
    session.refresh(bag)
    assert bag.status == "journal_only"
    assert client.place_trailing_stop_sell.called is False
    assert summary["processed"] == 1


def test_armed_bag_gets_trailing_in_test_only():
    SessionLocal, _ = make_session_factory("sqlite:///:memory:")
    session = SessionLocal()
    bag = Bag(symbol="BTCUSDT", quantity=0.01, entry_price=50000.0, status="open")
    session.add(bag)
    session.commit()

    client = MagicMock()
    filters = MagicMock()
    filters.trailing_delta_min_bips = 10
    filters.trailing_delta_max_bips = 2000
    client.get_symbol_filters.return_value = filters
    client.ticker_price.return_value = {"price": "51000"}
    client.place_trailing_stop_sell.return_value = {"orderId": 999}
    client.open_orders.return_value = [{"orderId": 999}]

    eg = EgaliseurEngine(client, session)
    cfg = EgaliseurConfig(operation_mode="test_only", test_armed_bag_ids=[bag.id])
    session.add(EgaliseurState(key="main", value_json=cfg.to_dict()))
    session.commit()

    eg.tick()
    session.refresh(bag)
    assert bag.status == "trailing_active"


def test_trailing_activation_sets_fields():
    SessionLocal, _ = make_session_factory("sqlite:///:memory:")
    session = SessionLocal()
    bag = Bag(symbol="BTCUSDT", quantity=0.01, entry_price=50000.0, status="open")
    session.add(bag)
    session.commit()

    client = MagicMock()
    filters = MagicMock()
    filters.trailing_delta_min_bips = 10
    filters.trailing_delta_max_bips = 2000
    client.get_symbol_filters.return_value = filters
    client.ticker_price.return_value = {"price": "51000"}
    client.place_trailing_stop_sell.return_value = {"orderId": 999}
    client.open_orders.return_value = [
        {"orderId": 999, "type": "STOP_LOSS_LIMIT", "trailingDelta": 150}
    ]

    eg = EgaliseurEngine(client, session)
    cfg = EgaliseurConfig(operation_mode="continuous", paused=False)
    session.add(EgaliseurState(key="main", value_json=cfg.to_dict()))
    session.commit()

    eg.tick()
    session.refresh(bag)
    assert bag.status == "trailing_active"
    assert bag.trailing_order_id == "999"
    assert bag.trailing_delta_bips == pct_to_bips(1.5)
    client.place_trailing_stop_sell.assert_called_once()


def test_time_exit_forces_market_sell():
    SessionLocal, _ = make_session_factory("sqlite:///:memory:")
    session = SessionLocal()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    bag = Bag(
        symbol="BTCUSDT",
        quantity=0.01,
        entry_price=50000.0,
        status="trailing_active",
        trailing_order_id="123",
        hard_stop_price=40000.0,
        max_exit_at=past,
    )
    session.add(bag)
    session.commit()

    client = MagicMock()
    client.ticker_price.return_value = {"price": "49000"}
    client.get_order.return_value = {"status": "NEW"}
    client.place_order.return_value = {"orderId": 555, "avgPrice": "49000"}
    client.my_trades.return_value = [
        {"qty": "0.01", "price": "49000", "commission": "0.01", "commissionAsset": "USDT"}
    ]
    client.base_asset_qty.return_value = 0.0

    eg = EgaliseurEngine(client, session)
    cfg = EgaliseurConfig(operation_mode="continuous", paused=False, max_hold_days=0.001)
    session.add(EgaliseurState(key="main", value_json=cfg.to_dict()))
    session.commit()

    eg.tick()
    session.refresh(bag)
    assert bag.status == "sold_forced_time"
    assert bag.sold_by == "bot_egaliseur"
    client.place_order.assert_called()
    args = client.place_order.call_args
    assert args[0][1] == "SELL"


def test_config_validation_bounds():
    cfg = EgaliseurConfig(trailing_delta_pct=0.01)
    errors = cfg.validate(trail_min_bips=10, trail_max_bips=500)
    assert errors
