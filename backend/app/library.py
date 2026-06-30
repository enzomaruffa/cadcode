"""The parts catalog the agent reads (plan §5).

Reflects ``lib.parts`` into signatures + docstrings so the library-aware agent
can compose with *your* parts, tokens, and joints — the library stops being
storage and becomes the agent's design system.
"""

from __future__ import annotations

import inspect
from typing import Any


def catalog() -> list[dict[str, Any]]:
    """Each exported part as {name, signature, doc, params}."""
    try:
        from lib import parts as _parts
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for name in getattr(_parts, "__all__", []):
        fn = getattr(_parts, name, None)
        if not callable(fn):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        params = [
            {"name": p.name, "default": (p.default if p.default is not inspect.Parameter.empty else None)}
            for p in sig.parameters.values()
        ]
        out.append(
            {
                "name": name,
                "signature": f"{name}{sig}",
                "doc": inspect.getdoc(fn) or "",
                "params": params,
                "import": f"from lib.parts import {name}",
            }
        )
    return out
