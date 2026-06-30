"""The starter build123d script a new session opens with.

Small enough to read at a glance, exercises solids + a fillet + a hole + color,
so the round-trip is visibly working the moment the app loads.
"""

DEFAULT_SOURCE = '''\
from build123d import *

# Design tokens (plan §5): change one, the whole part re-derives.
WIDTH = 80
DEPTH = 50
HEIGHT = 12
FILLET = 4
HOLE_D = 6

with BuildPart() as plate:
    Box(WIDTH, DEPTH, HEIGHT)
    # four mounting holes
    with Locations((-30, -15), (30, -15), (-30, 15), (30, 15)):
        Hole(radius=HOLE_D / 2)
    # soften the vertical edges
    fillet(plate.edges().filter_by(Axis.Z), radius=FILLET)

show(plate.part, name="plate", color="#9aa7ff")
'''
