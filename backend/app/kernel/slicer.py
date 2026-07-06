"""Real-slicer integration: exact print times from a headless desktop slicer.

The mesh estimator (`print_time.py`) is instant and good for interactive
feedback + orientation search; a real slicer is the authority. We support two
backends and prefer **OrcaSlicer** (Super Enzo's daily driver — the numbers then
match what he'll actually print), falling back to **PrusaSlicer**:

* OrcaSlicer — a Bambu/Prusa descendant; CLI takes JSON profiles + `--slice`.
* PrusaSlicer — Debian-packaged; CLI takes granular `--layer-height` flags.

Selection: `CAD_SLICER` env (`orca` | `prusa` | `auto`, default `auto` = Orca if
present else Prusa). Both export the plate to STL, slice with a matching 0.2mm /
0.4mm-nozzle / 15%-infill / PLA profile, and parse the G-code's time + filament
comments. The public API (`slice_minutes`, `slicer_available`) is backend-neutral
so callers don't care which engine ran.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# --- PrusaSlicer G-code comments ---
#   ; estimated printing time (normal mode) = 1h 22m 18s
#   ; filament used [cm3] = 13.92
_PRUSA_TIME_RE = re.compile(r";\s*estimated printing time.*=\s*(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")
_PRUSA_FIL_RE = re.compile(r";\s*filament used \[cm3\]\s*=\s*([\d.]+)")

# --- OrcaSlicer G-code comments (filled/confirmed against a real slice) ---
#   ; total estimated time: 1h 5m 12s   (also "model printing time: ...")
#   ; total filament used [g] = ...      / filament volume [cm^3]
_ORCA_TIME_RE = re.compile(
    r";\s*(?:total estimated time|model printing time)\s*[:=]\s*"
    r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?",
    re.IGNORECASE,
)
_ORCA_FIL_RE = re.compile(r";\s*(?:total\s+)?filament (?:used|volume)\s*\[cm\^?3\]\s*[:=]\s*([\d.]+)", re.IGNORECASE)


def _orca_bin() -> str | None:
    """Locate OrcaSlicer: PATH, or the extracted-AppImage entrypoint we install
    in the Docker image (`/opt/orcaslicer/...`)."""
    for name in ("orca-slicer", "orcaslicer", "OrcaSlicer"):
        p = shutil.which(name)
        if p:
            return p
    for cand in ("/opt/orcaslicer/orca-slicer", "/opt/orcaslicer/AppRun", "/opt/orcaslicer/OrcaSlicer"):
        if Path(cand).exists():
            return cand
    return None


def _prusa_bin() -> str | None:
    return shutil.which("prusa-slicer")


def _backend_order() -> list[str]:
    """Backends to try, best first. `CAD_SLICER=orca|prusa` forces exactly one;
    `auto` (default) prefers Orca (the daily driver) and falls back to Prusa."""
    pref = (os.environ.get("CAD_SLICER") or "auto").lower()
    orca, prusa = _orca_bin() is not None, _prusa_bin() is not None
    if pref == "orca":
        return ["orca"] if orca else []
    if pref == "prusa":
        return ["prusa"] if prusa else []
    order = []
    if orca:
        order.append("orca")
    if prusa:
        order.append("prusa")
    return order


def slicer_available() -> bool:
    return bool(_backend_order())


def slicer_name() -> str:
    """Backend that would run first — surfaced in the UI ('orca' / 'prusa')."""
    order = _backend_order()
    return order[0] if order else "none"


def _place_on_bed(objs: list[Any], bed: tuple[float, float]) -> Any:
    """Combine the plate and translate it into the bed's +quadrant. Slicers put
    the bed origin at a corner and drop off-bed objects (empty G-code, exit 0),
    so a plate centered on the world origin must be shifted onto the bed first."""
    from build123d import Compound, Pos

    obj = objs[0] if len(objs) == 1 else Compound(children=objs)
    bb = obj.bounding_box()
    return Pos(bed[0] / 2 - (bb.min.X + bb.max.X) / 2, bed[1] / 2 - (bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * obj


def _parse_time(text: str, time_re: re.Pattern[str]) -> float | None:
    m = time_re.search(text)
    if not m:
        return None
    days, hours, mins, secs = (int(g) if g else 0 for g in m.groups())
    return days * 1440 + hours * 60 + mins + secs / 60.0


def slice_minutes(
    objs: list[Any],
    bed: tuple[float, float] = (220.0, 220.0),
    timeout_s: int = 180,
    supports: bool = True,
) -> dict[str, Any] | None:
    """Slice the given solids as ONE plate; returns {minutes, filament_cm3,
    supported, slicer} or None (no slicer / slicing failed). `supports=True`
    auto-adds support material where overhangs need it; `False` prints
    supportless (overhangs at the user's own risk). Tries the preferred backend
    first, falling back to the other if it fails (unless CAD_SLICER pins one)."""
    order = _backend_order()
    if not objs or not order:
        return None
    from build123d import export_stl

    obj = _place_on_bed(objs, bed)
    for backend in order:
        with tempfile.TemporaryDirectory(prefix="cadslice_") as d:
            stl = Path(d) / "plate.stl"
            export_stl(obj, str(stl))
            fn = _slice_orca if backend == "orca" else _slice_prusa
            res = fn(stl, Path(d), bed, supports, timeout_s)
        if res is not None:
            return res
    return None


def _slice_prusa(
    stl: Path, workdir: Path, bed: tuple[float, float], supports: bool, timeout_s: int
) -> dict[str, Any] | None:
    gcode = workdir / "plate.gcode"
    bed_shape = f"0x0,{bed[0]:g}x0,{bed[0]:g}x{bed[1]:g},0x{bed[1]:g}"
    cmd = [
        _prusa_bin() or "prusa-slicer",
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
        cmd.insert(-3, "--support-material")  # auto support only where overhangs need it
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=True)
        text = gcode.read_text(errors="ignore")
    except (subprocess.SubprocessError, OSError):
        return None
    minutes = _parse_time(text, _PRUSA_TIME_RE)
    if minutes is None:
        return None
    fm = _PRUSA_FIL_RE.search(text)
    supported = supports and ("; support_material = 1" in text or "support material" in text.lower())
    return {
        "minutes": round(minutes, 1),
        "filament_cm3": round(float(fm.group(1)), 2) if fm else 0.0,
        "supported": bool(supported),
        "slicer": "prusa",
    }


def _orca_machine(bed: tuple[float, float]) -> dict[str, Any]:
    w, d = f"{bed[0]:g}", f"{bed[1]:g}"
    return {
        "type": "machine",
        "name": "cadcode generic",
        "from": "User",
        "instantiation": "true",
        "printer_technology": "FFF",
        "printable_area": ["0x0", f"{w}x0", f"{w}x{d}", f"0x{d}"],
        "printable_height": "250",
        "nozzle_diameter": ["0.4"],
        "gcode_flavor": "marlin",
        "extruder_clearance_radius": "45",
        "extruder_clearance_height_to_rod": "36",
        "extruder_clearance_height_to_lid": "140",
    }


def _orca_process(supports: bool) -> dict[str, Any]:
    p: dict[str, Any] = {
        "type": "process",
        "name": "cadcode 0.2mm",
        "from": "User",
        "instantiation": "true",
        "layer_height": "0.2",
        "initial_layer_print_height": "0.2",
        "line_width": "0.42",
        "sparse_infill_density": "15%",
        "wall_loops": "2",
        "top_shell_layers": "4",
        "bottom_shell_layers": "3",
        # Avoids the "Relative extruder addressing requires G92 E0" CLI abort.
        "layer_change_gcode": "G92 E0\n",
        "enable_support": "1" if supports else "0",
    }
    if supports:
        # auto normal support only where overhangs steeper than 45° need it
        p["support_type"] = "normal(auto)"
        p["support_threshold_angle"] = "45"
        p["support_on_build_plate_only"] = "0"
    return p


def _orca_filament() -> dict[str, Any]:
    return {
        "type": "filament",
        "name": "cadcode PLA",
        "from": "User",
        "instantiation": "true",
        "filament_type": ["PLA"],
        "filament_diameter": ["1.75"],
        "nozzle_temperature": ["210"],
        "nozzle_temperature_initial_layer": ["210"],
        "hot_plate_temp": ["60"],
        "hot_plate_temp_initial_layer": ["60"],
    }


def _slice_orca(
    stl: Path, workdir: Path, bed: tuple[float, float], supports: bool, timeout_s: int
) -> dict[str, Any] | None:
    """OrcaSlicer backend. CLI is profile-driven (no granular flags): we write a
    flattened machine/process/filament JSON (all values as STRINGS — numeric
    literals fail silently), slice with `--slice 0`, and read `plate_1.gcode`.
    Runs under xvfb + software GL because OrcaSlicer initializes GTK even in CLI
    mode. The raw G-code footer uses the same `estimated printing time` /
    `filament used [cm3]` comments as PrusaSlicer."""
    import json

    machine = workdir / "machine.json"
    process = workdir / "process.json"
    filament = workdir / "filament.json"
    outdir = workdir / "out"
    datadir = workdir / "orca_data"
    outdir.mkdir(exist_ok=True)
    datadir.mkdir(exist_ok=True)
    machine.write_text(json.dumps(_orca_machine(bed)))
    process.write_text(json.dumps(_orca_process(supports)))
    filament.write_text(json.dumps(_orca_filament()))

    orca = _orca_bin() or "orca-slicer"
    inner = [
        orca,
        "--slice",
        "0",
        "--arrange",
        "1",
        "--datadir",
        str(datadir),
        "--load-settings",
        f"{machine};{process}",  # machine FIRST, process SECOND — order matters
        "--load-filaments",
        str(filament),
        "--outputdir",
        str(outdir),
        str(stl),
    ]
    # OrcaSlicer needs a display (GTK) + software GL even headless.
    cmd = inner
    if shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", "--server-args=-screen 0 1024x768x24", *inner]
    env = {**os.environ, "LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe"}
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=True, env=env)
    except (subprocess.SubprocessError, OSError):
        return None
    gcode = outdir / "plate_1.gcode"
    if not gcode.exists():
        # first plate can be named plate_1 or (single-object) plate_0 — grab any
        found = sorted(outdir.glob("*.gcode"))
        if not found:
            return None
        gcode = found[0]
    try:
        text = gcode.read_text(errors="ignore")
    except OSError:
        return None
    minutes = _parse_time(text, _PRUSA_TIME_RE) or _parse_time(text, _ORCA_TIME_RE)
    if minutes is None:
        return None
    fm = _PRUSA_FIL_RE.search(text) or _ORCA_FIL_RE.search(text)
    low = text.lower()
    supported = supports and ("feature: support" in low or "support material" in low or "enable_support = 1" in low)
    return {
        "minutes": round(minutes, 1),
        "filament_cm3": round(float(fm.group(1)), 2) if fm else 0.0,
        "supported": bool(supported),
        "slicer": "orca",
    }
