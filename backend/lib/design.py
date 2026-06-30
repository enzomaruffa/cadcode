"""Design tokens — CSS variables for CAD (plan §5).

Every part imports these. Change a token and every part re-derives. The agent
reads them and respects them, so edits land with *your* wall thickness, fit, and
fastener sizes instead of generic defaults.
"""

# Wall / shell
WALL = 2.0          # default wall thickness (mm)
FLOOR = 2.0         # default floor thickness (mm)

# Edge treatment
FILLET = 1.5        # default outer fillet radius (mm)
CHAMFER = 1.0       # default chamfer length (mm)

# Fits / clearances
CLEARANCE = 0.2     # slip fit gap (mm)
PRESS_FIT = -0.05   # interference for a press fit (mm)

# M3 fastener family (mm)
M3_CLEARANCE_D = 3.4   # clearance hole for an M3 screw
M3_TAP_D = 2.5         # tapping hole for M3
M3_HEAD_D = 6.0        # socket-head cap screw head diameter
M3_BOSS_D = 7.0        # recommended boss outer diameter around an M3

# 3D-printing
NOZZLE = 0.4        # nozzle diameter (mm)
LAYER = 0.2         # layer height (mm)
OVERHANG_LIMIT = 45.0  # max unsupported overhang angle (deg)


def tokens() -> dict[str, float]:
    """All tokens as a dict (for the agent / UI to read)."""
    return {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))}
