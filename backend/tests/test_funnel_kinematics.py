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
