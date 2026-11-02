"""Quiz.get must reject ids that are not in the quiz table.

Finding: 2026-10-16-quiz-me.md S6. ``ObjectModel.get`` is polymorphic on the
record prefix, so ``Quiz.get("source:x")`` returns a Source and the next
attribute access becomes an untyped 500. ``Quiz.get`` has to require ``quiz:``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rules.common import Violation

RULE_ID = "ON-S6"
RELATIVE = "open_notebook/domain/quiz.py"


def check_source(path: str, source: str) -> list[Violation]:
    if path != RELATIVE:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 1, RULE_ID, f"syntax error: {exc.msg}")]
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Quiz":
            continue
        for statement in node.body:
            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if statement.name != "get":
                continue
            segment = ast.get_source_segment(source, statement) or ""
            if 'startswith("quiz:")' in segment or "startswith('quiz:')" in segment:
                return []
            return [
                Violation(
                    path,
                    statement.lineno,
                    RULE_ID,
                    'Quiz.get must reject ids that do not start with "quiz:" '
                    "(2026-10-16-quiz-me.md S6).",
                )
            ]
        return [
            Violation(
                path,
                node.lineno,
                RULE_ID,
                "Quiz has no get() override, so a foreign record id is resolved "
                "polymorphically (2026-10-16-quiz-me.md S6).",
            )
        ]
    return [
        Violation(
            path,
            1,
            RULE_ID,
            "Quiz class is missing (2026-10-16-quiz-me.md S6).",
        )
    ]


def check_repo(root: Path) -> list[Violation]:
    path = root / RELATIVE
    if not path.is_file():
        return [Violation(RELATIVE, 1, RULE_ID, "quiz domain module is missing")]
    return check_source(RELATIVE, path.read_text(encoding="utf-8"))
