"""In-process kernel (M0).

Runs the script in a worker thread so a slow tessellation doesn't block the
event loop. Crash/timeout isolation arrives at M1 with the subprocess kernel,
which implements this same ``Kernel`` interface.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from app.kernel.result import RunResult
from app.kernel.runner import run_source


@runtime_checkable
class Kernel(Protocol):
    async def run(self, source: str) -> RunResult: ...


class InProcessKernel:
    async def run(self, source: str) -> RunResult:
        return await asyncio.to_thread(run_source, source)

    async def close(self) -> None:  # symmetry with the subprocess kernel
        return None
