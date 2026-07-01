"""Design tokens — CSS variables for CAD (plan §5).

Every part imports these. Change a token and every part re-derives. The agent
reads them and respects them, so edits land with *your* wall thickness, fit, and
fastener sizes instead of generic defaults.
"""

from dataclasses import dataclass

# Wall / shell
WALL = 2.0  # default wall thickness (mm)
FLOOR = 2.0  # default floor thickness (mm)

# Edge treatment
FILLET = 1.5  # default outer fillet radius (mm)
CHAMFER = 1.0  # default chamfer length (mm)

# Fits / clearances
CLEARANCE = 0.2  # slip fit gap (mm)
PRESS_FIT = -0.05  # interference for a press fit (mm)

# M3 fastener family (mm)
M3_CLEARANCE_D = 3.4  # clearance hole for an M3 screw
M3_TAP_D = 2.5  # tapping hole for M3
M3_HEAD_D = 6.0  # socket-head cap screw head diameter
M3_BOSS_D = 7.0  # recommended boss outer diameter around an M3

# 3D-printing
NOZZLE = 0.4  # nozzle diameter (mm)
LAYER = 0.2  # layer height (mm)
OVERHANG_LIMIT = 45.0  # max unsupported overhang angle (deg)


# --- materials (physical-properties simulation) -----------------------------
# Material lives in the *code* (``show(part, material=PLA)``) so mass, cost, and
# buoyancy re-derive on every run and version in git — same invariant as color.
@dataclass(frozen=True)
class Material:
    """A print material. ``density`` is g/cm³, ``cost_per_kg`` is currency/kg,
    ``filament_d`` is FDM stock diameter in mm (0 for resin / non-filament)."""

    name: str
    density: float
    cost_per_kg: float
    filament_d: float = 1.75


PLA = Material("PLA", 1.24, 22.0)
PETG = Material("PETG", 1.27, 25.0)
ABS = Material("ABS", 1.04, 22.0)
RESIN = Material("Resin", 1.10, 55.0, filament_d=0.0)  # SLA/DLP — no filament

DEFAULT_MATERIAL = PLA
MATERIALS = {m.name.upper(): m for m in (PLA, PETG, ABS, RESIN)}


def tokens() -> dict[str, float]:
    """All scalar tokens as a dict (for the agent / UI to read). Materials are
    excluded — they aren't scalars."""
    return {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))}
