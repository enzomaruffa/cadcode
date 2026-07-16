---
name: cad-design
description: How to design a part or project in this repo — build123d conventions, the ambient DSL, constants-driven geometry, printability rules learned from real prints, and the fit-proof (CAD-as-TDD) pattern. Use when creating or modifying parts, scenes, or whole projects under backend/projects/.
---

# Designing a part/project in cadcode

## Project anatomy

`backend/projects/<name>/{project.py, parts/*.py, scenes/*.py}`. Local `backend/projects/` is **gitignored** — prod copies live on the `/data/projects` volume (see the `cad-deploy` skill).

- **`project.py`** holds constants. A literal `NAME: Annotated[float, Range(lo, hi)] = v` becomes a UI slider; computed constants are skipped (that's a feature — derive freely). `int` literals work too. Group with comments; real-world floors go in `SPEC_*` constants (absolute printer physics — walls, fits, hole sizes — that scaled variants must NOT scale).
- **`parts/<x>.py`** must define a function named exactly `<x>`. Args are allowed but need defaults (previews call `x()` bare). Size/variant wrappers are one-liner files calling a shared parametric part.
- **`scenes/<s>.py`** run top-to-bottom, `show(obj, name=...)` each placed part. Scenes assert ASSEMBLY-level specs; parts assert only their own sanity (a part-level headroom spec once broke an intentionally-short scene).
- Imports are bare (`from project import X`, `from parts.y import y`) — the runner rewrites them to absolute. Cross-project imports bind to the OTHER project's constants.

## The ambient DSL (do not import these)

`show`, `require`, `print_hint` are bound on builtins at exec time. Linters flag them `F821` — that's the expected baseline, never "fix" it. `require(cond, "message")` is an executable spec; write specs as the promises that make the design work, not restatements of arithmetic.

## Style

- **Algebra mode** (Super Enzo's preference): `part = Cylinder(...) + Pos(...) * Cone(...) - cut`, not BuildPart contexts.
- Everything constants-driven; absolute mm in part code is a smell.
- Shared interfaces live in their own part module (e.g. a `mount.py` exposing `female_cut()`/`male_plug()`/`radii()`), so mating parts derive from the SAME numbers and cannot drift. Give it a tiny demo part function so it previews in the picker.
- One source of truth for placements: if a part cuts sockets at Locations, expose `slots()` and let scenes reuse them (`slot * female()` to cut, `slot * Rot(Z=lock) * plug_part()` to place).
- Colors: `part.color = Color("#hex")` at the end of the part fn.

## Printability rules (learned from real prints)

- **Bores that print vertically** get 45° cone roofs; **bores that print horizontally** get a teardrop roof (union a triangle prism whose apex points at print-up) — otherwise the slicer grows support INSIDE the fit, which is impossible to dig out.
- **Tapers**: axial seat slop = radial_fit / tan(taper_angle). A 0.15mm fit at 2° slides ~4mm deeper than nominal — budget geometry for it or use a **bayonet** (pins + L-slot + detent) when the joint must hold: it's a positive lock, prints fine, and the kinematics are provable (see fit-proofs below).
- Friction/push fits: ~0.1mm radial = snug hand fit, 0.25–0.3 = easy slip; bayonets run loose (0.3) because pins do the holding. Always expose the fit as a slider ("tune per printer").
- Boxes flush with cylinders: pull 1mm inside the tangent (tangent faces print as square artifacts). Parts wider than the bed: bolted half-lap halves, not slicer-split.
- Angled studs/bosses on sloped surfaces: keep the axis ≥45° from horizontal in the PRINT orientation; check weld depth vs the curved-surface sagitta (`r²/2R`) so the boss welds full-circle without piercing the inner wall.
- Granular-flow interiors (funnels/hoppers): mass-flow = walls ≥~65° from horizontal + a tangent-arc throat blend (loft a smooth r,z profile) — sharp internal ledges are where solids arch.
- External alignment signals for twist/slide joints: a raised ALIGN ridge on both halves + a LOCK dot at the locked angle (~1mm proud, MARK_SINK into the surface). Put them in the shared interface module so every socket/plug gets them for free.
- No `print_hint(flow=...)` on axisymmetric parts — flow must lie IN the layer plane, so an axial flow vector forces side-printing. Let the orientation search work; it prefers flat+stable now.

## Fit-proofs — CAD-as-TDD for mating parts

Prove assembly kinematics with boolean interference volumes inside `require`s (in a scene or a verify script). `a & b` returns None or an empty-ish solid when clear:

```python
def interf(deg, dz=0.0):
    x = socket_body & (Pos(0, 0, -dz) * Rot(Z=deg) * plug)
    return 0.0 if x is None else x.volume

require(interf(0) < 0.5, "inserts free")
require(interf(50) > 0.15, "detent bites mid-twist (the snap)")
require(interf(lock_angle()) < 0.5, "seats free when locked")
require(interf(lock_angle(), 2.0) > 3.0, "cannot pull straight out")
```

Same pattern for stacking (registered = free, shifted 1mm = collides), clips (seated free, pulled = blocked), dividers. These are the specs that catch real design bugs — write them before polishing cosmetics.

## Modular/grid systems

Compute mating features in GRID space, not box space: exterior = `n*UNIT - GAP` centered in its grid region, and every foot/slot/groove positioned per grid cell — that's what makes cross-size combinations (a 2×1 onto two 1×1s) register. Same-footprint nesting is impossible with vertical walls; the honest capacity is (W−1)×(D−1) units inside — encode it as a spec, don't pretend otherwise.
