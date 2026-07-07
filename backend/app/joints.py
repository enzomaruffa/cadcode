"""Lift a build123d assembly's joint graph into a physics-ready payload.

Parts declare joints (`RigidJoint`, `RevoluteJoint`, `LinearJoint`, …) and an
assembly connects them (`a.joints[x].connect_to(b.joints[y])`). build123d records
that on the *initiating* joint's `connected_to`. We walk every shown part's
joints, and for each connection emit one constraint — the two bodies it links
(by their `show(name=…)`), the anchor on each (in that part's LOCAL frame), the
DOF axis, and the motion range. The viewer maps these to Rapier joints so a
grabbed lever swings on its real axis and drives whatever it's linked to.
"""

from __future__ import annotations

from typing import Any

# build123d joint class name → physics DOF kind. The connection's DOF is the
# most permissive of the two joints (a rigid anchor + a revolute = a hinge).
_KIND_RANK = {"BallJoint": 4, "CylindricalJoint": 3, "LinearJoint": 2, "RevoluteJoint": 1, "RigidJoint": 0}
_KIND_NAME = {
    "BallJoint": "spherical",
    "CylindricalJoint": "cylindrical",
    "LinearJoint": "prismatic",
    "RevoluteJoint": "revolute",
    "RigidJoint": "fixed",
}


def _xyz(v: Any) -> list[float]:
    return [float(v.X), float(v.Y), float(v.Z)]


def _local_frame(joint: Any) -> tuple[list[float], list[float]]:
    """(anchor, axis) of a joint in its parent's LOCAL frame. Rigid joints have
    no axis (default +Z); revolute/linear carry a `relative_axis`."""
    ra = getattr(joint, "relative_axis", None)
    if ra is not None:
        return _xyz(ra.position), _xyz(ra.direction)
    rl = getattr(joint, "relative_location", None)
    if rl is not None:
        return _xyz(rl.position), [0.0, 0.0, 1.0]
    loc = getattr(joint, "location", None)
    return (_xyz(loc.position) if loc is not None else [0.0, 0.0, 0.0]), [0.0, 0.0, 1.0]


def _range(joint: Any, kind: str) -> list[float] | None:
    if kind == "revolute":
        r = getattr(joint, "angular_range", None)
        return [float(r[0]), float(r[1])] if r else None
    if kind == "prismatic":
        r = getattr(joint, "linear_range", None)
        return [float(r[0]), float(r[1])] if r else None
    return None


def extract_joints(shown: list[tuple[Any, str | None, Any, Any]]) -> list[dict[str, Any]]:
    """From the shown `(obj, name, …)` list → a list of constraint dicts:
    {kind, a, b, anchorA, anchorB, axisA, axisB, range}. `a`/`b` are show names."""
    named = [(o, n) for (o, n, *_rest) in shown if n]
    by_id = {id(o): n for (o, n) in named}
    out: list[dict[str, Any]] = []
    seen: set[frozenset[int]] = set()
    for obj, name in named:
        joints = getattr(obj, "joints", None) or {}
        for j in joints.values():
            ct = getattr(j, "connected_to", None)
            if ct is None:
                continue
            other = getattr(ct, "parent", None)
            other_name = by_id.get(id(other))
            if other_name is None or other_name == name:
                continue
            pair = frozenset({id(j), id(ct)})
            if pair in seen:
                continue
            seen.add(pair)

            jt, ctt = type(j).__name__, type(ct).__name__
            # DOF comes from the more permissive joint; its axis is authoritative
            dof_joint, dof_name = (j, jt) if _KIND_RANK.get(jt, 0) >= _KIND_RANK.get(ctt, 0) else (ct, ctt)
            kind = _KIND_NAME.get(dof_name, "fixed")
            anchor_a, axis_a = _local_frame(j)
            anchor_b, axis_b = _local_frame(ct)
            out.append(
                {
                    "kind": kind,
                    "a": name,
                    "b": other_name,
                    "anchorA": anchor_a,
                    "anchorB": anchor_b,
                    "axisA": axis_a,
                    "axisB": axis_b,
                    "range": _range(dof_joint, kind),
                }
            )
    return out
