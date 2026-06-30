"""Execute a build123d source string and tessellate the result.

This is the pure, side-effect-free core shared by every kernel implementation
(in-process now, sandboxed subprocess at M1). It re-execs the *whole* script —
deterministic and stateless, matching code-as-truth.

Objects reach the viewport one of two ways:
  * the script calls ``show(part, name=..., color=...)`` / ``show_object(...)``;
  * otherwise we auto-collect every top-level build123d shape in the namespace.

Color & material live in the code (``show(part, color=...)``) so they survive
re-runs and version in git (plan §6).
"""

from __future__ import annotations

import io
import traceback as tb_mod
from contextlib import redirect_stdout
from typing import Any

from app.kernel.result import RunResult
from app.tessellate import tessellate

SOURCE_FILENAME = "<cad-source>"


def _make_namespace() -> tuple[dict[str, Any], list[tuple[Any, str | None, Any]]]:
    """Build the exec globals, including the ``show`` collectors."""
    shown: list[tuple[Any, str | None, Any]] = []

    def show(*objs: Any, name: str | None = None, color: Any = None, **_kw: Any) -> Any:
        for i, obj in enumerate(objs):
            nm = name if (name and len(objs) == 1) else (f"{name}_{i}" if name else None)
            shown.append((obj, nm, color))
        return objs[0] if len(objs) == 1 else objs

    def show_object(obj: Any, name: str | None = None, options: dict | None = None, **_kw: Any) -> Any:
        color = (options or {}).get("color") if options else None
        shown.append((obj, name, color))
        return obj

    import build123d as _bd

    ns: dict[str, Any] = {
        "__name__": "__cad__",
        "__builtins__": __builtins__,
        "show": show,
        "show_object": show_object,
        "bd": _bd,
    }
    # `from build123d import *` so scripts can use the bare API.
    star = getattr(_bd, "__all__", None) or [n for n in dir(_bd) if not n.startswith("_")]
    for n in star:
        ns[n] = getattr(_bd, n)
    return ns, shown


def _is_renderable(obj: Any) -> bool:
    from ocp_tessellate import convert as C

    return bool(
        C.is_build123d_shape(obj)
        or C.is_build123d_compound(obj)
        or C.is_build123d(obj)
        or C.is_topods_shape(obj)
    )


def _auto_collect(ns: dict[str, Any]) -> list[tuple[Any, str | None, Any]]:
    """Fallback: render every top-level build123d shape the script defined."""
    collected: list[tuple[Any, str | None, Any]] = []
    for name, obj in ns.items():
        if name.startswith("_") or name in ("show", "show_object", "bd"):
            continue
        try:
            if _is_renderable(obj):
                collected.append((obj, name, None))
        except Exception:
            continue
    return collected


def _error_line(exc: BaseException) -> int | None:
    """Find the line in the user's source where the exception originated."""
    line: int | None = None
    for frame, lineno in tb_mod.walk_tb(exc.__traceback__):
        if frame.f_code.co_filename == SOURCE_FILENAME:
            line = lineno
    if line is None and isinstance(exc, SyntaxError) and exc.lineno:
        line = exc.lineno
    return line


def run_source(source: str) -> RunResult:
    """Execute ``source`` and return geometry or a structured error."""
    ns, shown = _make_namespace()
    buf = io.StringIO()
    try:
        code = compile(source, SOURCE_FILENAME, "exec")
        with redirect_stdout(buf):
            exec(code, ns)
    except BaseException as exc:  # noqa: BLE001 - report every failure to the editor
        line = _error_line(exc)
        msg = f"{type(exc).__name__}: {exc}"
        full = "".join(tb_mod.format_exception(type(exc), exc, exc.__traceback__))
        return RunResult.failure(msg, traceback=_clean_traceback(full), line=line, stdout=buf.getvalue())

    objects = shown or _auto_collect(ns)
    if not objects:
        return RunResult.success({}, {}, None, stdout=buf.getvalue())

    objs = [o for (o, _n, _c) in objects]
    names = [n for (_o, n, _c) in objects]
    colors = [c for (_o, _n, c) in objects]
    try:
        shapes, states, bbox = tessellate(objs, names=names, colors=colors)
    except Exception as exc:  # noqa: BLE001
        full = "".join(tb_mod.format_exception(type(exc), exc, exc.__traceback__))
        return RunResult.failure(f"Tessellation failed: {exc}", traceback=_clean_traceback(full), stdout=buf.getvalue())

    return RunResult.success(shapes, states, bbox, stdout=buf.getvalue())


def _clean_traceback(full: str) -> str:
    """Trim the exec scaffolding frames so the user sees their own code first."""
    lines = full.splitlines()
    out: list[str] = []
    skipping = True
    for ln in lines:
        if SOURCE_FILENAME in ln:
            skipping = False
        if not skipping:
            out.append(ln)
    return "\n".join(out) if out else full
