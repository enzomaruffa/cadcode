"""Run a whole project — a scene (or part) that imports the project's own
parts + constants. Materializes the project + any uncommitted edits into a temp
dir on ``sys.path`` so ``import project`` and ``from parts.x import x`` resolve,
then executes the run-target through the normal runner.

This is the foundation for scene rendering and for the multi-file agent's
edit -> run -> view -> edit loop (it dry-runs candidate edits here).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.projects import _file_path, _project_dir

# run_project mutates global sys.path / sys.modules, so runs must not overlap.
_RUN_LOCK = threading.Lock()


def run_project(project: str, kind: str, name: str = "", overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """Run one project file (kind: scene|part|project) with the project importable.

    ``overrides`` maps project-relative paths (e.g. ``"parts/bracket.py"``) to new
    source, applied on top of what's on disk — so the agent can dry-run edits
    without committing them. Returns the RunResult dict (ok/error/shapes/…)."""
    src_dir = _project_dir(project)
    if not src_dir.is_dir():
        return {"ok": False, "error": f"no such project {project!r}"}
    target = _file_path(project, kind, name)
    if target is None:
        return {"ok": False, "error": f"bad target kind {kind!r}"}

    from app.kernel.runner import run_source

    tmp = Path(tempfile.mkdtemp(prefix="cadproj_"))
    added_path = str(tmp)
    with _RUN_LOCK:
        return _run_locked(tmp, added_path, src_dir, target, overrides, run_source)


def _run_locked(tmp, added_path, src_dir, target, overrides, run_source) -> dict[str, Any]:
    try:
        shutil.copytree(src_dir, tmp, dirs_exist_ok=True)
        for rel, source in (overrides or {}).items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(source)

        target_rel = target.relative_to(src_dir)
        target_file = tmp / target_rel
        if not target_file.is_file():
            return {"ok": False, "error": f"run target {target_rel} does not exist"}
        target_source = target_file.read_text()

        sys.path.insert(0, added_path)
        try:
            result = run_source(target_source, sandbox=False)  # project imports need the real import machinery
        finally:
            if added_path in sys.path:
                sys.path.remove(added_path)
            # Drop the project's modules so the next run re-imports fresh sources.
            for mod in [m for m in sys.modules if m == "project" or m == "parts" or m.startswith("parts.")]:
                del sys.modules[mod]
        return result.as_dict()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
