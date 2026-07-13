"""GL-free shaded isometric thumbnails for the parts palette (plan §5).

Tessellate a part, project its triangles isometrically, depth-sort them
(painter's algorithm), fake-light each by its normal, and emit a tiny SVG —
plus crisp feature edges on top. No headless GL needed; the thumbnail rides the
same render pipeline we already built, and matches the app's warm render.
"""

from __future__ import annotations

import math
from typing import Any

from app.tessellate import tessellate

_COS30 = math.cos(math.radians(30))
_SIN30 = math.sin(math.radians(30))

# Warm base color for faces + a fake light direction (upper-front).
_BASE = (201, 160, 106)  # #c9a06a
_EDGE = "#6b5a3a"
_LIGHT = (0.40, -0.52, 0.76)
_LN = math.sqrt(sum(c * c for c in _LIGHT))


def _iso(x: float, y: float, z: float) -> tuple[float, float]:
    # Standard isometric projection (z up).
    return (x - y) * _COS30, (x + y) * _SIN30 - z


def _qrot(q: tuple, v: tuple) -> tuple:
    """Rotate vector v by quaternion q = (x, y, z, w)."""
    qx, qy, qz, qw = q
    x, y, z = v
    ix = qw * x + qy * z - qz * y
    iy = qw * y + qz * x - qx * z
    iz = qw * z + qx * y - qy * x
    iw = -qx * x - qy * y - qz * z
    return (
        ix * qw + iw * -qx + iy * -qz - iz * -qy,
        iy * qw + iw * -qy + iz * -qx - ix * -qz,
        iz * qw + iw * -qz + ix * -qy - iy * -qx,
    )


_IDENT = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def _compose(parent: tuple, loc: Any) -> tuple:
    """Compose a parent (pos, quat) with a node's loc [[x,y,z],[qx,qy,qz,qw]]."""
    if not loc:
        return parent
    (pp, pq) = parent
    lp, lq = tuple(loc[0]), tuple(loc[1])
    # world_pos = parent_pos + parent_rot·local_pos ; world_rot = parent_rot·local_rot
    rp = _qrot(pq, lp)
    pos = (pp[0] + rp[0], pp[1] + rp[1], pp[2] + rp[2])
    x1, y1, z1, w1 = pq
    x2, y2, z2, w2 = lq
    quat = (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    )
    return pos, quat


def _hex_rgb(c: Any) -> tuple | None:
    if not isinstance(c, str) or not c.startswith("#") or len(c) < 7:
        return None
    try:
        return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
    except ValueError:
        return None


def _leaves(shapes: dict) -> list[tuple[dict, tuple, tuple]]:
    """Leaf shapes WITH their world transform AND base color — every node's
    `loc` composed down the tree (a scene positions whole parts via loc;
    ignoring it collapses every module onto the origin, which is exactly what
    it used to do) and the nearest `color` in the tree carried along."""
    out: list[tuple[dict, tuple, tuple]] = []

    def walk(o: Any, xform: tuple, color: tuple) -> None:
        if not isinstance(o, dict):
            return
        xform = _compose(xform, o.get("loc"))
        color = _hex_rgb(o.get("color")) or color
        if o.get("parts"):
            for p in o["parts"]:
                walk(p, xform, color)
        elif isinstance(o.get("shape"), dict):
            out.append((o["shape"], xform, color))

    walk(shapes, _IDENT, _BASE)
    return out


def _tri_shade(v0: tuple, v1: tuple, v2: tuple) -> float:
    # Geometric normal (robust; ignores stored normals) -> Lambert + ambient.
    ax, ay, az = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    bx, by, bz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
    n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    d = (nx * _LIGHT[0] + ny * _LIGHT[1] + nz * _LIGHT[2]) / (n * _LN)
    return 0.32 + 0.68 * max(abs(d), 0.0)


def iso_svg(part: Any, size: int = 140) -> str:
    """Shaded isometric SVG thumbnail for ``part``."""
    try:
        shapes, _states, _bbox = tessellate([part], names=["thumb"])
    except Exception:
        return ""
    return iso_svg_shapes(shapes, size)


def decimate(verts: Any, tris: Any, cells: int = 24) -> tuple[Any, Any]:
    """Grid vertex-clustering decimation — snap verts to a coarse ``cells``³ grid,
    merge coincident ones, drop the triangles that collapse. A part tessellated at
    ~9k triangles drops to a few hundred: plenty for a small thumbnail, and cheap
    enough to render inline. Faceted, but that reads fine at thumbnail size."""
    import numpy as np

    v = np.asarray(verts, dtype=float)
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if len(v) == 0 or len(t) == 0:
        return v, t
    lo = v.min(axis=0)
    cell = float(np.maximum(v.max(axis=0) - lo, 1e-6).max()) / max(cells, 1)
    key = np.floor((v - lo) / cell).astype(np.int64)
    _uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    n = int(inv.max()) + 1
    newv = np.zeros((n, 3))
    counts = np.zeros(n)
    np.add.at(newv, inv, v)
    np.add.at(counts, inv, 1)
    newv /= np.maximum(counts, 1)[:, None]
    nt = inv[t]
    good = (nt[:, 0] != nt[:, 1]) & (nt[:, 1] != nt[:, 2]) & (nt[:, 0] != nt[:, 2])
    return newv, nt[good]


def iso_svg_mesh(verts: Any, tris: Any, size: int = 96, color: tuple = _BASE, cells: int | None = 24) -> str:
    """Shaded isometric SVG straight from a raw (verts, tris) mesh — used for the
    print planner's per-orientation previews (the mesh is already rotated into the
    candidate orientation, so the thumbnail shows exactly how it sits on the bed).
    Decimates to ``cells``³ first unless ``cells`` is None."""
    import numpy as np

    v = np.asarray(verts, dtype=float)
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if cells:
        v, t = decimate(v, t, cells)
    if len(v) == 0 or len(t) == 0:
        return ""
    tv = v[t]  # (M,3,3)
    x, y, z = tv[:, :, 0], tv[:, :, 1], tv[:, :, 2]
    px = (x - y) * _COS30
    py = (x + y) * _SIN30 - z
    depth = (x + y + z).mean(axis=1)
    p0, p1, p2 = tv[:, 0], tv[:, 1], tv[:, 2]
    nrm = np.cross(p1 - p0, p2 - p0)
    nn = np.linalg.norm(nrm, axis=1)
    nn[nn == 0] = 1.0
    dot = (nrm @ np.asarray(_LIGHT)) / (nn * _LN)
    shade = 0.32 + 0.68 * np.clip(np.abs(dot), 0.0, 1.0)
    minx, maxx = float(px.min()), float(px.max())
    miny, maxy = float(py.min()), float(py.max())
    w = (maxx - minx) or 1.0
    h = (maxy - miny) or 1.0
    scale = (size - 8) / max(w, h)
    ox = (size - w * scale) / 2 - minx * scale
    oy = (size - h * scale) / 2 - miny * scale
    sx = px * scale + ox
    sy = py * scale + oy
    r, g, b = color
    order = np.argsort(depth)
    out = []
    for i in order:
        s = shade[i]
        fill = f"rgb({int(r * s)},{int(g * s)},{int(b * s)})"
        pts = f"{sx[i, 0]:.1f},{sy[i, 0]:.1f} {sx[i, 1]:.1f},{sy[i, 1]:.1f} {sx[i, 2]:.1f},{sy[i, 2]:.1f}"
        out.append(f'<polygon points="{pts}" fill="{fill}" stroke="{fill}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'stroke-width="0.5" stroke-linejoin="round">' + "".join(out) + "</svg>"
    )


def iso_svg_shapes(shapes: dict, size: int = 140) -> str:
    """Shaded isometric SVG from an already-tessellated shapes tree (any number
    of parts) — used for library thumbnails AND the agent's vision sanity pass."""
    # Collect projected, depth-keyed, shaded triangles + edge segments.
    tris: list[tuple[float, list[tuple[float, float]], str]] = []
    edges: list[tuple[float, tuple[float, float], tuple[float, float]]] = []
    allpts: list[tuple[float, float]] = []

    for shape, (tpos, tq), base_rgb in _leaves(shapes):
        ident = tq == (0.0, 0.0, 0.0, 1.0) and tpos == (0.0, 0.0, 0.0)

        def world(v: tuple, tpos=tpos, tq=tq, ident=ident) -> tuple:
            if ident:
                return v
            r = _qrot(tq, v)
            return (r[0] + tpos[0], r[1] + tpos[1], r[2] + tpos[2])

        verts = shape.get("vertices") or []
        idx = shape.get("triangles") or []
        for t in range(0, len(idx) - 2, 3):
            a, b, c = idx[t] * 3, idx[t + 1] * 3, idx[t + 2] * 3
            if c + 2 >= len(verts):
                continue
            v0 = world((verts[a], verts[a + 1], verts[a + 2]))
            v1 = world((verts[b], verts[b + 1], verts[b + 2]))
            v2 = world((verts[c], verts[c + 1], verts[c + 2]))
            depth = sum(v[0] + v[1] + v[2] for v in (v0, v1, v2)) / 3.0
            p = [_iso(*v0), _iso(*v1), _iso(*v2)]
            allpts.extend(p)
            shade = _tri_shade(v0, v1, v2)
            fill = f"rgb({int(base_rgb[0] * shade)},{int(base_rgb[1] * shade)},{int(base_rgb[2] * shade)})"
            tris.append((depth, p, fill))

        edata = shape.get("edges") or []
        if edata and isinstance(edata[0], (list, tuple)):
            for i in range(0, len(edata) - 1, 2):
                p1, p2 = edata[i], edata[i + 1]
                if len(p1) >= 3 and len(p2) >= 3:
                    w1, w2 = world(tuple(p1[:3])), world(tuple(p2[:3]))
                    a2, b2 = _iso(*w1), _iso(*w2)
                    # depth-key the edge like the triangles so hidden lines stay
                    # hidden (drawing all edges last made everything look x-ray)
                    edepth = (sum(w1) + sum(w2)) / 2.0
                    edges.append((edepth, a2, b2))
                    allpts.extend((a2, b2))

    if not allpts:
        return ""

    xs = [p[0] for p in allpts]
    ys = [p[1] for p in allpts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w = maxx - minx or 1.0
    h = maxy - miny or 1.0
    scale = (size - 18) / max(w, h)
    ox = (size - w * scale) / 2 - minx * scale
    oy = (size - h * scale) / 2 - miny * scale

    def sx(x: float) -> float:
        return x * scale + ox

    def sy(y: float) -> float:
        return y * scale + oy

    # One painter's pass over triangles AND edges together (far → near), so an
    # edge behind a nearer face gets painted over — no more x-ray look. Each
    # triangle strokes with its OWN fill (hides AA seams between triangles
    # without outlining the tessellation); only feature edges get _EDGE.
    items: list[tuple[float, str]] = []
    for depth, p, fill in tris:
        pts = " ".join(f"{sx(px):.1f},{sy(py):.1f}" for px, py in p)
        items.append((depth, f'<polygon points="{pts}" fill="{fill}" stroke="{fill}"/>'))
    for depth, (x1, y1), (x2, y2) in edges:
        items.append(
            (
                depth + 0.01,
                f'<line x1="{sx(x1):.1f}" y1="{sy(y1):.1f}" x2="{sx(x2):.1f}" y2="{sy(y2):.1f}" stroke="{_EDGE}"/>',
            )
        )
    items.sort(key=lambda t: t[0])
    body = [svg for _d, svg in items]

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'stroke-width="0.8" stroke-linejoin="round" stroke-linecap="round">' + "".join(body) + "</svg>"
    )
