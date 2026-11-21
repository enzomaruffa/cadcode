"""Run INSIDE the prod container: pick each part's print orientation with the real
slicer (plan_print probe=True) and export it as a bed-ready STL.

    docker exec -w /data -e PYTHONPATH=/app/backend cad-kit-web-1 \
      /app/backend/.venv/bin/python /tmp/plan_oriented.py <project> <part>[=<orient>] ... [--bed 256x256] [--out DIR]

`<orient>` forces an orientation label ("as-is", "upside-down", "on side -Y", ...).
Each STL is centred on X/Y with its lowest point at z=0. A summary JSON is
written next to them (orientation, support grams, minutes per part).
"""

import json
import os
import sys

from build123d import Pos, export_stl

from app.printplan import plan_print

args = sys.argv[1:]
bed = (256.0, 256.0)
out = None
if "--bed" in args:
    i = args.index("--bed")
    w, h = args[i + 1].lower().split("x")
    bed = (float(w), float(h))
    del args[i : i + 2]
if "--out" in args:
    i = args.index("--out")
    out = args[i + 1]
    del args[i : i + 2]
project, specs = args[0], args[1:]
out = out or f"/tmp/{project}_oriented"
os.makedirs(out, exist_ok=True)

items, names = [], []
for s in specs:
    name, _, orient = s.partition("=")
    names.append(name)
    items.append({"project": project, "name": name, "qty": 1, "orient": orient or "auto"})

r = plan_print(
    items,
    bed=bed,
    want_objects=True,
    strategy="material",
    supports=True,
    probe=True,
    slice_settings={"supports": True, "layer_height": 0.2},
)
summary = []
for name, st, obj in zip(names, r["stats"], r["_objects"], strict=True):
    bb = obj.bounding_box()
    obj = Pos(-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z) * obj
    size = obj.bounding_box().size
    export_stl(obj, f"{out}/{name}.stl")
    probe = st.get("probe") or {}
    row = {
        "name": name,
        "orientation": st.get("orientation"),
        "support_g": probe.get("support_g"),
        "model_g": probe.get("model_g"),
        "minutes": probe.get("minutes"),
        "size": [round(size.X, 1), round(size.Y, 1), round(size.Z, 1)],
    }
    # A thin part standing on edge is almost always a planner miss: say so.
    if min(size.X, size.Y) < 4.0 and size.Z > 3 * min(size.X, size.Y):
        row["warning"] = "thin part standing on edge: try forcing it flat and compare"
    summary.append(row)
json.dump(
    {"probed": r.get("probed"), "slicer": r.get("slicer_name"), "parts": summary},
    open(f"{out}/summary.json", "w"),
    indent=1,
)
print(json.dumps(summary, indent=1))
print("wrote", out)
