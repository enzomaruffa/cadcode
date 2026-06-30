"""The starter build123d script a new session opens with.

Explicit imports (no ``import *``), inline slider-range annotations, and
executable specs — so the round-trip, sliders, and CAD-as-TDD are all visibly
working the moment the app loads.
"""

DEFAULT_SOURCE = '''\
from build123d import BuildPart, Box, Hole, Locations, fillet, Axis
from lib.design import FILLET, M3_CLEARANCE_D  # shared design tokens (plan §5)

# Part-specific parameters — the `# [min, max]` annotation drives each slider.
WIDTH = 80   # [20, 160]
DEPTH = 50   # [20, 120]
HEIGHT = 12  # [4, 40]

with BuildPart() as plate:
    Box(WIDTH, DEPTH, HEIGHT)
    # four mounting holes, sized from the shared M3 token
    with Locations((-30, -15), (30, -15), (-30, 15), (30, 15)):
        Hole(radius=M3_CLEARANCE_D / 2)
    # soften the vertical edges with the shared fillet token
    fillet(plate.edges().filter_by(Axis.Z), radius=FILLET)

show(plate.part, name="plate", color="#9aa7ff")

# Executable specs (CAD-as-TDD): the agent must keep these passing.
require(plate.part.volume > 1000, "plate must have enough material")
require(max(plate.part.bounding_box().size.X, plate.part.bounding_box().size.Y) <= 160, "footprint must fit in 160mm")
'''
