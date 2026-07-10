"""Reflect the REAL build123d API into editor docs.

Signatures and docstrings come from the installed package via `inspect`, so the
editor's autocomplete, hover, and parameter hints are always true to the running
kernel — no hand-maintained (and drift-prone) API list. Computed once per
process and cached; the payload also covers the cadcode DSL (show/require/…).
"""

from __future__ import annotations

import enum
import inspect
from typing import Any

_DOC_LIMIT = 900  # chars of docstring per entry (hover stays readable)

_cache: dict[str, Any] | None = None

# The cadcode DSL isn't importable from build123d — documented by hand.
_DSL: dict[str, dict[str, Any]] = {
    "show": {
        "kind": "function",
        "signature": "show(obj, name=None, color=None, material=None)",
        "doc": "Render an object in the viewport (scenes only). `name` keys the part for physics/joints/print; `color` is a hex string. A part function should RETURN its shape instead and let the scene show it.",
        "params": [
            {"name": "obj"},
            {"name": "name", "default": "None"},
            {"name": "color", "default": "None"},
            {"name": "material", "default": "None"},
        ],
    },
    "show_object": {
        "kind": "function",
        "signature": "show_object(obj, name=None, options=None)",
        "doc": "Alias of show() (cq-editor compatibility).",
        "params": [{"name": "obj"}, {"name": "name", "default": "None"}, {"name": "options", "default": "None"}],
    },
    "require": {
        "kind": "function",
        "signature": "require(condition, message='requirement')",
        "doc": "Executable spec (CAD-as-TDD): asserts a geometric condition every run — e.g. `require(part.volume > 0, 'not empty')`. Ambient in parts too. Failures show in the specs panel and gate the agent.",
        "params": [{"name": "condition"}, {"name": "message", "default": "'requirement'"}],
    },
    "print_hint": {
        "kind": "function",
        "signature": "print_hint(target=None, *, flow=None, cosmetic=False)",
        "doc": "Declare PRINT INTENT in the model — the print planner's orientation search obeys it. "
        "`print_hint(part, flow=(0,0,-1))`: water/fluid runs along this part-frame vector → the planner keeps "
        "layer lines PARALLEL to the flow (prints the part sideways if needed). "
        "`print_hint(part.faces().sort_by(Axis.Z)[-1], cosmetic=True)`: keep support scars and bed texture off "
        "these faces. Returns target; a no-op outside print planning.",
        "params": [
            {"name": "target", "default": "None"},
            {"name": "flow", "default": "None"},
            {"name": "cosmetic", "default": "False"},
        ],
    },
    "Range": {
        "kind": "class",
        "signature": "Range(min, max)",
        "doc": "Slider bounds for a typed param: `SIZE: Annotated[float, Range(5, 80)] = 20` renders a live slider in the params panel.",
        "params": [{"name": "min"}, {"name": "max"}],
    },
}


def _entry(name: str, obj: Any) -> dict[str, Any] | None:
    kind = "class" if inspect.isclass(obj) else "function" if callable(obj) else "value"
    out: dict[str, Any] = {"kind": kind}
    doc = inspect.getdoc(obj) or ""

    if inspect.isclass(obj) and issubclass(obj, enum.Enum):
        members = [m.name for m in obj]
        out["kind"] = "enum"
        out["signature"] = name
        out["members"] = members
        out["doc"] = (doc.split("\n\n")[0] + "\n\nMembers: " + ", ".join(members))[:_DOC_LIMIT]
        return out

    try:
        sig = inspect.signature(obj)
        out["signature"] = f"{name}{sig}"
        out["params"] = [
            {
                "name": p.name,
                **({"default": repr(p.default)} if p.default is not inspect.Parameter.empty else {}),
            }
            for p in sig.parameters.values()
            if p.name not in ("self", "cls") and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        ]
    except (ValueError, TypeError):
        out["signature"] = name
        out["params"] = []
    out["doc"] = doc[:_DOC_LIMIT]
    return out


def build123d_docs() -> dict[str, Any]:
    """{name: {kind, signature, doc, params[, members]}} for every public
    build123d name + the cadcode DSL. Cached for the process lifetime."""
    global _cache
    if _cache is not None:
        return _cache
    import build123d as b3d

    names = list(getattr(b3d, "__all__", [])) or [n for n in dir(b3d) if not n.startswith("_")]
    docs: dict[str, Any] = {}
    for name in names:
        obj = getattr(b3d, name, None)
        if obj is None:
            continue
        try:
            e = _entry(name, obj)
        except Exception:  # noqa: BLE001 - one weird symbol must not kill the endpoint
            e = None
        if e is not None:
            docs[name] = e
    docs.update(_DSL)
    _cache = docs
    return docs
