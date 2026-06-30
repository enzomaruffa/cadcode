# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status: pre-implementation

This repo currently contains **only the architecture plan** (`cad-platform-v0-plan.md`) — no code, no git repo, no build tooling is scaffolded yet. There are intentionally **no build/lint/test commands documented here** because nothing exists to run. When scaffolding begins, add the real commands to this file. Until then, the plan *is* the spec; read it before designing anything.

## What this project is

An AI-native, code-first CAD environment where **the canvas *is* code**. A React/TS frontend (Monaco editor + `three-cad-viewer` viewport + agent chat) talks over a single WebSocket to a FastAPI backend that runs **build123d** Python in a sandboxed kernel worker, with a server-side **PydanticAI** agent as a peer editor. Output: 3D-printable parts, with a printability overlay as the differentiating feature.

## The one invariant — enforce this above all else

**The model is a build123d Python script. That script is the only source of truth.** Geometry, history, measurements, colors/materials, part references, and the agent's worldview are all *derived from* or *expressed in* that script.

Concrete consequences that constrain every design decision:

- **Three editors, one buffer.** The human (Monaco), the agent (proposed patches), and the viewport (gestures compiled to code) all mutate the *same* source buffer through one path: `apply_edit(buffer) → run → tessellate → render`. Never introduce a second "real" model to reconcile.
- **Undo/redo is buffer history; durable checkpoints are git commits.** No separate feature-tree state.
- **Anything you'd store *about* the model is stored as code that re-derives it** — never a transient ID. A clicked face becomes a **selector expression** (`.faces().filter_by(Plane.XY).sort_by(Axis.Z)[-1]`), a color becomes `show(part, color=...)` in the source, a part reference becomes an `import`. IDs break on re-run; code survives parameter changes.
- **Re-exec the whole script per edit.** Deterministic, stateless, matches code-as-truth. Debounce on the frontend (~250ms). Incremental recompute is explicitly deferred (see below).

If a proposed change would let any of the three editors fork reality, or would persist state that doesn't re-derive from the script, it violates the invariant — reconsider it.

## Architecture (three processes)

- **Frontend (React + TS + Vite):** Monaco editor pane, `three-cad-viewer` viewport, agent chat + diff panel, history timeline, one WebSocket client.
- **Backend (FastAPI, async):** WebSocket gateway (one socket per session), in-memory document/buffer state (no database in v0), the PydanticAI agent service, git integration, part library on `PYTHONPATH`.
- **Kernel worker (isolated subprocess):** runs the build123d script in a *separate process* so a hang/loop/OOM can't take the backend down. Returns `{meshes, ops, measurements}` or `{error: traceback+line}`. Uses the `build123d-mcp` sandbox pattern (AST import allowlist, restricted builtins, `SIGALRM` timeout, restart-on-breach).

Render pipeline: `build123d objects → ocp-tessellate (mesh + topology indices) → WebSocket → three-cad-viewer`. The geometry math is done by those two libraries — the work here is the glue and the protocol.

## Locked tech-stack decisions (don't relitigate or reinvent)

| Layer | Pick |
|---|---|
| Frontend | React + TS + Vite |
| Editor | **Monaco** — do not hand-roll an editor |
| Viewport | **three-cad-viewer** — do not build a viewer from scratch (months of work already done) |
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

PydanticAI agent with typed dependency injection (`CadDeps`: live doc, kernel, selection, library) and a **validated `Patch` output** (`diff` / `rationale` / `targets`) — the agent retries until the output validates. The loop: read source + geometry + selection → **`dry_run`/`measure` the candidate edit in the sandbox and self-correct before the human sees anything** → return a validated `Patch` shown as a code + geometry diff to accept/reject. Step 3 (self-correction against real execution) is the whole point — never bypass it. Tools mirror `build123d-mcp`: `read_source`, `propose_patch`, `execute`, `measure`, `clearance`, `get_selection`, `list_library_parts`. Skip `pydantic-graph` in v0.

## Stances on the hard problems (decided — don't rediscover mid-build)

- **Stable references → store selectors, not IDs.** Resolve every click to a selector expression and store *that*. The agent is good at "the top rim" → robust selector.
- **Provenance (which line made which face) → instrumentation spike (milestone M6).** Wrap the build123d operation API to tag output topology with source location. Required for bidirectional code↔geometry highlighting and geometry diffs.
- **Incremental recompute → deferred, but keep the model content-addressable.** Re-exec-per-edit in v0; design operations to be content-addressable (hash of producing code + inputs) so caching/history/diffing can later fall out of one mechanism. Don't build the build graph in v0.
- **Agent correctness → assertions-as-spec (CAD-as-TDD).** Executable specs (`assert part.clearance(shaft) >= 0.2`) give an objective done-condition; the agent iterates until they pass.
- **Concurrency** is dodged in v0 via turn-taking + the approve flow, but keep all edits modeled as patches so CRDT co-editing stays possible later.

## Cohesion model

Cohesion = **consistency** (shared `lib/design.py` design tokens like `WALL`, `CLEARANCE`, `M3` that every part imports) + **connection** (build123d `Joint`s + `connect_to`, not hand-computed transforms) + **composition** (a library-aware agent fed each part's signature/docstring/params/joints/thumbnail). An assembly is just a script that imports parts and connects joints — same substrate, so history/agent/diff/render all work identically.

## Build milestones (sequenced; M3 is the soul — don't over-polish M0–M2)

M0 round-trip (Monaco→WS→build123d→tessellate→render) · M1 live + errors + sandbox · M2 selection + measure (click→selector synthesis starts) · **M3 the agent** · M4 cohesion (tokens/joints/library) · M5 git + history · M6 provenance + bidirectional highlighting · M7 mouse→code (one gesture first). *Parallel track after M2:* the printability overlay (overhang heatmap vs. build direction) — the differentiating feature.

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
