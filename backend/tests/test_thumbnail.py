import base64
import io
import re

import numpy as np
from PIL import Image

from app.thumbnail import iso_svg_mesh, iso_svg_shapes


def _box(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> dict:
    corners = np.array([[(lo, hi)[(i >> k) & 1][k] for k in range(3)] for i in range(8)], dtype=float)
    faces = [
        ((0, 2, 6, 4), (-1, 0, 0)),
        ((1, 5, 7, 3), (1, 0, 0)),
        ((0, 4, 5, 1), (0, -1, 0)),
        ((2, 3, 7, 6), (0, 1, 0)),
        ((0, 1, 3, 2), (0, 0, -1)),
        ((4, 6, 7, 5), (0, 0, 1)),
    ]
    verts, normals, tris = [], [], []
    for quad, n in faces:
        base = len(verts) // 3
        for c in quad:
            verts.extend(corners[c].tolist())
            normals.extend(n)
        tris.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    edges = []
    for a in range(8):
        for k in range(3):
            if not (a >> k) & 1:
                edges.extend([corners[a].tolist(), corners[a | (1 << k)].tolist()])
    return {"vertices": verts, "normals": normals, "triangles": tris, "edges": edges}


def _leaf(name: str, color: str, shape: dict) -> dict:
    return {"name": name, "color": color, "state": [1, 1], "shape": shape}


def _pixels(svg: str) -> np.ndarray:
    m = re.search(r'href="data:image/png;base64,([^"]+)"', svg)
    assert m, svg[:200]
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(m.group(1)))).convert("RGBA")).astype(int)


def _red(px: np.ndarray) -> int:
    r, g, b, a = px[..., 0], px[..., 1], px[..., 2], px[..., 3]
    return int(((a > 0) & (r > g + 60) & (r > b + 60)).sum())


def test_hidden_box_contributes_no_pixels():
    outer = _leaf("outer", "#3050e0", _box((0, 0, 0), (20, 20, 20)))
    inner = _leaf("inner", "#e03030", _box((5, 5, 5), (15, 15, 15)))
    px = _pixels(iso_svg_shapes({"parts": [inner, outer]}, size=140))
    assert px.shape == (140, 140, 4)
    assert _red(px) == 0
    assert (px[..., 3] > 0).sum() > 1000


def test_box_beside_is_visible():
    left = _leaf("left", "#3050e0", _box((0, 0, 0), (20, 20, 20)))
    right = _leaf("right", "#e03030", _box((30, 0, 0), (40, 10, 10)))
    px = _pixels(iso_svg_shapes({"parts": [left, right]}, size=140))
    assert _red(px) > 100


def test_background_is_transparent():
    px = _pixels(iso_svg_shapes({"parts": [_leaf("b", "#3050e0", _box((0, 0, 0), (10, 10, 10)))]}, size=96))
    assert px[0, 0, 3] == 0 and px[-1, -1, 3] == 0


def test_mesh_thumbnail_keeps_size():
    shape = _box((0, 0, 0), (10, 20, 5))
    svg = iso_svg_mesh(np.reshape(shape["vertices"], (-1, 3)), np.reshape(shape["triangles"], (-1, 3)), size=104)
    assert 'viewBox="0 0 104 104"' in svg
    assert _pixels(svg).shape == (104, 104, 4)
    assert iso_svg_mesh([], [], size=104) == ""
