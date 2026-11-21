# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy-stl"]
# ///
"""Build ready-to-print Bambu Studio projects (.3mf) from bed-ready STLs, one per
plate-split option, slice each with the real Bambu engine and print a
time/filament table.

    uv run bambu_project.py spec.json

spec.json:
{
  "out": "/abs/dir",                        # projects, sliced copies, previews, results.json
  "parts": [                                # STLs already oriented (plan_oriented.py)
    {"name": "sleeve", "stl": "/abs/sleeve.stl", "qty": 1, "supports": true},
    {"name": "clip",   "stl": "/abs/clip.stl",   "qty": 3, "supports": false}
  ],
  "options": {                              # optional; default = everything on one plate
    "1_plate": [["sleeve", "clip"]],
    "2_plates": [["clip"], ["sleeve"]]
  },
  "machine": "...", "process": "...", "filament": "...",   # optional; default = what Studio has selected
  "bed_type": "Textured PEI Plate",          # optional; default = Studio's current plate
  "gap": 10, "supports_gap": 50,             # mm between parts; tree supports need more room
  "cost_per_kg": 90                          # optional, for the cost column
}

Why this exists: the Bambu CLI does NOT resolve preset `inherits`. A system
preset loaded directly keeps only its own keys and falls back to generic defaults
for the rest (an A1 became a 200x200 bed with wrong speeds). So every preset is
flattened here first. Support is set per object, so parts that must stay clean
(grooves, snap hooks) never get support even on a plate where others need it.
"""

import json
import os
import re
import subprocess
import sys
import zipfile

import numpy as np
from stl import mesh

BS = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
ROOT = os.path.expanduser("~/Library/Application Support/BambuStudio")
BED_TYPES = {"1": "Cool Plate", "2": "Engineering Plate", "3": "High Temp Plate", "4": "Textured PEI Plate"}


# ---------------------------------------------------------------- presets
def _preset_dirs(kind):
    dirs = [f"{ROOT}/system/BBL/{kind}"]
    user = f"{ROOT}/user"
    if os.path.isdir(user):
        dirs += [f"{user}/{u}/{kind}" for u in os.listdir(user) if os.path.isdir(f"{user}/{u}/{kind}")]
    return dirs


def _index(kind):
    idx = {}
    for d in _preset_dirs(kind):
        for f in os.listdir(d):
            if f.endswith(".json"):
                try:
                    j = json.load(open(f"{d}/{f}"))
                except Exception:
                    continue
                if "name" in j:
                    idx[j["name"]] = j
    return idx


def flatten(kind, name):
    idx = _index(kind)
    if name not in idx:
        sys.exit(f"{kind} preset not found: {name}")
    chain, cur = [], idx[name]
    while True:
        chain.append(cur)
        parent = cur.get("inherits")
        if not parent:
            break
        if parent not in idx:
            sys.exit(f"{kind} preset {cur['name']} inherits missing {parent}")
        cur = idx[parent]
    flat = {}
    for j in reversed(chain):
        flat.update(j)
    flat.pop("inherits", None)
    flat["name"] = name
    flat["from"] = "system"
    return flat


def studio_defaults():
    conf = json.load(open(f"{ROOT}/BambuStudio.conf"))
    p = conf.get("presets", {})

    def find(o, key):
        if isinstance(o, dict):
            if key in o:
                return o[key]
            for v in o.values():
                r = find(v, key)
                if r is not None:
                    return r
        return None

    bed = find(conf, "curr_bed_type")
    return p.get("machine"), p.get("process"), (p.get("filaments") or [None])[0], BED_TYPES.get(str(bed))


# ---------------------------------------------------------------- packing
def footprint(stl):
    v = mesh.Mesh.from_file(stl).vectors.reshape(-1, 3)
    lo, hi = v.min(0), v.max(0)
    return (
        float(hi[0] - lo[0]),
        float(hi[1] - lo[1]),
        float(hi[2] - lo[2]),
        (float(lo[0] + hi[0]) / 2, float(lo[1] + hi[1]) / 2),
    )


def rect_of(points):
    xy = np.array([[float(a) for a in p.split("x")] for p in points])
    return xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()


def pack(instances, area, keepouts, gap, supports_gap, margin=3.0):
    """Bottom-left placement of (w, d) footprints; centre positions, or None.

    Every part keeps a clearance of half its own gap; two neighbours sit the sum
    of their halves apart. Tree supports spread ~20+ mm over the bed beyond a
    supported part's walls, so supported parts get the big half. The bed edge
    only needs `margin` (supports past the edge are just clipped)."""
    x0, y0, x1, y1 = area
    L, T, R, B = x0 + margin, y0 + margin, x1 - margin, y1 - margin
    half = [(supports_gap if it["supports"] else gap) / 2.0 for it in instances]
    order = sorted(range(len(instances)), key=lambda i: -instances[i]["w"] * instances[i]["d"])
    placed = []  # (x0, y0, x1, y1, half)
    pos = [None] * len(instances)

    def clear(r, h):
        for q in placed:
            g = h + q[4]
            if r[0] < q[2] + g and r[2] > q[0] - g and r[1] < q[3] + g and r[3] > q[1] - g:
                return False
        for k in keepouts:
            if r[0] < k[2] + h and r[2] > k[0] - h and r[1] < k[3] + h and r[3] > k[1] - h:
                return False
        return True

    for i in order:
        it, h = instances[i], half[i]
        xs = {L} | {q[2] + q[4] + h for q in placed} | {k[2] + h for k in keepouts}
        ys = {T} | {q[3] + q[4] + h for q in placed} | {k[3] + h for k in keepouts}
        spot = None
        for y in sorted(ys):
            for x in sorted(xs):
                r = (x, y, x + it["w"], y + it["d"])
                if r[2] <= R and r[3] <= B and clear(r, h):
                    spot = r
                    break
            if spot:
                break
        if spot is None:
            return None
        placed.append((*spot, h))
        pos[i] = ((spot[0] + spot[2]) / 2, (spot[1] + spot[3]) / 2)
    return pos


# ---------------------------------------------------------------- main
def main():
    spec = json.load(open(sys.argv[1]))
    out = spec["out"]
    os.makedirs(out, exist_ok=True)
    m_name, p_name, f_name, bed_default = studio_defaults()
    m_name = spec.get("machine") or m_name
    p_name = spec.get("process") or p_name
    f_name = spec.get("filament") or f_name
    bed_type = spec.get("bed_type") or bed_default

    machine, process, filament = flatten("machine", m_name), flatten("process", p_name), flatten("filament", f_name)
    paths = {}
    for key, j in (("machine", machine), ("process", process), ("filament", filament)):
        paths[key] = f"{out}/_{key}.json"
        json.dump(j, open(paths[key], "w"), indent=1)
    paths["process_sup"] = f"{out}/_process_supports.json"
    json.dump(
        dict(process, enable_support="1", support_type=process.get("support_type") or "tree(auto)"),
        open(paths["process_sup"], "w"),
        indent=1,
    )

    area = rect_of(machine["printable_area"])
    keepouts = []
    for key in ("bed_exclude_area", "head_wrap_detect_zone"):
        pts = [p for p in (machine.get(key) or []) if p]
        if len(pts) >= 3:
            keepouts.append(rect_of(pts))

    parts = {}
    for p in spec["parts"]:
        w, d, h, c = footprint(p["stl"])
        parts[p["name"]] = dict(
            p, w=w, d=d, h=h, c=c, qty=int(p.get("qty", 1)), supports=bool(p.get("supports", False))
        )
    options = spec.get("options") or {"1_plate": [list(parts)]}

    dia = float((filament.get("filament_diameter") or ["1.75"])[0])
    dens = float((filament.get("filament_density") or ["1.24"])[0])
    g_per_m = np.pi * (dia / 2) ** 2 * dens  # mm2 * g/cm3 = g/m
    cost_kg = spec.get("cost_per_kg")
    gap, sgap = float(spec.get("gap", 10)), float(spec.get("supports_gap", 50))

    def build(opt, plates, sgap):
        alist, any_sup, bad = {"plates": []}, False, None
        for pi, names in enumerate(plates):
            inst = [parts[n] for n in names for _ in range(parts[n]["qty"])]
            pos = pack(inst, area, keepouts, gap, sgap)
            if pos is None:
                bad = f"plate {pi + 1} does not fit the bed"
                break
            objs = []
            for it, (cx, cy) in zip(inst, pos, strict=True):
                any_sup |= it["supports"]
                o = {
                    "path": it["stl"],
                    "count": 1,
                    "filaments": [1],
                    "assemble_index": [0],
                    "pos_x": [cx],
                    "pos_y": [cy],
                    "pos_z": [0],
                }
                if not it["supports"]:
                    o["print_params"] = {"enable_support": "0"}
                objs.append(o)
            alist["plates"].append({"plate_name": f"plate {pi + 1}", "need_arrange": False, "objects": objs})
        if bad:
            return {"error": bad}
        lst, proj, sliced = f"{out}/{opt}.assemble.json", f"{out}/{opt}.3mf", f"{out}/{opt}.gcode.3mf"
        json.dump(alist, open(lst, "w"), indent=1)
        proc = paths["process_sup"] if any_sup else paths["process"]
        cmd = [
            BS,
            "--orient",
            "0",
            "--arrange",
            "0",
            "--load-settings",
            f"{paths['machine']};{proc}",
            "--load-filaments",
            paths["filament"],
            "--load-assemble-list",
            lst,
            "--export-3mf",
            proj,
        ]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            return {"error": "build failed"}
        if bed_type:
            tmp = proj + ".tmp"
            with zipfile.ZipFile(proj) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for it in zin.infolist():
                    data = zin.read(it.filename)
                    if it.filename == "Metadata/project_settings.config":
                        cfg = json.loads(data)
                        cfg["curr_bed_type"] = bed_type
                        data = json.dumps(cfg, indent=4).encode()
                    zout.writestr(it, data)
            os.replace(tmp, proj)
        r = subprocess.run([BS, "--slice", "0", "--export-3mf", sliced, proj], capture_output=True, text=True)
        if r.returncode != 0:
            errs = [ln for ln in (r.stdout + r.stderr).splitlines() if "[error]" in ln][-2:]
            return {"error": f"slice failed rc={r.returncode}: " + " | ".join(e.split("]")[-1].strip() for e in errs)}
        z = zipfile.ZipFile(sliced)
        info = z.read("Metadata/slice_info.config").decode()
        rows = []
        for n, blk in enumerate(re.findall(r"<plate>(.*?)</plate>", info, re.S), start=1):
            sec = int(re.search(r'prediction" value="(\d+)"', blk).group(1))
            m = sum(float(x) for x in re.findall(r'used_m="([\d.]+)"', blk))
            g = m * g_per_m
            row = {
                "plate": n,
                "hours": round(sec / 3600, 2),
                "grams": round(g),
                "objects": len(re.findall(r"<object ", blk)),
            }
            if cost_kg:
                row["cost"] = round(g / 1000 * cost_kg, 2)
            rows.append(row)
            for fn in (f"Metadata/plate_{n}.png", f"Metadata/top_{n}.png"):
                if fn in z.namelist():
                    open(f"{out}/{opt}_{os.path.basename(fn)}", "wb").write(z.read(fn))
        return {"plates": rows, "supports_gap": sgap}

    results = {}
    for opt, plates in options.items():
        # Tree supports can still reach a neighbour; widen and rebuild on a conflict.
        for extra in (0, 20, 40):
            res = build(opt, plates, sgap + extra)
            if "path conflicts" not in res.get("error", ""):
                break
        results[opt] = res

    json.dump(
        {"machine": m_name, "process": p_name, "filament": f_name, "bed_type": bed_type, "options": results},
        open(f"{out}/results.json", "w"),
        indent=1,
    )
    print(f"{m_name} | {p_name} | {f_name} | {bed_type}\n")
    print("| option | plates | hours per plate | total h | total g |" + (" cost |" if cost_kg else ""))
    print("|---|---:|---|---:|---:|" + ("---:|" if cost_kg else ""))
    for opt, r in results.items():
        if "error" in r:
            print(f"| {opt} | - | {r['error']} | - | - |" + (" - |" if cost_kg else ""))
            continue
        ps = r["plates"]
        th, tg = sum(p["hours"] for p in ps), sum(p["grams"] for p in ps)
        line = f"| {opt} | {len(ps)} | {' + '.join(str(p['hours']) for p in ps)} | {th:.2f} | {tg} |"
        if cost_kg:
            line += f" {sum(p['cost'] for p in ps):.2f} |"
        print(line)


if __name__ == "__main__":
    main()
