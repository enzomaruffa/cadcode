"""Physical-properties analysis (simulation layer, plan §Phase 1).

Exact mass / center-of-mass / inertia straight from the OCP solid, plus the
print-oriented derived quantities a maker actually asks about: filament mass,
cost, a print-time estimate, whether it floats, and whether it will tip over on
a slope. All computed from the live geometry + the material the *script*
declared (``show(part, material=PLA)``) — nothing stored, everything re-derives.

Only ``print_time_min`` is a heuristic estimate; the rest are exact geometry.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# Print-time estimate: a single-extruder volumetric-flow model. Real slicers
# account for travel, acceleration and infill, so this is deliberately labelled
# an ESTIMATE in the UI.
_FLOW_MM3_S = 8.0  # typical sustained volumetric flow (mm³/s)
_FLOW_EFFICIENCY = 0.55  # derate for travel / accel / non-print moves
_WATER_DENSITY = 1.0  # g/cm³, for the buoyancy check
_Z_TOL = 1e-3  # a vertex within this of bbox.min.Z is "on the build plate"


def _convex_hull_2d(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain — CCW hull, no scipy. Returns the hull vertices
    (>=3) or the deduped input when it's degenerate (collinear / <3 points)."""
    uniq = sorted(set((round(x, 6), round(y, 6)) for x, y in pts))
    if len(uniq) < 3:
        return uniq

    def cross(o: tuple, a: tuple, b: tuple) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in uniq:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(uniq):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _footprint(objs: list[Any], base_z: float) -> list[tuple[float, float]]:
    """XY points of the support polygon — the geometry resting on the build
    plate. Samples the outline of each downward face at ``base_z`` (so a round
    base gives a circle, not the 1-2 B-rep vertices a cylinder actually has),
    falling back to raw vertices near ``base_z`` for odd topologies."""
    band = max(_Z_TOL, 0.05)
    pts: list[tuple[float, float]] = []
    for obj in objs:
        try:
            faces = obj.faces()
        except Exception:
            continue
        for f in faces:
            try:
                if f.normal_at().Z >= -0.5 or abs(f.center().Z - base_z) >= band:
                    continue
                wire = f.outer_wire()
                for i in range(48):
                    p = wire @ (i / 48.0)
                    pts.append((p.X, p.Y))
            except Exception:
                continue
    if len(pts) >= 3:
        return pts
    for obj in objs:  # fallback: raw vertices resting on the plate
        try:
            pts.extend((v.X, v.Y) for v in obj.vertices() if abs(v.Z - base_z) < band)
        except Exception:
            continue
    return pts


def _tip_analysis(
    hull: list[tuple[float, float]], com_xy: tuple[float, float], com_height: float
) -> tuple[float | None, bool]:
    """(tip_angle_deg, stable). The tip angle is the smallest tilt that walks the
    COM's ground projection past a support-polygon edge: ``atan(d_min / h)`` where
    ``d_min`` is the closest hull edge to the projection. COM outside the hull or
    a degenerate footprint (<3 pts) ⇒ already unstable."""
    if len(hull) < 3 or com_height <= _Z_TOL:
        return None, False

    cx, cy = com_xy
    inside = True
    d_min = math.inf
    n = len(hull)
    for i in range(n):
        ax, ay = hull[i]
        bx, by = hull[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        seg_len = math.hypot(ex, ey)
        if seg_len < 1e-9:
            continue
        # Signed cross product: CCW hull ⇒ negative means the point is outside.
        cross = ex * (cy - ay) - ey * (cx - ax)
        if cross < 0:
            inside = False
        d_min = min(d_min, abs(cross) / seg_len)

    if not inside or not math.isfinite(d_min):
        return 0.0, False
    return math.degrees(math.atan2(d_min, com_height)), True


def mass_properties(objs: list[Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the physical-properties readout for the shown solids.

    ``objs`` are live build123d shapes; ``materials`` is the parallel list of
    resolved material dicts (from ``runner.LAST_MATERIAL``)."""
    from build123d import CenterOf

    if not objs:
        return {"error": "no geometry yet (run first)"}

    total_vol_mm3 = 0.0  # mm³
    total_area_mm2 = 0.0
    total_mass_g = 0.0
    total_cost = 0.0
    total_filament_g = 0.0

    # Per-part accumulation for a mass-weighted COM + a proper (parallel-axis)
    # inertia tensor that stays correct across mixed materials.
    part_mass: list[float] = []  # grams
    part_com: list[np.ndarray] = []  # mm
    part_moi_vol: list[np.ndarray] = []  # mm⁵, volume-weighted, about the part COM
    part_rho: list[float] = []  # g/mm³

    base_z = math.inf
    names = set()

    for obj, mat in zip(objs, materials, strict=False):
        try:
            vol = float(obj.volume)
        except Exception:
            continue
        if vol <= 0:
            continue
        density = float(mat.get("density", 1.24))  # g/cm³
        cost_per_kg = float(mat.get("cost_per_kg", 0.0))
        names.add(mat.get("name", "?"))

        mass_g = (vol / 1000.0) * density
        total_vol_mm3 += vol
        total_area_mm2 += float(getattr(obj, "area", 0.0) or 0.0)
        total_mass_g += mass_g
        total_cost += (mass_g / 1000.0) * cost_per_kg
        total_filament_g += mass_g

        com = obj.center(CenterOf.MASS)
        part_mass.append(mass_g)
        part_com.append(np.array([com.X, com.Y, com.Z], dtype=float))
        part_rho.append(density / 1000.0)  # g/mm³
        try:
            part_moi_vol.append(np.array(obj.matrix_of_inertia, dtype=float))
        except Exception:
            part_moi_vol.append(np.zeros((3, 3)))

        base_z = min(base_z, obj.bounding_box().min.Z)

    if total_vol_mm3 <= 0 or not part_mass:
        return {"error": "geometry has no solid volume"}

    # Footprint: the support polygon where the geometry meets the build plate.
    lows = _footprint(objs, base_z)

    masses = np.array(part_mass)
    global_com = (np.stack(part_com) * masses[:, None]).sum(axis=0) / masses.sum()

    # Mass moment of inertia about the global COM (g·mm²): ρ·I_vol per part,
    # each shifted from its own COM to the global COM by the parallel-axis term.
    inertia = np.zeros((3, 3))
    for m_g, com_i, moi_vol, rho in zip(part_mass, part_com, part_moi_vol, part_rho, strict=False):
        d = com_i - global_com
        shift = m_g * (float(d @ d) * np.eye(3) - np.outer(d, d))
        inertia += rho * moi_vol + shift
    # Symmetrize (guards float noise), then diagonalize for principal moments.
    inertia = 0.5 * (inertia + inertia.T)
    principal, axes = np.linalg.eigh(inertia)

    volume_cm3 = total_vol_mm3 / 1000.0
    effective_density = total_mass_g / volume_cm3 if volume_cm3 else 0.0
    com_xy = (float(global_com[0]), float(global_com[1]))
    com_height = float(global_com[2]) - base_z
    hull = _convex_hull_2d(lows)
    tip_angle, stable = _tip_analysis(hull, com_xy, com_height)

    # Filament length for FDM stock (skip for resin, filament_d == 0).
    fil_d = float(materials[0].get("filament_d", 1.75)) if materials else 1.75
    filament_len_mm = None
    if fil_d > 0:
        filament_len_mm = round(total_vol_mm3 / (math.pi * (fil_d / 2.0) ** 2), 1)

    # Slicer-style layer estimate from the real mesh (walls + infill + skins +
    # travel), not the old solid-volume ÷ flow heuristic.
    try:
        from app.kernel.print_time import estimate_objects_minutes

        print_time_min = estimate_objects_minutes(list(objs))
    except Exception:  # noqa: BLE001 - keep the readout alive on any mesh hiccup
        print_time_min = round((total_vol_mm3 / (_FLOW_MM3_S * _FLOW_EFFICIENCY)) / 60.0, 1)

    return {
        "mass_g": round(total_mass_g, 3),
        "volume_cm3": round(volume_cm3, 3),
        "area_mm2": round(total_area_mm2, 2),
        "com": [round(float(x), 4) for x in global_com],
        "inertia": [[round(float(inertia[i, j]), 3) for j in range(3)] for i in range(3)],
        "principal": [round(float(p), 3) for p in principal],
        "principal_axes": [[round(float(axes[i, j]), 4) for i in range(3)] for j in range(3)],
        "base_z": round(base_z, 4),
        "com_height": round(com_height, 4),
        "footprint": [[round(x, 4), round(y, 4)] for (x, y) in hull],
        "tip_angle": round(tip_angle, 2) if tip_angle is not None else None,
        "stable": stable,
        "floats": effective_density < _WATER_DENSITY,
        "effective_density": round(effective_density, 4),
        "filament_g": round(total_filament_g, 2),
        "filament_len_mm": filament_len_mm,
        "cost": round(total_cost, 3),
        "print_time_min": print_time_min,
        "material_name": next(iter(names)) if len(names) == 1 else "mixed",
        "parts": len(part_mass),
    }
