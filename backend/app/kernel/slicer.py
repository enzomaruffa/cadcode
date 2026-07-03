"""Real-slicer integration: exact print times from headless PrusaSlicer.

The mesh estimator (`print_time.py`) is instant and good for interactive
feedback + orientation search; PrusaSlicer is the authority. When the
`prusa-slicer` binary is present (it's in the Docker image), we export the
plate to STL, slice it with the same profile the estimator assumes, and parse
the `; estimated printing time` / `; filament used` comments from the G-code.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_TIME_RE = re.compile(r";\s*estimated printing time.*=\s*(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")
_FILAMENT_RE = re.compile(r";\s*filament used \[cm3\]\s*=\s*([\d.]+)")


def slicer_available() -> bool:
    return shutil.which("prusa-slicer") is not None


def slice_minutes(
    objs: list[Any],
    bed: tuple[float, float] = (220.0, 220.0),
    timeout_s: int = 180,
    supports: bool = True,
) -> dict[str, Any] | None:
    """Slice the given solids as ONE plate; returns {minutes, filament_cm3,
    supported} or None (no slicer / slicing failed). Runs PrusaSlicer headless.
    `supports=True` lets PrusaSlicer auto-add support material where overhangs
    need it; `False` prints supportless (overhangs at the user's own risk).

    Our plates are centered on the origin, but PrusaSlicer's bed starts at the
    corner (0,0) and defaults to ~200mm — off-bed objects "slice" to an empty
    G-code with exit 0. So: translate the plate into the bed's quadrant and pass
    the real bed shape."""
    if not objs or not slicer_available():
        return None
    from build123d import Compound, Pos, export_stl

    obj = objs[0] if len(objs) == 1 else Compound(children=objs)
    bb = obj.bounding_box()
    obj = Pos(bed[0] / 2 - (bb.min.X + bb.max.X) / 2, bed[1] / 2 - (bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * obj
    bed_shape = f"0x0,{bed[0]:g}x0,{bed[0]:g}x{bed[1]:g},0x{bed[1]:g}"

    with tempfile.TemporaryDirectory(prefix="cadslice_") as d:
        stl = Path(d) / "plate.stl"
        gcode = Path(d) / "plate.gcode"
        export_stl(obj, str(stl))
        cmd = [
            "prusa-slicer",
            "--export-gcode",
            "--layer-height",
            "0.2",
            "--fill-density",
            "15%",
            "--nozzle-diameter",
            "0.4",
            "--filament-diameter",
            "1.75",
            "--bed-shape",
            bed_shape,
            "--output",
            str(gcode),
            str(stl),
        ]
        if supports:
            # auto-add support only where overhangs need it (match reality)
            cmd.insert(-3, "--support-material")
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=True)
        except (subprocess.SubprocessError, OSError):
            return None
        try:
            text = gcode.read_text(errors="ignore")
        except OSError:
            return None  # "sliced" but wrote nothing (e.g. still off-bed)
    m = _TIME_RE.search(text)
    if not m:
        return None
    days, hours, mins, secs = (int(g) if g else 0 for g in m.groups())
    minutes = days * 1440 + hours * 60 + mins + secs / 60.0
    fm = _FILAMENT_RE.search(text)
    supported = supports and ("; support_material = 1" in text or "support material" in text.lower())
    return {
        "minutes": round(minutes, 1),
        "filament_cm3": round(float(fm.group(1)), 2) if fm else 0.0,
        "supported": bool(supported),
    }
