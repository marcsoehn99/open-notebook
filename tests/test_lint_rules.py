"""Fixtures for the reviewer lint rules.

Each test feeds a bad snippet and a good snippet to one rule. The repository
snapshot is checked separately so a regression in the tree fails here too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lint-rules"))

from rules.constraint_sync import check_repo as constraint_repo  # noqa: E402
from rules.constraint_sync import check_texts  # noqa: E402
from rules.dep_diff import (  # noqa: E402
    changelog_violations,
    floor_violations,
    lock_versions,
)
from rules.no_npmmirror import check_repo as mirror_repo  # noqa: E402
from rules.no_npmmirror import check_text as check_mirror  # noqa: E402
from rules.packaging_declared import (  # noqa: E402
    _declares_packaging,
    _imports_packaging,
)
from rules.packaging_declared import check_repo as packaging_repo  # noqa: E402
from rules.quiz_command_valueerror import check_repo as valueerror_repo  # noqa: E402
from rules.quiz_command_valueerror import check_source as check_valueerror  # noqa: E402
from rules.quiz_id_prefix import check_repo as prefix_repo  # noqa: E402
from rules.quiz_id_prefix import check_source as check_prefix  # noqa: E402
from rules.quiz_request_bounds import check_repo as bounds_repo  # noqa: E402
from rules.quiz_request_bounds import check_source as check_bounds  # noqa: E402
from rules.raw_exception_text import check_repo as raw_repo  # noqa: E402
from rules.raw_exception_text import check_source as check_raw  # noqa: E402

QUIZ_PY = "open_notebook/domain/quiz.py"
QUIZ_ROUTER = "api/routers/quizzes.py"
QUIZ_COMMAND = "commands/quiz_commands.py"


def test_raw_exception_text_flags_str_of_caught_error() -> None:
    bad = "try:\n    parse()\nexcept ValidationError as error:\n    raise ValueError(str(error))\n"
    good = (
        "try:\n    parse()\nexcept ValidationError as error:\n"
        "    logger.warning(str(error))\n"
        '    raise QuizModelOutputError("quiz JSON did not match the schema")\n'
    )
    baseline = (
        "try:\n    parse()\nexcept ValueError as e:\n"
        "    # on-lint: allow-raw-exception-text — user-safe on purpose\n"
        "    raise ConfigurationError(str(e)) from e\n"
    )
    assert check_raw("open_notebook/graphs/quiz.py", bad)
    assert check_raw("open_notebook/graphs/quiz.py", good) == []
    assert check_raw("open_notebook/ai/models.py", baseline) == []


def test_quiz_command_rejects_valueerror_and_allows_typed() -> None:
    bad = 'def run():\n    raise ValueError("source has no text")\n'
    good = 'def run():\n    raise QuizSourceEmptyError("source has no text")\n'
    assert check_valueerror(QUIZ_COMMAND, bad)
    assert check_valueerror(QUIZ_COMMAND, good) == []
    assert check_valueerror("open_notebook/domain/quiz.py", bad) == []


def test_quiz_get_must_check_prefix() -> None:
    bad = (
        "class Quiz:\n"
        "    async def get(cls, id: str):\n"
        "        return await super().get(id)\n"
    )
    good = (
        "class Quiz:\n"
        "    async def get(cls, id: str):\n"
        '        if not id.startswith("quiz:"):\n'
        '            raise InvalidInputError("quiz id must start with quiz:")\n'
        "        return await super().get(id)\n"
    )
    assert check_prefix(QUIZ_PY, bad)
    assert check_prefix(QUIZ_PY, good) == []


def test_quiz_request_bounds() -> None:
    bad = (
        "from pydantic import BaseModel\n"
        "class CreateQuizRequest(BaseModel):\n"
        "    topic_prompt: str | None = None\n"
        "    source_ids: list[str]\n"
        "    num_questions: int\n"
    )
    good = (
        "from typing import Annotated\n"
        "from pydantic import BaseModel, Field\n"
        "BoundedId = Annotated[str, Field(max_length=200)]\n"
        "class CreateQuizRequest(BaseModel):\n"
        "    topic_prompt: str | None = Field(default=None, max_length=2000)\n"
        "    source_ids: list[BoundedId] = Field(max_length=50)\n"
        "    num_questions: int = Field(ge=1, le=10)\n"
    )
    assert len(check_bounds(QUIZ_ROUTER, bad)) == 3
    assert check_bounds(QUIZ_ROUTER, good) == []


def test_npmmirror_host_is_rejected() -> None:
    bad = '{ "resolved": "https://registry.npmmirror.com/left-pad/-/left-pad-1.0.0.tgz" }\n'
    good = (
        '{ "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.0.0.tgz" }\n'
    )
    assert check_mirror("frontend/package-lock.json", bad)
    assert check_mirror("frontend/package-lock.json", good) == []
    assert check_mirror("CHANGELOG.md", bad) == []


def test_packaging_import_requires_direct_dependency() -> None:
    assert _imports_packaging("from packaging.version import parse\n") == [1]
    assert _imports_packaging("import os\n") == []
    assert _declares_packaging('[project]\ndependencies = ["fastapi"]\n') is False
    assert (
        _declares_packaging('[project]\ndependencies = ["packaging>=25,<26"]\n') is True
    )


def test_constraint_lists_must_match() -> None:
    pyproject = """
[tool.uv]
constraint-dependencies = ["anyio>=4.14.2", "filelock>=4.2.1"]
"""
    lock_match = """
[manifest]
constraints = [
    { name = "anyio", specifier = ">=4.14.2" },
    { name = "filelock", specifier = ">=4.2.1" },
]
[[package]]
"""
    lock_missing = """
[manifest]
constraints = [
    { name = "anyio", specifier = ">=4.14.2" },
]
[[package]]
"""
    assert check_texts(pyproject, lock_match) == []
    missed = check_texts(pyproject, lock_missing)
    assert missed and "filelock" in missed[0].message


def test_transitive_bump_needs_a_floor_and_a_changelog() -> None:
    old_lock = '[[package]]\nname = "pyasn1"\nversion = "0.6.4"\n'
    new_lock = '[[package]]\nname = "pyasn1"\nversion = "0.6.5"\n'
    py_without = '[project]\ndependencies = ["fastapi>=0.1"]\n[tool.uv]\nconstraint-dependencies = []\n'
    py_with = (
        '[project]\ndependencies = ["fastapi>=0.1"]\n'
        '[tool.uv]\nconstraint-dependencies = ["pyasn1>=0.6.5"]\n'
    )
    assert floor_violations(
        old_lock=old_lock, new_lock=new_lock, new_pyproject=py_without
    )
    assert (
        floor_violations(old_lock=old_lock, new_lock=new_lock, new_pyproject=py_with)
        == []
    )
    direct = (
        '[project]\ndependencies = ["pyasn1>=0.6.5"]\n'
        "[tool.uv]\nconstraint-dependencies = []\n"
    )
    assert (
        floor_violations(old_lock=old_lock, new_lock=new_lock, new_pyproject=direct)
        == []
    )

    empty = "{}"
    lock_same = old_lock
    missed_log = changelog_violations(
        old_pyproject=py_without,
        new_pyproject=py_with,
        old_lock=lock_same,
        new_lock=lock_same,
        old_package_json=empty,
        new_package_json=empty,
        old_package_lock='{"packages":{}}',
        new_package_lock='{"packages":{}}',
        changelog_changed=False,
    )
    assert missed_log and missed_log[0].rule_id == "ON-CHANGELOG"
    ok_log = changelog_violations(
        old_pyproject=py_without,
        new_pyproject=py_with,
        old_lock=lock_same,
        new_lock=lock_same,
        old_package_json=empty,
        new_package_json=empty,
        old_package_lock='{"packages":{}}',
        new_package_lock='{"packages":{}}',
        changelog_changed=True,
    )
    assert ok_log == []


def test_lock_versions_read_the_real_lock() -> None:
    versions = lock_versions((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert versions["packaging"]
    assert "fastapi" in versions


def test_repository_snapshot_is_clean() -> None:
    assert raw_repo(ROOT) == []
    assert valueerror_repo(ROOT) == []
    assert prefix_repo(ROOT) == []
    assert bounds_repo(ROOT) == []
    assert mirror_repo(ROOT) == []
    assert packaging_repo(ROOT) == []
    assert constraint_repo(ROOT) == []


@pytest.mark.parametrize(
    "host",
    ["registry.npmjs.org"],
)
def test_package_lock_host(host: str) -> None:
    text = (ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    assert "registry.npmmirror.com" not in text
    assert host in text
