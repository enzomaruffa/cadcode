"""Print plating: pick parts + quantities → arranged, print-ready plates.

Each part is tessellated once and its six principal orientations are scored on
the MESH (`kernel/print_time.py`): support area, slicer-style estimated minutes,
and footprint — the chosen strategy decides which metric leads. The best
orientation is dropped onto the bed and all instances are packed onto as few
bed-sized plates as needed (first-fit-decreasing shelf packing with free 90°
in-plane rotation). Plates render in the viewport, carry per-part/per-plate time
estimates, and export as one STL/3MF each; headless PrusaSlicer (when installed)
provides exact times on demand.
"""

from __future__ import annotations

import sys
from typing import Any

# Default FDM bed (mm) — a common 220x220 printer; the UI can override.
DEFAULT_BED = (220.0, 220.0)
PADDING = 6.0  # space between parts on the plate


def _has_slicer() -> bool:
    from app.kernel.slicer import slicer_available

    return slicer_available()


def _slicer_name() -> str:
    from app.kernel.slicer import slicer_name

    return slicer_name()


# Two-phase orientation search: phase 1 scores ~48 candidate "down" directions
# (6 principal + a Fibonacci-sphere sweep, so 45° tilts are really tried) on
# CHEAP metrics — removability-weighted support cost (support trapped in a bore
# costs up to 10× open support), height, footprint. Phase 2 layer-slices only
# the best few for a real time estimate. See print_time.support_cost.
_PHASE2_KEEP = 6


# What each strategy optimizes when choosing a part's orientation. Metrics per
# candidate come from the MESH (tessellated once, rotated per candidate — the
# same triangles a slicer sees): support area, estimated minutes, footprint.
#   material — least support waste (then fastest)
#   plates   — smallest footprint so more parts share a bed (then least support)
#   fastest  — least ESTIMATED PRINT TIME (slicer-style layer estimate)
STRATEGIES = ("material", "plates", "fastest")


def _orient(
    obj: Any, strategy: str = "material", supports: bool = True
) -> tuple[Any, str, float, float, tuple[Any, Any], Any]:
    """Pick the best orientation for the strategy. Phase 1: ~48 candidate down
    directions scored on removability-weighted support cost (+ height/footprint)
    — support trapped inside a bore/pocket is penalized up to 10×, so "print it
    upside-down with external supports" wins on the shapes where a human would
    choose that. Phase 2: layer-slice the best few for real minutes. Returns
    (rotated + bed-dropped solid, label, support area, est minutes, oriented mesh
    (verts, tris), profile)."""
    import numpy as np
    from build123d import Axis, Pos

    from app.kernel.print_time import (
        build_occupancy,
        candidate_down_dirs,
        estimate_minutes,
        mesh_of,
        rotation_to_down,
        support_area,
        support_cost,
    )

    # Supportless prints don't extrude support material — zero its density so the
    # time estimate drops the support term (overhang area is still measured for
    # ranking + display: less overhang prints cleaner either way).
    prof = None if supports else {"support_density": 0.0}
    verts, tris = mesh_of(obj)
    occ = build_occupancy(verts, tris)

    # Phase 1 — cheap metrics for every candidate.
    phase1: list[tuple[tuple[float, float, float], str, Any, Any, float, Any]] = []
    for label, d in candidate_down_dirs():
        r, axis, angle = rotation_to_down(d)
        vr = verts @ r.T
        cost = support_cost(vr, tris, occ, r)  # rotated-only (support_cost maps rays back to the grid)
        v = vr - [0.0, 0.0, float(vr[:, 2].min())]  # dropped onto the bed for the size metrics
        height = float(v[:, 2].max())
        footprint = float((v[:, 0].max() - v[:, 0].min()) * (v[:, 1].max() - v[:, 1].min()))
        phase1.append(((round(cost, 1), round(height, 1), round(footprint, 1)), label, axis, angle, cost, v))
    phase1.sort(key=lambda p: p[0])

    # Phase 2 — real layer-sliced estimate for the survivors; strategy picks.
    best: tuple[tuple[float, ...], str, Any, Any, float, float, Any] | None = None
    for (_cost_key, _h, fp), label, axis, angle, cost, v in phase1[:_PHASE2_KEEP]:
        minutes = estimate_minutes(v, tris, profile=prof)["minutes"]
        sup = support_area(v, tris)
        if strategy == "plates":
            key = (fp, cost, minutes)
        elif strategy == "fastest":
            key = (minutes, cost, fp)
        else:  # material — removability-weighted support first
            key = (cost, minutes, fp)
        if best is None or key < best[0]:
            best = (key, label, axis, angle, sup, minutes, v)
    assert best is not None
    _key, label, axis, angle, sup, minutes, best_v = best

    oriented = obj.rotate(Axis((0, 0, 0), tuple(np.asarray(axis, dtype=float))), angle) if angle else obj
    bb = oriented.bounding_box()
    # drop onto the bed and center the footprint at its own origin
    oriented = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * oriented
    return oriented, label, sup, minutes, (best_v, tris), prof


def _build_part(project: str | None, name: str) -> Any:
    """Build one part instance: a project part (`projects.<p>.parts.<name>`) or a
    global library part. Caller holds the run lock and has the materialized
    workspace on sys.path for project parts.

    Library parts live as functions in the `lib.parts` package (`lib/parts/
    __init__.py`), not as per-part submodules, so import the package and pull the
    function off it; project parts are per-name submodules."""
    if project:
        mod = __import__(f"projects.{project}.parts.{name}", fromlist=[name])
    else:
        mod = __import__("lib.parts", fromlist=[name])
    fn = getattr(mod, name)
    return fn()


class _Plate:
    """One bed-sized plate being shelf-packed."""

    def __init__(self) -> None:
        self.shelves: list[list[float]] = []  # [y0, height, x_used]
        self.y_next = 0.0
        self.used_w = 0.0
        self.used_d = 0.0

    def try_place(self, w: float, d: float, bed: tuple[float, float]) -> tuple[float, float] | None:
        """Place a w×d rect; returns its center (x, y) or None if it can't fit."""
        for shelf in self.shelves:
            y0, h, x = shelf
            if d <= h and x + w <= bed[0]:
                shelf[2] = x + w
                self.used_w = max(self.used_w, x + w)
                return (x + w / 2, y0 + d / 2)
        if w <= bed[0] and self.y_next + d <= bed[1]:
            y0 = self.y_next
            self.shelves.append([y0, d, w])
            self.y_next += d
            self.used_w = max(self.used_w, w)
            self.used_d = self.y_next
            return (w / 2, y0 + d / 2)
        return None


def _pack_plates(
    rects: list[tuple[int, float, float]], bed: tuple[float, float]
) -> tuple[dict[int, tuple[int, float, float, bool]], list[_Plate], bool]:
    """Pack rectangles (idx, w, d) onto as FEW bed-sized plates as needed
    (first-fit-decreasing shelf packing; each rect may rotate 90° in-plane —
    support-neutral). Returns placements {idx: (plate, cx, cy, rotated)}, the
    plates, and whether everything genuinely fits the bed (a part too big for an
    empty bed gets its own oversize virtual plate and flips this to False)."""
    order = sorted(rects, key=lambda r: -max(r[1], r[2]))
    placements: dict[int, tuple[int, float, float, bool]] = {}
    plates: list[_Plate] = []
    fits = True
    for idx, w, d in order:
        # landscape-first: shelf packers waste less with the long side along X
        orients = [(w, d, False), (d, w, True)] if w >= d else [(d, w, True), (w, d, False)]
        placed = False
        for pi, plate in enumerate(plates):
            for pw, pd, rot in orients:
                pos = plate.try_place(pw, pd, bed)
                if pos:
                    placements[idx] = (pi, pos[0], pos[1], rot)
                    placed = True
                    break
            if placed:
                break
        if placed:
            continue
        # open a fresh plate
        plate = _Plate()
        plates.append(plate)
        pi = len(plates) - 1
        for pw, pd, rot in orients:
            pos = plate.try_place(pw, pd, bed)
            if pos:
                placements[idx] = (pi, pos[0], pos[1], rot)
                placed = True
                break
        if not placed:
            # oversize — doesn't fit an empty bed in either orientation; give it
            # a virtual plate of its own so it still renders/export
            fits = False
            pos = plate.try_place(w, d, (max(w, bed[0]), max(d, bed[1])))
            placements[idx] = (pi, pos[0] if pos else w / 2, pos[1] if pos else d / 2, False)
    return placements, plates, fits


def print_candidates(project: str | None) -> list[dict]:
    """The printable parts for the picker. With an active project: that
    project's own parts + every part its files import (cross-project
    `from projects.<p>.parts.<n> import …` and global `from lib.parts import …`).
    Without one: everything (all projects' parts + the global catalog)."""
    import re

    from app.projects import list_projects, project_files, project_tree

    out: list[dict] = []
    seen: set[tuple[str | None, str]] = set()

    def add(proj: str | None, name: str, note: str = "") -> None:
        key = (proj, name)
        if key in seen or not name:
            return
        seen.add(key)
        label = f"{proj} / {name}" if proj else f"lib / {name}"
        out.append({"project": proj, "name": name, "label": label + note})

    if project:
        tree = project_tree(project)
        for name in tree["parts"]:
            add(tree["name"], name)
        text = "\n".join(project_files(project).values())
        for m in re.finditer(r"from\s+projects\.(\w+)\.parts\.(\w+)\s+import", text):
            add(m.group(1), m.group(2), " (imported)")
        for m in re.finditer(r"from\s+lib\.parts\s+import\s+([\w ,]+)", text):
            for n in m.group(1).split(","):
                add(None, n.strip(), " (imported)")
        for m in re.finditer(r"from\s+lib\.parts\.(\w+)\s+import", text):
            add(None, m.group(1), " (imported)")
    else:
        for t in list_projects():
            for name in t["parts"]:
                add(t["name"], name)
        try:
            from app.library import catalog

            for p in catalog():
                add(None, p["name"])
        except Exception:
            pass
    return out


def plan_print(
    items: list[dict],
    bed: tuple[float, float] = DEFAULT_BED,
    want_objects: bool = False,
    strategy: str = "material",
    supports: bool = True,
) -> dict:
    """items: [{project?: str, name: str, qty: int}] → the arranged plate(s).

    `strategy` picks the orientation objective (see STRATEGIES): least support
    material, smallest footprints (fewest plates), or shortest parts (fastest).
    `supports` toggles support material (auto-placed where overhangs need it) —
    it drops the support time from the estimate and, downstream, from the slice.
    Returns {ok, shapes, states, bbox, stats, fits, plates} (plus the raw located
    objects under "_objects"/"_plate_of" when want_objects, for export)."""
    import shutil
    import tempfile
    from pathlib import Path

    from build123d import Pos, Rot

    from app.kernel.runner import _obj_color
    from app.project_runner import _RUN_LOCK, _materialize
    from app.tessellate import tessellate

    wanted = [
        (str(i.get("project") or "") or None, str(i.get("name") or ""), max(int(i.get("qty") or 0), 0)) for i in items
    ]
    wanted = [w for w in wanted if w[1] and w[2] > 0]
    if not wanted:
        return {"ok": False, "error": "pick at least one part (qty ≥ 1)"}

    needs_projects = any(p for p, _n, _q in wanted)
    tmp = Path(tempfile.mkdtemp(prefix="cadprint_")) if needs_projects else None

    with _RUN_LOCK:
        added = None
        try:
            if tmp is not None:
                _materialize(tmp, {}, "_none")
                added = str(tmp)
                sys.path.insert(0, added)

            # (label, solid, orientation, support, per-part est minutes, qty, mesh)
            oriented: list[tuple[str, Any, str, float, float, int, tuple[Any, Any]]] = []
            # Parts may declare specs with the ambient `require(...)` — bind the
            # DSL while building (same as any run), else such parts NameError.
            from app.kernel.runner import _ambient_dsl, _make_namespace

            ns, _shown, _specs = _make_namespace()
            with _ambient_dsl(ns):
                for project, name, qty in wanted:
                    try:
                        base = _build_part(project, name)
                    except Exception as exc:  # noqa: BLE001
                        return {"ok": False, "error": f"couldn't build {name}: {type(exc).__name__}: {exc}"}
                    solid, orientation, support, minutes, mesh, _prof = _orient(
                        base, strategy if strategy in STRATEGIES else "material", supports
                    )
                    oriented.append(
                        (f"{project + '/' if project else ''}{name}", solid, orientation, support, minutes, qty, mesh)
                    )

            # one rect per INSTANCE (carrying the oriented mesh so a plate's time
            # can be aggregated with SHARED layer overhead, not summed per part)
            rects: list[tuple[int, float, float]] = []
            inst: list[tuple[int, Any, str, tuple[Any, Any]]] = []  # (rect idx, solid, display name, mesh)
            counters: dict[str, int] = {}
            for label, solid, _o, _s, _minutes, qty, mesh in oriented:
                bb = solid.bounding_box()
                w, d = bb.max.X - bb.min.X + PADDING, bb.max.Y - bb.min.Y + PADDING
                for _ in range(qty):
                    counters[label] = counters.get(label, 0) + 1
                    idx = len(rects)
                    rects.append((idx, w, d))
                    inst.append((idx, solid, f"{label}_{counters[label]}".replace("/", "_"), mesh))

            placements, plates, fits = _pack_plates(rects, bed)

            # Lay the plates out in a row along X with a visible gap, the whole
            # row centered at the origin. Rotated instances get a free Z-spin
            # (support-neutral) that let them pack tighter.
            gap = 30.0
            n_plates = len(plates)
            row_w = n_plates * bed[0] + (n_plates - 1) * gap
            objs, names, colors, plate_of = [], [], [], []
            plate_meshes: list[list[tuple[Any, Any]]] = [[] for _ in range(n_plates)]
            for (idx, solid, name, mesh), _r in zip(inst, rects, strict=True):
                pi, cx, cy, rotated = placements[idx]
                placed = Rot(Z=90) * solid if rotated else solid
                px = pi * (bed[0] + gap) - row_w / 2  # this plate's left edge
                objs.append(Pos(px + cx, cy - bed[1] / 2, 0) * placed)
                names.append(f"plate{pi + 1}_{name}" if n_plates > 1 else name)
                colors.append(_obj_color(solid))
                plate_of.append(pi)
                plate_meshes[pi].append(mesh)

            shapes, states, bbox = tessellate(objs, names=names, colors=colors)
        finally:
            if added and added in sys.path:
                sys.path.remove(added)
            for mod in [m for m in sys.modules if m == "projects" or m.startswith("projects.")]:
                del sys.modules[mod]
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    # A plate's time is its parts printing TOGETHER: extrusion (incl. support)
    # summed, but layer overhead paid once per plate layer (shared). This is the
    # correct model for "fastest total time" — summing per-part times would
    # over-count the shared layers. (The auto-slicer still supersedes this per
    # plate with the exact number; the grand total is the shortest-path time to
    # print everything on one printer, support included.)
    from app.kernel.print_time import plate_minutes as _plate_minutes_est

    plan_prof = None if supports else {"support_density": 0.0}
    plate_minutes = [_plate_minutes_est(m, plan_prof) if m else 0.0 for m in plate_meshes]
    total_minutes = round(sum(plate_minutes), 1)

    # A part "needs support" when its best orientation still leaves meaningful
    # overhang area — surfaced so the user sees why a part costs what it does.
    _SUP_MIN = 25.0  # mm² — below this, overhang is negligible (chamfers etc.)
    stats = [
        {
            "name": label,
            "qty": qty,
            "orientation": orientation,
            "support_area": round(support, 1),
            "needs_support": support > _SUP_MIN,
            "est_min": round(minutes, 1),
        }
        for label, _s, orientation, support, minutes, qty, _mesh in oriented
    ]
    result: dict[str, Any] = {
        "ok": True,
        "shapes": shapes,
        "states": states,
        "bbox": bbox,
        "stats": stats,
        "supports": supports,
        "any_needs_support": any(s["needs_support"] for s in stats),
        "fits": fits,
        "total_min": total_minutes,  # shortest-path time for ALL plates (one printer), support incl.
        "plates": [
            {
                "index": i,
                "w": round(p.used_w, 1),
                "d": round(p.used_d, 1),
                "bed_w": bed[0],
                "bed_d": bed[1],
                "est_min": round(plate_minutes[i], 1),
            }
            for i, p in enumerate(plates)
        ],
        "slicer": _has_slicer(),
        "slicer_name": _slicer_name(),  # "orca" | "prusa" | "none"
    }
    if want_objects:
        result["_objects"] = objs
        result["_plate_of"] = plate_of
    return result
