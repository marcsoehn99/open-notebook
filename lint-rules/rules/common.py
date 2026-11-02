"""Shared types for reviewer lint rules."""

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Violation:
    """One lint hit. ``path`` is relative to the repo root, forward slashes."""

    path: str
    line: int
    rule_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule_id} {self.message}"


def dep_name(spec: str) -> str:
    """Package name from a PEP 508 dependency spec, without extras or markers."""
    name = spec.split(";", 1)[0].strip()
    name = name.split("[", 1)[0].strip()
    for separator in (">=", "<=", "===", "==", "~=", "!=", ">", "<"):
        name = name.split(separator, 1)[0]
    return name.strip().lower().replace("_", "-")
