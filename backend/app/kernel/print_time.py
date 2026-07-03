"""Print-time estimates from the real mesh, feature-based + self-calibrating.

Two tiers of accuracy:
  * this module — instant, mesh-only. Slices the tessellated triangles into
    layers, measures each layer's cross-section + perimeter, and times each
    feature class (walls / sparse infill / solid skins / support) with real
    speeds + flow caps. Fast enough to score six orientations per part.
  * `slicer.py` — headless PrusaSlicer, the ground truth, on demand.

The instant estimate is a LINEAR model over geometric "feature seconds":

    minutes = Σ coeff[k] · feature_seconds[k] / 60

with coefficients that start at hand-tuned defaults and are refit from every
real slicer run (`calibration.py`) — so the gap to the slicer closes over time,
per feature (support gets its own coefficient, which is where the bias lived).

Key model choices vs. the naive volume÷flow heuristic:
  * support cost scales with overhang AREA × the HEIGHT it must descend to the
    bed (a tall overhang needs a tall support column), not area alone;
  * a plate's time aggregates EXTRUSION across all parts but pays per-layer
    overhead once per plate layer (parts print together), not per part.
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
    "support_density": 0.14,  # sparse support lattice
    "wall_speed": 45.0,  # mm/s
    "infill_speed": 90.0,
    "solid_speed": 55.0,
    "support_speed": 80.0,
    "max_flow": 11.0,  # mm³/s volumetric ceiling
    "travel_factor": 1.12,  # non-print moves (default coeff for extrusion features)
    "layer_overhead_s": 1.2,  # z move + accel/decel per plate layer (default layer coeff)
}

# Order of the calibrated feature vector.
FEATURES = ("wall_s", "skin_s", "infill_s", "support_s", "layers")
OVERHANG_COS = -math.sin(math.radians(45.0))  # nz below this ⇒ needs support


def default_coeffs(profile: dict[str, float] | None = None) -> dict[str, float]:
    p = {**PROFILE, **(profile or {})}
    tf = p["travel_factor"]
    return {"wall_s": tf, "skin_s": tf, "infill_s": tf, "support_s": tf, "layers": p["layer_overhead_s"]}


def combine(features: dict[str, float], coeffs: dict[str, float]) -> float:
    """minutes = Σ coeff·feature_seconds / 60 (layers term is seconds/layer)."""
    total = sum(coeffs.get(k, 0.0) * features.get(k, 0.0) for k in FEATURES)
    return round(total / 60.0, 1)


# --- meshing ----------------------------------------------------------------


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


def _tri_geometry(verts: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(areas (M,), unit normals (M,3), centroids (M,3)) per triangle."""
    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    cross = np.cross(p1 - p0, p2 - p0)
    norm = np.linalg.norm(cross, axis=1)
    areas = 0.5 * norm
    with np.errstate(invalid="ignore", divide="ignore"):
        normals = cross / np.where(norm[:, None] > 0, norm[:, None], 1.0)
    centroids = (p0 + p1 + p2) / 3.0
    return areas, normals, centroids


def _overhang_mask(normals: np.ndarray, centroids: np.ndarray, zmin: float) -> np.ndarray:
    """Downward faces past the overhang limit, excluding flat undersides on the bed."""
    over = normals[:, 2] < OVERHANG_COS
    on_bed = (centroids[:, 2] - zmin) < 0.05
    flat_down = normals[:, 2] < -0.999
    return over & ~(on_bed & flat_down)


def support_area(verts: np.ndarray, tris: np.ndarray) -> float:
    """Total overhang face area needing support (for display / ranking)."""
    areas, normals, centroids = _tri_geometry(verts, tris)
    return float(areas[_overhang_mask(normals, centroids, float(verts[:, 2].min()))].sum())


def _support_volume(verts: np.ndarray, tris: np.ndarray, density: float) -> float:
    """Sparse support material volume: each overhang triangle needs a column of
    lattice from it down to the bed — so cost ∝ (horizontal area × drop height).
    Horizontal area = the triangle's area projected onto XY (|nz|)."""
    areas, normals, centroids = _tri_geometry(verts, tris)
    mask = _overhang_mask(normals, centroids, float(verts[:, 2].min()))
    if not mask.any():
        return 0.0
    horiz = areas[mask] * np.abs(normals[mask, 2])  # footprint of each overhang
    drop = np.clip(centroids[mask, 2] - float(verts[:, 2].min()), 0.0, None)
    return float((horiz * drop * density).sum())


def _layer_sections(verts: np.ndarray, tris: np.ndarray, layer_h: float) -> tuple[np.ndarray, np.ndarray]:
    """Slice: per layer (perimeter_mm, cross_section_mm²), exact.

    Each crossing triangle contributes one oriented segment; on a manifold mesh
    the segments of every loop are consistently oriented, so Green's theorem
    sums the area edge-by-edge — no loop stitching."""
    tv = verts[tris]  # (M, 3, 3)
    z = tv[:, :, 2]
    zmin, zmax = z.min(axis=1), z.max(axis=1)
    lo, hi = float(verts[:, 2].min()), float(verts[:, 2].max())
    n_layers = max(int(math.ceil((hi - lo) / layer_h)), 1)
    perims = np.zeros(n_layers)
    areas = np.zeros(n_layers)

    ea = tv[:, [0, 1, 2], :]  # edge starts
    eb = tv[:, [1, 2, 0], :]  # edge ends

    for li in range(n_layers):
        zc = lo + (li + 0.5) * layer_h + 1e-7  # mid-layer, nudged off vertices
        sel = (zmin <= zc) & (zmax > zc)
        if not sel.any():
            continue
        A, B = ea[sel], eb[sel]
        za, zb = A[:, :, 2], B[:, :, 2]
        cross_edge = (za - zc) * (zb - zc) < 0
        with np.errstate(invalid="ignore", divide="ignore"):
            t = (zc - za) / np.where(zb != za, zb - za, 1.0)
        pts = A + (B - A) * t[:, :, None]
        order = np.argsort(~cross_edge, axis=1)
        k = np.arange(len(A))
        p1 = pts[k, order[:, 0], :2]
        p2 = pts[k, order[:, 1], :2]
        ok = cross_edge.sum(axis=1) == 2
        p1, p2 = p1[ok], p2[ok]
        n3 = np.cross(B[ok, 1] - A[ok, 1], B[ok, 2] - A[ok, 2])
        want = np.stack([n3[:, 1], -n3[:, 0]], axis=1)
        flip = ((p2 - p1) * want).sum(axis=1) < 0
        p1[flip], p2[flip] = p2[flip].copy(), p1[flip].copy()
        perims[li] = float(np.linalg.norm(p2 - p1, axis=1).sum())
        areas[li] = abs(0.5 * float((p1[:, 0] * p2[:, 1] - p2[:, 0] * p1[:, 1]).sum()))
    return perims, areas


# --- features + estimate ----------------------------------------------------


def part_features(verts: np.ndarray, tris: np.ndarray, profile: dict[str, float] | None = None) -> dict[str, float]:
    """Geometric feature 'seconds' for ONE part (before calibration coeffs):
    {wall_s, skin_s, infill_s, support_s, layers, extruded_mm3}."""
    p = {**PROFILE, **(profile or {})}
    lh, lw = p["layer_h"], p["line_w"]
    flow_cap = p["max_flow"] / (lw * lh)  # mm/s speed ceiling from volumetric flow

    def speed(s: float) -> float:
        return min(s, flow_cap)

    perims, areas = _layer_sections(verts, tris, lh)
    n_layers = float(len(perims))

    wall_len = float(perims.sum()) * p["walls"]
    wall_s = wall_len / speed(p["wall_speed"])
    wall_vol = wall_len * lw * lh

    t_areas, normals, _c = _tri_geometry(verts, tris)
    up_proj = float((t_areas * np.clip(normals[:, 2], 0, None)).sum())
    down_proj = float((t_areas * np.clip(-normals[:, 2], 0, None)).sum())
    sliced_vol = float(areas.sum()) * lh
    skin_vol = min((up_proj * p["top_layers"] + down_proj * p["bottom_layers"]) * lh, sliced_vol)
    skin_s = (skin_vol / (lw * lh)) / speed(p["solid_speed"])

    infill_vol = max(sliced_vol - wall_vol - skin_vol, 0.0) * p["infill"]
    infill_s = (infill_vol / (lw * lh)) / speed(p["infill_speed"])

    support_vol = _support_volume(verts, tris, p["support_density"])
    support_s = (support_vol / (lw * lh)) / speed(p["support_speed"])

    return {
        "wall_s": wall_s,
        "skin_s": skin_s,
        "infill_s": infill_s,
        "support_s": support_s,
        "layers": n_layers,
        "extruded_mm3": round(wall_vol + skin_vol + infill_vol + support_vol, 1),
    }


def _coeffs() -> dict[str, float]:
    try:
        from app.kernel.calibration import coeffs

        return coeffs()
    except Exception:
        return default_coeffs()


def estimate_minutes(
    verts: np.ndarray,
    tris: np.ndarray,
    profile: dict[str, float] | None = None,
    support_mm2: float | None = None,  # kept for call compatibility (unused)
) -> dict[str, float]:
    """Calibrated print-time (minutes) + feature breakdown for one part."""
    feats = part_features(verts, tris, profile)
    c = _coeffs()
    return {**feats, "minutes": combine(feats, c)}


def plate_features(
    meshes: list[tuple[np.ndarray, np.ndarray]], profile: dict[str, float] | None = None
) -> dict[str, float]:
    """Aggregate features for a whole PLATE: extrusion sums across parts, but the
    layer count is the MAX (parts print together, one overhead per plate layer)."""
    agg = {"wall_s": 0.0, "skin_s": 0.0, "infill_s": 0.0, "support_s": 0.0, "layers": 0.0, "extruded_mm3": 0.0}
    for v, t in meshes:
        f = part_features(v, t, profile)
        for k in ("wall_s", "skin_s", "infill_s", "support_s", "extruded_mm3"):
            agg[k] += f[k]
        agg["layers"] = max(agg["layers"], f["layers"])
    return agg


def plate_minutes(meshes: list[tuple[np.ndarray, np.ndarray]], profile: dict[str, float] | None = None) -> float:
    """Calibrated print-time for a plate of already-positioned meshes."""
    return combine(plate_features(meshes, profile), _coeffs())


def estimate_objects_minutes(objs: list[Any]) -> float:
    """Calibrated plate estimate for positioned solids (e.g. massprops readout)."""
    if not objs:
        return 0.0
    return plate_minutes([mesh_of(o) for o in objs])
