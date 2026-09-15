from .streak_failure import StreakFailureStrategy
from .trend_rebalance import TrendRebalanceStrategy


def build_strategy(cfg):
    if cfg.strategy == "streak_failure":
        return StreakFailureStrategy(cfg)
    return TrendRebalanceStrategy(cfg)


__all__ = ["build_strategy", "TrendRebalanceStrategy", "StreakFailureStrategy"]
