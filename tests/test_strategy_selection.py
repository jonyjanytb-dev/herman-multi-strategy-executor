from __future__ import annotations

from app.config import Config
from app.models import RuntimeState
from app.strategy import AWLiquidityReversalStrategy, StreakFailureReversalStrategy, TrendRebalanceStrategy, build_strategy


def base_env(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EXCHANGE", "hyperliquid")
    monkeypatch.setenv("NETWORK", "mainnet")
    monkeypatch.setenv("DEX", "xyz")
    monkeypatch.setenv("COIN", "xyz:XYZ100")
    monkeypatch.setenv("INTERVAL", "1m")


def test_selects_trend_strategy(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STRATEGY", "trend_rebalance")
    cfg = Config.load()
    assert isinstance(build_strategy(cfg), TrendRebalanceStrategy)
    assert cfg.state_path.endswith("state-hyperliquid-trend_rebalance.json")


def test_selects_streak_strategy(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STRATEGY", "streak_failure")
    cfg = Config.load()
    assert isinstance(build_strategy(cfg), StreakFailureReversalStrategy)
    assert cfg.state_path.endswith("state-hyperliquid-streak_failure.json")


def test_selects_aw_strategy(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STRATEGY", "aw_liquidity")
    cfg = Config.load()
    assert isinstance(build_strategy(cfg), AWLiquidityReversalStrategy)
    assert cfg.state_path.endswith("state-hyperliquid-aw_liquidity.json")


def test_runtime_state_rejects_cross_strategy_reuse():
    state = RuntimeState(strategy="trend_rebalance")
    assert state.strategy != "streak_failure"
