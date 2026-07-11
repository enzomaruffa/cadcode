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


def _hint_penalty(hints: list[dict], r: Any) -> float:
    """How badly a candidate orientation violates the part's declared print
    intent. flow=(x,y,z): water runs along this part-frame vector — layer ridges
    are contour lines, so the flow must lie IN the layer plane (rotated flow ⊥ Z);
    penalty ∝ |rotated_flow · ẑ|. cosmetic faces: their normals must not end up
    facing DOWN (support scars) — penalty ∝ face area. Hints outrank strategy."""
    import numpy as np

    pen = 0.0
    for h in hints or []:
        f = h.get("flow")
        if f is not None:
            v = np.asarray(f, dtype=float)
            n = np.linalg.norm(v)
            if n > 0:
                pen += 1000.0 * abs(float((r @ (v / n))[2]))
        for face in h.get("cosmetic_faces") or []:
            try:
                nrm = face.normal_at(face.center())
                nv = r @ np.array([float(nrm.X), float(nrm.Y), float(nrm.Z)])
                if nv[2] < -0.35:  # would need support / sit on the bed → scarred
                    pen += 10.0 * float(face.area)
            except Exception:  # noqa: BLE001 - hints must never break planning
                continue
    return pen


# Ground-truth probe cache: (mesh sha1, orientation label, settings signature) →
# the real slicer's {minutes, support_g, …}. Content-addressed, so re-planning
# the same part at the same orientation never re-slices.
_PROBE_CACHE: dict[tuple[str, str, tuple], dict] = {}
_PROBE_KEEP = 3  # how many top candidates get a real slice
_PROBE_TIMEOUT_S = 150


def _mesh_sha(verts: Any, tris: Any) -> str:
    import hashlib

    import numpy as np

    return hashlib.sha1(np.round(np.asarray(verts, dtype=float), 2).tobytes() + np.asarray(tris).tobytes()).hexdigest()


def _probe_one(
    obj: Any, axis: Any, angle: float, bed: tuple[float, float], settings: dict, timeout_s: int = _PROBE_TIMEOUT_S
) -> dict | None:
    """Really slice ONE part at ONE orientation and return the slicer's numbers."""
    import numpy as np
    from build123d import Axis, Pos

    from app.kernel.slicer import slice_minutes

    oriented = obj.rotate(Axis((0, 0, 0), tuple(np.asarray(axis, dtype=float))), angle) if angle else obj
    bb = oriented.bounding_box()
    oriented = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * oriented
    return slice_minutes([oriented], bed=bed, timeout_s=timeout_s, settings=settings)


def _orient(
    obj: Any,
    strategy: str = "material",
    supports: bool = True,
    hints: list[dict] | None = None,
    force: str | None = None,
    probe: dict | None = None,
) -> tuple[Any, str, float, float, tuple[Any, Any], Any, dict]:
    """Pick the best orientation for the strategy. Phase 1: ~48 candidate down
    directions scored on removability-weighted support cost (+ height/footprint)
    — support trapped inside a bore/pocket is penalized up to 10×, so "print it
    upside-down with external supports" wins on the shapes where a human would
    choose that. Phase 2: layer-slice the best few for real minutes. Phase 3
    (opt-in, `probe={"bed", "settings"}`): the heuristic only FILTERS — the top
    few survivors are sliced by the REAL slicer (Orca tree supports and all) and
    re-ranked by actual support grams × our removability ratio + real minutes.
    Returns (rotated + bed-dropped solid, label, support area, est minutes,
    oriented mesh (verts, tris), profile, extra) — extra carries the Pareto set
    of runner-up orientations (for swap-to-fit packing) and the probe numbers."""
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

    # Candidates: the full sweep, or just the user's forced orientation.
    candidates = candidate_down_dirs()
    if force and force != "auto":
        forced = [c for c in candidates if c[0] == force]
        if forced:
            candidates = forced

    # Phase 1 — cheap metrics for every candidate. Declared print intent
    # (print_hint flow/cosmetic) outranks everything: it leads the sort key.
    phase1: list[tuple[tuple[float, float, float, float], str, Any, Any, float, float, Any]] = []
    for label, d in candidates:
        r, axis, angle = rotation_to_down(d)
        vr = verts @ r.T
        cost = support_cost(vr, tris, occ, r)  # rotated-only (support_cost maps rays back to the grid)
        pen = _hint_penalty(hints or [], r)
        v = vr - [0.0, 0.0, float(vr[:, 2].min())]  # dropped onto the bed for the size metrics
        height = float(v[:, 2].max())
        footprint = float((v[:, 0].max() - v[:, 0].min()) * (v[:, 1].max() - v[:, 1].min()))
        phase1.append(
            ((round(pen, 1), round(cost, 1), round(height, 1), round(footprint, 1)), label, axis, angle, cost, pen, v)
        )
    phase1.sort(key=lambda p: p[0])

    # Phase 2 — real layer-sliced estimate for the survivors; strategy picks.
    scored: list[tuple[tuple[float, ...], str, Any, Any, float, float, Any, float, float]] = []
    for (_pen_key, _cost_key, _h, fp), label, axis, angle, cost, pen, v in phase1[:_PHASE2_KEEP]:
        minutes = estimate_minutes(v, tris, profile=prof)["minutes"]
        sup = support_area(v, tris)
        pen_r = round(pen, 1)
        if strategy == "plates":
            key = (pen_r, fp, cost, minutes)
        elif strategy == "fastest":
            key = (pen_r, minutes, cost, fp)
        else:  # material — removability-weighted support first
            key = (pen_r, cost, minutes, fp)
        scored.append((key, label, axis, angle, sup, minutes, v, cost, pen_r))
    scored.sort(key=lambda s: s[0])

    # Phase 3 — ground truth. Slice the top candidates for REAL and re-rank on
    # actual support grams (weighted by our removability ratio — the slicer
    # can't know a support is trapped in a bore) + real minutes.
    probe_info: dict | None = None
    if probe and scored:
        sig = tuple(sorted((k, str(v_)) for k, v_ in (probe.get("settings") or {}).items()))
        sha = _mesh_sha(verts, tris)
        seen_labels: set[str] = set()
        probed: list[tuple[tuple[float, ...], int, dict]] = []
        for i, (_key, label, axis, angle, sup, _minutes, v, cost, pen_r) in enumerate(scored[:_PROBE_KEEP]):
            if label in seen_labels:
                continue
            seen_labels.add(label)
            ck = (sha, label, sig)
            res = _PROBE_CACHE.get(ck)
            if res is None:
                res = _probe_one(obj, axis, angle, probe["bed"], probe.get("settings") or {})
                if res is not None:
                    _PROBE_CACHE[ck] = res
            if res is None:
                continue  # slicer failed on this one — heuristic rank stands for it
            ratio = min(max(cost / max(sup, 1.0), 1.0), 10.0)  # removability multiplier
            g = float(res.get("support_g") or 0.0)
            m = float(res.get("minutes") or 0.0)
            fp = float((v[:, 0].max() - v[:, 0].min()) * (v[:, 1].max() - v[:, 1].min()))
            if strategy == "plates":
                pkey = (pen_r, round(fp, 1), round(g * ratio, 2), m)
            elif strategy == "fastest":
                pkey = (pen_r, m, round(g * ratio, 2), round(fp, 1))
            else:
                pkey = (pen_r, round(g * ratio, 2), m, round(fp, 1))
            probed.append((pkey, i, res))
        if probed:
            probed.sort(key=lambda p: p[0])
            _pk, best_i, res = probed[0]
            scored.insert(0, scored.pop(best_i))
            probe_info = {
                "support_g": res.get("support_g"),
                "model_g": res.get("model_g"),
                "minutes": res.get("minutes"),
                "slicer": res.get("slicer"),
                "candidates": len(probed),
            }

    _key, label, axis, angle, sup, minutes, best_v, _cost, _pen = scored[0]

    # Pareto set of runner-ups on (footprint area, support cost) — packing can
    # swap to one of these when a smaller footprint saves a whole plate.
    alts: list[dict] = []
    for _k, alabel, aaxis, aangle, asup, amin, av, acost, apen in scored:
        w = float(av[:, 0].max() - av[:, 0].min())
        d = float(av[:, 1].max() - av[:, 1].min())
        area = w * d
        dominated = any(
            (o["w"] * o["d"] <= area and o["cost"] <= acost and (o["w"] * o["d"], o["cost"]) != (area, acost))
            for o in alts
        )
        if not dominated:
            alts.append(
                {
                    "label": alabel,
                    "axis": tuple(np.asarray(aaxis, dtype=float)),
                    "angle": float(aangle),
                    "w": w,
                    "d": d,
                    "cost": float(acost),
                    "sup": float(asup),
                    "minutes": float(amin),
                    "pen": float(apen),
                }
            )

    oriented = obj.rotate(Axis((0, 0, 0), tuple(np.asarray(axis, dtype=float))), angle) if angle else obj
    bb = oriented.bounding_box()
    # drop onto the bed and center the footprint at its own origin
    oriented = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * oriented
    extra = {"alts": alts, "probe": probe_info, "verts": verts, "tris": tris}
    return oriented, label, sup, minutes, (best_v, tris), prof, extra


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


def _alt_solid_mesh(base: Any, verts: Any, tris: Any, alt: dict) -> tuple[Any, tuple[Any, Any]]:
    """Materialize an alternate orientation: rotated + bed-dropped solid and the
    matching oriented mesh (for plate-time aggregation)."""
    import numpy as np
    from build123d import Axis, Pos

    axis, angle = alt["axis"], alt["angle"]
    solid = base.rotate(Axis((0, 0, 0), tuple(axis)), angle) if angle else base
    bb = solid.bounding_box()
    solid = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * solid
    # Rodrigues: rotate the mesh the same way the solid was rotated.
    a = np.asarray(axis, dtype=float)
    n = np.linalg.norm(a) or 1.0
    ux, uy, uz = a / n
    t = np.radians(angle)
    c, s = np.cos(t), np.sin(t)
    r = np.array(
        [
            [c + ux * ux * (1 - c), ux * uy * (1 - c) - uz * s, ux * uz * (1 - c) + uy * s],
            [uy * ux * (1 - c) + uz * s, c + uy * uy * (1 - c), uy * uz * (1 - c) - ux * s],
            [uz * ux * (1 - c) - uy * s, uz * uy * (1 - c) + ux * s, c + uz * uz * (1 - c)],
        ]
    )
    v = verts @ r.T
    v = v - [0.0, 0.0, float(v[:, 2].min())]
    return solid, (v, tris)


def plan_print(
    items: list[dict],
    bed: tuple[float, float] = DEFAULT_BED,
    want_objects: bool = False,
    strategy: str = "material",
    supports: bool = True,
    probe: bool = False,
    slice_settings: dict | None = None,
) -> dict:
    """items: [{project?: str, name: str, qty: int}] → the arranged plate(s).

    `strategy` picks the orientation objective (see STRATEGIES): least support
    material, smallest footprints (fewest plates), or shortest parts (fastest).
    `supports` toggles support material (auto-placed where overhangs need it) —
    it drops the support time from the estimate and, downstream, from the slice.
    `probe` (needs a slicer): ground-truth the orientation choice — the heuristic
    only shortlists, the REAL slicer (tree supports and all) ranks the finalists
    by actual support grams and minutes. After packing, a swap-to-fit pass tries
    each part's Pareto runner-up orientations when that saves a whole plate.
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
        (
            str(i.get("project") or "") or None,
            str(i.get("name") or ""),
            max(int(i.get("qty") or 0), 0),
            str(i.get("orient") or "auto"),  # per-part user override, "auto" = search
        )
        for i in items
    ]
    wanted = [w for w in wanted if w[1] and w[2] > 0]
    if not wanted:
        return {"ok": False, "error": "pick at least one part (qty ≥ 1)"}

    probe_ctx = None
    if probe and _has_slicer():
        st = dict(slice_settings or {})
        st["supports"] = supports
        probe_ctx = {"bed": bed, "settings": st}

    needs_projects = any(p for p, _n, _q, _o in wanted)
    tmp = Path(tempfile.mkdtemp(prefix="cadprint_")) if needs_projects else None

    with _RUN_LOCK:
        added = None
        try:
            if tmp is not None:
                _materialize(tmp, {}, "_none")
                added = str(tmp)
                sys.path.insert(0, added)

            # (label, solid, orientation, support, per-part est minutes, qty, mesh, extra)
            oriented: list[dict] = []
            # Parts may declare specs (`require`) and PRINT INTENT (`print_hint`)
            # — bind the ambient DSL while building, and slice each part's newly
            # collected hints off the shared collector.
            from app.kernel.runner import _ambient_dsl, _make_namespace

            ns, _shown, _specs = _make_namespace()
            all_hints: list[dict] = ns["__print_hints__"]
            with _ambient_dsl(ns):
                for project, name, qty, orient_override in wanted:
                    before = len(all_hints)
                    try:
                        base = _build_part(project, name)
                    except Exception as exc:  # noqa: BLE001
                        return {"ok": False, "error": f"couldn't build {name}: {type(exc).__name__}: {exc}"}
                    part_hints = all_hints[before:]
                    solid, orientation, support, minutes, mesh, _prof, extra = _orient(
                        base,
                        strategy if strategy in STRATEGIES else "material",
                        supports,
                        hints=part_hints,
                        force=orient_override,
                        probe=probe_ctx,
                    )
                    oriented.append(
                        {
                            "label": f"{project + '/' if project else ''}{name}",
                            "solid": solid,
                            "orientation": orientation,
                            "support": support,
                            "minutes": minutes,
                            "qty": qty,
                            "mesh": mesh,
                            "base": base,
                            "extra": extra,
                            "swap": None,  # filled by swap-to-fit
                        }
                    )

            # --- pack, then try to SAVE PLATES by swapping to Pareto runner-up
            # orientations (smaller footprint, bounded support-cost increase).
            def part_dims(o: dict) -> tuple[float, float]:
                if o["swap"] is not None:
                    return o["swap"]["w"] + PADDING, o["swap"]["d"] + PADDING
                bb = o["solid"].bounding_box()
                return bb.max.X - bb.min.X + PADDING, bb.max.Y - bb.min.Y + PADDING

            def pack_current() -> tuple[list[tuple[int, float, float]], dict, list[_Plate], bool]:
                rects: list[tuple[int, float, float]] = []
                for o in oriented:
                    w, d = part_dims(o)
                    for _ in range(o["qty"]):
                        rects.append((len(rects), w, d))
                placements, plates, fits = _pack_plates(rects, bed)
                return rects, placements, plates, fits

            rects, placements, plates, fits = pack_current()
            if fits and len(plates) > 1:
                # Greedy: each round, apply the single orientation swap that
                # reduces the plate count the most for the least extra support.
                # Hints (pen) are never traded away; cost may grow by at most
                # 3× (plates strategy) / 75% + 100mm² (others).
                limit = 3.0 if strategy == "plates" else 0.75
                for _round in range(6):
                    baseline = len(plates)
                    best_swap: tuple[float, int, dict] | None = None
                    for i, o in enumerate(oriented):
                        cur = o["swap"] or next(
                            (a for a in o["extra"]["alts"] if a["label"] == o["orientation"]),
                            None,
                        )
                        cur_cost = cur["cost"] if cur else 0.0
                        cur_area = (cur["w"] * cur["d"]) if cur else part_dims(o)[0] * part_dims(o)[1]
                        cur_pen = cur["pen"] if cur else 0.0
                        for alt in o["extra"]["alts"]:
                            if alt is cur or alt["w"] * alt["d"] >= cur_area or alt["pen"] > cur_pen:
                                continue
                            incr = alt["cost"] - cur_cost
                            if incr > cur_cost * limit + 100.0:
                                continue
                            prev = o["swap"]
                            o["swap"] = alt
                            _r, _pl, trial_plates, trial_fits = pack_current()
                            o["swap"] = prev
                            if trial_fits and len(trial_plates) < baseline:
                                score = (len(trial_plates), incr)
                                if best_swap is None or score < (best_swap[0], best_swap[2]["cost"] - cur_cost):
                                    best_swap = (len(trial_plates), i, alt)
                    if best_swap is None:
                        break
                    _n, i, alt = best_swap
                    oriented[i]["swap"] = alt
                    rects, placements, plates, fits = pack_current()

            # Materialize swapped parts: rotate the base into the alt orientation
            # and swap the mesh so plate times stay honest.
            for o in oriented:
                if o["swap"] is not None:
                    alt = o["swap"]
                    o["solid"], o["mesh"] = _alt_solid_mesh(o["base"], o["extra"]["verts"], o["extra"]["tris"], alt)
                    o["orientation"] = alt["label"]
                    o["support"] = alt["sup"]
                    o["minutes"] = alt["minutes"]

            # one rect per INSTANCE (carrying the oriented mesh so a plate's time
            # can be aggregated with SHARED layer overhead, not summed per part)
            inst: list[tuple[int, Any, str, tuple[Any, Any]]] = []  # (rect idx, solid, display name, mesh)
            counters: dict[str, int] = {}
            idx = 0
            for o in oriented:
                label = o["label"]
                for _ in range(o["qty"]):
                    counters[label] = counters.get(label, 0) + 1
                    inst.append((idx, o["solid"], f"{label}_{counters[label]}".replace("/", "_"), o["mesh"]))
                    idx += 1

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
    _SPLIT_MIN = 400.0  # mm² — even the BEST orientation is support-heavy → suggest a split
    stats = []
    for o in oriented:
        label, support, minutes, mesh = o["label"], o["support"], o["minutes"], o["mesh"]
        row: dict[str, Any] = {
            "name": label,
            "qty": o["qty"],
            "orientation": o["orientation"],
            "support_area": round(support, 1),
            "needs_support": support > _SUP_MIN,
            "est_min": round(minutes, 1),
        }
        pinfo = o["extra"].get("probe")
        if pinfo:
            # ground truth from the real slicer: exact support grams + minutes
            row["probe"] = pinfo
        if o["swap"] is not None:
            row["swapped_to_fit"] = True
        if support > _SPLIT_MIN:
            # No orientation escapes heavy support — the honest fix is often to
            # split into two flat-backed halves and print both cut-face down.
            h = float(mesh[0][:, 2].max() - mesh[0][:, 2].min())
            row["suggest_split"] = True
            row["split_hint"] = (
                f"even the best orientation needs ~{support:.0f} mm² of support — consider splitting into two "
                f"flat-backed halves: `a, b = split(part, bisect_by=Plane.XY.offset({h / 2:.1f}), keep=Keep.BOTH)` "
                "then print both cut-face down (glue or add alignment pins)."
            )
        stats.append(row)
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
        "probed": bool(probe_ctx),  # orientations ground-truthed by the real slicer
    }
    if want_objects:
        result["_objects"] = objs
        result["_plate_of"] = plate_of
    return result
