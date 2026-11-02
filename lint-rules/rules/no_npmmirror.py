"""Lockfiles must not download packages from registry.npmmirror.com.

Finding: 2026-10-23-osrh-security-deps.md question 3, and the follow-up in
2026-10-26-sec-deps-3-open-notebook.md ("Außerhalb des Diffs") /
2026-10-28-sec-deps-4-nacharbeiten-open-notebook.md Sp2. Those ``resolved``
URLs make CI and Docker depend on a third-party mirror. Versions and
integrity hashes stay on registry.npmjs.org.
"""

from __future__ import annotations

from pathlib import Path

from rules.common import Violation

RULE_ID = "ON-MIRROR"
NEEDLE = "registry.npmmirror.com"
PATHS = ("frontend/package-lock.json", "frontend/package.json")


def check_text(path: str, text: str) -> list[Violation]:
    if path not in PATHS or NEEDLE not in text:
        return []
    line = next(
        index
        for index, content in enumerate(text.splitlines(), start=1)
        if NEEDLE in content
    )
    return [
        Violation(
            path,
            line,
            RULE_ID,
            "package is resolved from registry.npmmirror.com "
            "(2026-10-23-osrh-security-deps.md question 3). "
            "Use https://registry.npmjs.org/ and keep the integrity hash.",
        )
    ]


def check_repo(root: Path) -> list[Violation]:
    found: list[Violation] = []
    for relative in PATHS:
        path = root / relative
        if not path.is_file():
            continue
        found.extend(check_text(relative, path.read_text(encoding="utf-8")))
    npmrc = root / "frontend" / ".npmrc"
    if npmrc.is_file():
        text = npmrc.read_text(encoding="utf-8")
        if NEEDLE in text:
            line = next(
                index
                for index, content in enumerate(text.splitlines(), start=1)
                if NEEDLE in content
            )
            found.append(
                Violation(
                    "frontend/.npmrc",
                    line,
                    RULE_ID,
                    "npmrc points at registry.npmmirror.com "
                    "(2026-10-23-osrh-security-deps.md question 3).",
                )
            )
    return found
