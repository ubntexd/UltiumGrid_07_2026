"""Backend API UltiumGrid — Running, History, PnL, Bags, Config, Market, WS."""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env")

from ultiumgrid.bot_runner import build_client_from_env  # noqa: E402
from ultiumgrid.control import push_command, read_main_state  # noqa: E402
from ultiumgrid.db.models import (  # noqa: E402
    Bag,
    BotState,
    Configuration,
    Cycle,
    FeePaid,
    PnlSnapshot,
    PriceTick,
    Trade,
    make_session_factory,
)
from ultiumgrid.engine.config import StrategyConfig  # noqa: E402
from ultiumgrid.engine.grid_profit import total_matched_trades_from_trades  # noqa: E402
from ultiumgrid.engine.viability import compute_viability  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'ultiumgrid.db'}")
Path(ROOT / "data").mkdir(exist_ok=True)

SessionLocal, engine = make_session_factory(DATABASE_URL)
try:
    client = build_client_from_env()
except Exception as _client_err:
    client = None  # type: ignore
    print(f"WARNING: Spot client not ready: {_client_err}")

_ws_clients: list[WebSocket] = []


def _client():
    global client
    if client is None:
        client = build_client_from_env()
    return client


def get_session():
    return SessionLocal()


def _level_visual_state(lv: dict[str, Any]) -> str:
    """active | inactive — pour rendu graphique (pending sans order_id = inactive)."""
    st = lv.get("status") or ""
    if st in ("grid_level_incomplete", "error"):
        return "inactive"
    if st == "pending" and not lv.get("order_id"):
        return "inactive"
    if st in ("open", "filled") and lv.get("order_id"):
        return "active"
    if st == "pending" and lv.get("order_id"):
        return "active"
    return "inactive"


def _build_grid_recap(
    session,
    cfg: StrategyConfig,
    g: dict[str, Any],
    cycle_id: int | None,
    grid_profit: float,
    floating_profit: float,
    gross_pnl: float,
) -> dict[str, Any] | None:
    """Une ligne récap par cycle actif — données DB + état bot (pas de recalcul parallèle)."""
    if not g.get("active") or not cycle_id:
        return None
    cycle = session.get(Cycle, cycle_id)
    if not cycle or cycle.status != "open":
        return None
    levels = g.get("levels") or []
    all_prices = [float(lv["price"]) for lv in levels if lv.get("price") is not None]
    grid_trade_rows = (
        session.query(Trade)
        .filter(Trade.cycle_id == cycle_id, Trade.level_index.isnot(None))
        .order_by(Trade.created_at.asc(), Trade.id.asc())
        .all()
    )
    grid_trades = [
        {
            "id": t.id,
            "side": t.side,
            "price": t.price,
            "quantity": t.quantity,
            "level_index": t.level_index,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in grid_trade_rows
    ]
    grid_matched = total_matched_trades_from_trades(grid_trades)
    initial_buy_row = (
        session.query(Trade)
        .filter(Trade.cycle_id == cycle_id, Trade.level_index.is_(None))
        .first()
    )
    meta = (
        session.query(BotState)
        .filter(BotState.key == f"cycle_meta_{cycle_id}")
        .first()
    )
    initial_buy_meta = (meta.value_json or {}).get("initial_buy") if meta else None
    from datetime import datetime, timezone

    opened = cycle.opened_at
    if opened and opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    duration_sec = (
        (datetime.now(timezone.utc) - opened).total_seconds() if opened else None
    )
    range_lo = min(all_prices) if all_prices else g.get("range_low")
    range_hi = max(all_prices) if all_prices else g.get("range_high")
    return {
        "pair": cfg.symbol,
        "time_created": opened.isoformat() if opened else None,
        "total_investment": float(cfg.capital_usdt),
        "total_profit": gross_pnl,
        "grid_profit": grid_profit,
        "floating_profit": floating_profit,
        "total_matched_trades": grid_matched,
        "grid_matched_trades": grid_matched,
        "initial_inventory_buy": {
            "order_id": str(initial_buy_meta.get("orderId"))
            if initial_buy_meta and initial_buy_meta.get("orderId")
            else (initial_buy_row.order_id if initial_buy_row else None),
            "recorded_in_trades": initial_buy_row is not None,
            "excluded_from_matched_trades": True,
            "note": (
                "Total Matched Trades = round-trips complets (BUY@i+SELL@i+1) via matched_ledger ; "
                "exclut achat initial et SELL d'inventaire initial sans BUY grille apparié"
            ),
        },
        "price_range_low": range_lo,
        "price_range_high": range_hi,
        "duration_sec": duration_sec,
        "number_of_grids": int(cfg.num_levels),
        "cycle_id": cycle_id,
    }


def build_status() -> dict[str, Any]:
    session = get_session()
    try:
        state = read_main_state(session)
        cfg = StrategyConfig.from_dict(state.get("config") or StrategyConfig().to_dict())
        g = state.get("grid") or {}
        levels = g.get("levels") or []
        incomplete = [lv for lv in levels if lv.get("status") == "grid_level_incomplete"]
        placed_prices = [
            float(lv["price"])
            for lv in levels
            if lv.get("status") not in ("grid_level_incomplete", "error", "pending")
        ]
        c = _client()
        mark = None
        mark_stale = False
        mark_error = None
        mark_source = "rest"
        # Prefer live WS mark from bot (recalculé à chaque bookTicker)
        live_row = session.query(BotState).filter(BotState.key == "live_pnl").first()
        live = (live_row.value_json if live_row else None) or {}
        live_age = None
        if live_row and live_row.updated_at:
            from datetime import datetime, timezone

            updated = live_row.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            live_age = (datetime.now(timezone.utc) - updated).total_seconds()
        if live.get("mark") is not None and live_age is not None and live_age < 3.0:
            mark = float(live["mark"])
            mark_source = "ws"
        else:
            try:
                mark = float(c.ticker_price(cfg.symbol)["price"])
            except Exception as exc:
                mark_error = str(exc)
                mark = c.last_ticker_price(cfg.symbol)
                mark_stale = mark is not None
        account = c.capital_snapshot(cfg.symbol)
        bags = (
            session.query(Bag)
            .filter(Bag.symbol == cfg.symbol, Bag.status == "open")
            .all()
        )
        guards = state.get("guards") or {}
        # Floating TOUJOURS recalculé avec le dernier mark (pas le cache du dernier fill)
        pos = float(live.get("position_qty") if live.get("position_qty") is not None else (g.get("position_qty") or 0))
        entry = float(live.get("entry_avg") if live.get("entry_avg") is not None else (g.get("entry_avg") or 0))
        grid_profit = float(g.get("grid_profit") or 0)
        if mark is not None and pos and entry:
            floating_profit = (mark - entry) * pos
        else:
            floating_profit = 0.0
        gross_pnl = grid_profit + floating_profit
        cycle_id = state.get("cycle_id")
        grid_recap = _build_grid_recap(
            session, cfg, g, cycle_id, grid_profit, floating_profit, gross_pnl
        )
        # openOrders (cache client via account TTL pattern — un seul appel si pas en cache)
        open_orders: list[dict[str, Any]] = []
        open_orders_error = None
        try:
            open_orders = c.open_orders(cfg.symbol)
        except Exception as exc:
            open_orders_error = str(exc)
        levels_exchange = []
        open_by_id = {str(o.get("orderId")): o for o in open_orders}
        for lv in levels:
            oid = lv.get("order_id")
            oid_s = str(oid) if oid is not None else None
            ex = open_by_id.get(oid_s) if oid_s else None
            levels_exchange.append(
                {
                    "index": lv.get("index"),
                    "order_id": oid,
                    "status_db": lv.get("status"),
                    "in_openOrders": ex is not None,
                    "openOrders_status": ex.get("status") if ex else None,
                }
            )
        mismatches = [
            row
            for row in levels_exchange
            if (row["status_db"] == "open" and not row["in_openOrders"])
            or (
                row["status_db"] in ("canceled", "pending", "filled", "grid_level_incomplete")
                and row["in_openOrders"]
            )
        ]
        for o in open_orders:
            if str(o.get("orderId")) not in {str(lv.get("order_id")) for lv in levels if lv.get("order_id")}:
                mismatches.append(
                    {
                        "order_id": o.get("orderId"),
                        "status_db": None,
                        "in_openOrders": True,
                        "openOrders_status": o.get("status"),
                        "issue": "orphan_open_order",
                    }
                )

        return {
            "running": bool(state.get("running")),
            "symbol": cfg.symbol,
            "mark_price": mark,
            "mark_stale": mark_stale,
            "mark_error": mark_error,
            "mark_source": mark_source,
            "grid": {
                "active": g.get("active"),
                "center_price": float(g["center_price"]) if g.get("center_price") else None,
                "range_low": min(placed_prices) if placed_prices else None,
                "range_high": max(placed_prices) if placed_prices else None,
                "position_qty": pos,
                "entry_avg": entry,
                "grid_profit": grid_profit,
                "floating_profit": floating_profit,
                "gross_pnl": gross_pnl,
                "levels": levels,
                "incomplete_levels": incomplete,
                "incomplete_count": len(incomplete),
            },
            "bags": [
                {
                    "id": b.id,
                    "quantity": b.quantity,
                    "entry_price": b.entry_price,
                    "status": b.status,
                    "cut_level": b.cut_level,
                    "realized_pnl": b.realized_pnl,
                }
                for b in bags
            ],
            "capital": account,
            "margin": account,
            "exchange_orders": {
                "openOrders_count": len(open_orders),
                "openOrders_error": open_orders_error,
                "openOrders": [
                    {
                        "orderId": o.get("orderId"),
                        "side": o.get("side"),
                        "price": o.get("price"),
                        "origQty": o.get("origQty"),
                        "status": o.get("status"),
                    }
                    for o in open_orders
                ],
                "levels_vs_openOrders": levels_exchange,
                "mismatches": mismatches,
            },
            "guards": {
                "daily_pnl": guards.get("daily_pnl"),
                "hard_stop": guards.get("hard_stop_triggered") or guards.get("hard_stop"),
                "circuit_breaker": guards.get("circuit_breaker_triggered")
                or guards.get("circuit_breaker"),
                "panic": guards.get("panic"),
            },
            "config": cfg.to_dict(),
            "cycle_id": cycle_id,
            "grid_recap": grid_recap,
        }
    finally:
        session.close()


async def _broadcast_loop() -> None:
    while True:
        try:
            status = build_status()
            dead = []
            for ws in _ws_clients:
                try:
                    await ws.send_json({"type": "status", "data": status})
                except Exception:
                    dead.append(ws)
            for ws in dead:
                if ws in _ws_clients:
                    _ws_clients.remove(ws)
        except Exception:
            pass
        # 5s : évite de saturer le poids Binance (ticker+account+openOrders)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_broadcast_loop())
    yield
    task.cancel()


app = FastAPI(title="UltiumGrid API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConfigUpdate(BaseModel):
    params: dict[str, Any]
    mode: str = Field(description="wait_cycle|close_now|apply")


class SellBagRequest(BaseModel):
    order_type: str = "MARKET"
    limit_price: float | None = None


class SimulateRequest(BaseModel):
    params: dict[str, Any]


@app.get("/health")
def health():
    """Heartbeat agrégé backend + dernier signal bot (bot_state.heartbeat)."""
    from ultiumgrid.db.models import BotState
    from datetime import datetime, timezone

    session = get_session()
    try:
        row = session.query(BotState).filter(BotState.key == "heartbeat").first()
        hb = row.value_json if row else None
        age = None
        if row and row.updated_at:
            updated = row.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
        return {
            "ok": True,
            "service": "backend",
            "bot_heartbeat": hb,
            "bot_heartbeat_age_s": age,
        }
    finally:
        session.close()


@app.get("/api/running")
def running():
    return build_status()


@app.post("/api/start")
def start():
    session = get_session()
    try:
        state = read_main_state(session)
        cfg = StrategyConfig.from_dict(state.get("config") or StrategyConfig().to_dict())
        if cfg.bnb_fee_discount:
            try:
                bnb = _client().balance_free("BNB")
                if bnb <= 0:
                    raise HTTPException(
                        400,
                        detail={
                            "errors": [
                                "bnb_fee_discount activé : solde BNB requis pour démarrer "
                                f"(actuel={bnb}). Approvisionner le compte Spot Demo avant tout nouveau cycle."
                            ]
                        },
                    )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, detail={"errors": [f"impossible de vérifier BNB: {exc}"]})
        already = bool(state.get("running")) and bool((state.get("grid") or {}).get("active"))
        push_command(session, "start")
        return {
            "ok": True,
            "queued": "start",
            "already_running": already,
            "cycle_id": state.get("cycle_id"),
            "message": (
                "Cycle déjà actif — commande ignorée côté moteur (pas de second cycle)"
                if already
                else "Démarrage demandé"
            ),
        }
    finally:
        session.close()


@app.post("/api/stop")
def stop():
    session = get_session()
    try:
        push_command(session, "stop")
        return {
            "ok": True,
            "queued": "stop",
            "message": "Arrêt demandé — annulation des ordres, position conservée",
        }
    finally:
        session.close()


@app.post("/api/panic")
def panic():
    session = get_session()
    try:
        push_command(session, "panic")
        return {
            "ok": True,
            "queued": "panic",
            "message": "Panic close demandé — vente du solde base réel",
        }
    finally:
        session.close()


@app.get("/api/last_command")
def last_command():
    session = get_session()
    try:
        from ultiumgrid.db.models import BotState

        row = session.query(BotState).filter(BotState.key == "last_command").first()
        return row.value_json if row else {"name": None, "result": None}
    finally:
        session.close()


@app.get("/api/history")
def history(symbol: str | None = None):
    session = get_session()
    try:
        q = session.query(Cycle).order_by(Cycle.id.desc())
        if symbol:
            q = q.filter(Cycle.symbol == symbol)
        rows = q.limit(200).all()
        return [
            {
                "id": c.id,
                "symbol": c.symbol,
                "status": c.status,
                "center_price": c.center_price,
                "grid_profit": c.grid_profit,
                "floating_profit": c.floating_profit,
                "funding_pnl": c.funding_pnl,
                "gross_pnl": c.gross_pnl,
                "net_pnl": c.net_pnl,
                "opened_at": c.opened_at.isoformat() if c.opened_at else None,
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                "close_reason": c.close_reason,
            }
            for c in rows
        ]
    finally:
        session.close()


@app.get("/api/pnl")
def pnl_analysis(symbol: str | None = None):
    session = get_session()
    try:
        status = build_status()
        symbol = symbol or status["symbol"]
        cycles = (
            session.query(Cycle)
            .filter(Cycle.symbol == symbol, Cycle.status == "closed")
            .all()
        )
        won = [c for c in cycles if c.net_pnl > 0]
        lost = [c for c in cycles if c.net_pnl <= 0]
        avg_win = sum(c.net_pnl for c in won) / len(won) if won else 0.0
        avg_loss = sum(c.net_pnl for c in lost) / len(lost) if lost else 0.0
        durations = []
        for c in cycles:
            if c.opened_at and c.closed_at:
                durations.append((c.closed_at - c.opened_at).total_seconds())
        avg_dur = sum(durations) / len(durations) if durations else 0.0
        net = sum(c.net_pnl for c in cycles)
        snaps = (
            session.query(PnlSnapshot)
            .filter(PnlSnapshot.symbol == symbol)
            .order_by(PnlSnapshot.ts.asc())
            .limit(2000)
            .all()
        )
        curve = [
            {
                "ts": s.ts.isoformat(),
                "cumulative_pnl": s.cumulative_pnl,
                "grid_pnl": s.grid_pnl,
                "bags_pnl": s.bags_pnl,
                "closed_cycles_pnl": s.closed_cycles_pnl,
            }
            for s in snaps
        ]
        theoretical = 10.0 * len(cycles)
        return {
            "symbol": symbol,
            "cycles_total": len(cycles),
            "cycles_won": len(won),
            "cycles_lost": len(lost),
            "win_rate": (len(won) / len(cycles)) if cycles else 0.0,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_cycle_duration_sec": avg_dur,
            "net_pnl": net,
            "theoretical_pnl": theoretical,
            "curve": curve,
            "formulas": {
                "win_rate": "cycles_won / cycles_total",
                "avg_win": "sum(net_pnl where net_pnl>0) / cycles_won",
                "avg_loss": "sum(net_pnl where net_pnl<=0) / cycles_lost",
                "avg_cycle_duration_sec": "mean(closed_at - opened_at)",
                "net_pnl": "sum(cycle.net_pnl)",
                "theoretical_pnl": "10 * cycles_total",
                "cumulative_pnl": "closed_cycles_pnl + grid_pnl + bags_pnl (snapshot)",
            },
        }
    finally:
        session.close()


@app.get("/api/bags")
def bags():
    return build_status()["bags"]


@app.post("/api/bags/{bag_id}/sell")
def sell_bag(bag_id: int, body: SellBagRequest):
    session = get_session()
    try:
        push_command(
            session,
            "sell_bag",
            {
                "bag_id": bag_id,
                "order_type": body.order_type,
                "limit_price": body.limit_price,
            },
        )
        return {"ok": True, "queued": "sell_bag", "bag_id": bag_id}
    finally:
        session.close()


def _viability_for(cfg: StrategyConfig) -> dict:
    acc = None
    bnb = 0.0
    try:
        c = _client()
        acc = c.account()
        bnb = c.balance_free("BNB")
        # capital ne peut pas dépasser le quote libre
        quote_free = c.quote_asset_free(cfg.symbol)
    except Exception:
        quote_free = None
    viab = compute_viability(
        capital_usdt=cfg.capital_usdt,
        num_levels=cfg.num_levels,
        step_pct=cfg.step_pct,
        cycle_trigger_usd=cfg.cycle_trigger_usd,
        bnb_fee_discount=cfg.bnb_fee_discount,
        account=acc,
        bnb_balance=bnb,
    )
    viab["quote_free"] = quote_free
    return viab


@app.get("/api/config")
def get_config():
    session = get_session()
    try:
        status = build_status()
        cfg = StrategyConfig.from_dict(status["config"])
        return {
            "active": status["config"],
            "bounds": StrategyConfig.BOUNDS,
            "viability": _viability_for(cfg),
            "symbols": _client().list_trading_symbols()[:80],
            "history": [
                {
                    "id": c.id,
                    "symbol": c.symbol,
                    "params": c.params_json,
                    "is_active": c.is_active,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "cycles_won": c.cycles_won,
                    "cycles_lost": c.cycles_lost,
                    "net_pnl": c.net_pnl,
                    "avg_cycle_duration_sec": c.avg_cycle_duration_sec,
                }
                for c in session.query(Configuration).order_by(Configuration.id.desc()).limit(50)
            ],
        }
    finally:
        session.close()


@app.post("/api/config/viability")
def config_viability(body: SimulateRequest):
    status = build_status()
    cfg = StrategyConfig.from_dict({**status["config"], **body.params})
    errors = cfg.validate()
    if errors:
        raise HTTPException(400, detail={"errors": errors})
    return _viability_for(cfg)


@app.post("/api/config")
def update_config(body: ConfigUpdate):
    status = build_status()
    cfg = StrategyConfig.from_dict({**status["config"], **body.params})
    errors = cfg.validate()
    # capital <= quote libre
    try:
        quote_free = _client().quote_asset_free(cfg.symbol)
        if cfg.capital_usdt > quote_free:
            errors.append(f"capital_usdt={cfg.capital_usdt} > quote libre {quote_free}")
    except Exception:
        pass
    # BNB discount nécessite solde BNB
    if cfg.bnb_fee_discount:
        try:
            bnb = _client().balance_free("BNB")
            if bnb <= 0:
                errors.append("bnb_fee_discount activé mais solde BNB = 0")
        except Exception as exc:
            errors.append(f"impossible de vérifier BNB: {exc}")
    if errors:
        raise HTTPException(400, detail={"errors": errors})
    mode = body.mode if body.mode != "apply" else "close_now"
    session = get_session()
    try:
        row = Configuration(symbol=cfg.symbol, params_json=cfg.to_dict(), is_active=False)
        session.add(row)
        session.commit()
        push_command(session, "config", {"params": cfg.to_dict(), "mode": mode})
        return {
            "ok": True,
            "queued": True,
            "mode": mode,
            "config": cfg.to_dict(),
            "viability": _viability_for(cfg),
        }
    finally:
        session.close()


@app.post("/api/config/simulate")
def simulate_config(body: SimulateRequest):
    status = build_status()
    cfg = StrategyConfig.from_dict({**status["config"], **body.params})
    errors = cfg.validate()
    if errors:
        raise HTTPException(400, detail={"errors": errors})
    session = get_session()
    try:
        cycles = (
            session.query(Cycle)
            .filter(Cycle.symbol == cfg.symbol, Cycle.status == "closed")
            .all()
        )
        if len(cycles) < 3:
            return {
                "ok": False,
                "insufficient_data": True,
                "message": f"Données historiques insuffisantes ({len(cycles)} cycles, minimum 3).",
                "simulated_pnl": None,
            }
        simulated = [c.net_pnl for c in cycles if c.gross_pnl >= cfg.cycle_trigger_usd or c.net_pnl != 0]
        return {
            "ok": True,
            "insufficient_data": False,
            "cycles_used": len(simulated),
            "simulated_pnl": sum(simulated),
            "avg_pnl": sum(simulated) / len(simulated) if simulated else 0.0,
            "method": "replay net_pnl des cycles clos filtrés par cycle_trigger_usd",
        }
    finally:
        session.close()


@app.get("/api/market")
def market():
    tickers = _client().ticker_24hr()
    if isinstance(tickers, dict):
        tickers = [tickers]
    out = []
    for t in tickers:
        out.append(
            {
                "symbol": t["symbol"],
                "price": float(t["lastPrice"]),
                "priceChangePercent": float(t["priceChangePercent"]),
                "volume": float(t["volume"]),
                "quoteVolume": float(t.get("quoteVolume", 0)),
            }
        )
    out.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return out[:100]


@app.get("/api/market/{symbol}")
def market_symbol(symbol: str):
    symbol = symbol.upper()
    c = _client()
    filters = c.get_symbol_filters(symbol)
    ticker = c.ticker_price(symbol)
    depth = c.depth(symbol, limit=10)
    kl = c.klines(symbol, interval="1h", limit=24)
    closes = [float(k[4]) for k in kl]
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
    vol = statistics.pstdev(returns) if len(returns) >= 2 else 0.0
    atr = 0.0
    if len(kl) >= 15:
        trs = []
        for i in range(1, 15):
            high = float(kl[-i][2])
            low = float(kl[-i][3])
            prev_close = float(kl[-i - 1][4])
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        atr = sum(trs) / len(trs)
    return {
        "symbol": symbol,
        "price": float(ticker["price"]),
        "filters": {
            "tickSize": str(filters.tick_size),
            "stepSize": str(filters.step_size),
            "minQty": str(filters.min_qty),
            "minNotional": str(filters.min_notional),
            "baseAsset": filters.base_asset,
            "quoteAsset": filters.quote_asset,
        },
        "orderbook": {"bids": depth.get("bids", [])[:5], "asks": depth.get("asks", [])[:5]},
        "volatility_stdev_1h": vol,
        "atr_14_1h": atr,
    }


@app.get("/api/charts/price")
def chart_price(symbol: str | None = None, limit: int = 200):
    """Points de prix réels (price_ticks) — jamais interpolés."""
    session = get_session()
    try:
        status = build_status()
        symbol = symbol or status["symbol"]
        rows = (
            session.query(PriceTick)
            .filter(PriceTick.symbol == symbol)
            .order_by(PriceTick.id.desc())
            .limit(min(limit, 2000))
            .all()
        )
        rows = list(reversed(rows))
        cycle_id = status.get("cycle_id")
        levels = status.get("grid", {}).get("levels") or []
        levels_chart = [
            {
                "index": lv.get("index"),
                "side": lv.get("side"),
                "price": float(lv["price"]) if lv.get("price") is not None else None,
                "quantity": float(lv["quantity"]) if lv.get("quantity") is not None else None,
                "status": lv.get("status"),
                "order_id": lv.get("order_id"),
                "visual": _level_visual_state(lv),
            }
            for lv in levels
            if lv.get("price") is not None
        ]
        fills_chart: list[dict[str, Any]] = []
        if cycle_id:
            trades = (
                session.query(Trade)
                .filter(Trade.cycle_id == cycle_id)
                .order_by(Trade.created_at.asc())
                .all()
            )
            fills_chart = [
                {
                    "id": t.id,
                    "side": t.side,
                    "price": t.price,
                    "quantity": t.quantity,
                    "ts": t.created_at.isoformat() if t.created_at else None,
                    "level_index": t.level_index,
                    "source": "trades",
                }
                for t in trades
            ]
            if not fills_chart:
                cycle_row = session.get(Cycle, cycle_id)
                meta = (
                    session.query(BotState)
                    .filter(BotState.key == f"cycle_meta_{cycle_id}")
                    .first()
                )
                ib = (meta.value_json or {}).get("initial_buy") if meta else None
                if ib and cycle_row:
                    fills_chart.append(
                        {
                            "id": f"initial-{ib.get('orderId')}",
                            "side": "BUY",
                            "price": float(ib.get("avg_price") or 0),
                            "quantity": float(ib.get("executedQty") or 0),
                            "ts": cycle_row.opened_at.isoformat()
                            if cycle_row.opened_at
                            else None,
                            "level_index": None,
                            "source": "initial_inventory_buy",
                        }
                    )
        if len(rows) < 2:
            return {
                "symbol": symbol,
                "insufficient_data": True,
                "message": "données insuffisantes pour l'instant",
                "points": [],
                "range_low": status["grid"].get("range_low"),
                "range_high": status["grid"].get("range_high"),
                "mark": status.get("mark_price"),
                "levels": levels_chart,
                "fills": fills_chart,
            }
        return {
            "symbol": symbol,
            "insufficient_data": False,
            "points": [
                {
                    "id": r.id,
                    "ts": r.ts.isoformat() if r.ts else None,
                    "price": r.price,
                    "range_low": r.range_low,
                    "range_high": r.range_high,
                }
                for r in rows
            ],
            "range_low": status["grid"].get("range_low"),
            "range_high": status["grid"].get("range_high"),
            "mark": status.get("mark_price"),
            "levels": levels_chart,
            "fills": fills_chart,
        }
    finally:
        session.close()


@app.get("/api/charts/pnl")
def chart_pnl(symbol: str | None = None, limit: int = 200):
    session = get_session()
    try:
        status = build_status()
        symbol = symbol or status["symbol"]
        rows = (
            session.query(PnlSnapshot)
            .filter(PnlSnapshot.symbol == symbol)
            .order_by(PnlSnapshot.id.desc())
            .limit(min(limit, 2000))
            .all()
        )
        rows = list(reversed(rows))
        if len(rows) < 2:
            return {
                "symbol": symbol,
                "insufficient_data": True,
                "message": "données insuffisantes pour l'instant",
                "points": [],
                "formula": "cumulative_pnl = closed_cycles_pnl + grid_pnl + bags_pnl",
            }
        return {
            "symbol": symbol,
            "insufficient_data": False,
            "points": [
                {
                    "id": r.id,
                    "ts": r.ts.isoformat() if r.ts else None,
                    "cumulative_pnl": r.cumulative_pnl,
                    "grid_pnl": r.grid_pnl,
                    "bags_pnl": r.bags_pnl,
                    "closed_cycles_pnl": r.closed_cycles_pnl,
                }
                for r in rows
            ],
            "formula": "cumulative_pnl = closed_cycles_pnl + grid_pnl + bags_pnl",
        }
    finally:
        session.close()


@app.get("/api/charts/cycles")
def chart_cycles(symbol: str | None = None, limit: int = 50):
    session = get_session()
    try:
        status = build_status()
        symbol = symbol or status["symbol"]
        rows = (
            session.query(Cycle)
            .filter(Cycle.symbol == symbol, Cycle.status == "closed")
            .order_by(Cycle.id.desc())
            .limit(limit)
            .all()
        )
        rows = list(reversed(rows))
        if not rows:
            return {
                "symbol": symbol,
                "insufficient_data": True,
                "message": "données insuffisantes pour l'instant",
                "bars": [],
            }
        return {
            "symbol": symbol,
            "insufficient_data": False,
            "bars": [
                {
                    "id": c.id,
                    "net_pnl": c.net_pnl,
                    "gross_pnl": c.gross_pnl,
                    "close_reason": c.close_reason,
                    "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                }
                for c in rows
            ],
        }
    finally:
        session.close()


@app.get("/api/supervision")
def supervision_dashboard():
    """Lecture seule des tables superviseur (écritures uniquement par le container supervisor)."""
    from sqlalchemy import text as sql_text

    session = get_session()
    try:
        # tables créées par le superviseur au démarrage
        alerts = []
        metrics = []
        states = {}
        try:
            rows = session.execute(
                sql_text(
                    "SELECT id, severity, kind, message, payload_json, status, created_at, resolved_at "
                    "FROM supervisor_alerts ORDER BY id DESC LIMIT 100"
                )
            ).mappings().all()
            alerts = [dict(r) for r in rows]
            for a in alerts:
                if a.get("created_at"):
                    a["created_at"] = a["created_at"].isoformat()
                if a.get("resolved_at"):
                    a["resolved_at"] = a["resolved_at"].isoformat()
            mrows = session.execute(
                sql_text(
                    "SELECT kind, value, payload_json, created_at FROM supervisor_metrics "
                    "ORDER BY id DESC LIMIT 200"
                )
            ).mappings().all()
            metrics = [dict(r) for r in mrows]
            for m in metrics:
                if m.get("created_at"):
                    m["created_at"] = m["created_at"].isoformat()
            srows = session.execute(
                sql_text("SELECT key, value_json, updated_at FROM supervisor_state")
            ).mappings().all()
            for s in srows:
                states[s["key"]] = {
                    "value": s["value_json"],
                    "updated_at": s["updated_at"].isoformat() if s["updated_at"] else None,
                }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "alerts": [], "metrics": [], "states": {}}
        return {"ok": True, "alerts": alerts, "metrics": metrics, "states": states}
    finally:
        session.close()


@app.get("/api/fees")
def fees(symbol: str | None = None, limit: int = 200):
    """Commissions réelles (myTrades) stockées en fees_paid — pas d'estimation."""
    session = get_session()
    try:
        status = build_status()
        symbol = symbol or status["symbol"]
        rows = (
            session.query(FeePaid)
            .filter(FeePaid.symbol == symbol)
            .order_by(FeePaid.id.desc())
            .limit(min(limit, 1000))
            .all()
        )
        total_usdt = sum(r.commission_usdt for r in rows)
        # frais théoriques par cycle clos (viabilité) vs réels
        cycles = (
            session.query(Cycle)
            .filter(Cycle.symbol == symbol, Cycle.status == "closed")
            .order_by(Cycle.id.desc())
            .limit(20)
            .all()
        )
        by_cycle = []
        for c in cycles:
            real = (
                session.query(FeePaid)
                .filter(FeePaid.cycle_id == c.id)
                .all()
            )
            real_usdt = sum(f.commission_usdt for f in real)
            by_cycle.append(
                {
                    "cycle_id": c.id,
                    "gross_pnl": c.gross_pnl,
                    "net_pnl": c.net_pnl,
                    "fees_real_usdt": real_usdt,
                    "close_reason": c.close_reason,
                }
            )
        # BNB balance
        c = _client()
        try:
            bnb_free = c.balance_free("BNB")
        except Exception as exc:
            bnb_free = None
            bnb_err = str(exc)
        else:
            bnb_err = None
        cfg = status.get("config") or {}
        return {
            "symbol": symbol,
            "bnb_fee_discount_config": bool(cfg.get("bnb_fee_discount")),
            "bnb_free": bnb_free,
            "bnb_error": bnb_err,
            "total_fees_usdt_listed": total_usdt,
            "rows": [
                {
                    "id": r.id,
                    "trade_id": r.trade_id,
                    "order_id": r.order_id,
                    "commission": r.commission,
                    "commission_asset": r.commission_asset,
                    "commission_usdt": r.commission_usdt,
                    "cycle_id": r.cycle_id,
                    "price": r.price,
                    "qty": r.qty,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "by_cycle": by_cycle,
            "note": "commission/commissionAsset issus de GET /api/v3/myTrades uniquement",
        }
    finally:
        session.close()


@app.get("/api/margin")
@app.get("/api/capital")
def capital():
    """Capital disponible Spot (quote free) — pas de marge/levier."""
    c = _client()
    session = get_session()
    try:
        state = read_main_state(session)
        symbol = (state.get("config") or {}).get("symbol") or "BTCUSDT"
    finally:
        session.close()
    return c.capital_snapshot(symbol)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        await ws.send_json({"type": "status", "data": build_status()})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
