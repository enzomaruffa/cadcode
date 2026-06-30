"""Reusable parametric parts (plan §5).

Each part is a function with a typed signature + docstring (the catalog the agent
reads via ``list_library_parts``). Parts publish named build123d ``Joint``s so
assembly is *snapping joints together* (``connect_to``) rather than hand-computing
transforms — connection, not coordinate guessing.
"""

from __future__ import annotations

from build123d import Align, BuildPart, Cylinder, Hole, Location, RigidJoint

from lib.design import M3_BOSS_D, M3_TAP_D, WALL

__all__ = ["m3_boss", "standoff"]


def m3_boss(height: float = 8.0, boss_d: float = M3_BOSS_D, tap_d: float = M3_TAP_D):
    """An M3 mounting boss: a cylinder with a blind tapping hole, plus a
    `base` RigidJoint at the bottom face for snapping onto a wall.

    Args:
        height: boss height (mm).
        boss_d: outer diameter (mm), defaults to the M3_BOSS_D token.
        tap_d:  tapping-hole diameter (mm), defaults to the M3_TAP_D token.
    """
    with BuildPart() as boss:
        Cylinder(radius=boss_d / 2, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN))
        Hole(radius=tap_d / 2, depth=height - WALL)
        RigidJoint("base", joint_location=Location((0, 0, 0)))
    return boss.part


def standoff(height: float = 10.0, outer_d: float = 6.0, bore_d: float = 3.4):
    """A hollow standoff (through-bore spacer) with `top` and `bottom` RigidJoints.

    Args:
        height:  standoff length (mm).
        outer_d: outer diameter (mm).
        bore_d:  through-bore diameter (mm), e.g. M3 clearance.
    """
    with BuildPart() as so:
        Cylinder(radius=outer_d / 2, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN))
        Hole(radius=bore_d / 2)
        RigidJoint("bottom", joint_location=Location((0, 0, 0)))
        RigidJoint("top", joint_location=Location((0, 0, height)))
    return so.part
