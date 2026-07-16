---
name: cad-verify
description: How to run, verify, and visually inspect projects locally — run_project preview wrappers, the verify-all loop, rendering ISO images (cairosvg) including half-sections, and the lint baseline. Use after any change to backend/projects/ or when you need images of geometry for the user.
---

# Verifying & rendering cadcode projects locally

Run everything from `backend/` with `uv run python` (repo rule: uv, never bare python).

## Run a part or scene

`app.project_runner.run_project(project, kind, name, overrides, preview_source)` is the same entry the app uses. Parts define a function but never call `show()`, so preview them with a wrapper:

```python
from app.project_runner import run_project
src = f'from parts.{n} import {n}\nshow({n}(), name="{n}")'
res = run_project("myproj", "part", n, None, src)      # parts need the wrapper
res = run_project("myproj", "scene", s)                 # scenes run as-is
res.get("ok")  # False → res["error"] has the traceback; require() failures land here too
```

Verify-all loop: iterate every part + scene, print OK/ERR, assert zero failures. Do this after every geometry change and again inside the prod container after deploying (see `cad-deploy`).

**Ad-hoc probes**: `preview_source` can be ANY script (it gets import-rewritten). Use it to run fit-checks or build cut-away views without touching project files. `print()` inside sandboxed runs is NOT forwarded — surface values via `require(cond, f"msg {value}")` failures or compute in the outer script instead.

## Render images for the user

GL-free renderer: `app.thumbnail.iso_svg_shapes(res["shapes"], size=N)` → SVG string. To PNG needs brew cairo (env var must be set BEFORE the process starts):

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run --with cairosvg python script.py
```

```python
cairosvg.svg2png(bytestring=svg.encode(), write_to=path, output_width=680,
                 output_height=680, background_color="#141210")  # bg matters — default is transparent
```

- **Half-section** (show internals): subtract a big aligned `Box` from the part in the preview script, e.g. `funnel() - Pos(0,0,z)*Box(400,400,400, align=(Align.MIN, Align.CENTER, Align.CENTER))`. Rotate the cut part so the opening faces the fixed ISO camera (there is no camera control — rotate the MODEL, e.g. `Rot(Z=180) * cut`).
- **Thin slice** (wall profile): `part & Box(400, 2, 400)` then `Rot(X=90)`.
- For raw meshes there's `iso_svg_mesh(verts, tris, size, cells=24)` with grid-decimation (~9k tris → hundreds; low-poly reads fine at thumbnail size).
- Send PNGs with SendUserFile; render at ≥560px on a dark bg.

## Checks

```bash
cd backend && uv run ruff format projects/<p>/ && uv run ruff check projects/<p>/
```

Expected baseline: `F821` for the ambient `show`/`require`/`print_hint` — do NOT fix those (dishrack has the same). Fix everything else (`I001` import order via `--fix`, `B905` zip strict, ...). Backend app code must also pass `uv run ty check`; project files aren't type-checked.
