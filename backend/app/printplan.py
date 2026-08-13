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
# The six principal (flat-on-a-face) orientations. These are ALWAYS in the
# ground-truth probe set when they fit the bed — never gated behind the cheap
# heuristic — because "flat on a face" is what a human reaches for first, and the
# heuristic's enclosure penalty used to bury the right one (a funnel's shallow
# socket bores read as "trapped support" and sank the mouth-down orientation
# below three near-identical tilts, so probing only ever compared the tilts).
_PRINCIPALS = ("as-is", "upside-down", "on side +X", "on side -X", "on side +Y", "on side -Y")
_HEUR_EXTRA = 4  # heuristic tilt candidates probed BEYOND the principals (teardrop holes etc.)
_PROBE_MAX = 10  # cap on real slices per part (mesh-hash cached, so re-plans are free)
_PROBE_WORKERS = 4  # concurrent slicer subprocesses during the orientation bake-off
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


def _probe_fields(res: dict) -> dict:
    """Pull the display/ranking fields out of a real slice result."""
    return {
        "support_g": res.get("support_g"),
        "model_g": res.get("model_g"),
        "minutes": res.get("minutes"),
        "slicer": res.get("slicer"),
        "probed": True,
    }


def _nearest_principal_tilt(d: Any) -> float:
    """Angle (deg) from the nearest principal axis — 0 for a flat-on-a-face
    orientation, larger the more tilted. Used to prefer clean flat prints and to
    tell the UI 'this one's a weird tilt'."""
    import math

    import numpy as np

    dn = np.asarray(d, dtype=float)
    dn = dn / (np.linalg.norm(dn) or 1.0)
    best = max(
        float(np.clip(abs(np.dot(dn, p)), -1.0, 1.0))
        for p in ((0, 0, -1), (0, 0, 1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0))
    )
    return round(math.degrees(math.acos(min(best, 1.0))), 1)


def _orient(
    obj: Any,
    strategy: str = "material",
    supports: bool = True,
    hints: list[dict] | None = None,
    force: str | None = None,
    probe: dict | None = None,
    bed: tuple[float, float] | None = None,
    thumbs: bool = False,
) -> tuple[Any, str, float, float, tuple[Any, Any], Any, dict]:
    """Pick the best orientation for the strategy and return the full ranked field.

    Phase 1 scores ~48 candidate down-directions on cheap mesh metrics (overhang
    area, removability-weighted support cost, height, footprint, BED-CONTACT area
    for stability). Phase 2 layer-slices a shortlist for instant minutes. Phase 3
    (opt-in `probe={"bed","settings"}`) really slices — IN PARALLEL — every
    bed-fitting PRINCIPAL orientation plus the top heuristic tilts, and ranks by
    the slicer's ACTUAL support grams / minutes with a bed-contact stability
    tiebreak. Crucially the principals are always probed, never gated behind the
    heuristic, so the mouth-down/flat orientation can't be silently dropped.

    Returns (rotated+dropped solid, label, overhang area, est minutes, oriented
    mesh, profile, extra). `extra["candidates"]` is the whole ranked field with
    per-orientation metrics (for the UI); `extra["alts"]` is the Pareto set for
    swap-to-fit packing; `extra["probe"]` is the winner's real numbers."""
    import numpy as np
    from build123d import Axis, Pos

    from app.kernel.print_time import (
        bed_contact_area,
        build_occupancy,
        candidate_down_dirs,
        estimate_minutes,
        mesh_of,
        rotation_to_down,
        support_area,
        support_cost,
        support_volume,
    )

    prof = None if supports else {"support_density": 0.0}
    verts, tris = mesh_of(obj)
    occ = build_occupancy(verts, tris)

    # A forced orientation overrides the SELECTION, never the evaluation: the full
    # candidate field still gets scored so the compare-&-choose gallery (and the
    # way back to auto) survives the override.
    candidates = candidate_down_dirs()
    forced_label = force if force and force != "auto" and any(c[0] == force for c in candidates) else None

    # Phase 1 — cheap metrics for every candidate.
    metas: list[dict] = []
    for label, dvec in candidates:
        r, axis, angle = rotation_to_down(dvec)
        vr = verts @ r.T
        cost = support_cost(vr, tris, occ, r)
        pen = _hint_penalty(hints or [], r)
        v = vr - [0.0, 0.0, float(vr[:, 2].min())]
        height = float(v[:, 2].max())
        w = float(v[:, 0].max() - v[:, 0].min())
        d = float(v[:, 1].max() - v[:, 1].min())
        area_overhang = support_area(v, tris)
        sup_vol = support_volume(v, tris)
        contact = bed_contact_area(v, tris)
        tilt = _nearest_principal_tilt(dvec)
        unfit = 0
        if bed is not None:
            bw, bd = bed[0] - PADDING, bed[1] - PADDING
            unfit = 0 if ((w <= bw and d <= bd) or (d <= bw and w <= bd)) else 1
        metas.append(
            {
                "label": label,
                "axis": tuple(np.asarray(axis, dtype=float)),
                "angle": float(angle),
                "v": v,
                "cost": float(cost),
                "pen": round(float(pen), 1),
                "height": height,
                "w": w,
                "d": d,
                "footprint": w * d,
                "overhang": float(area_overhang),
                "sup_vol": float(sup_vol),
                "contact": float(contact),
                "tilt": tilt,
                "unfit": unfit,
                "principal": label in _PRINCIPALS,
                "est_min": None,
                "support_g": None,
                "model_g": None,
                "minutes": None,
                "probed": False,
            }
        )

    # Stability: how much flat bed contact this part NEEDS scales with its size —
    # 60 mm² plants a clip, but a 150 mm funnel balanced on 100 mm² is a tip-over.
    # Below the need, the orientation is "balancing on an edge" and loses to any
    # stable one before material is even considered (a hard tier, like bed-fit).
    _CONTACT_MIN = 60.0

    def _stable_need(m: dict) -> float:
        return max(_CONTACT_MIN, 0.04 * float(m["footprint"]))

    def unstable(m: dict) -> int:
        return int(float(m["contact"]) < _stable_need(m))

    def tippy(m: dict) -> float:
        return max(0.0, _stable_need(m) - float(m["contact"]))

    # Cheap heuristic support score used when we CAN'T slice: support VOLUME
    # (overhang footprint × drop height), not raw area — a floor ring hovering
    # 2.5 mm over the bed is a dusting of support, not a disaster, and area-based
    # ranking used to let absurd tilts "win" against it. Enclosure (removability)
    # stays a mild multiplier.
    def heur(m: dict) -> float:
        enclosure = max(0.0, float(m["cost"]) - float(m["overhang"])) / max(float(m["overhang"]), 1.0)
        return float(m["sup_vol"]) * (1.0 + 0.15 * enclosure)

    # The evaluation set: EVERY bed-fitting principal (always — this is the fix)
    # plus the best heuristic tilts, deduped by resulting placement, capped.
    def sig(m: dict) -> tuple:
        return (round(m["height"]), round(m["w"]), round(m["d"]), round(m["contact"] / 5) * 5)

    fitting = [m for m in metas if m["unfit"] == 0] or metas
    principals = [m for m in fitting if m["principal"]]
    tilts = sorted(
        (m for m in fitting if not m["principal"]), key=lambda m: (m["pen"], unstable(m), heur(m), m["height"])
    )
    eval_set: list[dict] = []
    seen_sig: set[tuple] = set()
    for m in principals + tilts:
        s = sig(m)
        if s in seen_sig:
            continue
        seen_sig.add(s)
        eval_set.append(m)
        if len(eval_set) >= _PROBE_MAX and sum(not x["principal"] for x in eval_set) >= _HEUR_EXTRA:
            break
    # keep principals + up to _HEUR_EXTRA tilts
    keep_tilts = 0
    trimmed: list[dict] = []
    for m in eval_set:
        if not m["principal"]:
            if keep_tilts >= _HEUR_EXTRA:
                continue
            keep_tilts += 1
        trimmed.append(m)
    eval_set = trimmed[:_PROBE_MAX]

    # A forced orientation is always part of the evaluation (so it gets layer
    # estimates and, with probe, real slicer numbers) even if the heuristic
    # would have trimmed it.
    if forced_label and not any(m["label"] == forced_label for m in eval_set):
        fm = next((m for m in metas if m["label"] == forced_label), None)
        if fm is not None:
            eval_set.append(fm)

    # Phase 2 — instant layer-sliced minutes for the eval set.
    for m in eval_set:
        m["est_min"] = estimate_minutes(m["v"], tris, profile=prof)["minutes"]

    # Phase 3 — ground truth. Really slice the WHOLE eval set in parallel; cache
    # by mesh hash + orientation + settings so re-plans never re-slice.
    probe_info: dict | None = None
    if probe and eval_set:
        import concurrent.futures as _cf

        sig_settings = tuple(sorted((k, str(v_)) for k, v_ in (probe.get("settings") or {}).items()))
        sha = _mesh_sha(verts, tris)
        todo: list[dict] = []
        for m in eval_set:
            ck = (sha, m["label"], sig_settings)
            cached = _PROBE_CACHE.get(ck)
            if cached is not None:
                m.update(_probe_fields(cached))
            else:
                todo.append(m)
        if todo:
            with _cf.ThreadPoolExecutor(max_workers=min(_PROBE_WORKERS, len(todo))) as ex:
                futs = {
                    ex.submit(_probe_one, obj, m["axis"], m["angle"], probe["bed"], probe.get("settings") or {}): m
                    for m in todo
                }
                for fut in _cf.as_completed(futs):
                    m = futs[fut]
                    try:
                        res = fut.result()
                    except Exception:  # noqa: BLE001 — a slicer failure just leaves this one un-probed
                        res = None
                    if res is not None:
                        _PROBE_CACHE[(sha, m["label"], sig_settings)] = res
                        m.update(_probe_fields(res))
        n_probed = sum(1 for m in eval_set if m["probed"])
        if n_probed:
            probe_info = {"candidates": n_probed}

    # --- rank the eval set in the strategy's currency ---------------------------
    # bed-fit and declared print intent (hints) always lead. Then the strategy
    # metric, using REAL slicer numbers where we have them. Bed-contact area
    # (bigger = more stable) and low tilt break ties toward the flat print a human
    # would pick — so a support-heavy weird tilt never wins on a coin-flip.
    def rank_key(m: dict) -> tuple:
        stable = (-round(float(m["contact"])), round(float(m["tilt"])))  # more contact, less tilt = better
        if m["probed"]:
            # real support grams, plus a small gram-equivalent tippiness nudge so a
            # marginally-lighter but tippy tilt can't beat a solidly flat print.
            sup_metric = round(float(m["support_g"] or 0.0) + 0.04 * tippy(m), 1)
            time_metric = round(float(m["minutes"] or 0.0), 1)
        else:
            sup_metric = round(heur(m), 1)
            time_metric = round(float(m["est_min"] or 0.0), 1)
        if strategy == "plates":
            return (
                m["unfit"],
                m["pen"],
                unstable(m),
                round(float(m["footprint"]), 1),
                sup_metric,
                *stable,
                time_metric,
            )
        if strategy == "fastest":
            return (m["unfit"], m["pen"], unstable(m), time_metric, sup_metric, *stable)
        return (m["unfit"], m["pen"], unstable(m), sup_metric, *stable, time_metric)  # material

    eval_set.sort(key=rank_key)
    best = eval_set[0]

    # Principal bias: a weird tilt has to EARN its weirdness. If the best flat-on-
    # a-face orientation is within 25% of the tilt on the strategy's own currency
    # (and no worse on bed-fit/hints/stability), print flat — flat prints pack
    # tighter, adhere better, and look like something a human chose.
    def strat_metric(m: dict) -> float:
        if strategy == "plates":
            return float(m["footprint"])
        if strategy == "fastest":
            return float(m["minutes"] if m["probed"] and m["minutes"] is not None else (m["est_min"] or 0.0))
        if m["probed"] and m["support_g"] is not None:
            return float(m["support_g"])
        return heur(m)

    if not best["principal"]:
        peers = [
            m
            for m in eval_set
            if m["principal"]
            and m["probed"] == best["probed"]
            and m["unfit"] == best["unfit"]
            and m["pen"] <= best["pen"]
            and unstable(m) <= unstable(best)
        ]
        if peers:
            bp = min(peers, key=rank_key)
            if strat_metric(bp) <= 1.25 * strat_metric(best) + 1e-9:
                best = bp

    best["recommended"] = True
    chosen = best
    if forced_label:
        fm = next((m for m in eval_set if m["label"] == forced_label), None)
        if fm is not None:
            chosen = fm
    label, axis, angle = chosen["label"], chosen["axis"], chosen["angle"]
    sup, minutes, best_v = chosen["overhang"], (chosen["minutes"] or chosen["est_min"] or 0.0), chosen["v"]
    if chosen["probed"]:
        probe_info = {
            "support_g": chosen.get("support_g"),
            "model_g": chosen.get("model_g"),
            "minutes": chosen.get("minutes"),
            "slicer": chosen.get("slicer"),
            "candidates": (probe_info or {}).get("candidates", 1),
        }

    # Pareto set of runner-ups on (footprint, support) for swap-to-fit packing.
    alts: list[dict] = []
    for m in eval_set:
        area = m["footprint"]
        acost = m["cost"]
        amin = m["minutes"] or m["est_min"] or 0.0
        dominated = any(
            (
                o["w"] * o["d"] <= area
                and o["cost"] <= acost
                and o["minutes"] <= amin
                and (o["w"] * o["d"], o["cost"], o["minutes"]) != (area, acost, amin)
            )
            for o in alts
        )
        if not dominated:
            alts.append(
                {
                    "label": m["label"],
                    "axis": m["axis"],
                    "angle": m["angle"],
                    "w": m["w"],
                    "d": m["d"],
                    "cost": acost,
                    "sup": m["overhang"],
                    "minutes": amin,
                    "pen": float(m["pen"]),
                }
            )

    # A compact per-orientation record for the UI (the whole ranked field). When
    # `thumbs`, each carries a shaded iso SVG of the part IN that orientation —
    # rendered from the already-rotated, bed-dropped mesh, so it shows exactly how
    # the part sits on the plate (mouth-down vs a weird tilt, at a glance).
    thumb_svgs: dict[str, str] = {}
    if thumbs:
        from app.thumbnail import iso_svg_mesh

        for m in eval_set:
            thumb_svgs[m["label"]] = iso_svg_mesh(m["v"], tris, size=104, cells=16)
    candidates_out = []
    for m in eval_set:
        rec = {
            "label": m["label"],
            "tilt_deg": m["tilt"],
            "principal": m["principal"],
            "fits": m["unfit"] == 0,
            "w": round(m["w"], 1),
            "d": round(m["d"], 1),
            "height": round(m["height"], 1),
            "footprint": round(m["footprint"], 1),
            "contact": round(m["contact"], 1),
            "overhang": round(m["overhang"], 1),
            "est_min": round(m["est_min"], 1) if m["est_min"] is not None else None,
            "support_g": m["support_g"],
            "model_g": m["model_g"],
            "minutes": m["minutes"],
            "probed": m["probed"],
            "recommended": bool(m.get("recommended")),
            "hint_penalty": m["pen"],
        }
        if thumbs:
            rec["thumb"] = thumb_svgs[m["label"]]
        candidates_out.append(rec)

    oriented = obj.rotate(Axis((0, 0, 0), tuple(np.asarray(axis, dtype=float))), angle) if angle else obj
    bb = oriented.bounding_box()
    oriented = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * oriented
    extra = {"alts": alts, "probe": probe_info, "candidates": candidates_out, "verts": verts, "tris": tris}
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


def _with_project(project: str | None, fn):
    """Run ``fn()`` with the project workspace materialized on sys.path and the
    ambient DSL (show/require/print_hint) bound — the setup a part build needs."""
    import shutil
    import tempfile
    from pathlib import Path

    from app.kernel.runner import _ambient_dsl, _make_namespace
    from app.project_runner import _RUN_LOCK, _materialize

    tmp = Path(tempfile.mkdtemp(prefix="cadthumb_")) if project else None
    with _RUN_LOCK:
        added = None
        try:
            if tmp is not None:
                _materialize(tmp, {}, "_none")
                added = str(tmp)
                sys.path.insert(0, added)
            ns, _shown, _specs = _make_namespace()
            with _ambient_dsl(ns):
                return fn()
        finally:
            if added and added in sys.path:
                sys.path.remove(added)
            for mod in [m for m in sys.modules if m == "projects" or m.startswith("projects.")]:
                del sys.modules[mod]
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)


def render_part_thumb(project: str | None, name: str, size: int = 120) -> str:
    """A shaded iso SVG thumbnail of a printable part (as-modelled) for the picker."""
    from app.kernel.print_time import mesh_of
    from app.thumbnail import iso_svg_mesh

    def go() -> str:
        v, t = mesh_of(_build_part(project, name))
        return iso_svg_mesh(v, t, size=size)

    try:
        return _with_project(project, go)
    except Exception:  # noqa: BLE001 — a thumbnail must never break the picker
        return ""


def orientation_thumbs(
    project: str | None, name: str, bed: tuple[float, float] = DEFAULT_BED, supports: bool = True
) -> dict[str, str]:
    """{orientation label → iso SVG of the part in that orientation}. The frontend
    merges these into the plan's ranked orientation field by label, so the gallery
    shows each orientation's thumbnail beside its real support/time numbers."""

    def go() -> dict[str, str]:
        base = _build_part(project, name)
        _s, _l, _su, _m, _mesh, _p, extra = _orient(
            base, "material", supports, hints=[], force="auto", probe=None, bed=bed, thumbs=True
        )
        return {c["label"]: c["thumb"] for c in extra.get("candidates", []) if c.get("thumb")}

    try:
        return _with_project(project, go)
    except Exception:  # noqa: BLE001
        return {}


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
                        bed=bed,
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
                # reduces the plate count the most for the least extra cost —
                # measured in the STRATEGY'S own currency (material → support
                # cost, fastest → minutes; plates is generous on both, since
                # plate count IS its objective). Hints (pen) are never traded.
                def swap_price(cur: dict | None, alt: dict) -> tuple[float, bool]:
                    """(price of this swap in the strategy's currency, acceptable?)"""
                    cur_cost = cur["cost"] if cur else 0.0
                    cur_min = cur["minutes"] if cur else 0.0
                    if strategy == "fastest":
                        incr = alt["minutes"] - cur_min
                        return incr, incr <= cur_min * 0.25 + 10.0
                    if strategy == "plates":
                        incr = alt["cost"] - cur_cost
                        return incr, incr <= cur_cost * 3.0 + 200.0
                    incr = alt["cost"] - cur_cost  # material
                    return incr, incr <= cur_cost * 0.75 + 100.0

                for _round in range(6):
                    baseline = len(plates)
                    best_swap: tuple[int, float, int, dict] | None = None  # (plates, price, part, alt)
                    for i, o in enumerate(oriented):
                        cur = o["swap"] or next(
                            (a for a in o["extra"]["alts"] if a["label"] == o["orientation"]),
                            None,
                        )
                        cur_area = (cur["w"] * cur["d"]) if cur else part_dims(o)[0] * part_dims(o)[1]
                        cur_pen = cur["pen"] if cur else 0.0
                        for alt in o["extra"]["alts"]:
                            if alt is cur or alt["w"] * alt["d"] >= cur_area or alt["pen"] > cur_pen:
                                continue
                            price, ok = swap_price(cur, alt)
                            if not ok:
                                continue
                            prev = o["swap"]
                            o["swap"] = alt
                            _r, _pl, trial_plates, trial_fits = pack_current()
                            o["swap"] = prev
                            if trial_fits and len(trial_plates) < baseline:
                                cand = (len(trial_plates), price, i, alt)
                                if best_swap is None or cand[:2] < best_swap[:2]:
                                    best_swap = cand
                    if best_swap is None:
                        break
                    _n, _price, i, alt = best_swap
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
            # Top-down footprint per plate (bed-local coords) — powers the 2D plate
            # preview so the packing is visible without reading the 3D viewport.
            plate_layout: list[list[dict]] = [[] for _ in range(n_plates)]
            for (idx, solid, name, mesh), _r in zip(inst, rects, strict=True):
                pi, cx, cy, rotated = placements[idx]
                placed = Rot(Z=90) * solid if rotated else solid
                px = pi * (bed[0] + gap) - row_w / 2  # this plate's left edge
                objs.append(Pos(px + cx, cy - bed[1] / 2, 0) * placed)
                names.append(f"plate{pi + 1}_{name}" if n_plates > 1 else name)
                colors.append(_obj_color(solid))
                plate_of.append(pi)
                plate_meshes[pi].append(mesh)
                bb = solid.bounding_box()
                pw, pdp = bb.max.X - bb.min.X, bb.max.Y - bb.min.Y
                lw, ld = (pdp, pw) if rotated else (pw, pdp)
                col = _obj_color(solid)
                plate_layout[pi].append(
                    {
                        "name": name,
                        "cx": round(cx, 1),
                        "cy": round(cy, 1),
                        "w": round(lw, 1),
                        "d": round(ld, 1),
                        "rotated": bool(rotated),
                        "color": col if isinstance(col, str) else None,
                    }
                )

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
        # The whole ranked orientation field (metrics per candidate) — powers the
        # UI's orientation picker so the choice is transparent and overridable.
        cands = o["extra"].get("candidates")
        if cands:
            row["orientations"] = cands
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
                "layout": plate_layout[i],  # top-down footprints for the 2D preview
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
