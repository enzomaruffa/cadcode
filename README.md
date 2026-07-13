# cadcode — AI-native, code-first CAD

The canvas **is** code. A human, an AI agent, and the viewport all edit the same
build123d Python script — the one source of truth. Everything (geometry, history,
selection, print plans, the agent's worldview) is derived from or expressed in
that script. Live at **cad.enzomaruffa.dev**.

```
React + TS + Monaco + @cadcode/viewer (three.js)  ⟷  WebSocket  ⟷  FastAPI
                                                                   ├─ sandboxed build123d kernel (subprocess)
                                                                   ├─ PydanticAI project agent (Gemini 3.5 Flash)
                                                                   ├─ print planner + headless OrcaSlicer
                                                                   └─ git checkpoints
```

## What works

- **Live code → geometry.** Type build123d in Monaco; debounced re-run renders in our own three.js viewer (PBR + IBL + GTAO, precise per-face picking, section planes, on-screen measurement, a measured ruler grid). Errors show as inline squiggles and keep the last-good geometry.
- **Projects.** Multi-file `project.py` (constants → auto **parameter sliders**) + `parts/` + `scenes/`; parts import each other across projects; rename refactors every reference. Everything constants-driven — change `TOWER_WIDTH` and the whole family re-derives.
- **The agent.** Ask in English (or ⌘K inline) → search/replace edits across project files, **dry-runs in the sandbox and self-corrects** before you see anything, optionally sanity-checks its result with a vision pass, and proposes a diff you accept/reject (geometry diff: added = green, removed = red).
- **CAD-as-TDD.** `require(cond, msg)` specs show pass/fail; parts assert their own sanity, scenes assert assembly clearances; the agent must satisfy them and can't weaken them.
- **Print intelligence.** `print_hint()` declares intent (water-flow layer direction, cosmetic faces); the planner searches ~48 orientations with removability-weighted support cost (support trapped in a bore ≫ open support), optionally **ground-truths the finalists with the real slicer** (exact tree-support grams parsed from G-code), packs as few bed-sized plates as possible (with orientation swap-to-fit), and exports STL/3MF/G-code per plate with time/filament/cost.
- **Physics & animation.** Rapier-powered sim in the viewport (concave collision, joint articulation, exploded view) — and "bake" writes the resulting pose back **as code**.
- **Editor intelligence.** build123d autocomplete + hover docs + signature help (introspected, not hand-written), import-aware part completion with auto-import.
- **Cohesion.** Shared `lib/design.py` tokens (walls, fits, M3 fasteners, materials), a parts catalog with joints + shaded isometric thumbnails.
- **Printability view, provenance, history.** Per-face overhang heatmap; cursor on a line → its faces glow (and back); durable git checkpoints, timeline + rollback.

## Run

See [CLAUDE.md](CLAUDE.md) for commands, deployment, and project conventions. TL;DR: `uv run uvicorn app.main:app --port 8787` in `backend/`, `npm run dev` in `frontend/`, open http://localhost:5173. The agent needs a `GEMINI_API_KEY`/`GOOGLE_API_KEY`.

Architecture rationale and the original milestone ladder: [`cad-platform-v0-plan.md`](cad-platform-v0-plan.md).
