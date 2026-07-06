"""Configuration du Bot Égaliseur — bornes validées comme Module 7bis."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any


def pct_to_bips(pct: float) -> int:
    """1 % = 100 basis points (Binance trailingDelta)."""
    return int(round(pct * 100))


def bips_to_pct(bips: int) -> float:
    return bips / 100.0


@dataclass
class EgaliseurConfig:
    symbol: str = "BTCUSDT"
    paused: bool = False
    # test_only = journalise les sacs Bot 1, ordres réels seulement sur sacs « armés » (tests ponctuels).
    # continuous = veille autonome permanente sur tout sac open.
    operation_mode: str = "test_only"
    test_armed_bag_ids: list[int] = field(default_factory=list)
    trailing_delta_pct: float = 1.5
    limit_margin_pct: float = 0.15
    activation_recovery_pct: float = 1.0
    hard_stop_pct: float = -8.0
    max_hold_days: float = 1.0  # CDC §4.2 : défaut 24 h
    daily_loss_cap_usd: float = -50.0
    cancel_orders_on_pause: bool = False
    poll_s: float = 5.0

    @classmethod
    def from_env(cls) -> "EgaliseurConfig":
        mode = os.getenv("EGALISEUR_OPERATION_MODE", "test_only").strip().lower()
        if mode not in ("test_only", "continuous"):
            mode = "test_only"
        # Rétrocompat : EGALISEUR_RESTRICTED_MODE=true force test_only sauf si continuous explicite.
        legacy_restricted = os.getenv("EGALISEUR_RESTRICTED_MODE", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        if legacy_restricted and mode != "continuous":
            mode = "test_only"
        return cls(operation_mode=mode)

    def may_place_orders_on_bag(self, bag_id: int) -> bool:
        if self.operation_mode == "continuous":
            return True
        return bag_id in (self.test_armed_bag_ids or [])

    def mode_label(self) -> str:
        if self.paused:
            return "en pause"
        if self.operation_mode == "continuous":
            return "actif en continu"
        armed = self.test_armed_bag_ids or []
        if armed:
            return f"test uniquement (sacs armés: {armed})"
        return "test uniquement"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EgaliseurConfig":
        base = cls.from_env()
        if not data:
            return base
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: data[k] for k in fields if k in data}
        # Migration restricted_mode → operation_mode
        if "restricted_mode" in data and "operation_mode" not in data:
            kwargs["operation_mode"] = (
                "test_only" if data.get("restricted_mode") else "continuous"
            )
        if "test_armed_bag_ids" in kwargs:
            kwargs["test_armed_bag_ids"] = [
                int(x) for x in (kwargs["test_armed_bag_ids"] or [])
            ]
        return cls(**{**asdict(base), **kwargs})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self, *, trail_min_bips: int, trail_max_bips: int) -> list[str]:
        errors: list[str] = []
        bips = pct_to_bips(self.trailing_delta_pct)
        if bips < trail_min_bips or bips > trail_max_bips:
            errors.append(
                f"trailing_delta_pct={self.trailing_delta_pct}% hors bornes "
                f"[{bips_to_pct(trail_min_bips)}%, {bips_to_pct(trail_max_bips)}%]"
            )
        if self.limit_margin_pct < 0.05 or self.limit_margin_pct > 1.0:
            errors.append("limit_margin_pct doit être entre 0.05 et 1.0")
        if self.activation_recovery_pct < 0 or self.activation_recovery_pct > 20:
            errors.append("activation_recovery_pct doit être entre 0 et 20")
        if self.hard_stop_pct > -1 or self.hard_stop_pct < -30:
            errors.append("hard_stop_pct doit être entre -30 et -1")
        if self.max_hold_days < 0.001 or self.max_hold_days > 365:
            errors.append("max_hold_days doit être entre 0.001 et 365")
        if self.daily_loss_cap_usd > 0:
            errors.append("daily_loss_cap_usd doit être négatif ou nul")
        return errors

    @property
    def trailing_delta_bips(self) -> int:
        return pct_to_bips(self.trailing_delta_pct)
