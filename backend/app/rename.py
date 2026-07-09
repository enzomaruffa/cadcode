"""Rename a project part/scene — a real refactor, not just a file move.

Renaming part `old` → `new` in project P:
  * renames `parts/old.py` → `parts/new.py` and the identifier inside (so
    `def old(…)` and any self-references become `new`);
  * in every file of P: rewrites `from parts.old import old [as x]` (module AND
    imported name), and — when the import was un-aliased — renames the call
    sites too;
  * in every OTHER project: same for `from projects.P.parts.old import …`.

Scenes aren't importable, so a scene rename is just the file move. All rewrites
are line-based regex with word boundaries (same spirit as the project runner's
import rewriting — line numbers survive, no AST reformat of user code).
"""

from __future__ import annotations

import re
from typing import Any

from app.projects import _file_path, _ident, list_projects, project_files, write_project_files


def _rewrite_refs(src: str, owner: str, old: str, new: str, allow_bare: bool) -> tuple[str, bool]:
    """Rewrite references to owner's part `old` in one file's source. Returns
    (new_source, touched). `allow_bare` = the file lives in the owner project,
    so bare `from parts.old import …` applies (elsewhere only the cross form)."""
    mod_bare = re.compile(rf"^(\s*from\s+)parts\.{re.escape(old)}(\s+import\s+)(.+)$")
    mod_cross = re.compile(rf"^(\s*from\s+)projects\.{re.escape(owner)}\.parts\.{re.escape(old)}(\s+import\s+)(.+)$")
    name_re = re.compile(rf"\b{re.escape(old)}\b")

    out: list[str] = []
    touched = False
    rename_call_sites = False
    for line in src.splitlines():
        m = mod_bare.match(line) if allow_bare else None
        cross = None if m else mod_cross.match(line)
        mm = m or cross
        if mm:
            names = mm.group(3)
            # un-aliased import binds the old name → call sites need renaming too
            if name_re.search(names) and not re.search(rf"\b{re.escape(old)}\s+as\s+\w+", names):
                rename_call_sites = True
            module = f"parts.{new}" if m else f"projects.{owner}.parts.{new}"
            line = f"{mm.group(1)}{module}{mm.group(2)}{name_re.sub(new, names)}"
            touched = True
        out.append(line)
    text = "\n".join(out) + ("\n" if src.endswith("\n") else "")
    if rename_call_sites:
        new_text = name_re.sub(new, text)
        touched = touched or new_text != text
        text = new_text
    return text, touched


def rename_file(project: str, kind: str, old: str, new: str) -> dict[str, Any]:
    """Rename a part/scene and refactor every reference. Returns
    {ok, new, changed: [project-relative paths]} or {ok: False, error}."""
    pid = _ident(project)
    old_i = _ident(old)
    new_i = _ident(new)
    if kind not in ("part", "scene"):
        return {"ok": False, "error": f"can't rename kind {kind!r}"}
    if new_i == old_i:
        return {"ok": False, "error": "new name is the same as the old one"}
    old_path = _file_path(pid, kind, old_i)
    new_path = _file_path(pid, kind, new_i)
    if old_path is None or not old_path.exists():
        return {"ok": False, "error": f"{kind} {old!r} not found"}
    if new_path is None or new_path.exists():
        return {"ok": False, "error": f"{kind} {new_i!r} already exists"}

    sub = "parts" if kind == "part" else "scenes"
    changed: list[str] = [f"{pid}/{sub}/{new_i}.py"]

    text = old_path.read_text()
    if kind == "part":
        # the function (and any self-reference) inside the renamed file
        text = re.sub(rf"\b{re.escape(old_i)}\b", new_i, text)
    old_path.unlink()
    new_path.write_text(text)

    if kind == "part":
        for t in list_projects():
            other = t["name"]
            edits: dict[str, str] = {}
            for rel, src in project_files(other).items():
                if other == pid and rel == f"parts/{new_i}.py":
                    continue  # the renamed file itself — already handled
                new_src, touched = _rewrite_refs(src, pid, old_i, new_i, allow_bare=other == pid)
                if touched:
                    edits[rel] = new_src
            if edits:
                write_project_files(other, edits)
                changed += [f"{other}/{rel}" for rel in edits]

    return {"ok": True, "new": new_i, "changed": changed}
