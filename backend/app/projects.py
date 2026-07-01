"""Projects: folder-first storage for the v2 model (see PROJECTS_PLAN.md).

A project is a directory on disk — which doubles as the "use a folder" store:

    projects/<project>/
        project.py        # project-wide constants + parameters
        parts/<part>.py   # reusable parametric part functions
        scenes/<scene>.py # assemblies of parts (physics / render / animation)

This module is the service the API + UI use to list/read/write those files. It
is deliberately storage-only (no kernel), so it never hard-depends on git.
"""

from __future__ import annotations

import keyword
import os
import re
from pathlib import Path

ROOT = Path(os.environ.get("CAD_PROJECTS", Path(__file__).resolve().parents[1] / "projects"))

# file "kind" -> subdir ("" = project.py, handled specially)
_KINDS = {"project": "", "part": "parts", "scene": "scenes"}


def _ident(name: str) -> str:
    """A safe identifier / filename stem from a user-typed name."""
    s = re.sub(r"\W+", "_", (name or "").strip().lower()).strip("_")
    if not s or s[0].isdigit():
        s = f"x_{s}" if s else "untitled"
    if keyword.iskeyword(s):
        s += "_"
    return s


def _project_dir(project: str) -> Path:
    return ROOT / _ident(project)


def _file_path(project: str, kind: str, name: str) -> Path | None:
    if kind not in _KINDS:
        return None
    base = _project_dir(project)
    if kind == "project":
        return base / "project.py"
    return base / _KINDS[kind] / f"{_ident(name)}.py"


def _stems(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.py") if p.stem != "__init__")


def project_tree(project: str) -> dict:
    base = _project_dir(project)
    return {
        "name": _ident(project),
        "constants": (base / "project.py").is_file(),
        "parts": _stems(base / "parts"),
        "scenes": _stems(base / "scenes"),
    }


def project_files(project: str) -> dict[str, str]:
    """Every source file in a project as {project-relative-path: source} —
    ``project.py`` + ``parts/*.py`` + ``scenes/*.py``. This is what the
    multi-file agent reads as its whole-project context."""
    base = _project_dir(project)
    files: dict[str, str] = {}
    if not base.is_dir():
        return files
    constants = base / "project.py"
    if constants.is_file():
        files["project.py"] = constants.read_text()
    for sub in ("parts", "scenes"):
        d = base / sub
        if d.is_dir():
            for p in sorted(d.glob("*.py")):
                if p.stem != "__init__":
                    files[f"{sub}/{p.name}"] = p.read_text()
    return files


def write_project_files(project: str, edits: dict[str, str]) -> dict:
    """Write a set of {project-relative-path: source} edits atomically-ish
    (all files written; new parts/scenes create their file). Paths are
    constrained to project.py / parts/*.py / scenes/*.py."""
    base = _project_dir(project)
    written: list[str] = []
    for rel, source in edits.items():
        rel = rel.strip().lstrip("/")
        parts = rel.split("/")
        ok = (rel == "project.py") or (len(parts) == 2 and parts[0] in ("parts", "scenes") and parts[1].endswith(".py"))
        if not ok:
            return {"ok": False, "error": f"illegal path {rel!r} (allowed: project.py, parts/*.py, scenes/*.py)"}
        if len(parts) == 2:
            rel = f"{parts[0]}/{_ident(parts[1][:-3])}.py"
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
        written.append(rel)
    return {"ok": True, "project": _ident(project), "written": written}


def list_projects() -> list[dict]:
    if not ROOT.is_dir():
        return []
    return [project_tree(p.name) for p in sorted(ROOT.iterdir()) if p.is_dir()]


def read_file(project: str, kind: str, name: str = "") -> dict:
    path = _file_path(project, kind, name)
    if path is None:
        return {"error": f"unknown kind {kind!r}"}
    if not path.is_file():
        return {"error": "not found", "source": ""}
    return {"source": path.read_text()}


def write_file(project: str, kind: str, name: str, source: str) -> dict:
    path = _file_path(project, kind, name)
    if path is None:
        return {"ok": False, "error": f"unknown kind {kind!r}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return {"ok": True, "project": _ident(project), "kind": kind, "name": _ident(name) if name else "project"}


def create_project(name: str) -> dict:
    ident = _ident(name)
    base = ROOT / ident
    (base / "parts").mkdir(parents=True, exist_ok=True)
    (base / "scenes").mkdir(parents=True, exist_ok=True)
    constants = base / "project.py"
    if not constants.is_file():
        constants.write_text(
            '"""Project-wide constants + parameters — imported by every part and scene."""\n\n'
            "# Example: UNIT = 10.0  # base module size (mm)\n"
        )
    return {"ok": True, "name": ident}
