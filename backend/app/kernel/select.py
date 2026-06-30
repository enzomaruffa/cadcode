"""Identify + measure a picked entity, and synthesize a robust selector (§7).

Runs in the worker, against the OCP objects from the last run. The pick index
from three-cad-viewer aligns with ``ocp_tessellate.get_faces/edges/vertices``
order (verified), so index ``i`` is unambiguous.

Selector synthesis is the keystone of stable references: instead of persisting a
face *id* that breaks on re-run, we resolve the click to a build123d *selector
expression* (e.g. ``faces().sort_by(Axis.Z)[-1]``) that survives parameter
changes. This is where that capability *starts* (plan §2, §7) — extreme
axis-aligned faces resolve with high confidence; everything else falls back to a
labelled index selector.
"""

from __future__ import annotations

from typing import Any

from ocp_tessellate import convert as C

_TOL = 1e-3
_AXES = ("X", "Y", "Z")
# +axis -> human name for an extreme face along that axis
_DIR_NAME = {
    ("X", 1): "right",
    ("X", -1): "left",
    ("Y", 1): "back",
    ("Y", -1): "front",
    ("Z", 1): "top",
    ("Z", -1): "bottom",
}


def _v3(v: Any) -> list[float]:
    return [round(v.X, 4), round(v.Y, 4), round(v.Z, 4)]


def _axis_aligned(nx: float, ny: float, nz: float) -> tuple[str, int] | None:
    comps = (nx, ny, nz)
    for i, name in enumerate(_AXES):
        others = [abs(comps[j]) for j in range(3) if j != i]
        if abs(abs(comps[i]) - 1.0) < _TOL and max(others) < _TOL:
            return name, (1 if comps[i] > 0 else -1)
    return None


def _unwrap(shape: Any) -> Any:
    return shape.wrapped if hasattr(shape, "wrapped") else shape


def _face_selector(faces: list, index: int) -> tuple[str, str, str]:
    """(selector, confidence, description) for face ``index``."""
    f = faces[index]
    n = f.normal_at()
    aa = _axis_aligned(n.X, n.Y, n.Z)
    if aa is None:
        return f"faces()[{index}]", "low", f"{f.geom_type.name.lower()} face (no axis-aligned selector)"

    axis, sign = aa
    coord = {"X": lambda p: p.X, "Y": lambda p: p.Y, "Z": lambda p: p.Z}[axis]
    here = coord(f.center())
    # Faces sharing this axis-aligned normal direction.
    same = [
        coord(ff.center())
        for ff in faces
        if _axis_aligned(*[getattr(ff.normal_at(), a) for a in ("X", "Y", "Z")]) == aa
    ]
    name = _DIR_NAME[(axis, sign)]
    if same and here >= max(same) - _TOL:
        return f"faces().sort_by(Axis.{axis})[-1]", "high", f"{name} face (+{axis})"
    if same and here <= min(same) + _TOL:
        return f"faces().sort_by(Axis.{axis})[0]", "high", f"{name} face (-{axis})"
    return f"faces().filter_by(Axis.{axis})", "medium", f"face normal to {axis}"


def measure_selection(shape: Any, kind: str, index: int) -> dict[str, Any]:
    from build123d import Edge, Face, Solid, Vertex  # noqa: F401

    w = _unwrap(shape)
    out: dict[str, Any] = {"kind": kind, "index": index}

    try:
        if kind == "face":
            faces = [Face(f) for f in C.get_faces(w)]
            if not (0 <= index < len(faces)):
                return {"error": f"face index {index} out of range (0..{len(faces) - 1})"}
            f = faces[index]
            out["properties"] = {
                "geom_type": f.geom_type.name,
                "area": round(f.area, 4),
                "center": _v3(f.center()),
                "normal": _v3(f.normal_at()),
            }
            sel, conf, desc = _face_selector(faces, index)
            out.update(selector=sel, selector_confidence=conf, description=desc)

        elif kind == "edge":
            edges = [Edge(e) for e in C.get_edges(w)]
            if not (0 <= index < len(edges)):
                return {"error": f"edge index {index} out of range (0..{len(edges) - 1})"}
            e = edges[index]
            out["properties"] = {
                "geom_type": e.geom_type.name,
                "length": round(e.length, 4),
                "center": _v3(e.center()),
            }
            out.update(
                selector=f"edges()[{index}]", selector_confidence="low", description=f"{e.geom_type.name.lower()} edge"
            )

        elif kind == "vertex":
            verts = [Vertex(v) for v in C.get_vertices(w)]
            if not (0 <= index < len(verts)):
                return {"error": f"vertex index {index} out of range (0..{len(verts) - 1})"}
            v = verts[index]
            out["properties"] = {"position": [round(v.X, 4), round(v.Y, 4), round(v.Z, 4)]}
            out.update(selector=f"vertices()[{index}]", selector_confidence="low", description="vertex")

        else:  # whole solid
            from build123d import Shape

            out["kind"] = "solid"
            s: Any = Shape(w)
            props: dict[str, Any] = {}
            try:
                props["volume"] = round(s.volume, 4)
            except Exception:
                pass
            try:
                props["area"] = round(s.area, 4)
            except Exception:
                pass
            try:
                c = s.center()
                props["center"] = _v3(c)
            except Exception:
                pass
            out.update(properties=props, selector="", selector_confidence="n/a", description="solid")

        # Parent-solid context (volume / bbox), best effort.
        try:
            from build123d import Shape

            s = Shape(w)
            bb = s.bounding_box()
            out["solid"] = {
                "bbox": {
                    "min": _v3(bb.min),
                    "max": _v3(bb.max),
                    "size": [round(bb.size.X, 4), round(bb.size.Y, 4), round(bb.size.Z, 4)],
                },
            }
            vol = getattr(s, "volume", None)
            if vol:
                out["solid"]["volume"] = round(vol, 4)
        except Exception:
            pass

    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}

    return out
