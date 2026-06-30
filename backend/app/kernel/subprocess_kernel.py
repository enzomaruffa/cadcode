"""Parent side of the sandboxed kernel: manages the worker subprocess.

Implements the same ``Kernel`` interface as ``InProcessKernel`` (plan §1):
re-exec the whole script per edit, but in a *separate* process so a hang,
infinite loop, or OOM can't take the backend down. On breach (parent-side
deadline exceeded or the worker died) the worker is killed and respawned, and
the edit gets a clean error instead of a wedged server.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from app.kernel.framing import read_frame_timeout, write_frame
from app.kernel.result import RunResult

# Directory containing the `app` package, so `-m app.kernel.worker_main` resolves.
_BACKEND_DIR = Path(__file__).resolve().parents[2]


class SubprocessKernel:
    def __init__(self, script_timeout: float = 10.0, parent_grace: float = 5.0, spawn_timeout: float = 45.0) -> None:
        self._script_timeout = script_timeout
        self._read_timeout = script_timeout + parent_grace
        self._spawn_timeout = spawn_timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = asyncio.Lock()
        # Guards the synchronous proc lifecycle when called from a worker thread.
        self._proc_lock = threading.Lock()

    # --- lifecycle ----------------------------------------------------------

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)
        except Exception:
            pass
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    def _spawn(self) -> None:
        self._kill()
        env = {**os.environ, "CAD_WORKER_TIMEOUT": str(self._script_timeout)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.kernel.worker_main"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # let worker stderr flow to the server log
            cwd=str(_BACKEND_DIR),
            env=env,
            bufsize=0,
            # New process group so we can hard-kill the whole worker if needed.
            start_new_session=True,
        )
        self._proc = proc
        # Wait for the readiness frame (OCP import completes here).
        assert proc.stdout is not None
        ready = read_frame_timeout(proc.stdout.fileno(), self._spawn_timeout)
        if ready is None:
            self._kill()
            raise RuntimeError("kernel worker failed to start within spawn timeout")

    # --- run ----------------------------------------------------------------

    async def run(self, source: str) -> RunResult:
        async with self._lock:
            data = await asyncio.to_thread(
                self._request, {"op": "run", "source": source, "timeout": self._script_timeout}, self._read_timeout
            )
        if isinstance(data, RunResult):
            return data  # an error envelope produced by _request itself
        return RunResult.from_dict(data)

    async def measure_selection(self, kind: str, shape_id: str, index: int) -> dict:
        async with self._lock:
            data = await asyncio.to_thread(
                self._request, {"op": "select", "kind": kind, "shape_id": shape_id, "index": index}, 8.0
            )
        if isinstance(data, RunResult):
            return {"error": data.error}
        return data

    def _request(self, payload: dict, read_timeout: float) -> dict | RunResult:
        """Send one framed request, return the parsed JSON dict — or a
        RunResult.failure envelope if the worker hung/died."""
        with self._proc_lock:
            if not self._alive():
                try:
                    self._spawn()
                except Exception as exc:  # noqa: BLE001
                    return RunResult.failure(f"kernel unavailable: {exc}")

            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            try:
                write_frame(proc.stdin, json.dumps(payload).encode())
            except (BrokenPipeError, OSError):
                self._kill()
                return RunResult.failure("kernel worker died; restarting — retry")

            data = read_frame_timeout(proc.stdout.fileno(), read_timeout)
            if data is None:
                # Hung in C (SIGALRM couldn't interrupt) or crashed/OOM'd.
                self._kill()
                return RunResult.failure(f"TimeoutError: kernel exceeded {read_timeout:.0f}s and was killed (restarted)")
            try:
                return json.loads(data)
            except Exception as exc:  # noqa: BLE001
                self._kill()
                return RunResult.failure(f"bad kernel response: {exc}")

    async def close(self) -> None:
        with self._proc_lock:
            self._kill()
