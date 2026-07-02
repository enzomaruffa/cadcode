"""Print plating: pick parts + quantities → an arranged, print-ready plate.

For each chosen part we try the six principal orientations and score them by the
area that would need support (downward faces past the overhang limit — same
physics as the printability heatmap), tie-breaking on height (shorter prints
faster and safer). The best orientation is dropped onto the bed (min Z = 0) and
all instances are shelf-packed onto the plate with padding. The result renders
in the viewport like any geometry and exports as one STL/3MF for the slicer.
"""

from __future__ import annotations

import sys
from typing import Any

from app.kernel.printability import OVERHANG_LIMIT, _overhang

# Default FDM bed (mm) — a common 220x220 printer; the UI can override.
DEFAULT_BED = (220.0, 220.0)
PADDING = 6.0  # space between parts on the plate


# 5x5 UV grid — catches curved undersides. Deliberately 5 points: an even count
# on a full cylinder lands every normal at exactly 45° (the threshold), scoring a
# sideways cylinder as support-free.
_UV = [0.1, 0.3, 0.5, 0.7, 0.9]


def _support_metrics(obj: Any) -> tuple[float, float]:
    """(support_area, height) for a candidate orientation: the summed area of
    downward face regions past the overhang limit, and the Z height. Curved
    faces are sampled on a UV grid (a face-center normal alone calls a sideways
    cylinder's belly 'vertical'). Flat undersides ON the bed are excluded."""
    from build123d import Face
    from ocp_tessellate import convert as C

    bb = obj.bounding_box()
    zmin = bb.min.Z
    height = bb.max.Z - zmin
    support = 0.0
    w = obj.wrapped if hasattr(obj, "wrapped") else obj
    for topo_face in C.get_faces(w):
        face = Face(topo_face)
        on_bed = abs(face.center().Z - zmin) < 0.05
        cell = face.area / (len(_UV) * len(_UV))
        for u in _UV:
            for v in _UV:
                try:
                    n = face.normal_at(u, v)
                except Exception:
                    n = face.normal_at()
                oh = _overhang(n.X, n.Y, n.Z, (0.0, 0.0, 1.0))
                if oh > OVERHANG_LIMIT:
                    # a flat underside resting on the bed needs no support
                    if on_bed and oh > 89.0:
                        continue
                    support += cell
    return support, height


def _orientations() -> list[tuple[str, float, float]]:
    """The six principal orientations as (label, X°, Y°)."""
    return [
        ("as-is", 0, 0),
        ("x+90", 90, 0),
        ("x-90", -90, 0),
        ("x180", 180, 0),
        ("y+90", 0, 90),
        ("y-90", 0, -90),
    ]


# What each strategy optimizes when choosing a part's orientation. The metrics
# per candidate are (support_area, height, footprint_area); the strategy is the
# priority order of those metrics:
#   material — least support waste (then shortest)
#   plates   — smallest footprint so more parts share a bed (then least support)
#   fastest  — shortest (layer count dominates print time; then least support)
STRATEGIES = ("material", "plates", "fastest")


def _orient(obj: Any, strategy: str = "material") -> tuple[Any, str, float]:
    """Pick the best orientation for the strategy. Returns the rotated +
    bed-dropped solid, the orientation label, and its support area."""
    from build123d import Pos, Rot

    best: tuple[tuple[float, ...], Any, str, float] | None = None
    for label, rx, ry in _orientations():
        cand = Rot(X=rx, Y=ry) * obj if (rx or ry) else obj
        support, height = _support_metrics(cand)
        bb = cand.bounding_box()
        footprint = (bb.max.X - bb.min.X) * (bb.max.Y - bb.min.Y)
        m = {"s": round(support, 3), "h": round(height, 3), "f": round(footprint, 1)}
        if strategy == "plates":
            key = (m["f"], m["s"], m["h"])
        elif strategy == "fastest":
            key = (m["h"], m["s"], m["f"])
        else:  # material
            key = (m["s"], m["h"], m["f"])
        if best is None or key < best[0]:
            best = (key, cand, label, support)
    assert best is not None
    oriented = best[1]
    bb = oriented.bounding_box()
    # drop onto the bed and center the footprint at its own origin
    oriented = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * oriented
    return oriented, best[2], best[3]


def _build_part(project: str | None, name: str) -> Any:
    """Build one part instance: a project part (`projects.<p>.parts.<name>`) or a
    global library part (`lib.parts.<name>`). Caller holds the run lock and has
    the materialized workspace on sys.path for project parts."""
    if project:
        mod = __import__(f"projects.{project}.parts.{name}", fromlist=[name])
    else:
        mod = __import__(f"lib.parts.{name}", fromlist=[name])
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
) -> dict:
    """items: [{project?: str, name: str, qty: int}] → the arranged plate(s).

    `strategy` picks the orientation objective (see STRATEGIES): least support
    material, smallest footprints (fewest plates), or shortest parts (fastest).
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

            oriented: list[tuple[str, Any, str, float, int]] = []  # (label, solid, orientation, support, qty)
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
                    solid, orientation, support = _orient(base, strategy if strategy in STRATEGIES else "material")
                    oriented.append((f"{project + '/' if project else ''}{name}", solid, orientation, support, qty))

            # one rect per INSTANCE
            rects: list[tuple[int, float, float]] = []
            inst: list[tuple[int, Any, str]] = []  # (rect idx, solid, display name)
            counters: dict[str, int] = {}
            for label, solid, _o, _s, qty in oriented:
                bb = solid.bounding_box()
                w, d = bb.max.X - bb.min.X + PADDING, bb.max.Y - bb.min.Y + PADDING
                for _ in range(qty):
                    counters[label] = counters.get(label, 0) + 1
                    idx = len(rects)
                    rects.append((idx, w, d))
                    inst.append((idx, solid, f"{label}_{counters[label]}".replace("/", "_")))

            placements, plates, fits = _pack_plates(rects, bed)

            # Lay the plates out in a row along X with a visible gap, the whole
            # row centered at the origin. Rotated instances get a free Z-spin
            # (support-neutral) that let them pack tighter.
            gap = 30.0
            n_plates = len(plates)
            row_w = n_plates * bed[0] + (n_plates - 1) * gap
            objs, names, colors, plate_of = [], [], [], []
            for (idx, solid, name), _r in zip(inst, rects, strict=True):
                pi, cx, cy, rotated = placements[idx]
                placed = Rot(Z=90) * solid if rotated else solid
                px = pi * (bed[0] + gap) - row_w / 2  # this plate's left edge
                objs.append(Pos(px + cx, cy - bed[1] / 2, 0) * placed)
                names.append(f"plate{pi + 1}_{name}" if n_plates > 1 else name)
                colors.append(_obj_color(solid))
                plate_of.append(pi)

            shapes, states, bbox = tessellate(objs, names=names, colors=colors)
        finally:
            if added and added in sys.path:
                sys.path.remove(added)
            for mod in [m for m in sys.modules if m == "projects" or m.startswith("projects.")]:
                del sys.modules[mod]
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    stats = [
        {
            "name": label,
            "qty": qty,
            "orientation": orientation,
            "support_area": round(support, 1),
        }
        for label, _s, orientation, support, qty in oriented
    ]
    result: dict[str, Any] = {
        "ok": True,
        "shapes": shapes,
        "states": states,
        "bbox": bbox,
        "stats": stats,
        "fits": fits,
        "plates": [
            {"index": i, "w": round(p.used_w, 1), "d": round(p.used_d, 1), "bed_w": bed[0], "bed_d": bed[1]}
            for i, p in enumerate(plates)
        ],
    }
    if want_objects:
        result["_objects"] = objs
        result["_plate_of"] = plate_of
    return result
