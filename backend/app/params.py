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
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = _number(node.value)
        if value is None or target.id in seen:
            continue
        seen.add(target.id)
        entry: dict[str, Any] = {
            "name": target.id,
            "value": value,
            "line": node.lineno,
            "is_int": isinstance(value, int),
        }
        line_text = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
        ann = _annotation(line_text)
        if ann:
            entry.update(ann)
        params.append(entry)
    return params
