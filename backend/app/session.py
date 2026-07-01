"""A single editing session: one WebSocket, one document, one kernel.

Routes typed envelopes to handlers and pushes geometry / error / status back.
This is the single path every edit funnels through —
``apply_edit(buffer) -> run -> tessellate -> render`` — so the human, the agent,
and the viewport can never fork reality.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app import protocol as P
from app.document import Document
from app.kernel import InProcessKernel, Kernel, RunResult

log = logging.getLogger("cadcode.session")

Sender = Callable[[P.Envelope], Awaitable[None]]


def _unified_diff(old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="current",
            tofile="proposed",
        )
    )


class Session:
    def __init__(self, source: str, send: Sender, kernel: Kernel | None = None, agent_model: Any | None = None) -> None:
        self.doc = Document(source=source)
        self.kernel = kernel or InProcessKernel()
        self._send = send
        # Agent state.
        self._agent: Any | None = None
        self._agent_model = agent_model  # override for tests (e.g. TestModel)
        self._pending_source: str | None = None
        self.selection: dict | None = None  # last viewport pick, fed to the agent
        self._git: Any | None = None  # lazy GitStore for durable checkpoints

    # --- outbound helpers ---------------------------------------------------

    async def send(self, type: str, payload: dict[str, Any], reply_to: str | None = None) -> None:
        await self._send(P.Envelope.make(type, payload, id=reply_to))

    async def _emit_result(self, result: RunResult, reply_to: str | None, *, stale_on_error: bool = True) -> None:
        if result.ok:
            self.doc.record_good(result)
            from app.params import extract_params

            await self.send(
                P.GEOMETRY,
                P.GeometryPayload(
                    shapes=result.shapes or {},
                    states=result.states or {},
                    bbox=result.bbox,
                    ops=result.ops,
                    specs=result.specs,
                    params=extract_params(self.doc.source),
                    stdout=result.stdout,
                ).model_dump(),
                reply_to,
            )
            await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())
            return

        # error: report it, but keep the last good geometry on screen (plan §6)
        await self.send(
            P.ERROR,
            P.ErrorPayload(
                message=result.error or "error", traceback=result.traceback, line=result.error_line
            ).model_dump(),
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
            await self.send(
                P.STATUS, P.StatusPayload(state="idle", detail=f"unknown message: {env.type}").model_dump(), env.id
            )
            return
        await handler(env)

    async def _send_undo_state(self) -> None:
        # Lightweight can_undo/can_redo push (no git log) so the frontend's
        # undo/redo buttons enable after ordinary edits + accepted patches too.
        await self.send(P.HISTORY, {"can_undo": self.doc.can_undo, "can_redo": self.doc.can_redo})

    async def _on_edit(self, env: P.Envelope) -> None:
        payload = P.EditPayload(**env.payload)
        changed = self.doc.set_source(payload.source)
        await self.run_current(env.id)
        if changed:
            await self._send_undo_state()

    async def _on_run(self, env: P.Envelope) -> None:
        await self.run_current(env.id)

    async def _on_set_mode(self, env: P.Envelope) -> None:
        mode = env.payload.get("mode", "technical")
        if mode == "printability":
            result = await self.kernel.printability(env.payload.get("build_axis", "Z"))
            if "error" in result:
                await self.send(P.STATUS, P.StatusPayload(state="error", detail=result["error"]).model_dump(), env.id)
                return
            await self.send(
                P.GEOMETRY,
                P.GeometryPayload(
                    shapes=result.get("shapes") or {},
                    states=result.get("states") or {},
                    bbox=result.get("bbox"),
                    mode="printability",
                    print_stats=result.get("stats"),
                ).model_dump(),
                env.id,
            )
            await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())
            return

        if mode == "physical":
            result = await self.kernel.physical()
            if "error" in result:
                await self.send(P.STATUS, P.StatusPayload(state="error", detail=result["error"]).model_dump(), env.id)
                return
            # No geometry: physical mode keeps the technical shading on screen and
            # only adds the COM / footprint overlay. shapes stays empty so the
            # frontend never blanks the model.
            await self.send(
                P.GEOMETRY,
                P.GeometryPayload(shapes={}, states={}, mode="physical", physical=result.get("physical")).model_dump(),
                env.id,
            )
            await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())
            return

        if mode == "highlight":
            result = await self.kernel.provenance(self.doc.source)
            if "error" in result:
                await self.send(P.STATUS, P.StatusPayload(state="error", detail=result["error"]).model_dump(), env.id)
                return
            await self.send(
                P.GEOMETRY,
                P.GeometryPayload(
                    shapes=result.get("shapes") or {},
                    states=result.get("states") or {},
                    bbox=result.get("bbox"),
                    mode="highlight",
                ).model_dump(),
                env.id,
            )
            await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())
            return

        await self.run_current(env.id)  # back to the normal render

    async def _on_select(self, env: P.Envelope) -> None:
        sel = P.SelectPayload(**env.payload)
        measurement = await self.kernel.measure_selection(sel.kind, sel.shape_id, sel.index)
        if "error" not in measurement:
            self.selection = measurement  # ground the agent on what's picked
        await self.send(P.MEASUREMENT, measurement, env.id)

    # --- agent ---------------------------------------------------------------

    def _agent_instance(self) -> Any:
        if self._agent is None:
            from app.agent import build_agent

            self._agent = build_agent(self._agent_model)
        return self._agent

    async def _on_chat(self, env: P.Envelope) -> None:
        message = P.ChatPayload(**env.payload).message
        await self.send(P.STATUS, P.StatusPayload(state="running", detail="agent thinking…").model_dump())
        try:
            from app.agent import CadDeps
            from app.library import catalog

            agent = self._agent_instance()
            deps = CadDeps(source=self.doc.source, kernel=self.kernel, selection=self.selection, library=catalog())
            result = await agent.run(message, deps=deps)
            patch = result.output
        except Exception as exc:  # noqa: BLE001 - surface agent/auth errors to the chat
            log.warning("agent run failed: %s", exc)
            await self.send(
                P.AGENT_MESSAGE,
                {"role": "assistant", "text": f"⚠️ Agent error: {exc}", "error": True},
                env.id,
            )
            await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())
            return

        self._pending_source = patch.new_source
        diff = _unified_diff(self.doc.source, patch.new_source)
        await self.send(P.AGENT_MESSAGE, {"role": "assistant", "text": patch.rationale}, env.id)
        await self.send(
            P.AGENT_PATCH,
            {"diff": diff, "rationale": patch.rationale, "targets": patch.targets, "new_source": patch.new_source},
            env.id,
        )
        await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())

    async def _on_accept_patch(self, env: P.Envelope) -> None:
        new_source = env.payload.get("new_source") or self._pending_source
        if not new_source:
            await self.send(P.STATUS, P.StatusPayload(state="idle", detail="no pending patch").model_dump(), env.id)
            return
        self._pending_source = None
        changed = self.doc.set_source(new_source)
        await self.run_current(env.id)
        if changed:
            await self._send_undo_state()

    async def _on_reject_patch(self, env: P.Envelope) -> None:
        self._pending_source = None
        await self.send(P.STATUS, P.StatusPayload(state="ok", detail="patch rejected").model_dump(), env.id)

    async def _on_preview_diff(self, env: P.Envelope) -> None:
        new_source = env.payload.get("new_source") or self._pending_source
        if not new_source:
            return
        result = await self.kernel.geomdiff(self.doc.source, new_source)
        if "error" in result:
            await self.send(P.STATUS, P.StatusPayload(state="error", detail=result["error"]).model_dump(), env.id)
            return
        await self.send(
            P.GEOMETRY,
            P.GeometryPayload(
                shapes=result.get("shapes") or {},
                states=result.get("states") or {},
                bbox=result.get("bbox"),
                mode="geomdiff",
                print_stats=result.get("diff_stats"),
            ).model_dump(),
            env.id,
        )
        await self.send(P.STATUS, P.StatusPayload(state="ok").model_dump())

    # --- git + history -------------------------------------------------------

    def _gitstore(self) -> Any:
        if self._git is None:
            from app.gitstore import GitStore

            self._git = GitStore()
        return self._git

    async def send_history(self) -> None:
        try:
            commits = await asyncio.to_thread(self._gitstore().log)
        except Exception as exc:  # noqa: BLE001
            log.warning("git log failed: %s", exc)
            commits = []
        await self.send(
            P.HISTORY,
            {"commits": commits, "can_undo": self.doc.can_undo, "can_redo": self.doc.can_redo},
        )

    async def _on_checkpoint(self, env: P.Envelope) -> None:
        message = env.payload.get("message") or "checkpoint"
        try:
            entry = await asyncio.to_thread(self._gitstore().checkpoint, self.doc.source, message)
            await self.send(
                P.STATUS, P.StatusPayload(state="ok", detail=f"checkpoint {entry['short']}").model_dump(), env.id
            )
        except Exception as exc:  # noqa: BLE001
            await self.send(
                P.STATUS, P.StatusPayload(state="error", detail=f"checkpoint failed: {exc}").model_dump(), env.id
            )
        await self.send_history()

    async def _on_rollback(self, env: P.Envelope) -> None:
        sha = env.payload.get("to")
        if not sha:
            return
        try:
            source = await asyncio.to_thread(self._gitstore().source_at, str(sha))
        except Exception as exc:  # noqa: BLE001
            await self.send(
                P.STATUS, P.StatusPayload(state="error", detail=f"rollback failed: {exc}").model_dump(), env.id
            )
            return
        self.doc.set_source(source)
        await self.send(P.SOURCE, {"source": self.doc.source})
        await self.run_current(env.id)
        await self.send_history()

    async def _on_undo(self, env: P.Envelope) -> None:
        if self.doc.undo():
            await self.send(P.SOURCE, {"source": self.doc.source})
            await self.run_current(env.id)
        await self.send_history()

    async def _on_redo(self, env: P.Envelope) -> None:
        if self.doc.redo():
            await self.send(P.SOURCE, {"source": self.doc.source})
            await self.run_current(env.id)
        await self.send_history()
