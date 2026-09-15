from __future__ import annotations

import json
from pathlib import Path

from .models import RuntimeState


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state root must be an object")
            return RuntimeState.from_dict(raw)
        except Exception as exc:
            raise RuntimeError(f"Runtime state is unreadable: {self.path}: {exc}") from exc

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
