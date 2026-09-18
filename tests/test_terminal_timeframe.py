from __future__ import annotations

import terminal


def test_configure_lighter_timeframe_resets_live_mode_and_state(monkeypatch):
    current = {
        "EXCHANGE": "lighter",
        "STRATEGY": "aw_liquidity",
        "INTERVAL": "1m",
        "DRY_RUN": "false",
        "STATE_PATH": "runtime/live-state.json",
    }
    changes: list[tuple[str, str]] = []

    monkeypatch.setattr(terminal, "read_env", lambda: current)
    monkeypatch.setattr(terminal, "set_env", lambda key, value: changes.append((key, value)))
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    terminal.configure_timeframe()

    assert ("INTERVAL", "5m") in changes
    assert ("AW_HTF_MINUTES", "60") in changes
    assert ("DRY_RUN", "true") in changes
    assert ("STATE_PATH", "") in changes


def test_configure_timeframe_is_lighter_only(monkeypatch):
    monkeypatch.setattr(terminal, "read_env", lambda: {"EXCHANGE": "hyperliquid"})

    try:
        terminal.configure_timeframe()
    except ValueError as exc:
        assert "Lighter" in str(exc)
    else:
        raise AssertionError("expected a Lighter-only validation error")


def test_configure_timeframe_zero_returns_without_changes(monkeypatch):
    changes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        terminal,
        "read_env",
        lambda: {
            "EXCHANGE": "lighter",
            "STRATEGY": "aw_liquidity",
            "INTERVAL": "1m",
        },
    )
    monkeypatch.setattr(terminal, "set_env", lambda key, value: changes.append((key, value)))
    monkeypatch.setattr("builtins.input", lambda _prompt: "0")

    terminal.configure_timeframe()

    assert changes == []


def test_switching_lighter_to_streak_restores_one_minute(monkeypatch):
    changes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        terminal,
        "read_env",
        lambda: {
            "EXCHANGE": "lighter",
            "STRATEGY": "aw_liquidity",
            "INTERVAL": "15m",
        },
    )
    monkeypatch.setattr(terminal, "set_env", lambda key, value: changes.append((key, value)))
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    terminal.configure_strategy()

    assert ("INTERVAL", "1m") in changes
    assert ("AW_HTF_MINUTES", "15") in changes


def test_leaving_lighter_restores_one_minute(monkeypatch):
    changes: list[tuple[str, str]] = []
    answers = iter(["1", ""])
    monkeypatch.setattr(
        terminal,
        "read_env",
        lambda: {
            "EXCHANGE": "lighter",
            "STRATEGY": "aw_liquidity",
            "INTERVAL": "30m",
            "ACCOUNT_ADDRESS": "",
        },
    )
    monkeypatch.setattr(terminal, "set_env", lambda key, value: changes.append((key, value)))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(terminal.getpass, "getpass", lambda _prompt: "")

    terminal.configure_exchange()

    assert ("INTERVAL", "1m") in changes
    assert ("AW_HTF_MINUTES", "15") in changes
