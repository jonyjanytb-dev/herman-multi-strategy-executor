from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass(frozen=True)
class Signal:
    side: str
    bar_time: int
    entry_reference: float
    initial_tp: Optional[float]
    sl: float
    label: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeState:
    strategy: str = "trend_rebalance"
    last_processed_bar: Optional[int] = None
    last_entry_bar: Optional[int] = None
    last_exit_bar: Optional[int] = None
    active_side: int = 0
    active_entry: Optional[float] = None
    active_sl: Optional[float] = None
    active_tp_at_entry: Optional[float] = None
    tp_order_oid: Optional[int] = None
    sl_order_oid: Optional[int] = None
    strategy_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "last_processed_bar": self.last_processed_bar,
            "last_entry_bar": self.last_entry_bar,
            "last_exit_bar": self.last_exit_bar,
            "active_side": self.active_side,
            "active_entry": self.active_entry,
            "active_sl": self.active_sl,
            "active_tp_at_entry": self.active_tp_at_entry,
            "tp_order_oid": self.tp_order_oid,
            "sl_order_oid": self.sl_order_oid,
            "strategy_state": self.strategy_state,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RuntimeState":
        return cls(
            strategy=str(raw.get("strategy") or "trend_rebalance"),
            last_processed_bar=raw.get("last_processed_bar"),
            last_entry_bar=raw.get("last_entry_bar"),
            last_exit_bar=raw.get("last_exit_bar"),
            active_side=int(raw.get("active_side") or 0),
            active_entry=raw.get("active_entry"),
            active_sl=raw.get("active_sl"),
            active_tp_at_entry=raw.get("active_tp_at_entry"),
            tp_order_oid=raw.get("tp_order_oid"),
            sl_order_oid=raw.get("sl_order_oid"),
            strategy_state=dict(raw.get("strategy_state") or {}),
        )
