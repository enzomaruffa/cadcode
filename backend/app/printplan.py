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


# What each strategy optimizes when choosing a part's orientation. Metrics per
# candidate come from the MESH (tessellated once, rotated per candidate — the
# same triangles a slicer sees): support area, estimated minutes, footprint.
#   material — least support waste (then fastest)
#   plates   — smallest footprint so more parts share a bed (then least support)
#   fastest  — least ESTIMATED PRINT TIME (slicer-style layer estimate)
STRATEGIES = ("material", "plates", "fastest")


def _orient(obj: Any, strategy: str = "material") -> tuple[Any, str, float, float]:
    """Pick the best orientation for the strategy — scored on the tessellated
    mesh (rotating vertices is free; no re-tessellation per candidate). Returns
    (rotated + bed-dropped solid, label, support area, estimated minutes)."""
    from build123d import Pos, Rot

    from app.kernel.print_time import estimate_minutes, mesh_of, rotate_mesh, support_area

    verts, tris = mesh_of(obj)
    best: tuple[tuple[float, ...], str, float, float, float, float] | None = None
    for label, rx, ry in _orientations():
        v = rotate_mesh(verts, rx, ry)
        v = v - [0.0, 0.0, float(v[:, 2].min())]  # drop onto the bed
        sup = support_area(v, tris)
        minutes = estimate_minutes(v, tris, support_mm2=sup)["minutes"]
        footprint = float((v[:, 0].max() - v[:, 0].min()) * (v[:, 1].max() - v[:, 1].min()))
        m = {"s": round(sup, 1), "t": minutes, "f": round(footprint, 1)}
        if strategy == "plates":
            key = (m["f"], m["s"], m["t"])
        elif strategy == "fastest":
            key = (m["t"], m["s"], m["f"])
        else:  # material
            key = (m["s"], m["t"], m["f"])
        if best is None or key < best[0]:
            best = (key, label, sup, minutes, rx, ry)
    assert best is not None
    _key, label, sup, minutes, rx, ry = best
    oriented = Rot(X=rx, Y=ry) * obj if (rx or ry) else obj
    bb = oriented.bounding_box()
    # drop onto the bed and center the footprint at its own origin
    oriented = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * oriented
    return oriented, label, sup, minutes


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

            # (label, solid, orientation, support, est minutes, qty)
            oriented: list[tuple[str, Any, str, float, float, int]] = []
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
                    solid, orientation, support, minutes = _orient(
                        base, strategy if strategy in STRATEGIES else "material"
                    )
                    oriented.append(
                        (f"{project + '/' if project else ''}{name}", solid, orientation, support, minutes, qty)
                    )

            # one rect per INSTANCE
            rects: list[tuple[int, float, float]] = []
            inst: list[tuple[int, Any, str, float]] = []  # (rect idx, solid, display name, est minutes)
            counters: dict[str, int] = {}
            for label, solid, _o, _s, minutes, qty in oriented:
                bb = solid.bounding_box()
                w, d = bb.max.X - bb.min.X + PADDING, bb.max.Y - bb.min.Y + PADDING
                for _ in range(qty):
                    counters[label] = counters.get(label, 0) + 1
                    idx = len(rects)
                    rects.append((idx, w, d))
                    inst.append((idx, solid, f"{label}_{counters[label]}".replace("/", "_"), minutes))

            placements, plates, fits = _pack_plates(rects, bed)

            # Lay the plates out in a row along X with a visible gap, the whole
            # row centered at the origin. Rotated instances get a free Z-spin
            # (support-neutral) that let them pack tighter.
            gap = 30.0
            n_plates = len(plates)
            row_w = n_plates * bed[0] + (n_plates - 1) * gap
            objs, names, colors, plate_of = [], [], [], []
            plate_minutes = [0.0] * n_plates
            for (idx, solid, name, minutes), _r in zip(inst, rects, strict=True):
                pi, cx, cy, rotated = placements[idx]
                placed = Rot(Z=90) * solid if rotated else solid
                px = pi * (bed[0] + gap) - row_w / 2  # this plate's left edge
                objs.append(Pos(px + cx, cy - bed[1] / 2, 0) * placed)
                names.append(f"plate{pi + 1}_{name}" if n_plates > 1 else name)
                colors.append(_obj_color(solid))
                plate_of.append(pi)
                plate_minutes[pi] += minutes

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
            "est_min": round(minutes, 1),
        }
        for label, _s, orientation, support, minutes, qty in oriented
    ]
    result: dict[str, Any] = {
        "ok": True,
        "shapes": shapes,
        "states": states,
        "bbox": bbox,
        "stats": stats,
        "fits": fits,
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
    }
    if want_objects:
        result["_objects"] = objs
        result["_plate_of"] = plate_of
    return result
