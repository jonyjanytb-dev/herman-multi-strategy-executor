from __future__ import annotations

from datetime import datetime, timezone

from app.aw_strategy import AWLiquidityReversalStrategy
from app.config import Config
from app.models import Candle, RuntimeState


def cfg(monkeypatch) -> Config:
    monkeypatch.setenv("STRATEGY", "aw_liquidity")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EXCHANGE", "hyperliquid")
    monkeypatch.setenv("NETWORK", "mainnet")
    monkeypatch.setenv("DEX", "xyz")
    monkeypatch.setenv("COIN", "xyz:XYZ100")
    monkeypatch.setenv("INTERVAL", "1m")
    monkeypatch.setenv("AW_SWING_LENGTH", "2")
    monkeypatch.setenv("AW_USE_HTF_LIQUIDITY", "false")
    monkeypatch.setenv("AW_USE_PDHL", "false")
    monkeypatch.setenv("AW_ATR_LENGTH", "2")
    monkeypatch.setenv("AW_DISPLACEMENT_MULT", "0.5")
    monkeypatch.setenv("AW_TARGET_MODE", "fixed_r")
    monkeypatch.setenv("AW_TARGET_R", "1.0")
    return Config.load()


BASE = int(datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)


def c(i: int, o: float, h: float, l: float, close: float) -> Candle:
    return Candle(BASE + i * 60_000, o, h, l, close, 1.0)


def bullish_fixture() -> list[Candle]:
    return [
        c(0, 100, 101, 99, 100),
        c(1, 100, 103, 100, 102),
        c(2, 102, 106, 101, 104),  # confirmed swing high / future neckline
        c(3, 104, 105, 100, 101),
        c(4, 101, 103, 98, 99),
        c(5, 99, 101, 95, 97),     # confirmed swing low / liquidity
        c(6, 97, 100, 96, 98),
        c(7, 98, 99, 96.5, 97.5),
        c(8, 96, 99, 94, 96),      # sweep below 95 and close back inside
        c(9, 100, 112, 100, 110),  # displacement through 106 + bullish FVG
    ]


def test_aw_bullish_sweep_shift_fvg_entry(monkeypatch):
    strategy = AWLiquidityReversalStrategy(cfg(monkeypatch))
    state = RuntimeState(strategy="aw_liquidity")
    candles = bullish_fixture()

    assert strategy.evaluate(candles[:-1], state, True) is None

    signal = strategy.evaluate(candles, state, True)
    assert signal is not None
    assert signal.side == "LONG"
    assert signal.entry_reference == 100
    assert signal.sl == 94
    assert signal.initial_tp == 106
    assert signal.meta["neckline"] == 106
    assert signal.meta["fvg_bot"] == 99
    assert signal.meta["fvg_top"] == 100

    tp, sl = strategy.protection_after_fill(signal, 101)
    assert tp == 106
    assert sl == 94


def test_aw_does_not_open_when_executor_is_not_flat(monkeypatch):
    strategy = AWLiquidityReversalStrategy(cfg(monkeypatch))
    state = RuntimeState(strategy="aw_liquidity")
    assert strategy.evaluate(bullish_fixture(), state, False) is None


def test_aw_aggregates_five_minute_bars_into_complete_hour(monkeypatch):
    monkeypatch.setenv("STRATEGY", "aw_liquidity")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EXCHANGE", "lighter")
    monkeypatch.setenv("INTERVAL", "5m")
    monkeypatch.setenv("LIGHTER_PROFILE", "mainnet")
    monkeypatch.setenv("LIGHTER_SYMBOL", "BTC")
    monkeypatch.setenv("AW_SWING_LENGTH", "100")
    monkeypatch.setenv("AW_USE_HTF_LIQUIDITY", "true")
    monkeypatch.setenv("AW_HTF_MINUTES", "60")
    monkeypatch.setenv("AW_HTF_PIVOT_STRENGTH", "1")
    monkeypatch.setenv("AW_USE_PDHL", "false")
    strategy = AWLiquidityReversalStrategy(Config.load())

    hourly_highs = [100, 110, 120, 110, 100]
    candles = []
    for hour, high in enumerate(hourly_highs):
        for bar in range(12):
            timestamp = BASE + (hour * 12 + bar) * 5 * 60_000
            candles.append(Candle(timestamp, 95, high, 80 + hour, 95, 1.0))

    _, status = strategy._replay(candles)

    assert "untaken H=1" in status
