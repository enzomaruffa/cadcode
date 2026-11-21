---
name: cad-bambu
description: Turn cadcode parts into ready-to-print Bambu Studio projects (.3mf) for Super Enzo's printer — best orientation from the real slicer, plates packed, supports per part, his own presets and bed, sliced by Bambu to check, plus a time/filament/cost table across plate splits. Use whenever he asks for a 3mf, a Bambu project, "ready to print", plate layout, or how to split parts across plates.
---

# Bambu Studio projects from cadcode parts

Two scripts in `scripts/`. Orientation comes from cadcode's planner on prod (real
Orca slices); the project, packing and slice check come from Bambu Studio's own
CLI on the Mac (`/Applications/BambuStudio.app`).

## The traps this skill exists for

- **The Bambu CLI does not resolve preset `inherits`.** Loading
  `system/BBL/machine/Bambu Lab A1 0.4 nozzle.json` directly gave a 200x200 bed
  and generic speeds (a 2.2 h print estimated at 14 h, and auto-arrange split
  parts that fit one plate). `bambu_project.py` flattens every preset's inherits
  chain first. Never feed raw system presets to the CLI.
- **Support is per part, not per plate.** Grooves, snap hooks and clip seats
  must never get support, even on a plate where the big parts need tree
  supports. The script writes `enable_support=0` per object for
  `"supports": false` parts.
- **Tree supports spread ~20–25 mm over the bed past a part's walls.** Parts
  packed closer collide ("gcode path conflicts"). Supported parts get a 25 mm
  half-clearance by default, and the script widens and rebuilds on a conflict.
- **The planner can stand thin flat parts on edge** (bead `claude-cad-1l8`).
  `plan_oriented.py` flags it; force those parts flat (`=on side -Y`) and
  compare.
- Use Super Enzo's own presets and plate. The script reads
  `~/Library/Application Support/BambuStudio/BambuStudio.conf` (selected
  printer, process, first filament, current bed type). Today that is an A1
  0.4, 0.20mm Standard @BBL A1, Bambu PLA Basic, Textured PEI.

## Steps

1. **Orient on prod** (real slicer lives there; see `cad-deploy` for access):

   ```bash
   scp .claude/skills/cad-bambu/scripts/plan_oriented.py webrato-apt:/tmp/
   ssh webrato-apt 'docker cp /tmp/plan_oriented.py cad-kit-web-1:/tmp/ && \
     docker exec -w /data -e PYTHONPATH=/app/backend cad-kit-web-1 /app/backend/.venv/bin/python \
       /tmp/plan_oriented.py medbox_lean sleeve drawer lock_clip="on side -Y" --bed 256x256 --out /tmp/medbox_oriented && \
     docker cp cad-kit-web-1:/tmp/medbox_oriented /tmp/ && tar czf /tmp/medbox_oriented.tgz -C /tmp medbox_oriented'
   scp webrato-apt:/tmp/medbox_oriented.tgz "$CLAUDE_JOB_DIR/tmp/" && tar xzf ...
   ```

   Read `summary.json`: orientation, support grams and minutes per part, and
   warnings. Decide `supports` per part: true when the probe shows support
   grams on a part that can take them; false for small functional parts (snap
   fingers, grooves, threads) even if the probe shows a fraction of a gram.

2. **Write a spec** (`out`, `parts` with `stl`/`qty`/`supports`, and `options`
   = the plate splits to compare). Ask nothing: include one-plate, the obvious
   two-plate splits, and one plate per big part. Put the quick functional test
   parts (locks, clips, fit coupons) on their own plate in at least one option
   so he can test fits first. Full field list is in the script's docstring.

3. **Build, slice, compare:**

   ```bash
   uv run .claude/skills/cad-bambu/scripts/bambu_project.py spec.json
   ```

   It writes per option: `<option>.3mf` (the project to open),
   `<option>.gcode.3mf` (sliced check), plate previews `<option>_plate_N.png`
   and `_top_N.png`, and `results.json`, and prints the markdown table (hours
   per plate, total hours, grams, cost if `cost_per_kg`).

4. **Look before sending:** open one `_top_1.png` / `_plate_1.png` with Read and
   check the orientations and spacing. Then copy the `.3mf` files next to the
   part's STLs (`exports/<project>/bambu/`) and send them with SendUserFile, with
   the table in chat.

## What to tell him

- The table, then a one-line pick (usually: test parts first on a small plate,
  or one plate when nothing needs testing).
- Material is the same across splits; time differs by minutes. The real trade
  is risk (one failure loses one plate) and testing fits early.
- Filament: whatever Studio has selected. Flag parts that want PETG (snap fits)
  if that's not PLA's job.
- UNVERIFIED until printed: first layers of tall thin walls, and fits.
