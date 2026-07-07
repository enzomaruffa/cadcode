"""Bake physics poses back into the source — the code-as-truth round-trip.

After playing the physics sandbox (settle, drag, articulate), each shown part
has a new world pose. `bake_source` takes the per-part delta transforms (the
move from the code pose to the current pose, as position + axis-angle) and wraps
the matching `show(expr, name=…)` argument with a build123d `Location(...) * …`,
using the AST so nested expressions are handled correctly. The result re-runs to
exactly the settled arrangement — the sim becomes script.
"""

from __future__ import annotations

import ast
from typing import Any

_EPS_POS = 1e-3
_EPS_ANGLE = 1e-2  # degrees


def _significant(pose: dict[str, Any]) -> bool:
    px, py, pz = pose.get("pos", [0, 0, 0])
    return abs(px) > _EPS_POS or abs(py) > _EPS_POS or abs(pz) > _EPS_POS or abs(pose.get("angle", 0)) > _EPS_ANGLE


def bake_source(source: str, poses: dict[str, dict[str, Any]]) -> str:
    """Wrap each `show(expr, name=N)` whose N has a (non-trivial) pose delta with
    `Location((dx,dy,dz),(ax,ay,az),deg) * (expr)`. Returns the rewritten source
    (unchanged on parse error). Idempotent-ish: only significant deltas apply."""
    applied = {k: v for k, v in poses.items() if isinstance(v, dict) and _significant(v)}
    if not applied:
        return source
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    # Is `Location` already available? (check BEFORE the transform mutates the tree)
    from_build123d: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("build123d"):
            from_build123d.update(a.name for a in n.names)
    has_location = "Location" in from_build123d or "*" in from_build123d

    changed = False

    class Baker(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.Call:
            self.generic_visit(node)
            if not (isinstance(node.func, ast.Name) and node.func.id in ("show", "show_object")):
                return node
            if not node.args:
                return node
            name = next(
                (kw.value.value for kw in node.keywords if kw.arg == "name" and isinstance(kw.value, ast.Constant)),
                None,
            )
            pose = applied.get(name) if isinstance(name, str) else None
            if pose is None:
                return node
            px, py, pz = pose["pos"]
            ax, ay, az = pose["axis"]
            ang = pose["angle"]
            loc = ast.parse(
                f"Location(({px:.4f}, {py:.4f}, {pz:.4f}), ({ax:.5f}, {ay:.5f}, {az:.5f}), {ang:.4f})", mode="eval"
            ).body
            node.args[0] = ast.BinOp(left=loc, op=ast.Mult(), right=node.args[0])
            nonlocal changed
            changed = True
            return node

    new_tree = Baker().visit(tree)
    if not changed:
        return source
    ast.fix_missing_locations(new_tree)
    baked = ast.unparse(new_tree)
    # Ensure Location resolves even when the script imported only specific names.
    if not has_location:
        baked = "from build123d import Location\n" + baked
    return baked
