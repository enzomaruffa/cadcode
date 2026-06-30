"""GL-free isometric wireframe thumbnails for the parts palette (plan §5).

Tessellate a part, take its edge segments, project them isometrically, and emit
a tiny SVG. No headless GL needed — the thumbnail rides the render pipeline we
already built.
"""

from __future__ import annotations

import math
from typing import Any

from app.tessellate import tessellate

_COS30 = math.cos(math.radians(30))
_SIN30 = math.sin(math.radians(30))


def _iso(x: float, y: float, z: float) -> tuple[float, float]:
    # Standard isometric projection (z up).
    sx = (x - y) * _COS30
    sy = (x + y) * _SIN30 - z
    return sx, sy


def _edge_points(shapes: dict) -> list[list[float]]:
    pts: list[list[float]] = []

    def walk(o: Any) -> None:
        if not isinstance(o, dict):
            return
        if o.get("parts"):
            for p in o["parts"]:
                walk(p)
        elif isinstance(o.get("shape"), dict):
            edges = o["shape"].get("edges") or []
            if edges and isinstance(edges[0], (list, tuple)):
                pts.extend(edges)

    walk(shapes)
    return pts


def iso_svg(part: Any, size: int = 120, stroke: str = "#9aa7ff") -> str:
    """Isometric wireframe SVG for ``part``."""
    try:
        shapes, _states, _bbox = tessellate([part], names=["thumb"])
    except Exception:
        return ""
    pts = _edge_points(shapes)
    if not pts:
        return ""

    proj = [_iso(p[0], p[1], p[2]) for p in pts if len(p) >= 3]
    if not proj:
        return ""
    xs = [p[0] for p in proj]
    ys = [p[1] for p in proj]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w = maxx - minx or 1.0
    h = maxy - miny or 1.0
    scale = (size - 16) / max(w, h)
    ox = (size - w * scale) / 2 - minx * scale
    oy = (size - h * scale) / 2 - miny * scale

    # edges come as consecutive point pairs -> one <line> per pair
    lines: list[str] = []
    for i in range(0, len(proj) - 1, 2):
        x1, y1 = proj[i]
        x2, y2 = proj[i + 1]
        lines.append(
            f'<line x1="{x1 * scale + ox:.1f}" y1="{y1 * scale + oy:.1f}" '
            f'x2="{x2 * scale + ox:.1f}" y2="{y2 * scale + oy:.1f}"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'stroke="{stroke}" stroke-width="1" fill="none" stroke-linecap="round">'
        + "".join(lines)
        + "</svg>"
    )
