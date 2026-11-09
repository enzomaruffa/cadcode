"""Run a whole project — a scene (or part) that imports the project's own parts
+ constants, and optionally parts from OTHER projects.

Every project is materialized into one temp workspace as a package under a shared
``projects`` namespace (``projects/<pid>/{__init__, project.py, parts/, scenes/}``),
and each project's *local* imports are rewritten to be absolute under its own
package. So:

  * within a project you still write clean bare imports — ``from project import
    UNIT``, ``from parts.base import base`` — which are rewritten at run time;
  * any project can reuse another's part with ``from projects.<other>.parts.<name>
    import <name>`` (already absolute — left untouched).

The rewrite is line-based (not AST) so line numbers survive for error mapping.
This is the foundation for scene rendering and the multi-file agent's
edit -> run -> view -> edit loop (it dry-runs candidate edits here).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.projects import ROOT, _file_path, _ident, _project_dir

# run_project mutates global sys.path / sys.modules, so runs must not overlap.
_RUN_LOCK = threading.Lock()

# A run materializes every project and imports lib/, so its result is a pure
# function of all of their files plus the request. Keyed by that hash, an LRU of
# results lets unchanged targets (library thumbnails) skip the kernel entirely.
_LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
_CACHE_ENTRIES = 160
_CACHE_BYTES = 96_000_000  # serialized JSON; big scenes are ~9 MB each
_cache: OrderedDict[str, tuple[dict[str, Any], int]] = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()

# Project-local imports to rewrite to `projects.<pid>.…`. `from projects.…` is
# already absolute and does NOT match these (project != projects, parts != …).
_FROM_PROJECT = re.compile(r"^(\s*from\s+)project(\s+import\s+)")
_FROM_PARTS = re.compile(r"^(\s*from\s+)parts(\.[\w.]+)?(\s+import\s+)")


def _rewrite_imports(source: str, pid: str) -> str:
    """Rewrite a project file's local imports (`from project import …`,
    `from parts.x import …`) to be absolute under the `projects.<pid>` package."""
    out = []
    for line in source.splitlines():
        line = _FROM_PROJECT.sub(rf"\1projects.{pid}.project\2", line)
        line = _FROM_PARTS.sub(rf"\1projects.{pid}.parts\2\3", line)
        out.append(line)
    return "\n".join(out)


def _write_pkg(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "__init__.py").write_text("")


def _read(path: Path) -> str:
    """Read a project source file. Python source is UTF-8 by definition (PEP 263),
    so it must never be decoded through the ambient locale — a C/POSIX locale
    turns every em-dash in a docstring into a 500."""
    return path.read_text(encoding="utf-8")


def _materialize(tmp: Path, overrides: dict[str, str], current: str) -> None:
    """Copy EVERY project into ``tmp/projects/<pid>/`` as a package (rewriting
    local imports), then apply the current project's uncommitted overrides."""
    root = tmp / "projects"
    _write_pkg(root)
    if ROOT.is_dir():
        for proj_dir in sorted(ROOT.iterdir()):
            if not proj_dir.is_dir():
                continue
            pid = proj_dir.name  # created via _ident → already a valid identifier
            dest = root / pid
            _write_pkg(dest)
            _write_pkg(dest / "parts")
            _write_pkg(dest / "scenes")
            constants = proj_dir / "project.py"
            if constants.is_file():
                (dest / "project.py").write_text(_rewrite_imports(_read(constants), pid), encoding="utf-8")
            for sub in ("parts", "scenes"):
                d = proj_dir / sub
                if not d.is_dir():
                    continue
                for f in d.glob("*.py"):
                    if f.stem != "__init__":
                        (dest / sub / f.name).write_text(_rewrite_imports(_read(f), pid), encoding="utf-8")

    # Overrides are keyed project-relative for the CURRENT project.
    cur = root / current
    _write_pkg(cur)
    _write_pkg(cur / "parts")
    _write_pkg(cur / "scenes")
    for rel, source in (overrides or {}).items():
        p = cur / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_rewrite_imports(source, current), encoding="utf-8")


def content_hash(
    project: str,
    kind: str,
    name: str,
    overrides: dict[str, str] | None,
    preview_source: str | None,
    lean: bool,
) -> str:
    """sha256 over every project and lib source file, the overrides and the run
    request. Equal hashes mean the run would produce the same result."""
    h = hashlib.sha256()

    def feed(data: str | bytes) -> None:
        b = data.encode("utf-8") if isinstance(data, str) else data
        h.update(len(b).to_bytes(8, "little"))
        h.update(b)

    for part in (project, kind, name, "lean" if lean else "full"):
        feed(part)
    feed("-" if preview_source is None else "+" + preview_source)
    for label, root in (("projects", ROOT), ("lib", _LIB_DIR)):
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.py")):
            feed(f"{label}/{f.relative_to(root).as_posix()}")
            feed(f.read_bytes())
    for rel, source in sorted((overrides or {}).items()):
        feed(f"override/{rel}")
        feed(source)
    return h.hexdigest()


def clear_cache() -> None:
    global _cache_bytes
    with _cache_lock:
        _cache.clear()
        _cache_bytes = 0


def run_project(
    project: str,
    kind: str,
    name: str = "",
    overrides: dict[str, str] | None = None,
    preview_source: str | None = None,
    lean: bool = False,
) -> dict[str, Any]:
    """Run a project (kind: scene|part|project) with the project importable.

    ``overrides`` maps project-relative paths (e.g. ``"parts/bracket.py"``) to new
    source, applied on top of what's on disk — so a candidate edit can be run
    without committing it. If ``preview_source`` is given, THAT script is run (a
    part has no ``show()`` of its own, so previewing one means running a wrapper
    like ``show(part())``); otherwise the target file itself is run. Returns the
    RunResult dict (ok/error/shapes/…) plus ``content_hash``; results are cached
    by that hash, so repeating an unchanged run returns immediately."""
    src_dir = _project_dir(project)
    if not src_dir.is_dir():
        return {"ok": False, "error": f"no such project {project!r}"}
    target = _file_path(project, kind, name)
    if target is None:
        return {"ok": False, "error": f"bad target kind {kind!r}"}

    from app.kernel.runner import run_source

    key = content_hash(project, kind, name, overrides, preview_source, lean)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return dict(hit[0])

    current = _ident(project)
    tmp = Path(tempfile.mkdtemp(prefix="cadproj_"))
    with _RUN_LOCK:
        result = _run_locked(tmp, current, src_dir, target, overrides, preview_source, run_source, lean)
    result["content_hash"] = key
    _remember(key, result)
    return dict(result)


def _remember(key: str, result: dict[str, Any]) -> None:
    global _cache_bytes
    size = len(json.dumps(result, default=str))
    if size > _CACHE_BYTES:
        return
    with _cache_lock:
        old = _cache.pop(key, None)
        if old is not None:
            _cache_bytes -= old[1]
        _cache[key] = (result, size)
        _cache_bytes += size
        while len(_cache) > _CACHE_ENTRIES or _cache_bytes > _CACHE_BYTES:
            _cache_bytes -= _cache.popitem(last=False)[1][1]


def _run_locked(tmp, current, src_dir, target, overrides, preview_source, run_source, lean=False) -> dict[str, Any]:
    added_path = str(tmp)
    try:
        _materialize(tmp, overrides or {}, current)

        if preview_source is not None:
            target_source = _rewrite_imports(preview_source, current)
        else:
            target_rel = target.relative_to(src_dir)  # e.g. scenes/main.py or project.py
            mat = tmp / "projects" / current / target_rel
            if not mat.is_file():
                return {"ok": False, "error": f"run target {target_rel} does not exist"}
            target_source = _read(mat)  # already rewritten during materialize

        sys.path.insert(0, added_path)
        try:
            if lean:
                # Agent dry-runs: exec + specs + geometry facts, no tessellation.
                from app.kernel.runner import run_source_lean

                return run_source_lean(target_source)
            result = run_source(target_source, sandbox=False)  # project imports need the real import machinery
        finally:
            if added_path in sys.path:
                sys.path.remove(added_path)
            # Drop the projects package so the next run re-imports fresh sources.
            for mod in [m for m in sys.modules if m == "projects" or m.startswith("projects.")]:
                del sys.modules[mod]
        return result.as_dict()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
