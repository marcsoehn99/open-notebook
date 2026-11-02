"""Quiz request models need explicit size bounds.

Finding: 2026-10-16-quiz-me.md S7. ``topic_prompt``, ``language``, ``attempt_id``
and ``source_ids`` were unbounded and were copied into the prompt. String
fields need ``max_length``. List fields need ``max_length`` (the item ceiling)
and a bounded element type. Int fields need ``ge`` or ``le``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

from rules.common import Violation

RULE_ID = "ON-S7"
RELATIVE = "api/routers/quizzes.py"


def _is_field_call(node: ast.AST) -> TypeGuard[ast.Call]:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Field"
    )


def _has_keyword(node: ast.Call, names: set[str]) -> bool:
    return any(keyword.arg in names for keyword in node.keywords)


def _annotated_slice(node: ast.Subscript) -> list[ast.expr]:
    if isinstance(node.slice, ast.Tuple):
        return list(node.slice.elts)
    return [node.slice]


def _string_aliases(tree: ast.Module) -> dict[str, bool]:
    """Names assigned to ``Annotated[str, Field(max_length=...)]`` (or not)."""
    aliases: dict[str, bool] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        bounded = _bounded_string(node.value, aliases)
        if bounded is not None:
            aliases[target.id] = bounded
    return aliases


def _bounded_string(node: ast.AST, aliases: dict[str, bool]) -> bool | None:
    """True/False if ``node`` is a string type, else None."""
    if isinstance(node, ast.Name):
        if node.id == "str":
            return False
        if node.id in aliases:
            return aliases[node.id]
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        sides = (
            _bounded_string(node.left, aliases),
            _bounded_string(node.right, aliases),
        )
        known = [side for side in sides if side is not None]
        if not known:
            return None
        return all(known)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Annotated"
    ):
        parts = _annotated_slice(node)
        if not parts:
            return None
        base = _bounded_string(parts[0], aliases)
        if base is None:
            return None
        has_max = any(
            _is_field_call(part) and _has_keyword(part, {"max_length"})
            for part in parts
        )
        return bool(base or has_max)
    return None


def _list_element(node: ast.AST) -> ast.AST | None:
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "list"
    ):
        return node.slice
    return None


def _models(tree: ast.Module) -> list[ast.ClassDef]:
    models: list[ast.ClassDef] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == "BaseModel":
                models.append(node)
                break
    return models


def check_source(path: str, source: str) -> list[Violation]:
    if path != RELATIVE:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 1, RULE_ID, f"syntax error: {exc.msg}")]
    aliases = _string_aliases(tree)
    found: list[Violation] = []
    for model in _models(tree):
        for statement in model.body:
            if not isinstance(statement, ast.AnnAssign):
                continue
            annotation = statement.annotation
            value = statement.value
            element = _list_element(annotation)
            if element is not None:
                element_bound = _bounded_string(element, aliases)
                if element_bound is False:
                    found.append(
                        Violation(
                            path,
                            statement.lineno,
                            RULE_ID,
                            "quiz list field has unbounded string items "
                            "(2026-10-16-quiz-me.md S7). Use Annotated[str, "
                            "Field(max_length=...)].",
                        )
                    )
                elif element_bound is True and not (
                    isinstance(value, ast.Call)
                    and _is_field_call(value)
                    and _has_keyword(value, {"max_length"})
                ):
                    found.append(
                        Violation(
                            path,
                            statement.lineno,
                            RULE_ID,
                            "quiz list field needs Field(max_length=...) for the "
                            "item ceiling (2026-10-16-quiz-me.md S7).",
                        )
                    )
                continue
            string_bound = _bounded_string(annotation, aliases)
            if string_bound is False:
                has_max = (
                    isinstance(value, ast.Call)
                    and _is_field_call(value)
                    and _has_keyword(value, {"max_length"})
                )
                if not has_max:
                    found.append(
                        Violation(
                            path,
                            statement.lineno,
                            RULE_ID,
                            "quiz string field needs Field(max_length=...) "
                            "(2026-10-16-quiz-me.md S7).",
                        )
                    )
                continue
            if isinstance(annotation, ast.Name) and annotation.id == "int":
                has_range = (
                    isinstance(value, ast.Call)
                    and _is_field_call(value)
                    and _has_keyword(value, {"ge", "le", "gt", "lt"})
                )
                if not has_range:
                    found.append(
                        Violation(
                            path,
                            statement.lineno,
                            RULE_ID,
                            "quiz int field needs Field(ge=... ) or Field(le=...) "
                            "(2026-10-16-quiz-me.md S7).",
                        )
                    )
    return found


def check_repo(root: Path) -> list[Violation]:
    path = root / RELATIVE
    if not path.is_file():
        return [Violation(RELATIVE, 1, RULE_ID, "quiz router is missing")]
    return check_source(RELATIVE, path.read_text(encoding="utf-8"))
