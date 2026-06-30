"""FastAPI app + WebSocket gateway (plan §1).

One socket per session, JSON envelope protocol. Document state is in-memory per
session; persisted to git on checkpoint (M5). No database in v0.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, protocol as P
from app.default_model import DEFAULT_SOURCE
from app.kernel import SubprocessKernel
from app.session import Session

log = logging.getLogger("cadcode")


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


@app.get("/library")
async def library() -> dict[str, list]:
    """The parts catalog for the palette: signatures, docs, params + an
    isometric wireframe thumbnail per part (plan §5)."""
    from app.library import catalog
    from app.thumbnail import iso_svg

    parts = catalog()
    for entry in parts:
        try:
            from lib import parts as _parts

            fn = getattr(_parts, entry["name"], None)
            entry["thumbnail"] = iso_svg(fn()) if fn else ""
        except Exception:
            entry["thumbnail"] = ""
    return {"parts": parts}


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
