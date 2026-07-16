# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status: v0 shipped + major arcs on top

The full v0 from `cad-platform-v0-plan.md` (M0–M7 + printability) is built, plus: the **projects model** (multi-file parts/scenes), the **print planner** (orientation search + real-slicer ground truth + plating), the **reworked project agent**, **physics/animation** in the viewer, and **editor intelligence** (completions/hover/rename). Deployed at **cad.enzomaruffa.dev**. Issue tracking is in **beads** (`bd list`, `bd ready`).

**Repo skills** (`.claude/skills/`) — invoke these before the matching task instead of rediscovering: `cad-design` (part/project design conventions + printability rules + fit-proof pattern), `cad-verify` (run/verify/render locally), `cad-deploy` (the two prod deploy paths + container verification), `cad-print-planner` (planner APIs, orientation probing, slicing).

### Run it

```bash
# Backend (FastAPI + sandboxed build123d kernel) — needs Python 3.12 (OCP wheels)
cd backend && uv sync --extra agent
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787   # 8000 is often taken; frontend expects 8787

# Frontend (Vite + React + Monaco + our own @cadcode/viewer renderer)
cd viewer && npm install                                    # the renderer package has its own deps (three)
cd frontend && npm install && npm run dev                   # http://localhost:5173

# Checks
cd backend  && uv run ruff format . && uv run ruff check . && uv run ty check   # lint + format + types
cd frontend && npx tsc -b --noEmit && npm run lint && npm run format             # types + eslint + prettier
```

Lint/format/type-check are wired: backend uses **ruff** (lint+format) + **ty** (Astral's type checker) — config in `backend/pyproject.toml` (`[tool.ruff]`, `[tool.ty]`); frontend uses **ESLint** (flat config `eslint.config.js`) + **Prettier** (`.prettierrc.json`) — `npm run lint` / `npm run format`. All green; keep them green.

The agent uses **Gemini 3.5 Flash** by default (`google:gemini-3.5-flash`); override with `CAD_AGENT_MODEL` (any pydantic-ai model id). It needs `GEMINI_API_KEY`/`GOOGLE_API_KEY` at runtime. The backend port is configurable on the frontend via `VITE_WS_URL` / `VITE_HTTP_URL`.

Local scripting gotcha: rendering SVG→PNG locally (`cairosvg`) needs brew cairo — run with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run python ...` (set BEFORE the process starts).

### Deploy (prod: cad.enzomaruffa.dev)

- Server **webrato-remote** (SSH alias), repo checkout at `~/cad-kit`, container `cad-kit-web-1`.
- **App code**: `git push`, then `ssh -A webrato-remote "cd ~/cad-kit && git pull --ff-only && docker compose build web && docker compose up -d web"`.
- **Project files** (parts/scenes) live on a volume at `/data/projects` — NOT in the image. `backend/projects/` is **gitignored** locally; push it with `COPYFILE_DISABLE=1 tar --no-xattrs -czf x.tgz <proj>` → scp → `docker cp` → extract in the container, then **`find /data/projects -name "._*" -delete`** (macOS AppleDouble files break `_materialize`'s `read_text`).
- Run Python inside the container with **`/app/backend/.venv/bin/python`** (bare `python` lacks the deps). Verify after every push: run every part preview + scene via `app.project_runner.run_project` and check `ok`.
- Prod has **headless OrcaSlicer** (`/opt/orcaslicer`, runs under xvfb) — real slicing works there; local usually has no slicer (probe features degrade gracefully).

### Projects (the multi-file model)

`projects/<name>/{project.py, parts/*.py, scenes/*.py}` — `project.py` holds constants (a literal `NAME: Annotated[float, Range(lo, hi)] = v` becomes a UI slider; computed constants are skipped, which is a feature), parts are functions named after their file, scenes `show(...)` assemblies. `app/project_runner.py` materializes every project into a temp workspace and **rewrites bare imports** (`from project import X`, `from parts.y import y`) to absolute `projects.<pid>.…` — so a part binds to *its own* project's constants. Cross-project imports (`from projects.<other>.parts.x import x`) pass through untouched **and bind to the OTHER project's constants**.

Conventions that hold across the flagship `dishrack` project (+ `dishrack_mini`):
- **Everything constants-driven.** Absolute mm in part code is a smell; real-world thresholds live as `SPEC_*` constants so scaled variants stay satisfiable.
- **Specs (`require(...)`) are ambient** (bound on builtins with `print_hint`): parts assert their own sanity only; **scenes assert assembly clearances** (a part-level headroom spec once broke an intentionally-short scene).
- **The mini-project pattern**: a scaled variant = a new project whose `project.py` derives from the source (`from projects.dishrack import project as full`, `X = full.X * SCALE`) with **printability floors** (walls, pin fits, drain holes are absolute printer physics) + verbatim-copied `parts/`/`scenes/` (refresh by re-copying).
- `print_hint(part, flow=(x,y,z))` declares print intent (water-flow layer alignment; `cosmetic` faces) — the planner obeys it above strategy.
- Sockets get **45° cone roofs** (self-supporting bores); boxes flush with cylinders are pulled **1mm inside the tangent** (tangent faces print as square artifacts); parts wider than the bed are designed as **bolted half-lap halves** (M3 tokens from `lib/design.py`), not slicer-split.

### The print planner (`app/printplan.py` + `kernel/print_time.py` + `kernel/slicer.py`)

Per part: ~48 candidate orientations (principal + Fibonacci sphere) scored on **removability-weighted support cost** (support trapped in a bore ×10, via a voxel occupancy grid + escape rays) → top few get layer-sliced estimates → optional **ground-truth probe** (`probe=True`, the 🎯 toggle): the REAL slicer slices the finalists and re-ranks by exact support grams parsed from G-code feature blocks (`support_stats`), cached by mesh hash. Ranking currency follows the strategy (material=grams, fastest=minutes, plates=footprint), **bed-fit outranks everything** (an orientation that can't fit any plate loses to one that can). Packing is FFD shelves + a greedy **swap-to-fit** pass over each part's Pareto-set of orientations (never trades away a print hint). Plates export as STL/3MF/G-code; per-part `orient` override; split suggestions for support-heavy parts.

### Where things live (backend `app/`)

- `protocol.py` — the one WS envelope + message types (§8). `session.py` — per-socket dispatch (the `apply_edit → run → tessellate → render` spine). `main.py` — FastAPI app, WS, `/library` endpoint, shared warmed kernel lifespan.
- `kernel/` — `runner.py` (exec build123d, `show()`/`require()` collectors, provenance-friendly), `sandbox.py` (AST allowlist + restricted builtins), `subprocess_kernel.py` + `worker_main.py` (isolated worker, SIGALRM timeout, op dispatch: run/select/printability/provenance/geomdiff), `select.py` (pick → measure + selector synthesis), `printability.py` (overhang heatmap), `provenance.py` (line↔face), `geomdiff.py` (boolean added/removed).
- `tessellate.py` — build123d → ocp-tessellate shapes tree consumed by our renderer (`numpy_to_json`; states embedded per-part; `triangles_per_face`/`segments_per_edge` drive per-face/edge picking). `gitstore.py` — checkpoints. `library.py` + `thumbnail.py` — parts catalog + iso SVGs. `params.py` — slider extraction. `agent/cad_agent.py` — PydanticAI agent (typed `CadDeps`, validated `Patch`, self-correct + spec-integrity validator).
- `lib/` — shared `design.py` tokens (WALL/CLEARANCE/M3_* + materials) + `parts/` catalog (on `PYTHONPATH`, allowlisted in the sandbox).
- `projects.py`/`project_runner.py` — multi-file projects (see Projects section). `project_agent.py` — the project agent. `printplan.py` + `kernel/print_time.py`/`slicer.py` — print planner + headless Orca/Prusa. `b3d_docs.py`/`completions.py` — build123d introspection feeding editor autocomplete/hover/signature-help. `rename.py` — cross-project part rename refactor. `joints.py`/`bake.py` — articulation + bake-animation-to-code. `thumbnail.py` — GL-free painter's-algorithm iso SVG renders (library thumbnails AND the agent's vision pass; composes per-node `loc` transforms, depth-keys edges).

### The 3D renderer (`viewer/` — our own `@cadcode/viewer`)

We render OCP tessellation with our **own three.js renderer** (top-level `viewer/` package, its own deps, consumed by the app via the Vite alias `@cadcode/viewer` → `viewer/src/index.ts`; `frontend/vite.config.ts` also dedupes react/react-dom/three). `three-cad-viewer` is gone. The two call sites are `Viewport.tsx` and `PartPreview.tsx`, both mounting one React component `CadCanvas` (wrapping the framework-agnostic `CadViewer`). The store contract is unchanged: a pick still calls `sendSelect(kind, shapeId, index)`; highlight picks call `setRevealLine`.

- `viewer/src/core/` — `CadViewer` (renderer/scene/camera/on-demand loop/lifecycle), `SceneGraph`+`leaf`+`buildGeometry` (tree → one **indexed** mesh per leaf — never merged or per-face-split, so `faceIndex` maps 1:1 to a triangle), `CameraRig` (ortho+persp, Z-up iso, camera persists across rebuilds), `Controls` (OrbitControls), `dispose`.
- `viewer/src/materials/` — PBR `materials`, `lighting` (key/fill/rim headlight + RoomEnvironment IBL), `shadows` (ShadowMaterial ground), `postprocessing` (EffectComposer: GTAO+SMAA+OutputPass for presentation), `colorApi` (per-leaf colors + cheap `applyHighlight` active-line glow + `applyFaceColors`).
- `viewer/src/interaction/` — `Picker`+`InteractionController` (raycast → OCP face/edge/vertex via prefix-sums), `SelectionHighlight`, `Section` (clipping planes), `Measure` (CSS2D dims).

### Gotchas learned the hard way

- **Per-face picking is precise now:** raycast `intersection.faceIndex` → OCP face via the prefix-sum of `triangles_per_face`. Leaf geometry must stay **indexed and unmerged** for this to hold (no `toNonIndexed`, no per-face explosion). Per-face color is done in-renderer (vertex colors / per-leaf), not by tessellating each face as its own part.
- **`edges` arrives nested** (`[[x,y,z],[x,y,z]]` per segment) — flatten before `LineSegmentsGeometry.setPositions`; tessellation arrays are plain JSON numbers, so wrap in `Float32Array`/`Uint32Array`. Don't call `computeLineDistances()` on solid `LineSegments2` (NaN bounding sphere → culled edges).
- **No React StrictMode** — `CadViewer`/Monaco are imperative singletons; the double-mount corrupts them.
- **Object names still use `|` as the path delimiter** (`group.name = id.replaceAll("/", "|")`) — preserved so `parsePick`/highlight name parsing (`L<line>__f<i>`) still works.
- **Diagnostic view modes force the flat preset** (no AO/tone-mapping) so printability/provenance/geomdiff colors stay literal; presentation (✨) only applies to the `technical` view mode.
- **Editor theme is configurable:** `frontend/src/lib/editorTheme.ts` defines the `cadcode` Monaco theme from token colors (persisted in localStorage; live picker in the File menu). Fonts are self-hosted Geist in `frontend/public/fonts/`.
- `window.__store` / `window.__viewer` / `window.monaco` are exposed for debugging/E2E.
- **Physics/animation** live in `viewer/src/physics/` (Rapier WASM in a Web Worker, voxel concave collision, joint articulation, exploded view); "bake" turns a sim pose into code via `app/bake.py` — code stays the source of truth.
- The viewport has a **measured ruler grid** (`viewer/src/core/Grid.ts`: cutting-mat floor + walls, 1/2/5 steps, size chip) cycled from the toolbar.

## What this project is

An AI-native, code-first CAD environment where **the canvas *is* code**. A React/TS frontend (Monaco editor + our own `@cadcode/viewer` viewport + agent chat) talks over a single WebSocket to a FastAPI backend that runs **build123d** Python in a sandboxed kernel worker, with a server-side **PydanticAI** agent as a peer editor. Output: 3D-printable parts, with a printability overlay as the differentiating feature.

## The one invariant — enforce this above all else

**The model is a build123d Python script. That script is the only source of truth.** Geometry, history, measurements, colors/materials, part references, and the agent's worldview are all *derived from* or *expressed in* that script.

Concrete consequences that constrain every design decision:

- **Three editors, one buffer.** The human (Monaco), the agent (proposed patches), and the viewport (gestures compiled to code) all mutate the *same* source buffer through one path: `apply_edit(buffer) → run → tessellate → render`. Never introduce a second "real" model to reconcile.
- **Undo/redo is buffer history; durable checkpoints are git commits.** No separate feature-tree state.
- **Anything you'd store *about* the model is stored as code that re-derives it** — never a transient ID. A clicked face becomes a **selector expression** (`.faces().filter_by(Plane.XY).sort_by(Axis.Z)[-1]`), a color becomes `show(part, color=...)` in the source, a part reference becomes an `import`. IDs break on re-run; code survives parameter changes.
- **Re-exec the whole script per edit.** Deterministic, stateless, matches code-as-truth. Debounce on the frontend (~250ms). Incremental recompute is explicitly deferred (see below).

If a proposed change would let any of the three editors fork reality, or would persist state that doesn't re-derive from the script, it violates the invariant — reconsider it.

## Architecture (three processes)

- **Frontend (React + TS + Vite):** Monaco editor pane, our own `@cadcode/viewer` (three.js) viewport, agent chat + diff panel, history timeline, one WebSocket client.
- **Backend (FastAPI, async):** WebSocket gateway (one socket per session), in-memory document/buffer state (no database in v0), the PydanticAI agent service, git integration, part library on `PYTHONPATH`.
- **Kernel worker (isolated subprocess):** runs the build123d script in a *separate process* so a hang/loop/OOM can't take the backend down. Returns `{meshes, ops, measurements}` or `{error: traceback+line}`. Uses the `build123d-mcp` sandbox pattern (AST import allowlist, restricted builtins, `SIGALRM` timeout, restart-on-breach).

Render pipeline: `build123d objects → ocp-tessellate (mesh + topology indices) → WebSocket → our @cadcode/viewer (three.js)`. ocp-tessellate does the meshing; the viewer (`viewer/`) is ours.

## Locked tech-stack decisions (don't relitigate or reinvent)

| Layer | Pick |
|---|---|
| Frontend | React + TS + Vite |
| Editor | **Monaco** — do not hand-roll an editor |
| Viewport | **our own `@cadcode/viewer`** (three.js) in `viewer/` — replaced three-cad-viewer for precise per-face picking + a prettier render |
| Backend | **FastAPI** (async, WS, Pydantic protocol models) |
| Agent | **PydanticAI** (+ Logfire/OTel); tools implemented **natively** in-process but as thin wrappers so they can be exposed over MCP later |
| Kernel | **build123d** + **bd_warehouse** |
| Tessellation | **ocp-tessellate** |
| Worker isolation | subprocess + `build123d-mcp` sandbox pattern |
| Persistence | **git repo on disk** — no DB, no Redis, no Pyodide in v0 |

## The WebSocket contract (pin this early — it's the FE/BE seam)

One JSON envelope, request/response correlated by `id`: `{ "type": "...", "id": "uuid", "payload": { } }`.

- **FE → BE:** `edit` · `run` · `select` · `gesture` · `chat` · `accept_patch` / `reject_patch` · `checkpoint` · `rollback`.
- **BE → FE:** `geometry` · `error` · `agent_message` · `agent_patch` · `measurement` · `status`.

## The agent loop (what makes it more than a chatbot)

Two agents share the philosophy (self-correct against real execution before the human sees anything — never bypass it):
- `agent/cad_agent.py` — single-buffer agent: typed `CadDeps`, validated `Patch` output, dry-run + spec-integrity validator.
- `app/project_agent.py` — the **multi-file project agent**: search/replace `FileEdit{path, find, replace}` patches, front-loaded first message (files + constants + geometry facts), **lean dry-runs** (`run_source_lean`: no tessellation, bbox/volume/gap facts), error hints from `b3d_docs` signatures, an optional **vision sanity pass** (`CAD_AGENT_VISION=1`: renders the result via `thumbnail.iso_svg_shapes` and a vision model checks it), and persistent `.agent_notes.md` memory. Returns the full new source (FE contract).

## Stances on the hard problems (decided — don't rediscover mid-build)

- **Stable references → store selectors, not IDs.** Resolve every click to a selector expression and store *that*. The agent is good at "the top rim" → robust selector.
- **Provenance (which line made which face) → instrumentation spike (milestone M6).** Wrap the build123d operation API to tag output topology with source location. Required for bidirectional code↔geometry highlighting and geometry diffs.
- **Incremental recompute → deferred, but keep the model content-addressable.** Re-exec-per-edit in v0; design operations to be content-addressable (hash of producing code + inputs) so caching/history/diffing can later fall out of one mechanism. Don't build the build graph in v0.
- **Agent correctness → assertions-as-spec (CAD-as-TDD).** Executable specs (`assert part.clearance(shaft) >= 0.2`) give an objective done-condition; the agent iterates until they pass.
- **Concurrency** is dodged in v0 via turn-taking + the approve flow, but keep all edits modeled as patches so CRDT co-editing stays possible later.

## Cohesion model

Cohesion = **consistency** (shared `lib/design.py` design tokens like `WALL`, `CLEARANCE`, `M3` that every part imports) + **connection** (build123d `Joint`s + `connect_to`, not hand-computed transforms) + **composition** (a library-aware agent fed each part's signature/docstring/params/joints/thumbnail). An assembly is just a script that imports parts and connects joints — same substrate, so history/agent/diff/render all work identically.

## Reference to mine

FluidCAD is the reference for mouse→code binding, smart-default ergonomics, and the history/rollback model; its tutorials (lantern, ice-cube tray, CSWP parts) are the acceptance-test corpus. **But it is single-player with no agent** — this project's data model must treat the agent as a peer editor from line one (everything addressable and diffable).


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->
