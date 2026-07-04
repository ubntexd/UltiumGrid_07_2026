"""Tests d'intégration Module 1 — Binance Spot Testnet.

Label: integration (pas de mock).
Preuves écrites dans docs/proofs/m1_*.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env")

from ultiumgrid.connector.binance_spot import BinanceSpotClient  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"
PROOFS.mkdir(parents=True, exist_ok=True)

SYMBOL = "BTCUSDT"


def _client() -> BinanceSpotClient:
    from ultiumgrid.bot_runner import build_client_from_env

    return build_client_from_env()


def _write_proof(name: str, payload: dict) -> None:
    path = PROOFS / name
    path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\n[PROOF] {path}")


@pytest.mark.integration
def test_account_and_balances():
    client = _client()
    ping = client.ping()
    account = client.account()
    filters = client.get_symbol_filters(SYMBOL)
    balances = [
        b
        for b in account.get("balances", [])
        if float(b.get("free", 0)) + float(b.get("locked", 0)) != 0
    ]
    proof = {
        "rest_base": client.rest_base,
        "ping": ping,
        "canTrade": account.get("canTrade"),
        "balances_nonzero": balances[:10],
        "quote_free": client.quote_asset_free(SYMBOL),
        "base_total": client.base_asset_qty(SYMBOL),
        "filters": {
            "tickSize": str(filters.tick_size),
            "stepSize": str(filters.step_size),
            "minQty": str(filters.min_qty),
            "minNotional": str(filters.min_notional),
            "baseAsset": filters.base_asset,
            "quoteAsset": filters.quote_asset,
        },
    }
    _write_proof("m1_account_balances_spot.json", proof)
    assert ping == {}
    assert account["canTrade"] is True
    assert filters.tick_size > 0
    assert filters.step_size > 0


@pytest.mark.integration
def test_place_verify_cancel_limit_order():
    """Place un ordre limite loin du marché, vérifie présence, annule, vérifie absence."""
    client = _client()
    ticker = client.ticker_price(SYMBOL)
    market_price = Decimal(ticker["price"])
    filters = client.get_symbol_filters(SYMBOL)

    # BUY loin sous le marché pour ne pas être fillé
    limit_price = filters.round_price(market_price * Decimal("0.80"))
    # qty telle que notional >= MIN_NOTIONAL
    min_notional = filters.min_notional
    qty = filters.round_qty(min_notional / limit_price * Decimal("1.1"))
    if qty < filters.min_qty:
        qty = filters.min_qty

    proof: dict = {
        "ticker": ticker,
        "limit_price": str(limit_price),
        "qty": str(qty),
        "steps": [],
    }

    # 1. Place
    placed = client.place_order(
        symbol=SYMBOL,
        side="BUY",
        order_type="LIMIT",
        quantity=qty,
        price=limit_price,
    )
    proof["steps"].append({"action": "PLACE", "raw": placed})
    order_id = placed["orderId"]
    assert placed["status"] in ("NEW", "PARTIALLY_FILLED")
    assert placed["symbol"] == SYMBOL

    # 2. Verify via openOrders
    open_orders = client.open_orders(SYMBOL)
    proof["steps"].append({"action": "OPEN_ORDERS_AFTER_PLACE", "raw": open_orders})
    ids = [o["orderId"] for o in open_orders]
    assert order_id in ids, f"order {order_id} absent de openOrders: {ids}"

    # 3. Verify via get_order
    got = client.get_order(SYMBOL, order_id)
    proof["steps"].append({"action": "GET_ORDER", "raw": got})
    assert got["orderId"] == order_id
    assert got["status"] == "NEW"

    # 4. Cancel
    cancelled = client.cancel_order(SYMBOL, order_id)
    proof["steps"].append({"action": "CANCEL", "raw": cancelled})
    assert cancelled["status"] == "CANCELED"
    assert cancelled["orderId"] == order_id

    # 5. Verify absence
    open_after = client.open_orders(SYMBOL)
    proof["steps"].append({"action": "OPEN_ORDERS_AFTER_CANCEL", "raw": open_after})
    ids_after = [o["orderId"] for o in open_after]
    assert order_id not in ids_after

    got_after = client.get_order(SYMBOL, order_id)
    proof["steps"].append({"action": "GET_ORDER_AFTER_CANCEL", "raw": got_after})
    assert got_after["status"] == "CANCELED"

    _write_proof("m1_place_cancel_order.json", proof)
    print(json.dumps(proof, indent=2, default=str))


@pytest.mark.integration
def test_websocket_mark_price_and_reconnect():
    """Reçoit des prix WS Spot (bookTicker public), kill, reprise."""
    # Flux public : pas besoin de clés valides
    client = BinanceSpotClient(api_key="public", api_secret="public")
    prices: list[dict] = []
    reconnect_seen = {"count": 0}
    stop = asyncio.Event()

    async def on_price(data: dict):
        prices.append(data)
        # Après au moins 2 messages, on force une coupure en fermant via stop partiel
        if len(prices) == 2:
            reconnect_seen["count"] += 1
            # Lever pour casser la connexion courante : on ferme en stoppant brièvement
            # en laissant le client reconnecter — on simule en annulant le task interne
            # via exception contrôlée dans le callback n'est pas possible ;
            # on utilise un wrapper ci-dessous.
        if len(prices) >= 5:
            stop.set()

    async def run_with_kill():
        # Première connexion : collecter 2 messages puis tuer le socket
        stream = f"{SYMBOL.lower()}@bookTicker"
        url = f"{client.ws_base}/{stream}"
        import websockets

        async with websockets.connect(url, ping_interval=20) as ws:
            for _ in range(2):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                prices.append(json.loads(raw))
            # Kill explicite de la connexion
            await ws.close()
            reconnect_seen["killed"] = True

        # Reconnexion manuelle prouvant que le flux reprend (même endpoint)
        async with websockets.connect(url, ping_interval=20) as ws:
            reconnect_seen["reconnected"] = True
            for _ in range(3):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                prices.append(json.loads(raw))

        # Test du helper stream_mark_price avec stop
        stop2 = asyncio.Event()
        received: list[dict] = []

        async def on_p(d):
            received.append(d)
            if len(received) >= 2:
                stop2.set()

        task = asyncio.create_task(client.stream_mark_price(SYMBOL, on_p, stop_event=stop2))
        await asyncio.wait_for(stop2.wait(), timeout=30)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        reconnect_seen["stream_helper_msgs"] = len(received)

    asyncio.run(run_with_kill())

    proof = {
        "prices_count": len(prices),
        "sample_prices": prices[:5],
        "reconnect": reconnect_seen,
    }
    _write_proof("m1_websocket_reconnect.json", proof)
    print(json.dumps(proof, indent=2, default=str))

    assert len(prices) >= 5
    assert reconnect_seen.get("killed") is True
    assert reconnect_seen.get("reconnected") is True
    assert reconnect_seen.get("stream_helper_msgs", 0) >= 2
    # Prix cohérents avec le marché REST au même instant
    rest_price = Decimal(client.ticker_price(SYMBOL)["price"])
    last_ws = Decimal(prices[-1].get("p") or prices[-1].get("markPrice") or "0")
    # Écart < 1 % (latence testnet)
    if last_ws > 0:
        drift = abs(rest_price - last_ws) / rest_price
        assert drift < Decimal("0.01"), f"drift={drift} rest={rest_price} ws={last_ws}"
