"""Auto-extract top-level numeric parameters for slider editing (plan §6).

Detects module-level ``NAME = <number>`` assignments so the frontend can render
labelled sliders that write back to the source on drag — the easy 80% of mouse
interaction without the hard viewport-gesture work.
"""

from __future__ import annotations

import ast
import re
from typing import Any

# Optional inline range annotation: `WIDTH = 28  # [10, 100]` or `# [10, 100, 2]`
_RANGE_RE = re.compile(
    r"#\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,\s*(-?\d+(?:\.\d+)?)\s*)?\]"
)


def _num(s: str) -> float | int:
    f = float(s)
    return int(f) if f.is_integer() else f


def _annotation(line: str) -> dict[str, float | int] | None:
    m = _RANGE_RE.search(line)
    if not m:
        return None
    out: dict[str, float | int] = {"min": _num(m.group(1)), "max": _num(m.group(2))}
    if m.group(3) is not None:
        out["step"] = _num(m.group(3))
    return out


def _range_from_annotation(annotation: ast.expr | None) -> dict[str, float | int] | None:
    """Parse `Annotated[<type>, Range(min, max[, step=...])]` -> {min,max[,step]}."""
    if not isinstance(annotation, ast.Subscript):
        return None
    base = annotation.value
    if not (isinstance(base, ast.Name) and base.id == "Annotated"):
        return None
    sl = annotation.slice
    elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
    for el in elts:
        if isinstance(el, ast.Call) and isinstance(el.func, ast.Name) and el.func.id == "Range":
            nums = [_number(a) for a in el.args]
            kw = {k.arg: _number(k.value) for k in el.keywords if k.arg}
            out: dict[str, float | int] = {}
            if len(nums) >= 1 and nums[0] is not None:
                out["min"] = nums[0]
            if len(nums) >= 2 and nums[1] is not None:
                out["max"] = nums[1]
            step = (nums[2] if len(nums) >= 3 else None) or kw.get("step")
            if step is not None:
                out["step"] = step
            return out if "min" in out and "max" in out else None
    return None


def _number(node: ast.expr) -> float | int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    # negative literal: -5
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _number(node.operand)
        if inner is not None:
            return -inner
    return None


def extract_params(source: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    params: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in tree.body:  # top-level only
        # Plain `NAME = 80` or typed `NAME: Annotated[float, Range(...)] = 80`.
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, ann_node, value_node = node.targets[0], None, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, ann_node, value_node = node.target, node.annotation, node.value
        else:
            continue

        value = _number(value_node)
        if value is None or target.id in seen:
            continue
        seen.add(target.id)
        entry: dict[str, Any] = {
            "name": target.id,
            "value": value,
            "line": node.lineno,
            "is_int": isinstance(value, int),
        }
        # Range from a typed annotation takes precedence over a `# [..]` comment.
        rng = _range_from_annotation(ann_node)
        if rng is None:
            line_text = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
            rng = _annotation(line_text)
        if rng:
            entry.update(rng)
        params.append(entry)
    return params
