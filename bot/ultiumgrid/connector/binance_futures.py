"""Connecteur Binance USDT-M Futures Testnet.

Règle anti-doublon post-timeout (-1007) :
un -1007 ne signifie PAS que l'ordre a échoué. Avant tout nouvel essai,
vérifier openOrders / allOrders / order par newClientOrderId.
Chaque tentative utilise un newClientOrderId unique (jamais réutilisé).

Backoff exponentiel : 200ms → 400ms → 800ms… (max 5 tentatives).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
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

DEFAULT_REST = "https://testnet.binancefuture.com"
DEFAULT_WS = "wss://stream.binancefuture.com"

# Backoff initial (secondes) : 200ms, 400ms, 800ms, 1600ms, 3200ms
BACKOFF_BASE_S = 0.2
MAX_ORDER_ATTEMPTS = 5


class RetryExhaustedError(Exception):
    """5 tentatives timeout_not_found — palier non placé, ne pas abandonner silencieusement."""

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

# Purposes prioritaires (fermeture) — censés être exempts de -1008
PRIORITY_CLOSE_PURPOSES = frozenset(
    {"panic_close", "risk_cut", "hard_stop", "cycle_close", "bag_sell"}
)


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
    # Journal en mémoire (toujours) + callback DB optionnel
    attempt_log: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _order_log_callback: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)

    def set_order_log_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._order_log_callback = callback

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

    def _raw_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> tuple[int, Any, str]:
        """Retourne (http_status, body_json|None, raw_text). Ne lève pas sur 4xx/5xx métier."""
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
        raw = resp.text
        body: Any = None
        if raw:
            try:
                body = resp.json()
            except Exception:
                body = None
        return resp.status_code, body, raw

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        retries: int = 3,
    ) -> Any:
        """Requêtes non-ordre : retry backoff sur 502/503 uniquement (pas de logique -1007 ordre)."""
        last_exc: Exception | None = None
        for attempt in range(retries):
            status, body, raw = self._raw_request(method, path, params, signed=signed)
            if 200 <= status < 300:
                if raw == "" or raw == "{}":
                    return {} if raw == "{}" else None
                return body
            code = body.get("code") if isinstance(body, dict) else None
            # Pas de retry aveugle sur -1007 ici (réservé à place_order)
            retryable = status in (502, 503) or code in (-1000, -1008)
            if retryable and attempt < retries - 1:
                delay = BACKOFF_BASE_S * (2**attempt)
                logger.warning(
                    "Binance %s %s status=%s code=%s — backoff %.3fs (attempt %s)",
                    method,
                    path,
                    status,
                    code,
                    delay,
                    attempt + 1,
                )
                time.sleep(delay)
                last_exc = requests.HTTPError(f"{status} {raw}")
                continue
            logger.error("Binance %s %s -> %s %s", method, path, status, raw)
            raise requests.HTTPError(f"{status} {raw}")
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Binance request failed: {method} {path}")

    def _log_attempt(self, entry: dict[str, Any]) -> None:
        self.attempt_log.append(entry)
        logger.info(
            "order_attempt outcome=%s clientOrderId=%s attempt=%s code=%s orderId=%s",
            entry.get("outcome"),
            entry.get("client_order_id"),
            entry.get("attempt_no"),
            entry.get("binance_code"),
            entry.get("order_id"),
        )
        if self._order_log_callback:
            try:
                self._order_log_callback(entry)
            except Exception:
                logger.exception("order_log_callback failed")

    @staticmethod
    def new_client_order_id(prefix: str = "ug") -> str:
        """Identifiant unique ≤ 36 caractères (limite Binance). Jamais réutilisé."""
        return f"{prefix}{uuid.uuid4().hex}"[:36]

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

    def all_orders(self, symbol: str, limit: int = 100) -> list:
        return self._request(
            "GET",
            "/fapi/v1/allOrders",
            {"symbol": symbol, "limit": limit},
            signed=True,
        )

    def get_order(self, symbol: str, order_id: int) -> dict:
        return self._request(
            "GET",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict:
        return self._request(
            "GET",
            "/fapi/v1/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
            signed=True,
        )

    def find_order_by_client_order_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> tuple[dict | None, dict[str, Any]]:
        """Vérifie openOrders, puis allOrders, puis GET order par origClientOrderId.

        Retourne (order|None, verify_trace) pour audit.
        """
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
        purpose: str = "normal",
        close_position: bool = False,
        max_attempts: int = MAX_ORDER_ATTEMPTS,
        grid_level: int | None = None,
    ) -> dict:
        """Place un ordre avec anti-doublon post -1007 et backoff exponentiel.

        Chaque tentative a un newClientOrderId unique.
        Après -1007 : vérifier Binance avant tout renvoi.
        Si 5× timeout_not_found → retry_exhausted (jamais abandon silencieux).
        """
        filters = self.get_symbol_filters(symbol)
        qty_str = filters.format_qty(quantity) if not close_position else None
        price_str: str | None = None
        hedge = self.is_hedge_mode()
        if position_side is None and hedge:
            position_side = "LONG" if side.upper() == "BUY" else "SHORT"

        is_priority = (
            purpose in PRIORITY_CLOSE_PURPOSES or close_position or reduce_only
        )

        base_params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
        }
        if not close_position:
            base_params["quantity"] = qty_str
        if order_type.upper() == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            price_str = filters.format_price(price)
            base_params["price"] = price_str
            base_params["timeInForce"] = time_in_force
        if position_side:
            base_params["positionSide"] = position_side
        if reduce_only and not hedge:
            base_params["reduceOnly"] = "true"
        if close_position:
            base_params["closePosition"] = "true"

        used_client_ids: list[str] = []
        last_error: Exception | None = None
        timeout_not_found_count = 0

        for attempt_no in range(1, max_attempts + 1):
            client_order_id = self.new_client_order_id()
            used_client_ids.append(client_order_id)
            params = dict(base_params)
            params["newClientOrderId"] = client_order_id

            status, body, raw = self._raw_request(
                "POST", "/fapi/v1/order", params, signed=True
            )
            code = body.get("code") if isinstance(body, dict) else None
            msg = body.get("msg") if isinstance(body, dict) else raw

            # Succès immédiat
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
                        "request_json": {k: v for k, v in params.items() if k != "signature"},
                        "response_json": body,
                        "verify_json": None,
                        "used_client_ids": list(used_client_ids),
                    }
                )
                return body

            # --- Statut inconnu (-1007 / 408 / 502) : vérifier avant tout renvoi ---
            # -1007 = matching engine timeout ; 502 = gateway — l'ordre PEUT exister.
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
                            "binance_msg": msg if isinstance(msg, str) else str(msg)[:500],
                            "order_id": str(found.get("orderId")),
                            "request_json": {k: v for k, v in params.items()},
                            "response_json": body if isinstance(body, dict) else {"raw": raw[:500]},
                            "verify_json": trace,
                            "used_client_ids": list(used_client_ids),
                        }
                    )
                    # Rattacher à l'état interne : retourner l'ordre existant, NE PAS renvoyer
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
                        "binance_msg": msg if isinstance(msg, str) else str(msg)[:500],
                        "order_id": None,
                        "request_json": {k: v for k, v in params.items()},
                        "response_json": body if isinstance(body, dict) else {"raw": raw[:500]},
                        "verify_json": trace,
                        "used_client_ids": list(used_client_ids),
                        "grid_level": grid_level,
                    }
                )
                last_error = requests.HTTPError(f"{status} {raw[:300]}")
                if attempt_no < max_attempts:
                    delay = BACKOFF_BASE_S * (2 ** (attempt_no - 1))
                    logger.warning(
                        "statut inconnu (%s/%s) sans ordre pour %s — backoff %.3fs puis nouvel id",
                        status,
                        code,
                        client_order_id,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                break

            # --- -1008 : throttle (reduce-only / close / cancel censés exempts) ---
            if code == -1008:
                outcome = (
                    "anomaly_1008_priority"
                    if is_priority
                    else "throttled"
                )
                if is_priority:
                    logger.critical(
                        "ANOMALIE HAUTE PRIORITÉ: -1008 sur ordre de fermeture "
                        "(purpose=%s reduceOnly=%s closePosition=%s) — "
                        "ces ordres sont censés être exempts du throttle",
                        purpose,
                        reduce_only,
                        close_position,
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
                        "request_json": {k: v for k, v in params.items()},
                        "response_json": body if isinstance(body, dict) else {"raw": raw},
                        "verify_json": None,
                        "used_client_ids": list(used_client_ids),
                    }
                )
                last_error = requests.HTTPError(f"{status} {raw}")
                if attempt_no < max_attempts:
                    delay = BACKOFF_BASE_S * (2 ** (attempt_no - 1))
                    time.sleep(delay)
                    continue
                break

            # Autre erreur : pas de retry silencieux
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
                    "request_json": {k: v for k, v in params.items()},
                    "response_json": body if isinstance(body, dict) else {"raw": raw},
                    "verify_json": None,
                    "used_client_ids": list(used_client_ids),
                }
            )
            raise requests.HTTPError(f"{status} {raw}")

        # Épuisement des retries timeout : jamais abandon silencieux
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

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Annulation — exemptée de -1008 ; si -1008 reçu = anomalie haute priorité."""
        status, body, raw = self._raw_request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
            signed=True,
        )
        code = body.get("code") if isinstance(body, dict) else None
        if code == -1008:
            logger.critical(
                "ANOMALIE HAUTE PRIORITÉ: -1008 sur cancel_order orderId=%s (censé exempt)",
                order_id,
            )
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
        # -1007 sur cancel : vérifier si l'ordre a disparu (déjà annulé/fillé)
        if code == -1007 or status == 408:
            opens = self.open_orders(symbol)
            still = [o for o in opens if o.get("orderId") == order_id]
            if not still:
                return {"orderId": order_id, "status": "CANCELED", "recovered_after_timeout": True}
        raise requests.HTTPError(f"{status} {raw}")

    def cancel_all_orders(self, symbol: str) -> Any:
        status, body, raw = self._raw_request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol},
            signed=True,
        )
        code = body.get("code") if isinstance(body, dict) else None
        if code == -1008:
            logger.critical(
                "ANOMALIE HAUTE PRIORITÉ: -1008 sur cancel_all_orders %s (censé exempt)",
                symbol,
            )
        if 200 <= status < 300:
            return body
        raise requests.HTTPError(f"{status} {raw}")

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
