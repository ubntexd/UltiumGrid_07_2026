"""Grid Profit — appariement officiel Binance (paires BUY bas + SELL haut).

Définition :
- Une paire matchée = BUY exécuté au palier inférieur i + SELL exécuté au palier i+1.
- Grid Profit = somme des profits des paires **complètes** uniquement.
- BUY sans SELL correspondant → flottant uniquement, pas de grid profit.

Formule par paire (quote asset) :
  profit = sell_qty * sell_price * (1 - fee_rate)
         - buy_qty  * buy_price  * (1 + fee_rate)
  (sur la quantité effectivement appariée)
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Lot:
    qty: float
    price: float


@dataclass
class MatchedGridLedger:
    """FIFO par couple de paliers (buy_level, buy_level+1)."""

    fee_rate: float = 0.001
    grid_profit: float = 0.0
    _buy_queues: dict[int, deque[_Lot]] = field(default_factory=lambda: defaultdict(deque))
    _sell_queues: dict[int, deque[_Lot]] = field(default_factory=lambda: defaultdict(deque))
    matched_roundtrips: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.grid_profit = 0.0
        self._buy_queues.clear()
        self._sell_queues.clear()
        self.matched_roundtrips.clear()

    def on_fill(self, side: str, level_index: int, fill_price: float, fill_qty: float) -> float:
        """Enregistre un fill grille et retourne le profit réalisé sur ce fill."""
        if fill_qty <= 0:
            return 0.0
        side_u = side.upper()
        if side_u == "BUY":
            self._buy_queues[level_index].append(_Lot(fill_qty, fill_price))
            return self._match_pair(level_index)
        if side_u == "SELL":
            self._sell_queues[level_index].append(_Lot(fill_qty, fill_price))
            return self._match_pair(level_index - 1)
        return 0.0

    def _match_pair(self, buy_level: int) -> float:
        sell_level = buy_level + 1
        if buy_level < 0:
            return 0.0
        profit_delta = 0.0
        fee = self.fee_rate
        bq = self._buy_queues[buy_level]
        sq = self._sell_queues[sell_level]
        while bq and sq:
            buy_lot = bq[0]
            sell_lot = sq[0]
            matched = min(buy_lot.qty, sell_lot.qty)
            if matched <= 0:
                break
            gross = matched * (
                sell_lot.price * (1.0 - fee) - buy_lot.price * (1.0 + fee)
            )
            profit_delta += gross
            self.grid_profit += gross
            self.matched_roundtrips.append(
                {
                    "buy_level": buy_level,
                    "sell_level": sell_level,
                    "qty": matched,
                    "buy_price": buy_lot.price,
                    "sell_price": sell_lot.price,
                    "profit": gross,
                }
            )
            buy_lot.qty -= matched
            sell_lot.qty -= matched
            if buy_lot.qty <= 1e-12:
                bq.popleft()
            if sell_lot.qty <= 1e-12:
                sq.popleft()
        return profit_delta

    def pending_buy_qty(self) -> float:
        return sum(lot.qty for q in self._buy_queues.values() for lot in q)

    def pending_sell_qty(self) -> float:
        return sum(lot.qty for q in self._sell_queues.values() for lot in q)


def total_matched_trades_from_trades(
    trades: list[dict[str, Any]],
    fee_rate: float = 0.001,
) -> int:
    """Nombre de round-trips grille complets (BUY@i + SELL@i+1).

    Source unique pour Total Matched Trades — même logique que Grid Profit.
    Exclut : achat initial, SELL d'inventaire initial sans BUY grille, BUY orphelins.
    """
    return compute_grid_profit_from_trades(trades, fee_rate)["roundtrip_count"]


def compute_grid_profit_from_trades(
    trades: list[dict[str, Any]],
    fee_rate: float = 0.001,
) -> dict[str, Any]:
    """Recalcule le Grid Profit depuis la table trades (level_index NOT NULL)."""
    ledger = MatchedGridLedger(fee_rate=fee_rate)
    ordered = sorted(trades, key=lambda t: (t.get("created_at") or "", t.get("id") or 0))
    for t in ordered:
        if t.get("level_index") is None:
            continue
        ledger.on_fill(
            str(t.get("side") or ""),
            int(t["level_index"]),
            float(t["price"]),
            float(t["quantity"]),
        )
    buys = sum(1 for t in ordered if t.get("level_index") is not None and t.get("side") == "BUY")
    sells = sum(1 for t in ordered if t.get("level_index") is not None and t.get("side") == "SELL")
    return {
        "grid_profit": ledger.grid_profit,
        "matched_roundtrips": ledger.matched_roundtrips,
        "roundtrip_count": len(ledger.matched_roundtrips),
        "buy_fills": buys,
        "sell_fills": sells,
        "orphan_buy_qty_pending": ledger.pending_buy_qty(),
        "orphan_sell_qty_pending": ledger.pending_sell_qty(),
    }
