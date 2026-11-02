"""Security floors in pyproject.toml and uv.lock must name the same specs.

Finding: 2026-10-23-osrh-security-deps.md S2, 2026-10-24-osrh-sec-deps-2.md S1
and 2026-10-31-sec-deps-5-open-notebook.md St1. A floor that exists only in the
lockfile (or only in pyproject) does not survive the next resolve. The two
``constraint-dependencies`` lists have to match, name and specifier.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from rules.common import Violation, dep_name

RULE_ID = "ON-FLOOR-SYNC"
_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def _split_constraint(raw: str) -> tuple[str, str]:
    body = raw.split(";", 1)[0].strip()
    match = _NAME.match(body)
    if match is None:
        return dep_name(body), body
    rest = body[match.end() :]
    if rest.startswith("["):
        rest = rest.split("]", 1)[1]
    return dep_name(match.group(0)), rest.strip()


def pyproject_constraints(text: str) -> dict[str, str]:
    data = tomllib.loads(text)
    specs = data.get("tool", {}).get("uv", {}).get("constraint-dependencies") or []
    mapping: dict[str, str] = {}
    for spec in specs:
        name, specifier = _split_constraint(str(spec))
        mapping[name] = specifier
    return mapping


def lock_constraints(text: str) -> dict[str, str]:
    """Read ``[manifest] constraints`` only, not later package tables."""
    manifest = re.search(r"(?ms)^\[manifest\]\n(.*?)(?:\n\[|\Z)", text)
    if manifest is None:
        return {}
    block = manifest.group(1)
    mapping: dict[str, str] = {}
    pattern = re.compile(
        r'\{\s*name\s*=\s*"([^"]+)"\s*,\s*specifier\s*=\s*"([^"]+)"\s*\}'
    )
    for name, specifier in pattern.findall(block):
        mapping[name.strip().lower().replace("_", "-")] = specifier.strip()
    return mapping


def check_texts(pyproject: str, lock: str) -> list[Violation]:
    left = pyproject_constraints(pyproject)
    right = lock_constraints(lock)
    if left == right:
        return []
    missing_in_lock = sorted(set(left) - set(right))
    missing_in_py = sorted(set(right) - set(left))
    mismatched = sorted(
        name for name in set(left) & set(right) if left[name] != right[name]
    )
    parts: list[str] = []
    if missing_in_lock:
        parts.append("missing in uv.lock: " + ", ".join(missing_in_lock))
    if missing_in_py:
        parts.append("missing in pyproject.toml: " + ", ".join(missing_in_py))
    if mismatched:
        parts.append("specifier mismatch: " + ", ".join(mismatched))
    return [
        Violation(
            "pyproject.toml",
            1,
            RULE_ID,
            "constraint-dependencies drift from uv.lock [manifest] constraints "
            "(2026-10-23-osrh-security-deps.md S2): " + "; ".join(parts),
        )
    ]


def check_repo(root: Path) -> list[Violation]:
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    return check_texts(pyproject, lock)
