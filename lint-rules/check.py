"""Run the reviewer lint rules. Exit 1 when any rule reports a violation.

Snapshot rules always run. Diff rules (changelog, transitive floors) run when
a base revision is available: pass ``--base origin/main``, or let the default
use ``origin/main`` when that ref exists. CI passes ``--base`` and treats a
missing merge-base as a failure.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent
if str(RULES_DIR) not in sys.path:
    sys.path.insert(0, str(RULES_DIR))

from rules.common import REPO_ROOT, Violation  # noqa: E402
from rules.constraint_sync import check_repo as check_constraints  # noqa: E402
from rules.dep_diff import changelog_violations, floor_violations  # noqa: E402
from rules.no_npmmirror import check_repo as check_npmmirror  # noqa: E402
from rules.packaging_declared import check_repo as check_packaging  # noqa: E402
from rules.quiz_command_valueerror import (
    check_repo as check_quiz_valueerror,  # noqa: E402
)
from rules.quiz_id_prefix import check_repo as check_quiz_prefix  # noqa: E402
from rules.quiz_request_bounds import check_repo as check_quiz_bounds  # noqa: E402
from rules.raw_exception_text import check_repo as check_raw_exception  # noqa: E402

DIFF_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "frontend/package.json",
    "frontend/package-lock.json",
    "CHANGELOG.md",
)


def snapshot_violations(root: Path) -> list[Violation]:
    found: list[Violation] = []
    found.extend(check_raw_exception(root))
    found.extend(check_quiz_valueerror(root))
    found.extend(check_quiz_prefix(root))
    found.extend(check_quiz_bounds(root))
    found.extend(check_npmmirror(root))
    found.extend(check_packaging(root))
    found.extend(check_constraints(root))
    return found


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def _merge_base(root: Path, base: str) -> str:
    proc = _run_git(root, ["merge-base", "HEAD", base])
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown git error"
        raise SystemExit(
            f"diff lint: cannot find merge-base of HEAD and {base}: {detail}"
        )
    return proc.stdout.strip()


def _show(root: Path, rev: str, path: str) -> str:
    proc = _run_git(root, ["show", f"{rev}:{path}"])
    if proc.returncode != 0:
        raise SystemExit(
            f"diff lint: cannot read {path} at {rev}: {proc.stderr.strip()}"
        )
    return proc.stdout


def diff_violations(root: Path, base: str) -> list[Violation]:
    rev = _merge_base(root, base)
    old = {path: _show(root, rev, path) for path in DIFF_PATHS}
    new = {path: (root / path).read_text(encoding="utf-8") for path in DIFF_PATHS}
    found = changelog_violations(
        old_pyproject=old["pyproject.toml"],
        new_pyproject=new["pyproject.toml"],
        old_lock=old["uv.lock"],
        new_lock=new["uv.lock"],
        old_package_json=old["frontend/package.json"],
        new_package_json=new["frontend/package.json"],
        old_package_lock=old["frontend/package-lock.json"],
        new_package_lock=new["frontend/package-lock.json"],
        changelog_changed=old["CHANGELOG.md"] != new["CHANGELOG.md"],
    )
    found.extend(
        floor_violations(
            old_lock=old["uv.lock"],
            new_lock=new["uv.lock"],
            new_pyproject=new["pyproject.toml"],
        )
    )
    return found


def _default_base(root: Path) -> str | None:
    proc = _run_git(root, ["rev-parse", "--verify", "--quiet", "origin/main"])
    if proc.returncode != 0:
        return None
    return "origin/main"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        help="Git rev to diff against for changelog and floor rules (for example origin/main).",
    )
    parser.add_argument(
        "--skip-diff",
        action="store_true",
        help="Run snapshot rules only.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    found = snapshot_violations(root)
    if not args.skip_diff:
        base = args.base or _default_base(root)
        if base is None:
            print(
                "diff lint skipped: no --base and origin/main is not available",
                file=sys.stderr,
            )
        else:
            found.extend(diff_violations(root, base))
    for violation in found:
        print(violation)
    if found:
        print(f"{len(found)} reviewer lint violation(s)", file=sys.stderr)
        return 1
    print("reviewer lint: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
