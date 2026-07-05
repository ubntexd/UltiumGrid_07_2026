"""Unit — recentrage idle hors fourchette (section 2bis)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from ultiumgrid.bot_runner import BotRunner
from ultiumgrid.engine.config import StrategyConfig
from ultiumgrid.engine.grid import GridEngine, GridLevel, GridState


def test_idle_recenter_triggers_when_out_of_range_no_fill():
    client = MagicMock()
    client.get_symbol_filters.return_value = MagicMock(
        base_asset="BTC", quote_asset="USDT", min_qty=Decimal("0.00001")
    )
    client.balance_total.return_value = 0.001  # inventaire SELL initial OK
    client._log_attempt = MagicMock()

    session = MagicMock()
    q = MagicMock()
    q.filter.return_value.all.return_value = []
    q.filter.return_value.order_by.return_value.all.return_value = []
    session.query.return_value = q
    cycle_row = MagicMock(id=2, status="open", gross_pnl=0.0)
    session.get.return_value = cycle_row

    cfg = StrategyConfig(idle_recenter_min=0.05)  # 3 secondes
    bot = BotRunner(client, session, cfg)
    bot.running = True
    bot.cycle_id = 2
    bot._last_fill_at = None
    bot._out_of_range_since = datetime.now(timezone.utc) - timedelta(seconds=10)
    bot.engine.state = GridState(
        symbol="BTCUSDT",
        center_price=Decimal("100"),
        active=True,
        levels=[
            GridLevel(0, Decimal("90"), "BUY", Decimal("0.001"), status="open", order_id=1),
            GridLevel(1, Decimal("110"), "SELL", Decimal("0.001"), status="pending"),
        ],
    )
    bot.engine.close_cycle = MagicMock(
        return_value={"grid_profit": 0.0, "floating_profit": 0.0, "gross_pnl": 0.0}
    )
    bot._open_new_cycle = MagicMock()
    bot.bags.bags_qty = MagicMock(return_value=0.0)

    # mark far above range high
    bot._check_idle_recenter(200.0)

    bot.engine.close_cycle.assert_called_once()
    bot._open_new_cycle.assert_called_once()
    assert cycle_row.close_reason == "idle_recenter_no_fill"


def test_idle_recenter_skips_when_in_range_despite_expired_timer():
    """Minuteur expiré mais prix dans la fourchette → pas de recentrage."""
    client = MagicMock()
    session = MagicMock()
    cfg = StrategyConfig(idle_recenter_min=0.05)
    bot = BotRunner(client, session, cfg)
    bot.running = True
    bot.cycle_id = 2
    bot._last_fill_at = None
    bot._out_of_range_since = datetime.now(timezone.utc) - timedelta(minutes=30)
    bot.engine.state = GridState(
        symbol="BTCUSDT",
        center_price=Decimal("100"),
        active=True,
        levels=[
            GridLevel(0, Decimal("90"), "BUY", Decimal("0.001"), status="open", order_id=1),
            GridLevel(1, Decimal("110"), "SELL", Decimal("0.001"), status="open", order_id=2),
        ],
    )
    bot.engine.close_cycle = MagicMock()
    bot._open_new_cycle = MagicMock()
    cycles_before = bot.cycle_id

    # mark inside [90, 110] — timer should reset, no recenter
    bot._check_idle_recenter(100.0)

    bot.engine.close_cycle.assert_not_called()
    bot._open_new_cycle.assert_not_called()
    assert bot.cycle_id == cycles_before
    assert bot._out_of_range_since is None


def test_idle_recenter_skips_when_fill_occurred():
    client = MagicMock()
    client.get_symbol_filters.return_value = MagicMock(
        base_asset="BTC", quote_asset="USDT", min_qty=Decimal("0.00001")
    )
    client.balance_total.return_value = 0.01
    session = MagicMock()
    cfg = StrategyConfig(idle_recenter_min=0.05)
    bot = BotRunner(client, session, cfg)
    bot.running = True
    bot._last_fill_at = datetime.now(timezone.utc)  # fill grille déjà vu
    bot._out_of_range_since = datetime.now(timezone.utc) - timedelta(minutes=30)
    bot.engine.state = GridState(
        symbol="BTCUSDT",
        center_price=Decimal("100"),
        active=True,
        levels=[
            GridLevel(0, Decimal("90"), "BUY", Decimal("0.001"), status="open", order_id=1),
            GridLevel(1, Decimal("110"), "SELL", Decimal("0.001"), status="open", order_id=2),
        ],
    )
    bot.engine.close_cycle = MagicMock()
    bot.bags.bags_qty = MagicMock(return_value=0.0)
    bot._check_idle_recenter(200.0)
    bot.engine.close_cycle.assert_not_called()
