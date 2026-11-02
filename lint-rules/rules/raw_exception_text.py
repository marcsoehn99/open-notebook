"""Caught exceptions must not be stringified into a new error message.

Finding: 2026-10-16-quiz-me.md S4. ``raise ValueError(str(error))`` copied the
full Pydantic/JSON text into the command ``error_message``, and the browser
showed it. Log the detail and raise a fixed, user-safe message.

A line marked ``# on-lint: allow-raw-exception-text`` (on the raise or the line
above) is an explicit baseline for a pre-existing site.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rules.common import Violation

RULE_ID = "ON-S4"
ALLOW = "on-lint: allow-raw-exception-text"
SCAN_ROOTS = ("open_notebook", "api", "commands")


def _allowed(source: str, node: ast.AST) -> bool:
    lines = source.splitlines()
    start = getattr(node, "lineno", 1) or 1
    end = getattr(node, "end_lineno", start) or start
    chunk = "\n".join(lines[max(0, start - 2) : end])
    return ALLOW in chunk


def _stringifies_except_name(expr: ast.AST, names: set[str]) -> bool:
    if not names:
        return False
    for node in ast.walk(expr):
        if isinstance(node, ast.FormattedValue):
            value = node.value
            if isinstance(value, ast.Name) and value.id in names:
                return True
            if _is_str_call(value, names):
                return True
        elif _is_str_call(node, names):
            return True
    return False


def _is_str_call(node: ast.AST, names: set[str]) -> bool:
    if not isinstance(node, ast.Call) or len(node.args) != 1:
        return False
    func = node.func
    arg = node.args[0]
    return (
        isinstance(func, ast.Name)
        and func.id == "str"
        and isinstance(arg, ast.Name)
        and arg.id in names
    )


def check_source(path: str, source: str) -> list[Violation]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 1, RULE_ID, f"syntax error: {exc.msg}")]

    found: list[Violation] = []

    def visit(node: ast.AST, names: set[str]) -> None:
        if isinstance(node, ast.ExceptHandler):
            inner = set(names)
            if isinstance(node.name, str):
                inner.add(node.name)
            for statement in node.body:
                visit(statement, inner)
            return
        if (
            isinstance(node, ast.Raise)
            and node.exc is not None
            and _stringifies_except_name(node.exc, names)
            and not _allowed(source, node)
        ):
            found.append(
                Violation(
                    path,
                    node.lineno,
                    RULE_ID,
                    "caught exception is stringified into the new error message "
                    "(2026-10-16-quiz-me.md S4). Log the detail and raise a fixed "
                    "message, or mark a reviewed baseline with "
                    "`# on-lint: allow-raw-exception-text`.",
                )
            )
        for child in ast.iter_child_nodes(node):
            visit(child, names)

    visit(tree, set())
    return found


def check_repo(root: Path) -> list[Violation]:
    found: list[Violation] = []
    for folder in SCAN_ROOTS:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            relative = path.relative_to(root).as_posix()
            found.extend(check_source(relative, source))
    return found
