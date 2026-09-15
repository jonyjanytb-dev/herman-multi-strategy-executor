from __future__ import annotations

from typing import Optional, Protocol

from ..models import Candle, RuntimeState, Signal


class InvalidProtection(RuntimeError):
    """The actual fill made the intended structural protection invalid."""


class Strategy(Protocol):
    id: str
    label: str

    def evaluate(self, candles: list[Candle], state: RuntimeState, flat: bool) -> Optional[Signal]: ...
    def protection_after_fill(self, signal: Signal, fill_price: float) -> tuple[float, float]: ...
    def current_dynamic_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]: ...
    def can_rebuild_missing_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]: ...
    def should_force_flat(self, candles: list[Candle], state: RuntimeState, position_size: float) -> bool: ...
    def entry_day(self, candles: list[Candle]) -> Optional[int]: ...
    def heartbeat(self, candles: list[Candle], state: RuntimeState, position_size: float, has_signal: bool) -> str: ...


class BaseStrategy:
    id = "base"
    label = "Base"

    def current_dynamic_tp(self, candles, state):
        return None

    def can_rebuild_missing_tp(self, candles, state):
        return None

    def should_force_flat(self, candles, state, position_size):
        return False

    def entry_day(self, candles):
        return None
