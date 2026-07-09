"""One-call completion catalog for the editor's autocomplete.

Aggregates everything the frontend needs to offer import-aware, auto-importing
completions: every project (parts with real signatures + docstrings, scenes,
project.py constants), the global library parts, and the global design tokens.
Signatures come from a cheap regex over each part file (`def name(...)`) — no
imports are executed, so this is fast and safe to call on every editor session.
"""

from __future__ import annotations

import re
from typing import Any

from app.projects import ROOT, _ident, list_projects

# `def <name>(<params>)` — the part's callable (multiline defs: first line only,
# which is fine for a signature hint).
_DEF_RE = re.compile(r"^def\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE)
_DOC_RE = re.compile(r'^\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\'|$)', re.MULTILINE)
_CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=", re.MULTILINE)


def _part_info(project: str, name: str) -> dict[str, Any]:
    """{name, params, doc} for one project part — regex, not import."""
    info: dict[str, Any] = {"name": name, "params": "", "doc": ""}
    try:
        text = (ROOT / _ident(project) / "parts" / f"{_ident(name)}.py").read_text()
    except OSError:
        return info
    for m in _DEF_RE.finditer(text):
        if m.group(1) == name:
            info["params"] = m.group(2).strip()
            tail = text[m.end() :]
            dm = _DOC_RE.search(tail[:400])
            if dm:
                info["doc"] = dm.group(1).strip()
            break
    return info


def _constants(project: str) -> list[str]:
    try:
        text = (ROOT / _ident(project) / "project.py").read_text()
    except OSError:
        return []
    return [m.group(1) for m in _CONST_RE.finditer(text)]


def completion_catalog() -> dict[str, Any]:
    """{projects: [{name, parts:[{name,params,doc}], scenes, constants}],
    lib_parts: [{name, signature, doc}]} — everything autocomplete needs."""
    projects = []
    for t in list_projects():
        pid = t["name"]
        projects.append(
            {
                "name": pid,
                "parts": [_part_info(pid, p) for p in t.get("parts", [])],
                "scenes": t.get("scenes", []),
                "constants": _constants(pid),
            }
        )
    try:
        from app.library import catalog

        lib_parts = [
            {"name": p["name"], "signature": p.get("signature", ""), "doc": p.get("doc", "")} for p in catalog()
        ]
    except Exception:  # noqa: BLE001 - library reflection must never break completions
        lib_parts = []
    return {"projects": projects, "lib_parts": lib_parts}
