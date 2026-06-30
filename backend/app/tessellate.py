"""build123d objects -> three-cad-viewer payload (plan §2).

    build123d objects
       -> ocp-tessellate (mesh + edges + face/edge/vertex indices)
       -> plain-array JSON (numpy_to_json)
       -> { shapes, states } the viewer renders

The geometry math lives in ocp-tessellate; this module is the glue + protocol.
``states`` is derived from the shape tree's leaf ids (``{"/Group/box": [1, 1]}``
means faces + edges visible).
"""

from __future__ import annotations

import json
from typing import Any

from ocp_tessellate import convert as C


def _decode_refs(instances: list, shapes: dict) -> None:
    """Resolve instance refs in the shape tree in place (mirrors
    ocp_tessellate.convert.export_three_cad_viewer_js)."""

    def walk(obj: dict) -> None:
        typ = None
        for attr in list(obj.keys()):
            if attr == "parts":
                for part in obj["parts"]:
                    walk(part)
            elif attr == "type":
                typ = obj["type"]
            elif attr == "shape" and typ == "shapes":
                shape = obj["shape"]
                if isinstance(shape, dict) and shape.get("ref") is not None:
                    obj["shape"] = instances[shape["ref"]]

    walk(shapes)


def _build_states(shapes: dict) -> dict[str, list[int]]:
    """Walk the (decoded) shape tree and mark every leaf visible: faces + edges."""

    states: dict[str, list[int]] = {}

    def walk(obj: dict) -> None:
        if not isinstance(obj, dict):
            return
        if obj.get("parts") is not None:
            for part in obj["parts"]:
                walk(part)
        else:
            sid = obj.get("id")
            if sid is not None:
                states[sid] = [1, 1]

    walk(shapes)
    return states


def tessellate(
    objs: list[Any],
    names: list[str | None] | None = None,
    colors: list[Any] | None = None,
) -> tuple[dict, dict[str, list[int]], dict | None]:
    """Tessellate build123d/OCP objects into ``(shapes, states, bbox)``.

    ``shapes`` and ``states`` are exactly what three-cad-viewer's
    ``viewer.render(shapes, states)`` consumes.
    """

    if not objs:
        return {"version": 3, "parts": [], "name": "Group", "id": "/Group", "loc": None, "bb": None}, {}, None

    part_group, instances = C.to_ocpgroup(*objs, names=names, colors=colors)
    instances, shapes, _mapping = C.tessellate_group(part_group, instances)
    _decode_refs(instances, shapes)

    shapes_json = json.loads(C.numpy_to_json(shapes))
    states = _build_states(shapes_json)
    bbox = shapes_json.get("bb")
    return shapes_json, states, bbox
