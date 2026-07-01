"""The whole-project multi-file agent (PROJECTS_PLAN.md → "Multi-file agent").

Where ``agent/cad_agent.py`` edits a single buffer, this agent edits a whole
**project** — ``project.py`` + ``parts/*`` + ``scenes/*`` — and a single change
may cascade across several files (fix a part, update the parts that build on it,
touch the scene that assembles them). Its output is therefore a *multi-file*
patch, and it runs an explicit **edit → run → view → edit** loop: it reads the
whole project, dry-runs candidate edits against the run target (with the edits
applied on top, via ``project_runner``), inspects the geometry/errors, and
refines until it's happy — then returns the validated set of file edits.

Same three properties as the single-file agent: typed deps, validated structured
output, and a self-correct output validator that actually runs the run target.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext

from app.agent.cad_agent import DEFAULT_MODEL, _require_calls
from app.project_runner import run_project

INSTRUCTIONS = """\
You edit a whole build123d **project** — the one source of truth for a 3D CAD project. A project is a set of files:
- `project.py` — project-wide constants + parameters, imported everywhere (`from project import UNIT, WALL`).
- `parts/<name>.py` — reusable parametric part functions: `def <name>(...) -> a build123d object`. Parts may import other parts (`from parts.base import base`) and the project constants.
- `scenes/<name>.py` — assemblies: import parts + constants, build the model, and `show(...)` it (plus `require(...)` specs). A scene is a normal script (it has `show`/`require` in scope).

One file is the RUN TARGET (usually the active scene) — that is what renders. Editing one part may require editing the parts or scenes that use it; make ALL the edits needed so the run target still runs and looks right.

Rules:
- Return a list of `edits`, each `{path, new_source}` with the COMPLETE new source for that file (not a diff). `path` is project-relative: `project.py`, `parts/<name>.py`, or `scenes/<name>.py`. You may add NEW files (a new part). Only include files you actually change.
- STRONGLY PREFER build123d **algebra mode**: build objects as expressions and combine with operators — `part = Box(80, 50, 12)`, union `a + b`, cut `a - b` (`a -= b`), intersect `a & b`, place `Pos(x, y, z) * obj` / `Rot(z=90) * obj`, modify `part = fillet(part.edges().filter_by(Axis.Z), radius=2)`. Avoid `with BuildPart()` builder mode unless a file already uses it.
- NEVER `from build123d import *`; use explicit named imports. In parts, import project constants (`from project import ...`) rather than hard-coding numbers.
- To reuse a part from ANOTHER project, import it absolutely: `from projects.<other_project>.parts.<name> import <name>` (that other project's own constants come with it). Use `list_library_parts` / your knowledge of the workspace to find reusable parts.
- A part file defines a function and returns the object — it does NOT call `show()`. Only scenes call `show(...)` / `require(...)`.
- Keep every existing `require(...)` spec verbatim in the run target and satisfy it by changing geometry — never weaken or delete a spec.
- Use the loop: call `read_project` to see all files, then `dry_run` with your candidate edits to run the run target with them applied — it returns ok/error/bbox/parts/specs. Iterate (fix errors, re-run) until it runs and the specs pass BEFORE returning.
- If the user refers to "this"/"the selected ...", call `get_selection`.

Put a one-or-two sentence explanation in `rationale`, and list the files/areas you touched in `targets`.
"""


@dataclass
class ProjectDeps:
    """Injected per run — the whole project + what's being viewed."""

    project: str
    run_kind: str  # "scene" | "part" | "project"
    run_name: str  # e.g. the scene stem
    files: dict[str, str] = field(default_factory=dict)  # {relpath: source}
    selection: dict | None = None
    library: list[dict] = field(default_factory=list)


class FileEdit(BaseModel):
    path: str = Field(description="project-relative path: project.py, parts/<name>.py, or scenes/<name>.py")
    new_source: str = Field(description="the COMPLETE new source for this file")


class ProjectPatch(BaseModel):
    """The validated multi-file edit the agent must return (or it retries)."""

    edits: list[FileEdit] = Field(description="one entry per file you change or add; complete source each")
    rationale: str = Field(description="one or two sentences explaining the change")
    targets: list[str] = Field(default_factory=list, description="files / areas touched")


def _edit_map(edits: list[FileEdit]) -> dict[str, str]:
    return {e.path.strip().lstrip("/"): e.new_source for e in edits}


def _preview_for(deps: ProjectDeps) -> str | None:
    """A part defines a function but never calls show(), so running its file alone
    won't exercise the body (a bug inside only fires when called). Preview it by
    wrapping in show(part()); project.py is constants, so just import it. A scene
    runs as-is."""
    if deps.run_kind == "part" and deps.run_name:
        n = deps.run_name
        return f'from parts.{n} import {n}\nshow({n}(), name="{n}")'
    if deps.run_kind == "project":
        return "import project"
    return None


async def _run_with(deps: ProjectDeps, overrides: dict[str, str]) -> dict[str, Any]:
    """Run the run target with `overrides` applied — off-thread (blocking exec)."""
    return await asyncio.to_thread(
        run_project, deps.project, deps.run_kind, deps.run_name, overrides, _preview_for(deps)
    )


def build_project_agent(model: Any | None = None) -> Agent[ProjectDeps, ProjectPatch]:
    agent = Agent[ProjectDeps, ProjectPatch](
        model or DEFAULT_MODEL,
        deps_type=ProjectDeps,
        output_type=ProjectPatch,
        instructions=INSTRUCTIONS,
        retries=3,
    )

    @agent.tool
    async def read_project(ctx: RunContext[ProjectDeps]) -> dict:
        """The whole project: every file's source, plus which file is the run target."""
        return {
            "run_target": f"{ctx.deps.run_kind}:{ctx.deps.run_name}",
            "files": ctx.deps.files,
        }

    @agent.tool
    async def dry_run(ctx: RunContext[ProjectDeps], edits: list[FileEdit]) -> dict:
        """Run the run target with these candidate edits applied on top of the
        project. Returns {ok, error, line, bbox, parts, specs}. Use this to verify
        (and iterate on) your edits before returning them."""
        result = await _run_with(ctx.deps, _edit_map(edits))
        if result.get("ok"):
            return {
                "ok": True,
                "bbox": result.get("bbox"),
                "parts": list((result.get("states") or {}).keys()),
                "specs": result.get("specs"),
                "stdout": result.get("stdout"),
            }
        return {"ok": False, "error": result.get("error"), "line": result.get("error_line")}

    @agent.tool
    async def get_selection(ctx: RunContext[ProjectDeps]) -> dict | None:
        """The entity currently picked in the viewport, with a synthesized selector."""
        return ctx.deps.selection

    @agent.tool
    async def list_library_parts(ctx: RunContext[ProjectDeps]) -> list[dict]:
        """The reusable parts catalog (signatures, params, joints)."""
        return ctx.deps.library

    @agent.output_validator
    async def validate_patch(ctx: RunContext[ProjectDeps], patch: ProjectPatch) -> ProjectPatch:
        """Self-correct: the edits must actually change something, run the run
        target, and satisfy its `require(...)` specs (no weakening the spec)."""
        edits = _edit_map(patch.edits)
        if not edits:
            raise ModelRetry("edits is empty — return the file(s) you changed with their complete new source.")

        unchanged = [p for p, s in edits.items() if ctx.deps.files.get(p, "").strip() == s.strip()]
        if len(unchanged) == len(edits):
            raise ModelRetry("None of the edits change any file — make the requested change.")

        result = await _run_with(ctx.deps, edits)
        if not result.get("ok"):
            where = f" (line {result['error_line']})" if result.get("error_line") else ""
            raise ModelRetry(
                f"Your edits fail to run the run target: {result.get('error')}{where}. "
                "Fix the file(s) and return working sources."
            )

        # Spec integrity on the run target: keep every original require() verbatim.
        target_path = "project.py" if ctx.deps.run_kind == "project" else f"{ctx.deps.run_kind}s/{ctx.deps.run_name}.py"
        original_src = ctx.deps.files.get(target_path, "")
        new_target_src = edits.get(target_path, original_src)
        try:
            missing = [r for r in _require_calls(original_src) if r not in _require_calls(new_target_src)]
        except Exception:
            missing = []
        if missing:
            raise ModelRetry(
                "You changed or removed existing require(...) specs in the run target: "
                + "; ".join(missing)
                + ". Keep every original require() call verbatim and satisfy it by changing the geometry instead."
            )

        failed = [s.get("message", "requirement") for s in (result.get("specs") or []) if not s.get("passed")]
        if failed:
            raise ModelRetry(
                "The run target runs but violates these require(...) specs: "
                + "; ".join(failed)
                + ". Adjust the geometry so every spec passes — do not weaken the specs."
            )
        return patch

    return agent


async def run_project_agent(
    project: str,
    message: str,
    run_kind: str,
    run_name: str,
    *,
    selection: dict | None = None,
    library: list[dict] | None = None,
    message_history: list | None = None,
    model: Any | None = None,
) -> dict:
    """Run the multi-file agent for one request. Returns a JSON-able dict:
    {ok, edits: [{path, new_source}], rationale, targets} or {ok: False, error}."""
    from app.projects import project_files

    deps = ProjectDeps(
        project=project,
        run_kind=run_kind,
        run_name=run_name,
        files=project_files(project),
        selection=selection,
        library=library or [],
    )
    try:
        agent = build_project_agent(model)  # construction needs the API key too
        result = await agent.run(message, deps=deps, message_history=message_history)
    except Exception as exc:  # surface a clean error instead of a 500
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    patch: ProjectPatch = result.output
    return {
        "ok": True,
        "edits": [{"path": e.path.strip().lstrip("/"), "new_source": e.new_source} for e in patch.edits],
        "rationale": patch.rationale,
        "targets": patch.targets,
    }
