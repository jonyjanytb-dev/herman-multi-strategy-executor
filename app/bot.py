from __future__ import annotations

import logging
import signal
import time
from datetime import datetime
from typing import Optional

from .config import Config
from .executor import BaseExecutor
from .executor_ext import MultiDryRunExecutor, MultiHyperliquidExecutor, MultiOKXExecutor
from .market_data import HyperliquidMarketData, OKXMarketData
from .models import RuntimeState, Signal
from .state import StateStore
from .strategies import build_strategy
from .strategies.base import InvalidProtection

log = logging.getLogger(__name__)


def build_market_data(cfg: Config):
    if cfg.exchange == "okx":
        return OKXMarketData(
            cfg.okx_inst_id,
            cfg.interval,
            timeout=cfg.request_timeout,
            base_url=cfg.okx_base_url,
            retry_attempts=cfg.request_retry_attempts,
        )
    return HyperliquidMarketData(cfg.network, cfg.coin, cfg.interval, timeout=cfg.request_timeout)


def build_executor(cfg: Config) -> BaseExecutor:
    if cfg.dry_run:
        return MultiDryRunExecutor(cfg)
    if cfg.exchange == "okx":
        return MultiOKXExecutor(cfg)
    return MultiHyperliquidExecutor(cfg)


class TradingBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.market = build_market_data(cfg)
        self.strategy = build_strategy(cfg)
        self.store = StateStore(cfg.state_path)
        self.state = self.store.load()
        if self.state.strategy_id and self.state.strategy_id != cfg.strategy:
            raise RuntimeError(
                f"State file belongs to strategy={self.state.strategy_id}, current={cfg.strategy}. "
                "Use the strategy-specific STATE_PATH."
            )
        self.state.strategy_id = cfg.strategy
        self.executor = build_executor(cfg)
        self.running = True

    def stop(self, *_):
        self.running = False

    @staticmethod
    def _response_error(resp: object) -> Optional[str]:
        if isinstance(resp, dict) and str(resp.get("status", "")).lower() == "err":
            return str(resp.get("response") or resp)
        return None

    def _wait_for_position(self) -> object:
        deadline = time.monotonic() + self.cfg.entry_position_wait_seconds
        last = self.executor.position()
        while last.flat and time.monotonic() < deadline:
            time.sleep(0.25)
            last = self.executor.position()
        return last

    def _clear_active_state(self, bar_time: int) -> None:
        self.state.last_exit_bar = bar_time
        self.state.active_side = 0
        self.state.active_entry = None
        self.state.active_sl = None
        self.state.active_tp_at_entry = None
        self.state.active_entry_day = None
        self.state.tp_order_oid = None
        self.state.sl_order_oid = None
        self.store.save(self.state)

    def _sync_position_state(self, candles) -> tuple[bool, float]:
        bar_time = candles[-1].t
        pos = self.executor.position()
        flat = pos.flat

        if self.state.active_side != 0 and flat:
            log.info("Managed position closed; clearing remaining protective orders")
            self.executor.cancel_all_protection()
            self._clear_active_state(bar_time)

        elif self.state.active_side == 0 and not flat:
            tp_oid, tp_px, sl_oid, sl_px = self.executor.recover_protection()
            if sl_px is None:
                raise RuntimeError(
                    "Existing position detected but its SL could not be recovered. "
                    "Refusing to manage or issue new trades until a stop is restored."
                )
            if tp_px is None:
                rebuild = self.strategy.can_rebuild_missing_tp(candles, self.state)
                if rebuild is None:
                    raise RuntimeError(
                        "Existing position detected but its TP could not be recovered for the selected strategy."
                    )
                tp_oid = self.executor.update_tp(pos.size, None, rebuild)
                tp_px = rebuild
                log.warning("Recovered position had no TP; rebuilt TP=%.4f oid=%s", rebuild, tp_oid)

            self.state.active_side = 1 if pos.size > 0 else -1
            self.state.active_entry = pos.entry_px
            self.state.active_tp_at_entry = tp_px
            self.state.active_sl = sl_px
            self.state.tp_order_oid = tp_oid
            self.state.sl_order_oid = sl_oid
            self.state.active_entry_day = self.strategy.entry_day(candles)
            self.store.save(self.state)
            log.info("Recovered existing position size=%s TP=%s SL=%s", pos.size, tp_px, sl_px)

        return flat, pos.size

    def _verify_known_protection(self, candles, pos) -> None:
        if pos.flat or self.state.active_side == 0:
            return
        if self.state.sl_order_oid is not None and self.state.tp_order_oid is not None:
            return
        tp_oid, tp_px, sl_oid, sl_px = self.executor.recover_protection()
        if sl_px is None:
            raise RuntimeError(
                "Managed live position has no recoverable SL. Refusing further strategy actions until protection is restored."
            )
        self.state.sl_order_oid = sl_oid
        self.state.active_sl = sl_px
        if tp_px is None:
            if self.state.active_tp_at_entry is None:
                rebuild = self.strategy.can_rebuild_missing_tp(candles, self.state)
                if rebuild is None:
                    raise RuntimeError("Managed live position has no recoverable TP and no safe TP reference")
                self.state.active_tp_at_entry = rebuild
            tp_oid = self.executor.update_tp(pos.size, None, float(self.state.active_tp_at_entry))
            tp_px = self.state.active_tp_at_entry
        self.state.tp_order_oid = tp_oid
        self.state.active_tp_at_entry = tp_px
        self.store.save(self.state)

    def _force_flat_if_needed(self, candles, position_size: float) -> bool:
        if not self.strategy.should_force_flat(candles, self.state, position_size):
            return False
        log.warning("Strategy hard-flat condition reached; closing position size=%s", position_size)
        resp = self.executor.close_market(position_size)
        error = self._response_error(resp)
        if error:
            raise RuntimeError(f"Hard-flat close failed: {error}")
        pos = self._wait_for_position()
        if not pos.flat:
            raise RuntimeError("Hard-flat close returned but live position is still open")
        self.executor.cancel_all_protection()
        self._clear_active_state(candles[-1].t)
        return True

    def _log_heartbeat(self, candles, position_size: float, has_signal: bool) -> None:
        bar = candles[-1]
        bar_time = datetime.fromtimestamp(bar.t / 1000).astimezone().strftime("%H:%M")
        detail = self.strategy.heartbeat(candles, self.state, position_size, has_signal)
        log.info("[%s] [%s] %s", bar_time, self.cfg.strategy_label, detail)

    def _record_entry_state(self, signal: Signal, pos, tp: float, sl: float, tp_oid, sl_oid, candles) -> None:
        self.state.active_side = 1 if pos.size > 0 else -1
        self.state.active_entry = pos.entry_px if pos.entry_px is not None else signal.entry_reference
        self.state.active_tp_at_entry = tp
        self.state.active_sl = sl
        self.state.active_entry_day = self.strategy.entry_day(candles)
        self.state.last_entry_bar = signal.bar_time
        self.state.tp_order_oid = tp_oid
        self.state.sl_order_oid = sl_oid
        self.store.save(self.state)

    def _execute_signal(self, signal: Signal, candles) -> None:
        is_buy = signal.side == "LONG"
        notional = signal.order_notional or self.cfg.order_notional_usdc
        log.info(
            "Signal %s strategy=%s bar=%s ref=%.4f SL=%.4f estTP=%s notional=%.2f",
            signal.side,
            self.cfg.strategy,
            signal.bar_time,
            signal.entry_reference,
            signal.sl,
            "-" if signal.initial_tp is None else f"{signal.initial_tp:.4f}",
            notional,
        )

        # Consume this closed bar before sending the order. If the API rejects or
        # times out, the same Pine signal is never blindly re-submitted in a loop.
        self.state.last_processed_bar = candles[-1].t
        self.store.save(self.state)

        resp = self.executor.open_market(is_buy, notional)
        log.info("Entry response: %s", resp)
        error = self._response_error(resp)
        if error:
            raise RuntimeError(f"Entry rejected: {error}")

        pos = self._wait_for_position()
        if pos.flat:
            raise RuntimeError("Entry response returned but no live position was found within the wait window")
        fill = pos.entry_px if pos.entry_px is not None else signal.entry_reference

        try:
            tp, sl = self.strategy.protection_after_fill(signal, fill)
        except InvalidProtection:
            log.exception("Actual fill invalidated intended strategy protection; closing position")
            close_resp = self.executor.close_market(pos.size)
            close_error = self._response_error(close_resp)
            if close_error:
                raise RuntimeError(f"Emergency invalid-protection close failed: {close_error}")
            after_close = self._wait_for_position()
            if not after_close.flat:
                raise RuntimeError("Emergency invalid-protection close returned but position is still open")
            raise

        # Persist intended levels before exchange order placement. If TP placement
        # fails after SL succeeds, the next operator/debug pass still knows the
        # intended immutable protection levels.
        self._record_entry_state(signal, pos, tp, sl, None, None, candles)
        tp_oid, sl_oid = self.executor.place_protection(pos.size, tp, sl)
        self.state.tp_order_oid = tp_oid
        self.state.sl_order_oid = sl_oid
        self.store.save(self.state)
        log.info("Protection active TP=%.4f SL=%.4f tp_oid=%s sl_oid=%s", tp, sl, tp_oid, sl_oid)

    def process_once(self) -> bool:
        candles = self.market.fetch_recent(self.cfg.lookback_candles)
        if not candles:
            return False
        bar = candles[-1]
        if self.state.last_processed_bar == bar.t:
            return False

        if isinstance(self.executor, MultiDryRunExecutor):
            self.executor.set_mid(bar.c)

        flat, position_size = self._sync_position_state(candles)
        if not flat and self._force_flat_if_needed(candles, position_size):
            self.state.last_processed_bar = bar.t
            self.store.save(self.state)
            self._log_heartbeat(candles, 0.0, False)
            return True

        pos = self.executor.position()
        flat = pos.flat
        if not flat and self.state.active_side != 0:
            self._verify_known_protection(candles, pos)
            dynamic_tp = self.strategy.current_dynamic_tp(candles, self.state)
            if dynamic_tp is not None:
                self.state.tp_order_oid = self.executor.update_tp(pos.size, self.state.tp_order_oid, dynamic_tp)
                self.state.active_tp_at_entry = dynamic_tp
                self.store.save(self.state)
                log.info("Dynamic TP updated to %.4f", dynamic_tp)

        decision = self.strategy.evaluate(candles, self.state, flat)
        self._log_heartbeat(candles, pos.size, decision is not None)
        if decision is not None:
            self._execute_signal(decision, candles)

        self.state.last_processed_bar = bar.t
        self.store.save(self.state)
        return True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info(
            "Starting Herman executor: strategy=%s exchange=%s market=%s mode=%s state=%s",
            self.cfg.strategy_label,
            self.cfg.exchange,
            self.cfg.market_symbol,
            self.cfg.execution_mode,
            self.cfg.state_path,
        )
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
