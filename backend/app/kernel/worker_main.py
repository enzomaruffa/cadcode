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
            source = req.get("source", "")
            timeout = float(req.get("timeout", DEFAULT_TIMEOUT))
        except Exception as exc:  # noqa: BLE001
            write_frame(stdout, json.dumps(RunResult.failure(f"bad request: {exc}").as_dict()).encode())
            continue

        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            result = run_source(source, sandbox=True)
        except BaseException as exc:  # noqa: BLE001 - report, never die
            result = RunResult.failure(f"{type(exc).__name__}: {exc}")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

        write_frame(stdout, json.dumps(result.as_dict()).encode())


if __name__ == "__main__":
    main()
