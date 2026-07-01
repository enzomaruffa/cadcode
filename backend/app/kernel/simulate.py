"""Motion / kinematics simulation (simulation layer, plan §Phase 2).

Drives an assembly's joints through a sweep and reports, per frame, a rigid
transform for each moving body plus collision + clearance. Because kinematic
motion is *rigid*, we tessellate once (the normal render already did) and animate
by shipping absolute per-leaf poses — tiny payloads, buttery playback.

The script opts in by defining ``motion(t: float) -> dict[str, Location]`` where
``t`` runs 0→1 and the keys are the ``show(..., name=...)`` names. Calling it
mutates the live joint-driven objects in place (build123d ``connect_to`` calls
``.locate``), so the shown objects are re-posed each frame — collision and
clearance then read straight off them.

``min_clearance_through_motion`` is fed back into the script (two-pass) so a
``require(min_clearance_through_motion >= CLEARANCE)`` spec becomes an executable
motion assertion the agent can iterate against (CAD-as-TDD, plan §7).
"""

from __future__ import annotations

from typing import Any

from app.kernel.runner import run_scene

MAX_FRAMES = 60


def _loc_to_pose(loc: Any) -> list[list[float]]:
    """A build123d ``Location`` → the viewer ``Loc`` ``[[x,y,z],[qx,qy,qz,qw]]``
    (quaternion w-last, three.js order — matches ``gp_Quaternion`` directly)."""
    p = loc.position
    q = loc.wrapped.Transformation().GetRotation()
    return [[p.X, p.Y, p.Z], [q.X(), q.Y(), q.Z(), q.W()]]


def _pairwise(objs: list[Any], leaf_ids: list[str], eps: float) -> tuple[list[str], float]:
    """Collision + min clearance among the posed objects. Returns
    ``(colliding_leaf_ids, min_clearance)``; clearance is 0.0 on any overlap."""
    colliding: set[str] = set()
    min_clear = float("inf")
    n = len(objs)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = objs[i], objs[j]
            vol = 0.0
            try:
                inter = a & b  # BRepAlgoAPI_Common; respects each shape's location
                if inter is not None:
                    vol = float(getattr(inter, "volume", 0.0) or 0.0)
            except Exception:
                vol = 0.0
            if vol > eps:
                colliding.add(leaf_ids[i])
                colliding.add(leaf_ids[j])
                min_clear = 0.0
            else:
                try:
                    d = float(a.distance(b))  # BRepExtrema_DistShapeShape
                    min_clear = min(min_clear, d)
                except Exception:
                    pass
    return sorted(colliding), (min_clear if min_clear != float("inf") else 0.0)


def simulate_motion(source: str, *, frames: int = 24, eps: float = 1e-6, sandbox: bool = True) -> dict[str, Any]:
    """Sweep the script's ``motion(t)`` and return per-frame poses + collision +
    clearance, plus the executable-spec results for the worst-case clearance."""
    frames = max(2, min(int(frames), MAX_FRAMES))

    # Pass 1 — discover motion() and sweep. Seed the clearance sentinel as +inf so
    # any `require(min_clearance_through_motion >= X)` passes during discovery.
    objs, names, leaf_ids, ns, _specs, err = run_scene(
        source, sandbox=sandbox, inject={"min_clearance_through_motion": float("inf")}
    )
    if err:
        return {"error": err}
    if not objs:
        return {"error": "no geometry to simulate (run first)"}

    motion = ns.get("motion")
    if not callable(motion):
        return {"error": "no motion(t) defined — add `def motion(t): ...` returning {name: Location} to animate joints"}

    name_to_leaf = {names[i]: leaf_ids[i] for i in range(min(len(names), len(leaf_ids))) if names[i]}

    ts = [i / (frames - 1) for i in range(frames)]
    frames_out: list[dict[str, Any]] = []
    collision_frames: list[int] = []
    worst_clearance = float("inf")

    for fi, t in enumerate(ts):
        try:
            posed = motion(float(t))  # mutates the live objects to this pose
        except Exception as exc:  # noqa: BLE001
            return {"error": f"motion(t={t:.3f}) failed: {type(exc).__name__}: {exc}"}

        transforms: dict[str, list[list[float]]] = {}
        if isinstance(posed, dict):
            for nm, loc in posed.items():
                leaf = name_to_leaf.get(nm)
                if leaf is not None and loc is not None:
                    try:
                        transforms[leaf] = _loc_to_pose(loc)
                    except Exception:
                        pass

        colliding, min_clear = _pairwise(objs, leaf_ids, eps)
        if colliding:
            collision_frames.append(fi)
        worst_clearance = min(worst_clearance, min_clear)
        frames_out.append(
            {"t": round(t, 4), "transforms": transforms, "colliding": colliding, "min_clearance": round(min_clear, 4)}
        )

    mctm = 0.0 if collision_frames else (worst_clearance if worst_clearance != float("inf") else None)

    # Pass 2 — re-exec with the real clearance so the require() spec evaluates.
    _o2, _n2, _l2, _ns2, specs2, err2 = run_scene(
        source, sandbox=sandbox, inject={"min_clearance_through_motion": mctm}
    )
    specs = [] if err2 else specs2

    return {
        "ok": True,
        "frames": frames_out,
        "summary": {
            "n_frames": len(frames_out),
            "worst_clearance": round(worst_clearance, 4) if worst_clearance != float("inf") else None,
            "min_clearance_through_motion": round(mctm, 4) if isinstance(mctm, float) else mctm,
            "collision_frames": collision_frames,
        },
        "specs": specs,
    }
