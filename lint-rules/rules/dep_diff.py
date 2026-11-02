"""Diff rules for dependency bumps.

ON-CHANGELOG — 2026-10-23-osrh-security-deps.md S1 (also sec-deps-2 S2,
sec-deps-3 St1, sec-deps-4 St1). A user-visible dependency change needs a
CHANGELOG entry under the existing ``## [Unreleased]`` section.

ON-DIFF-FLOOR — 2026-10-24-osrh-sec-deps-2.md S1 and
2026-10-31-sec-deps-5-open-notebook.md St1. A transitive package whose locked
version changed must have a floor in ``[tool.uv] constraint-dependencies``,
otherwise the next resolve can pick the vulnerable version again.
"""

from __future__ import annotations

import json
import re
import tomllib

from rules.common import Violation, dep_name
from rules.constraint_sync import pyproject_constraints

CHANGELOG = "ON-CHANGELOG"
FLOOR = "ON-DIFF-FLOOR"
PROJECT_NAME = "open-notebook"


def lock_versions(text: str) -> dict[str, str]:
    """Map uv package name to the version on its ``[[package]]`` header."""
    versions: dict[str, str] = {}
    name: str | None = None
    for line in text.splitlines():
        if line.startswith("[[package]]"):
            name = None
            continue
        if line.startswith('name = "'):
            name = line.split('"', 2)[1]
            continue
        if name and line.startswith('version = "'):
            versions.setdefault(name, line.split('"', 2)[1])
            name = None
    return versions


def direct_python_names(pyproject: str) -> set[str]:
    data = tomllib.loads(pyproject)
    names: set[str] = set()
    for spec in data.get("project", {}).get("dependencies") or []:
        names.add(dep_name(str(spec)))
    groups = data.get("dependency-groups") or {}
    for specs in groups.values():
        for spec in specs or []:
            names.add(dep_name(str(spec)))
    return names


def python_dep_signature(pyproject: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = tomllib.loads(pyproject)
    project = tuple(
        str(item) for item in data.get("project", {}).get("dependencies") or []
    )
    groups = data.get("dependency-groups") or {}
    grouped: list[str] = []
    for name in sorted(groups):
        for spec in groups[name] or []:
            grouped.append(f"{name}:{spec}")
    constraints = tuple(
        str(item)
        for item in data.get("tool", {}).get("uv", {}).get("constraint-dependencies")
        or []
    )
    return (project + tuple(grouped), constraints)


def npm_dep_signature(package_json: str) -> tuple[tuple[str, str], ...]:
    data = json.loads(package_json)
    pairs: list[tuple[str, str]] = []
    for section in ("dependencies", "devDependencies", "overrides"):
        block = data.get(section) or {}
        for name in sorted(block):
            value = block[name]
            if isinstance(value, str):
                pairs.append((f"{section}:{name}", value))
    return tuple(pairs)


def npm_lock_versions(package_lock: str) -> dict[str, str]:
    data = json.loads(package_lock)
    versions: dict[str, str] = {}
    packages = data.get("packages") or {}
    for path, meta in packages.items():
        if not isinstance(meta, dict) or not path.startswith("node_modules/"):
            continue
        version = meta.get("version")
        if isinstance(version, str):
            versions[path] = version
    return versions


def resolved_hosts(package_lock: str) -> set[str]:
    return set(re.findall(r'"resolved":\s*"https://([^/"]+)/', package_lock))


def changelog_violations(
    *,
    old_pyproject: str,
    new_pyproject: str,
    old_lock: str,
    new_lock: str,
    old_package_json: str,
    new_package_json: str,
    old_package_lock: str,
    new_package_lock: str,
    changelog_changed: bool,
) -> list[Violation]:
    reasons: list[str] = []
    if python_dep_signature(old_pyproject) != python_dep_signature(new_pyproject):
        reasons.append("pyproject.toml dependencies or constraint-dependencies")
    if lock_versions(old_lock) != lock_versions(new_lock):
        reasons.append("uv.lock package versions")
    if npm_dep_signature(old_package_json) != npm_dep_signature(new_package_json):
        reasons.append("frontend/package.json dependency versions")
    if npm_lock_versions(old_package_lock) != npm_lock_versions(new_package_lock):
        reasons.append("frontend/package-lock.json package versions")
    if resolved_hosts(old_package_lock) != resolved_hosts(new_package_lock):
        reasons.append("frontend/package-lock.json download hosts")
    if not reasons or changelog_changed:
        return []
    return [
        Violation(
            "CHANGELOG.md",
            1,
            CHANGELOG,
            "dependency change without a CHANGELOG entry "
            "(2026-10-23-osrh-security-deps.md S1): " + "; ".join(reasons) + ". "
            "Add it under the existing ## [Unreleased] section.",
        )
    ]


def floor_violations(
    *,
    old_lock: str,
    new_lock: str,
    new_pyproject: str,
) -> list[Violation]:
    old_versions = lock_versions(old_lock)
    new_versions = lock_versions(new_lock)
    direct = direct_python_names(new_pyproject)
    floors = set(pyproject_constraints(new_pyproject))
    found: list[Violation] = []
    for name in sorted(new_versions):
        if name == PROJECT_NAME or name in direct:
            continue
        if name not in old_versions or old_versions[name] == new_versions[name]:
            continue
        if name in floors:
            continue
        found.append(
            Violation(
                "pyproject.toml",
                1,
                FLOOR,
                f"transitive package {name} moved from {old_versions[name]} to "
                f"{new_versions[name]} without a [tool.uv] constraint-dependencies "
                "floor (2026-10-31-sec-deps-5-open-notebook.md St1 / "
                "2026-10-24-osrh-sec-deps-2.md S1).",
            )
        )
    return found
