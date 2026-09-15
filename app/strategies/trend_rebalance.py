from __future__ import annotations

from statistics import fmean
from typing import Optional

from .base import BaseStrategy
from ..config import Config
from ..models import Candle, RuntimeState, Signal


class TrendRebalanceStrategy(BaseStrategy):
    id = "trend_rebalance"
    label = "1.0 Trend Rebalance Map"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @staticmethod
    def _sma(closes: list[float], length: int) -> float:
        return fmean(closes[-length:])

    def evaluate(self, candles: list[Candle], state: RuntimeState, flat: bool) -> Optional[Signal]:
        need = max(self.cfg.sma200_length, self.cfg.sma50_length) + 1
        if len(candles) < need:
            return None
        current = candles[-1]
        closes = [x.c for x in candles]
        sma50 = self._sma(closes, self.cfg.sma50_length)
        sma50_prev = fmean(closes[-self.cfg.sma50_length - 1:-1])
        sma200 = self._sma(closes, self.cfg.sma200_length)
        bullish_cross = closes[-1] > sma50 and closes[-2] <= sma50_prev
        bearish_cross = closes[-1] < sma50 and closes[-2] >= sma50_prev
        long_direction = sma50 < sma200
        short_direction = sma50 > sma200
        separation = abs(sma50 - sma200) / self.cfg.point_size
        separation_pass = separation > self.cfg.min_separation
        can_enter = (
            flat
            and (state.last_entry_bar is None or current.t != state.last_entry_bar)
            and (state.last_exit_bar is None or current.t != state.last_exit_bar)
        )
        side = None
        if self.cfg.enable_longs and bullish_cross and long_direction and separation_pass and can_enter:
            side = "LONG"
        elif self.cfg.enable_shorts and bearish_cross and short_direction and separation_pass and can_enter:
            side = "SHORT"
        if side is None:
            return None
        direction = 1 if side == "LONG" else -1
        entry = current.c
        initial_tp = sma200 if self.cfg.tp_mode == "200 SMA" else entry + direction * self.cfg.tp_fixed_points * self.cfg.point_size
        distance = abs(initial_tp - entry)
        sl = (
            entry - direction * distance
            if self.cfg.sl_mode == "1R to TP"
            else entry - direction * self.cfg.sl_fixed_points * self.cfg.point_size
        )
        return Signal(
            side=side,
            bar_time=current.t,
            entry_reference=entry,
            sl=sl,
            strategy_id=self.id,
            initial_tp=initial_tp,
            meta={"sma50": sma50, "sma200": sma200, "separation": separation},
        )

    def protection_after_fill(self, signal: Signal, fill_price: float) -> tuple[float, float]:
        if signal.initial_tp is None:
            raise RuntimeError("Trend signal is missing initial TP")
        return signal.initial_tp, signal.sl

    def current_dynamic_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]:
        if state.active_side == 0 or self.cfg.tp_mode != "200 SMA" or self.cfg.sma_target_behaviour != "Dynamic":
            return None
        if len(candles) < self.cfg.sma200_length:
            return None
        return fmean([x.c for x in candles][-self.cfg.sma200_length:])

    def can_rebuild_missing_tp(self, candles: list[Candle], state: RuntimeState) -> Optional[float]:
        if self.cfg.tp_mode != "200 SMA" or self.cfg.sma_target_behaviour != "Dynamic":
            return None
        if len(candles) < self.cfg.sma200_length:
            return None
        return fmean([x.c for x in candles][-self.cfg.sma200_length:])

    def heartbeat(self, candles: list[Candle], state: RuntimeState, position_size: float, has_signal: bool) -> str:
        bar = candles[-1]
        closes = [x.c for x in candles]
        if len(closes) < self.cfg.sma200_length:
            return f"{self.cfg.market_symbol}={bar.c:.2f} | SMA warmup {len(closes)}/{self.cfg.sma200_length} | 等待数据"
        sma50 = fmean(closes[-self.cfg.sma50_length:])
        sma200 = fmean(closes[-self.cfg.sma200_length:])
        separation = abs(sma50 - sma200) / self.cfg.point_size
        status = "持有 LONG" if position_size > 0 else "持有 SHORT" if position_size < 0 else "触发信号" if has_signal else "等待信号"
        return f"{self.cfg.market_symbol}={bar.c:.2f} | SMA50={sma50:.2f} | SMA200={sma200:.2f} | Sep={separation:.2f} | {status}"
