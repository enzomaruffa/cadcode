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


@app.get("/design")
async def get_design() -> dict:
    """Shared design tokens (lib/design.py) — name, value, and doc comment — so
    the UI can show what's importable and let you tune them."""
    tokens = []
    try:
        for line in _design_path().read_text().splitlines():
            m = _TOKEN_RE.match(line)
            if m:
                tokens.append({"name": m.group(1), "value": float(m.group(2)), "comment": (m.group(3) or "").strip()})
    except Exception as exc:  # noqa: BLE001
        return {"tokens": [], "error": str(exc)}
    return {"tokens": tokens}


@app.post("/design")
async def set_design(payload: dict) -> dict:
    """Write new design-token values into lib/design.py and recycle the kernel so
    the next run (and every part) re-derives with them."""
    updates = payload.get("updates")
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "error": "no updates"}
    path = _design_path()
    try:
        lines = path.read_text().splitlines()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

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

    if changed:
        path.write_text("\n".join(lines) + "\n")
        kernel = getattr(app.state, "kernel", None)
        reload = getattr(kernel, "reload_design", None)
        if reload is not None:
            await reload()
    return {"ok": True, "changed": changed}


@app.post("/library/save")
async def library_save(payload: dict) -> dict:
    """Save the current model as a reusable, importable library part — afterwards
    `from lib.parts import <name>` and call `<name>(WIDTH=..., ...)`."""
    import asyncio

    from app.library_save import save_part

    source = str(payload.get("source") or "")
    name = str(payload.get("name") or "")
    if not source.strip():
        return {"ok": False, "error": "no source to save"}

    result = await asyncio.to_thread(save_part, name, source)
    if result.get("ok"):
        kernel = getattr(app.state, "kernel", None)
        reload = getattr(kernel, "reload_design", None)
        if reload is not None:
            await reload()  # recycle so the new part is importable on the next run
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
