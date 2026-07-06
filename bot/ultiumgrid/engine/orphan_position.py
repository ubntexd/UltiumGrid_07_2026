"""Position résiduelle après Stop — détection, alerte, entry_avg stock préexistant."""

from __future__ import annotations

import os
from typing import Any

ORPHAN_MIN_NOTIONAL_USDT = float(os.getenv("ORPHAN_MIN_NOTIONAL_USDT", "10"))
ORPHAN_STOPPED_MIN_S = float(os.getenv("ORPHAN_STOPPED_MIN_S", "600"))


class UntrackedInventoryError(RuntimeError):
    """Stock base présent sans coût d'acquisition traçable."""


def orphan_qty(binance_base: float, bags_qty: float) -> float:
    """Quantité hors sacs formels (grille inactive ou non)."""
    return max(0.0, float(binance_base) - float(bags_qty))


def floating_pnl_vs_entry(qty: float, entry_avg: float, mark: float) -> float:
    if qty <= 0 or entry_avg <= 0:
        return 0.0
    return (mark - entry_avg) * qty


def residual_position_warning(
    client: Any,
    symbol: str,
    bags_qty: float,
    entry_avg: float = 0.0,
    min_notional_usdt: float | None = None,
    *,
    force_account: bool = True,
) -> dict[str, Any] | None:
    """Retourne un dict d'avertissement si position orpheline > seuil, sinon None."""
    threshold = ORPHAN_MIN_NOTIONAL_USDT if min_notional_usdt is None else min_notional_usdt
    try:
        if force_account and hasattr(client, "account"):
            client.account(force=True)
        base = float(client.base_asset_qty(symbol))
        mark = float(client.ticker_price(symbol, force=True)["price"])
    except Exception:
        return None
    oq = orphan_qty(base, bags_qty)
    notional = oq * mark
    if notional < threshold:
        return None
    out: dict[str, Any] = {
        "qty": oq,
        "notional_usdt": round(notional, 4),
        "mark_price": mark,
        "message": (
            f"Position résiduelle {oq:.8f} (~{notional:.2f} USDT) non surveillée "
            "tant que le bot est arrêté — vendre (Panic) ou relancer le bot."
        ),
    }
    if entry_avg > 0:
        out["entry_avg"] = entry_avg
        out["floating_pnl"] = round(floating_pnl_vs_entry(oq, entry_avg, mark), 6)
    return out


def entry_avg_from_my_trades(
    client: Any,
    symbol: str,
    qty: float,
    limit: int = 100,
) -> tuple[float, str]:
    """Coût FIFO depuis les BUY récents (myTrades) pour la quantité détenue."""
    if qty <= 0:
        raise UntrackedInventoryError("qty nulle")
    trades = client.my_trades(symbol, limit=limit)
    buys = sorted(
        [t for t in trades if t.get("isBuyer")],
        key=lambda t: int(t.get("time") or 0),
        reverse=True,
    )
    remaining = float(qty)
    cost = 0.0
    matched = 0.0
    for t in buys:
        q = float(t.get("qty") or 0)
        p = float(t.get("price") or 0)
        if q <= 0 or p <= 0:
            continue
        take = min(remaining, q)
        cost += take * p
        matched += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if matched < qty * 0.95:
        raise UntrackedInventoryError(
            f"myTrades ne couvre que {matched:.8f} / {qty:.8f} BTC — entry_avg non fiable"
        )
    return cost / matched, "myTrades_fifo"


def resolve_entry_avg_existing(
    client: Any,
    symbol: str,
    free_qty: float,
    center_price: float,
    prior_entry_avg: float | None = None,
) -> tuple[float, str]:
    """Coût d'acquisition pour stock préexistant — jamais center_price par défaut."""
    if prior_entry_avg and prior_entry_avg > 0:
        return float(prior_entry_avg), "prior_bot_state_entry_avg"
    try:
        return entry_avg_from_my_trades(client, symbol, free_qty)
    except UntrackedInventoryError as exc:
        raise UntrackedInventoryError(
            f"{exc}. Stock BTC préexistant sans coût traçable : vendre (Panic) avant Start "
            "ou s'assurer que myTrades / entry_avg cycle précédent est disponible."
        ) from exc
