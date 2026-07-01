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


_TOKEN_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*(?:#\s*(.*?))?\s*$")


def _design_path() -> Path:
    import lib.design

    return Path(lib.design.__file__)


def _parse_tokens(text: str) -> list[dict]:
    """Numeric constants (NAME = value  # comment) in a tokens file → list of
    {name, value, comment}. Shared by global lib.design and a project's project.py."""
    tokens = []
    for line in text.splitlines():
        m = _TOKEN_RE.match(line)
        if m:
            tokens.append({"name": m.group(1), "value": float(m.group(2)), "comment": (m.group(3) or "").strip()})
    return tokens


def _rewrite_tokens(text: str, updates: dict) -> tuple[str, bool]:
    """Rewrite numeric-token values in `text` from `updates` (name→value),
    preserving comments and everything else. Returns (new_text, changed)."""
    lines = text.splitlines()
    changed = False
    for i, line in enumerate(lines):
        m = _TOKEN_RE.match(line)
        if not m or m.group(1) not in updates:
            continue
        name, comment = m.group(1), (m.group(3) or "").strip()
        try:
            val = float(updates[name])
        except (TypeError, ValueError):
            continue
        newline = f"{name} = {val}" + (f"  # {comment}" if comment else "")
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
    return await asyncio.to_thread(run_project, project, kind, name, overrides, preview_source)


@app.post("/projects/{project}/agent")
async def project_agent(project: str, payload: dict) -> dict:
    """Run the whole-project multi-file agent for one request. Returns
    {ok, edits: [{path, new_source}], rationale, targets} — a multi-file patch
    the UI reviews as per-file diffs before writing."""
    from app.library import catalog
    from app.project_agent import run_project_agent

    message = str(payload.get("message") or "").strip()
    if not message:
        return {"ok": False, "error": "empty message"}
    try:
        library = catalog()
    except Exception:
        library = []
    return await run_project_agent(
        project,
        message,
        str(payload.get("run_kind") or "scene"),
        str(payload.get("run_name") or ""),
        selection=payload.get("selection"),
        library=library,
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
