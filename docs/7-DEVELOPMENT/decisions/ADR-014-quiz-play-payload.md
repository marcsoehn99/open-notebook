# ADR-014: The quiz play payload has no answer key

- **Status**: Accepted
- **Date**: 2026-10

## Context

A notebook quiz is generated in the background, then played one question at a time. The player needs the stem and, for choice questions, the options. It must not receive the correct option or the accepted strings until the learner has submitted an answer. A later client, or a refreshed page, should not be able to read the key out of the play response.

Short and cloze answers are graded by comparing the submission to strings stored with the question. That match is exact after case-folding and whitespace collapsing. Punctuation stays. There is no second model call to judge a paraphrase at play time.

## Decision

**GET play returns `QuizPending` or `QuizReady`.** Pending covers a quiz whose command is not completed, and it has no cards. Ready is a completed quiz plus one card per question. Each card's prompt has the stem and, for choice questions, the shuffled options. It does not include `correct_option_id`, `correct_option_ids`, `accepted`, `quote`, or `explanation`.

The server shuffles choice options once, when the deck is sealed. Later reads return that order.

**POST attempt returns the verdict, and the verdict includes the reveal.** Choice reveals name the correct option ids. Short and cloze reveals include the accepted strings. Grading compares a short or cloze submission to those stored strings. Explain returns the stored explanation, quote, and source id, and only after that attempt has already submitted the question. Explain does not call a model.

**POST /quizzes/{id}/attempts/start creates the attempt.** The response is an attempt id chosen by the server. GET play, the answer endpoint, and explain reveal the key only for an attempt that this endpoint created for that quiz, and only after that attempt has stored an answer for the question. An unknown attempt id does not insert a row and does not receive a reveal. A second answer for the same question on that attempt returns 409. The stored answer stays. GET play still returns the reveal for questions that attempt already answered. Each answer is its own `quiz_answer` row, unique on attempt and question, so two answers sent at the same time for different questions both remain stored.

**The quiz row stores `status`.** The values are `new`, `queued`, `running`, `completed`, and `failed`. Generation runs once. The whole job stops after 6 minutes. On timeout or any other failure the row becomes `failed` and `error_message` is a short code. The UI translates that code. It does not show the model's raw error. Retry is `POST /quizzes/{id}/retry`, and it submits a new job only when the quiz is failed.

## Alternatives considered

- **Send the full sealed question on play and hide the key in the client.** Rejected. The key would be in the JSON.
- **Reshuffle options on every GET.** Rejected. A refresh would move the options under the learner.
- **Ask a model whether a short answer is close enough.** Rejected. The score would change between calls, and play would spend a model request.

## Consequences

- A client can render a question without being able to grade it locally.
- A client that invents an attempt id cannot read the key.
- Two answers for different questions, sent at the same time, both stay stored.
- Short answers that a person would accept, but that are not in the stored list, are marked wrong.
- The reveal is available only after a submission for that question on that attempt.
