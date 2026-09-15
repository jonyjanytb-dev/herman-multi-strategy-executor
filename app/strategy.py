from __future__ import annotations

from datetime import datetime, time as dtime
from statistics import fmean
from typing import Optional
from zoneinfo import ZoneInfo

from .config import Config
from .models import Candle, RuntimeState, Signal

NY = ZoneInfo("America/New_York")


def _sma(values: list[float], length: int) -> float:
    return fmean(values[-length:])


def _parse_hhmm(value: str) -> dtime:
    hh, mm = value.split(":", 1)
    return dtime(int(hh), int(mm))


def _in_session(close_ms: int, start: str, end: str) -> bool:
    now = datetime.fromtimestamp(close_ms / 1000, tz=NY).time().replace(tzinfo=None)
    a = _parse_hhmm(start)
    b = _parse_hhmm(end)
    if a == b:
        return True
    return a <= now < b if a < b else (now >= a or now < b)


def _day_key(close_ms: int) -> str:
    return datetime.fromtimestamp(close_ms / 1000, tz=NY).strftime("%Y-%m-%d")


def _aggregate(candles: list[Candle], minutes: int) -> list[Candle]:
    if minutes == 1:
        return candles
    bucket_ms = minutes * 60_000
    groups: dict[int, list[Candle]] = {}
    for c in candles:
        bucket = (c.t // bucket_ms) * bucket_ms
        groups.setdefault(bucket, []).append(c)
    out: list[Candle] = []
    for bucket in sorted(groups):
        xs = sorted(groups[bucket], key=lambda x: x.t)
        if len(xs) != minutes:
            continue
        if xs[-1].t != bucket + (minutes - 1) * 60_000:
            continue
        out.append(Candle(bucket, xs[0].o, max(x.h for x in xs), min(x.l for x in xs), xs[-1].c, sum(x.v for x in xs)))
    return out


def _true_ranges(candles: list[Candle]) -> list[float]:
    out: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.h - c.l)
        else:
            prev = candles[i - 1].c
            out.append(max(c.h - c.l, abs(c.h - prev), abs(c.l - prev)))
    return out


class TrendRebalanceStrategy:
    name = "trend_rebalance"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def evaluate(self, candles: list[Candle], state: RuntimeState, flat: bool) -> Optional[Signal]:
        need = max(self.cfg.trend_sma200_length, self.cfg.trend_sma50_length) + 1
        if len(candles) < need:
            return None
        current = candles[-1]
        closes = [x.c for x in candles]
        sma50 = _sma(closes, self.cfg.trend_sma50_length)
        sma50_prev = fmean(closes[-self.cfg.trend_sma50_length - 1:-1])
        sma200 = _sma(closes, self.cfg.trend_sma200_length)
        bullish_cross = closes[-1] > sma50 and closes[-2] <= sma50_prev
        bearish_cross = closes[-1] < sma50 and closes[-2] >= sma50_prev
        separation = abs(sma50 - sma200) / self.cfg.trend_point_size
        pass_sep = separation > self.cfg.trend_min_separation
        can_enter = flat and state.last_entry_bar != current.t and state.last_exit_bar != current.t

        if self.cfg.enable_longs and can_enter and bullish_cross and sma50 < sma200 and pass_sep:
            entry = current.c
            tp = sma200 if self.cfg.trend_tp_mode == "200 SMA" else entry + self.cfg.trend_tp_fixed_points * self.cfg.trend_point_size
            distance = abs(tp - entry)
            sl = entry - distance if self.cfg.trend_sl_mode == "1R to TP" else entry - self.cfg.trend_sl_fixed_points * self.cfg.trend_point_size
            return Signal("LONG", current.t, entry, tp, sl, "LongSMA", {"sma50": sma50, "sma200": sma200, "separation": separation})

        if self.cfg.enable_shorts and can_enter and bearish_cross and sma50 > sma200 and pass_sep:
            entry = current.c
            tp = sma200 if self.cfg.trend_tp_mode == "200 SMA" else entry - self.cfg.trend_tp_fixed_points * self.cfg.trend_point_size
            distance = abs(entry - tp)
            sl = entry + distance if self.cfg.trend_sl_mode == "1R to TP" else entry + self.cfg.trend_sl_fixed_points * self.cfg.trend_point_size
            return Signal("SHORT", current.t, entry, tp, sl, "ShortSMA", {"sma50": sma50, "sma200": sma200, "separation": separation})
        return None

    def current_dynamic_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]:
        if state.active_side == 0:
            return None
        if self.cfg.trend_tp_mode != "200 SMA" or self.cfg.trend_sma_target_behaviour != "Dynamic":
            return None
        if len(candles) < self.cfg.trend_sma200_length:
            return None
        return _sma([x.c for x in candles], self.cfg.trend_sma200_length)

    def protection_after_fill(self, signal: Signal, fill_price: float) -> tuple[float, float]:
        if signal.initial_tp is None:
            raise RuntimeError("Trend signal is missing TP")
        return signal.initial_tp, signal.sl

    def should_hard_flat(self, candle: Candle, state: RuntimeState) -> bool:
        return False

    def status_text(self, candles: list[Candle], state: RuntimeState) -> str:
        if len(candles) < self.cfg.trend_sma200_length:
            return f"SMA warmup {len(candles)}/{self.cfg.trend_sma200_length}"
        closes = [x.c for x in candles]
        s50 = _sma(closes, self.cfg.trend_sma50_length)
        s200 = _sma(closes, self.cfg.trend_sma200_length)
        sep = abs(s50 - s200) / self.cfg.trend_point_size
        return f"SMA50={s50:.2f} SMA200={s200:.2f} Sep={sep:.2f}"


class StreakFailureReversalStrategy:
    name = "streak_failure"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _signal_candles(self, candles: list[Candle]) -> list[Candle]:
        return _aggregate(candles, 5 if self.cfg.streak_signal_timeframe == "5m" else 1)

    def _is_bull(self, sig: list[Candle], i: int) -> bool:
        if self.cfg.streak_definition == "higher_lower_closes":
            return i > 0 and sig[i].c > sig[i - 1].c
        return sig[i].c > sig[i].o

    def _is_bear(self, sig: list[Candle], i: int) -> bool:
        if self.cfg.streak_definition == "higher_lower_closes":
            return i > 0 and sig[i].c < sig[i - 1].c
        return sig[i].c < sig[i].o

    def _counts(self, sig: list[Candle]) -> tuple[int, int]:
        up = dn = 0
        for i in range(len(sig)):
            if self._is_bull(sig, i):
                up += 1
            else:
                up = 0
            if self._is_bear(sig, i):
                dn += 1
            else:
                dn = 0
        return up, dn

    def evaluate(self, candles: list[Candle], state: RuntimeState, flat: bool) -> Optional[Signal]:
        sig = self._signal_candles(candles)
        need = max(self.cfg.streak_atr_length + 1, self.cfg.streak_length + 1)
        if len(sig) < need:
            return None
        cur = sig[-1]
        tf_ms = 300_000 if self.cfg.streak_signal_timeframe == "5m" else 60_000
        close_ms = cur.t + tf_ms
        ss = state.strategy_state
        if ss.get("last_signal_time") == cur.t:
            return None

        day = _day_key(close_ms)
        if ss.get("day") and ss.get("day") != day:
            ss.pop("arm_short", None)
            ss.pop("arm_long", None)
        ss["day"] = day
        ss["last_signal_time"] = cur.t
        counter = int(ss.get("signal_counter", 0)) + 1
        ss["signal_counter"] = counter

        session_ok = (not self.cfg.streak_use_session_filter) or _in_session(close_ms, self.cfg.streak_session_start, self.cfg.streak_session_end)
        trigger_session_ok = (not self.cfg.streak_use_session_filter) or (not self.cfg.streak_trigger_must_be_in_session) or session_ok

        arm_short = dict(ss.get("arm_short") or {})
        arm_long = dict(ss.get("arm_long") or {})

        if arm_short:
            arm_short["running_high"] = max(float(arm_short["running_high"]), cur.h)
            if counter - int(arm_short["counter"]) > self.cfg.streak_wait_bars:
                arm_short = {}
        if arm_long:
            arm_long["running_low"] = min(float(arm_long["running_low"]), cur.l)
            if counter - int(arm_long["counter"]) > self.cfg.streak_wait_bars:
                arm_long = {}

        short_age = counter - int(arm_short["counter"]) if arm_short else 0
        long_age = counter - int(arm_long["counter"]) if arm_long else 0
        short_break = bool(arm_short and 1 <= short_age <= self.cfg.streak_wait_bars and cur.c < float(arm_short["break_level"]))
        long_break = bool(arm_long and 1 <= long_age <= self.cfg.streak_wait_bars and cur.c > float(arm_long["break_level"]))

        direction = 0
        chosen: dict = {}
        if short_break and long_break:
            if int(arm_short["counter"]) > int(arm_long["counter"]):
                direction, chosen = -1, arm_short
            else:
                direction, chosen = 1, arm_long
        elif short_break:
            direction, chosen = -1, arm_short
        elif long_break:
            direction, chosen = 1, arm_long

        # A first break consumes the setup even if an entry filter rejects it.
        if short_break:
            arm_short = {}
        if long_break:
            arm_long = {}

        signal: Optional[Signal] = None
        enabled = (direction > 0 and self.cfg.enable_longs) or (direction < 0 and self.cfg.enable_shorts)
        if direction and flat and enabled and trigger_session_ok and not self._after_hard_flat(close_ms):
            trs = _true_ranges(sig)
            atr = fmean(trs[-self.cfg.streak_atr_length:])
            if self.cfg.streak_sl_mode == "trigger":
                raw_stop = cur.l if direction > 0 else cur.h
            elif self.cfg.streak_sl_mode == "terminal":
                raw_stop = float(chosen["terminal_low"] if direction > 0 else chosen["terminal_high"])
            elif self.cfg.streak_sl_mode == "whole_streak":
                raw_stop = float(chosen["streak_low"] if direction > 0 else chosen["streak_high"])
            elif self.cfg.streak_sl_mode == "whole_move":
                raw_stop = float(chosen["running_low"] if direction > 0 else chosen["running_high"])
            else:
                raw_stop = cur.c - direction * atr * self.cfg.streak_atr_mult
            sl = raw_stop - direction * self.cfg.streak_stop_buffer_points
            est_risk = direction * (cur.c - sl)
            if est_risk > 0:
                structural_tp: Optional[float] = None
                if self.cfg.streak_tp_mode == "first_extreme":
                    structural_tp = float(chosen["first_high"] if direction > 0 else chosen["first_low"])
                elif self.cfg.streak_tp_mode == "first_open":
                    structural_tp = float(chosen["first_open"])
                structural_valid = structural_tp is not None and direction * (structural_tp - cur.c) > 0
                if self.cfg.streak_tp_mode == "r_multiple" or self.cfg.streak_invalid_target_policy == "use_r" or structural_valid:
                    signal = Signal(
                        "LONG" if direction > 0 else "SHORT",
                        cur.t,
                        cur.c,
                        structural_tp,
                        sl,
                        "Bear streak failure LONG" if direction > 0 else "Bull streak failure SHORT",
                        {
                            "direction": direction,
                            "tp_mode": self.cfg.streak_tp_mode,
                            "tp_r": self.cfg.streak_tp_r,
                            "invalid_target_policy": self.cfg.streak_invalid_target_policy,
                            "structural_tp": structural_tp,
                            "signal_close_ms": close_ms,
                        },
                    )

        # Last allowed candle was checked above; expire only after giving it a chance.
        if arm_short and counter - int(arm_short["counter"]) >= self.cfg.streak_wait_bars:
            arm_short = {}
        if arm_long and counter - int(arm_long["counter"]) >= self.cfg.streak_wait_bars:
            arm_long = {}

        # New setups are armed after older setups had their chance on this candle.
        up, dn = self._counts(sig)
        can_arm = flat and session_ok and not self._after_hard_flat(close_ms)
        streak_slice = sig[-self.cfg.streak_length:]
        first = streak_slice[0]
        if can_arm and up == self.cfg.streak_length and self.cfg.enable_shorts:
            arm_short = {
                "counter": counter,
                "break_level": cur.l,
                "terminal_high": cur.h,
                "streak_high": max(x.h for x in streak_slice),
                "running_high": max(x.h for x in streak_slice),
                "first_low": first.l,
                "first_open": first.o,
            }
        if can_arm and dn == self.cfg.streak_length and self.cfg.enable_longs:
            arm_long = {
                "counter": counter,
                "break_level": cur.h,
                "terminal_low": cur.l,
                "streak_low": min(x.l for x in streak_slice),
                "running_low": min(x.l for x in streak_slice),
                "first_high": first.h,
                "first_open": first.o,
            }

        ss["arm_short"] = arm_short
        ss["arm_long"] = arm_long
        return signal

    def _after_hard_flat(self, close_ms: int) -> bool:
        if not self.cfg.streak_hard_flat:
            return False
        now = datetime.fromtimestamp(close_ms / 1000, tz=NY)
        cutoff = _parse_hhmm(self.cfg.streak_hard_flat_time)
        return now.time().replace(tzinfo=None) >= cutoff

    def should_hard_flat(self, candle: Candle, state: RuntimeState) -> bool:
        return state.active_side != 0 and self._after_hard_flat(candle.t + 60_000)

    def protection_after_fill(self, signal: Signal, fill_price: float) -> tuple[float, float]:
        direction = 1 if signal.side == "LONG" else -1
        risk = direction * (fill_price - signal.sl)
        if risk <= 0:
            raise RuntimeError("Streak fill invalidated the structural stop; refuse unprotected trade")
        structural = signal.meta.get("structural_tp")
        structural_valid = structural is not None and direction * (float(structural) - fill_price) > 0
        if signal.meta.get("tp_mode") == "r_multiple" or (not structural_valid and signal.meta.get("invalid_target_policy") == "use_r"):
            tp = fill_price + direction * risk * float(signal.meta.get("tp_r", 1.0))
        elif structural_valid:
            tp = float(structural)
        else:
            raise RuntimeError("Streak fill invalidated structural TP")
        return tp, signal.sl

    def current_dynamic_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]:
        return None

    def status_text(self, candles: list[Candle], state: RuntimeState) -> str:
        ss = state.strategy_state
        a_l = bool(ss.get("arm_long"))
        a_s = bool(ss.get("arm_short"))
        if a_l or a_s:
            labels = []
            if a_l:
                labels.append("ARM LONG")
            if a_s:
                labels.append("ARM SHORT")
            return " / ".join(labels)
        return f"Streak {self.cfg.streak_length} | window {self.cfg.streak_wait_bars} | 等待 setup"


def build_strategy(cfg: Config):
    if cfg.strategy == "streak_failure":
        return StreakFailureReversalStrategy(cfg)
    return TrendRebalanceStrategy(cfg)
