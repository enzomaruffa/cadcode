"""Geometry diff for agent patches (plan §6).

Shows the *geometric* consequence of a proposed edit, not just a text diff: the
proposed shape ghosted, with added material in green and removed material in
red (argus-diff style). A better review surface for 3D.
"""

from __future__ import annotations

from typing import Any

from app.kernel.runner import run_objects
from app.tessellate import tessellate


def _first_solid(objs: list[Any]) -> Any | None:
    return objs[0] if objs else None


def geomdiff_shapes(old_source: str, new_source: str, sandbox: bool = True) -> dict[str, Any]:
    old_objs, e1 = run_objects(old_source, sandbox=sandbox)
    new_objs, e2 = run_objects(new_source, sandbox=sandbox)
    if e2:
        return {"error": f"proposed source failed: {e2}"}
    old = _first_solid(old_objs)
    new = _first_solid(new_objs)
    if new is None:
        return {"error": "proposed source shows no geometry"}

    objs: list[Any] = []
    names: list[str] = []
    colors: list[str] = []
    alphas: list[float] = []

    # The proposed shape, ghosted, so the diff reads in context.
    objs.append(new)
    names.append("proposed")
    colors.append("#8893a0")
    alphas.append(0.12)

    if old is not None:
        try:
            added = new - old  # material present in new but not old
            if added is not None and getattr(added, "volume", 0) > 1e-6:
                objs.append(added)
                names.append("added")
                colors.append("#3fb950")
                alphas.append(1.0)
        except Exception:
            pass
        try:
            removed = old - new  # material present in old but not new
            if removed is not None and getattr(removed, "volume", 0) > 1e-6:
                objs.append(removed)
                names.append("removed")
                colors.append("#f85149")
                alphas.append(0.55)
        except Exception:
            pass

    shapes, states, bbox = tessellate(objs, names=names, colors=colors, alphas=alphas)
    n_added = sum(1 for n in names if n == "added")
    n_removed = sum(1 for n in names if n == "removed")
    return {
        "ok": True,
        "shapes": shapes,
        "states": states,
        "bbox": bbox,
        "diff_stats": {"added": n_added, "removed": n_removed},
    }
