"""FastAPI app + WebSocket gateway (plan §1).

One socket per session, JSON envelope protocol. Document state is in-memory per
session; persisted to git on checkpoint (M5). No database in v0.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app import protocol as P
from app.default_model import DEFAULT_SOURCE
from app.kernel import SubprocessKernel
from app.session import Session

log = logging.getLogger("cadcode")

# When the built frontend is present (production image), serve it from this app
# so everything is one origin (the frontend then uses same-origin WS).
STATIC_DIR = Path(os.environ.get("CAD_STATIC_DIR", Path(__file__).resolve().parents[1] / "static"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # One sandboxed worker, shared across sessions and warmed at startup so the
    # first edit renders without paying the OpenCASCADE cold-import cost.
    kernel = SubprocessKernel()
    app.state.kernel = kernel
    try:
        yield
    finally:
        await kernel.close()


app = FastAPI(title="cadcode backend", version=__version__, lifespan=lifespan)

# Dev only: Vite serves the frontend on a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/default-source")
async def default_source() -> dict[str, str]:
    """The starter buffer the editor loads with."""
    return {"source": DEFAULT_SOURCE}


@app.post("/export")
async def export(payload: dict) -> Response:
    """Run the given source and export the shown geometry to a CAD/print format
    (step | stl | glb | 3mf | brep). Returns the file as a download."""
    import asyncio
    import tempfile
    from pathlib import Path as _P

    source = payload.get("source", "")
    fmt = str(payload.get("format", "step")).lower()
    name = payload.get("name") or "model"

    specs = {
        "step": ("step", "application/step"),
        "stl": ("stl", "model/stl"),
        "glb": ("glb", "model/gltf-binary"),
        "3mf": ("3mf", "model/3mf"),
        "brep": ("brep", "application/octet-stream"),
    }
    if fmt not in specs:
        return Response(content=f"unsupported format {fmt!r}", status_code=400)
    ext, media = specs[fmt]

    def _go() -> tuple[bytes | None, str]:
        from build123d import Compound, Mesher, export_brep, export_gltf, export_step, export_stl

        from app.kernel.runner import run_objects

        objs, err = run_objects(source, sandbox=True)
        if err:
            return None, err
        if not objs:
            return None, "nothing to export (script shows no geometry)"
        obj = objs[0] if len(objs) == 1 else Compound(children=objs)
        with tempfile.TemporaryDirectory() as d:
            path = _P(d) / f"{name}.{ext}"
            try:
                if fmt == "step":
                    export_step(obj, path)
                elif fmt == "stl":
                    export_stl(obj, str(path))
                elif fmt == "glb":
                    export_gltf(obj, str(path), binary=True)
                elif fmt == "brep":
                    export_brep(obj, str(path))
                elif fmt == "3mf":
                    m = Mesher()
                    m.add_shape(obj)
                    m.write(str(path))
                return path.read_bytes(), ""
            except Exception as exc:  # noqa: BLE001
                return None, f"{type(exc).__name__}: {exc}"

    data, err = await asyncio.to_thread(_go)
    if data is None:
        return Response(content=f"export failed: {err}", status_code=400)
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'},
    )


@app.get("/library/{name}/geometry")
async def library_geometry(name: str) -> dict:
    """Tessellated geometry for one catalog part — drives the rotatable preview
    in the library modal."""
    import asyncio

    def _go() -> dict:
        from app.tessellate import tessellate
        from lib import parts as _parts

        fn = getattr(_parts, name, None)
        if not callable(fn) or name not in getattr(_parts, "__all__", []):
            return {"error": f"unknown part {name!r}"}
        try:
            shapes, states, bbox = tessellate([fn()], names=[name], colors=["#9aa7ff"])
            return {"shapes": shapes, "states": states, "bbox": bbox}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    return await asyncio.to_thread(_go)


@app.get("/library")
async def library() -> dict[str, list]:
    """The parts catalog for the palette: signatures, docs, params. Previews are
    rendered live in the frontend via @cadcode/viewer (see the geometry route)."""
    from app.library import catalog

    return {"parts": catalog()}


# Plain constant: `NAME = value  # comment`
_TOKEN_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*(?:#\s*(.*?))?\s*$")
# Typed slider param: `NAME: Annotated[float, Range(min, max)] = value  # comment`
_TYPED_RE = re.compile(
    r"^([A-Z][A-Z0-9_]*)\s*:\s*Annotated\[[^\]]*Range\("
    r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,[^)]*)?\)[^\]]*\]"
    r"\s*=\s*(-?\d+(?:\.\d+)?)\s*(?:#\s*(.*?))?\s*$"
)
# Just the trailing `= <number>` of an assignment (to rewrite the value in place).
_VAL_RE = re.compile(r"(=\s*)(-?\d+(?:\.\d+)?)(\s*(?:#.*)?)\s*$")


def _design_path() -> Path:
    import lib.design

    return Path(lib.design.__file__)


def _parse_tokens(text: str) -> list[dict]:
    """Numeric params in a tokens file → {name, value, comment[, min, max]}.
    Handles plain `NAME = value` AND typed `NAME: Annotated[float, Range(a, b)] =
    value` (the typed form carries slider bounds). Shared by lib.design + project.py."""
    tokens = []
    for line in text.splitlines():
        mt = _TYPED_RE.match(line)
        if mt:
            tokens.append(
                {
                    "name": mt.group(1),
                    "value": float(mt.group(4)),
                    "comment": (mt.group(5) or "").strip(),
                    "min": float(mt.group(2)),
                    "max": float(mt.group(3)),
                }
            )
            continue
        m = _TOKEN_RE.match(line)
        if m:
            tokens.append({"name": m.group(1), "value": float(m.group(2)), "comment": (m.group(3) or "").strip()})
    return tokens


def _token_name(line: str) -> str | None:
    mt = _TYPED_RE.match(line)
    if mt:
        return mt.group(1)
    m = _TOKEN_RE.match(line)
    return m.group(1) if m else None


def _rewrite_tokens(text: str, updates: dict) -> tuple[str, bool]:
    """Rewrite token values from `updates` (name→value) in place, preserving the
    type annotation, comment, and formatting. Handles plain + typed forms."""
    lines = text.splitlines()
    changed = False
    for i, line in enumerate(lines):
        name = _token_name(line)
        if not name or name not in updates:
            continue
        try:
            val = float(updates[name])
        except (TypeError, ValueError):
            continue
        newline = _VAL_RE.sub(lambda mm, v=val: f"{mm.group(1)}{v}{mm.group(3)}", line)
        if newline != line:
            lines[i] = newline
            changed = True
    return ("\n".join(lines) + "\n", changed)


async def _reload_kernel() -> None:
    kernel = getattr(app.state, "kernel", None)
    reload = getattr(kernel, "reload_design", None)
    if reload is not None:
        await reload()


@app.get("/design")
async def get_design() -> dict:
    """Shared GLOBAL design tokens (lib/design.py) — name, value, doc comment —
    so the UI can show what's importable and let you tune them. For a project's
    own constants, use /projects/{project}/design."""
    try:
        return {"tokens": _parse_tokens(_design_path().read_text())}
    except Exception as exc:  # noqa: BLE001
        return {"tokens": [], "error": str(exc)}


@app.post("/design")
async def set_design(payload: dict) -> dict:
    """Write new global design-token values into lib/design.py and recycle the
    kernel so the next run (and every part) re-derives with them."""
    updates = payload.get("updates")
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "error": "no updates"}
    path = _design_path()
    try:
        text, changed = _rewrite_tokens(path.read_text(), updates)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    if changed:
        path.write_text(text)
        await _reload_kernel()
    return {"ok": True, "changed": changed}


@app.get("/projects/{project}/design")
async def get_project_design(project: str) -> dict:
    """A project's own constants (project.py) as tunable tokens — the
    project-scoped equivalent of /design. Imported everywhere as `from project`."""
    from app.projects import read_file

    r = read_file(project, "project", "")
    return {"project": project, "tokens": _parse_tokens(r.get("source") or "")}


@app.post("/projects/{project}/design")
async def set_project_design(project: str, payload: dict) -> dict:
    """Tune a project's constants (project.py) — rewrites values in place and
    recycles the kernel so parts/scenes re-derive with them."""
    from app.projects import read_file, write_file

    updates = payload.get("updates")
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "error": "no updates"}
    src = read_file(project, "project", "").get("source") or ""
    text, changed = _rewrite_tokens(src, updates)
    if changed:
        write_file(project, "project", "", text)
        await _reload_kernel()
    return {"ok": True, "changed": changed}


@app.post("/library/save")
async def library_save(payload: dict) -> dict:
    """Save the current model as a reusable, importable library part — afterwards
    `from lib.parts import <name>` and call `<name>(WIDTH=..., ...)`."""
    import asyncio

    from app.library_save import save_part

    source = str(payload.get("source") or "")
    name = str(payload.get("name") or "")
    project = payload.get("project")
    project = str(project) if project else None
    if not source.strip():
        return {"ok": False, "error": "no source to save"}

    result = await asyncio.to_thread(save_part, name, source, project)
    if result.get("ok"):
        kernel = getattr(app.state, "kernel", None)
        reload = getattr(kernel, "reload_design", None)
        if reload is not None:
            await reload()  # recycle so the new part is importable on the next run
    return result


# --- projects: folder-first storage (project.py + parts/ + scenes/) ---------


@app.get("/projects")
async def get_projects() -> dict:
    from app.projects import list_projects

    return {"projects": list_projects()}


@app.post("/projects")
async def new_project(payload: dict) -> dict:
    from app.projects import create_project

    return create_project(str(payload.get("name") or ""))


@app.get("/projects/{project}/file")
async def get_project_file(project: str, kind: str = "project", name: str = "") -> dict:
    from app.projects import read_file

    return read_file(project, kind, name)


@app.post("/projects/{project}/file")
async def set_project_file(project: str, payload: dict) -> dict:
    from app.projects import write_file

    result = write_file(
        project, str(payload.get("kind") or "part"), str(payload.get("name") or ""), str(payload.get("source") or "")
    )
    # Autosave passes reload=false: the project runner re-materializes from disk +
    # overrides every run (no stale module cache), so a recycle isn't needed for
    # keystroke-by-keystroke saves — only for explicit saves other code imports.
    if result.get("ok") and payload.get("reload", True):
        kernel = getattr(app.state, "kernel", None)
        reload = getattr(kernel, "reload_design", None)
        if reload is not None:
            await reload()  # so imports of the edited project file pick up changes
    return result


@app.get("/projects/{project}/files")
async def get_project_files(project: str) -> dict:
    """Every source file in the project as {relpath: source} — the file view +
    the diff base for the multi-file agent."""
    from app.projects import project_files

    return {"project": project, "files": project_files(project)}


@app.post("/projects/{project}/run")
async def run_project_target(project: str, payload: dict) -> dict:
    """Run one project file (a scene assembling parts, or a single part) with the
    project importable, applying any uncommitted `overrides` ({relpath: source}).
    Returns the RunResult dict (ok/error/shapes/bbox/specs) for rendering."""
    import asyncio

    from app.project_runner import run_project

    kind = str(payload.get("kind") or "scene")
    name = str(payload.get("name") or "")
    overrides = payload.get("overrides") or None
    if overrides is not None and not isinstance(overrides, dict):
        overrides = None
    preview = payload.get("source")
    preview_source = str(preview) if isinstance(preview, str) else None
    result = await asyncio.to_thread(run_project, project, kind, name, overrides, preview_source)
    # Slider params for the file being edited (the run target), so the ParamsPanel
    # works for project scenes/files just like the scratch buffer does.
    from app.params import extract_params
    from app.projects import read_file

    rel = "project.py" if kind == "project" else f"{kind}s/{name}.py"
    src = (overrides or {}).get(rel)
    if src is None:
        src = read_file(project, kind, name).get("source") or ""
    try:
        result["params"] = extract_params(src)
    except Exception:
        result["params"] = []
    return result


# The project agent (LLM + self-correct loop) can run for minutes — far past any
# proxy timeout — so it runs as a background JOB the client polls. In-memory is
# fine: single-process backend, jobs are ephemeral review artifacts.
_AGENT_JOBS: dict[str, dict] = {}
_AGENT_JOBS_MAX = 20


async def _run_agent_job(job_id: str, project: str, payload: dict) -> None:
    from app.library import catalog
    from app.project_agent import run_project_agent

    try:
        try:
            library = catalog()
        except Exception:
            library = []
        result = await run_project_agent(
            project,
            str(payload.get("message") or "").strip(),
            str(payload.get("run_kind") or "scene"),
            str(payload.get("run_name") or ""),
            selection=payload.get("selection"),
            library=library,
        )
        _AGENT_JOBS[job_id] = {"status": "done", "result": result}
    except Exception as exc:  # noqa: BLE001 - a job must always resolve
        _AGENT_JOBS[job_id] = {"status": "done", "result": {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}


@app.post("/projects/{project}/agent")
async def project_agent(project: str, payload: dict) -> dict:
    """START the whole-project multi-file agent as a background job. Returns
    {job_id}; poll GET /projects/{project}/agent/{job_id} for the result — the
    agent iterates (dry-run/self-correct) and can outlive any gateway timeout."""
    import asyncio
    import uuid

    message = str(payload.get("message") or "").strip()
    if not message:
        return {"ok": False, "error": "empty message"}
    # Drop the oldest finished jobs so the map can't grow unbounded.
    while len(_AGENT_JOBS) >= _AGENT_JOBS_MAX:
        done = next((k for k, v in _AGENT_JOBS.items() if v.get("status") == "done"), None)
        if done is None:
            break
        _AGENT_JOBS.pop(done, None)
    job_id = uuid.uuid4().hex[:12]
    _AGENT_JOBS[job_id] = {"status": "running"}
    task = asyncio.create_task(_run_agent_job(job_id, project, payload))
    _AGENT_JOBS[job_id]["task"] = task  # keep a reference so it isn't GC'd
    return {"ok": True, "job_id": job_id}


@app.get("/projects/{project}/agent/{job_id}")
async def project_agent_poll(project: str, job_id: str) -> dict:
    """Poll an agent job: {status: running} or {status: done, result: {...}}."""
    job = _AGENT_JOBS.get(job_id)
    if job is None:
        return {"status": "gone", "result": {"ok": False, "error": "job not found (server restarted?) — ask again"}}
    if job.get("status") == "done":
        return {"status": "done", "result": job.get("result")}
    return {"status": "running"}


def _print_args(payload: dict) -> tuple[list[dict], tuple[float, float], str, bool]:
    items = [i for i in (payload.get("items") or []) if isinstance(i, dict)]
    bed_in = payload.get("bed") or {}
    try:
        bed = (float(bed_in.get("w") or 220), float(bed_in.get("d") or 220))
    except (TypeError, ValueError):
        bed = (220.0, 220.0)
    strategy = str(payload.get("strategy") or "material")
    supports = payload.get("supports")
    supports = True if supports is None else bool(supports)  # default: auto-support on
    return items, bed, strategy, supports


def _slice_settings(payload: dict) -> dict:
    """The print settings relayed to the slicer (see slicer._norm_settings)."""
    return {
        "supports": payload.get("supports", True),
        "printer": payload.get("printer") or None,
        "filament": payload.get("filament") or None,
        "layer_height": payload.get("layer_height"),
        "infill": payload.get("infill"),
        "support_style": payload.get("support_style"),
        "adhesion": payload.get("adhesion"),
    }


@app.get("/print/printers")
async def print_printers() -> dict:
    """Supported printers for the picker (OrcaSlicer catalog), grouped client-side
    by vendor; picking one prefills the bed. Empty when Orca isn't the slicer."""
    import asyncio

    from app.kernel.slicer import orca_catalog_printers

    return await asyncio.to_thread(orca_catalog_printers)


@app.get("/print/filaments")
async def print_filaments(printer: str = "", q: str = "") -> dict:
    """Filaments for the picker: generics + those compatible with the chosen
    printer (the full catalog is ~6k, so we scope it). `q` searches ALL brands
    (compat is forced at slice time). Empty w/o Orca."""
    import asyncio

    from app.kernel.slicer import orca_catalog_filaments

    return await asyncio.to_thread(orca_catalog_filaments, printer or None, q or None)


@app.post("/print/profile")
async def print_profile(payload: dict) -> dict:
    """Upload a custom OrcaSlicer preset (exported from the GUI) so it appears in
    the pickers: {kind: filament|machine|process, profile: {...}}."""
    import asyncio

    from app.kernel.slicer import orca_save_profile

    kind = str(payload.get("kind") or "filament")
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return {"ok": False, "error": "expected a 'profile' JSON object"}
    return await asyncio.to_thread(orca_save_profile, kind, profile)


@app.get("/print/parts")
async def print_parts(project: str = "") -> dict:
    """Printable-part candidates for the picker — scoped to a project (its own
    parts + parts it imports) when given, else everything."""
    from app.printplan import print_candidates

    return {"parts": print_candidates(project or None)}


@app.get("/print/calibration")
async def print_calibration() -> dict:
    """How well-calibrated the instant estimator is (samples learned from real
    slicer runs) — so the UI can show 'calibrated from N prints'."""
    from app.kernel.calibration import status

    return status()


@app.post("/print/plan")
async def print_plan(payload: dict) -> dict:
    """Arrange parts for printing: {items: [{project?, name, qty}], bed: {w, d}}.
    Each part is auto-oriented to minimize support, instances are packed on the
    bed, and the plate comes back as renderable geometry + per-part stats."""
    import asyncio

    from app.printplan import plan_print

    items, bed, strategy, supports = _print_args(payload)
    return await asyncio.to_thread(plan_print, items, bed, False, strategy, supports)


@app.post("/print/slice")
async def print_slice(payload: dict) -> dict:
    """EXACT print time via headless PrusaSlicer (when installed): re-plan
    deterministically, slice the requested plate (or everything), and parse the
    slicer's own estimate. Slower than /print/plan — seconds, not instant."""
    import asyncio

    from app.kernel.slicer import slice_minutes, slicer_available
    from app.printplan import plan_print

    if not slicer_available():
        return {"ok": False, "error": "no slicer is installed on this server"}
    items, bed, strategy, supports = _print_args(payload)
    settings = _slice_settings(payload)
    plate_no = payload.get("plate")

    def _go() -> dict:
        plan = plan_print(items, bed, want_objects=True, strategy=strategy, supports=supports)
        if not plan.get("ok"):
            return {"ok": False, "error": plan.get("error") or "plan failed"}
        objs = plan.get("_objects") or []
        if plate_no is not None:
            plate_of = plan.get("_plate_of") or []
            objs = [o for o, p in zip(objs, plate_of, strict=False) if p == int(plate_no)]
        result = slice_minutes(objs, bed, settings=settings)
        if result is None:
            return {"ok": False, "error": "slicing failed"}
        # Teach the instant estimator: pair this plate's features with the
        # slicer's true minutes and refit the calibration coefficients. Only
        # supported runs feed calibration (the estimator's default weights
        # assume support is on, matching how these features were fit).
        if supports:
            try:
                from app.kernel.calibration import record
                from app.kernel.print_time import mesh_of, plate_features

                feats = plate_features([mesh_of(o) for o in objs])
                record(feats, result["minutes"])
            except Exception:
                pass  # calibration is best-effort
        return {"ok": True, "plate": plate_no, **result}

    return await asyncio.to_thread(_go)


@app.post("/print/gcode")
async def print_gcode(payload: dict) -> Response:
    """Slice the plate (with the chosen printer/filament/quality) and return the
    ready-to-print G-code file. Slower — runs the real slicer."""
    import asyncio

    from app.kernel.slicer import slice_gcode, slicer_available
    from app.printplan import plan_print

    if not slicer_available():
        return Response(content="no slicer installed", status_code=400)
    items, bed, strategy, supports = _print_args(payload)
    settings = _slice_settings(payload)
    plate_no = payload.get("plate")

    def _go() -> tuple[bytes | None, str]:
        plan = plan_print(items, bed, want_objects=True, strategy=strategy, supports=supports)
        if not plan.get("ok"):
            return None, str(plan.get("error") or "plan failed")
        objs = plan.get("_objects") or []
        if plate_no is not None:
            plate_of = plan.get("_plate_of") or []
            objs = [o for o, p in zip(objs, plate_of, strict=False) if p == int(plate_no)]
        data = slice_gcode(objs, bed, settings=settings)
        return (data, "" if data else "slicing failed")

    data, err = await asyncio.to_thread(_go)
    if data is None:
        return Response(content=err, status_code=400)
    name = f"plate{int(plate_no) + 1}.gcode" if plate_no is not None else "plate.gcode"
    return Response(
        content=data, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@app.post("/print/export")
async def print_export(payload: dict) -> Response:
    """Re-plan the plate (deterministic for the same inputs) and export it as one
    file for the slicer: stl | 3mf | step."""
    import asyncio
    import tempfile
    from pathlib import Path as _P

    from app.printplan import plan_print

    fmt = str(payload.get("format") or "stl").lower()
    media = {"stl": "model/stl", "3mf": "model/3mf", "step": "application/step"}.get(fmt)
    if media is None:
        return Response(content=f"unsupported format {fmt!r}", status_code=400)
    items, bed, strategy, supports = _print_args(payload)
    plate_no = payload.get("plate")  # optional: export ONE plate of a multi-plate plan

    def _go() -> tuple[bytes | None, str]:
        from build123d import Compound, Mesher, export_step, export_stl

        plan = plan_print(items, bed, want_objects=True, strategy=strategy, supports=supports)
        if not plan.get("ok"):
            return None, str(plan.get("error") or "plan failed")
        objs = plan.get("_objects") or []
        if plate_no is not None:
            plate_of = plan.get("_plate_of") or []
            objs = [o for o, p in zip(objs, plate_of, strict=False) if p == int(plate_no)]
            if not objs:
                return None, f"plate {plate_no} is empty"
        obj = objs[0] if len(objs) == 1 else Compound(children=objs)
        with tempfile.TemporaryDirectory() as d:
            path = _P(d) / f"plate.{fmt}"
            try:
                if fmt == "stl":
                    export_stl(obj, str(path))
                elif fmt == "step":
                    export_step(obj, path)
                else:
                    m = Mesher()
                    m.add_shape(obj)
                    m.write(str(path))
                return path.read_bytes(), ""
            except Exception as exc:  # noqa: BLE001
                return None, f"{type(exc).__name__}: {exc}"

    data, err = await asyncio.to_thread(_go)
    if data is None:
        return Response(content=f"export failed: {err}", status_code=400)
    fname = f"plate{int(plate_no) + 1}.{fmt}" if plate_no is not None else f"plate.{fmt}"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/projects/{project}/apply")
async def apply_project_patch(project: str, payload: dict) -> dict:
    """Write an accepted multi-file patch — a list of {path, new_source} edits —
    to the project atomically-ish, then recycle the kernel so imports refresh."""
    from app.projects import write_project_files

    raw = payload.get("edits") or []
    edits: dict[str, str] = {}
    for e in raw:
        if isinstance(e, dict) and e.get("path"):
            edits[str(e["path"])] = str(e.get("new_source") or "")
    if not edits:
        return {"ok": False, "error": "no edits"}
    result = write_project_files(project, edits)
    if result.get("ok"):
        kernel = getattr(app.state, "kernel", None)
        reload = getattr(kernel, "reload_design", None)
        if reload is not None:
            await reload()
    return result


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()

    async def send(env: P.Envelope) -> None:
        await websocket.send_text(env.model_dump_json())

    # Share the warmed, sandboxed kernel across sessions.
    session = Session(source=DEFAULT_SOURCE, send=send, kernel=websocket.app.state.kernel)

    # Push the initial geometry so the viewport isn't empty on connect.
    await session.run_current()
    await session.send_history()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                env = P.Envelope(**json.loads(raw))
            except Exception as exc:  # noqa: BLE001
                await send(P.Envelope.make(P.STATUS, {"state": "error", "detail": f"bad envelope: {exc}"}))
                continue
            await session.handle(env)
    except WebSocketDisconnect:
        log.info("client disconnected")


# Serve the built SPA last so the API/WS routes above take precedence. html=True
# makes "/" return index.html (and unknown paths fall back to it for the SPA).
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="spa")
    log.info("serving static frontend from %s", STATIC_DIR)
