"""Printability overlay: per-face overhang heatmap (plan §6, the A1 edge).

For each face, the angle of its outward normal from the build direction (Z up by
default) decides whether it needs support. We color every face green→yellow→red
and re-tessellate the faces individually so three-cad-viewer shows the heatmap —
a print-aware view general CAD-as-code tools don't have.
"""

from __future__ import annotations

import math
from typing import Any

from ocp_tessellate import convert as C

from app.tessellate import tessellate

# Past this overhang angle (measured from horizontal) a downward face needs support.
OVERHANG_LIMIT = 45.0

_BUILD_AXES = {"Z": (0.0, 0.0, 1.0), "X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0)}


def _overhang(nx: float, ny: float, nz: float, build: tuple[float, float, float]) -> float:
    """Overhang angle from horizontal for a face with this normal.

    0 = up-facing or vertical wall (fine); 90 = flat underside (worst).
    Up-facing and vertical faces return 0 (never need support)."""
    bx, by, bz = build
    dot = max(-1.0, min(1.0, nx * bx + ny * by + nz * bz))
    slope = math.degrees(math.acos(dot))  # angle of normal from build axis
    if slope <= 90.0 + 1e-6:
        return 0.0  # up-facing (≤90 from +build) — supported by what's below
    return slope - 90.0  # downward-facing: 0..90 overhang from horizontal


def _color(overhang: float) -> str:
    """green (0) -> yellow (limit) -> red (>limit)."""
    if overhang <= 0.5:
        return "#3fb950"  # fine
    if overhang <= OVERHANG_LIMIT:
        # green -> yellow as we approach the limit
        t = overhang / OVERHANG_LIMIT
        r = int(0x3F + (0xFF - 0x3F) * t)
        g = int(0xB9 + (0xD2 - 0xB9) * t)
        return f"#{r:02x}{g:02x}22"
    return "#f85149"  # needs support


def printability_shapes(objs: list[Any], build_axis: str = "Z") -> tuple[dict, dict, dict | None, dict]:
    """Re-tessellate every face of the shown solids, colored by overhang.

    Returns (shapes, states, bbox, stats)."""
    from build123d import Face

    build = _BUILD_AXES.get(build_axis.upper(), _BUILD_AXES["Z"])
    faces: list[Any] = []
    colors: list[str] = []
    names: list[str] = []
    n_support = 0

    for oi, obj in enumerate(objs):
        w = obj.wrapped if hasattr(obj, "wrapped") else obj
        for fi, topo_face in enumerate(C.get_faces(w)):
            face = Face(topo_face)
            n = face.normal_at()
            oh = _overhang(n.X, n.Y, n.Z, build)
            if oh > OVERHANG_LIMIT:
                n_support += 1
            faces.append(face)
            colors.append(_color(oh))
            names.append(f"o{oi}_face_{fi}")

    if not faces:
        return {}, {}, None, {"faces": 0, "needs_support": 0}

    shapes, states, bbox = tessellate(faces, names=names, colors=colors)
    stats = {"faces": len(faces), "needs_support": n_support, "build_axis": build_axis.upper(), "limit": OVERHANG_LIMIT}
    return shapes, states, bbox, stats
