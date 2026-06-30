"""Sandbox for executing untrusted build123d source (plan §1, §7).

Borrowed from the build123d-mcp posture — defense in depth, not a perfect jail
(no in-process Python sandbox is). The real isolation is the subprocess + OS
(SIGALRM timeout, restart-on-breach). These layers stop the obvious foot-guns
and give a clean error instead of a crash:

  * an AST pass rejects imports outside an allowlist *before* the code runs;
  * a guarded ``__import__`` re-checks at runtime (dynamic imports);
  * restricted builtins drop ``open`` / ``eval`` / ``exec`` / ``compile`` / ...
"""

from __future__ import annotations

import ast
import builtins as _builtins
from typing import Any

# Top-level modules a CAD script may import. Everything else is rejected.
ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "build123d",
        "bd_warehouse",
        "lib",  # the project's shared design tokens + parts catalog (plan §5)
        "math",
        "cmath",
        "statistics",
        "random",
        "itertools",
        "functools",
        "operator",
        "typing",
        "dataclasses",
        "enum",
        "collections",
        "copy",
        "numpy",
        "re",
        "fractions",
        "decimal",
        "string",
    }
)

# Builtins that enable sandbox escape / IO — removed from the exec namespace.
_BLOCKED_BUILTINS: frozenset[str] = frozenset(
    {
        "open",
        "eval",
        "exec",
        "compile",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "help",
        "memoryview",
        "__import__",  # replaced by the guarded version below
    }
)


class SandboxError(Exception):
    """Raised when the source violates the sandbox policy. Carries a line."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


def _top(module: str | None) -> str:
    return (module or "").split(".", 1)[0]


def check_imports(source: str) -> None:
    """Static pass: reject disallowed or relative imports before execution."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Let the normal exec path report syntax errors with a clean traceback.
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top(alias.name)
                if top not in ALLOWED_MODULES:
                    raise SandboxError(
                        f"import of '{alias.name}' is not allowed in the sandbox", getattr(node, "lineno", None)
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise SandboxError("relative imports are not allowed in the sandbox", getattr(node, "lineno", None))
            top = _top(node.module)
            if top not in ALLOWED_MODULES:
                raise SandboxError(
                    f"import from '{node.module}' is not allowed in the sandbox", getattr(node, "lineno", None)
                )


def _guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
    if _top(name) not in ALLOWED_MODULES:
        raise ImportError(f"import of '{name}' is not allowed in the sandbox")
    return _builtins.__import__(name, *args, **kwargs)


def safe_builtins() -> dict[str, Any]:
    """A copy of the standard builtins with dangerous names removed and a
    guarded ``__import__`` installed."""
    base = vars(_builtins).copy()
    for name in _BLOCKED_BUILTINS:
        base.pop(name, None)
    base["__import__"] = _guarded_import
    return base
