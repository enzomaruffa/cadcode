// Loadable example scripts (opened via the File menu). These double as living
// docs: the turntable assembly exercises both new simulation view modes —
// "motion" (joint sweep + collision/clearance) and "physical" (mass / balance).

export interface Example {
  name: string;
  label: string;
  source: string;
}

const TURNTABLE = `from build123d import Align, Axis, Box, Cylinder, Location, Pos, RevoluteJoint, RigidJoint
from lib.design import CLEARANCE, PLA

# A turntable (build123d ALGEBRA mode — objects are expressions combined with
# + - & ). An arm spins about the Z axis on a base disk and must clear a fixed
# post. Switch the view to "motion" to watch it spin and check clearance, or
# "physical" to read its mass, balance, and cost.
base = Cylinder(radius=34, height=3, align=(Align.CENTER, Align.CENTER, Align.MIN))
post = Pos(0, 26, 0) * Cylinder(radius=4, height=16, align=(Align.CENTER, Align.CENTER, Align.MIN))
base = base + post  # the post is fixed to the base
arm = Pos(0, 0, 3) * Box(36, 6, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))

# A revolute joint spins the arm about the base's central Z axis.
RevoluteJoint("spin", to_part=base, axis=Axis((0, 0, 3), (0, 0, 1)), angular_range=(0, 180))
RigidJoint("hub", to_part=arm, joint_location=Location((0, 0, 0)))
base.joints["spin"].connect_to(arm.joints["hub"], angle=0)

show(base, name="base", color="#8a94b0", material=PLA)
show(arm, name="arm", color="#c98a5a", material=PLA)


def motion(t: float) -> dict:
    """Spin the arm 0->180deg. Return each body's world Location keyed by its
    show() name — the sim animates these rigidly and flags any collision."""
    base.joints["spin"].connect_to(arm.joints["hub"], angle=t * 180)
    return {"base": base.location, "arm": arm.location}


# Executable MOTION spec: the arm must clear the post through the whole spin.
# Lengthen the arm (try 48) to watch the sim flag a collision and fail this spec.
require(min_clearance_through_motion >= CLEARANCE, "arm clears the post through the spin")
`;

export const EXAMPLES: Example[] = [{ name: "turntable", label: "Turntable (motion)", source: TURNTABLE }];
