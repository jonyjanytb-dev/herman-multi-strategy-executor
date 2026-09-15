from __future__ import annotations

import json
from pathlib import Path

from .models import RuntimeState


class StateStore:
    def __init__(self, path: str, strategy: str):
        self.path = Path(path)
        self.strategy = strategy

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState(strategy=self.strategy)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = RuntimeState.from_dict(raw)
        except Exception as exc:
            raise RuntimeError(f"Runtime state is unreadable: {self.path}: {exc}") from exc
        if state.strategy != self.strategy:
            raise RuntimeError(
                f"State strategy mismatch: file={state.strategy} selected={self.strategy}. "
                "Use a separate state file per strategy."
            )
        return state

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
