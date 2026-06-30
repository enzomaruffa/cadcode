# cadcode — AI-native, code-first CAD

The canvas **is** code. A human, an AI agent, and the viewport all edit the same
build123d Python script — the one source of truth. Everything (geometry, history,
selection, the agent's worldview) is derived from or expressed in that script.

```
React + TS + Monaco + three-cad-viewer  ⟷  WebSocket  ⟷  FastAPI
                                                          ├─ sandboxed build123d kernel (subprocess)
                                                          ├─ PydanticAI agent (Gemini 3.5 Flash)
                                                          └─ git checkpoints
```

## What works (v0)

- **Live code → geometry.** Type build123d in Monaco; debounced re-run renders in three-cad-viewer. Errors show as inline squiggles and keep the last-good geometry.
- **Sandboxed kernel.** Scripts run in an isolated subprocess (AST import allowlist, restricted builtins, SIGALRM timeout, restart-on-breach).
- **Selection + measure.** Click geometry → properties + a synthesized robust **selector** (`faces().sort_by(Axis.Z)[-1]`), not a brittle id.
- **The agent.** Ask in English (or ⌘K inline) → it edits the source with robust selectors, respects shared design tokens, **self-corrects in the sandbox**, and proposes a diff you accept/reject. Preview the **geometry diff** (added = green, removed = red).
- **CAD-as-TDD.** `require(cond, msg)` specs show pass/fail and the agent must satisfy them (and can't weaken them).
- **Cohesion.** Shared `lib/design.py` tokens, a reusable parts catalog with build123d Joints + isometric thumbnails, and auto-extracted **parameter sliders**.
- **Printability.** Per-face overhang **heatmap** vs. a chosen build axis — the print-aware view.
- **History.** Durable git **checkpoints**, timeline + rollback, undo/redo.
- **Provenance + highlighting.** Cursor on a line → its faces glow; click a face → jump to its code.

## Run

See [CLAUDE.md](CLAUDE.md) for commands. TL;DR: `uv run uvicorn app.main:app --port 8787` in `backend/`, `npm run dev` in `frontend/`, open http://localhost:5173. The agent needs a `GEMINI_API_KEY`/`GOOGLE_API_KEY`.

Architecture rationale and milestone ladder: [`cad-platform-v0-plan.md`](cad-platform-v0-plan.md).
