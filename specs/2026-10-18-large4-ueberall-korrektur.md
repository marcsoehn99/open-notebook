# Spec-Korrektur

This note corrects line 15 of `specs/2026-10-18-large4-ueberall.md`. That spec file lives in another worktree and is not edited on this branch.

Line 15 should read:

> Keine Aufgabe endet wegen eines Timeouts mit "failed" (Quiz-Job 6 min, pro Modellaufruf 180 s laut Code). Das wird mit einer großen Quelle getestet.

The code those numbers come from:

- `commands/quiz_commands.py` sets `QUIZ_JOB_TIMEOUT_SECONDS = 6 * 60`. The command passes `time.monotonic() + QUIZ_JOB_TIMEOUT_SECONDS` into the quiz graph as `deadline`, and wraps `quiz_graph.ainvoke` in `asyncio.wait_for` with the same limit.
- `open_notebook/config.py` sets `DEFAULT_LLM_TIMEOUT_SECONDS = 180`. `ensure_llm_timeout_default` writes that value into `ESPERANTO_LLM_TIMEOUT` when the variable is unset or not a positive number. Esperanto uses it as the timeout of one model call.
- The quiz job has no 300 second timeout. A 300 second limit appears only in `api/routers/sources.py` and `utils/mistral_ocr.py`.

The large-source check is `test_large_source_repair_stays_small_and_inside_the_deadline` in `tests/test_quiz_repair.py`.
