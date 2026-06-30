"""A single editing session: one WebSocket, one document, one kernel.

Routes typed envelopes to handlers and pushes geometry / error / status back.
This is the single path every edit funnels through —
``apply_edit(buffer) -> run -> tessellate -> render`` — so the human, the agent,
and the viewport can never fork reality.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app import protocol as P
from app.document import Document
from app.kernel import InProcessKernel, Kernel, RunResult

Sender = Callable[[P.Envelope], Awaitable[None]]


class Session:
    def __init__(self, source: str, send: Sender, kernel: Kernel | None = None) -> None:
        self.doc = Document(source=source)
        self.kernel = kernel or InProcessKernel()
        self._send = send

    # --- outbound helpers ---------------------------------------------------

    async def send(self, type: str, payload: dict[str, Any], reply_to: str | None = None) -> None:
        await self._send(P.Envelope.make(type, payload, id=reply_to))

    async def _emit_result(self, result: RunResult, reply_to: str | None, *, stale_on_error: bool = True) -> None:
        if result.ok:
            self.doc.record_good(result)
            await self.send(
                P.GEOMETRY,
                P.GeometryPayload(
                    shapes=result.shapes or {},
                    states=result.states or {},
                    bbox=result.bbox,
                    ops=result.ops,
                    stdout=result.stdout,
                ).model_dump(),
                reply_to,
            )
            await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())
            return

        # error: report it, but keep the last good geometry on screen (plan §6)
        await self.send(
            P.ERROR,
            P.ErrorPayload(message=result.error or "error", traceback=result.traceback, line=result.error_line).model_dump(),
            reply_to,
        )
        if stale_on_error and self.doc.last_good is not None:
            lg = self.doc.last_good
            await self.send(
                P.GEOMETRY,
                P.GeometryPayload(
                    shapes=lg.shapes or {},
                    states=lg.states or {},
                    bbox=lg.bbox,
                    ops=lg.ops,
                    stdout=lg.stdout,
                    stale=True,
                ).model_dump(),
            )
        await self.send(P.STATUS, P.StatusPayload(state="error", detail=result.error or "").model_dump())

    # --- run ----------------------------------------------------------------

    async def run_current(self, reply_to: str | None = None) -> RunResult:
        await self.send(P.STATUS, P.StatusPayload(state="running").model_dump())
        result = await self.kernel.run(self.doc.source)
        await self._emit_result(result, reply_to)
        return result

    # --- inbound dispatch ---------------------------------------------------

    async def handle(self, env: P.Envelope) -> None:
        handler = getattr(self, f"_on_{env.type}", None)
        if handler is None:
            await self.send(P.STATUS, P.StatusPayload(state="idle", detail=f"unknown message: {env.type}").model_dump(), env.id)
            return
        await handler(env)

    async def _on_edit(self, env: P.Envelope) -> None:
        payload = P.EditPayload(**env.payload)
        self.doc.set_source(payload.source)
        await self.run_current(env.id)

    async def _on_run(self, env: P.Envelope) -> None:
        await self.run_current(env.id)
