"""Journal de trades — catégorisation alignée sur matched_ledger / order_attempts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ultiumgrid.engine.grid_profit import MatchedGridLedger

JOURNAL_CATEGORIES = (
    "initial_inventory_buy",
    "grid_fill",
    "forced_sell_stuck_level",
)

SORTABLE_COLUMNS = frozenset(
    {
        "created_at",
        "symbol",
        "cycle_id",
        "side",
        "category",
        "level_index",
        "price",
        "quantity",
        "fees_usdt",
        "trade_pnl",
    }
)


def creation_reason_from_cut_level(cut_level: int | None, source: str = "cut") -> str:
    if source == "manual":
        return "manual"
    if cut_level == 10:
        return "cut_level_10"
    if cut_level == 14:
        return "cut_level_14"
    if cut_level is not None:
        return f"cut_level_{cut_level}"
    return "cut_unknown"


def classify_trade_row(
    trade: dict[str, Any],
    forced_sell_order_ids: set[str],
) -> str:
    """Même logique que grid_recap / matched_ledger — pas de classification parallèle."""
    if trade.get("level_index") is None:
        return "initial_inventory_buy"
    oid = str(trade.get("order_id") or "")
    if oid and oid in forced_sell_order_ids:
        return "forced_sell_stuck_level"
    return "grid_fill"


def _fees_for_order(fees_by_order: dict[str, list[dict]], order_id: str | None) -> dict[str, Any]:
    rows = fees_by_order.get(str(order_id or ""), [])
    total_usdt = sum(float(r.get("commission_usdt") or 0) for r in rows)
    assets = sorted({str(r.get("commission_asset") or "") for r in rows if r.get("commission_asset")})
    return {
        "fees_usdt": round(total_usdt, 8),
        "commission_assets": assets,
        "fee_rows": len(rows),
    }


def build_trade_journal_entries(
    trades: list[dict[str, Any]],
    fees_by_order: dict[str, list[dict[str, Any]]],
    forced_sell_order_ids: set[str],
    fee_rate: float = 0.001,
) -> list[dict[str, Any]]:
    """Enrichit les lignes `trades` DB avec catégorie, frais, round-trip et PnL par fill."""
    by_cycle: dict[int | None, list[dict[str, Any]]] = {}
    for t in trades:
        cid = t.get("cycle_id")
        by_cycle.setdefault(cid, []).append(t)

    entries: list[dict[str, Any]] = []
    for cid, cycle_trades in by_cycle.items():
        ordered = sorted(
            cycle_trades,
            key=lambda x: (x.get("created_at") or "", x.get("id") or 0),
        )
        ledger = MatchedGridLedger(fee_rate=fee_rate)
        rt_index = 0
        for t in ordered:
            category = classify_trade_row(t, forced_sell_order_ids)
            level_index = t.get("level_index")
            side = str(t.get("side") or "").upper()
            fee_info = _fees_for_order(fees_by_order, t.get("order_id"))
            trade_pnl: float | None = None
            roundtrip_ref: str | None = None

            if category == "grid_fill" and level_index is not None:
                pnl_delta = ledger.on_fill(
                    side,
                    int(level_index),
                    float(t["price"]),
                    float(t["quantity"]),
                )
                if side == "SELL" and pnl_delta and ledger.matched_roundtrips:
                    rt = ledger.matched_roundtrips[-1]
                    roundtrip_ref = (
                        f"BUY@{rt['buy_level']}+SELL@{rt['sell_level']} "
                        f"qty={rt['qty']:.8f}"
                    )
                    trade_pnl = round(float(rt["profit"]), 8)
                elif side == "SELL" and pnl_delta:
                    trade_pnl = round(float(pnl_delta), 8)
                rt_index = len(ledger.matched_roundtrips)

            level_label: str | int | None = level_index
            if category == "initial_inventory_buy":
                level_label = "achat initial"
            elif category == "forced_sell_stuck_level" and level_index is not None:
                level_label = f"vente forcée Cas B (palier {level_index})"

            created = t.get("created_at")
            if isinstance(created, datetime):
                created_iso = created.isoformat()
            else:
                created_iso = str(created) if created else None

            entries.append(
                {
                    "id": t.get("id"),
                    "created_at": created_iso,
                    "symbol": t.get("symbol"),
                    "cycle_id": cid,
                    "side": side,
                    "category": category,
                    "level_index": level_index,
                    "level_label": level_label,
                    "price": float(t.get("price") or 0),
                    "quantity": float(t.get("quantity") or 0),
                    "order_id": t.get("order_id"),
                    "fees_usdt": fee_info["fees_usdt"],
                    "commission_assets": fee_info["commission_assets"],
                    "roundtrip_ref": roundtrip_ref,
                    "trade_pnl": trade_pnl,
                    "excluded_from_matched_trades": category == "initial_inventory_buy",
                }
            )

    return entries


def sort_journal_entries(
    entries: list[dict[str, Any]],
    sort_by: str,
    sort_dir: str,
) -> list[dict[str, Any]]:
    col = sort_by if sort_by in SORTABLE_COLUMNS else "created_at"
    reverse = sort_dir.lower() != "asc"

    def key_fn(row: dict[str, Any]):
        val = row.get(col)
        if val is None:
            return (1, "")
        if col == "created_at":
            return (0, str(val))
        if isinstance(val, (int, float)):
            return (0, val)
        return (0, str(val))

    return sorted(entries, key=key_fn, reverse=reverse)
