"""WebSocket envelope protocol (plan §8).

One JSON envelope, request/response correlated by ``id``::

    { "type": "...", "id": "uuid", "payload": { } }

Frontend -> backend:  edit · run · select · gesture · chat · accept_patch /
reject_patch · checkpoint · rollback
Backend -> frontend:  geometry · error · agent_message · agent_patch ·
measurement · status

This contract is the seam that lets the frontend and backend be built in
parallel, so it is pinned early and kept stable.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

# --- message type constants -------------------------------------------------

# frontend -> backend
EDIT = "edit"
RUN = "run"
SELECT = "select"
GESTURE = "gesture"
CHAT = "chat"
ACCEPT_PATCH = "accept_patch"
REJECT_PATCH = "reject_patch"
CHECKPOINT = "checkpoint"
ROLLBACK = "rollback"
SET_MODE = "set_mode"
UNDO = "undo"
REDO = "redo"
PREVIEW_DIFF = "preview_diff"

# backend -> frontend
GEOMETRY = "geometry"
ERROR = "error"
AGENT_MESSAGE = "agent_message"
AGENT_PATCH = "agent_patch"
MEASUREMENT = "measurement"
STATUS = "status"
HISTORY = "history"
SOURCE = "source"  # backend pushes a new buffer (rollback / undo / redo)
SIMULATION = "simulation"  # motion-sim frames (per-leaf poses) + summary + specs


class Envelope(BaseModel):
    """The single wire format. ``payload`` stays loosely typed so the protocol
    can evolve without lockstep client/server model bumps; handlers validate the
    fields they need with the payload models below."""

    type: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def make(cls, type: str, payload: dict[str, Any] | None = None, id: str | None = None) -> Envelope:
        env = cls(type=type, payload=payload or {})
        if id is not None:
            env.id = id
        return env


# --- payload models (frontend -> backend) -----------------------------------


class EditPayload(BaseModel):
    """A full-source edit. The Monaco buffer *is* the new source."""

    source: str
    debounced: bool = True


class SelectPayload(BaseModel):
    """A clicked entity in the viewport (plan §7: resolve to a selector)."""

    kind: Literal["face", "edge", "vertex", "solid"]
    # path of the leaf shape that was hit (e.g. "/Group/box") + sub-index
    shape_id: str
    index: int


class ChatPayload(BaseModel):
    message: str


class CheckpointPayload(BaseModel):
    message: str = "checkpoint"


class RollbackPayload(BaseModel):
    # to a buffer-history step, an op index, or a git commit
    to: str | int


# --- payload models (backend -> frontend) ------------------------------------


class GeometryPayload(BaseModel):
    """Tessellated geometry for three-cad-viewer plus the derived op list."""

    shapes: dict[str, Any]
    states: dict[str, list[int]]
    bbox: dict[str, float] | None = None
    ops: list[dict[str, Any]] = Field(default_factory=list)
    specs: list[dict[str, Any]] = Field(default_factory=list)
    params: list[dict[str, Any]] = Field(default_factory=list)
    stdout: str = ""
    # whether this geometry is the last *good* render kept after an error
    stale: bool = False
    # "technical" (normal), "printability" (overhang heatmap), "highlight",
    # "geomdiff", or "physical" (mass/COM readout — carries `physical`, not shapes)
    mode: str = "technical"
    print_stats: dict[str, Any] | None = None
    # physical-properties readout (mass, com, inertia, tip, cost) for mode="physical"
    physical: dict[str, Any] | None = None


class SimulationPayload(BaseModel):
    """Motion-sim result: per-frame rigid poses keyed by leaf id, a run summary
    (worst clearance / collision frames), and the clearance ``require`` specs."""

    frames: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    specs: list[dict[str, Any]] = Field(default_factory=list)


class ErrorPayload(BaseModel):
    message: str
    traceback: str = ""
    line: int | None = None


class StatusPayload(BaseModel):
    state: Literal["idle", "running", "ok", "error"]
    detail: str = ""
