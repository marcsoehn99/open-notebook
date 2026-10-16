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

## Alternatives considered

- **Send the full sealed question on play and hide the key in the client.** Rejected. The key would be in the JSON.
- **Reshuffle options on every GET.** Rejected. A refresh would move the options under the learner.
- **Ask a model whether a short answer is close enough.** Rejected. The score would change between calls, and play would spend a model request.

## Consequences

- A client can render a question without being able to grade it locally.
- Short answers that a person would accept, but that are not in the stored list, are marked wrong.
- The reveal is available only after a submission for that question on that attempt.
