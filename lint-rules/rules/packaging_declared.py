"""An import of ``packaging`` must be a direct project dependency.

Finding: 2026-10-23-osrh-security-deps.md S5. ``version_utils`` imports
``packaging`` while the package was only transitive, so a later resolve could
drop it.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from rules.common import Violation, dep_name

RULE_ID = "ON-S5"
SCAN_ROOTS = ("open_notebook", "api", "commands")


def _imports_packaging(source: str) -> list[int]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root == "packaging":
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] == "packaging":
                    lines.append(node.lineno)
    return lines


def _declares_packaging(pyproject: str) -> bool:
    data = tomllib.loads(pyproject)
    dependencies = data.get("project", {}).get("dependencies") or []
    return any(dep_name(str(spec)) == "packaging" for spec in dependencies)


def check_repo(root: Path) -> list[Violation]:
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    if _declares_packaging(pyproject):
        return []
    found: list[Violation] = []
    for folder in SCAN_ROOTS:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for line in _imports_packaging(source):
                found.append(
                    Violation(
                        path.relative_to(root).as_posix(),
                        line,
                        RULE_ID,
                        "imports packaging but pyproject.toml does not depend on it "
                        "(2026-10-23-osrh-security-deps.md S5).",
                    )
                )
    return found
