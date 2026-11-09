"""Motion sweeps — the kinematic half of CAD-as-TDD.

A sweep drives one part along a parametric path and boolean-intersects it
against the rest of the assembly at every pose, so "the cup twists to lock
without jamming" becomes an executable spec (``require_motion`` in the ambient
DSL) instead of a hand-rolled probe script. Paths are tiny factories returning
``t → Location``; any bare callable with that shape works too. All paths move
in WORLD frame about the origin — compose with ``Pos``/``Rot`` for anything else.

Everything here is pure geometry — no exec machinery, no state. The moving part
is never mutated: each pose is ``path(t) * moving``, a located copy.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

MotionPath = Callable[[float], Any]

_AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def _axis_vec(axis: Any) -> tuple[float, float, float]:
    if isinstance(axis, str):
        v = _AXES.get(axis.upper())
        if v is None:
            raise ValueError(f"axis must be X, Y, Z or a vector, not {axis!r}")
        return v
    x, y, z = (float(axis[0]), float(axis[1]), float(axis[2]))
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return (x / n, y / n, z / n)


def _axis_rotation(direction: tuple[float, float, float], deg: float) -> Any:
    """A Location rotating ``deg`` about a world axis through the origin — the
    same axis-angle constructor bake.py emits: Location(position, direction, deg)."""
    from build123d import Location

    return Location((0.0, 0.0, 0.0), direction, deg)


def loc_to_pose(loc: Any) -> list[list[float]]:
    """A build123d ``Location`` → the viewer ``Loc`` ``[[x,y,z],[qx,qy,qz,qw]]``
    (quaternion w-last, three.js order — matches ``gp_Quaternion`` directly)."""
    p = loc.position
    q = loc.wrapped.Transformation().GetRotation()
    return [[p.X, p.Y, p.Z], [q.X(), q.Y(), q.Z(), q.W()]]


def turn(axis: Any = "Z", start_deg: float = 0.0, end_deg: float = 90.0) -> MotionPath:
    """Rotation about a world axis through the origin, ``start_deg → end_deg``."""
    d = _axis_vec(axis)

    def path(t: float) -> Any:
        return _axis_rotation(d, start_deg + (end_deg - start_deg) * t)

    return path


def slide(vector: Any = (0, 0, 1), mm: float = 10.0) -> MotionPath:
    """Straight translation along ``vector`` by ``mm`` at t=1."""
    from build123d import Pos

    vx, vy, vz = _axis_vec(vector)

    def path(t: float) -> Any:
        d = mm * t
        return Pos(vx * d, vy * d, vz * d)

    return path


def screw(axis: Any = "Z", deg: float = 360.0, pitch: float = 2.0) -> MotionPath:
    """Helical motion: rotate ``deg`` about the axis while advancing along it by
    ``pitch`` mm per full turn (a thread; a bayonet is `turn`, not `screw`)."""
    from build123d import Pos

    d = _axis_vec(axis)

    def path(t: float) -> Any:
        a = deg * t
        adv = pitch * a / 360.0
        return Pos(d[0] * adv, d[1] * adv, d[2] * adv) * _axis_rotation(d, a)

    return path


def _contact_volume(a: Any, b: Any) -> float:
    inter = a & b
    if inter is None:
        return 0.0
    if hasattr(inter, "volume"):
        return float(inter.volume or 0.0)
    return float(sum(s.volume for s in inter))


def _bboxes_clear(a: Any, b: Any) -> bool:
    """Fast reject: disjoint axis-aligned bounding boxes cannot intersect."""
    ba, bb = a.bounding_box(), b.bounding_box()
    return (
        ba.max.X < bb.min.X
        or bb.max.X < ba.min.X
        or ba.max.Y < bb.min.Y
        or bb.max.Y < ba.min.Y
        or ba.max.Z < bb.min.Z
        or bb.max.Z < ba.min.Z
    )


def sweep_contact(
    moving: Any,
    against: list[Any],
    path: MotionPath,
    poses: int = 12,
    budget_s: float | None = None,
) -> dict[str, Any]:
    """Sweep ``moving`` along ``path`` and measure boolean contact against each
    fixed body at every pose. Returns per-pose contact volumes, the worst pose,
    and ``truncated=True`` if the soft time budget ran out (callers must FAIL
    the spec then — a half-checked sweep is not a passed sweep)."""
    poses = max(2, min(int(poses), 64))
    t0 = time.monotonic()
    out: list[dict[str, Any]] = []
    worst = 0.0
    worst_t = 0.0
    truncated = False
    for i in range(poses):
        t = i / (poses - 1)
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            truncated = True
            break
        posed = path(float(t)) * moving
        contact = 0.0
        for other in against:
            if _bboxes_clear(posed, other):
                continue
            contact += _contact_volume(other, posed)
        if contact > worst:
            worst, worst_t = contact, t
        out.append({"t": round(t, 4), "contact_mm3": round(contact, 4), "pose": loc_to_pose(path(float(t)))})
    return {"poses": out, "worst_contact": round(worst, 4), "worst_t": round(worst_t, 4), "truncated": truncated}
