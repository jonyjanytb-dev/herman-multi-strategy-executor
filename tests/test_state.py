import json

import pytest

from app.models import RuntimeState
from app.state import StateStore


def test_state_roundtrip_with_strategy_data(tmp_path):
    p = tmp_path / "state.json"
    store = StateStore(str(p))
    state = RuntimeState(strategy_id="streak_failure", strategy_data={"arm_short": True})
    store.save(state)
    loaded = store.load()
    assert loaded.strategy_id == "streak_failure"
    assert loaded.strategy_data["arm_short"] is True


def test_corrupt_state_fails_closed(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError):
        StateStore(str(p)).load()
