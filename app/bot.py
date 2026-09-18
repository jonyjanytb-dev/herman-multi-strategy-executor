from __future__ import annotations

import logging
import signal
import time
from datetime import datetime
from typing import Optional

from .config import Config
from .executor import DryRunExecutor, build_executor
from .market_data import HyperliquidMarketData, OKXMarketData
from .models import RuntimeState, Signal
from .state import StateStore
from .strategy import build_strategy

log = logging.getLogger(__name__)


def build_market_data(cfg: Config):
    if cfg.exchange == "okx":
        return OKXMarketData(cfg.okx_inst_id, cfg.interval, base_url=cfg.okx_base_url)
    return HyperliquidMarketData(cfg.network, cfg.coin, cfg.interval)


class TradingBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.market = build_market_data(cfg)
        self.strategy = build_strategy(cfg)
        self.store = StateStore(cfg.state_path, cfg.strategy)
        self.state = self.store.load()
        self.executor = build_executor(cfg)
        self.running = True

    def stop(self, *_):
        self.running = False

    def _save(self) -> None:
        self.store.save(self.state)

    def _clear_active(self, bar_time: Optional[int] = None) -> None:
        self.state.active_side = 0
        self.state.active_entry = None
        self.state.active_sl = None
        self.state.active_tp_at_entry = None
        self.state.tp_order_oid = None
        self.state.sl_order_oid = None
        if bar_time is not None:
            self.state.last_exit_bar = bar_time
        self._save()

    def _wait_for_position(self, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pos = self.executor.position()
            if not pos.flat:
                return pos
            time.sleep(0.25)
        return self.executor.position()

    def _sync_position_state(self, candles) -> tuple[bool, float]:
        bar_time = candles[-1].t
        pos = self.executor.position()
        if self.state.active_side != 0 and pos.flat:
            log.info("Managed position closed; clearing remaining protection")
            self.executor.cancel_all_protection()
            self._clear_active(bar_time)
            return True, 0.0

        if self.state.active_side == 0 and not pos.flat:
            tp_oid, tp_px, sl_oid, sl_px = self.executor.recover_protection()
            if sl_px is None:
                raise RuntimeError("Existing live position has no recoverable SL; refusing to manage it")
            if tp_px is None:
                if self.cfg.strategy == "trend_rebalance":
                    dynamic = self.strategy.current_dynamic_tp(candles, RuntimeState(strategy=self.cfg.strategy, active_side=1 if pos.size > 0 else -1))
                    if dynamic is None:
                        raise RuntimeError("Existing Trend position has no TP and Dynamic SMA200 cannot be rebuilt")
                    tp_oid = self.executor.update_tp(pos.size, None, dynamic)
                    tp_px = dynamic
                else:
                    raise RuntimeError(f"Existing {self.cfg.strategy_label} position has no recoverable frozen TP; refusing reconstruction")
            self.state.active_side = 1 if pos.size > 0 else -1
            self.state.active_entry = pos.entry_px
            self.state.active_sl = sl_px
            self.state.active_tp_at_entry = tp_px
            self.state.tp_order_oid = tp_oid
            self.state.sl_order_oid = sl_oid
            self._save()
            log.info("Recovered position size=%s TP=%s SL=%s", pos.size, tp_px, sl_px)
        return pos.flat, pos.size

    def _hard_flat_if_needed(self, candles) -> bool:
        if not self.strategy.should_hard_flat(candles[-1], self.state):
            return False
        log.warning("Strategy hard-flat time reached; closing managed position")
        self.executor.cancel_all_protection()
        self.executor.close_market()
        pos = self._wait_for_position(timeout=5.0)
        if not pos.flat:
            raise RuntimeError("Hard-flat close was sent but position is still open")
        self._clear_active(candles[-1].t)
        return True

    def _place_new_trade(self, signal: Signal) -> None:
        is_buy = signal.side == "LONG"
        log.info("Signal %s %s bar=%s close=%.4f", self.cfg.strategy_label, signal.side, signal.bar_time, signal.entry_reference)
        resp = self.executor.open_market(is_buy, self.cfg.order_notional_usdc)
        log.info("Entry response: %s", resp)
        pos = self._wait_for_position()
        if pos.flat:
            raise RuntimeError("Entry response returned but no live position was found")
        fill = pos.entry_px if pos.entry_px is not None else signal.entry_reference
        try:
            tp, sl = self.strategy.protection_after_fill(signal, fill)
        except Exception:
            log.exception("Fill invalidated planned protection; closing immediately")
            self.executor.close_market()
            raise
        tp_oid, sl_oid = self.executor.place_protection(pos.size, tp, sl)
        self.state.active_side = 1 if pos.size > 0 else -1
        self.state.active_entry = fill
        self.state.active_sl = sl
        self.state.active_tp_at_entry = tp
        self.state.last_entry_bar = signal.bar_time
        self.state.tp_order_oid = tp_oid
        self.state.sl_order_oid = sl_oid
        self._save()

    def _heartbeat(self, candles, pos_size: float, signal_found: bool) -> None:
        bar = candles[-1]
        stamp = datetime.fromtimestamp(bar.t / 1000).astimezone().strftime("%H:%M")
        if pos_size > 0: state = "持有 LONG"
        elif pos_size < 0: state = "持有 SHORT"
        elif signal_found: state = "触发信号"
        else: state = "等待信号"
        log.info("[%s] %s=%.2f | %s | %s", stamp, self.cfg.market_symbol, bar.c, self.strategy.status_text(candles, self.state), state)

    def process_once(self) -> bool:
        required = int(getattr(self.strategy, "required_lookback", 0) or 0)
        candles = self.market.fetch_recent(max(self.cfg.lookback_candles, required))
        if not candles:
            return False
        bar = candles[-1]
        if self.state.last_processed_bar == bar.t:
            return False
        if isinstance(self.executor, DryRunExecutor):
            self.executor.set_mid(bar.c)

        flat, pos_size = self._sync_position_state(candles)
        if not flat and self._hard_flat_if_needed(candles):
            self.state.last_processed_bar = bar.t
            self._save()
            self._heartbeat(candles, 0.0, False)
            return True

        pos = self.executor.position()
        if not pos.flat and self.state.active_side != 0:
            dynamic_tp = self.strategy.current_dynamic_tp(candles, self.state)
            if dynamic_tp is not None:
                self.state.tp_order_oid = self.executor.update_tp(pos.size, self.state.tp_order_oid, dynamic_tp)
                self.state.active_tp_at_entry = dynamic_tp
                self._save()
                log.info("Dynamic TP updated to %.4f", dynamic_tp)

        pos = self.executor.position()
        decision = self.strategy.evaluate(candles, self.state, pos.flat)

        # Consume this closed bar before any network-side entry attempt. A transient
        # API failure must not submit the same Pine signal repeatedly.
        self.state.last_processed_bar = bar.t
        self._save()
        self._heartbeat(candles, pos.size, decision is not None)

        if decision is not None:
            self._place_new_trade(decision)
        return True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("Starting Herman executor: strategy=%s exchange=%s market=%s mode=%s", self.cfg.strategy_label, self.cfg.exchange, self.cfg.market_symbol, self.cfg.execution_mode)
        failures = 0
        while self.running:
            try:
                changed = self.process_once()
                failures = 0
                time.sleep(self.cfg.poll_seconds if not changed else 0.5)
            except Exception:
                failures += 1
                delay = min(60, 2 ** min(failures, 5))
                log.exception("Bot iteration failed; retrying in %ss", delay)
                time.sleep(delay)
