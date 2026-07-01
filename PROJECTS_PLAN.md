# cadcode v2 — Projects, Scenes, and folder-first storage

Direction for the next architecture step. Building incrementally; each slice ships on its own.

## The model

A **Project** is a folder on disk — this doubles as the "use a folder instead of git" storage:

```
projects/<project>/
  project.py        # project-wide constants + parameters (importable everywhere)
  parts/<part>.py   # reusable parametric part functions (def part(...) -> Part)
  scenes/<scene>.py # assemble parts (import + connect joints); physics, render, animation, interaction
```

- **Parts** — the callable functions we already have (`from lib.parts import x`), but now scoped per project. `Save to library` writes here.
- **Scenes** — a script that imports parts and assembles them; adds physics (the concurrent motion/physical work), pretty rendering, animations, and interaction (drag, collisions).
- **Project constants/params** (`project.py`) — one place that affects every part + scene in the project (extends the `lib.design` token idea to per-project scope).
- **Library** = the set of projects + their parts; a project can import parts from another project (`from projects.<other>.parts import x`).

## Slices (independent, shippable)

1. **Git optional → folder** *(this pass)*: checkpoints/history degrade to plain folder snapshots when git is off/unavailable (`CAD_GIT=0`), so the app never hard-depends on git. — `backend/app/gitstore.py` (+ a `FolderStore` fallback), `session.py` picks the store.
2. **Agent diffs in the code** *(this pass)*: when a patch is pending, the code pane shows an inline Monaco diff (current vs proposed) instead of a plain editor. — `frontend/src/components/EditorPane.tsx`.
3. **Project data model + folder storage**: `projects/` on disk, a `Project` service (list/create/read/write files), `/projects` API. Parts/scenes are files in a project.
4. **File view**: a collapsible file tree (project ▸ parts / scenes / project.py) — lives above/ў the agent pane (reuse that column; toggle between "files" and "agent"). Selecting a file opens it as an editor tab.
5. **Project constants**: `project.py` edited via the existing tokens modal, scoped to the active project; parts/scenes import from it.
6. **Scenes**: a scene doc type that imports parts + assembles; runs through the physics/animation/interaction pipeline (built alongside the concurrent motion work).
7. **Cross-project parts**: parts are importable across projects; the library modal groups by project.
