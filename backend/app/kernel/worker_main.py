"""Sandboxed kernel worker (subprocess entrypoint).

Runs as ``python -m app.kernel.worker_main``. Reads framed JSON requests
(``{source, timeout}``) on stdin, executes the build123d script in the sandbox
with a SIGALRM wall-clock limit, and writes a framed JSON ``RunResult`` on
stdout. A hang or crash here cannot take the web server down — the parent
(SubprocessKernel) kills and restarts this process on breach.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from types import FrameType

from app.kernel.framing import read_frame, write_frame
from app.kernel.result import RunResult
from app.kernel.runner import run_source

DEFAULT_TIMEOUT = float(os.environ.get("CAD_WORKER_TIMEOUT", "10"))


def _on_alarm(_signum: int, _frame: FrameType | None) -> None:
    # Raised inside the user's code; run_source attributes it to the right line.
    raise TimeoutError("script exceeded the wall-clock time limit")


def main() -> None:
    signal.signal(signal.SIGALRM, _on_alarm)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    # Warm up the heavy OpenCASCADE import before signalling ready, so the
    # parent's per-request deadline doesn't have to absorb cold-start latency.
    import build123d  # noqa: F401

    write_frame(stdout, json.dumps({"ready": True}).encode())

    while True:
        frame = read_frame(stdin)
        if frame is None:
            break  # parent closed the pipe
        try:
            req = json.loads(frame)
            op = req.get("op", "run")
        except Exception as exc:  # noqa: BLE001
            write_frame(stdout, json.dumps({"ok": False, "error": f"bad request: {exc}"}).encode())
            continue

        if op == "select":
            write_frame(stdout, json.dumps(_handle_select(req)).encode())
            continue

        if op == "printability":
            write_frame(stdout, json.dumps(_handle_printability(req)).encode())
            continue

        if op == "physical":
            write_frame(stdout, json.dumps(_handle_physical(req)).encode())
            continue

        if op == "provenance":
            write_frame(stdout, json.dumps(_handle_provenance(req)).encode())
            continue

        if op == "geomdiff":
            write_frame(stdout, json.dumps(_handle_geomdiff(req)).encode())
            continue

        # op == "run"
        source = req.get("source", "")
        timeout = float(req.get("timeout", DEFAULT_TIMEOUT))
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            result = run_source(source, sandbox=True)
        except BaseException as exc:  # noqa: BLE001 - report, never die
            result = RunResult.failure(f"{type(exc).__name__}: {exc}")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

        write_frame(stdout, json.dumps(result.as_dict()).encode())


def _handle_select(req: dict) -> dict:
    """Resolve a viewport pick against the last run's cached objects."""
    from app.kernel import runner
    from app.kernel.select import measure_selection

    shape_id = req.get("shape_id", "")
    kind = req.get("kind", "face")
    index = int(req.get("index", 0))
    obj = runner.LAST_SHOWN.get(shape_id)
    if obj is None:
        # Single-part fallback: if exactly one object is shown, use it.
        if len(runner.LAST_SHOWN) == 1:
            obj = next(iter(runner.LAST_SHOWN.values()))
        else:
            return {"error": f"no object for shape {shape_id!r} (re-run first)"}
    try:
        return measure_selection(obj, kind, index)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _handle_printability(req: dict) -> dict:
    """Re-color the last run's faces by overhang angle."""
    from app.kernel import runner
    from app.kernel.printability import printability_shapes

    objs = list(runner.LAST_SHOWN.values())
    if not objs:
        return {"error": "no geometry yet (run first)"}
    try:
        shapes, states, bbox, stats = printability_shapes(objs, build_axis=req.get("build_axis", "Z"))
        return {"ok": True, "shapes": shapes, "states": states, "bbox": bbox, "stats": stats}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _handle_physical(req: dict) -> dict:
    """Compute the physical-properties readout for the last run's solids."""
    from app.kernel import runner
    from app.kernel.massprops import mass_properties

    ids = list(runner.LAST_SHOWN.keys())
    if not ids:
        return {"error": "no geometry yet (run first)"}
    objs = [runner.LAST_SHOWN[i] for i in ids]
    materials = [runner.LAST_MATERIAL.get(i, {}) for i in ids]
    try:
        result = mass_properties(objs, materials)
        if "error" in result:
            return result
        return {"ok": True, "physical": result}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _handle_provenance(req: dict) -> dict:
    """Per-face provenance render: faces from the active line glow."""
    from app.kernel.provenance import provenance_render

    source = req.get("source", "")
    active_line = req.get("active_line")
    try:
        return provenance_render(source, active_line=active_line, sandbox=True)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _handle_geomdiff(req: dict) -> dict:
    from app.kernel.geomdiff import geomdiff_shapes

    try:
        return geomdiff_shapes(req.get("old_source", ""), req.get("new_source", ""), sandbox=True)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    main()
