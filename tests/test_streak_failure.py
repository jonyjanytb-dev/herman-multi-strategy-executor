from types import SimpleNamespace

import pytest

from app.models import Candle, RuntimeState
from app.strategies.base import InvalidProtection
from app.strategies.streak_failure import StreakFailureStrategy


def cfg(**overrides):
    data = dict(
        streak_signal_timeframe="1m",
        streak_definition="bodies",
        streak_length=5,
        streak_wait_bars=15,
        streak_use_session_filter=False,
        streak_session_start="09:45",
        streak_session_end="12:00",
        streak_trigger_must_be_in_session=True,
        streak_hard_flat_enabled=False,
        streak_hard_flat="16:00",
        streak_sl_mode="Terminal streak candle extreme",
        streak_stop_buffer_ticks=0,
        streak_tick_size=1.0,
        streak_atr_length=3,
        streak_atr_mult=1.0,
        streak_tp_mode="R multiple",
        streak_tp_r=1.0,
        streak_invalid_target_policy="Use R target",
        streak_sizing_mode="Fixed notional",
        streak_max_risk_usd=200.0,
        streak_max_notional_usd=10000.0,
        streak_sizing_slippage_ticks=1,
        order_notional_usdc=750.0,
        enable_longs=True,
        enable_shorts=True,
        market_symbol="xyz:XYZ100",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def candle(i, o, h, l, c):
    return Candle(i * 60_000, (i + 1) * 60_000 - 1, o, h, l, c, 1)


def seed():
    # Warmup neutral candles.
    rows = [candle(i, 100, 101, 99, 100) for i in range(5)]
    # Five bullish bodies. Terminal candle low=104, high=106.
    for j in range(5):
        i = 5 + j
        o = 100 + j
        rows.append(candle(i, o, o + 2, o - 1, o + 1))
    return rows


def test_bull_streak_arms_short_then_close_break_triggers():
    s = StreakFailureStrategy(cfg())
    st = RuntimeState(strategy_data={})
    rows = seed()
    assert s.evaluate(rows, st, True) is None
    assert st.strategy_data["arm_short"] is True

    # Wick below terminal low does not count; close remains above/equal.
    rows.append(candle(10, 105, 106, 103, 104))
    assert s.evaluate(rows, st, True) is None
    assert st.strategy_data["arm_short"] is True

    rows.append(candle(11, 104, 105, 101, 102))
    sig = s.evaluate(rows, st, True)
    assert sig is not None
    assert sig.side == "SHORT"
    assert sig.sl == 106.0
    assert sig.order_notional == 750.0
    assert st.strategy_data["arm_short"] is False

    # Actual fill defines R-target; frozen stop remains structural.
    tp, sl = s.protection_after_fill(sig, 102.0)
    assert sl == 106.0
    assert tp == 98.0


def test_actual_gap_can_invalidate_structural_stop():
    s = StreakFailureStrategy(cfg())
    st = RuntimeState(strategy_data={})
    rows = seed()
    s.evaluate(rows, st, True)
    rows.append(candle(10, 105, 106, 101, 102))
    sig = s.evaluate(rows, st, True)
    assert sig is not None
    with pytest.raises(InvalidProtection):
        s.protection_after_fill(sig, 107.0)  # short fill above its stop


def test_stop_risk_budget_converts_risk_to_notional_and_caps():
    s = StreakFailureStrategy(cfg(streak_sizing_mode="Stop-risk budget", streak_max_risk_usd=10, streak_max_notional_usd=500))
    st = RuntimeState(strategy_data={})
    rows = seed()
    s.evaluate(rows, st, True)
    rows.append(candle(10, 105, 106, 101, 102))
    sig = s.evaluate(rows, st, True)
    assert sig is not None
    assert sig.order_notional <= 500


def test_five_minute_signal_requires_complete_bucket():
    s = StreakFailureStrategy(cfg(streak_signal_timeframe="5m"))
    rows = [candle(i, 100, 101, 99, 100) for i in range(9)]
    grouped = s._signal_candles(rows)
    assert len(grouped) == 1
    rows.append(candle(9, 100, 101, 99, 100))
    assert len(s._signal_candles(rows)) == 2
