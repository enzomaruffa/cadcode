"""Provenance spike: which source line produced which face (plan §7, M6).

The tractable path the plan calls for: trace execution and, after each line,
fingerprint the faces of every build123d builder/shape in scope. The first line
at which a face's fingerprint appears is the line that produced it. At the end we
map every final face (in ``get_faces`` order, matching the tessellation) back to
its originating line.

Best-effort and geometric: a fingerprint is (center, area) rounded. Faces that
can't be attributed fall back to the ``show(...)`` line. Good enough to drive
bidirectional code<->geometry highlighting; not a solution to topological naming
in general.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from typing import Any

from ocp_tessellate import convert as C

from app.kernel.runner import SOURCE_FILENAME, _auto_collect, _make_namespace

_MAX_FACES = 2000  # safety bound for the per-line snapshot cost

# Deterministic per-line palette (indexed by line number); shared with the
# frontend recolor so code->geometry highlighting stays consistent.
PROVENANCE_PALETTE = ["#5a7bd6", "#4ab0a0", "#c08a4a", "#9a6fc0", "#5aa0c0", "#b06a8a", "#7aa84a", "#c0644a"]


def _fp(face: Any) -> tuple:
    c = face.center()
    return (round(c.X, 1), round(c.Y, 1), round(c.Z, 1), round(face.area, 1))


def _faces_of(value: Any) -> list[Any]:
    """build123d faces of a builder's current part or a shape, else []."""
    try:
        from build123d import BuildPart, Face

        if isinstance(value, BuildPart):
            part = value.part
            return list(part.faces()) if part is not None else []
        if isinstance(value, Face):
            return [value]
        faces_attr = getattr(value, "faces", None)
        if callable(faces_attr):
            fs = faces_attr()
            return list(fs)
    except Exception:
        return []
    return []


def _snapshot(frame_locals: dict, line: int | None, fp_line: dict) -> None:
    if line is None:
        return
    count = 0
    for value in list(frame_locals.values()):
        for face in _faces_of(value):
            try:
                fp = _fp(face)
            except Exception:
                continue
            if fp not in fp_line:
                fp_line[fp] = line
            count += 1
            if count > _MAX_FACES:
                return


def run_with_provenance(source: str, sandbox: bool = True) -> dict[str, Any]:
    """Return {ok, face_lines, show_line, error}. ``face_lines[i]`` is the source
    line that produced face ``i`` (in get_faces order)."""
    builtins_override = None
    if sandbox:
        from app.kernel.sandbox import SandboxError, check_imports, safe_builtins

        try:
            check_imports(source)
        except SandboxError as exc:
            return {"ok": False, "error": f"SandboxError: {exc}"}
        builtins_override = safe_builtins()

    ns, shown, _specs = _make_namespace(builtins_override)

    fp_line: dict[tuple, int] = {}
    state: dict[str, int | None] = {"prev": None}

    def tracer(frame, event, _arg):
        if frame.f_code.co_filename != SOURCE_FILENAME:
            return tracer
        if event == "line":
            _snapshot(frame.f_locals, state["prev"], fp_line)
            state["prev"] = frame.f_lineno
        return tracer

    buf = io.StringIO()
    try:
        code = compile(source, SOURCE_FILENAME, "exec")
        sys.settrace(tracer)
        with redirect_stdout(buf):
            exec(code, ns)
    except BaseException as exc:  # noqa: BLE001
        sys.settrace(None)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        sys.settrace(None)

    # Attribute anything new on the final line.
    _snapshot(ns, state["prev"], fp_line)

    objects = shown or _auto_collect(ns)
    show_line = state["prev"] or 1
    if not objects:
        return {"ok": True, "face_lines": [], "show_line": show_line}

    from build123d import Face

    face_lines: list[int] = []
    faces: list[Any] = []
    for obj, _name, _color in objects:
        w = obj.wrapped if hasattr(obj, "wrapped") else obj
        for topo in C.get_faces(w):
            face = Face(topo)
            try:
                line = fp_line.get(_fp(face), show_line)
            except Exception:
                line = show_line
            face_lines.append(line)
            faces.append(face)

    return {"ok": True, "face_lines": face_lines, "faces": faces, "show_line": show_line}


def provenance_render(source: str, active_line: int | None = None, sandbox: bool = True) -> dict[str, Any]:
    """Run with provenance, then tessellate each face individually — faces from
    ``active_line`` glow, the rest are dimmed. Drives code->geometry highlighting
    (plan §6). ``face_lines`` lets the frontend map a clicked face back to its
    line (geometry->code)."""
    from app.tessellate import tessellate

    info = run_with_provenance(source, sandbox)
    if not info.get("ok"):
        return info
    faces = info.get("faces") or []
    face_lines = info.get("face_lines") or []
    if not faces:
        return {
            "ok": True,
            "shapes": {},
            "states": {},
            "bbox": None,
            "face_lines": [],
            "show_line": info.get("show_line"),
        }

    # Color faces by the source line that produced them (a provenance map, so
    # the view is always informative), and make the cursor's line glow bright.
    # Deterministic line->hue (line % palette) so the frontend recolor agrees.
    colors: list[str] = []
    names: list[str] = []
    for i, line in enumerate(face_lines):
        glow = active_line is not None and line == active_line
        colors.append("#ffd23f" if glow else PROVENANCE_PALETTE[line % len(PROVENANCE_PALETTE)])
        names.append(f"L{line}__f{i}")

    shapes, states, bbox = tessellate(faces, names=names, colors=colors)
    return {
        "ok": True,
        "shapes": shapes,
        "states": states,
        "bbox": bbox,
        "face_lines": face_lines,
        "show_line": info.get("show_line"),
    }
