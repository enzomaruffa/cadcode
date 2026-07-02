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


def _orient(obj: Any) -> tuple[Any, str, float]:
    """Pick the orientation minimizing support area (then height). Returns the
    rotated + bed-dropped solid, the orientation label, and the support area."""
    from build123d import Pos, Rot

    best: tuple[float, float, Any, str] | None = None
    for label, rx, ry in _orientations():
        cand = Rot(X=rx, Y=ry) * obj if (rx or ry) else obj
        support, height = _support_metrics(cand)
        key = (round(support, 3), round(height, 3))
        if best is None or key < (best[0], best[1]):
            best = (key[0], key[1], cand, label)
    assert best is not None
    oriented = best[2]
    bb = oriented.bounding_box()
    # drop onto the bed and center the footprint at its own origin
    oriented = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * oriented
    return oriented, best[3], best[0]


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


def _pack(
    rects: list[tuple[int, float, float]], bed: tuple[float, float]
) -> tuple[list[tuple[float, float]], float, float, bool]:
    """Shelf-pack rectangles (idx, w, d) onto the bed. Returns each input rect's
    center offset (x, y), the used plate w/d, and whether everything fit the bed."""
    order = sorted(rects, key=lambda r: (-r[2], -r[1]))  # deepest first
    positions: dict[int, tuple[float, float]] = {}
    bed_w = bed[0]
    x = y = shelf_d = 0.0
    used_w = used_d = 0.0
    fits = True
    for idx, w, d in order:
        if x > 0 and x + w > bed_w:
            # wrap to a new shelf
            y += shelf_d
            x = 0.0
            shelf_d = 0.0
        if w > bed_w:
            fits = False  # part wider than the bed — place anyway on a virtual bed
        positions[idx] = (x + w / 2, y + d / 2)
        x += w
        shelf_d = max(shelf_d, d)
        used_w = max(used_w, x)
        used_d = max(used_d, y + shelf_d)
    if used_d > bed[1]:
        fits = False
    offsets = [positions[i] for i, _w, _d in rects]
    return offsets, used_w, used_d, fits


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
) -> dict:
    """items: [{project?: str, name: str, qty: int}] → the arranged plate.

    Returns {ok, shapes, states, bbox, stats, fits, plate} (and the raw located
    objects under "_objects" when want_objects, for export)."""
    import shutil
    import tempfile
    from pathlib import Path

    from build123d import Pos

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
                    solid, orientation, support = _orient(base)
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

            offsets, used_w, used_d, fits = _pack(rects, bed)

            objs, names, colors = [], [], []
            for (idx, solid, name), _r in zip(inst, rects, strict=True):
                ox, oy = offsets[idx]
                # center the whole plate around the origin for a nice render
                objs.append(Pos(ox - used_w / 2, oy - used_d / 2, 0) * solid)
                names.append(name)
                colors.append(_obj_color(solid))

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
        "plate": {"w": round(used_w, 1), "d": round(used_d, 1), "bed_w": bed[0], "bed_d": bed[1]},
    }
    if want_objects:
        result["_objects"] = objs
    return result
