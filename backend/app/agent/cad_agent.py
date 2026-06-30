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

# Gemini 3.5 Flash by default (needs GEMINI_API_KEY / GOOGLE_API_KEY at run time).
# Override with the CAD_AGENT_MODEL env var (any pydantic-ai model id).
DEFAULT_MODEL = os.environ.get("CAD_AGENT_MODEL", "google:gemini-3.5-flash")

INSTRUCTIONS = """\
You edit a single build123d Python script — the one source of truth for a 3D CAD model.

Rules:
- Always return the COMPLETE updated script in `new_source` (not a diff, not a fragment). It must run top-to-bottom.
- Keep the user's structure, comments, and any design-token constants (e.g. WALL, FILLET, CLEARANCE). Reuse those tokens instead of hard-coding numbers.
- Make the smallest change that satisfies the request. Don't rewrite unrelated code.
- Prefer robust build123d selectors (e.g. `faces().sort_by(Axis.Z)[-1]`, `edges().filter_by(Axis.Z)`) over fragile indices.
- The script must call `show(part, name=..., color=...)` (or `show_object`) so the result renders. Preserve existing show() calls.
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
    targets: list[str] = Field(default_factory=list, description="selectors / areas touched, e.g. ['faces().sort_by(Axis.Z)[-1]']")


def build_agent(model: Any | None = None) -> Agent[CadDeps, Patch]:
    agent: Agent[CadDeps, Patch] = Agent(
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
            return {"ok": True, "bbox": result.bbox, "parts": list((result.states or {}).keys()), "stdout": result.stdout}
        return {"ok": False, "error": result.error, "line": result.error_line}

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
            raise ModelRetry(f"Your edit fails to run: {result.error}{where}. Fix new_source and return a working script.")

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
