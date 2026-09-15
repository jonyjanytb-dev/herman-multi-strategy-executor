from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Candle:
    t: int
    T: int
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
    sl: float
    strategy_id: str
    initial_tp: Optional[float] = None
    order_notional: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeState:
    strategy_id: str = ""
    last_processed_bar: Optional[int] = None
    last_entry_bar: Optional[int] = None
    last_exit_bar: Optional[int] = None
    active_side: int = 0
    active_entry: Optional[float] = None
    active_sl: Optional[float] = None
    active_tp_at_entry: Optional[float] = None
    active_entry_day: Optional[int] = None
    tp_order_oid: Optional[int] = None
    sl_order_oid: Optional[int] = None
    strategy_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, obj: dict) -> "RuntimeState":
        allowed = cls.__dataclass_fields__.keys()
        data = {k: obj.get(k) for k in allowed if k in obj}
        if not isinstance(data.get("strategy_data", {}), dict):
            data["strategy_data"] = {}
        return cls(**data)
