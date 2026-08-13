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

# The geometric feature 'seconds'. Each maps to a calibration GROUP: everything
# non-support is scaled by one `base` multiplier, support by its own `support`
# multiplier. Two robust knobs (overall speed bias + the gnarly support bias)
# instead of five collinear per-feature coefficients that overfit on few samples.
FEATURES = ("wall_s", "skin_s", "infill_s", "support_s", "layers")
GROUP = {"wall_s": "base", "skin_s": "base", "infill_s": "base", "support_s": "support", "layers": "base"}
OVERHANG_COS = -math.sin(math.radians(45.0))  # nz below this ⇒ needs support


def default_coeffs(profile: dict[str, float] | None = None) -> dict[str, float]:
    """Hand-tuned per-feature seconds→seconds weights (travel + overhead)."""
    p = {**PROFILE, **(profile or {})}
    tf = p["travel_factor"]
    return {"wall_s": tf, "skin_s": tf, "infill_s": tf, "support_s": tf, "layers": p["layer_overhead_s"]}


def default_mult() -> dict[str, float]:
    return {"base": 1.0, "support": 1.0}


def base_support_seconds(features: dict[str, float]) -> tuple[float, float]:
    """Split feature-seconds into (base_seconds, support_seconds) at default
    weights — the two regressors calibration fits a multiplier for."""
    dc = default_coeffs()
    base = sum(dc[k] * features.get(k, 0.0) for k in FEATURES if GROUP[k] == "base")
    support = sum(dc[k] * features.get(k, 0.0) for k in FEATURES if GROUP[k] == "support")
    return base, support


def combine(features: dict[str, float], mult: dict[str, float] | None = None) -> float:
    """minutes = (base_scale·base_seconds + support_scale·support_seconds) / 60."""
    m = mult or default_mult()
    base, support = base_support_seconds(features)
    return round((m.get("base", 1.0) * base + m.get("support", 1.0) * support) / 60.0, 1)


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


def candidate_down_dirs(n_extra: int = 42) -> list[tuple[str, np.ndarray]]:
    """Orientation candidates as (label, unit direction that will face the BED).
    The 6 principal directions plus a Fibonacci-sphere sweep — so 45° tilts (a
    self-supporting "teardrop" hole) and every in-between are actually tried."""
    out: list[tuple[str, np.ndarray]] = [
        ("as-is", np.array([0.0, 0.0, -1.0])),
        ("upside-down", np.array([0.0, 0.0, 1.0])),
        ("on side +X", np.array([1.0, 0.0, 0.0])),
        ("on side -X", np.array([-1.0, 0.0, 0.0])),
        ("on side +Y", np.array([0.0, 1.0, 0.0])),
        ("on side -Y", np.array([0.0, -1.0, 0.0])),
    ]
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(n_extra):
        z = 1.0 - 2.0 * (i + 0.5) / n_extra
        r = math.sqrt(max(1.0 - z * z, 0.0))
        th = golden * i
        d = np.array([r * math.cos(th), r * math.sin(th), z])
        tilt = math.degrees(math.acos(max(min(-d[2], 1.0), -1.0)))  # vs printing as-is
        out.append((f"tilted {tilt:.0f}°", d))
    return out


def rotation_to_down(d: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Rotation taking part-frame direction `d` to world -Z (the bed). Returns
    (R 3×3, axis, angle°) — R for the mesh, axis/angle for the build123d solid."""
    d = d / (np.linalg.norm(d) or 1.0)
    target = np.array([0.0, 0.0, -1.0])
    c = float(np.dot(d, target))
    if c > 1.0 - 1e-9:
        return np.eye(3), np.array([1.0, 0.0, 0.0]), 0.0
    if c < -1.0 + 1e-9:  # 180° — any horizontal axis works
        return np.diag([1.0, -1.0, -1.0]), np.array([1.0, 0.0, 0.0]), 180.0
    axis = np.cross(d, target)
    axis = axis / np.linalg.norm(axis)
    angle = math.acos(max(min(c, 1.0), -1.0))
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    r = np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * (k @ k)
    return r, axis, math.degrees(angle)


def build_occupancy(verts: np.ndarray, tris: np.ndarray, res: int = 40) -> dict:
    """Coarse surface-occupancy grid in the part's ORIGINAL frame — sample every
    triangle at ~cell spacing and mark the cells. Built once per part; the
    removability rays are rotated into this frame per candidate (no rebuild)."""
    lo = verts.min(axis=0)
    hi = verts.max(axis=0)
    span = np.maximum(hi - lo, 1e-6)
    cell = float(span.max() / res)
    dims = np.maximum((span / cell).astype(int) + 1, 1)
    grid = np.zeros(dims, dtype=bool)

    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    e1 = np.linalg.norm(p1 - p0, axis=1)
    e2 = np.linalg.norm(p2 - p0, axis=1)
    # 1.5× oversampling so even long thin triangles (a cylinder wall spans the
    # whole height in 2 tris) leave no gaps a ray could slip through.
    steps = np.clip(np.ceil(1.5 * np.maximum(e1, e2) / cell).astype(int), 1, 96)
    for i in range(len(tris)):
        n = int(steps[i])
        u = np.linspace(0, 1, n + 1)
        uu, vv = np.meshgrid(u, u)
        m = uu + vv <= 1.0
        uu, vv = uu[m], vv[m]
        pts = p0[i] + uu[:, None] * (p1[i] - p0[i]) + vv[:, None] * (p2[i] - p0[i])
        idx = np.clip(((pts - lo) / cell).astype(int), 0, dims - 1)
        grid[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return {"grid": grid, "lo": lo, "cell": cell, "dims": dims}


def support_cost(verts_rot: np.ndarray, tris: np.ndarray, occ: dict, r: np.ndarray) -> float:
    """Removability-weighted support cost (weighted mm²) for one candidate
    orientation. Every overhang face contributes its area × a penalty for how
    ENCLOSED its support column is: 8 horizontal escape rays (in the candidate
    frame) are marched through the part's occupancy grid — support you can't
    reach (inside a bore/pocket) costs up to 10×, open external support ~1×.
    This is what makes the optimizer prefer YOUR upside-down choice.

    `verts_rot` must be the ROTATED-only mesh (no bed-drop translation), so
    `c @ r` maps ray origins exactly back into the grid's original frame."""
    areas, normals, centroids = _tri_geometry(verts_rot, tris)
    zmin = float(verts_rot[:, 2].min())
    mask = _overhang_mask(normals, centroids, zmin)
    if not mask.any():
        return 0.0
    a = areas[mask]
    c = centroids[mask]
    # cap the ray work: keep the largest ~120 faces (they dominate the cost)
    if len(a) > 120:
        keep = np.argsort(a)[-120:]
        a, c = a[keep], c[keep]

    cell = occ["cell"]
    grid = occ["grid"]
    lo = occ["lo"]
    dims = occ["dims"]
    # candidate-frame directions → original frame (rays march the unrotated grid)
    down = r.T @ np.array([0.0, 0.0, -1.0])
    thetas = np.arange(8) * (math.pi / 4)
    dirs = np.stack([np.cos(thetas), np.sin(thetas), np.zeros(8)], axis=1) @ r  # (8,3) original frame
    # start just below each face (where the support column lives), original frame
    starts = (c @ r) + down * (2.0 * cell)  # rotate centroids back: c_orig = c_rot @ R

    n_steps = int(max(dims.max(), 8))
    t = (np.arange(1, n_steps + 1) * cell)[None, None, :, None]  # (1,1,S,1)
    pts = starts[:, None, None, :] + dirs[None, :, None, :] * t  # (P,8,S,3)
    idx = ((pts - lo) / cell).astype(int)
    inside = ((idx >= 0) & (idx < dims)).all(axis=3)
    ii = np.clip(idx, 0, dims - 1)
    occ_hit = grid[ii[..., 0], ii[..., 1], ii[..., 2]] & inside
    blocked = occ_hit.any(axis=2)  # (P,8) — ray hits the part before escaping
    frac = blocked.mean(axis=1)
    weight = 1.0 + 9.0 * frac**2  # fully enclosed ⇒ 10×
    return float((a * weight).sum())


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


def support_volume(verts: np.ndarray, tris: np.ndarray) -> float:
    """Support LATTICE volume proxy (mm³): overhang footprint × drop to the bed.
    The honest ranking currency — a floor ring hovering 2.5 mm up costs a dusting
    of support however large its area, while the same area 100 mm up is a forest."""
    return _support_volume(verts, tris, 1.0)


def bed_contact_area(verts: np.ndarray, tris: np.ndarray, tol: float = 0.4) -> float:
    """Flat area actually touching the bed (mm²) — a downward-facing face sitting
    within `tol` of the lowest point. This is the real adhesion/stability proxy:
    a big flat base is solid; a part balanced on a tiny tip or an edge has almost
    none, which is exactly the tippy orientation a human rejects on sight."""
    areas, normals, centroids = _tri_geometry(verts, tris)
    zmin = float(verts[:, 2].min())
    on_bed = (centroids[:, 2] - zmin) <= tol
    flat_down = normals[:, 2] < -0.966  # within ~15° of straight down
    return float(areas[on_bed & flat_down].sum())


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


def _mult() -> dict[str, float]:
    try:
        from app.kernel.calibration import mult

        return mult()
    except Exception:
        return default_mult()


def estimate_minutes(
    verts: np.ndarray,
    tris: np.ndarray,
    profile: dict[str, float] | None = None,
    support_mm2: float | None = None,  # kept for call compatibility (unused)
) -> dict[str, float]:
    """Calibrated print-time (minutes) + feature breakdown for one part."""
    feats = part_features(verts, tris, profile)
    return {**feats, "minutes": combine(feats, _mult())}


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
    return combine(plate_features(meshes, profile), _mult())


def estimate_objects_minutes(objs: list[Any]) -> float:
    """Calibrated plate estimate for positioned solids (e.g. massprops readout)."""
    if not objs:
        return 0.0
    return plate_minutes([mesh_of(o) for o in objs])
