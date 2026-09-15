from types import SimpleNamespace

from app.bot import TradingBot
from app.executor import Position
from app.models import RuntimeState


class SequenceExecutor:
    def __init__(self, positions):
        self.positions = list(positions)
        self.i = 0

    def position(self):
        value = self.positions[min(self.i, len(self.positions) - 1)]
        self.i += 1
        return value


def bare_bot(executor):
    bot = object.__new__(TradingBot)
    bot.cfg = SimpleNamespace(entry_position_wait_seconds=2.0)
    bot.executor = executor
    return bot


def test_wait_for_open_position_waits_until_nonflat(monkeypatch):
    monkeypatch.setattr("app.bot.time.sleep", lambda _: None)
    monkeypatch.setattr("app.bot.time.monotonic", iter([0.0, 0.1, 0.2, 0.3]).__next__)
    bot = bare_bot(SequenceExecutor([Position(0, None), Position(0, None), Position(1, 100)]))
    assert bot._wait_for_open_position().size == 1


def test_wait_for_flat_position_waits_until_flat(monkeypatch):
    monkeypatch.setattr("app.bot.time.sleep", lambda _: None)
    monkeypatch.setattr("app.bot.time.monotonic", iter([0.0, 0.1, 0.2, 0.3]).__next__)
    bot = bare_bot(SequenceExecutor([Position(1, 100), Position(1, 100), Position(0, None)]))
    assert bot._wait_for_flat_position().flat


class ProtectionExecutor:
    def __init__(self):
        self.recover_calls = 0

    def recover_protection(self):
        self.recover_calls += 1
        return 11, 90.0, 12, 110.0


def test_known_local_oids_do_not_skip_exchange_protection_verification():
    bot = object.__new__(TradingBot)
    bot.executor = ProtectionExecutor()
    bot.state = RuntimeState(
        active_side=1,
        tp_order_oid=999,
        sl_order_oid=998,
        active_tp_at_entry=90.0,
        active_sl=110.0,
    )
    bot.strategy = SimpleNamespace(can_rebuild_missing_tp=lambda candles, state: None)
    bot.store = SimpleNamespace(save=lambda state: None)
    bot._verify_known_protection([], Position(1, 100))
    assert bot.executor.recover_calls == 1
    assert bot.state.tp_order_oid == 11
    assert bot.state.sl_order_oid == 12
