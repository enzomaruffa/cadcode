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
    async def measure_selection(self, kind: str, shape_id: str, index: int) -> dict: ...
    async def printability(self, build_axis: str = "Z") -> dict: ...
    async def provenance(self, source: str, active_line: int | None = None) -> dict: ...
    async def close(self) -> None: ...


class InProcessKernel:
    async def run(self, source: str) -> RunResult:
        return await asyncio.to_thread(run_source, source)

    async def measure_selection(self, kind: str, shape_id: str, index: int) -> dict:
        from app.kernel import runner
        from app.kernel.select import measure_selection

        obj = runner.LAST_SHOWN.get(shape_id)
        if obj is None and len(runner.LAST_SHOWN) == 1:
            obj = next(iter(runner.LAST_SHOWN.values()))
        if obj is None:
            return {"error": f"no object for shape {shape_id!r} (re-run first)"}
        return await asyncio.to_thread(measure_selection, obj, kind, index)

    async def printability(self, build_axis: str = "Z") -> dict:
        from app.kernel import runner
        from app.kernel.printability import printability_shapes

        objs = list(runner.LAST_SHOWN.values())
        if not objs:
            return {"error": "no geometry yet (run first)"}

        def _go() -> dict:
            shapes, states, bbox, stats = printability_shapes(objs, build_axis=build_axis)
            return {"ok": True, "shapes": shapes, "states": states, "bbox": bbox, "stats": stats}

        return await asyncio.to_thread(_go)

    async def provenance(self, source: str, active_line: int | None = None) -> dict:
        from app.kernel.provenance import provenance_render

        return await asyncio.to_thread(provenance_render, source, active_line)

    async def close(self) -> None:  # symmetry with the subprocess kernel
        return None
