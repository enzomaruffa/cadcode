"""Auto-extract top-level numeric parameters for slider editing (plan §6).

Detects module-level ``NAME = <number>`` assignments so the frontend can render
labelled sliders that write back to the source on drag — the easy 80% of mouse
interaction without the hard viewport-gesture work.
"""

from __future__ import annotations

import ast
from typing import Any


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
        params.append(
            {
                "name": target.id,
                "value": value,
                "line": node.lineno,
                "is_int": isinstance(value, int),
            }
        )
    return params
