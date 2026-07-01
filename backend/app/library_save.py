"""Save the current model as a reusable library part.

Turns an editor script (params + build + `show(...)`) into a callable part
function module under ``lib/parts/`` and registers it, so afterwards you can
``from lib.parts import <name>`` and call ``<name>(WIDTH=..., ...)`` — the same
shape as the built-in catalog parts.
"""

from __future__ import annotations

import ast
import keyword
import re
from pathlib import Path


def sanitize_name(name: str) -> str:
    """A safe Python identifier from a user-typed part name."""
    ident = re.sub(r"\W+", "_", (name or "").strip().lower()).strip("_")
    if not ident or ident[0].isdigit():
        ident = f"part_{ident}" if ident else "part"
    if keyword.iskeyword(ident):
        ident += "_"
    return ident


def script_to_part(source: str, name: str) -> str:
    """Rewrite a model script as `def <name>(params=defaults): ...; return <shown>`."""
    tree = ast.parse(source)  # SyntaxError bubbles up to the caller
    imports: list[str] = []
    params: list[tuple[str, str]] = []
    body: list[ast.stmt] = []
    return_var: str | None = None

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            txt = ast.unparse(node)
            # Annotated/Range are only used in the param annotations we drop.
            if "lib.params" in txt or "typing" in txt:
                continue
            imports.append(txt)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            params.append((node.target.id, ast.unparse(node.value)))  # slider param -> function arg
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            fn = node.value.func.id
            if fn in ("show", "show_object"):
                if node.value.args and isinstance(node.value.args[0], ast.Name):
                    return_var = node.value.args[0].id
                # drop show() — the function returns the object instead
            elif fn == "require":
                pass  # drop specs (not part of the reusable geometry)
            else:
                body.append(node)
        else:
            body.append(node)

    if return_var is None:
        raise ValueError("The script needs a show(part, ...) call so I know which object the part returns.")

    sig = ", ".join(f"{a}={d}" for a, d in params)
    body_src = "\n".join(ast.unparse(s) for s in body)
    indented = "\n".join(("    " + line) if line.strip() else "" for line in body_src.splitlines())
    fn = f'def {name}({sig}):\n    """{name} — a reusable part saved from cadcode."""\n{indented}\n    return {return_var}\n'
    header = f'"""{name} — a reusable part saved from cadcode."""\nfrom __future__ import annotations\n\n'
    return header + "\n".join(imports) + "\n\n\n" + fn


def _register(parts_dir: Path, name: str) -> None:
    """Add `from .<name> import <name>` + include it in __all__ of the package."""
    init = parts_dir / "__init__.py"
    text = init.read_text()
    import_line = f"from .{name} import {name}  # saved part"
    if import_line not in text:
        text = text.replace("__all__ = [", f"{import_line}\n\n__all__ = [", 1)
    m = re.search(r"__all__\s*=\s*\[([^\]]*)\]", text)
    if m:
        items = [s.strip().strip("\"'") for s in m.group(1).split(",") if s.strip()]
        if name not in items:
            items.append(name)
        rebuilt = "__all__ = [" + ", ".join(f'"{i}"' for i in items) + "]"
        text = text[: m.start()] + rebuilt + text[m.end() :]
    init.write_text(text)


def save_part(name: str, source: str) -> dict:
    """Write + register a part module. Returns {ok, name} or {ok: False, error}."""
    ident = sanitize_name(name)
    try:
        module_text = script_to_part(source, ident)
    except SyntaxError as exc:
        return {"ok": False, "error": f"the script has a syntax error: {exc}"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    import lib.parts as _parts

    pkg_file = _parts.__file__
    if not pkg_file:
        return {"ok": False, "error": "cannot locate the parts package on disk"}
    parts_dir = Path(pkg_file).parent
    (parts_dir / f"{ident}.py").write_text(module_text)
    try:
        _register(parts_dir, ident)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"saved the module but failed to register it: {exc}"}
    return {"ok": True, "name": ident}
