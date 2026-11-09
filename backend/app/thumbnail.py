"""GL-free shaded isometric thumbnails (parts palette, print planner, agent vision).

Triangles are projected isometrically and rasterized with numpy into a
supersampled z-buffer, shaded per pixel from interpolated normals, outlined
from depth/object discontinuities plus the tessellation's own edge polylines
(depth-tested), then box-downsampled and wrapped in an SVG ``<image>`` so every
caller keeps receiving an SVG string.
"""

from __future__ import annotations

import base64
import math
import struct
import zlib
from typing import Any

import numpy as np

from app.tessellate import tessellate

_COS30 = math.cos(math.radians(30))
_SIN30 = math.sin(math.radians(30))

_BASE = (201, 160, 106)  # #c9a06a
_EDGE_RGB = np.array([22.0, 20.0, 18.0])

# Screen x, screen y (down), depth (smaller = nearer), all in the same units as
# screen x/y so depth thresholds can be expressed in pixels.
_PROJ = np.array(
    [
        [_COS30, _SIN30, -math.sqrt(0.5)],
        [-_COS30, _SIN30, -math.sqrt(0.5)],
        [0.0, -1.0, -math.sqrt(0.5)],
    ]
)


def _unit(v: tuple[float, float, float]) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    return a / np.linalg.norm(a)


_VIEW = _unit((1, 1, 1))
_LEFT = _unit((-1, 1, 0))
_UP = _unit((-1, -1, 2))
_KEY = _unit(tuple(0.75 * _VIEW + 0.5 * _LEFT + 0.65 * _UP))
_FILL = _unit(tuple(0.6 * _VIEW - 0.6 * _LEFT - 0.25 * _UP))
_HALF = _unit(tuple(_KEY + _VIEW))


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


def _leaves(shapes: dict) -> list[tuple[dict, tuple, tuple, Any]]:
    """Leaf shapes with their world transform (every node's `loc` composed down
    the tree), the nearest `color`, and the leaf's visibility `state`."""
    out: list[tuple[dict, tuple, tuple, Any]] = []

    def walk(o: Any, xform: tuple, color: tuple) -> None:
        if not isinstance(o, dict):
            return
        xform = _compose(xform, o.get("loc"))
        color = _hex_rgb(o.get("color")) or color
        if o.get("parts"):
            for p in o["parts"]:
                walk(p, xform, color)
        elif isinstance(o.get("shape"), dict):
            out.append((o["shape"], xform, color, o.get("state")))

    walk(shapes, _IDENT, _BASE)
    return out


def _vertex_normals(v: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Area-weighted vertex normals. Tessellators duplicate vertices along
    B-rep face boundaries, so this stays smooth within a face and sharp across."""
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    vn = np.zeros_like(v)
    for k in range(3):
        np.add.at(vn, t[:, k], fn)
    return vn


# --- rasterizer -------------------------------------------------------------------


def _raster(p: np.ndarray, h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Scanline-rasterize projected triangles ``p`` (M,3,3: px, py, depth) into a
    z-buffer. Returns (zbuf float32 (h,w), triangle id int32 (h,w), -1 = empty)."""
    zbuf = np.full(h * w, np.inf, dtype=np.float32)
    tid = np.full(h * w, -1, dtype=np.int32)
    x, y, z = p[:, :, 0], p[:, :, 1], p[:, :, 2]
    e1x, e1y = x[:, 1] - x[:, 0], y[:, 1] - y[:, 0]
    e2x, e2y = x[:, 2] - x[:, 0], y[:, 2] - y[:, 0]
    det = e1x * e2y - e2x * e1y
    r0 = np.maximum(np.ceil(y.min(axis=1) - 0.5), 0).astype(np.int64)
    r1 = np.minimum(np.floor(y.max(axis=1) - 0.5), h - 1).astype(np.int64)
    keep = np.nonzero((np.abs(det) > 1e-9) & (r1 >= r0))[0]
    if len(keep) == 0:
        return zbuf.reshape(h, w), tid.reshape(h, w)
    est = (r1[keep] - r0[keep] + 1) * (np.ptp(x[keep], axis=1) + 2)
    bounds = np.searchsorted(np.cumsum(est), np.arange(1, int(est.sum() // 3_000_000) + 2) * 3_000_000)
    start = 0
    for stop in [*bounds.tolist(), len(keep)]:
        stop = min(int(stop), len(keep))
        if stop <= start:
            continue
        _raster_chunk(keep[start:stop], x, y, z, e1x, e1y, e2x, e2y, det, r0, r1, w, zbuf, tid)
        start = stop
    return zbuf.reshape(h, w), tid.reshape(h, w)


def _raster_chunk(
    tris: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    e1x: np.ndarray,
    e1y: np.ndarray,
    e2x: np.ndarray,
    e2y: np.ndarray,
    det: np.ndarray,
    r0: np.ndarray,
    r1: np.ndarray,
    w: int,
    zbuf: np.ndarray,
    tid: np.ndarray,
) -> None:
    nrows = r1[tris] - r0[tris] + 1
    span_tri = np.repeat(tris, nrows)
    first = np.cumsum(nrows) - nrows
    row = r0[span_tri] + (np.arange(len(span_tri)) - np.repeat(first, nrows))
    yc = row + 0.5
    xl = np.full(len(span_tri), np.inf)
    xr = np.full(len(span_tri), -np.inf)
    for a, b in ((0, 1), (1, 2), (2, 0)):
        ya, yb = y[span_tri, a], y[span_tri, b]
        xa, xb = x[span_tri, a], x[span_tri, b]
        dy = yb - ya
        hit = (yc >= np.minimum(ya, yb)) & (yc <= np.maximum(ya, yb)) & (dy != 0)
        xi = xa + (yc - ya) * (xb - xa) / np.where(dy == 0, 1.0, dy)
        xl = np.where(hit, np.minimum(xl, xi), xl)
        xr = np.where(hit, np.maximum(xr, xi), xr)
    ok = np.isfinite(xl)
    c0 = np.maximum(np.ceil(np.where(ok, xl, 0) - 0.5), 0).astype(np.int64)
    c1 = np.minimum(np.floor(np.where(ok, xr, -1) - 0.5), w - 1).astype(np.int64)
    count = np.where(ok, np.maximum(c1 - c0 + 1, 0), 0)
    if not count.any():
        return
    ftri = np.repeat(span_tri, count)
    ffirst = np.cumsum(count) - count
    col = np.repeat(c0, count) + (np.arange(len(ftri)) - np.repeat(ffirst, count))
    frow = np.repeat(row, count)
    dx = col + 0.5 - x[ftri, 0]
    dy = frow + 0.5 - y[ftri, 0]
    d = det[ftri]
    l1 = (dx * e2y[ftri] - dy * e2x[ftri]) / d
    l2 = (e1x[ftri] * dy - e1y[ftri] * dx) / d
    z0 = z[ftri, 0]
    fz = (z0 + l1 * (z[ftri, 1] - z0) + l2 * (z[ftri, 2] - z0)).astype(np.float32)
    pix = frow * w + col
    np.minimum.at(zbuf, pix, fz)
    win = fz <= zbuf[pix]
    tid[pix[win]] = ftri[win].astype(np.int32)


def _png(rgba: np.ndarray) -> bytes:
    """Encode an (h, w, 4) uint8 array as PNG (filter 'Up' on every row)."""
    h, w, _ = rgba.shape
    up = rgba.astype(np.int16)
    up[1:] -= rgba[:-1]
    rows = np.empty((h, 1 + w * 4), dtype=np.uint8)
    rows[:, 0] = 2
    rows[0, 0] = 0
    rows[:, 1:] = (up & 0xFF).astype(np.uint8).reshape(h, -1)
    rows[0, 1:] = rgba[0].reshape(-1)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
        + chunk(b"IEND", b"")
    )


def _shift(a: np.ndarray, dy: int, dx: int, fill: Any) -> np.ndarray:
    """``out[i, j] = a[i + dy, j + dx]`` with ``fill`` past the border."""
    out = np.full_like(a, fill)
    h, w = a.shape
    out[max(-dy, 0) : h - max(dy, 0), max(-dx, 0) : w - max(dx, 0)] = a[
        max(dy, 0) : h - max(-dy, 0), max(dx, 0) : w - max(-dx, 0)
    ]
    return out


def _render(
    tv: np.ndarray,
    tn: np.ndarray,
    tcol: np.ndarray,
    tobj: np.ndarray,
    edges: np.ndarray,
    ecol: np.ndarray,
    size: int,
    pad: float,
    creases: bool,
) -> str:
    """Render world triangles ``tv`` (M,3,3) with corner normals ``tn`` (M,3,3),
    per-triangle colors/objects, and world edge segments ``edges`` (K,2,3) with
    per-segment colors → an SVG string embedding the raster."""
    ss = 3 if size <= 200 else 2
    pts = [tv.reshape(-1, 3)] + ([edges.reshape(-1, 3)] if len(edges) else [])
    proj = np.concatenate(pts) @ _PROJ
    lo = proj[:, :2].min(axis=0)
    span = np.maximum(proj[:, :2].max(axis=0) - lo, 1e-9)
    scale = (size - pad) / float(span.max())
    off = (size - span * scale) / 2 - lo * scale
    k = scale * ss
    hw = size * ss

    def to_px(world: np.ndarray) -> np.ndarray:
        q = world @ _PROJ
        q[..., :2] = q[..., :2] * k + off * ss
        q[..., 2] *= k
        return q

    p = to_px(tv)
    zbuf, tid = _raster(p, hw, hw)
    filled = tid >= 0
    rgb = np.zeros((hw, hw, 3))
    cover = filled.astype(float)
    obj = np.full((hw, hw), -1, dtype=np.int64)

    if filled.any():
        t = tid[filled]
        rows, cols = np.nonzero(filled)
        x, y = p[t, :, 0], p[t, :, 1]
        e1x, e1y, e2x, e2y = x[:, 1] - x[:, 0], y[:, 1] - y[:, 0], x[:, 2] - x[:, 0], y[:, 2] - y[:, 0]
        dx, dy = cols + 0.5 - x[:, 0], rows + 0.5 - y[:, 0]
        det = e1x * e2y - e2x * e1y
        l1 = np.clip((dx * e2y - dy * e2x) / det, 0, 1)
        l2 = np.clip((e1x * dy - e1y * dx) / det, 0, 1)
        l0 = np.clip(1 - l1 - l2, 0, 1)
        corners = tn[t]
        n = l0[:, None] * corners[:, 0] + l1[:, None] * corners[:, 1] + l2[:, None] * corners[:, 2]
        tri = tv[t]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        fn *= np.where(fn @ _VIEW < 0, -1.0, 1.0)[:, None]
        n *= np.where(np.einsum("ij,ij->i", n, fn) < 0, -1.0, 1.0)[:, None]
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
        key = np.clip(n @ _KEY, 0, 1)
        fill_l = np.clip(n @ _FILL, 0, 1)
        sky = 0.5 + 0.5 * n[:, 2]
        light = 0.30 + 0.12 * sky + 0.58 * key + 0.16 * fill_l
        spec = 0.10 * np.clip(n @ _HALF, 0, 1) ** 32
        rgb[filled] = tcol[t] * light[:, None] + 255.0 * spec[:, None]
        obj[filled] = tobj[t]

        line = np.zeros((hw, hw), dtype=bool)
        z = np.where(filled, zbuf, np.float32(np.inf))
        for sy, sx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nobj = _shift(obj, sy, sx, -1)
            nz = _shift(z, sy, sx, np.inf)
            line |= filled & (nobj != obj) & (nz >= z)
        for sy, sx in ((0, 1), (1, 0)):
            a, b = _shift(z, sy, sx, np.inf), _shift(z, -sy, -sx, np.inf)
            with np.errstate(invalid="ignore"):
                lap = np.abs(a + b - 2 * z)
            same = (_shift(obj, sy, sx, -1) == obj) & (_shift(obj, -sy, -sx, -1) == obj)
            line |= filled & same & (lap > 3.0 * ss)
        if creases:
            fnu = np.zeros((hw, hw, 3))
            fnu[filled] = fn / np.maximum(np.linalg.norm(fn, axis=1), 1e-12)[:, None]
            for sy, sx in ((0, 1), (1, 0)):
                other = np.stack([_shift(fnu[..., c], sy, sx, 0.0) for c in range(3)], axis=-1)
                same = _shift(obj, sy, sx, -1) == obj
                line |= filled & same & (np.einsum("ijk,ijk->ij", fnu, other) < 0.8)
        rgb[line] = rgb[line] * 0.45 + _EDGE_RGB * 0.55

    if len(edges):
        e = to_px(edges)
        seg = e[:, 1] - e[:, 0]
        n_s = np.maximum(np.ceil(np.hypot(seg[:, 0], seg[:, 1]) / 0.5).astype(np.int64), 1) + 1
        si = np.repeat(np.arange(len(e)), n_s)
        tpar = (np.arange(len(si)) - np.repeat(np.cumsum(n_s) - n_s, n_s)) / np.repeat(n_s - 1, n_s)
        sp = e[si, 0] + tpar[:, None] * seg[si]
        width = max(1, round(ss * min(1.0, size / 300)))
        bias = 1.5 * width + 1.5
        hit = np.zeros((hw, hw), dtype=bool)
        hcol = np.zeros((hw, hw, 3))
        for oy in range(width):
            for ox in range(width):
                c = np.floor(sp[:, 0] - width / 2 + 0.5 + ox).astype(np.int64)
                r = np.floor(sp[:, 1] - width / 2 + 0.5 + oy).astype(np.int64)
                inb = (c >= 0) & (c < hw) & (r >= 0) & (r < hw)
                c, r, zz, s = c[inb], r[inb], sp[inb, 2], si[inb]
                vis = zz <= zbuf[r, c] + bias
                hit[r[vis], c[vis]] = True
                hcol[r[vis], c[vis]] = ecol[s[vis]]
        a = 0.8
        rgb[hit] = np.where(filled[hit][:, None], rgb[hit] * (1 - a) + hcol[hit] * a, hcol[hit])
        cover[hit] = np.where(filled[hit], 1.0, a)

    pm = np.concatenate([rgb * cover[..., None], cover[..., None]], axis=-1)
    pm = pm.reshape(size, ss, size, ss, 4).mean(axis=(1, 3))
    alpha = pm[..., 3:]
    out = np.where(alpha > 0, pm[..., :3] / np.maximum(alpha, 1e-9), 0)
    rgba = np.concatenate([np.clip(out, 0, 255), alpha * 255], axis=-1).round().astype(np.uint8)
    href = "data:image/png;base64," + base64.b64encode(_png(rgba)).decode("ascii")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {size} {size}"><image width="{size}" height="{size}" '
        f'href="{href}" xlink:href="{href}"/></svg>'
    )


# --- public API -------------------------------------------------------------------


def iso_svg(part: Any, size: int = 140) -> str:
    """Shaded isometric SVG thumbnail for ``part``."""
    try:
        shapes, _states, _bbox = tessellate([part], names=["thumb"])
    except Exception:
        return ""
    return iso_svg_shapes(shapes, size)


def decimate(verts: Any, tris: Any, cells: int = 24) -> tuple[Any, Any]:
    """Grid vertex-clustering decimation — snap verts to a coarse ``cells``³ grid,
    merge coincident ones, drop the triangles that collapse."""
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
    ``cells`` is accepted for compatibility; the raster renders the full mesh."""
    del cells
    v = np.asarray(verts, dtype=float).reshape(-1, 3)
    t = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if len(v) == 0 or len(t) == 0:
        return ""
    t = t[(t < len(v)).all(axis=1)]
    vn = _vertex_normals(v, t)
    m = len(t)
    return _render(
        v[t],
        vn[t],
        np.broadcast_to(np.asarray(color, dtype=float), (m, 3)),
        np.zeros(m, dtype=np.int64),
        np.zeros((0, 2, 3)),
        np.zeros((0, 3)),
        size,
        8,
        creases=True,
    )


def iso_svg_shapes(shapes: dict, size: int = 140) -> str:
    """Shaded isometric SVG from an already-tessellated shapes tree (any number
    of parts) — used for library thumbnails AND the agent's vision sanity pass."""
    tvs, tns, tcols, tobjs, segs, scols = [], [], [], [], [], []
    for i, (shape, (tpos, tq), base_rgb, state) in enumerate(_leaves(shapes)):
        rot = np.array([_qrot(tq, e) for e in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))])
        pos = np.asarray(tpos, dtype=float)
        rgb = np.asarray(base_rgb, dtype=float)
        show_faces = not (isinstance(state, list) and state and state[0] == 0)
        show_edges = not (isinstance(state, list) and len(state) > 1 and state[1] == 0)

        verts = np.asarray(shape.get("vertices") or [], dtype=float).reshape(-1, 3)
        idx = np.asarray(shape.get("triangles") or [], dtype=np.int64)
        idx = idx[: len(idx) // 3 * 3].reshape(-1, 3)
        idx = idx[(idx < len(verts)).all(axis=1)]
        if show_faces and len(idx):
            wv = verts @ rot + pos
            nrm = np.asarray(shape.get("normals") or [], dtype=float).reshape(-1, 3)
            wn = nrm @ rot if len(nrm) == len(verts) else _vertex_normals(wv, idx)
            tvs.append(wv[idx])
            tns.append(wn[idx])
            tcols.append(np.broadcast_to(rgb, (len(idx), 3)))
            tobjs.append(np.full(len(idx), i, dtype=np.int64))

        edata = np.asarray(shape.get("edges") or [], dtype=float).ravel()
        if show_edges and len(edata) >= 6:
            seg = edata[: len(edata) // 6 * 6].reshape(-1, 2, 3) @ rot + pos
            segs.append(seg)
            scols.append(np.broadcast_to(rgb * 0.18 + _EDGE_RGB * 0.82, (len(seg), 3)))

    if not tvs and not segs:
        return ""
    return _render(
        np.concatenate(tvs) if tvs else np.zeros((0, 3, 3)),
        np.concatenate(tns) if tns else np.zeros((0, 3, 3)),
        np.concatenate(tcols) if tcols else np.zeros((0, 3)),
        np.concatenate(tobjs) if tobjs else np.zeros(0, dtype=np.int64),
        np.concatenate(segs) if segs else np.zeros((0, 2, 3)),
        np.concatenate(scols) if scols else np.zeros((0, 3)),
        size,
        18,
        creases=False,
    )
