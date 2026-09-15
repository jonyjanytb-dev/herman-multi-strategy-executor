from types import SimpleNamespace

from app.models import Candle, RuntimeState
from app.strategies.trend_rebalance import TrendRebalanceStrategy


def cfg():
    return SimpleNamespace(
        sma50_length=50,
        sma200_length=200,
        point_size=1.0,
        min_separation=1.0,
        enable_longs=True,
        enable_shorts=True,
        tp_mode="200 SMA",
        sma_target_behaviour="Dynamic",
        tp_fixed_points=100.0,
        sl_mode="Fixed Points",
        sl_fixed_points=125.0,
        market_symbol="xyz:XYZ100",
    )


def c(i, close, open_=None):
    open_ = close if open_ is None else open_
    return Candle(i * 60_000, (i + 1) * 60_000 - 1, open_, max(open_, close), min(open_, close), close, 1)


def test_long_cross_rebalances_toward_sma200():
    closes = [100.0] * 150 + [90.0] * 50
    rows = [c(i, x) for i, x in enumerate(closes)]
    rows.append(c(200, 91.0))
    s = TrendRebalanceStrategy(cfg())
    sig = s.evaluate(rows, RuntimeState(), True)
    assert sig is not None
    assert sig.side == "LONG"
    assert sig.initial_tp > sig.entry_reference
    assert sig.sl == sig.entry_reference - 125.0


def test_dynamic_tp_follows_current_sma200():
    rows = [c(i, 100 + i * 0.01) for i in range(220)]
    state = RuntimeState(active_side=1)
    s = TrendRebalanceStrategy(cfg())
    expected = sum(x.c for x in rows[-200:]) / 200
    assert s.current_dynamic_tp(rows, state) == expected
