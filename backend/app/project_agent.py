"""The whole-project multi-file agent (PROJECTS_PLAN.md → "Multi-file agent").

Where ``agent/cad_agent.py`` edits a single buffer, this agent edits a whole
**project** — ``project.py`` + ``parts/*`` + ``scenes/*`` — and a single change
may cascade across several files. It runs an explicit **edit → run → view →
edit** loop: dry-runs candidate edits against the run target, inspects
errors/specs/geometry facts, and refines until validated.

Speed + smarts (2026-07-09 rework):
  * edits are **search/replace patches**, not whole files — 5-20× fewer output
    tokens (the wall-clock lever) and no mangling of untouched code;
  * the whole project + selection + parts catalog + per-project memory are
    **front-loaded** into the first message (no read_project roundtrip);
  * dry runs are **lean** — exec + specs + geometry FACTS (bbox/volume/gaps),
    no tessellation;
  * every step appends to a **status log** the UI polls;
  * on errors, the retry message carries the **real signature** of the failing
    callable (introspected build123d / parts catalog) — repair, not re-guess;
  * after specs pass, an optional **vision check** renders an iso thumbnail and
    asks the (multimodal) model whether the result is geometrically plausible;
  * a validated patch may leave a one-line **note** in the project's agent
    memory (applied on accept).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext

from app.agent.cad_agent import DEFAULT_MODEL, _require_calls
from app.project_runner import run_project

INSTRUCTIONS = """\
You edit a whole build123d **project** — the one source of truth for a 3D CAD project. A project is a set of files:
- `project.py` — project-wide constants + parameters, imported everywhere (`from project import UNIT, WALL`).
- `parts/<name>.py` — reusable parametric part functions: `def <name>(...) -> a build123d object`. Parts may import other parts (`from parts.base import base`) and the project constants.
- `scenes/<name>.py` — assemblies: import parts + constants, build the model, and `show(...)` it (plus `require(...)` specs). A scene is a normal script (it has `show`/`require` in scope).

One file is the RUN TARGET (usually the active scene) — that is what renders. Editing one part may require editing the parts or scenes that use it; make ALL the edits needed so the run target still runs and looks right.

Your first message already contains the FULL project source, the run target, any viewport selection, the parts catalog, and project memory — do not ask for them.

Edits are SEARCH/REPLACE patches — small and precise:
- Each edit is `{path, find, replace}`. `find` is EXACT text copied verbatim from the current file (with its whitespace), occurring exactly once in that file; `replace` is its replacement.
- To create a NEW file (or fully rewrite a small one), use `find: ""` and put the complete source in `replace`.
- Prefer several small find/replace edits over rewriting a file. Never retype code you aren't changing.

Rules:
- STRONGLY PREFER build123d **algebra mode**: build objects as expressions and combine with operators — `part = Box(80, 50, 12)`, union `a + b`, cut `a - b`, intersect `a & b`, place `Pos(x, y, z) * obj` / `Rot(Z=90) * obj`, modify `part = fillet(part.edges().filter_by(Axis.Z), radius=2)`. Avoid `with BuildPart()` builder mode unless a file already uses it.
- NEVER `from build123d import *`; use explicit named imports. In parts, import project constants (`from project import ...`) rather than hard-coding numbers.
- To reuse a part from ANOTHER project: `from projects.<other_project>.parts.<name> import <name>`. Global library parts: `from lib.parts import <name>`.
- A part file defines a function and returns the object — it does NOT call `show()` (only scenes render). A part MAY set `part.color = Color("#rrggbb")`, declare its own `require(...)` specs, and declare PRINT INTENT: `print_hint(part, flow=(0,0,-1))` (fluid runs along this vector — the print planner keeps layer lines parallel to it) or `print_hint(faces, cosmetic=True)` (keep support scars off these faces).
- Keep every existing `require(...)` spec verbatim in the run target and satisfy it by changing geometry — never weaken or delete a spec.
- For KINEMATIC fit-proofs (a part that slides/twists/locks into another), prefer `require_motion(moving, path, against=[...], max_contact=..., label=...)` over hand-rolled per-pose `&` intersection loops. Paths: `turn(axis, start_deg, end_deg)`, `slide(vector, mm)`, `screw(axis, deg, pitch)`, or any `t -> Location` callable (world frame, about the origin). A detent the part must snap past gets its interference budget via `max_contact` (mm³); free travel should sweep at `max_contact=0.01`.
- WORKFLOW — plan, then edit incrementally: for anything non-trivial, decide the sequence of file changes first; then apply ONE file's edits at a time with `dry_run` between steps, fixing as you go, instead of one giant multi-file patch. `dry_run` returns ok/error/specs plus geometry FACTS (per-part bbox size + volume, pairwise gaps/overlaps) — sanity-check them (is the lid larger than the box? do parts that should touch actually touch?).
- If a dry_run error comes with a "signature:" hint, trust the hint — it is the REAL signature introspected from the installed library.
- If something durable was decided (a design choice, a constraint, something the user prefers), put ONE short line in `note` — it becomes project memory for future requests. Leave it empty otherwise.

Put a one-or-two sentence explanation in `rationale`, and list the files/areas you touched in `targets`.
"""

# --- deps + patch model ---------------------------------------------------------


@dataclass
class ProjectDeps:
    """Injected per run — the whole project + what's being viewed."""

    project: str
    run_kind: str  # "scene" | "part" | "project"
    run_name: str  # e.g. the scene stem
    files: dict[str, str] = field(default_factory=dict)  # {relpath: source}
    selection: dict | None = None
    library: list[dict] = field(default_factory=list)
    memory: str = ""  # per-project agent notes (read-only here; appended on accept)
    log: Callable[[str], None] = lambda _m: None  # streamed to the UI via job polling
    vision_checked: bool = False  # one visual sanity pass per job


class FileEdit(BaseModel):
    path: str = Field(description="project-relative path: project.py, parts/<name>.py, or scenes/<name>.py")
    find: str = Field(
        "",
        description="EXACT text to replace, copied verbatim from the file (must occur exactly once). "
        'Empty ("") means create a new file / full rewrite.',
    )
    replace: str = Field(description="the replacement text (or the complete source when find is empty)")


class ProjectPatch(BaseModel):
    """The validated multi-file patch the agent must return (or it retries)."""

    edits: list[FileEdit] = Field(description="search/replace edits; several small edits beat one big rewrite")
    rationale: str = Field(description="one or two sentences explaining the change")
    targets: list[str] = Field(default_factory=list, description="files / areas touched")
    note: str = Field("", description="ONE short durable note for project memory (decision/constraint), or empty")


def apply_edits(files: dict[str, str], edits: list[FileEdit]) -> dict[str, str]:
    """Apply search/replace edits on top of `files` → {path: full new source} for
    every touched file. Raises ValueError with an agent-actionable message."""
    out: dict[str, str] = {}
    for e in edits:
        path = e.path.strip().lstrip("/")
        base = out.get(path, files.get(path, ""))
        if not e.find:
            out[path] = e.replace
            continue
        n = base.count(e.find)
        if n == 0:
            raise ValueError(
                f"edit to {path}: `find` text not found. Copy it EXACTLY from the current file "
                '(including whitespace/indentation), or use find:"" with the complete new source.'
            )
        if n > 1:
            raise ValueError(
                f"edit to {path}: `find` text occurs {n} times — include more surrounding lines so it is unique."
            )
        out[path] = base.replace(e.find, e.replace, 1)
    return out


# --- error → real-signature hints -------------------------------------------------

_NAME_RE = re.compile(r"name '(\w+)' is not defined")
_CALL_RE = re.compile(r"(\w+)(?:\.__init__)?\(\)\s")


def _error_hints(error: str) -> str:
    """Real signatures for the callables an error message mentions — introspected
    from the installed build123d / the parts catalog, so the retry repairs
    instead of re-guessing."""
    names: list[str] = []
    m = _NAME_RE.search(error)
    if m:
        names.append(m.group(1))
    names += _CALL_RE.findall(error)
    hints: list[str] = []
    seen: set[str] = set()
    try:
        from app.b3d_docs import build123d_docs

        docs = build123d_docs()
    except Exception:
        docs = {}
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        e = docs.get(n)
        if e and e.get("signature") and e["signature"] != n:
            hints.append(f"signature: {e['signature']}")
    if m and m.group(1) not in docs:  # unknown name — maybe a part that needs importing
        try:
            from app.completions import completion_catalog

            cat = completion_catalog()
            for p in cat.get("projects", []):
                for part in p.get("parts", []):
                    if part["name"] == m.group(1):
                        hints.append(
                            f"hint: `{part['name']}` is a part in project `{p['name']}` — import it "
                            f"(same project: `from parts.{part['name']} import {part['name']}`; "
                            f"other project: `from projects.{p['name']}.parts.{part['name']} import {part['name']}`)"
                        )
            for lp in cat.get("lib_parts", []):
                if lp["name"] == m.group(1):
                    hints.append(f"hint: `from lib.parts import {lp['name']}` — {lp.get('signature', '')}")
        except Exception:
            pass
    return (" | " + "; ".join(hints)) if hints else ""


# --- running ----------------------------------------------------------------------


def _preview_for(deps: ProjectDeps) -> str | None:
    """A part defines a function but never calls show(); preview it by wrapping
    in show(part()). Scenes/project.py run as-is."""
    if deps.run_kind == "part" and deps.run_name:
        n = deps.run_name
        return f'from parts.{n} import {n}\nshow({n}(), name="{n}")'
    return None


async def _run_with(deps: ProjectDeps, overrides: dict[str, str], *, lean: bool = True) -> dict[str, Any]:
    """Run the run target with `overrides` applied — off-thread. Lean by default:
    exec + specs + geometry facts, no tessellation (the agent never sees meshes)."""
    return await asyncio.to_thread(
        run_project, deps.project, deps.run_kind, deps.run_name, overrides, _preview_for(deps), lean
    )


def _first_message(deps: ProjectDeps, message: str) -> str:
    """Front-load everything the agent needs — no context-fetch roundtrips."""
    blocks = [f"REQUEST: {message}", f"RUN TARGET: {deps.run_kind}:{deps.run_name or 'project.py'}"]
    if deps.selection:
        blocks.append(f"VIEWPORT SELECTION: {deps.selection}")
    if deps.memory:
        blocks.append(f"PROJECT MEMORY (decisions from earlier sessions):\n{deps.memory}")
    if deps.library:
        lib = "\n".join(f"- {p.get('signature', p.get('name'))}" for p in deps.library[:40])
        blocks.append(f"GLOBAL LIBRARY PARTS:\n{lib}")
    total = sum(len(s) for s in deps.files.values())
    if total <= 60_000:
        listing = "\n\n".join(f"### {path}\n```python\n{src}```" for path, src in sorted(deps.files.items()))
        blocks.append(f"PROJECT FILES:\n{listing}")
    else:  # huge project — fall back to the tool
        blocks.append("PROJECT FILES: too large to inline — call read_project() for {path: source}.")
    return "\n\n".join(blocks)


# --- the vision sanity pass --------------------------------------------------------


class VisionVerdict(BaseModel):
    plausible: bool = Field(description="does the render plausibly match the request? (geometry, not style)")
    issue: str = Field("", description="if not plausible: the ONE most important geometric problem you see")


async def _vision_check(deps: ProjectDeps, edits_map: dict[str, str], request: str, model: Any) -> str | None:
    """Render the run target (with edits) to an iso thumbnail and ask the
    multimodal model whether the geometry is plausible. Returns an issue string
    (→ retry) or None (fine / unavailable). Best-effort by design."""
    if os.environ.get("CAD_AGENT_VISION", "1") != "1":
        return None
    try:
        import cairosvg  # requires libcairo2 — present in the Docker image

        from app.thumbnail import iso_svg_shapes

        full = await asyncio.to_thread(
            run_project, deps.project, deps.run_kind, deps.run_name, edits_map, _preview_for(deps), False
        )
        shapes = full.get("shapes")
        if not full.get("ok") or not shapes:
            return None
        svg = iso_svg_shapes(shapes, size=420)
        if not svg:
            return None
        png = cairosvg.svg2png(bytestring=svg.encode(), output_width=420, output_height=420)
        deps.log("vision: checking the render…")
        critic = Agent[None, VisionVerdict](model or DEFAULT_MODEL, output_type=VisionVerdict)
        result = await critic.run(
            [
                "Isometric render of a CAD result. The user asked: "
                f"{request!r}. Is the GEOMETRY plausible for that request — parts present, roughly the right "
                "proportions and positions, nothing wildly misplaced or interpenetrating? Judge geometry only, "
                "not style/colors. Be lenient: flag only obvious nonsense.",
                BinaryContent(data=png, media_type="image/png"),
            ]
        )
        if not result.output.plausible and result.output.issue:
            return result.output.issue
    except Exception:
        return None  # vision is a bonus — never block the patch on it
    return None


# --- the agent --------------------------------------------------------------------


def build_project_agent(model: Any | None = None) -> Agent[ProjectDeps, ProjectPatch]:
    agent = Agent[ProjectDeps, ProjectPatch](
        model or DEFAULT_MODEL,
        deps_type=ProjectDeps,
        output_type=ProjectPatch,
        instructions=INSTRUCTIONS,
        retries=3,
    )
    the_model = model or DEFAULT_MODEL

    @agent.tool
    async def read_project(ctx: RunContext[ProjectDeps]) -> dict:
        """Fallback for huge projects: every file's source + the run target."""
        return {"run_target": f"{ctx.deps.run_kind}:{ctx.deps.run_name}", "files": ctx.deps.files}

    @agent.tool
    async def dry_run(ctx: RunContext[ProjectDeps], edits: list[FileEdit]) -> dict:
        """Run the run target with these candidate edits applied. Returns
        {ok, error?, line?, specs, facts} — facts = per-part bbox/volume +
        pairwise gaps. Verify (and iterate) BEFORE returning your patch."""
        try:
            overrides = apply_edits(ctx.deps.files, edits)
        except ValueError as exc:
            ctx.deps.log(f"dry-run: bad edit — {exc}")
            return {"ok": False, "error": str(exc)}
        result = await _run_with(ctx.deps, overrides)
        if result.get("ok"):
            specs = result.get("specs") or []
            failed = sum(1 for s in specs if not s.get("passed"))
            ctx.deps.log(f"dry-run: ok — {len(specs)} spec(s), {failed} failing" if specs else "dry-run: ok")
            return {
                "ok": True,
                "specs": specs,
                "facts": result.get("facts"),
                "stdout": result.get("stdout"),
            }
        err = str(result.get("error") or "")
        ctx.deps.log(f"dry-run: error — {err[:120]}")
        return {"ok": False, "error": err + _error_hints(err), "line": result.get("error_line")}

    @agent.tool
    async def get_selection(ctx: RunContext[ProjectDeps]) -> dict | None:
        """The entity currently picked in the viewport, with a synthesized selector."""
        return ctx.deps.selection

    @agent.output_validator
    async def validate_patch(ctx: RunContext[ProjectDeps], patch: ProjectPatch) -> ProjectPatch:
        """Self-correct: edits must apply cleanly, change something, run the run
        target, satisfy its `require(...)` specs, and pass one visual sanity check."""
        if not patch.edits:
            raise ModelRetry("edits is empty — return the search/replace edits for the change.")
        try:
            edits_map = apply_edits(ctx.deps.files, patch.edits)
        except ValueError as exc:
            ctx.deps.log(f"validate: bad edit — {exc}")
            raise ModelRetry(str(exc)) from exc

        unchanged = [p for p, s in edits_map.items() if ctx.deps.files.get(p, "").strip() == s.strip()]
        if len(unchanged) == len(edits_map):
            raise ModelRetry("None of the edits change any file — make the requested change.")

        ctx.deps.log("validating: running the run target…")
        result = await _run_with(ctx.deps, edits_map)
        if not result.get("ok"):
            err = str(result.get("error") or "")
            where = f" (line {result['error_line']})" if result.get("error_line") else ""
            ctx.deps.log(f"validate: run failed — {err[:120]}")
            raise ModelRetry(f"Your edits fail to run the run target: {err}{where}.{_error_hints(err)} Fix and retry.")

        # Spec integrity on the run target: keep every original require() verbatim.
        target_path = "project.py" if ctx.deps.run_kind == "project" else f"{ctx.deps.run_kind}s/{ctx.deps.run_name}.py"
        original_src = ctx.deps.files.get(target_path, "")
        new_target_src = edits_map.get(target_path, original_src)
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
            ctx.deps.log(f"validate: {len(failed)} spec(s) failing")
            raise ModelRetry(
                "The run target runs but violates these require(...) specs: "
                + "; ".join(failed)
                + ". Adjust the geometry so every spec passes — do not weaken the specs."
            )

        # One visual sanity pass per job, after everything else is green.
        if not ctx.deps.vision_checked:
            ctx.deps.vision_checked = True
            issue = await _vision_check(
                ctx.deps, edits_map, ctx.prompt if isinstance(ctx.prompt, str) else "", the_model
            )
            if issue:
                ctx.deps.log(f"vision: {issue[:120]}")
                raise ModelRetry(f"Visual check of the rendered result: {issue}. Fix the geometry accordingly.")

        ctx.deps.log("validated ✓")
        return patch

    return agent


# --- per-project memory -------------------------------------------------------------


def _memory_path(project: str):
    from app.projects import ROOT, _ident

    return ROOT / _ident(project) / ".agent_notes.md"


def read_agent_memory(project: str, limit: int = 4000) -> str:
    try:
        return _memory_path(project).read_text()[-limit:]
    except OSError:
        return ""


def append_agent_memory(project: str, note: str, request: str = "") -> None:
    """Called when a patch is ACCEPTED — decisions become durable project memory."""
    note = (note or "").strip()
    if not note:
        return
    try:
        p = _memory_path(project)
        p.parent.mkdir(parents=True, exist_ok=True)
        prefix = f" (re: {request.strip()[:80]})" if request.strip() else ""
        with p.open("a") as f:
            f.write(f"- {note}{prefix}\n")
    except OSError:
        pass


# --- entry point --------------------------------------------------------------------


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
    log: Callable[[str], None] | None = None,
) -> dict:
    """Run the multi-file agent for one request. Returns a JSON-able dict:
    {ok, edits: [{path, new_source}], rationale, targets, note} or {ok, error}.
    (`edits` carry the FULL applied sources so the review/apply flow is unchanged
    even though the agent itself speaks in search/replace patches.)"""
    from app.projects import project_files

    deps = ProjectDeps(
        project=project,
        run_kind=run_kind,
        run_name=run_name,
        files=project_files(project),
        selection=selection,
        library=library or [],
        memory=read_agent_memory(project),
        log=log or (lambda _m: None),
    )
    try:
        agent = build_project_agent(model)  # construction needs the API key too
        deps.log("thinking…")
        result = await agent.run(_first_message(deps, message), deps=deps, message_history=message_history)
    except Exception as exc:  # surface a clean error instead of a 500
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    patch: ProjectPatch = result.output
    try:
        applied = apply_edits(deps.files, patch.edits)
    except ValueError as exc:  # validator already vetted this; belt-and-suspenders
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "edits": [{"path": p, "new_source": s} for p, s in applied.items()],
        "rationale": patch.rationale,
        "targets": patch.targets,
        "note": patch.note,
    }
