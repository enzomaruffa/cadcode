"""Slicer-grade print estimates from the real mesh (no more volume ÷ flow).

The old heuristic treated every part as 100% solid extruded at constant flow —
wildly wrong for real FDM prints (2-3 walls + ~15% infill + solid skins). This
module does what a slicer does, on the tessellated triangles:

  * slice the mesh into layers; per layer get the EXACT cross-section area and
    perimeter length (oriented triangle/plane intersection segments — Green's
    theorem gives the area without stitching loops);
  * time each feature class separately: walls at wall speed, sparse infill at
    infill speed, solid top/bottom skins, support; all flow-capped;
  * add travel factor + per-layer overhead (z change, wipes, accel).

The same mesh drives orientation scoring: rotating vertices is free, so the
planner tessellates a part once and scores all six orientations on the mesh
(support area, height, footprint, estimated minutes).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# A middle-of-the-road 0.4mm-nozzle PLA profile (Prusa/Bambu-ish defaults).
PROFILE: dict[str, float] = {
    "layer_h": 0.2,  # mm
    "line_w": 0.42,  # mm
    "walls": 2,  # perimeter count
    "top_layers": 4,
    "bottom_layers": 3,
    "infill": 0.15,  # sparse density
    "wall_speed": 45.0,  # mm/s
    "infill_speed": 90.0,
    "solid_speed": 55.0,
    "support_speed": 80.0,
    "max_flow": 11.0,  # mm³/s volumetric ceiling
    "travel_factor": 1.12,  # non-print moves
    "layer_overhead_s": 1.0,  # z move + accel/decel per layer
}

OVERHANG_COS = -math.sin(math.radians(45.0))  # nz below this ⇒ needs support


def mesh_of(obj: Any) -> tuple[np.ndarray, np.ndarray]:
    """Tessellate one build123d/OCP solid → (vertices (N,3), triangles (M,3))."""
    from ocp_tessellate import convert as C

    group, instances = C.to_ocpgroup(obj)
    insts = C.tessellate_group(group, instances)[0]
    v = np.asarray(insts[0]["vertices"], dtype=np.float64).reshape(-1, 3)
    t = np.asarray(insts[0]["triangles"], dtype=np.int64).reshape(-1, 3)
    return v, t


def rotate_mesh(verts: np.ndarray, rx_deg: float, ry_deg: float) -> np.ndarray:
    """Rotate vertices about X then Y (matches build123d Rot(X=…, Y=…))."""
    out = verts
    if rx_deg:
        a = math.radians(rx_deg)
        c, s = math.cos(a), math.sin(a)
        out = out @ np.array([[1, 0, 0], [0, c, s], [0, -s, c]])
    if ry_deg:
        a = math.radians(ry_deg)
        c, s = math.cos(a), math.sin(a)
        out = out @ np.array([[c, 0, -s], [0, 1, 0], [s, 0, c]])
    return out


def _tri_geometry(verts: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(areas (M,), unit normals (M,3)) for every triangle."""
    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    cross = np.cross(p1 - p0, p2 - p0)
    norm = np.linalg.norm(cross, axis=1)
    areas = 0.5 * norm
    with np.errstate(invalid="ignore", divide="ignore"):
        normals = cross / np.where(norm[:, None] > 0, norm[:, None], 1.0)
    return areas, normals


def support_area(verts: np.ndarray, tris: np.ndarray) -> float:
    """Total downward-facing area past the overhang limit, excluding faces
    resting on the bed (the mesh's own min Z)."""
    areas, normals = _tri_geometry(verts, tris)
    zmin = float(verts[:, 2].min())
    centers_z = verts[tris].mean(axis=1)[:, 2]
    over = normals[:, 2] < OVERHANG_COS
    on_bed = (centers_z - zmin) < 0.05
    flat_down = normals[:, 2] < -0.999
    return float(areas[over & ~(on_bed & flat_down)].sum())


def _layer_sections(verts: np.ndarray, tris: np.ndarray, layer_h: float) -> tuple[np.ndarray, np.ndarray]:
    """Slice the mesh: per layer (perimeter_mm, cross_section_mm²), exact.

    Each crossing triangle contributes one oriented segment; with a manifold
    mesh the segments of every loop are consistently oriented, so Green's
    theorem sums the area edge-by-edge — no loop stitching needed."""
    tv = verts[tris]  # (M, 3, 3)
    z = tv[:, :, 2]
    zmin, zmax = z.min(axis=1), z.max(axis=1)
    lo, hi = float(verts[:, 2].min()), float(verts[:, 2].max())
    n_layers = max(int(math.ceil((hi - lo) / layer_h)), 1)
    perims = np.zeros(n_layers)
    areas = np.zeros(n_layers)

    # edge pairs of each triangle: (0,1), (1,2), (2,0)
    ea = tv[:, [0, 1, 2], :]  # start points
    eb = tv[:, [1, 2, 0], :]  # end points

    for li in range(n_layers):
        zc = lo + (li + 0.5) * layer_h + 1e-7  # mid-layer, nudged off vertices
        sel = (zmin <= zc) & (zmax > zc)
        if not sel.any():
            continue
        A, B = ea[sel], eb[sel]  # (K, 3, 3)
        za, zb = A[:, :, 2], B[:, :, 2]
        cross_edge = (za - zc) * (zb - zc) < 0  # (K, 3) — exactly two per triangle
        with np.errstate(invalid="ignore", divide="ignore"):
            t = (zc - za) / np.where(zb != za, zb - za, 1.0)
        pts = A + (B - A) * t[:, :, None]  # (K, 3, 3)
        order = np.argsort(~cross_edge, axis=1)  # valid edges first
        k = np.arange(len(A))
        p1 = pts[k, order[:, 0], :2]
        p2 = pts[k, order[:, 1], :2]
        ok = cross_edge.sum(axis=1) == 2
        p1, p2 = p1[ok], p2[ok]
        # orient each segment along (triangle normal × ẑ) so loops run consistently
        n3 = np.cross(B[ok, 1] - A[ok, 1], B[ok, 2] - A[ok, 2])  # ≈ face normal per tri
        want = np.stack([n3[:, 1], -n3[:, 0]], axis=1)
        flip = ((p2 - p1) * want).sum(axis=1) < 0
        p1[flip], p2[flip] = p2[flip].copy(), p1[flip].copy()
        perims[li] = float(np.linalg.norm(p2 - p1, axis=1).sum())
        areas[li] = abs(0.5 * float((p1[:, 0] * p2[:, 1] - p2[:, 0] * p1[:, 1]).sum()))
    return perims, areas


def estimate_minutes(
    verts: np.ndarray,
    tris: np.ndarray,
    profile: dict[str, float] | None = None,
    support_mm2: float | None = None,
) -> dict[str, float]:
    """Feature-based print time for one part, in minutes (+ breakdown)."""
    p = {**PROFILE, **(profile or {})}
    lh, lw = p["layer_h"], p["line_w"]
    flow_cap = p["max_flow"] / (lw * lh)  # mm/s ceiling from volumetric flow

    def speed(s: float) -> float:
        return min(s, flow_cap)

    perims, areas = _layer_sections(verts, tris, lh)
    n_layers = len(perims)

    # walls: perimeter loops per layer
    wall_len = float(perims.sum()) * p["walls"]
    wall_s = wall_len / speed(p["wall_speed"])
    wall_vol = wall_len * lw * lh

    # solid skins: up/down projected areas × solid layer counts
    t_areas, normals = _tri_geometry(verts, tris)
    up_proj = float((t_areas * np.clip(normals[:, 2], 0, None)).sum())
    down_proj = float((t_areas * np.clip(-normals[:, 2], 0, None)).sum())
    sliced_vol = float(areas.sum()) * lh
    skin_vol = (up_proj * p["top_layers"] + down_proj * p["bottom_layers"]) * lh
    skin_vol = min(skin_vol, sliced_vol)  # a part thinner than top+bottom layers is just solid
    skin_s = (skin_vol / (lw * lh)) / speed(p["solid_speed"])

    # sparse infill fills what's left of the sliced volume
    infill_vol = max(sliced_vol - wall_vol - skin_vol, 0.0) * p["infill"]
    infill_s = (infill_vol / (lw * lh)) / speed(p["infill_speed"])

    # support: thin curtain under overhangs (area × equivalent solid thickness)
    sup = support_mm2 if support_mm2 is not None else support_area(verts, tris)
    support_vol = sup * 0.8
    support_s = (support_vol / (lw * lh)) / speed(p["support_speed"])

    total_s = (wall_s + skin_s + infill_s + support_s) * p["travel_factor"] + n_layers * p["layer_overhead_s"]
    return {
        "minutes": round(total_s / 60.0, 1),
        "wall_min": round(wall_s / 60.0, 1),
        "skin_min": round(skin_s / 60.0, 1),
        "infill_min": round(infill_s / 60.0, 1),
        "support_min": round(support_s / 60.0, 1),
        "layers": n_layers,
        "extruded_mm3": round(wall_vol + skin_vol + infill_vol + support_vol, 1),
    }


def estimate_objects_minutes(objs: list[Any]) -> float:
    """Total estimated minutes for already-positioned solids (e.g. a plate)."""
    total = 0.0
    for obj in objs:
        v, t = mesh_of(obj)
        total += estimate_minutes(v, t)["minutes"]
    return round(total, 1)
