# ADR-015: Optional reasoning effort and partial quiz repair

- **Status**: Accepted
- **Date**: 2026-10
- **Related**: [Environment reference](../../5-CONFIGURATION/environment-reference.md#optional-mistral-reasoning), [Spec-Korrektur](../../../specs/2026-10-18-large4-ueberall-korrektur.md)

## Context

`mistral-large-4` reasons by default. A quiz on a short source (170 words, 3 questions) took 50 s and 5169 output tokens with no `reasoning_effort` field, and about 92 percent of that output was thinking. The same quiz with `high` took 43 s, 4819 tokens, and about 91 percent thinking. With `none` it took 22 s, 708 tokens, and 0 percent thinking. All three kept 3 of 3 quotes verbatim. Measured 2026-10-18. Default and `high` behave alike, so the API default for Large 4 is reasoning on.

The same day, `reasoning_effort="low"` on `mistral-large-4` returned HTTP 400. The body was `reasoning_effort low is not supported for this model, supported values: [high, none]` (code 3051). An unsupported value fails the call. The mapping may send only `none` or `high`.

Quiz repair adds model calls inside one job. The job limit is 6 minutes (`QUIZ_JOB_TIMEOUT_SECONDS` in `commands/quiz_commands.py`). `ESPERANTO_LLM_TIMEOUT` defaults to 180 seconds (`DEFAULT_LLM_TIMEOUT_SECONDS` in `open_notebook/config.py`). That limit is one HTTP attempt. `langchain_mistralai` retries `httpx.RequestError`, including a read timeout, up to `max_retries`, and the library default is 5. The quiz path sets `max_retries` to 1 and wraps each call in `asyncio.wait_for` for the seconds left until the job deadline. There is no 300 second quiz timeout.

## Decision

**Leave `reasoning_effort` off unless an operator sets it.** `reasoning_effort_for` returns nothing for `off`, `provider_default`, and an unset variable. Provision then sends the field on no request. That default stays, because quality comes before a faster answer.

**Send the field only for the models Mistral documents.** [Reasoning](https://docs.mistral.ai/studio/conversations/reasoning) lists `mistral-small-latest`, `mistral-medium-3-5`, and `mistral-large-4-0`. The allowlist is the exact name `mistral-small-latest`, plus names that start with `mistral-large-4` or `mistral-medium-3-5`, and only when the provider is Mistral. Embeddings, OCR, speech to text, and text to speech never read these variables.

**Map every accepted level onto `none` or `high`.**

| Value set | Field sent | Effect |
| --- | --- | --- |
| `none`, `minimal`, `low` | `none` | No thinking. This is the strongest cut. |
| `medium`, `high` | `high` | No cut. `medium` does not select a middle band. |
| `off`, `provider_default`, unset, or any other value | nothing | API default. A value outside the table logs a warning. |

A cut is allowed only after spec criterion 6 (every repaired quote is verbatim) and spec criterion 7 (the evaluation is at least as good as Medium). Until both are met, leave the variables unset.

**Copy `model_kwargs` before writing the field.** Provision calls `model_copy` with a new dict. The Esperanto instance keeps its original kwargs, including `response_format`. If kwargs is not a dict and an effort was requested, provision logs a warning and sends no field.

**Repair inside a budget, then keep what is already valid.** `RepairBudget` tracks rounds, full rewrites, the job deadline, and a per-call estimate. The estimate starts at `llm_timeout_seconds()` and rises when a measured call was longer. The estimate is one HTTP attempt. It is not a cap on a client that retries. Before each repair call, the graph skips the call when the remaining time is shorter than that estimate. A call that has started is cancelled when the job deadline is reached. If at least one question is already valid, the job returns that deck and logs `quiz repair skipped`. It does not fail the command for lack of time. Two rounds are still the cap when time remains. A second full rewrite still raises.

**Ask only for what is missing, and do not resend the source.** A short deck of valid questions asks for `num_questions - kept` new questions. A full-length deck asks only for the failing questions and merges them by index. The prompt is `prompts/quiz/repair.jinja`. It carries the failing questions, the reasons, the kept stems under "do not repeat", and a source excerpt. The excerpt is a window around a failing quote or stem (`REPAIR_EXCERPT_WINDOW` 800, `REPAIR_EXCERPT_CAP` 4000), or a bounded prefix when the quote is not in the source. A repaired stem that repeats a kept stem is rejected. An unreadable repair reply, or one with the wrong length, logs a warning with the round and the reason, and the round still counts.

**A deck fault is not a question fault.** `DeckFault` is the length mismatch. `QuestionFault.question_index` is always a real index. There is no `-1` sentinel.

**Drop thinking before a trace is stored.** The Langfuse client mask removes `thinking` and `reasoning` parts, and `<think>` blocks, from the payload the SDK exports. Tracing stays off when either key is unset, and that path does not import the client.

## Alternatives considered

- Sending `low` and `medium` as written. Measured 2026-10-18, `low` is HTTP 400 on Large 4. The call would fail in production.
- Rewriting the whole deck whenever one question fails. That spends another full generation and can replace questions that already quoted the source.
- Failing the job when the deadline is too close for another call. A deck with valid questions is usable. The player reads the stored cards, not the requested count.

## Consequences

- Operators who want the speed of `none` set `OPEN_NOTEBOOK_REASONING_QUIZ=none` (or `low` or `minimal`, which send the same field). They do that only after criteria 6 and 7 pass.
- A quiz can finish with fewer cards than `num_questions`. The player tolerates that. `QuizDialog` totals `ready.cards.length`. Play, progress, and scoring use the stored questions. `start_attempt` only requires a non-empty deck. The summary field can still show the requested count.
- The large-source test builds a source near `QUIZ_SOURCE_TOKEN_BUDGET`, checks that the repair prompt is much smaller than the original, and checks that a slow mock still finishes inside the 6 minute deadline.

## Spec mapping

The spec file lives in another worktree and is not edited here. The correction for its timeout line is [Spec-Korrektur](../../../specs/2026-10-18-large4-ueberall-korrektur.md).

| Criterion | Where it lives |
| --- | --- |
| 1. Every call uses `mistral-large-4` | Server config, set by Hostinger-Bot. Not this branch. |
| 2. Embeddings, OCR, speech to text, and text to speech stay unchanged | Fulfilled. This branch does not change those packages. Confirmed. |
| 3. Frontend progress for a running quiz | Out of scope. |
| 4. No quiz job ends as `failed` because a call would miss the deadline | This branch. Deadline-aware repair, and `test_large_source_repair_stays_small_and_inside_the_deadline`. |
| 5. Langfuse evaluation sets | Out of scope. |
| 6. Repaired quotes are verbatim | This branch. `quote_is_verbatim` runs on repaired questions through `faults_for`. |
| 7. Evaluation at least as good as Medium | Out of scope. It gates any cut to reasoning. See the decision above. |
| 8. No `reasoning_effort` unless configured | This branch. `open_notebook/ai/reasoning.py` and `open_notebook/ai/provision.py`. |
| 9. Repair keeps valid questions | This branch. `open_notebook/graphs/quiz.py` and `open_notebook/domain/quiz.py`. |
| 10. Score or judge changes | Out of scope. |

Criterion 2, and the decision to leave 3, 5, 7, and 10 untouched, were checked again for this record. No change was required.
