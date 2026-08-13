"""require_motion: kinematic sweeps as inline specs.

Fixture: a 4mm pin sliding through a 20mm block's 6mm slot. Centered it passes
clean; rotated 30° about X it must strike the slot walls mid-travel.
"""

from app.kernel.runner import run_source, run_source_lean

FIXTURE = """
from build123d import Align, Box, Pos, Rot

block = Box(20, 20, 20) - Box(6, 6, 30)
pin = Pos(0, 0, -25) * Box(4, 4, 10)
show(block, name="block")
show(pin, name="pin")
"""

PASS_SWEEP = 'require_motion(pin, slide((0, 0, 1), 40), against=[block], label="pin slides through the slot")\n'
FAIL_SWEEP = (
    "tilted = Rot(X=30) * pin\n"
    'show(tilted, name="tilted")\n'
    'require_motion(tilted, slide((0, 0, 1), 40), against=[block], label="tilted pin still fits")\n'
)


def _spec(res, label):
    return next(s for s in res.specs if label in s["message"])


def test_straight_slide_passes():
    res = run_source(FIXTURE + PASS_SWEEP)
    assert res.ok, res.error
    assert _spec(res, "pin slides through the slot")["passed"]


def test_tilted_pin_fails_with_worst_pose():
    res = run_source(FIXTURE + FAIL_SWEEP)
    assert res.ok, res.error
    spec = _spec(res, "tilted pin still fits")
    assert not spec["passed"]
    assert "worst contact" in spec["message"]
    sweep = next(m for m in res.motion if m["label"].startswith("tilted"))
    assert sweep["worst_contact"] > 0.01
    assert 0.0 < sweep["worst_t"] < 1.0


def test_motion_serializes_with_leaf_ids():
    res = run_source(FIXTURE + PASS_SWEEP)
    assert len(res.motion) == 1
    sweep = res.motion[0]
    assert "moving_obj" not in sweep
    assert sweep["moving"] in (res.states or {})
    assert len(sweep["poses"]) == 12
    pose = sweep["poses"][-1]["pose"]
    assert len(pose) == 2 and len(pose[0]) == 3 and len(pose[1]) == 4
    d = res.as_dict()
    assert d["motion"][0]["moving"] == sweep["moving"]


def test_ambient_from_imported_module(tmp_path, monkeypatch):
    helper = tmp_path / "sweep_helper.py"
    helper.write_text(
        "def prove(pin, block):\n"
        '    require_motion(pin, slide((0, 0, 1), 40), against=[block], label="helper sweep")\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    src = FIXTURE + "from sweep_helper import prove\nprove(pin, block)\n"
    res = run_source(src)
    assert res.ok, res.error
    assert _spec(res, "helper sweep")["passed"]


def test_lean_run_reports_spec_without_motion_payload():
    res = run_source_lean(FIXTURE + PASS_SWEEP)
    assert res["ok"], res.get("error")
    assert any(s["passed"] and "pin slides" in s["message"] for s in res["specs"])
    assert "motion" not in res


def test_bad_path_fails_spec_not_run():
    res = run_source(FIXTURE + 'require_motion(pin, lambda t: 1 + t, against=[block], label="broken path")\n')
    assert res.ok, res.error
    spec = _spec(res, "broken path")
    assert not spec["passed"]
