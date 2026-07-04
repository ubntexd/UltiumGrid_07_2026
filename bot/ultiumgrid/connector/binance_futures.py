"""Connecteur Binance USDT-M Futures Testnet.

Tous les filtres (tickSize, stepSize, MIN_NOTIONAL) proviennent de exchangeInfo.
Aucune valeur inventée.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable, Awaitable
from urllib.parse import urlencode

import requests
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

DEFAULT_REST = "https://testnet.binancefuture.com"
DEFAULT_WS = "wss://stream.binancefuture.com"


@dataclass
class SymbolFilters:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int

    def round_price(self, price: Decimal | float | str) -> Decimal:
        p = Decimal(str(price))
        return (p / self.tick_size).to_integral_value(rounding=ROUND_DOWN) * self.tick_size

    def round_qty(self, qty: Decimal | float | str) -> Decimal:
        q = Decimal(str(qty))
        return (q / self.step_size).to_integral_value(rounding=ROUND_DOWN) * self.step_size

    def format_price(self, price: Decimal | float | str) -> str:
        p = self.round_price(price)
        return f"{p:.{self.price_precision}f}"

    def format_qty(self, qty: Decimal | float | str) -> str:
        q = self.round_qty(qty)
        return f"{q:.{self.quantity_precision}f}"


@dataclass
class BinanceFuturesClient:
    api_key: str
    api_secret: str
    rest_base: str = DEFAULT_REST
    ws_base: str = DEFAULT_WS
    timeout: int = 15
    _filters_cache: dict[str, SymbolFilters] = field(default_factory=dict, repr=False)
    _hedge_mode: bool | None = field(default=None, repr=False)
    _session: requests.Session = field(default_factory=requests.Session, repr=False)

    # --- HTTP helpers ---

    def _headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self.api_key}

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        params["signature"] = hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        return params

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        retries: int = 3,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(retries):
            params_use = dict(params or {})
            if signed:
                params_use = self._sign(params_use)
            url = f"{self.rest_base}{path}"
            resp = self._session.request(
                method,
                url,
                params=params_use,
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.ok:
                if resp.text == "" or resp.text == "{}":
                    return {} if resp.text == "{}" else None
                return resp.json()
            # Timeouts / 5xx testnet: retry
            try:
                body = resp.json()
            except Exception:
                body = {}
            retryable = body.get("code") in (-1007, -1008, -1000) or resp.status_code in (408, 502, 503)
            if retryable and attempt < retries - 1:
                logger.warning(
                    "Binance retryable error on %s %s (%s), retry %s",
                    method,
                    path,
                    resp.status_code,
                    attempt + 1,
                )
                time.sleep(1.5 * (attempt + 1))
                last_exc = requests.HTTPError(f"{resp.status_code} {resp.text}", response=resp)
                continue
            logger.error("Binance %s %s -> %s %s", method, path, resp.status_code, resp.text)
            resp.raise_for_status()
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Binance request failed: {method} {path}")

    # --- Public REST ---

    def ping(self) -> dict:
        return self._request("GET", "/fapi/v1/ping")

    def server_time(self) -> int:
        return int(self._request("GET", "/fapi/v1/time")["serverTime"])

    def ticker_price(self, symbol: str) -> dict:
        return self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})

    def premium_index(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/fapi/v1/premiumIndex", params)

    def funding_rate(self, symbol: str) -> dict:
        """Funding rate courant via premiumIndex (lastFundingRate, nextFundingTime)."""
        data = self.premium_index(symbol)
        return {
            "symbol": data["symbol"],
            "markPrice": data["markPrice"],
            "lastFundingRate": data["lastFundingRate"],
            "nextFundingTime": data["nextFundingTime"],
            "time": data["time"],
        }

    def exchange_info(self) -> dict:
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def depth(self, symbol: str, limit: int = 10) -> dict:
        return self._request("GET", "/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def klines(self, symbol: str, interval: str = "1h", limit: int = 24) -> list:
        return self._request(
            "GET",
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    def get_symbol_filters(self, symbol: str, force: bool = False) -> SymbolFilters:
        if not force and symbol in self._filters_cache:
            return self._filters_cache[symbol]
        info = self.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] != symbol:
                continue
            filters = {f["filterType"]: f for f in s["filters"]}
            pf = filters["PRICE_FILTER"]
            ls = filters["LOT_SIZE"]
            mn = filters.get("MIN_NOTIONAL", {})
            min_notional = Decimal(str(mn.get("notional", mn.get("minNotional", "0"))))
            sf = SymbolFilters(
                symbol=symbol,
                tick_size=Decimal(pf["tickSize"]),
                step_size=Decimal(ls["stepSize"]),
                min_qty=Decimal(ls["minQty"]),
                min_notional=min_notional,
                price_precision=int(s["pricePrecision"]),
                quantity_precision=int(s["quantityPrecision"]),
            )
            self._filters_cache[symbol] = sf
            return sf
        raise ValueError(f"Symbol {symbol} not found in exchangeInfo")

    def list_trading_symbols(self) -> list[str]:
        info = self.exchange_info()
        return [s["symbol"] for s in info["symbols"] if s.get("status") == "TRADING"]

    def ticker_24hr(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/fapi/v1/ticker/24hr", params)

    # --- Signed REST ---

    def account(self) -> dict:
        return self._request("GET", "/fapi/v2/account", signed=True)

    def balance(self) -> list:
        return self._request("GET", "/fapi/v2/balance", signed=True)

    def position_risk(self, symbol: str | None = None) -> list:
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/fapi/v2/positionRisk", params, signed=True)

    def open_orders(self, symbol: str | None = None) -> list:
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/fapi/v1/openOrders", params, signed=True)

    def get_order(self, symbol: str, order_id: int) -> dict:
        return self._request(
            "GET",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def is_hedge_mode(self, force: bool = False) -> bool:
        if self._hedge_mode is None or force:
            data = self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
            self._hedge_mode = bool(data.get("dualSidePosition"))
        return bool(self._hedge_mode)

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal | float | str,
        price: Decimal | float | str | None = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        position_side: str | None = None,
    ) -> dict:
        filters = self.get_symbol_filters(symbol)
        qty_str = filters.format_qty(quantity)
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": qty_str,
        }
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            params["price"] = filters.format_price(price)
            params["timeInForce"] = time_in_force
        hedge = self.is_hedge_mode()
        # Hedge mode (dualSidePosition=true) exige positionSide
        if position_side is None and hedge:
            position_side = "LONG" if side.upper() == "BUY" else "SHORT"
        if position_side:
            params["positionSide"] = position_side
        # reduceOnly non utilisé en hedge mode (positionSide gère le sens)
        if reduce_only and not hedge:
            params["reduceOnly"] = "true"
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def cancel_all_orders(self, symbol: str) -> Any:
        return self._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol},
            signed=True,
        )

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        return self._request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
            signed=True,
        )

    def create_listen_key(self) -> str:
        resp = self._session.post(
            f"{self.rest_base}/fapi/v1/listenKey",
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["listenKey"]

    # --- WebSocket mark price ---

    async def stream_mark_price(
        self,
        symbol: str,
        on_price: Callable[[dict], Awaitable[None] | None],
        stop_event: asyncio.Event | None = None,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        """Flux markPrice@1s avec reconnexion automatique."""
        stream = f"{symbol.lower()}@markPrice@1s"
        url = f"{self.ws_base}/ws/{stream}"
        delay = reconnect_delay
        stop_event = stop_event or asyncio.Event()

        while not stop_event.is_set():
            try:
                logger.info("WS connecting %s", url)
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    delay = reconnect_delay
                    logger.info("WS connected %s", symbol)
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            continue
                        data = json.loads(raw)
                        result = on_price(data)
                        if asyncio.iscoroutine(result):
                            await result
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.warning("WS closed: %s — reconnect in %.1fs", exc, delay)
            except Exception as exc:
                logger.warning("WS error: %s — reconnect in %.1fs", exc, delay)
            if stop_event.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_reconnect_delay)
