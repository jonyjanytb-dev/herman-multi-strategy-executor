from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from zoneinfo import ZoneInfo

from .base import BaseStrategy, InvalidProtection
from ..config import Config
from ..models import Candle, RuntimeState, Signal


MINUTE_MS = 60_000
TZ = ZoneInfo("America/New_York")


class StreakFailureStrategy(BaseStrategy):
    id = "streak_failure"
    label = "1.1 Streak Failure Reversal"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @staticmethod
    def _close_ms(candle: Candle) -> int:
        return candle.T + 1

    @staticmethod
    def _day(ts_ms: int) -> int:
        dt = datetime.fromtimestamp(ts_ms / 1000, TZ)
        return dt.year * 10000 + dt.month * 100 + dt.day

    @staticmethod
    def _minutes(ts_ms: int) -> int:
        dt = datetime.fromtimestamp(ts_ms / 1000, TZ)
        return dt.hour * 60 + dt.minute

    @staticmethod
    def _hhmm(value: str) -> int:
        h, m = value.split(":", 1)
        return int(h) * 60 + int(m)

    def _in_session(self, close_ms: int) -> bool:
        m = self._minutes(close_ms)
        start = self._hhmm(self.cfg.streak_session_start)
        end = self._hhmm(self.cfg.streak_session_end)
        if start == end:
            return True
        return start <= m < end if start < end else (m >= start or m < end)

    def _after_hard_flat(self, close_ms: int) -> bool:
        if not self.cfg.streak_hard_flat_enabled:
            return False
        return self._minutes(close_ms) >= self._hhmm(self.cfg.streak_hard_flat)

    def _signal_candles(self, candles: list[Candle]) -> list[Candle]:
        if self.cfg.streak_signal_timeframe == "1m":
            return candles
        bucket_ms = 5 * MINUTE_MS
        groups: dict[int, list[Candle]] = {}
        for candle in candles:
            bucket = (candle.t // bucket_ms) * bucket_ms
            groups.setdefault(bucket, []).append(candle)
        out: list[Candle] = []
        for bucket in sorted(groups):
            rows = sorted(groups[bucket], key=lambda c: c.t)
            expected = [bucket + i * MINUTE_MS for i in range(5)]
            if len(rows) != 5 or [x.t for x in rows] != expected:
                continue
            out.append(Candle(bucket, bucket + bucket_ms - 1, rows[0].o, max(x.h for x in rows), min(x.l for x in rows), rows[-1].c, sum(x.v for x in rows)))
        return out

    def _is_bull(self, rows: list[Candle], idx: int) -> bool:
        cur = rows[idx]
        if self.cfg.streak_definition == "higher_lower_closes":
            return idx > 0 and cur.c > rows[idx - 1].c
        return cur.c > cur.o

    def _is_bear(self, rows: list[Candle], idx: int) -> bool:
        cur = rows[idx]
        if self.cfg.streak_definition == "higher_lower_closes":
            return idx > 0 and cur.c < rows[idx - 1].c
        return cur.c < cur.o

    def _count_back(self, rows: list[Candle], idx: int, bullish: bool) -> int:
        count = 0
        while idx >= 0:
            passed = self._is_bull(rows, idx) if bullish else self._is_bear(rows, idx)
            if not passed:
                break
            count += 1
            idx -= 1
        return count

    @staticmethod
    def _true_range(rows: list[Candle], idx: int) -> float:
        cur = rows[idx]
        if idx == 0:
            return cur.h - cur.l
        prev = rows[idx - 1].c
        return max(cur.h - cur.l, abs(cur.h - prev), abs(cur.l - prev))

    def _atr(self, rows: list[Candle], idx: int) -> Optional[float]:
        n = self.cfg.streak_atr_length
        if idx + 1 < n:
            return None
        vals = [self._true_range(rows, i) for i in range(idx - n + 1, idx + 1)]
        return sum(vals) / n

    @staticmethod
    def _clear_arms(data: dict) -> None:
        data["arm_short"] = False
        data["arm_long"] = False

    @staticmethod
    def _find_index(rows: list[Candle], t: Optional[int]) -> Optional[int]:
        if t is None:
            return None
        for i in range(len(rows) - 1, -1, -1):
            if rows[i].t == t:
                return i
        return None

    def _floor_tick(self, price: float) -> float:
        tick = self.cfg.streak_tick_size
        return math.floor(price / tick + 1e-9) * tick

    def _ceil_tick(self, price: float) -> float:
        tick = self.cfg.streak_tick_size
        return math.ceil(price / tick - 1e-9) * tick

    def _round_tick(self, price: float) -> float:
        tick = Decimal(str(self.cfg.streak_tick_size))
        units = (Decimal(str(price)) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return float(units * tick)

    def _current_age(self, rows: list[Candle], setup_t: Optional[int], current_idx: int) -> int:
        setup_idx = self._find_index(rows, setup_t)
        if setup_idx is None:
            return self.cfg.streak_wait_bars + 1
        return current_idx - setup_idx

    def evaluate(self, candles: list[Candle], state: RuntimeState, flat: bool) -> Optional[Signal]:
        rows = self._signal_candles(candles)
        need = max(self.cfg.streak_atr_length, self.cfg.streak_length) + 1
        if len(rows) < need:
            return None
        current_idx = len(rows) - 1
        cur = rows[current_idx]
        data = state.strategy_data
        if data.get("last_signal_t") == cur.t:
            return None

        close_ms = self._close_ms(cur)
        current_day = self._day(close_ms)
        last_day = data.get("last_logic_day")
        if last_day is not None and last_day != current_day:
            self._clear_arms(data)
        data["last_logic_day"] = current_day
        data["last_signal_t"] = cur.t

        after_hard_flat = self._after_hard_flat(close_ms)
        if after_hard_flat:
            self._clear_arms(data)

        setup_session_ok = (not self.cfg.streak_use_session_filter) or self._in_session(close_ms)
        entry_session_ok = (not self.cfg.streak_use_session_filter) or (not self.cfg.streak_trigger_must_be_in_session) or self._in_session(close_ms)
        warmup_ok = current_idx >= max(self.cfg.streak_atr_length, self.cfg.streak_length - 1) and self._atr(rows, current_idx) is not None

        short_age = self._current_age(rows, data.get("short_setup_t"), current_idx) if data.get("arm_short") else 0
        long_age = self._current_age(rows, data.get("long_setup_t"), current_idx) if data.get("arm_long") else 0

        if data.get("arm_short"):
            data["short_running_high"] = max(float(data["short_running_high"]), cur.h)
            if short_age > self.cfg.streak_wait_bars:
                data["arm_short"] = False
        if data.get("arm_long"):
            data["long_running_low"] = min(float(data["long_running_low"]), cur.l)
            if long_age > self.cfg.streak_wait_bars:
                data["arm_long"] = False

        short_break = bool(data.get("arm_short") and 1 <= short_age <= self.cfg.streak_wait_bars and cur.c < float(data["short_break_level"]))
        long_break = bool(data.get("arm_long") and 1 <= long_age <= self.cfg.streak_wait_bars and cur.c > float(data["long_break_level"]))

        direction = 0
        if short_break and long_break:
            direction = -1 if int(data.get("short_setup_t", 0)) > int(data.get("long_setup_t", 0)) else 1
        elif short_break:
            direction = -1
        elif long_break:
            direction = 1

        signal: Optional[Signal] = None
        if direction != 0:
            enabled = self.cfg.enable_longs if direction > 0 else self.cfg.enable_shorts
            if flat and enabled and entry_session_ok and not after_hard_flat and warmup_ok:
                atr = self._atr(rows, current_idx)
                if self.cfg.streak_sl_mode == "Trigger candle extreme":
                    raw_stop = cur.l if direction > 0 else cur.h
                elif self.cfg.streak_sl_mode == "Terminal streak candle extreme":
                    raw_stop = float(data["long_terminal_low"] if direction > 0 else data["short_terminal_high"])
                elif self.cfg.streak_sl_mode == "Whole streak extreme":
                    raw_stop = float(data["long_streak_low"] if direction > 0 else data["short_streak_high"])
                elif self.cfg.streak_sl_mode == "Whole move through trigger":
                    raw_stop = float(data["long_running_low"] if direction > 0 else data["short_running_high"])
                else:
                    assert atr is not None
                    raw_stop = cur.c - direction * atr * self.cfg.streak_atr_mult

                raw_stop -= direction * self.cfg.streak_stop_buffer_ticks * self.cfg.streak_tick_size
                planned_stop = self._floor_tick(raw_stop) if direction > 0 else self._ceil_tick(raw_stop)
                estimated_fill = cur.c + direction * self.cfg.streak_sizing_slippage_ticks * self.cfg.streak_tick_size
                estimated_risk = direction * (estimated_fill - planned_stop)

                structural_tp = None
                if self.cfg.streak_tp_mode == "First streak candle extreme":
                    structural_tp = float(data["long_first_high"] if direction > 0 else data["short_first_low"])
                elif self.cfg.streak_tp_mode == "First streak candle open":
                    structural_tp = float(data["long_first_open"] if direction > 0 else data["short_first_open"])

                stop_valid = estimated_risk >= self.cfg.streak_tick_size * 0.5
                structural_valid = structural_tp is not None and direction * (structural_tp - estimated_fill) >= self.cfg.streak_tick_size * 0.5
                target_valid = self.cfg.streak_tp_mode == "R multiple" or self.cfg.streak_invalid_target_policy == "Use R target" or structural_valid
                if stop_valid and target_valid:
                    if self.cfg.streak_sizing_mode == "Stop-risk budget":
                        notional = self.cfg.streak_max_risk_usd * estimated_fill / estimated_risk
                        notional = min(notional, self.cfg.streak_max_notional_usd)
                    else:
                        notional = self.cfg.order_notional_usdc
                    estimated_tp = estimated_fill + direction * estimated_risk * self.cfg.streak_tp_r if self.cfg.streak_tp_mode == "R multiple" or not structural_valid else structural_tp
                    signal = Signal(
                        side="LONG" if direction > 0 else "SHORT",
                        bar_time=cur.t,
                        entry_reference=cur.c,
                        sl=planned_stop,
                        strategy_id=self.id,
                        initial_tp=float(estimated_tp) if estimated_tp is not None else None,
                        order_notional=notional,
                        meta={"structural_tp": structural_tp, "trigger_close": cur.c, "signal_timeframe": self.cfg.streak_signal_timeframe},
                    )

            if short_break:
                data["arm_short"] = False
            if long_break:
                data["arm_long"] = False

        if data.get("arm_short") and short_age >= self.cfg.streak_wait_bars:
            data["arm_short"] = False
        if data.get("arm_long") and long_age >= self.cfg.streak_wait_bars:
            data["arm_long"] = False

        can_arm = flat and signal is None and not after_hard_flat and setup_session_ok and warmup_ok
        up = self._count_back(rows, current_idx, True)
        down = self._count_back(rows, current_idx, False)
        n = self.cfg.streak_length
        if can_arm and up == n and self.cfg.enable_shorts:
            streak = rows[current_idx - n + 1: current_idx + 1]
            first = streak[0]
            data.update(arm_short=True, short_setup_t=cur.t, short_break_level=cur.l, short_terminal_high=cur.h, short_streak_high=max(x.h for x in streak), short_running_high=max(x.h for x in streak), short_first_low=first.l, short_first_open=first.o)
        if can_arm and down == n and self.cfg.enable_longs:
            streak = rows[current_idx - n + 1: current_idx + 1]
            first = streak[0]
            data.update(arm_long=True, long_setup_t=cur.t, long_break_level=cur.h, long_terminal_low=cur.l, long_streak_low=min(x.l for x in streak), long_running_low=min(x.l for x in streak), long_first_high=first.h, long_first_open=first.o)
        return signal

    def protection_after_fill(self, signal: Signal, fill_price: float) -> tuple[float, float]:
        direction = 1 if signal.side == "LONG" else -1
        stop = signal.sl
        risk = direction * (fill_price - stop)
        if risk < self.cfg.streak_tick_size * 0.5:
            raise InvalidProtection("Actual fill invalidated the frozen structural stop")
        structural_tp = signal.meta.get("structural_tp")
        structural_valid = structural_tp is not None and direction * (float(structural_tp) - fill_price) >= self.cfg.streak_tick_size * 0.5
        use_r = self.cfg.streak_tp_mode == "R multiple" or (not structural_valid and self.cfg.streak_invalid_target_policy == "Use R target")
        if use_r:
            raw = fill_price + direction * risk * self.cfg.streak_tp_r
            tp = self._ceil_tick(raw) if direction > 0 else self._floor_tick(raw)
        elif structural_valid:
            tp = self._round_tick(float(structural_tp))
        else:
            raise InvalidProtection("Actual fill invalidated the frozen structural target")
        return tp, stop

    def should_force_flat(self, candles: list[Candle], state: RuntimeState, position_size: float) -> bool:
        if not self.cfg.streak_hard_flat_enabled or not candles:
            return False
        close_ms = self._close_ms(candles[-1])
        current_day = self._day(close_ms)
        if position_size != 0 and state.active_entry_day is not None and state.active_entry_day != current_day:
            return True
        return position_size != 0 and self._after_hard_flat(close_ms)

    def entry_day(self, candles: list[Candle]) -> Optional[int]:
        if not candles:
            return None
        return self._day(self._close_ms(candles[-1]))

    def heartbeat(self, candles: list[Candle], state: RuntimeState, position_size: float, has_signal: bool) -> str:
        bar = candles[-1]
        data = state.strategy_data
        status = "持有 LONG" if position_size > 0 else "持有 SHORT" if position_size < 0 else "触发反转" if has_signal else "等待 Streak/确认"
        armed = []
        if data.get("arm_long"):
            armed.append("ARM-L")
        if data.get("arm_short"):
            armed.append("ARM-S")
        arm_text = "/".join(armed) if armed else "NONE"
        return f"{self.cfg.market_symbol}={bar.c:.2f} | SignalTF={self.cfg.streak_signal_timeframe} | Armed={arm_text} | {status}"
