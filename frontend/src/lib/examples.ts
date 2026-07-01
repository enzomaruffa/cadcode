// Loadable example scripts (opened via the File menu). These double as living
// docs: the hinged-lid assembly exercises both new simulation view modes —
// "motion" (joint sweep + collision/clearance) and "physical" (mass / balance).

export interface Example {
  name: string;
  label: string;
  source: string;
}

const HINGED_LID = `from build123d import Align, Axis, Box, Location, RevoluteJoint, RigidJoint
from lib.design import CLEARANCE, PLA

# A hinged lid on a base plate — build123d ALGEBRA mode (objects are expressions
# combined with + - & ). Switch the view to "motion" to watch the lid swing and
# check clearance, or "physical" to read its mass, balance, and cost.
base = Box(60, 40, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
lid = Box(60, 40, 3, align=(Align.CENTER, Align.MIN, Align.MIN))

# Revolute hinge along the base's back edge, lifted a touch for a printable gap.
RevoluteJoint("hinge", to_part=base, axis=Axis((0, 20, 5.2), (1, 0, 0)),
              angle_reference=(0, 1, 0), angular_range=(0, 90))
RigidJoint("pivot", to_part=lid, joint_location=Location((0, 0, 0), (-90, 0, 0)))

# Snap the lid onto the hinge (each body is shown as its own leaf so it can move).
base.joints["hinge"].connect_to(lid.joints["pivot"], angle=20)

show(base, name="base", color="#8a94b0", material=PLA)
show(lid, name="lid", color="#c98a5a", material=PLA)


def motion(t: float) -> dict:
    """Swing the lid from 20deg (ajar) to 90deg (upright). Return each body's
    world Location keyed by its show() name — the sim animates these rigidly."""
    angle = 20 + t * 70
    base.joints["hinge"].connect_to(lid.joints["pivot"], angle=angle)
    return {"base": base.location, "lid": lid.location}


# Executable MOTION spec: the lid must never gouge the base through the swing.
# Lower the hinge axis Z or lengthen the lid to watch the sim flag a collision.
require(min_clearance_through_motion >= CLEARANCE, f"lid keeps >= {CLEARANCE}mm through the swing")
`;

export const EXAMPLES: Example[] = [{ name: "hinged_lid", label: "Hinged lid (motion)", source: HINGED_LID }];
