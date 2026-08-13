"""require_flow: leak detection by void-connectivity flood fill.

Synthetic fixtures model the funnel v1 failure: a plug sitting INSIDE a bore
with an annular fit gap and a side slot draining it (leaks), versus a cup
sealing the same interface from OUTSIDE (passes).
"""

from app.kernel.runner import run_source

# A vertical tube (bore Ø20) with a closed floor and a Ø8 exit hole, a plug
# inside leaving a 0.8mm annular gap, and a 2mm side slot from the gap to the
# exterior — the v1 leak topology (flow SHOULD go bore → plug bore → exit hole).
V1_STYLE = """
from build123d import Align, Box, Cylinder, Pos

tube = Cylinder(15, 30, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Cylinder(
    10, 30.5, align=(Align.CENTER, Align.CENTER, Align.MIN)
)
tube += Pos(0, 0, -3) * Cylinder(15, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
tube -= Cylinder(4, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER))
plug = Pos(0, 0, 0) * Cylinder(9.2, 12, align=(Align.CENTER, Align.CENTER, Align.MIN))
plug -= Cylinder(4, 30, align=(Align.CENTER, Align.CENTER, Align.CENTER))
slot = Pos(12, 0, 5) * Box(8, 2, 4)
tube -= slot
show(tube, name="tube")
show(plug, name="plug")

flow_port(point=(0, 0, 20), kind="inlet")
flow_port(point=(0, 0, -3), kind="outlet")
"""

# The same tube sealed by an external cup: the gap opens upward, above the flow.
V2_STYLE = """
from build123d import Align, Cylinder, Pos

tube = Cylinder(15, 30, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Cylinder(
    10, 32, align=(Align.CENTER, Align.CENTER, Align.MIN)
)
cup = Pos(0, 0, -10) * (
    Cylinder(18, 22, align=(Align.CENTER, Align.CENTER, Align.MIN))
    - Pos(0, 0, 2) * Cylinder(15.5, 22, align=(Align.CENTER, Align.CENTER, Align.MIN))
    - Cylinder(4, 44, align=(Align.CENTER, Align.CENTER, Align.MIN))
)
show(tube, name="tube")
show(cup, name="cup")

flow_port(point=(0, 0, 20), kind="inlet")
flow_port(point=(0, 0, -9), kind="outlet")
"""


def _spec(res, label):
    return next(s for s in res.specs if label in s["message"])


def test_v1_style_gap_leaks_through_the_slot():
    # Tight explicit region (just past the tube surface) so the leak exit is
    # localizable at the slot mouth instead of flooding the whole air volume.
    res = run_source(
        V1_STYLE + "require_flow(min_gap=0.4, region=((-15.2, -15.2, -3.0), (15.2, 15.2, 20.0)),"
        ' label="no leak past the plug")\n'
    )
    assert res.ok, res.error
    spec = _spec(res, "no leak past the plug")
    assert not spec["passed"], spec["message"]
    chk = res.flow[0]
    assert chk["leaked_cells"] > 0
    # The leak exits near the slot mouth: a reported point ON the slot patch.
    assert any(p[0] > 10 and 1 < p[2] < 9 for p in chk["leak_points"]), chk["leak_points"]


def test_v2_style_cup_seals_and_reaches_outlet():
    res = run_source(V2_STYLE + 'require_flow(min_gap=0.4, label="sealed by the cup")\n')
    assert res.ok, res.error
    spec = _spec(res, "sealed by the cup")
    assert spec["passed"], spec["message"]
    assert res.flow[0]["reaches_outlet"]


def test_blocked_outlet_fails_with_no_path():
    src = V2_STYLE.replace("- Cylinder(4, 44", "- Cylinder(0.1, 44") + 'require_flow(min_gap=0.4, label="blocked")\n'
    res = run_source(src)
    assert res.ok, res.error
    spec = _spec(res, "blocked")
    assert not spec["passed"]
    assert "no void path" in spec["message"]


def test_region_over_cap_fails_loudly():
    src = V1_STYLE + 'require_flow(min_gap=0.02, region=((-100,-100,-100),(100,100,100)), label="too fine")\n'
    res = run_source(src)
    assert res.ok, res.error
    spec = _spec(res, "too fine")
    assert not spec["passed"]
    assert "region" in spec["message"] and "min_gap" in spec["message"]


def test_sub_gap_channel_counts_as_sealed():
    # Narrow the annular gap below min_gap: 9.9 plug in a 10 bore = 0.1mm gap,
    # min_gap 0.5 → treated as sealed, so the only path is the outlet.
    src = V1_STYLE.replace("Cylinder(9.2, 12", "Cylinder(9.9, 12") + (
        'require_flow(min_gap=0.5, label="capillary gap is sealed")\n'
    )
    res = run_source(src)
    assert res.ok, res.error
    assert _spec(res, "capillary gap is sealed")["passed"]


def test_missing_ports_fail():
    res = run_source("from build123d import Box\nshow(Box(5, 5, 5), name='b')\nrequire_flow(label='no ports')\n")
    assert res.ok, res.error
    assert not _spec(res, "no ports")["passed"]
