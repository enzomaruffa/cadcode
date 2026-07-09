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

import builtins as _builtins
import io
import traceback as tb_mod
from contextlib import contextmanager, redirect_stdout
from typing import Any

from app.kernel.result import RunResult
from app.tessellate import tessellate

SOURCE_FILENAME = "<cad-source>"
_MISSING = object()
_REQUIRE = "require"  # attribute name (a variable, so ruff B010 stays quiet)


@contextmanager
def _ambient_dsl(ns: dict[str, Any]):
    """Make ``require`` available inside imported modules (a project part is
    imported, so it wouldn't otherwise see it) — so a part can declare its own
    specs (`require(...)`) that get collected into the run. ``show`` stays
    scene-only on purpose: parts return shapes, scenes render them. Runs are
    serialized, so temporarily binding it on ``builtins`` is safe."""
    prev = getattr(_builtins, _REQUIRE, _MISSING)
    setattr(_builtins, _REQUIRE, ns["require"])
    try:
        yield
    finally:
        if prev is _MISSING:
            try:
                delattr(_builtins, _REQUIRE)
            except AttributeError:
                pass
        else:
            setattr(_builtins, _REQUIRE, prev)


# Objects from the most recent successful run, keyed by their shape-tree id
# (e.g. "/Group/plate"). Lets a follow-up `select` resolve a pick against the
# live OCP topology without re-running. Lives in whatever process ran the
# script (the worker), since OCP objects don't cross the process boundary.
LAST_SHOWN: dict[str, Any] = {}
# The resolved material (density / cost / filament) per shown leaf, populated in
# lockstep with LAST_SHOWN so a follow-up `physical` op can weigh each part.
LAST_MATERIAL: dict[str, dict[str, Any]] = {}


def _resolve_material(material: Any = None, density: Any = None) -> dict[str, Any]:
    """Normalize a ``show(..., material=..., density=...)`` argument to a plain
    dict ``{name, density (g/cm³), cost_per_kg, filament_d}``. Accepts a
    ``lib.design.Material``, a preset name string, a bare density float, or
    nothing (→ the default material). Kept JSON-friendly so it crosses the
    worker boundary and never couples callers to the Material class."""
    from lib.design import DEFAULT_MATERIAL, MATERIALS, Material

    m = DEFAULT_MATERIAL
    if isinstance(material, Material):
        m = material
    elif isinstance(material, str) and material.upper() in MATERIALS:
        m = MATERIALS[material.upper()]
    elif isinstance(material, (int, float)) and not isinstance(material, bool) and material > 0:
        m = Material("custom", float(material), DEFAULT_MATERIAL.cost_per_kg, DEFAULT_MATERIAL.filament_d)
    elif isinstance(density, (int, float)) and not isinstance(density, bool) and density > 0:
        m = Material("custom", float(density), DEFAULT_MATERIAL.cost_per_kg, DEFAULT_MATERIAL.filament_d)
    return {"name": m.name, "density": m.density, "cost_per_kg": m.cost_per_kg, "filament_d": m.filament_d}


def _obj_color(obj: Any) -> Any:
    """A color a part set on the object it returns (`obj.color = Color(...)`),
    normalized to a "#rrggbb" hex string. None if unset — so an explicit
    ``show(..., color=...)`` always wins and uncolored parts keep the default."""
    c = getattr(obj, "color", None)
    if c is None:
        return None
    if isinstance(c, str):
        return c
    try:
        t = c.to_tuple()  # build123d Color → (r, g, b, a) in 0..1
        return f"#{round(t[0] * 255):02x}{round(t[1] * 255):02x}{round(t[2] * 255):02x}"
    except Exception:
        return None


def _make_namespace(
    builtins_override: Any = None,
) -> tuple[dict[str, Any], list[tuple[Any, str | None, Any, dict[str, Any]]], list[dict[str, Any]]]:
    """Build the exec globals, including the ``show`` collectors and the
    ``require`` spec collector (CAD-as-TDD, plan §7)."""
    shown: list[tuple[Any, str | None, Any, dict[str, Any]]] = []
    specs: list[dict[str, Any]] = []

    def show(
        *objs: Any, name: str | None = None, color: Any = None, material: Any = None, density: Any = None, **_kw: Any
    ) -> Any:
        mat = _resolve_material(material, density)
        for i, obj in enumerate(objs):
            nm = name if (name and len(objs) == 1) else (f"{name}_{i}" if name else None)
            # Fall back to a color the part set on the object itself (`obj.color =
            # Color(...)`), so a part can OWN its color and a scene's bare
            # `show(part())` still picks it up.
            c = color if color is not None else _obj_color(obj)
            shown.append((obj, nm, c, mat))
        return objs[0] if len(objs) == 1 else objs

    def show_object(obj: Any, name: str | None = None, options: dict | None = None, **_kw: Any) -> Any:
        opts = options or {}
        color = (opts.get("color") if opts else None) or _obj_color(obj)
        mat = _resolve_material(opts.get("material"), opts.get("density"))
        shown.append((obj, name, color, mat))
        return obj

    def require(condition: Any, message: str = "") -> bool:
        """A soft, executable spec (CAD-as-TDD). Unlike ``assert`` it never
        raises — it records pass/fail so the geometry still renders and *all*
        unmet specs are reported. The agent iterates until they pass."""
        passed = bool(condition)
        specs.append({"passed": passed, "message": message or "requirement"})
        return passed

    ns: dict[str, Any] = {
        "__name__": "__cad__",
        "__builtins__": builtins_override if builtins_override is not None else __builtins__,
        # The cadcode DSL — provided like builtins (scripts can't import these).
        "show": show,
        "show_object": show_object,
        "require": require,
        # Motion-sim clearance (plan §7): defined so a script's
        # `require(min_clearance_through_motion >= CLEARANCE)` runs (and passes)
        # in the normal technical render; the sim re-execs with the real value.
        "min_clearance_through_motion": float("inf"),
    }
    # NOTE: we deliberately do NOT inject `from build123d import *` (or a `bd`
    # handle). The script is real Python and the single source of truth — it must
    # import the names it uses (`from build123d import Box, Cylinder, ...`), so a
    # missing import is a real NameError, and a buffer that runs here behaves the
    # same when saved as a library part (imported normally, with no injection).
    return ns, shown, specs


def _is_renderable(obj: Any) -> bool:
    from ocp_tessellate import convert as C

    return bool(
        C.is_build123d_shape(obj) or C.is_build123d_compound(obj) or C.is_build123d(obj) or C.is_topods_shape(obj)
    )


def _auto_collect(ns: dict[str, Any]) -> list[tuple[Any, str | None, Any, dict[str, Any]]]:
    """Fallback: render every top-level build123d shape the script defined."""
    default_mat = _resolve_material()
    collected: list[tuple[Any, str | None, Any, dict[str, Any]]] = []
    for name, obj in ns.items():
        if name.startswith("_") or name in ("show", "show_object", "require"):
            continue
        try:
            if _is_renderable(obj):
                collected.append((obj, name, None, default_mat))
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


def run_objects(source: str, *, sandbox: bool = False) -> tuple[list[Any], str | None]:
    """Run ``source`` and return the shown build123d objects (for boolean diffs),
    plus an error string if it failed."""
    builtins_override = None
    if sandbox:
        from app.kernel.sandbox import SandboxError, check_imports, safe_builtins

        try:
            check_imports(source)
        except SandboxError as exc:
            return [], f"SandboxError: {exc}"
        builtins_override = safe_builtins()

    ns, shown, _specs = _make_namespace(builtins_override)
    try:
        code = compile(source, SOURCE_FILENAME, "exec")
        with redirect_stdout(io.StringIO()):
            exec(code, ns)
    except BaseException as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    objects = shown or _auto_collect(ns)
    return [o for (o, _n, _c, _m) in objects], None


def run_scene(
    source: str, *, sandbox: bool = False, inject: dict[str, Any] | None = None
) -> tuple[list[Any], list[str | None], list[str], dict[str, Any], list[dict[str, Any]], str | None]:
    """Exec ``source`` and hand back everything a motion sim needs that
    ``run_source`` throws away: the live namespace (to find a ``motion(t)``
    function), the shown objects + their names + tessellation leaf ids (to key
    per-part transforms), and the ``require`` specs.

    ``inject`` seeds extra globals before exec — used to feed
    ``min_clearance_through_motion`` back into the script for its clearance spec.

    Returns ``(objs, names, leaf_ids, ns, specs, error)``."""
    builtins_override = None
    if sandbox:
        from app.kernel.sandbox import SandboxError, check_imports, safe_builtins

        try:
            check_imports(source)
        except SandboxError as exc:
            return [], [], [], {}, [], f"SandboxError: {exc}"
        builtins_override = safe_builtins()

    ns, shown, specs = _make_namespace(builtins_override)
    if inject:
        ns.update(inject)
    try:
        code = compile(source, SOURCE_FILENAME, "exec")
        with redirect_stdout(io.StringIO()), _ambient_dsl(ns):
            exec(code, ns)
    except BaseException as exc:  # noqa: BLE001
        return [], [], [], ns, specs, f"{type(exc).__name__}: {exc}"

    objects = shown or _auto_collect(ns)
    objs = [o for (o, _n, _c, _m) in objects]
    names = [n for (_o, n, _c, _m) in objects]
    if not objs:
        return [], [], [], ns, specs, None
    try:
        _shapes, states, _bbox = tessellate(objs, names=names)
    except Exception as exc:  # noqa: BLE001
        return objs, names, [], ns, specs, f"Tessellation failed: {exc}"
    return objs, names, list(states.keys()), ns, specs, None


def run_source(source: str, *, sandbox: bool = False) -> RunResult:
    """Execute ``source`` and return geometry or a structured error.

    With ``sandbox=True`` the import allowlist is enforced statically and the
    exec namespace gets restricted builtins (plan §7)."""
    builtins_override = None
    if sandbox:
        from app.kernel.sandbox import SandboxError, check_imports, safe_builtins

        try:
            check_imports(source)
        except SandboxError as exc:
            return RunResult.failure(f"SandboxError: {exc}", line=exc.line)
        builtins_override = safe_builtins()

    ns, shown, specs = _make_namespace(builtins_override)
    buf = io.StringIO()
    try:
        code = compile(source, SOURCE_FILENAME, "exec")
        with redirect_stdout(buf), _ambient_dsl(ns):
            exec(code, ns)
    except BaseException as exc:  # noqa: BLE001 - report every failure to the editor
        line = _error_line(exc)
        msg = f"{type(exc).__name__}: {exc}"
        full = "".join(tb_mod.format_exception(type(exc), exc, exc.__traceback__))
        return RunResult.failure(msg, traceback=_clean_traceback(full), line=line, stdout=buf.getvalue())

    objects = shown or _auto_collect(ns)
    if not objects:
        return RunResult.success({}, {}, None, stdout=buf.getvalue(), specs=specs)

    objs = [o for (o, _n, _c, _m) in objects]
    names = [n for (_o, n, _c, _m) in objects]
    colors = [c for (_o, _n, c, _m) in objects]
    materials = [m for (_o, _n, _c, m) in objects]
    try:
        shapes, states, bbox = tessellate(objs, names=names, colors=colors)
    except Exception as exc:  # noqa: BLE001
        full = "".join(tb_mod.format_exception(type(exc), exc, exc.__traceback__))
        return RunResult.failure(f"Tessellation failed: {exc}", traceback=_clean_traceback(full), stdout=buf.getvalue())

    # Cache objects (and their materials) by leaf id (states keys are leaf ids in
    # tessellation order) so a follow-up select / physical op can resolve against
    # the live topology.
    LAST_SHOWN.clear()
    LAST_MATERIAL.clear()
    for leaf_id, obj, mat in zip(states.keys(), objs, materials, strict=False):
        LAST_SHOWN[leaf_id] = obj
        LAST_MATERIAL[leaf_id] = mat

    # Assembly joint graph → physics constraints (articulated playground).
    try:
        from app.joints import extract_joints

        joints = extract_joints(objects)
    except Exception:  # noqa: BLE001 - joints are a bonus; never fail a render over them
        joints = []

    return RunResult.success(shapes, states, bbox, stdout=buf.getvalue(), specs=specs, joints=joints)


def run_source_lean(source: str) -> dict:
    """Execute ``source`` and return errors/specs/geometry FACTS — no tessellation.

    Built for the agent's dry-run loop: verification needs "does it run, do the
    specs pass, are the proportions sane" — not meshes. Skipping ocp-tessellate
    cuts most of the run cost, and (unlike ``run_source``) this never touches the
    ``LAST_SHOWN`` cache, so agent dry-runs can't clobber the live selection."""
    ns, shown, specs = _make_namespace(None)
    buf = io.StringIO()
    try:
        code = compile(source, SOURCE_FILENAME, "exec")
        with redirect_stdout(buf), _ambient_dsl(ns):
            exec(code, ns)
    except BaseException as exc:  # noqa: BLE001 - report every failure to the agent
        line = _error_line(exc)
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "error_line": line,
            "stdout": buf.getvalue(),
        }

    objects = shown or _auto_collect(ns)
    return {
        "ok": True,
        "specs": specs,
        "stdout": buf.getvalue(),
        "facts": _facts([(o, n) for (o, n, _c, _m) in objects]),
    }


def _facts(objects: list[tuple[Any, str | None]]) -> dict:
    """Compact geometric facts the agent can sanity-check numerically: per shown
    part its bbox size + volume, and pairwise bbox gaps/overlaps (≤ 8 parts)."""
    parts: list[dict] = []
    boxes: list[tuple[str, Any]] = []
    for i, (obj, name) in enumerate(objects[:12]):
        label = name or f"part{i + 1}"
        try:
            bb = obj.bounding_box()
            size = [round(bb.max.X - bb.min.X, 2), round(bb.max.Y - bb.min.Y, 2), round(bb.max.Z - bb.min.Z, 2)]
            vol = round(float(obj.volume), 1) if hasattr(obj, "volume") else None
            parts.append({"name": label, "bbox_size": size, "volume": vol})
            boxes.append((label, bb))
        except Exception:  # noqa: BLE001 - facts are best-effort
            parts.append({"name": label})
    pairs: list[dict] = []
    for i in range(len(boxes)):
        for j in range(i + 1, min(len(boxes), 8)):
            (na, a), (nb, b) = boxes[i], boxes[j]
            gaps = [
                max(a.min.X - b.max.X, b.min.X - a.max.X, 0.0),
                max(a.min.Y - b.max.Y, b.min.Y - a.max.Y, 0.0),
                max(a.min.Z - b.max.Z, b.min.Z - a.max.Z, 0.0),
            ]
            gap = round((gaps[0] ** 2 + gaps[1] ** 2 + gaps[2] ** 2) ** 0.5, 2)
            pairs.append({"a": na, "b": nb, "bbox_gap": gap} | ({} if gap > 0 else {"overlapping_or_touching": True}))
    return {"parts": parts, "pairs": pairs[:15]}


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
