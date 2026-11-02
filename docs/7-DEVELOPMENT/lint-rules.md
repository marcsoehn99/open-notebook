# Reviewer lint rules

When a review finds a mistake that a machine can recognize, the pull request that fixes it also adds a lint rule for that exact mistake. The rule fails CI if the pattern comes back. Judgment calls (product decisions, spec wording, "this class is too big") stay in the review; they are not rules.

## Where a rule lives

Python and lockfile rules are modules in [`lint-rules/rules/`](../../lint-rules/rules/). Each module's docstring names the review file and the finding id (`S1`, `St1`, `Q-S1`, …). The message string repeats that citation. [`lint-rules/check.py`](../../lint-rules/check.py) runs them.

Frontend rules are an ESLint plugin in [`lint-rules/eslint/`](../../lint-rules/eslint/), loaded from [`frontend/eslint.config.mjs`](../../frontend/eslint.config.mjs). `npm run lint` runs them with the rest of ESLint. The plugin message names the same review file and finding id.

Ruff still covers style (`E`, `F`, `I`). These rules are the patterns Ruff cannot express.

## Baseline

If the tree already contains the pattern, fix it in the same PR, or leave an explicit baseline:

- Python: a comment on the raise (or the line above) reading `on-lint: allow-raw-exception-text`, naming the finding.
- ESLint: `eslint-disable-next-line reviewer/<rule>` with a comment that names the finding.

A baseline is for code that was already there. New code does not get one unless the review says the pattern is intentional.

## Test

Every rule has a test that fails on a bad fixture and passes on a good one.

- Python: [`tests/test_lint_rules.py`](../../tests/test_lint_rules.py), run with `uv run pytest tests/test_lint_rules.py`.
- ESLint: [`frontend/src/lint-rules/reviewer-rules.test.ts`](../../frontend/src/lint-rules/reviewer-rules.test.ts), run with `npm run test -- src/lint-rules/reviewer-rules.test.ts` inside `frontend/`. That folder is excluded from `frontend/tsconfig.json`. The Docker frontend stage only copies `frontend/`, and the fixtures import `lint-rules/` from the repo root, so Next must not typecheck them.

## CI

[`.github/workflows/test.yml`](../../.github/workflows/test.yml) runs `uv run python lint-rules/check.py --base origin/<base>` in the backend lint job, and `npm run lint` for the ESLint plugin. The diff rules (changelog entry, transitive security floor) compare the pull request with its base branch. Locally:

```bash
uv run python lint-rules/check.py --base origin/main
```

`--skip-diff` runs only the snapshot rules.

## Add a rule

1. Put the module or ESLint rule in the folder above. Cite the review file and the finding id in the docstring and in the message.
2. Add the bad and good fixtures to the test for that tool.
3. Wire a Python rule into `snapshot_violations` or the diff helpers in `lint-rules/check.py`. Wire an ESLint rule in `lint-rules/eslint/index.mjs` and `frontend/eslint.config.mjs`.
4. If the current tree already matches the bad pattern, fix it or add a baseline comment in this same PR.
5. The rule lands in the same PR as the fix. Do not merge the fix and follow with the rule later.
