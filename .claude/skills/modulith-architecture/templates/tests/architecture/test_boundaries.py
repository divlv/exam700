# tests/architecture/test_boundaries.py
# All comments are in English.

from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture._settings import APP, MODULES_DIR


def _iter_py_files() -> list[Path]:
    return [p for p in MODULES_DIR.rglob("*.py") if p.is_file()]


def _owner_module(py_file: Path) -> str | None:
    try:
        rel = py_file.relative_to(MODULES_DIR)
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def _current_package(py_file: Path) -> str:
    """
    Convert file path to a Python package path.
    Example:
      src/myapp/modules/orders/subpkg/x.py -> myapp.modules.orders.subpkg.x
    """
    rel = py_file.relative_to(Path("src"))
    parts = list(rel.parts)
    parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _resolve_import(py_file: Path, node: ast.ImportFrom) -> str | None:
    """
    Resolve absolute module name for ImportFrom, including relative imports.
    Returns None when it cannot be resolved.
    """
    if node.module is None and node.level == 0:
        return None

    if node.level == 0:
        return node.module

    # Relative import resolution:
    # level=1 means "from .", which stays in the current package.
    # level=2 means "from ..", which goes one package up, etc.
    pkg = _current_package(py_file)
    pkg_parts = pkg.split(".")[:-1]  # drop the module file name, keep package

    if node.level == 1:
        base = pkg_parts
    else:
        up = node.level - 1
        if up > len(pkg_parts):
            return None
        base = pkg_parts[:-up]

    if node.module:
        return ".".join(base + node.module.split("."))
    return ".".join(base)


def test_each_module_has_api_py() -> None:
    violations: list[str] = []
    for module_dir in [p for p in MODULES_DIR.iterdir() if p.is_dir()]:
        api_file = module_dir / "api.py"
        if not api_file.exists():
            violations.append(f"{module_dir}: missing required api.py")

    if violations:
        raise AssertionError("Missing module api.py files:\n" + "\n".join(violations))


def test_cross_module_imports_only_via_api() -> None:
    violations: list[str] = []
    prefix = f"{APP}.modules."

    for py_file in _iter_py_files():
        owner = _owner_module(py_file)
        if not owner:
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue

            resolved = _resolve_import(py_file, node)
            if not resolved:
                continue

            if not resolved.startswith(prefix):
                continue

            parts = resolved.split(".")
            if len(parts) < 3:
                continue

            target_module = parts[2]
            if target_module == owner:
                continue

            # Allowed: <app>.modules.<target>.api...
            if len(parts) >= 4 and parts[3] == "api":
                continue

            violations.append(
                f"{py_file}: forbidden import '{resolved}' from '{owner}'. "
                f"Cross-module imports must use '{APP}.modules.{target_module}.api'."
            )

    if violations:
        raise AssertionError("Modulith boundary violations:\n" + "\n".join(violations))


def test_no_internal_imports_across_modules() -> None:
    violations: list[str] = []
    prefix = f"{APP}.modules."

    for py_file in _iter_py_files():
        owner = _owner_module(py_file)
        if not owner:
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue

            resolved = _resolve_import(py_file, node)
            if not resolved:
                continue

            if not resolved.startswith(prefix):
                continue

            parts = resolved.split(".")
            if len(parts) < 4:
                continue

            target_module = parts[2]
            if target_module == owner:
                continue

            if "internal" in parts:
                violations.append(
                    f"{py_file}: forbidden import '{resolved}'. "
                    "Never import 'internal' across modules."
                )

    if violations:
        raise AssertionError("Internal import violations:\n" + "\n".join(violations))
