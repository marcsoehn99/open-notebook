"""Quiz jobs must not raise bare ValueError for user or config conditions.

Finding: 2026-10-16-quiz-me.md S3. ``raise ValueError("source has no text")``
in the quiz command is a condition the job should understand
(``QuizSourceEmptyError`` / ``InvalidInputError`` / ``ConfigurationError``).
ValueError inside deck validation stays allowed; this rule covers the command
and the quiz graph only.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rules.common import Violation

RULE_ID = "ON-S3"
FILES = (
    "commands/quiz_commands.py",
    "open_notebook/graphs/quiz.py",
)


def check_source(path: str, source: str) -> list[Violation]:
    if path not in FILES:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 1, RULE_ID, f"syntax error: {exc.msg}")]
    found: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        if isinstance(func, ast.Name) and func.id == "ValueError":
            found.append(
                Violation(
                    path,
                    node.lineno,
                    RULE_ID,
                    "quiz command/graph raises ValueError "
                    "(2026-10-16-quiz-me.md S3). Use a typed exception from "
                    "open_notebook.exceptions.",
                )
            )
    return found


def check_repo(root: Path) -> list[Violation]:
    found: list[Violation] = []
    for relative in FILES:
        path = root / relative
        if not path.is_file():
            continue
        found.extend(check_source(relative, path.read_text(encoding="utf-8")))
    return found
