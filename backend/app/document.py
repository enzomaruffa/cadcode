"""Per-session document state (plan §1, §3).

The buffer *is* the model. Undo/redo is buffer history — because all three
editors (human, agent, viewport) mutate this one buffer, there is never a second
"real" model to reconcile. Last-good geometry is retained so a typo never blanks
the viewport (plan §6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.kernel.result import RunResult


@dataclass
class Document:
    source: str
    _history: list[str] = field(default_factory=list)
    _redo: list[str] = field(default_factory=list)
    last_good: RunResult | None = None

    def __post_init__(self) -> None:
        self._history = [self.source]

    def set_source(self, source: str) -> bool:
        """Record a new buffer state. Returns False if unchanged (no-op edits
        from debounce shouldn't pollute history)."""
        if source == self.source:
            return False
        self._history.append(source)
        self._redo.clear()
        self.source = source
        return True

    def undo(self) -> bool:
        if len(self._history) <= 1:
            return False
        self._redo.append(self._history.pop())
        self.source = self._history[-1]
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self.source = self._redo.pop()
        self._history.append(self.source)
        return True

    def record_good(self, result: RunResult) -> None:
        if result.ok:
            self.last_good = result

    @property
    def history_len(self) -> int:
        return len(self._history)

    def snapshot(self) -> dict[str, Any]:
        return {"source": self.source, "history_len": self.history_len}
