"""FastAPI app + WebSocket gateway (plan §1).

One socket per session, JSON envelope protocol. Document state is in-memory per
session; persisted to git on checkpoint (M5). No database in v0.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app import __version__, protocol as P
from app.default_model import DEFAULT_SOURCE
from app.session import Session

log = logging.getLogger("cadcode")

app = FastAPI(title="cadcode backend", version=__version__)

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


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()

    async def send(env: P.Envelope) -> None:
        await websocket.send_text(env.model_dump_json())

    session = Session(source=DEFAULT_SOURCE, send=send)

    # Push the initial geometry so the viewport isn't empty on connect.
    await session.run_current()

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
    finally:
        await session.kernel.close()
