"""Leak detection by void-connectivity flood fill — tier-1 "fluids".

Not CFD: the assembly's solids are shell-voxelized into a cropped fine grid, the
void is labeled into 6-connected components (one C-speed ``scipy.ndimage.label``
call), and a leak is any component that touches an inlet AND escapes the region
somewhere that is not a declared outlet. Gravity-driven bulk flow follows
exactly these paths; anything narrower than ``min_gap`` is treated as sealed
(capillary films are out of scope, and the spec message says so).

The funnel v1 failure this exists to catch: liquid in the wetted bore dove into
the 0.3 mm plug↔bore fit gap and drained out through the bayonet entry slots —
a void path from inlet to exterior that bypassed the outlet.
"""

from __future__ import annotations

from typing import Any

import numpy as np

MAX_CELLS = 48_000_000  # hard grid cap — beyond this the check must FAIL, not coarsen
_PAD_MM = 3.0  # auto-region inflation around the ports' bounding box
_SEED_ZONE = 2  # cells around a port sample that count as that port's zone


def _mesh_of_all(objs: list[Any]) -> tuple[np.ndarray, np.ndarray]:
    """One WORLD-FRAME (verts, tris) soup for every solid. build123d's own
    ``tessellate`` applies each shape's Location; ``print_time.mesh_of`` does not
    (it meshes the local frame — fine for the planner's identity-located parts,
    silently wrong for placed assembly members)."""
    vs: list[np.ndarray] = []
    ts: list[np.ndarray] = []
    off = 0
    for obj in objs:
        v_raw, t_raw = obj.tessellate(0.2)
        v = np.asarray([[p.X, p.Y, p.Z] for p in v_raw], dtype=np.float64)
        t = np.asarray(t_raw, dtype=np.int64)
        vs.append(v)
        ts.append(t + off)
        off += len(v)
    return np.concatenate(vs), np.concatenate(ts)


def voxelize_shell(verts: np.ndarray, tris: np.ndarray, lo: np.ndarray, hi: np.ndarray, cell: float) -> np.ndarray:
    """Boolean occupancy of the solids' SURFACES inside the crop box, at ``cell``
    resolution. A watertight shell splits the grid's void into disconnected
    components, which is all the flood fill needs. Long triangle edges are
    chunked (build_occupancy's fixed 96-step sampling would tear holes in the
    shell at fine cells)."""
    dims = np.maximum(((hi - lo) / cell).astype(int) + 1, 1)
    grid = np.zeros(dims, dtype=bool)
    tv = verts[tris]  # (M, 3, 3)
    # Drop triangles entirely outside the crop (cheap bbox test per triangle).
    tmin, tmax = tv.min(axis=1), tv.max(axis=1)
    keep = ~((tmax < lo - cell).any(axis=1) | (tmin > hi + cell).any(axis=1))
    tv = tv[keep]
    if not len(tv):
        return grid
    # Step count must cover the LONGEST edge — measuring only the two edges from
    # vertex 0 under-samples slivers whose long edge is v1–v2 and tears holes in
    # the shell (which the flood fill then pours through).
    e1 = np.linalg.norm(tv[:, 1] - tv[:, 0], axis=1)
    e2 = np.linalg.norm(tv[:, 2] - tv[:, 0], axis=1)
    e3 = np.linalg.norm(tv[:, 2] - tv[:, 1], axis=1)
    steps = np.ceil(1.5 * np.maximum(np.maximum(e1, e2), e3) / cell).astype(int) + 1
    for n in np.unique(steps):
        sel = tv[steps == n]
        k = int(n)
        # Barycentric lattice: (k+1)(k+2)/2 sample points per triangle, vectorized
        # over every triangle needing k steps.
        ii, jj = np.meshgrid(np.arange(k + 1), np.arange(k + 1), indexing="ij")
        mask = ii + jj <= k
        a = (ii[mask] / max(k, 1))[None, :, None]
        b = (jj[mask] / max(k, 1))[None, :, None]
        pts = sel[:, 0][:, None, :] * (1 - a - b) + sel[:, 1][:, None, :] * a + sel[:, 2][:, None, :] * b
        pts = pts.reshape(-1, 3)
        idx = ((pts - lo) / cell).astype(int)
        ok = ((idx >= 0) & (idx < dims)).all(axis=1)
        idx = idx[ok]
        grid[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return grid


def _face_samples(face: Any, nudge: float, n: int = 5) -> list[tuple[float, float, float]]:
    """World sample points on a build123d Face: a u-v grid plus the center,
    nudged both ways along the normal so at least one lands in open void."""
    pts: list[tuple[float, float, float]] = []
    centers: list[Any] = []
    try:
        for u in np.linspace(0.15, 0.85, n):
            for v in np.linspace(0.15, 0.85, n):
                try:
                    centers.append((face.position_at(u, v), face.normal_at(face.position_at(u, v))))
                except Exception:  # noqa: BLE001 — off-surface uv on trimmed faces
                    continue
        centers.append((face.center(), face.normal_at(face.center())))
    except Exception:  # noqa: BLE001 — a degenerate face contributes no seeds
        return pts
    for p, nrm in centers:
        for s in (0.0, nudge, -nudge):
            pts.append((p.X + s * nrm.X, p.Y + s * nrm.Y, p.Z + s * nrm.Z))
    return pts


def _port_points(port: dict, cell: float) -> list[tuple[float, float, float]]:
    if "point" in port:
        x, y, z = port["point"]
        return [(float(x), float(y), float(z))]
    out: list[tuple[float, float, float]] = []
    for f in port.get("faces") or []:
        out.extend(_face_samples(f, nudge=2.0 * cell))
    return out


def flow_check(
    objs: list[Any],
    inlets: list[dict],
    outlets: list[dict],
    min_gap: float = 0.3,
    region: tuple | None = None,
    max_leak_cells: int = 0,
) -> dict[str, Any]:
    """Flood-fill the void from the inlets and classify every escape.

    Returns ``{passed, reason, leaked_cells, leak_points, reaches_outlet, cell,
    dims}``. Failure reasons are explicit — resolution overflow and blocked
    inlets fail loudly rather than degrade into a false pass."""
    from scipy import ndimage

    if not inlets or not outlets:
        return {"passed": False, "reason": "declare at least one inlet and one outlet flow_port"}

    cell = min_gap / 2.0
    in_pts = [p for port in inlets for p in _port_points(port, cell)]
    out_pts = [p for port in outlets for p in _port_points(port, cell)]
    if not in_pts or not out_pts:
        return {"passed": False, "reason": "a flow_port produced no sample points (degenerate face?)"}

    verts, tris = _mesh_of_all(objs)

    if region is not None:
        lo = np.asarray(region[0], dtype=float)
        hi = np.asarray(region[1], dtype=float)
    else:
        # Default region: full assembly extent laterally, CLAMPED at the port
        # planes along any axis the ports actually span (the flow axis) — flow is
        # only checked BETWEEN the ports; the open mouth above an inlet is out of
        # domain, and its crop face is owned by that port below.
        allp = np.asarray(in_pts + out_pts, dtype=float)
        p_lo, p_hi = allp.min(axis=0), allp.max(axis=0)
        s_lo, s_hi = verts.min(axis=0), verts.max(axis=0)
        lo, hi = s_lo - _PAD_MM, s_hi + _PAD_MM
        span_s = np.maximum(s_hi - s_lo, 1e-6)
        for a in range(3):
            if (p_hi[a] - p_lo[a]) >= 0.25 * span_s[a]:
                lo[a], hi[a] = p_lo[a] - cell, p_hi[a] + cell
    lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)

    dims = np.maximum(((hi - lo) / cell).astype(int) + 1, 1)
    n_cells = int(np.prod(dims))
    if n_cells > MAX_CELLS:
        return {
            "passed": False,
            "reason": f"region needs {n_cells / 1e6:.0f}M cells at min_gap={min_gap:g} "
            f"(cap {MAX_CELLS / 1e6:.0f}M) — pass a tighter region=((x0,y0,z0),(x1,y1,z1)) or raise min_gap",
        }

    solid = voxelize_shell(verts, tris, lo, hi, cell)
    # Dilate the shell one cell: a channel narrower than min_gap (= 2 cells) is
    # SEALED — the declared semantic ("narrower counts as capillary, not a flow
    # path"). The flip side is quantization: a channel is only GUARANTEED open
    # from ~2×min_gap up, so probe a suspect gap with min_gap ≤ half its width.
    solid = ndimage.binary_dilation(solid)

    labels, _n = ndimage.label(~solid)  # 6-connected by default

    def cell_of(p: tuple[float, float, float]) -> tuple[int, int, int] | None:
        idx = ((np.asarray(p) - lo) / cell).astype(int)
        if (idx < 0).any() or (idx >= dims).any():
            return None
        return (int(idx[0]), int(idx[1]), int(idx[2]))

    seed_labels: set[int] = set()
    for p in in_pts:
        c = cell_of(p)
        if c is not None and not solid[c]:
            seed_labels.add(int(labels[c]))
    seed_labels.discard(0)
    if not seed_labels:
        return {"passed": False, "reason": "inlet is blocked — no empty cell at any inlet sample"}

    seeded = np.isin(labels, list(seed_labels))

    # Port zones: void cells within a couple of cells of a port sample.
    def zone_mask(pts: list[tuple[float, float, float]]) -> np.ndarray:
        m = np.zeros(dims, dtype=bool)
        r = _SEED_ZONE
        for p in pts:
            c = cell_of(p)
            if c is None:
                continue
            x0, y0, z0 = (max(0, c[0] - r), max(0, c[1] - r), max(0, c[2] - r))
            x1, y1, z1 = (
                min(int(dims[0]), c[0] + r + 1),
                min(int(dims[1]), c[1] + r + 1),
                min(int(dims[2]), c[2] + r + 1),
            )
            m[x0:x1, y0:y1, z0:z1] = True
        return m

    out_zone = zone_mask(out_pts)
    in_zone = zone_mask(in_pts)

    # A crop face that a port sits ON (the clamped planes) belongs wholly to that
    # port: flow crossing it is arriving from / leaving to that port's side, not
    # leaking. Leaks are escapes through unowned boundary — the lateral walls.
    def own_faces(pts: list[tuple[float, float, float]], m: np.ndarray) -> None:
        tol = 3 * cell
        for p in pts:
            v = np.asarray(p, dtype=float)
            for a in range(3):
                if abs(v[a] - lo[a]) <= tol:
                    sl: list[Any] = [slice(None)] * 3
                    sl[a] = 0
                    m[tuple(sl)] = True
                if abs(v[a] - hi[a]) <= tol:
                    sl = [slice(None)] * 3
                    sl[a] = int(dims[a]) - 1
                    m[tuple(sl)] = True

    own_faces(out_pts, out_zone)
    own_faces(in_pts, in_zone)
    reaches_outlet = bool((seeded & out_zone & ~solid).any())

    # Boundary cells of the crop that carry a seeded label and are not part of a
    # declared port zone = leaks (flow escaping somewhere it should not).
    boundary = np.zeros(dims, dtype=bool)
    boundary[0, :, :] = boundary[-1, :, :] = True
    boundary[:, 0, :] = boundary[:, -1, :] = True
    boundary[:, :, 0] = boundary[:, :, -1] = True
    leak_mask = seeded & boundary & ~solid & ~out_zone & ~in_zone
    leaked = int(leak_mask.sum())

    leak_points: list[list[float]] = []
    if leaked:
        # Cluster the leak exits and report each cluster's MEDOID — an actual
        # leak cell (a ring-shaped cluster's centroid would lie in empty space).
        lk_labels, n_lk = ndimage.label(leak_mask)
        for i in range(1, min(n_lk, 8) + 1):
            idx = np.argwhere(lk_labels == i)
            centroid = idx.mean(axis=0)
            m = idx[np.argmin(((idx - centroid) ** 2).sum(axis=1))]
            c = m * cell + lo
            leak_points.append([round(float(x), 2) for x in c])

    passed = reaches_outlet and leaked <= max_leak_cells
    if not reaches_outlet:
        reason = "no void path from inlet to outlet — flow is blocked"
    elif leaked > max_leak_cells:
        reason = f"{leaked} leak cells escape the region outside the outlet (exits near {leak_points})"
    else:
        reason = "flow reaches the outlet and nowhere else"
    return {
        "passed": passed,
        "reason": reason,
        "leaked_cells": leaked,
        "leak_points": leak_points,
        "reaches_outlet": reaches_outlet,
        "cell": round(cell, 4),
        "dims": [int(d) for d in dims],
    }
