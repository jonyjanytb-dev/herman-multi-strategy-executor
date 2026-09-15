from __future__ import annotations

from app.config import Config
from app.models import Candle, RuntimeState
from app.strategy import StreakFailureReversalStrategy


def cfg(monkeypatch) -> Config:
    monkeypatch.setenv("STRATEGY", "streak_failure")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EXCHANGE", "hyperliquid")
    monkeypatch.setenv("NETWORK", "mainnet")
    monkeypatch.setenv("DEX", "xyz")
    monkeypatch.setenv("COIN", "xyz:XYZ100")
    monkeypatch.setenv("STREAK_USE_SESSION_FILTER", "false")
    monkeypatch.setenv("STREAK_HARD_FLAT", "false")
    monkeypatch.setenv("STREAK_LENGTH", "3")
    monkeypatch.setenv("STREAK_WAIT_BARS", "4")
    monkeypatch.setenv("STREAK_ATR_LENGTH", "2")
    monkeypatch.setenv("STREAK_SIGNAL_TIMEFRAME", "1m")
    monkeypatch.setenv("STREAK_SL_MODE", "terminal")
    monkeypatch.setenv("STREAK_TP_MODE", "r_multiple")
    monkeypatch.setenv("STREAK_TP_R", "1.0")
    return Config.load()


def c(i: int, o: float, h: float, l: float, close: float) -> Candle:
    return Candle(1_700_000_000_000 + i * 60_000, o, h, l, close, 1.0)


def streak_fixture() -> list[Candle]:
    return [
        c(0, 100, 101, 98, 99),
        c(1, 99, 100, 98, 99),
        c(2, 100, 102, 99, 101),
        c(3, 101, 103, 100, 102),
        c(4, 102, 104, 101, 103),
    ]


def test_bull_streak_arms_then_failure_confirms_short(monkeypatch):
    strategy = StreakFailureReversalStrategy(cfg(monkeypatch))
    state = RuntimeState(strategy="streak_failure")
    candles = streak_fixture()

    for i in range(3, 6):
        decision = strategy.evaluate(candles[:i], state, True)
        assert decision is None

    assert state.strategy_state["arm_short"]
    assert state.strategy_state["arm_short"]["break_level"] == 101

    trigger = c(5, 103, 103.5, 99, 100)
    decision = strategy.evaluate(candles + [trigger], state, True)
    assert decision is not None
    assert decision.side == "SHORT"
    assert decision.sl == 104

    tp, sl = strategy.protection_after_fill(decision, 100)
    assert sl == 104
    assert tp == 96
    assert not state.strategy_state["arm_short"]


def test_wick_only_does_not_confirm(monkeypatch):
    strategy = StreakFailureReversalStrategy(cfg(monkeypatch))
    state = RuntimeState(strategy="streak_failure")
    candles = streak_fixture()
    for i in range(3, 6):
        strategy.evaluate(candles[:i], state, True)
    wick_break = c(5, 103, 103.5, 100, 101.5)
    assert strategy.evaluate(candles + [wick_break], state, True) is None
    assert state.strategy_state["arm_short"]
