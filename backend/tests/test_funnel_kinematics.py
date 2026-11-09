"""Regression: the funnel's bayonet twist is proven by require_motion.

The v2 (external cup) scene must pass its sweep; the v1 geometry — the plug that
physically jammed at ~56° on a real print — must FAIL the same sweep. Projects
live on a gitignored volume, so these skip wherever the fixtures are absent.
"""

from pathlib import Path

import pytest

PROJECTS = Path(__file__).resolve().parents[1] / "projects"

V1_SWEEP = """
from build123d import Rot
from parts.funnel import funnel
from parts.mount import lock_angle
from parts.nozzle import nozzle

f = funnel()
n = nozzle(2.0)
show(f, name="funnel")
show(n, name="nozzle")
require_motion(n, turn("Z", 0.0, lock_angle()), against=[f], max_contact=0.5,
               label="nozzle twists to lock without jamming")
"""


@pytest.mark.skipif(not (PROJECTS / "funnel").is_dir(), reason="funnel project not on this machine")
def test_v2_assembled_scene_passes():
    from app.project_runner import run_project

    r = run_project("funnel", "scene", "assembled")
    assert r.get("ok"), r.get("error")
    bad = [s["message"] for s in r.get("specs", []) if not s["passed"]]
    assert not bad, bad
    sweeps = r.get("motion") or []
    assert any("twists to lock" in m["label"] for m in sweeps)


@pytest.mark.skipif(not (PROJECTS / "funnel_before").is_dir(), reason="v1 fixture not on this machine")
def test_v1_geometry_fails_the_same_sweep():
    from app.project_runner import run_project

    r = run_project("funnel_before", "part", "funnel", None, V1_SWEEP)
    assert r.get("ok"), r.get("error")
    spec = next(s for s in r.get("specs", []) if "twists to lock" in s["message"])
    assert not spec["passed"], "the v1 bayonet jam must be caught by the sweep"
    sweep = next(m for m in (r.get("motion") or []) if "twists to lock" in m["label"])
    assert sweep["worst_contact"] > 0.5


V1_FLOW = """
from build123d import Pos, Rot
from parts.funnel import funnel
from parts.mount import lock_angle
from parts.nozzle import nozzle
from project import BAYO_FIT

f = funnel()
# Worst-case SEATED pose, not nominal: the pin rides a channel 2·BAYO_FIT taller
# than itself, so a hanging nozzle sags by the play — which is what opened the
# collar interface on the real print. Nominal CAD kisses shut and hides the leak.
n = Pos(0, 0, -2 * BAYO_FIT) * Rot(Z=lock_angle()) * nozzle(2.0)
show(f, name="funnel")
show(n, name="nozzle")

# The wetted bore just above the seated plug in, the nozzle's internal bore at
# the bottom of the region out. min_gap 0.15 keeps the 0.3mm fit gap reliably
# OPEN (a channel is only guaranteed open from ~2x min_gap at this resolution).
flow_port(point=(0.0, 0.0, 13.9), kind="inlet")
flow_port(point=(0.0, 0.0, -2.0), kind="outlet")
require_flow(min_gap=0.15, region=((-16.0, -16.0, -2.0), (16.0, 16.0, 14.0)),
             label="v1 joint holds liquid")
"""


@pytest.mark.skipif(not (PROJECTS / "funnel_before").is_dir(), reason="v1 fixture not on this machine")
def test_v1_geometry_leaks_where_the_print_leaked():
    from app.project_runner import run_project

    r = run_project("funnel_before", "part", "funnel", None, V1_FLOW)
    assert r.get("ok"), r.get("error")
    spec = next(s for s in r.get("specs", []) if "v1 joint holds liquid" in s["message"])
    assert not spec["passed"], "IMG_3393's leak path must be caught by the flood fill"
    chk = next(c for c in (r.get("flow") or []) if "v1 joint" in c["label"])
    assert chk["leaked_cells"] > 0
