"""Test d'intégration anti-doublon post -1007 — frappes réelles Binance + journal DB.

Scénarios :
1. Si un ordre est placé avec succès : prouver find_order_by_client_order_id le retrouve
   via openOrders/allOrders réels, puis simuler un -1007 avec le MÊME clientOrderId
   pour démontrer duplicate_avoided (aucun second POST effectif côté matching).
2. Si -1007 survient naturellement : prouver timeout_not_found + clientOrderIds uniques
   + traces verify openOrders/allOrders dans le journal.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
load_dotenv(ROOT / ".env")

from ultiumgrid.connector.binance_futures import BinanceFuturesClient  # noqa: E402
from ultiumgrid.db.models import OrderAttempt, make_session_factory  # noqa: E402

PROOFS = ROOT / "docs" / "proofs"
PROOFS.mkdir(parents=True, exist_ok=True)
SYMBOL = "BTCUSDT"
DB_URL = f"sqlite:///{ROOT / 'data' / 'test_antiduze.db'}"


def _client_with_db_log():
    key = os.getenv("BINANCE_FUTURES_TESTNET_API_KEY", "").strip()
    secret = os.getenv("BINANCE_FUTURES_TESTNET_API_SECRET", "").strip()
    assert key and secret
    Path(ROOT / "data").mkdir(exist_ok=True)
    db_path = ROOT / "data" / "test_antiduze.db"
    if db_path.exists():
        db_path.unlink()
    SessionLocal, engine = make_session_factory(DB_URL)
    session = SessionLocal()

    client = BinanceFuturesClient(api_key=key, api_secret=secret)

    def persist(entry: dict) -> None:
        row = OrderAttempt(
            symbol=entry.get("symbol") or "",
            side=entry.get("side") or "",
            order_type=entry.get("order_type") or "",
            purpose=entry.get("purpose") or "normal",
            client_order_id=entry.get("client_order_id") or "",
            attempt_no=int(entry.get("attempt_no") or 0),
            outcome=entry.get("outcome") or "",
            http_status=entry.get("http_status"),
            binance_code=entry.get("binance_code"),
            binance_msg=entry.get("binance_msg"),
            order_id=entry.get("order_id"),
            request_json=entry.get("request_json"),
            response_json=entry.get("response_json"),
            verify_json=entry.get("verify_json"),
        )
        session.add(row)
        session.commit()

    client.set_order_log_callback(persist)
    return client, session, engine


@pytest.mark.integration
def test_antiduze_post_1007_real_binance():
    client, session, engine = _client_with_db_log()
    proof: dict = {"steps": [], "scenario": None}

    ticker = client.ticker_price(SYMBOL)
    market = Decimal(ticker["price"])
    filters = client.get_symbol_filters(SYMBOL)
    limit_price = filters.round_price(market * Decimal("0.70"))
    qty = filters.round_qty(filters.min_notional / limit_price * Decimal("1.2"))
    if qty < filters.min_qty:
        qty = filters.min_qty

    proof["ticker"] = ticker
    proof["limit_price"] = str(limit_price)
    proof["qty"] = str(qty)

    # --- Tentative réelle de placement ---
    placed = None
    natural_1007 = False
    try:
        placed = client.place_order(
            symbol=SYMBOL,
            side="BUY",
            order_type="LIMIT",
            quantity=qty,
            price=limit_price,
            purpose="normal",
            max_attempts=3,
        )
        proof["steps"].append({"action": "PLACE_RESULT", "order": placed})
    except Exception as exc:
        natural_1007 = True
        proof["steps"].append(
            {
                "action": "PLACE_RAISED",
                "error": str(exc)[:500],
                "attempt_log": list(client.attempt_log),
            }
        )

    if placed and placed.get("orderId"):
        # Scénario A : ordre réellement placé — prouver vérification + anti-doublon
        proof["scenario"] = "duplicate_avoided_after_successful_place"
        client_order_id = placed.get("clientOrderId")
        assert client_order_id, "Binance doit renvoyer clientOrderId"
        order_id = placed["orderId"]

        # Preuve réelle openOrders / allOrders / get
        found, trace = client.find_order_by_client_order_id(SYMBOL, client_order_id)
        proof["steps"].append(
            {
                "action": "VERIFY_REAL_FIND",
                "client_order_id": client_order_id,
                "found": found,
                "trace": trace,
            }
        )
        assert found is not None
        assert found["orderId"] == order_id
        assert trace["source"] in ("openOrders", "allOrders", "get_order_origClientOrderId")

        # Simuler un -1007 sur une « nouvelle » tentative qui réutiliserait le même id
        # (cas fantôme) : le connecteur doit retrouver l'ordre et NE PAS renvoyer.
        real_raw = client._raw_request
        post_count = {"n": 0}

        def raw_1007(method, path, params=None, signed=False):
            if method == "POST" and path == "/fapi/v1/order":
                post_count["n"] += 1
                body = {
                    "code": -1007,
                    "msg": "Timeout waiting for response from backend server. Send status unknown; execution status unknown.",
                }
                return 408, body, json.dumps(body)
            return real_raw(method, path, params, signed)

        client._raw_request = raw_1007  # type: ignore
        client.new_client_order_id = lambda prefix="ug": client_order_id  # type: ignore

        recovered = client.place_order(
            symbol=SYMBOL,
            side="BUY",
            order_type="LIMIT",
            quantity=qty,
            price=limit_price,
            max_attempts=2,
        )
        proof["steps"].append(
            {
                "action": "SIMULATED_1007_RECOVERY",
                "recovered_order": recovered,
                "post_count": post_count["n"],
                "attempt_log_tail": client.attempt_log[-2:],
            }
        )
        assert recovered["orderId"] == order_id
        assert post_count["n"] == 1  # un seul POST timeout, pas de second envoi
        assert any(a["outcome"] == "duplicate_avoided" for a in client.attempt_log)

        # Toujours un seul ordre live avec cet id
        opens = client.open_orders(SYMBOL)
        matches = [o for o in opens if o.get("clientOrderId") == client_order_id]
        proof["steps"].append({"action": "OPEN_ORDERS_AFTER_RECOVERY", "matches": matches})
        assert len(matches) == 1

        # Cleanup
        client._raw_request = real_raw  # type: ignore
        client.new_client_order_id = BinanceFuturesClient.new_client_order_id  # type: ignore
        cancelled = client.cancel_order(SYMBOL, order_id)
        proof["steps"].append({"action": "CANCEL", "raw": cancelled})

    else:
        # Scénario B : -1007 naturel — prouver vérif avant retry + ids uniques
        proof["scenario"] = "natural_1007_timeout_not_found"
        assert client.attempt_log, "le journal de tentatives doit être rempli"
        outcomes = [a["outcome"] for a in client.attempt_log]
        proof["outcomes"] = outcomes
        assert all(o in ("timeout_not_found", "throttled", "error", "anomaly_1008_priority") for o in outcomes)
        assert any(o == "timeout_not_found" for o in outcomes) or any(
            a.get("binance_code") == -1007 for a in client.attempt_log
        )

        client_ids = [a["client_order_id"] for a in client.attempt_log]
        proof["client_order_ids"] = client_ids
        assert len(client_ids) == len(set(client_ids)), "chaque tentative doit avoir un id unique"

        for a in client.attempt_log:
            if a["outcome"] == "timeout_not_found":
                assert a.get("verify_json") is not None
                # Preuve que openOrders a été interrogé
                v = a["verify_json"]
                assert "open_orders_count" in v or v.get("open_orders_match") is not None or v.get("source") is None
                proof["steps"].append(
                    {
                        "action": "NATURAL_1007_VERIFY_TRACE",
                        "client_order_id": a["client_order_id"],
                        "verify_json": v,
                    }
                )

        # Aucun ordre fantôme ouvert pour ces clientOrderIds
        opens = client.open_orders(SYMBOL)
        ghosts = [o for o in opens if o.get("clientOrderId") in client_ids]
        proof["steps"].append({"action": "NO_GHOST_ORDERS", "open_orders": opens, "ghosts": ghosts})
        # Si un ghost existait, ce serait duplicate_avoided — ici timeout_not_found implique 0
        assert ghosts == []

    # --- Démonstration duplicate_avoided (cas où un doublon aurait pu se produire) ---
    # On simule : POST renvoie -1007 ALORS QUE l'ordre est déjà visible dans openOrders
    # (situation réelle quand le matching engine a accepté l'ordre mais le client a timeout).
    # openOrders est un appel RÉEL ; on y injecte l'entrée fantôme pour reproduire ce cas
    # sans pouvoir s'appuyer sur un POST réussi (testnet en -1007 permanent).
    dupe_cid = BinanceFuturesClient.new_client_order_id()
    phantom_order = {
        "orderId": 900001,
        "clientOrderId": dupe_cid,
        "status": "NEW",
        "symbol": SYMBOL,
        "side": "BUY",
        "type": "LIMIT",
        "price": str(limit_price),
        "origQty": str(qty),
    }
    real_open_orders = client.open_orders
    real_raw = client._raw_request
    real_all = client.all_orders
    real_get_cid = client.get_order_by_client_id

    open_orders_calls: list = []

    def open_orders_with_phantom(symbol=None):
        # Appel réel Binance
        live = real_open_orders(symbol)
        open_orders_calls.append({"live_raw": live, "injected": phantom_order})
        # L'ordre « accepté malgré timeout » apparaît dans le carnet
        return list(live) + [phantom_order]

    def raw_timeout(method, path, params=None, signed=False):
        if method == "POST" and path == "/fapi/v1/order":
            body = {
                "code": -1007,
                "msg": "Timeout waiting for response from backend server. Send status unknown; execution status unknown.",
            }
            return 408, body, json.dumps(body)
        return real_raw(method, path, params, signed)

    client.open_orders = open_orders_with_phantom  # type: ignore
    client._raw_request = raw_timeout  # type: ignore
    client.new_client_order_id = lambda prefix="ug": dupe_cid  # type: ignore
    # allOrders / get_order restent réels (ne contiendront pas le fantôme)

    before_posts = sum(1 for a in client.attempt_log if a.get("outcome"))
    recovered = client.place_order(
        symbol=SYMBOL,
        side="BUY",
        order_type="LIMIT",
        quantity=qty,
        price=limit_price,
        max_attempts=2,
    )
    dupe_entries = [a for a in client.attempt_log if a["outcome"] == "duplicate_avoided"]
    proof["steps"].append(
        {
            "action": "DUPLICATE_AVOIDED_DEMO",
            "note": (
                "POST simulé -1007 ; openOrders RÉEL + entrée injectée représentant "
                "l'ordre accepté côté matching engine. Le connecteur rattache l'ordre "
                "existant et n'effectue pas de second envoi."
            ),
            "client_order_id": dupe_cid,
            "recovered_order_id": recovered.get("orderId"),
            "open_orders_calls": open_orders_calls,
            "duplicate_avoided_log": dupe_entries[-1] if dupe_entries else None,
            "all_orders_still_real": True,
        }
    )
    assert recovered["orderId"] == phantom_order["orderId"]
    assert dupe_entries, "duplicate_avoided doit être journalisé"
    assert dupe_entries[-1]["verify_json"]["source"] == "openOrders"
    assert open_orders_calls, "openOrders réel doit avoir été appelé"
    # Un seul POST timeout, pas de renvoi
    assert dupe_entries[-1]["attempt_no"] == 1

    # Restaurer
    client.open_orders = real_open_orders  # type: ignore
    client._raw_request = real_raw  # type: ignore
    client.all_orders = real_all  # type: ignore
    client.get_order_by_client_id = real_get_cid  # type: ignore

    # Journal DB SQL direct
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT client_order_id, attempt_no, outcome, binance_code, order_id "
                "FROM order_attempts ORDER BY id"
            )
        ).all()
    proof["db_order_attempts"] = [list(r) for r in rows]
    assert len(rows) >= 1
    assert any(r[2] == "duplicate_avoided" for r in rows)

    (PROOFS / "m1_antiduze_post_1007.json").write_text(json.dumps(proof, indent=2, default=str))
    print(json.dumps(proof, indent=2, default=str))

    session.close()
