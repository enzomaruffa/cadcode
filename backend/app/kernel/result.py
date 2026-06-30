"""The structured result of running a build123d script.

Returns ``{meshes, ops, measurements}`` on success or ``{error: traceback+line}``
on failure (plan §1, §8). Shared by every kernel implementation and serialized
straight into ``geometry`` / ``error`` envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunResult:
    ok: bool
    # success payload
    shapes: dict[str, Any] | None = None
    states: dict[str, list[int]] | None = None
    bbox: dict[str, float] | None = None
    ops: list[dict[str, Any]] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    # failure payload
    error: str | None = None
    traceback: str = ""
    error_line: int | None = None

    @classmethod
    def success(
        cls,
        shapes: dict,
        states: dict,
        bbox: dict | None,
        *,
        ops: list | None = None,
        measurements: dict | None = None,
        stdout: str = "",
    ) -> "RunResult":
        return cls(
            ok=True,
            shapes=shapes,
            states=states,
            bbox=bbox,
            ops=ops or [],
            measurements=measurements or {},
            stdout=stdout,
        )

    @classmethod
    def failure(cls, error: str, traceback: str = "", line: int | None = None, stdout: str = "") -> "RunResult":
        return cls(ok=False, error=error, traceback=traceback, error_line=line, stdout=stdout)
