---
name: cad-print-planner
description: The print planner's APIs and behavior — plan_print, orientation search + ground-truth probing, slicing one orientation, per-part overrides, thumbnails/plate-layout endpoints. Use when answering print questions, comparing orientations, or extending the print pipeline/UI.
---

# Print planner — APIs & behavior

Code: `app/printplan.py` (+ `app/kernel/print_time.py` heuristics, `app/kernel/slicer.py` real slicer). HTTP surface in `app/main.py` under `/print/*`.

## Core call

```python
from app.printplan import plan_print
r = plan_print(items, bed=(220,220), want_objects=False, strategy="material",
               supports=True, probe=False, slice_settings=None)
# items: [{"project": "funnel", "name": "funnel", "qty": 1, "orient": "auto"}]
# orient override: "as-is" | "upside-down" | "on side ±X/±Y" | any candidate label
```
Returns per-part `stats` (chosen `orientation`, `orientations` = the whole ranked candidate field with per-orientation metrics, `probe` = winner's real grams/minutes, split hints) and `plates` (each with `layout` = top-down footprints for the 2D map). Strategies: `material` (least support), `plates`, `fastest`.

## Orientation search — the contract

Phase 1 scores ~48 down-directions on cheap mesh metrics (overhang area, removability ray-cast, height, footprint, **bed-contact area** = stability). The heuristic ranks by raw overhang + tippiness penalty — the enclosure penalty is only a mild tiebreak (it used to bury flat orientations; a funnel's socket bores read as "trapped support" and three near-identical tilts won).

**Invariant (probe=True): every bed-fitting PRINCIPAL orientation is always ground-truth sliced** — never gated behind the heuristic — plus top tilts, in parallel (`_PROBE_WORKERS`), cached by (mesh sha, label, settings). Ranking uses REAL support grams/minutes with a stability tiebreak. If you touch this code, keep that invariant; it's the fix for "the planner picked a weird tilt".

Probe needs the real slicer → prod container only (see `cad-deploy`).

## Slice ONE orientation (orientation A/B tests)

```python
from build123d import Axis, Pos
from app.kernel.slicer import slice_minutes
o = obj.rotate(Axis((0,0,0),(1,0,0)), 180)          # e.g. mouth-down
bb = o.bounding_box(); o = Pos(-(bb.min.X+bb.max.X)/2, -(bb.min.Y+bb.max.Y)/2, -bb.min.Z) * o
res = slice_minutes([o], bed=(220,220), timeout_s=240, settings={"supports": True, "layer_height": 0.2})
# → {minutes, filament_g, support_g, model_g, slicer} — support_g parsed from G-code feature blocks
```
Compare orientations at IDENTICAL settings; report grams + minutes. This settles "which way should I print it" arguments with data.

## HTTP endpoints (what the modal uses)

- `POST /print/plan` — plan (body: items/bed/strategy/supports/probe + slicer settings)
- `POST /print/slice` — exact per-plate time (feeds estimator calibration)
- `POST /print/gcode`, `POST /print/export` (stl|3mf|step), per-plate via `plate`
- `GET /print/thumb?project=&name=` — decimated iso SVG for picker rows
- `POST /print/orient-thumbs` {project, name, bed} — {label: svg} per orientation, lazy-loaded when the UI gallery expands; merged onto `stats[].orientations` by label
- `GET /print/parts`, `/print/printers`, `/print/filaments`, `POST /print/profile`

## Gotchas

- `print_hint(flow=...)` must lie IN the layer plane; hints outrank strategy — don't hint axisymmetric parts.
- Thumbnails: never inline full meshes; `app.thumbnail.decimate()` (grid clustering) then `iso_svg_mesh`.
- Reality check for users: tiny bores clog solids regardless of planning; heavy-overhang parts get an honest `suggest_split` with a build123d `split()` recipe.
