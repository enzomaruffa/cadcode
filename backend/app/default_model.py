"""The starter build123d script a new session opens with.

Explicit imports (no ``import *``), inline slider-range annotations, and
executable specs — so the round-trip, sliders, and CAD-as-TDD are all visibly
working the moment the app loads.
"""

DEFAULT_SOURCE = """\
from typing import Annotated
from build123d import Box, Cylinder, Pos, fillet, Axis
from lib.design import FILLET, M3_CLEARANCE_D  # shared design tokens (plan §5)
from lib.params import Range  # typed slider ranges (plan §6)

# Part-specific parameters — the typed Range(...) annotation drives each slider.
WIDTH: Annotated[float, Range(20, 160)] = 80
DEPTH: Annotated[float, Range(20, 120)] = 50
HEIGHT: Annotated[float, Range(4, 40)] = 12

# Algebra mode: build objects as expressions and combine with + - & operators.
plate = Box(WIDTH, DEPTH, HEIGHT)

# four mounting holes, sized from the shared M3 token
for x in (-30, 30):
    for y in (-15, 15):
        plate -= Pos(x, y) * Cylinder(M3_CLEARANCE_D / 2, HEIGHT)

# soften the vertical edges with the shared fillet token
plate = fillet(plate.edges().filter_by(Axis.Z), radius=FILLET)

show(plate, name="plate", color="#9aa7ff")

# Executable specs (CAD-as-TDD): the agent must keep these passing.
require(plate.volume > 1000, "plate must have enough material")
require(max(plate.bounding_box().size.X, plate.bounding_box().size.Y) <= 160, "footprint must fit in 160mm")
"""
