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
    printer: str | None = None,
    filament: str | None = None,
) -> dict[str, Any] | None:
    """Slice the given solids as ONE plate; returns {minutes, filament_cm3,
    supported, slicer, printer?, filament?} or None (no slicer / slicing failed).
    `supports=True` auto-adds support material where overhangs need it; `False`
    prints supportless. `printer`/`filament` are OrcaSlicer preset names (ignored
    by the Prusa fallback). Tries the preferred backend first, falling back to the
    other if it fails (unless CAD_SLICER pins one)."""
    order = _backend_order()
    if not objs or not order:
        return None
    from build123d import export_stl

    obj = _place_on_bed(objs, bed)
    for backend in order:
        with tempfile.TemporaryDirectory(prefix="cadslice_") as d:
            stl = Path(d) / "plate.stl"
            export_stl(obj, str(stl))
            if backend == "orca":
                res = _slice_orca(stl, Path(d), bed, supports, timeout_s, printer, filament)
            else:
                res = _slice_prusa(stl, Path(d), bed, supports, timeout_s)
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


# OrcaSlicer's CLI needs a self-contained (flattened) preset: `--load-settings`
# does NOT resolve a preset's `inherits` chain, and a process/filament is only
# accepted if its `compatible_printers` lists the machine's exact `name`. So for
# any chosen printer we: flatten that machine, find a compatible ~0.2mm process
# (flatten it), flatten the chosen filament, then FORCE the compat fields to the
# machine name — which sidesteps the -17 "printer not compatible" error for any
# printer×process×filament combo. A generic Marlin/PLA triple is the fallback
# when no printer is chosen.
_ORCA_DEFAULT = {
    "printer": "Bambu Lab X1 Carbon 0.4 nozzle",
    "filament": "Generic PLA @System",
}
_ORCA_FALLBACK = {  # used when no printer is picked / a name goes missing
    "machine": "MyMarlin 0.4 nozzle",
    "process": "0.20mm Standard @MyMarlin",
    "filament": "Generic PLA @System",
}
# name → OrcaFilamentLibrary vendor label for the generic (always-offered) filaments
_ORCA_GENERIC_VENDOR = "OrcaFilamentLibrary"

# lazily-built, cached-for-process-lifetime indexes over resources/profiles
_orca_idx: dict[tuple[str, str], dict[str, Any]] | None = None
_orca_vendor: dict[tuple[str, str], str] = {}
_orca_flat_cache: dict[tuple[str, str], dict[str, Any]] = {}
_orca_proc_for: dict[str, str] = {}  # machine name → best ~0.2mm process name


def _orca_resources() -> Path | None:
    """The bundled `resources/profiles` dir (relative to the extracted AppImage)."""
    cands: list[Path] = []
    b = _orca_bin()
    if b:
        root = Path(b).resolve().parent
        cands += [root / "resources" / "profiles", root.parent / "resources" / "profiles"]
    cands.append(Path("/opt/orcaslicer/resources/profiles"))
    return next((c for c in cands if c.is_dir()), None)


def _orca_load_index() -> dict[tuple[str, str], dict[str, Any]]:
    """Index every preset by (type, name); record its vendor (top dir). One-time."""
    global _orca_idx
    if _orca_idx is not None:
        return _orca_idx
    import json

    idx: dict[tuple[str, str], dict[str, Any]] = {}
    profiles = _orca_resources()
    if profiles is not None:
        root = str(profiles)
        for f in profiles.rglob("*.json"):
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and "name" in d and "type" in d:
                key = (d["type"], d["name"])
                idx[key] = d
                rel = str(f)[len(root) + 1 :]
                _orca_vendor[key] = rel.split("/")[0]
    _orca_idx = idx
    return idx


def _orca_flatten(kind: str, name: str) -> dict[str, Any]:
    """Flatten a preset's `inherits` chain into one self-contained dict (cached)."""
    ckey = (kind, name)
    if ckey in _orca_flat_cache:
        return _orca_flat_cache[ckey]
    idx = _orca_load_index()

    def flat(t: str, nm: str, seen: tuple[str, ...] = ()) -> dict[str, Any]:
        d = idx.get((t, nm))
        if d is None or nm in seen:
            return {}
        base = flat(t, d["inherits"], (*seen, nm)) if d.get("inherits") else {}
        base.update({k: v for k, v in d.items() if k != "inherits"})
        return base

    out = flat(kind, name)
    _orca_flat_cache[ckey] = out
    return out


def _orca_bed_of(machine_name: str) -> tuple[float, float] | None:
    """Bed (w, d) mm from a machine preset's flattened `printable_area` rectangle."""
    area = _orca_flatten("machine", machine_name).get("printable_area")
    if not isinstance(area, list) or len(area) < 3:
        return None
    try:
        xs, ys = [], []
        for pt in area:
            x, y = str(pt).split("x")
            xs.append(float(x))
            ys.append(float(y))
        return (max(xs) - min(xs), max(ys) - min(ys))
    except (ValueError, AttributeError):
        return None


def _orca_process_for(machine_name: str) -> str:
    """Best ~0.2mm process compatible with the machine (cached). Falls back to the
    generic Marlin 0.2mm process (compat is forced later regardless)."""
    if not _orca_proc_for:
        idx = _orca_load_index()
        for t, nm in idx:
            if t != "process":
                continue
            fp = _orca_flatten("process", nm)
            if str(fp.get("instantiation", "")).lower() != "true":
                continue
            compat = fp.get("compatible_printers") or []
            if not isinstance(compat, list):
                continue
            try:
                lh = float(fp.get("layer_height", 0) or 0)
            except (ValueError, TypeError):
                lh = 0.0
            # prefer exactly 0.2mm; note the closest otherwise
            score = 0 if abs(lh - 0.2) < 1e-6 else 1
            for m in compat:
                cur = _orca_proc_for.get(m)
                if cur is None or (score == 0 and "0.20mm" not in cur and "0.20mm" in nm):
                    if score == 0 or cur is None:
                        _orca_proc_for[m] = nm
    return _orca_proc_for.get(machine_name, _ORCA_FALLBACK["process"])


def orca_catalog_printers() -> dict[str, Any]:
    """All instantiable printers grouped for the picker: {printers:[{name, vendor,
    bed_w, bed_d, nozzle}], default}. Empty when Orca isn't the active slicer."""
    if "orca" not in _backend_order():
        return {"printers": [], "default": None}
    idx = _orca_load_index()
    out = []
    for (t, nm), d in idx.items():
        if t != "machine" or str(d.get("instantiation", "")).lower() != "true":
            continue
        bed = _orca_bed_of(nm)
        nozzle = _orca_flatten("machine", nm).get("nozzle_diameter")
        noz = nozzle[0] if isinstance(nozzle, list) and nozzle else nozzle
        out.append(
            {
                "name": nm,
                "vendor": _orca_vendor.get((t, nm), "?"),
                "bed_w": round(bed[0], 1) if bed else None,
                "bed_d": round(bed[1], 1) if bed else None,
                "nozzle": noz,
            }
        )
    out.sort(key=lambda p: (p["vendor"], p["name"]))
    default = _ORCA_DEFAULT["printer"] if any(p["name"] == _ORCA_DEFAULT["printer"] for p in out) else None
    return {"printers": out, "default": default}


def orca_catalog_filaments(vendor: str | None = None) -> dict[str, Any]:
    """Filaments for the picker: generics + the printer vendor's own, so the list
    stays small (the full catalog is ~6k). {filaments:[{name, vendor, type}], default}."""
    if "orca" not in _backend_order():
        return {"filaments": [], "default": None}
    idx = _orca_load_index()
    keep = {_ORCA_GENERIC_VENDOR}
    if vendor:
        keep.add(vendor)
    out = []
    for (t, nm), d in idx.items():
        if t != "filament" or str(d.get("instantiation", "")).lower() != "true":
            continue
        v = _orca_vendor.get((t, nm), "?")
        if v not in keep:
            continue
        ft = _orca_flatten("filament", nm).get("filament_type")
        out.append({"name": nm, "vendor": v, "type": (ft[0] if isinstance(ft, list) and ft else ft) or "?"})
    out.sort(key=lambda f: (f["vendor"] != _ORCA_GENERIC_VENDOR, f["vendor"], f["name"]))
    default = _ORCA_DEFAULT["filament"] if any(f["name"] == _ORCA_DEFAULT["filament"] for f in out) else None
    return {"filaments": out, "default": default}


def _slice_orca(
    stl: Path,
    workdir: Path,
    bed: tuple[float, float],
    supports: bool,
    timeout_s: int,
    printer: str | None = None,
    filament: str | None = None,
) -> dict[str, Any] | None:
    """OrcaSlicer backend. Flattens the chosen printer + a compatible ~0.2mm
    process + the chosen filament into self-contained JSON (compat FORCED to the
    machine name so any combo slices), overrides bed/infill/support, slices with
    `--slice 0`, and reads `plate_1.gcode`. Runs under xvfb + software GL because
    OrcaSlicer initializes GTK even in CLI mode. The raw G-code footer uses the
    same `estimated printing time` / `filament used [cm3]` comments as Prusa."""
    import copy
    import json

    if _orca_resources() is None:
        return None
    machine_name = printer if (printer and _orca_load_index().get(("machine", printer))) else _ORCA_FALLBACK["machine"]
    process_name = _orca_process_for(machine_name)
    fil_name = filament if (filament and _orca_load_index().get(("filament", filament))) else _ORCA_FALLBACK["filament"]

    machine = copy.deepcopy(_orca_flatten("machine", machine_name))
    process = copy.deepcopy(_orca_flatten("process", process_name))
    fdict = copy.deepcopy(_orca_flatten("filament", fil_name))
    if not (machine and process and fdict):
        return None
    # FORCE mutual compatibility (bypasses the -17 gate for any combo)
    process["compatible_printers"] = [machine_name]
    process["compatible_printers_condition"] = ""
    fdict["compatible_printers"] = [machine_name]
    fdict["compatible_printers_condition"] = ""
    fdict["compatible_prints"] = []
    fdict["compatible_prints_condition"] = ""

    w, d = f"{bed[0]:g}", f"{bed[1]:g}"
    machine["printable_area"] = ["0x0", f"{w}x0", f"{w}x{d}", f"0x{d}"]
    machine["printable_height"] = machine.get("printable_height", "250")
    process["sparse_infill_density"] = "15%"
    process["enable_support"] = "1" if supports else "0"
    if supports:
        process["support_type"] = "normal(auto)"  # auto support only where needed
        process["support_threshold_angle"] = "45"

    mfile, pfile, ffile = workdir / "machine.json", workdir / "process.json", workdir / "filament.json"
    mfile.write_text(json.dumps(machine))
    pfile.write_text(json.dumps(process))
    ffile.write_text(json.dumps(fdict))
    outdir = workdir / "out"
    outdir.mkdir(exist_ok=True)

    orca = _orca_bin() or "orca-slicer"
    inner = [
        orca,
        "--slice",
        "0",
        "--arrange",
        "1",
        "--load-settings",
        f"{mfile};{pfile}",  # machine FIRST, process SECOND — order matters
        "--load-filaments",
        str(ffile),
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
    found = sorted(outdir.glob("*.gcode"))  # plate_1.gcode, …
    if not found:
        return None
    try:
        text = found[0].read_text(errors="ignore")
    except OSError:
        return None
    minutes = _parse_time(text, _PRUSA_TIME_RE) or _parse_time(text, _ORCA_TIME_RE)
    if minutes is None:
        return None
    fm = _PRUSA_FIL_RE.search(text) or _ORCA_FIL_RE.search(text)
    low = text.lower()
    supported = supports and ("feature: support" in low or "type:support" in low or "support_material" in low)
    return {
        "minutes": round(minutes, 1),
        "filament_cm3": round(float(fm.group(1)), 2) if fm else 0.0,
        "supported": bool(supported),
        "slicer": "orca",
        "printer": machine_name,
        "filament": fil_name,
    }
