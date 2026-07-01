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
7. **Cross-project parts** *(done)*: any project can import another's part with `from projects.<other>.parts.<name> import <name>`. The runner materializes every project into one `projects` package namespace (`app/project_runner.py`), rewriting each project's local imports (`from project`/`from parts.x`) to absolute `projects.<pid>.…` so projects coexist and each part keeps resolving its OWN constants. The library modal groups by project and shows both the intra-project and cross-project import forms.

## Multi-file agent (confirmed representation)

The agent edits the **whole project**, not one buffer — and editing a part may cascade to other parts. Representation:

- The edit is a **multi-file patch**: `edits: [{path, new_source}]` across `project.py` / `parts/*` / `scenes/*` (new files allowed). One file is the **run target** (the active scene, or a part being worked on) — that's what renders.
- The agent runs an **edit → run → view → edit** loop: it reads the whole project (`read_project`), proposes edits, **dry-runs the run target with the edits applied** (`app/project_runner.run_project`, project + lib on `sys.path`), sees geometry/errors, and refines — iterating until it's happy, then returns the multi-file patch.
- Review = per-file diff; Accept writes all files (one git checkpoint) + re-runs the run target. Undo/checkpoint snapshot the whole project.
- Foundation: `app/project_runner.py` (materialize project + overrides in a temp dir, run the target with the project importable). Then `app/project_agent.py` (the multi-file agent) + `/projects/{project}/agent`. UI: project-scoped chat + multi-file diff review.
