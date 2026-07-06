"""Connecteur Binance Spot Testnet (pur, sans levier ni marge).

Base REST : https://testnet.binance.vision
Base WS   : wss://testnet.binance.vision/ws

Anti-doublon post -1007, retry_exhausted, journal order_attempts : inchangés
dans le principe, endpoints /api/v3/*.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable, Awaitable
from urllib.parse import urlencode

import requests
import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

# Spot Demo Mode (clés demo.binance.com) — prouvé place/cancel 2026-07-04
# Docs: https://developers.binance.com/docs/binance-spot-api-docs/demo-mode/general-info
# Note: testnet.binance.vision rejette ces clés (-2015) ; ce n'est PAS le même environnement.
DEFAULT_REST = "https://demo-api.binance.com"
DEFAULT_WS = "wss://demo-stream.binance.com/ws"

BACKOFF_BASE_S = 0.2
MAX_ORDER_ATTEMPTS = 5

PRIORITY_CLOSE_PURPOSES = frozenset(
    {
        "panic_close",
        "risk_cut",
        "hard_stop",
        "cycle_close",
        "bag_sell",
        "egaliseur_forced_stop",
        "egaliseur_forced_time",
        "egaliseur_trailing_fill",
    }
)


class RetryExhaustedError(Exception):
    def __init__(
        self,
        *,
        symbol: str,
        side: str,
        price: str | None,
        quantity: str | None,
        grid_level: int | None,
        used_client_ids: list[str],
        last_error: Exception | None,
    ):
        self.symbol = symbol
        self.side = side
        self.price = price
        self.quantity = quantity
        self.grid_level = grid_level
        self.used_client_ids = used_client_ids
        self.last_error = last_error
        super().__init__(
            f"retry_exhausted symbol={symbol} level={grid_level} "
            f"price={price} qty={quantity} attempts={len(used_client_ids)}"
        )


@dataclass
class SymbolFilters:
    symbol: str
    base_asset: str
    quote_asset: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int
    trailing_delta_min_bips: int = 10
    trailing_delta_max_bips: int = 2000

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


def _env_url(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip().rstrip("/")


@dataclass
class BinanceSpotClient:
    api_key: str
    api_secret: str
    rest_base: str = field(default_factory=lambda: _env_url("BINANCE_SPOT_REST_BASE", DEFAULT_REST))
    ws_base: str = field(default_factory=lambda: _env_url("BINANCE_SPOT_WS_BASE", DEFAULT_WS))
    timeout: int = 15
    account_cache_ttl_s: float = 8.0
    ticker_cache_ttl_s: float = 2.0
    _filters_cache: dict[str, SymbolFilters] = field(default_factory=dict, repr=False)
    _session: requests.Session = field(default_factory=requests.Session, repr=False)
    attempt_log: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _order_log_callback: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    _account_cache: tuple[float, dict] | None = field(default=None, repr=False)
    _ticker_cache: dict[str, tuple[float, dict]] = field(default_factory=dict, repr=False)
    _open_orders_cache: dict[str, tuple[float, list]] = field(default_factory=dict, repr=False)
    _last_ticker: dict[str, float] = field(default_factory=dict, repr=False)
    _last_capital: dict[str, dict] = field(default_factory=dict, repr=False)
    _ban_until_ms: float = field(default=0.0, repr=False)

    def set_order_log_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._order_log_callback = callback

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

    def _raw_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> tuple[int, Any, str]:
        params_use = dict(params or {})
        if signed:
            params_use = self._sign(params_use)
        url = f"{self.rest_base}{path}"
        method_u = method.upper()
        headers = self._headers()
        if method_u in ("POST", "PUT", "DELETE") and signed:
            headers = {
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ultiumgrid-spot/0.2.0",
            }
            resp = self._session.request(
                method_u, url, data=params_use, headers=headers, timeout=self.timeout
            )
        else:
            resp = self._session.request(
                method_u, url, params=params_use, headers=headers, timeout=self.timeout
            )
        raw = resp.text
        body: Any = None
        if raw:
            try:
                body = resp.json()
            except Exception:
                body = None
        return resp.status_code, body, raw

    def _raise_if_banned(self) -> None:
        now_ms = time.time() * 1000
        if self._ban_until_ms and now_ms < self._ban_until_ms:
            raise requests.HTTPError(
                f"418 {{\"code\":-1003,\"msg\":\"IP banned until {int(self._ban_until_ms)} (local backoff)\"}}"
            )

    def _note_ban_from_body(self, body: Any, raw: str) -> None:
        if not isinstance(body, dict) or body.get("code") != -1003:
            return
        msg = str(body.get("msg") or "")
        # "IP banned until 1783173740800"
        for token in msg.replace(".", " ").split():
            if token.isdigit() and len(token) >= 12:
                self._ban_until_ms = float(token)
                return
        # fallback: 60s
        self._ban_until_ms = time.time() * 1000 + 60_000

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        retries: int = 3,
    ) -> Any:
        self._raise_if_banned()
        last_exc: Exception | None = None
        for attempt in range(retries):
            status, body, raw = self._raw_request(method, path, params, signed=signed)
            if 200 <= status < 300:
                if raw == "" or raw == "{}":
                    return {} if raw == "{}" else None
                return body
            code = body.get("code") if isinstance(body, dict) else None
            if status == 418 or code == -1003:
                self._note_ban_from_body(body, raw)
                logger.error("Binance Spot %s %s -> %s %s", method, path, status, raw)
                raise requests.HTTPError(f"{status} {raw}")
            retryable = status in (502, 503) or code in (-1000, -1008)
            if retryable and attempt < retries - 1:
                delay = BACKOFF_BASE_S * (2**attempt)
                time.sleep(delay)
                last_exc = requests.HTTPError(f"{status} {raw}")
                continue
            logger.error("Binance Spot %s %s -> %s %s", method, path, status, raw)
            raise requests.HTTPError(f"{status} {raw}")
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Binance Spot request failed: {method} {path}")

    def _log_attempt(self, entry: dict[str, Any]) -> None:
        self.attempt_log.append(entry)
        logger.info(
            "order_attempt outcome=%s clientOrderId=%s attempt=%s",
            entry.get("outcome"),
            entry.get("client_order_id"),
            entry.get("attempt_no"),
        )
        if self._order_log_callback:
            try:
                self._order_log_callback(entry)
            except Exception:
                logger.exception("order_log_callback failed")

    @staticmethod
    def new_client_order_id(prefix: str = "ug") -> str:
        return f"{prefix}{uuid.uuid4().hex}"[:36]

    # --- Public ---

    def ping(self) -> dict:
        return self._request("GET", "/api/v3/ping")

    def server_time(self) -> int:
        return int(self._request("GET", "/api/v3/time")["serverTime"])

    def ticker_price(self, symbol: str, *, force: bool = False) -> dict:
        now = time.time()
        cached = self._ticker_cache.get(symbol)
        if not force and cached and now - cached[0] < self.ticker_cache_ttl_s:
            return cached[1]
        body = self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        self._ticker_cache[symbol] = (now, body)
        try:
            self._last_ticker[symbol] = float(body["price"])
        except (KeyError, TypeError, ValueError):
            pass
        return body

    def last_ticker_price(self, symbol: str) -> float | None:
        return self._last_ticker.get(symbol)

    def ticker_24hr(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/api/v3/ticker/24hr", params)

    def depth(self, symbol: str, limit: int = 10) -> dict:
        return self._request("GET", "/api/v3/depth", {"symbol": symbol, "limit": limit})

    def klines(self, symbol: str, interval: str = "1h", limit: int = 24) -> list:
        return self._request(
            "GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit}
        )

    def exchange_info(self, symbol: str | None = None) -> dict:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/api/v3/exchangeInfo", params)

    def get_symbol_filters(self, symbol: str, force: bool = False) -> SymbolFilters:
        if not force and symbol in self._filters_cache:
            return self._filters_cache[symbol]
        info = self.exchange_info(symbol)
        symbols = info.get("symbols") or []
        for s in symbols:
            if s["symbol"] != symbol:
                continue
            filters = {f["filterType"]: f for f in s["filters"]}
            pf = filters["PRICE_FILTER"]
            ls = filters["LOT_SIZE"]
            mn = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
            min_notional = Decimal(str(mn.get("minNotional") or mn.get("notional") or "0"))
            td = filters.get("TRAILING_DELTA") or {}
            trail_min = int(
                td.get("minTrailingAboveDelta")
                or td.get("minTrailingBelowDelta")
                or 10
            )
            trail_max = int(
                td.get("maxTrailingAboveDelta")
                or td.get("maxTrailingBelowDelta")
                or 2000
            )
            # precision from tick/step
            tick = Decimal(pf["tickSize"])
            step = Decimal(ls["stepSize"])
            price_precision = max(0, -tick.as_tuple().exponent)
            quantity_precision = max(0, -step.as_tuple().exponent)
            sf = SymbolFilters(
                symbol=symbol,
                base_asset=s["baseAsset"],
                quote_asset=s["quoteAsset"],
                tick_size=tick,
                step_size=step,
                min_qty=Decimal(ls["minQty"]),
                min_notional=min_notional,
                price_precision=price_precision,
                quantity_precision=quantity_precision,
                trailing_delta_min_bips=trail_min,
                trailing_delta_max_bips=trail_max,
            )
            self._filters_cache[symbol] = sf
            return sf
        raise ValueError(f"Symbol {symbol} not found in exchangeInfo")

    def list_trading_symbols(self) -> list[str]:
        info = self.exchange_info()
        return [
            s["symbol"]
            for s in info["symbols"]
            if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
        ]

    # --- Signed ---

    def account(self, *, force: bool = False) -> dict:
        """GET /api/v3/account — mis en cache pour éviter le ban poids (weight 20)."""
        now = time.time()
        if (
            not force
            and self._account_cache is not None
            and now - self._account_cache[0] < self.account_cache_ttl_s
        ):
            return self._account_cache[1]
        body = self._request("GET", "/api/v3/account", signed=True)
        self._account_cache = (now, body)
        return body

    def balances(self, *, force: bool = False) -> list[dict]:
        return self.account(force=force).get("balances") or []

    def balance_free(self, asset: str, *, force: bool = False) -> float:
        for b in self.balances(force=force):
            if b.get("asset") == asset:
                return float(b.get("free") or 0)
        return 0.0

    def balance_total(self, asset: str, *, force: bool = False) -> float:
        for b in self.balances(force=force):
            if b.get("asset") == asset:
                return float(b.get("free") or 0) + float(b.get("locked") or 0)
        return 0.0

    def capital_snapshot(self, symbol: str, *, force: bool = False) -> dict[str, Any]:
        """Un seul GET /api/v3/account pour quote/base/canTrade (+ cache TTL)."""
        filters = self.get_symbol_filters(symbol)
        try:
            acc = self.account(force=force)
            snap = {
                "quote_free": self.balance_free(filters.quote_asset),
                "base_total": self.balance_total(filters.base_asset),
                "base_free": self.balance_free(filters.base_asset),
                "quote_asset": filters.quote_asset,
                "base_asset": filters.base_asset,
                "canTrade": acc.get("canTrade"),
                "availableBalance": self.balance_free(filters.quote_asset),
                "totalWalletBalance": self.balance_free(filters.quote_asset),
                "stale": False,
                "error": None,
            }
            self._last_capital[symbol] = {k: v for k, v in snap.items() if k != "stale"}
            return snap
        except Exception as exc:
            prev = dict(self._last_capital.get(symbol) or {})
            prev.update(
                {
                    "quote_asset": filters.quote_asset,
                    "base_asset": filters.base_asset,
                    "stale": bool(prev),
                    "error": str(exc),
                }
            )
            return prev

    def base_asset_qty(self, symbol: str) -> float:
        """Solde réel de l'actif de base (ex. BTC pour BTCUSDT) — source de vérité Spot."""
        filters = self.get_symbol_filters(symbol)
        return self.balance_total(filters.base_asset)

    def quote_asset_free(self, symbol: str) -> float:
        filters = self.get_symbol_filters(symbol)
        return self.balance_free(filters.quote_asset)

    def my_trades(self, symbol: str, *, limit: int = 50, order_id: int | None = None) -> list:
        """GET /api/v3/myTrades — commissions réelles par fill."""
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if order_id is not None:
            params["orderId"] = order_id
        body = self._request("GET", "/api/v3/myTrades", params, signed=True)
        return body if isinstance(body, list) else []

    def open_orders(self, symbol: str | None = None, *, force: bool = False) -> list:
        key = symbol or "*"
        now = time.time()
        cached = self._open_orders_cache.get(key)
        if not force and cached and now - cached[0] < self.account_cache_ttl_s:
            return cached[1]
        params = {"symbol": symbol} if symbol else {}
        body = self._request("GET", "/api/v3/openOrders", params, signed=True)
        orders = body if isinstance(body, list) else []
        self._open_orders_cache[key] = (now, orders)
        return orders

    def all_orders(self, symbol: str, limit: int = 100) -> list:
        return self._request(
            "GET", "/api/v3/allOrders", {"symbol": symbol, "limit": limit}, signed=True
        )

    def get_order(self, symbol: str, order_id: int) -> dict:
        return self._request(
            "GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True
        )

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict:
        return self._request(
            "GET",
            "/api/v3/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
            signed=True,
        )

    def find_order_by_client_order_id(
        self, symbol: str, client_order_id: str
    ) -> tuple[dict | None, dict[str, Any]]:
        trace: dict[str, Any] = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "open_orders_match": None,
            "all_orders_match": None,
            "get_order_match": None,
        }
        opens = self.open_orders(symbol)
        for o in opens:
            if o.get("clientOrderId") == client_order_id:
                trace["open_orders_match"] = o
                trace["source"] = "openOrders"
                return o, trace
        trace["open_orders_count"] = len(opens)
        try:
            history = self.all_orders(symbol, limit=100)
        except Exception as exc:
            trace["all_orders_error"] = str(exc)
            history = []
        for o in history:
            if o.get("clientOrderId") == client_order_id:
                trace["all_orders_match"] = o
                trace["source"] = "allOrders"
                return o, trace
        trace["all_orders_scanned"] = len(history)
        try:
            got = self.get_order_by_client_id(symbol, client_order_id)
            trace["get_order_match"] = got
            trace["source"] = "get_order_origClientOrderId"
            return got, trace
        except Exception as exc:
            trace["get_order_error"] = str(exc)
        trace["source"] = None
        return None, trace

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal | float | str,
        price: Decimal | float | str | None = None,
        time_in_force: str = "GTC",
        purpose: str = "normal",
        max_attempts: int = MAX_ORDER_ATTEMPTS,
        grid_level: int | None = None,
        **_ignored: Any,
    ) -> dict:
        """Place un ordre Spot. **_ignored absorbe reduce_only/position_side hérités."""
        filters = self.get_symbol_filters(symbol)
        qty_str = filters.format_qty(quantity)
        price_str: str | None = None
        is_priority = purpose in PRIORITY_CLOSE_PURPOSES

        base_params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": qty_str,
        }
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            price_str = filters.format_price(price)
            base_params["price"] = price_str
            base_params["timeInForce"] = time_in_force

        used_client_ids: list[str] = []
        last_error: Exception | None = None
        timeout_not_found_count = 0

        for attempt_no in range(1, max_attempts + 1):
            client_order_id = self.new_client_order_id()
            used_client_ids.append(client_order_id)
            params = dict(base_params)
            params["newClientOrderId"] = client_order_id

            status, body, raw = self._raw_request(
                "POST", "/api/v3/order", params, signed=True
            )
            code = body.get("code") if isinstance(body, dict) else None
            msg = body.get("msg") if isinstance(body, dict) else raw

            if 200 <= status < 300 and isinstance(body, dict) and "orderId" in body:
                self._log_attempt(
                    {
                        "symbol": symbol,
                        "side": side.upper(),
                        "order_type": order_type.upper(),
                        "purpose": purpose,
                        "client_order_id": client_order_id,
                        "attempt_no": attempt_no,
                        "outcome": "success",
                        "http_status": status,
                        "binance_code": None,
                        "binance_msg": None,
                        "order_id": str(body.get("orderId")),
                        "request_json": params,
                        "response_json": body,
                        "verify_json": None,
                        "used_client_ids": list(used_client_ids),
                        "grid_level": grid_level,
                    }
                )
                return body

            status_unknown = code == -1007 or status in (408, 502, 504)
            if status_unknown:
                found, trace = self.find_order_by_client_order_id(symbol, client_order_id)
                if found:
                    self._log_attempt(
                        {
                            "symbol": symbol,
                            "side": side.upper(),
                            "order_type": order_type.upper(),
                            "purpose": purpose,
                            "client_order_id": client_order_id,
                            "attempt_no": attempt_no,
                            "outcome": "duplicate_avoided",
                            "http_status": status,
                            "binance_code": code,
                            "binance_msg": str(msg)[:500] if msg else None,
                            "order_id": str(found.get("orderId")),
                            "request_json": params,
                            "response_json": body if isinstance(body, dict) else {"raw": raw[:500]},
                            "verify_json": trace,
                            "used_client_ids": list(used_client_ids),
                            "grid_level": grid_level,
                        }
                    )
                    return found

                timeout_not_found_count += 1
                self._log_attempt(
                    {
                        "symbol": symbol,
                        "side": side.upper(),
                        "order_type": order_type.upper(),
                        "purpose": purpose,
                        "client_order_id": client_order_id,
                        "attempt_no": attempt_no,
                        "outcome": "timeout_not_found",
                        "http_status": status,
                        "binance_code": code,
                        "binance_msg": str(msg)[:500] if msg else None,
                        "order_id": None,
                        "request_json": params,
                        "response_json": body if isinstance(body, dict) else {"raw": raw[:500]},
                        "verify_json": trace,
                        "used_client_ids": list(used_client_ids),
                        "grid_level": grid_level,
                    }
                )
                last_error = requests.HTTPError(f"{status} {raw[:300]}")
                if attempt_no < max_attempts:
                    time.sleep(BACKOFF_BASE_S * (2 ** (attempt_no - 1)))
                    continue
                break

            if code == -1008:
                outcome = "anomaly_1008_priority" if is_priority else "throttled"
                if is_priority:
                    logger.critical(
                        "ANOMALIE HAUTE PRIORITÉ: -1008 sur ordre de fermeture purpose=%s",
                        purpose,
                    )
                self._log_attempt(
                    {
                        "symbol": symbol,
                        "side": side.upper(),
                        "order_type": order_type.upper(),
                        "purpose": purpose,
                        "client_order_id": client_order_id,
                        "attempt_no": attempt_no,
                        "outcome": outcome,
                        "http_status": status,
                        "binance_code": code,
                        "binance_msg": msg,
                        "order_id": None,
                        "request_json": params,
                        "response_json": body if isinstance(body, dict) else {"raw": raw},
                        "verify_json": None,
                        "used_client_ids": list(used_client_ids),
                        "grid_level": grid_level,
                    }
                )
                last_error = requests.HTTPError(f"{status} {raw}")
                if attempt_no < max_attempts:
                    time.sleep(BACKOFF_BASE_S * (2 ** (attempt_no - 1)))
                    continue
                break

            self._log_attempt(
                {
                    "symbol": symbol,
                    "side": side.upper(),
                    "order_type": order_type.upper(),
                    "purpose": purpose,
                    "client_order_id": client_order_id,
                    "attempt_no": attempt_no,
                    "outcome": "error",
                    "http_status": status,
                    "binance_code": code,
                    "binance_msg": msg,
                    "order_id": None,
                    "request_json": params,
                    "response_json": body if isinstance(body, dict) else {"raw": raw},
                    "verify_json": None,
                    "used_client_ids": list(used_client_ids),
                    "grid_level": grid_level,
                }
            )
            raise requests.HTTPError(f"{status} {raw}")

        if timeout_not_found_count >= max_attempts:
            from datetime import datetime, timezone

            ts = datetime.now(timezone.utc).isoformat()
            self._log_attempt(
                {
                    "symbol": symbol,
                    "side": side.upper(),
                    "order_type": order_type.upper(),
                    "purpose": purpose,
                    "client_order_id": used_client_ids[-1] if used_client_ids else "",
                    "attempt_no": max_attempts,
                    "outcome": "retry_exhausted",
                    "http_status": None,
                    "binance_code": -1007,
                    "binance_msg": f"Palier {grid_level} non placé après {max_attempts} tentatives",
                    "order_id": None,
                    "request_json": {
                        "symbol": symbol,
                        "side": side.upper(),
                        "price": price_str,
                        "quantity": qty_str,
                        "grid_level": grid_level,
                        "last_attempt_at": ts,
                    },
                    "response_json": None,
                    "verify_json": {
                        "used_client_ids": list(used_client_ids),
                        "timeout_not_found_count": timeout_not_found_count,
                    },
                    "grid_level": grid_level,
                }
            )
            raise RetryExhaustedError(
                symbol=symbol,
                side=side.upper(),
                price=price_str,
                quantity=qty_str,
                grid_level=grid_level,
                used_client_ids=list(used_client_ids),
                last_error=last_error,
            )
        assert last_error is not None
        raise last_error

    def place_trailing_stop_sell(
        self,
        symbol: str,
        quantity: Decimal | float | str,
        *,
        trailing_delta_bips: int,
        limit_price: Decimal | float | str,
        activation_stop_price: Decimal | float | str | None = None,
        purpose: str = "egaliseur_trailing",
        max_attempts: int = MAX_ORDER_ATTEMPTS,
    ) -> dict:
        """STOP_LOSS_LIMIT SELL avec trailingDelta — bornes lues via exchangeInfo."""
        filters = self.get_symbol_filters(symbol)
        bips = max(
            filters.trailing_delta_min_bips,
            min(int(trailing_delta_bips), filters.trailing_delta_max_bips),
        )
        qty_str = filters.format_qty(quantity)
        price_str = filters.format_price(limit_price)
        base_params: dict[str, Any] = {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "quantity": qty_str,
            "price": price_str,
            "timeInForce": "GTC",
            "trailingDelta": bips,
        }
        if activation_stop_price is not None:
            base_params["stopPrice"] = filters.format_price(activation_stop_price)

        used_client_ids: list[str] = []
        last_error: Exception | None = None
        for attempt_no in range(1, max_attempts + 1):
            client_order_id = self.new_client_order_id()
            used_client_ids.append(client_order_id)
            params = dict(base_params)
            params["newClientOrderId"] = client_order_id
            status, body, raw = self._raw_request("POST", "/api/v3/order", params, signed=True)
            code = body.get("code") if isinstance(body, dict) else None
            msg = body.get("msg") if isinstance(body, dict) else raw
            if 200 <= status < 300 and isinstance(body, dict) and "orderId" in body:
                self._open_orders_cache.pop(symbol, None)
                self._open_orders_cache.pop("*", None)
                self._log_attempt(
                    {
                        "symbol": symbol,
                        "side": "SELL",
                        "order_type": "STOP_LOSS_LIMIT",
                        "purpose": purpose,
                        "client_order_id": client_order_id,
                        "attempt_no": attempt_no,
                        "outcome": "success",
                        "http_status": status,
                        "binance_code": None,
                        "binance_msg": None,
                        "order_id": str(body.get("orderId")),
                        "request_json": params,
                        "response_json": body,
                        "verify_json": {"trailing_delta_bips": bips},
                        "used_client_ids": list(used_client_ids),
                    }
                )
                return body
            status_unknown = code == -1007 or status in (408, 502, 504)
            if status_unknown:
                found, trace = self.find_order_by_client_order_id(symbol, client_order_id)
                if found:
                    return found
                last_error = requests.HTTPError(f"{status} {raw[:300]}")
                if attempt_no < max_attempts:
                    time.sleep(BACKOFF_BASE_S * (2 ** (attempt_no - 1)))
                    continue
                break
            last_error = requests.HTTPError(f"{status} {raw}")
            break
        if last_error:
            raise last_error
        raise RuntimeError("place_trailing_stop_sell failed without error")

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        status, body, raw = self._raw_request(
            "DELETE",
            "/api/v3/order",
            {"symbol": symbol, "orderId": order_id},
            signed=True,
        )
        self._open_orders_cache.pop(symbol, None)
        self._open_orders_cache.pop("*", None)
        code = body.get("code") if isinstance(body, dict) else None
        if code == -1008:
            logger.critical("ANOMALIE HAUTE PRIORITÉ: -1008 sur cancel_order orderId=%s", order_id)
            self._log_attempt(
                {
                    "symbol": symbol,
                    "side": "CANCEL",
                    "order_type": "CANCEL",
                    "purpose": "cancel",
                    "client_order_id": f"cancel-{order_id}",
                    "attempt_no": 1,
                    "outcome": "anomaly_1008_priority",
                    "http_status": status,
                    "binance_code": code,
                    "binance_msg": body.get("msg") if isinstance(body, dict) else raw,
                    "order_id": str(order_id),
                    "request_json": {"symbol": symbol, "orderId": order_id},
                    "response_json": body if isinstance(body, dict) else {"raw": raw},
                    "verify_json": None,
                }
            )
        if 200 <= status < 300 and isinstance(body, dict):
            return body
        if code == -1007 or status == 408:
            opens = self.open_orders(symbol)
            if not any(o.get("orderId") == order_id for o in opens):
                return {"orderId": order_id, "status": "CANCELED", "recovered_after_timeout": True}
        raise requests.HTTPError(f"{status} {raw}")

    def cancel_all_orders(self, symbol: str) -> Any:
        status, body, raw = self._raw_request(
            "DELETE", "/api/v3/openOrders", {"symbol": symbol}, signed=True
        )
        # Invalider le cache openOrders
        self._open_orders_cache.pop(symbol, None)
        self._open_orders_cache.pop("*", None)
        code = body.get("code") if isinstance(body, dict) else None
        if code == -1008:
            logger.critical("ANOMALIE HAUTE PRIORITÉ: -1008 sur cancel_all_orders %s", symbol)
        if 200 <= status < 300:
            return body
        # -2011 = aucun ordre à annuler → succès logique
        if code == -2011:
            return []
        raise requests.HTTPError(f"{status} {raw}")

    async def stream_mark_price(
        self,
        symbol: str,
        on_price: Callable[[dict], Awaitable[None] | None],
        stop_event: asyncio.Event | None = None,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
    ) -> None:
        """Flux prix Spot via bookTicker (équivalent mark price Futures)."""
        stream = f"{symbol.lower()}@bookTicker"
        url = f"{self.ws_base}/{stream}"
        delay = reconnect_delay
        stop_event = stop_event or asyncio.Event()

        while not stop_event.is_set():
            try:
                logger.info("WS connecting %s", url)
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    delay = reconnect_delay
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            continue
                        data = json.loads(raw)
                        # Normaliser vers champ 'p' comme markPrice futures
                        bid = data.get("b") or data.get("bidPrice")
                        ask = data.get("a") or data.get("askPrice")
                        if bid and ask:
                            mid = (Decimal(bid) + Decimal(ask)) / 2
                            data = {**data, "p": str(mid), "s": data.get("s") or symbol}
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


# Alias de compatibilité pour imports existants pendant la migration
BinanceFuturesClient = BinanceSpotClient
