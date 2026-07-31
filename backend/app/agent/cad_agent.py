"""The CAD agent (plan §4).

A server-side PydanticAI agent that edits the build123d source. It fits this
project on three axes: typed dependency injection (the live session), validated
structured output (a ``Patch`` is a guaranteed shape, not parsed prose), and the
self-correct loop — the agent runs its own candidate in the sandbox and fixes it
*before you see anything*.

We deviate from the plan's literal "unified diff" on purpose: LLMs emit reliable
*full source* far more often than correct diff hunks, and CAD scripts are small.
So ``Patch`` carries the complete new source; the backend derives the unified
diff for the review UI. The self-correct guarantee comes from an output
validator that actually runs the patch and raises ``ModelRetry`` on failure.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext


def _require_calls(source: str) -> list[str]:
    """The text of every ``require(...)`` call in the source. Used to detect an
    agent weakening or deleting a spec to make it 'pass' (cheating)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "require":
            try:
                calls.append(ast.unparse(node))
            except Exception:
                pass
    return calls


# GPT-5.6 Terra by default (needs OPENAI_API_KEY at run time).
# Override with the CAD_AGENT_MODEL env var (any pydantic-ai model id).
DEFAULT_MODEL = os.environ.get("CAD_AGENT_MODEL", "openai:gpt-5.6-terra")

INSTRUCTIONS = """\
You edit a single build123d Python script — the one source of truth for a 3D CAD model.

Rules:
- Always return the COMPLETE updated script in `new_source` (not a diff, not a fragment). It must run top-to-bottom.
- STRONGLY PREFER build123d **algebra mode** over builder mode — the user finds it easier to read. Build objects as expressions and combine them with operators:
    - create: `part = Box(80, 50, 12)`, `Cylinder(radius, height)`, `Sphere(r)`, `Cone(r1, r2, h)` (all centered at the origin by default).
    - combine: union `part + other`, cut `part - other` (or `part -= other`), intersect `part & other`.
    - place: `Pos(x, y, z) * obj` to translate, `Rot(z=90) * obj` to rotate, `plane * obj` to locate on a plane. Repeat with a loop or `[Pos(x, y) * Cylinder(...) for x in (-30, 30) for y in (-15, 15)]` and sum them.
    - modify: `part = fillet(part.edges().filter_by(Axis.Z), radius=2)`, `part = chamfer(part.faces().sort_by(Axis.Z)[-1].edges(), length=1)`, `extrude(sketch, amount=5)` — these work in algebra mode too.
  Do NOT use `with BuildPart()` / builder mode unless the user's existing script already uses it and asks for a minimal change.
- NEVER use `from build123d import *`. Use explicit named imports (e.g. `from build123d import Box, Cylinder, Pos, Rot, fillet, chamfer, Axis, Plane`); add names to the existing import line as you need them.
- Reuse shared design tokens from `lib.design` (`from lib.design import WALL, FILLET, CLEARANCE, M3_CLEARANCE_D, ...`) instead of hard-coding numbers. Keep any token constants the user already defined.
- Preserve typed slider ranges on parameters: `WIDTH: Annotated[float, Range(20, 160)] = 80` (from `typing.Annotated` + `from lib.params import Range`). Keep/extend them when you touch a parameter; prefer this typed form over magic comments.
- Compose with the parts catalog when it fits the request — call `list_library_parts` to see available parts (signatures, params, joints) and `from lib.parts import <name>` to use them, snapping joints with `connect_to` rather than guessing coordinates.
- Make the smallest change that satisfies the request. Don't rewrite unrelated code.
- Prefer robust build123d selectors (e.g. `faces().sort_by(Axis.Z)[-1]`, `edges().filter_by(Axis.Z)`) over fragile indices.
- The script must call `show(part, name=..., color=...)` (or `show_object`) so the result renders. Preserve existing show() calls. When the user cares about mass / cost / balance, pass `material=PLA` (or PETG/ABS/RESIN from `lib.design`) to `show(...)` so the physical readout is right.
- Moving mechanisms: give the parts build123d joints (`RevoluteJoint`/`LinearJoint`), `show(...)` each moving body under its OWN name, and define `def motion(t): ...` (t in 0..1) that drives the joints and returns `{name: <body>.location}` per moving body. A `require(min_clearance_through_motion >= CLEARANCE, ...)` line then becomes an executable motion check — call `simulate(new_source)` to confirm it swings without collision before finalizing.
- Before finalizing, call `dry_run(new_source)` to confirm it executes and the geometry is sane. If it errors, fix it and try again.
- If the user refers to "this" / "the selected ...", call `get_selection` — it returns the picked entity and a selector you should reuse.

Put a one-or-two sentence explanation in `rationale`, and list the selectors/areas you touched in `targets`.
"""


@dataclass
class CadDeps:
    """Injected per run — the live session (plan §4)."""

    source: str
    kernel: Any  # KernelWorker: run / measure in the sandbox
    selection: dict | None = None  # current viewport pick (a measurement payload)
    library: list[dict] = field(default_factory=list)  # catalog parts (M4)


class Patch(BaseModel):
    """The validated edit the agent must return (or it retries)."""

    new_source: str = Field(description="the COMPLETE updated build123d script after your edit")
    rationale: str = Field(description="one or two sentences explaining the change")
    targets: list[str] = Field(
        default_factory=list, description="selectors / areas touched, e.g. ['faces().sort_by(Axis.Z)[-1]']"
    )


def build_agent(model: Any | None = None) -> Agent[CadDeps, Patch]:
    # output_type=Patch makes this an Agent[CadDeps, Patch] at runtime, but the
    # type checker can't tie the kwarg to the generic — annotate it explicitly.
    agent = Agent[CadDeps, Patch](
        model or DEFAULT_MODEL,
        deps_type=CadDeps,
        output_type=Patch,
        instructions=INSTRUCTIONS,
        retries=3,
    )

    @agent.tool
    async def read_source(ctx: RunContext[CadDeps]) -> str:
        """Return the current build123d source."""
        return ctx.deps.source

    @agent.tool
    async def dry_run(ctx: RunContext[CadDeps], source: str) -> dict:
        """Run a candidate script in the sandbox. Returns {ok, error, line, bbox, parts}.
        Use this to verify your edit before returning it."""
        result = await ctx.deps.kernel.run(source)
        if result.ok:
            return {
                "ok": True,
                "bbox": result.bbox,
                "parts": list((result.states or {}).keys()),
                "stdout": result.stdout,
            }
        return {"ok": False, "error": result.error, "line": result.error_line}

    @agent.tool
    async def simulate(ctx: RunContext[CadDeps], source: str) -> dict:
        """Sweep a candidate script's `motion(t)` and report the worst-case
        clearance + any collision. Returns {ok, worst_clearance, collision_frames,
        specs} — use it to check a mechanism swings without gouging itself before
        finalizing. Errors if the script defines no `motion(t)`."""
        result = await ctx.deps.kernel.simulate(source)
        if "error" in result:
            return {"ok": False, "error": result["error"]}
        summary = result.get("summary") or {}
        return {
            "ok": True,
            "worst_clearance": summary.get("min_clearance_through_motion"),
            "collision_frames": summary.get("collision_frames"),
            "specs": result.get("specs"),
        }

    @agent.tool
    async def get_selection(ctx: RunContext[CadDeps]) -> dict | None:
        """The entity currently picked in the viewport, with a synthesized selector."""
        return ctx.deps.selection

    @agent.tool
    async def list_library_parts(ctx: RunContext[CadDeps]) -> list[dict]:
        """The reusable parts catalog (signatures, params, joints). Empty until M4."""
        return ctx.deps.library

    @agent.output_validator
    async def validate_patch(ctx: RunContext[CadDeps], patch: Patch) -> Patch:
        """Self-correct guarantee: the patch must run *and* satisfy every
        ``require(...)`` spec in the script (CAD-as-TDD, plan §4 step 3, §7)."""
        if patch.new_source.strip() == ctx.deps.source.strip():
            raise ModelRetry("new_source is unchanged from the current source — make the requested edit.")
        result = await ctx.deps.kernel.run(patch.new_source)
        if not result.ok:
            where = f" (line {result.error_line})" if result.error_line else ""
            raise ModelRetry(
                f"Your edit fails to run: {result.error}{where}. Fix new_source and return a working script."
            )

        # Spec integrity: the agent may ADD require()s but must not change or
        # delete the existing ones (no cheating by weakening the spec).
        original = _require_calls(ctx.deps.source)
        kept = _require_calls(patch.new_source)
        missing = [r for r in original if r not in kept]
        if missing:
            raise ModelRetry(
                "You changed or removed existing require(...) specs: "
                + "; ".join(missing)
                + ". Keep every original require() call verbatim and satisfy it by changing the geometry instead."
            )

        failed = [s.get("message", "requirement") for s in (result.specs or []) if not s.get("passed")]
        if failed:
            raise ModelRetry(
                "Your edit runs but violates these require(...) specs: "
                + "; ".join(failed)
                + ". Adjust the geometry so every spec passes — do not weaken the specs."
            )
        return patch

    return agent
