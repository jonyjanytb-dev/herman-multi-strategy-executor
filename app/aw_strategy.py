from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .config import Config
from .models import Candle, RuntimeState, Signal

NY = ZoneInfo("America/New_York")
MINUTE_MS = 60_000


def _minute_of_day_utc(ms: int) -> int:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.hour * 60 + dt.minute


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _in_clock_range(minute: int, start: int, end: int) -> bool:
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


class AWLiquidityReversalStrategy:
    """
    Independent Python implementation of the public AW Model liquidity-reversal
    rules. The upstream Pine source is referenced in THIRD_PARTY_NOTICES.md and
    is not redistributed in this repository.
    """

    name = "aw_liquidity"
    required_lookback = 3000

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._last_status = "AW warmup"

    def _session_allowed(self, bar_open_ms: int) -> bool:
        dt = datetime.fromtimestamp(bar_open_ms / 1000, tz=NY)
        minute = dt.hour * 60 + dt.minute
        return (
            (self.cfg.aw_use_asia and _in_clock_range(minute, 20 * 60, 2 * 60))
            or (self.cfg.aw_use_london and _in_clock_range(minute, 2 * 60, 8 * 60))
            or (self.cfg.aw_use_nyam and _in_clock_range(minute, 8 * 60, 12 * 60))
            or (self.cfg.aw_use_nypm and _in_clock_range(minute, 12 * 60, 16 * 60))
        )

    @staticmethod
    def _push_liquidity(levels: list[dict], price: float, source: str, max_levels: int) -> None:
        levels.append({"price": float(price), "taken": False, "source": source})
        if len(levels) > max_levels:
            del levels[0]

    @staticmethod
    def _nearest_untaken_above(levels: list[dict], price: float) -> Optional[float]:
        candidates = [float(x["price"]) for x in levels if not x["taken"] and float(x["price"]) > price]
        return min(candidates) if candidates else None

    @staticmethod
    def _nearest_untaken_below(levels: list[dict], price: float) -> Optional[float]:
        candidates = [float(x["price"]) for x in levels if not x["taken"] and float(x["price"]) < price]
        return max(candidates) if candidates else None

    @staticmethod
    def _pivot_high(candles: list[Candle], center: int, strength: int) -> bool:
        if center - strength < 0 or center + strength >= len(candles):
            return False
        window = candles[center - strength:center + strength + 1]
        return candles[center].h == max(x.h for x in window)

    @staticmethod
    def _pivot_low(candles: list[Candle], center: int, strength: int) -> bool:
        if center - strength < 0 or center + strength >= len(candles):
            return False
        window = candles[center - strength:center + strength + 1]
        return candles[center].l == min(x.l for x in window)

    @staticmethod
    def _empty_leg() -> dict:
        return {
            "active": False,
            "mss": False,
            "fvg_set": False,
            "triggered": False,
            "neckline": None,
            "swept": None,
            "sweep_bar": None,
            "mss_bar": None,
            "fvg_top": None,
            "fvg_bot": None,
            "fvg_bar": None,
        }

    def _replay(self, candles: list[Candle]) -> tuple[Optional[Signal], str]:
        if not candles:
            return None, "AW warmup"

        swing = self.cfg.aw_swing_length
        max_liq = self.cfg.aw_max_liquidity
        atr_len = self.cfg.aw_atr_length

        liq_high: list[dict] = []
        liq_low: list[dict] = []
        last_pivot_high: Optional[float] = None
        last_pivot_high_bar: Optional[int] = None
        last_pivot_low: Optional[float] = None
        last_pivot_low_bar: Optional[int] = None

        bull = self._empty_leg()
        bear = self._empty_leg()
        live_long: Optional[dict] = None
        live_short: Optional[dict] = None
        latest_signal: Optional[Signal] = None

        atr: Optional[float] = None
        atr_seed: list[float] = []
        prev_close: Optional[float] = None

        htf_minutes = self.cfg.aw_htf_minutes
        htf_ms = htf_minutes * MINUTE_MS
        htf_buf: list[Candle] = []
        htf_candles: list[Candle] = []

        day_key: Optional[str] = None
        day_high: Optional[float] = None
        day_low: Optional[float] = None
        day_first_minute: Optional[int] = None
        day_last_minute: Optional[int] = None
        day_count = 0

        for i, c in enumerate(candles):
            # Pine ta.atr() uses Wilder RMA of true range.
            tr = c.h - c.l if prev_close is None else max(c.h - c.l, abs(c.h - prev_close), abs(c.l - prev_close))
            prev_close = c.c
            if atr is None:
                atr_seed.append(tr)
                if len(atr_seed) >= atr_len:
                    atr = sum(atr_seed[-atr_len:]) / atr_len
            else:
                atr = (atr * (atr_len - 1) + tr) / atr_len

            # Resolve virtual trades before evaluating new entries, matching the
            # upstream tracker. Stop wins if stop and target are touched together.
            if live_long is not None:
                if c.l <= float(live_long["stop"]) or c.h >= float(live_long["target"]):
                    live_long = None
            if live_short is not None:
                if c.h >= float(live_short["stop"]) or c.l <= float(live_short["target"]):
                    live_short = None

            # Confirm chart-timeframe pivots after the configured right-side lag.
            center = i - swing
            if center >= swing:
                if self._pivot_high(candles, center, swing):
                    last_pivot_high = candles[center].h
                    last_pivot_high_bar = center
                    self._push_liquidity(liq_high, last_pivot_high, "ltf", max_liq)
                if self._pivot_low(candles, center, swing):
                    last_pivot_low = candles[center].l
                    last_pivot_low_bar = center
                    self._push_liquidity(liq_low, last_pivot_low, "ltf", max_liq)

            # The original marks already-known liquidity as taken before adding
            # HTF / previous-day sources on this bar.
            for level in liq_high:
                if not level["taken"] and c.h > float(level["price"]):
                    level["taken"] = True
            for level in liq_low:
                if not level["taken"] and c.l < float(level["price"]):
                    level["taken"] = True

            # 1m engine -> default upstream auto pairing is 15m. Aggregate only
            # fully closed higher-timeframe bars.
            if self.cfg.aw_use_htf_liquidity:
                htf_buf.append(c)
                if (c.t + MINUTE_MS) % htf_ms == 0:
                    if htf_buf:
                        htf = Candle(
                            htf_buf[0].t,
                            htf_buf[0].o,
                            max(x.h for x in htf_buf),
                            min(x.l for x in htf_buf),
                            htf_buf[-1].c,
                            sum(x.v for x in htf_buf),
                        )
                        htf_candles.append(htf)
                        htf_buf = []
                        hs = self.cfg.aw_htf_pivot_strength
                        hcenter = len(htf_candles) - 1 - hs
                        if hcenter >= hs:
                            if self._pivot_high(htf_candles, hcenter, hs):
                                self._push_liquidity(liq_high, htf_candles[hcenter].h, "htf", max_liq)
                            if self._pivot_low(htf_candles, hcenter, hs):
                                self._push_liquidity(liq_low, htf_candles[hcenter].l, "htf", max_liq)

            # Previous completed UTC day high/low. The first partial day in a
            # finite replay window is ignored unless it contains a full 24h.
            current_day = _utc_day(c.t)
            minute_utc = _minute_of_day_utc(c.t)
            if day_key is None:
                day_key = current_day
                day_high = c.h
                day_low = c.l
                day_first_minute = minute_utc
                day_last_minute = minute_utc
                day_count = 1
            elif current_day != day_key:
                full_day = day_first_minute == 0 and day_last_minute == 1439 and day_count >= 1400
                if self.cfg.aw_use_pdhl and full_day and day_high is not None and day_low is not None:
                    self._push_liquidity(liq_high, day_high, "pdh", max_liq)
                    self._push_liquidity(liq_low, day_low, "pdl", max_liq)
                day_key = current_day
                day_high = c.h
                day_low = c.l
                day_first_minute = minute_utc
                day_last_minute = minute_utc
                day_count = 1
            else:
                day_high = c.h if day_high is None else max(day_high, c.h)
                day_low = c.l if day_low is None else min(day_low, c.l)
                day_last_minute = minute_utc
                day_count += 1

            # Bullish reversal: sweep low -> displacement through neckline -> FVG -> entry.
            sweep_low = (
                not bull["active"]
                and last_pivot_low is not None
                and c.l < last_pivot_low
                and c.c > last_pivot_low
                and last_pivot_low_bar is not None
                and i > last_pivot_low_bar
            )
            if sweep_low:
                bull = self._empty_leg()
                bull.update(
                    active=True,
                    swept=c.l,
                    sweep_bar=i,
                    neckline=last_pivot_high,
                )

            if bull["active"] and not bull["mss"]:
                if i - int(bull["sweep_bar"]) > self.cfg.aw_max_bars_to_mss:
                    bull["active"] = False
                elif (
                    bull["neckline"] is not None
                    and atr is not None
                    and c.c > float(bull["neckline"])
                    and (c.c - c.o) >= atr * self.cfg.aw_displacement_mult
                    and c.c > c.o
                ):
                    bull["mss"] = True
                    bull["mss_bar"] = i

            if bull["active"] and bull["mss"] and not bull["fvg_set"]:
                if i - int(bull["mss_bar"]) > self.cfg.aw_fvg_search_window + 2:
                    bull["active"] = False
                elif i >= 2 and c.l > candles[i - 2].h:
                    bull["fvg_set"] = True
                    bull["fvg_top"] = c.l
                    bull["fvg_bot"] = candles[i - 2].h
                    bull["fvg_bar"] = i

            if bull["active"] and bull["fvg_set"] and not bull["triggered"]:
                if i - int(bull["fvg_bar"]) > self.cfg.aw_max_bars_to_entry:
                    bull["active"] = False
                elif c.c < float(bull["swept"]):
                    bull["active"] = False
                elif c.l <= float(bull["fvg_top"]) and live_long is None and self._session_allowed(c.t):
                    entry = min(float(bull["fvg_top"]), c.h)
                    stop = float(bull["swept"])
                    if self.cfg.aw_target_mode == "opposite_liquidity":
                        above = self._nearest_untaken_above(liq_high, c.c)
                        target = above if above is not None else entry + (entry - stop) * self.cfg.aw_target_r
                    else:
                        target = entry + (entry - stop) * self.cfg.aw_target_r
                    live_long = {"entry": entry, "stop": stop, "target": target}
                    bull["triggered"] = True
                    signal = Signal(
                        "LONG",
                        c.t,
                        entry,
                        target,
                        stop,
                        "AW bullish liquidity reversal",
                        {
                            "neckline": bull["neckline"],
                            "fvg_top": bull["fvg_top"],
                            "fvg_bot": bull["fvg_bot"],
                            "swept_liquidity": bull["swept"],
                            "target_mode": self.cfg.aw_target_mode,
                            "target_r": self.cfg.aw_target_r,
                        },
                    )
                    if i == len(candles) - 1 and latest_signal is None:
                        latest_signal = signal
                    bull["active"] = False

            # Bearish reversal mirrors the bullish state machine.
            sweep_high = (
                not bear["active"]
                and last_pivot_high is not None
                and c.h > last_pivot_high
                and c.c < last_pivot_high
                and last_pivot_high_bar is not None
                and i > last_pivot_high_bar
            )
            if sweep_high:
                bear = self._empty_leg()
                bear.update(
                    active=True,
                    swept=c.h,
                    sweep_bar=i,
                    neckline=last_pivot_low,
                )

            if bear["active"] and not bear["mss"]:
                if i - int(bear["sweep_bar"]) > self.cfg.aw_max_bars_to_mss:
                    bear["active"] = False
                elif (
                    bear["neckline"] is not None
                    and atr is not None
                    and c.c < float(bear["neckline"])
                    and (c.o - c.c) >= atr * self.cfg.aw_displacement_mult
                    and c.c < c.o
                ):
                    bear["mss"] = True
                    bear["mss_bar"] = i

            if bear["active"] and bear["mss"] and not bear["fvg_set"]:
                if i - int(bear["mss_bar"]) > self.cfg.aw_fvg_search_window + 2:
                    bear["active"] = False
                elif i >= 2 and c.h < candles[i - 2].l:
                    bear["fvg_set"] = True
                    bear["fvg_top"] = candles[i - 2].l
                    bear["fvg_bot"] = c.h
                    bear["fvg_bar"] = i

            if bear["active"] and bear["fvg_set"] and not bear["triggered"]:
                if i - int(bear["fvg_bar"]) > self.cfg.aw_max_bars_to_entry:
                    bear["active"] = False
                elif c.c > float(bear["swept"]):
                    bear["active"] = False
                elif c.h >= float(bear["fvg_bot"]) and live_short is None and self._session_allowed(c.t):
                    entry = max(float(bear["fvg_bot"]), c.l)
                    stop = float(bear["swept"])
                    if self.cfg.aw_target_mode == "opposite_liquidity":
                        below = self._nearest_untaken_below(liq_low, c.c)
                        target = below if below is not None else entry - (stop - entry) * self.cfg.aw_target_r
                    else:
                        target = entry - (stop - entry) * self.cfg.aw_target_r
                    live_short = {"entry": entry, "stop": stop, "target": target}
                    bear["triggered"] = True
                    signal = Signal(
                        "SHORT",
                        c.t,
                        entry,
                        target,
                        stop,
                        "AW bearish liquidity reversal",
                        {
                            "neckline": bear["neckline"],
                            "fvg_top": bear["fvg_top"],
                            "fvg_bot": bear["fvg_bot"],
                            "swept_liquidity": bear["swept"],
                            "target_mode": self.cfg.aw_target_mode,
                            "target_r": self.cfg.aw_target_r,
                        },
                    )
                    if i == len(candles) - 1 and latest_signal is None:
                        latest_signal = signal
                    bear["active"] = False

        untaken_high = sum(1 for x in liq_high if not x["taken"])
        untaken_low = sum(1 for x in liq_low if not x["taken"])
        phases = []
        if bull["active"]:
            phases.append("BULL " + ("FVG" if bull["fvg_set"] else "SHIFT" if bull["mss"] else "SWEEP"))
        if bear["active"]:
            phases.append("BEAR " + ("FVG" if bear["fvg_set"] else "SHIFT" if bear["mss"] else "SWEEP"))
        phase = " / ".join(phases) if phases else "等待 setup"
        status = f"AW {phase} | untaken H={untaken_high} L={untaken_low}"
        return latest_signal, status

    def evaluate(self, candles: list[Candle], state: RuntimeState, flat: bool) -> Optional[Signal]:
        signal, self._last_status = self._replay(candles)
        if signal is None or not flat:
            return None
        if signal.side == "LONG" and not self.cfg.enable_longs:
            return None
        if signal.side == "SHORT" and not self.cfg.enable_shorts:
            return None
        if state.last_entry_bar == signal.bar_time or state.last_exit_bar == signal.bar_time:
            return None
        return signal

    def protection_after_fill(self, signal: Signal, fill_price: float) -> tuple[float, float]:
        if signal.initial_tp is None:
            raise RuntimeError("AW signal is missing TP")
        direction = 1 if signal.side == "LONG" else -1
        if direction * (fill_price - signal.sl) <= 0:
            raise RuntimeError("AW fill invalidated the sweep stop; refuse unprotected trade")
        if direction * (float(signal.initial_tp) - fill_price) <= 0:
            raise RuntimeError("AW fill invalidated the planned target; refuse unprotected trade")
        return float(signal.initial_tp), signal.sl

    def current_dynamic_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]:
        return None

    def should_hard_flat(self, candle: Candle, state: RuntimeState) -> bool:
        return False

    def status_text(self, candles: list[Candle], state: RuntimeState) -> str:
        return self._last_status
