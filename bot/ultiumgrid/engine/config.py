"""Paramètres de stratégie Spot (sans levier) avec validation des bornes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class StrategyConfig:
    symbol: str = "BTCUSDT"
    capital_usdt: float = 5000.0
    num_levels: int = 20
    step_pct: float = 0.25
    cycle_trigger_usd: float = 15.0
    cut_level_1: int = 10
    cut_pct_1: float = 50.0
    cut_level_2: int = 14
    cut_pct_2: float = 100.0
    rearm_levels: int = 2
    rearm_delay_min: int = 20
    hard_stop_pct: float = -8.0
    daily_circuit_breaker_usd: float = -40.0
    bags_capital_threshold_pct: float = 40.0  # capital immobilisé en sacs
    bnb_fee_discount: bool = False

    BOUNDS = {
        "step_pct": (0.05, 2.0),
        "num_levels": (4, 40),
        "capital_usdt": (50.0, 1_000_000.0),
        "cycle_trigger_usd": (1.0, 10_000.0),
        "cut_level_1": (1, 30),
        "cut_pct_1": (1.0, 100.0),
        "cut_level_2": (2, 40),
        "cut_pct_2": (1.0, 100.0),
        "rearm_levels": (1, 10),
        "rearm_delay_min": (5, 120),
        "hard_stop_pct": (-50.0, -1.0),
        "daily_circuit_breaker_usd": (-10_000.0, -1.0),
        "bags_capital_threshold_pct": (5.0, 95.0),
    }

    def validate(self) -> list[str]:
        errors: list[str] = []
        for name, (lo, hi) in self.BOUNDS.items():
            val = getattr(self, name)
            if val < lo or val > hi:
                errors.append(f"{name}={val} hors bornes [{lo}, {hi}]")
        if self.cut_level_2 <= self.cut_level_1:
            errors.append("cut_level_2 doit être > cut_level_1")
        if not self.symbol or not self.symbol.endswith("USDT"):
            errors.append("symbol invalide (attendu *USDT)")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyConfig":
        known = {f.name for f in fields(cls)}
        # ignorer leverage hérité futures
        filtered = {k: v for k, v in data.items() if k in known}
        # alias ancien nom
        if "bags_margin_threshold_pct" in data and "bags_capital_threshold_pct" not in filtered:
            filtered["bags_capital_threshold_pct"] = data["bags_margin_threshold_pct"]
        return cls(**filtered)
