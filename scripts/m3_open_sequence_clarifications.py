#!/usr/bin/env python3
"""Clarifications Module 3 — grille 4 vs 20, T2 prix/range, T3 WS réel.

Génère docs/proofs/m3_open_sequence_clarifications.json
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path("/tmp")
# Chemin projet (hôte) ou /app (container Docker)
PROJECT_ROOT = Path("/app") if Path("/app/bot").exists() else Path(__file__).resolve().parents[1]

import sys

if str(PROJECT_ROOT / "bot") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "bot"))

_env = PROJECT_ROOT / ".env"
if _env.exists():
    load_dotenv(_env, override=True)

from ultiumgrid.bot_runner import BotRunner, build_client_from_env  # noqa: E402
from ultiumgrid.db.models import Cycle, FeePaid, make_session_factory  # noqa: E402
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402


async def fetch_ws_mark(symbol: str, timeout: float = 5.0) -> dict:
    """Un tick bookTicker réel (mid bid/ask)."""
    import websockets

    stream = f"{symbol.lower()}@bookTicker"
    url = f"wss://demo-stream.binance.com/ws/{stream}"
    t0 = datetime.now(timezone.utc)
    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    data = json.loads(raw)
    bid = Decimal(data.get("b") or data.get("bidPrice") or "0")
    ask = Decimal(data.get("a") or data.get("askPrice") or "0")
    mid = float((bid + ask) / 2) if bid and ask else 0.0
    return {
        "ws_url": url,
        "bid": str(bid),
        "ask": str(ask),
        "mid": mid,
        "event_time": data.get("E"),
        "captured_at_utc": t0.isoformat(),
        "raw": data,
    }


def flatten_all_base(client, symbol: str = "BTCUSDT") -> None:
    """Vend toute la base libre (plusieurs passes après déblocage ordres)."""
    try:
        client.cancel_all_orders(symbol)
    except Exception:
        pass
    time.sleep(0.5)
    f = client.get_symbol_filters(symbol, force=True)
    for _ in range(5):
        free = Decimal(str(client.balance_free(f.base_asset, force=True)))
        sq = (free / f.step_size).to_integral_value(rounding=ROUND_DOWN) * f.step_size
        if sq < f.min_qty:
            break
        px = Decimal(client.ticker_price(symbol, force=True)["price"])
        if sq * px < f.min_notional:
            break
        try:
            client.place_order(
                symbol=symbol,
                side="SELL",
                order_type="MARKET",
                quantity=sq,
                purpose="test_cleanup",
            )
            time.sleep(0.8)
        except Exception:
            break


def cleanup(client, session) -> None:
    try:
        client.cancel_all_orders("BTCUSDT")
    except Exception:
        pass
    for c in session.query(Cycle).filter(Cycle.status == "open").all():
        c.status = "closed"
        c.close_reason = "test_cleanup"
        c.closed_at = datetime.now(timezone.utc)
    session.commit()
    time.sleep(0.5)
    flatten_all_base(client)


def run_t1_production(client, session) -> dict:
    """T1 avec grille production 20 niveaux."""
    cfg = StrategyConfig(
        symbol="BTCUSDT",
        capital_usdt=500.0,
        num_levels=20,
        step_pct=0.25,
        idle_recenter_min=20.0,
        stuck_sell_min=15.0,
    )
    bot = BotRunner(client, session, cfg)
    bot._session_factory = lambda: session  # type: ignore[assignment]
    try:
        r = bot.start()
    except RuntimeError as exc:
        if "inventaire insuffisant" in str(exc):
            flatten_all_base(client)
            time.sleep(1.0)
            bot = BotRunner(client, session, cfg)
            bot._session_factory = lambda: session  # type: ignore[assignment]
            r = bot.start()
        else:
            raise
    levels = bot.engine.levels_as_dict()
    ib = bot.engine.state.initial_buy
    oo = client.open_orders("BTCUSDT", force=True)
    buys = [l for l in levels if l["side"] == "BUY"]
    sells = [l for l in levels if l["side"] == "SELL"]
    fees = (
        session.query(FeePaid).filter(FeePaid.order_id == str(ib["orderId"])).count()
        if ib
        else 0
    )
    incomplete = [l for l in levels if l["status"] == "grid_level_incomplete"]
    t1 = {
        "config": cfg.to_dict(),
        "config_label": "production_20_levels",
        "start": r,
        "initial_buy": ib,
        "num_levels": len(levels),
        "buys_open": len([l for l in buys if l.get("order_id") and l["status"] == "open"]),
        "sells_open": len([l for l in sells if l.get("order_id") and l["status"] == "open"]),
        "buys_n": len(buys),
        "sells_n": len(sells),
        "incomplete_n": len(incomplete),
        "binance_buys": len([o for o in oo if o["side"] == "BUY"]),
        "binance_sells": len([o for o in oo if o["side"] == "SELL"]),
        "fees": fees,
        "cycles_open": session.query(Cycle).filter(Cycle.status == "open").count(),
    }
    t1["conforme"] = bool(
        ib
        and t1["num_levels"] == 20
        and t1["sells_open"] == t1["sells_n"]
        and t1["buys_open"] == t1["buys_n"]
        and t1["binance_sells"] >= t1["sells_n"]
        and t1["binance_buys"] >= t1["buys_n"]
        and t1["cycles_open"] == 1
        and t1["fees"] >= 1
    )
    bot.stop()
    return t1, bot


def main() -> None:
    client = build_client_from_env()
    SessionLocal, _ = make_session_factory(os.environ["DATABASE_URL"])
    session = SessionLocal()

    proof: dict = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "prior_proof": "docs/proofs/m3_open_sequence.json",
        "prior_config": {
            "num_levels": 4,
            "capital_usdt": 100.0,
            "step_pct": 0.3,
            "note": "T1–T4 originaux utilisaient une mini-grille 4 paliers pour vitesse",
        },
        "tests": {},
    }

    cleanup(client, session)

    # --- T1 production 20 niveaux ---
    print("T1 prod 20 levels...")
    t1_prod, _ = run_t1_production(client, session)
    proof["tests"]["t1_production_20_levels"] = t1_prod
    print("  conforme:", t1_prod["conforme"], "levels", t1_prod["num_levels"])

    cleanup(client, session)

    # --- Setup mini-grille pour T2/T3/T2neg ---
    cfg_mini = StrategyConfig(
        symbol="BTCUSDT",
        capital_usdt=100.0,
        num_levels=4,
        step_pct=0.3,
        idle_recenter_min=0.05,
        stuck_sell_min=0.05,
    )
    bot = BotRunner(client, session, cfg_mini)
    bot._session_factory = SessionLocal
    bot.start()

    # --- T2 avec capture prix vs range ---
    low, high = bot._grid_price_bounds()
    mark_out = high * 1.05
    old_id = bot.cycle_id
    bot._last_fill_at = None
    bot._out_of_range_since = datetime.now(timezone.utc) - timedelta(seconds=10)
    bot._check_idle_recenter(mark_out)
    t2 = {
        "config": cfg_mini.to_dict(),
        "trigger": {
            "mark_at_trigger": mark_out,
            "range_low": low,
            "range_high": high,
            "price_out_of_range": mark_out < low or mark_out > high,
            "out_direction": "above_high" if mark_out > high else ("below_low" if mark_out < low else "in_range"),
            "timer_elapsed_sec": 10,
            "idle_recenter_min": cfg_mini.idle_recenter_min,
        },
        "old_cycle": old_id,
        "new_cycle": bot.cycle_id,
        "new_initial_buy": bot.engine.state.initial_buy,
        "idle_closed": session.query(Cycle).filter(Cycle.close_reason == "idle_recenter_no_fill").count(),
    }
    # Preuve DB order_attempts
    row = session.execute(
        text(
            """
            SELECT verify_json FROM order_attempts
            WHERE outcome = 'idle_recenter_no_fill'
            ORDER BY created_at DESC LIMIT 1
            """
        )
    ).fetchone()
    if row and row[0]:
        t2["db_verify_json"] = row[0]
    t2["conforme"] = bool(
        t2["trigger"]["price_out_of_range"]
        and t2["new_cycle"] != t2["old_cycle"]
        and t2["new_initial_buy"]
        and t2["idle_closed"] >= 1
    )
    proof["tests"]["t2_idle_recenter_price_condition"] = t2
    print("T2 price OOR:", t2["trigger"]["price_out_of_range"], "conforme:", t2["conforme"])

    # --- T2 négatif : timer expiré, prix DANS la fourchette ---
    bot.stop()
    cleanup(client, session)
    time.sleep(1.0)
    bot = BotRunner(client, session, cfg_mini)
    bot._session_factory = SessionLocal
    bot.start()
    low2, high2 = bot._grid_price_bounds()
    mark_in = (low2 + high2) / 2.0
    cycle_before = bot.cycle_id
    bot._last_fill_at = None
    bot._out_of_range_since = datetime.now(timezone.utc) - timedelta(minutes=30)
    bot._check_idle_recenter(mark_in)
    t2neg = {
        "trigger": {
            "mark_at_trigger": mark_in,
            "range_low": low2,
            "range_high": high2,
            "price_out_of_range": mark_in < low2 or mark_in > high2,
            "timer_elapsed_min": 30,
            "idle_recenter_min": cfg_mini.idle_recenter_min,
        },
        "cycle_before": cycle_before,
        "cycle_after": bot.cycle_id,
        "out_of_range_since_after": bot._out_of_range_since,
        "recentered": bot.cycle_id != cycle_before,
    }
    t2neg["conforme"] = bool(
        not t2neg["trigger"]["price_out_of_range"]
        and not t2neg["recentered"]
        and t2neg["out_of_range_since_after"] is None
    )
    proof["tests"]["t2_negative_in_range_no_recenter"] = t2neg
    print("T2neg no recenter:", t2neg["conforme"])

    # --- T3 stuck sell : WS réel au moment T + mécanisme forcé (pas fill naturel) ---
    cleanup(client, session)
    time.sleep(1.0)
    cfg_t3 = StrategyConfig(
        symbol="BTCUSDT",
        capital_usdt=100.0,
        num_levels=4,
        step_pct=0.3,
        idle_recenter_min=0.05,
        stuck_sell_min=0.05,
    )
    bot = BotRunner(client, session, cfg_t3)
    bot._session_factory = SessionLocal
    t3_started = datetime.now(timezone.utc)
    bot.start()

    sell_lv = next(
        lv for lv in bot.engine.state.levels if lv.side == "SELL" and lv.status == "open" and lv.order_id
    )
    ws_snap = asyncio.run(fetch_ws_mark("BTCUSDT"))
    ws_mark = ws_snap["mid"]
    sell_px = float(sell_lv.price)
    # mark passé au mécanisme : légèrement au-dessus du SELL (comme tick WS au-dessus du palier)
    mark_for_check = sell_px * 1.01

    bot._stuck_sell_since[sell_lv.index] = datetime.now(timezone.utc) - timedelta(seconds=10)
    oid_before = sell_lv.order_id
    qty_before = float(sell_lv.quantity)
    bot._check_stuck_sells(mark_for_check)
    time.sleep(1.5)

    lv_after = next(lv for lv in bot.engine.state.levels if lv.index == sell_lv.index)
    stuck_row = session.execute(
        text(
            """
            SELECT verify_json, response_json, outcome, created_at
            FROM order_attempts
            WHERE outcome = 'forced_sell_stuck_level'
              AND created_at >= :since
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"since": t3_started},
    ).fetchone()
    verify = stuck_row[0] if stuck_row else None
    response = stuck_row[1] if stuck_row else None

    t3 = {
        "ws_snapshot_at_trigger": ws_snap,
        "ws_mark_mid": ws_mark,
        "sell_level": {
            "index": sell_lv.index,
            "price": sell_px,
            "order_id_before": oid_before,
            "qty": qty_before,
        },
        "comparison_ws_vs_sell": {
            "ws_mark": ws_mark,
            "sell_price": sell_px,
            "ws_gte_sell": ws_mark + 1e-12 >= sell_px,
            "delta_usd": round(ws_mark - sell_px, 4),
        },
        "mark_passed_to_check_stuck_sells": mark_for_check,
        "mark_gte_sell": mark_for_check + 1e-12 >= sell_px,
        "natural_limit_fill_at_ws_price": ws_mark + 1e-12 >= sell_px,
        "interpretation": (
            "WS réel < sell → le marché n'a pas atteint le palier SELL au moment T; "
            "le mécanisme utilise mark>=sell (ici sell*1.01) pour détecter un SELL bloqué; "
            "la clôture est un MARKET SELL forcé (forced_sell_stuck_level), pas un fill limite."
            if ws_mark + 1e-12 < sell_px
            else "WS >= sell au moment T — vérifier order type MARKET dans order_attempts"
        ),
        "status_after": lv_after.status,
        "order_attempt_at": str(stuck_row[3]) if stuck_row else None,
        "order_attempt_verify_json": verify,
        "order_attempt_response_json": response,
        "fill_type": (
            response.get("type") if isinstance(response, dict) else None
        ),
        "natural_limit_fill_ruled_out": bool(
            verify
            and isinstance(response, dict)
            and response.get("type") == "MARKET"
            and stuck_row
            and stuck_row[2] == "forced_sell_stuck_level"
        ),
    }
    t3["conforme"] = bool(
        t3["mark_gte_sell"]
        and lv_after.status == "filled"
        and verify is not None
        and float(verify.get("mark", 0)) + 1e-12 >= float(verify.get("sell_price", 0))
        and t3["natural_limit_fill_ruled_out"]
    )
    proof["tests"]["t3_stuck_sell_ws_proof"] = t3
    print(
        "T3 conforme:",
        t3["conforme"],
        "ws>=sell:",
        t3["comparison_ws_vs_sell"]["ws_gte_sell"],
        "fill:",
        t3["fill_type"],
    )

    bot.stop()
    cleanup(client, session)

    proof["all_conforme"] = all(t.get("conforme") for t in proof["tests"].values())
    out_candidates = [
        Path("/proofs/m3_open_sequence_clarifications.json"),
        ROOT / "m3_open_sequence_clarifications.json",
    ]
    out = out_candidates[0] if out_candidates[0].parent.exists() else out_candidates[1]
    out.write_text(json.dumps(proof, indent=2, default=str))
    print("WROTE", out)
    print("ALL", proof["all_conforme"])
    session.close()


if __name__ == "__main__":
    main()
