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


def slice_minutes(objs: list[Any], timeout_s: int = 180) -> dict[str, float] | None:
    """Slice the given solids as ONE plate; returns {minutes, filament_cm3} or
    None (no slicer / slicing failed). Runs PrusaSlicer headless."""
    if not objs or not slicer_available():
        return None
    from build123d import Compound, export_stl

    with tempfile.TemporaryDirectory(prefix="cadslice_") as d:
        stl = Path(d) / "plate.stl"
        gcode = Path(d) / "plate.gcode"
        obj = objs[0] if len(objs) == 1 else Compound(children=objs)
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
            "--support-material",  # match reality: overhangs get support
            "--output",
            str(gcode),
            str(stl),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=True)
        except (subprocess.SubprocessError, OSError):
            return None
        try:
            text = gcode.read_text(errors="ignore")
        except OSError:
            return None
    m = _TIME_RE.search(text)
    if not m:
        return None
    days, hours, mins, secs = (int(g) if g else 0 for g in m.groups())
    minutes = days * 1440 + hours * 60 + mins + secs / 60.0
    fm = _FILAMENT_RE.search(text)
    return {"minutes": round(minutes, 1), "filament_cm3": float(fm.group(1)) if fm else 0.0}
