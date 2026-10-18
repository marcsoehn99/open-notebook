"""Quiz types and the rules that score, seal, and check a deck."""

import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, ClassVar, Literal, NamedTuple, TypedDict, Union, cast

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import ObjectModel
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import (
    ConfigurationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    QuizModelOutputError,
    QuizPromptTooLargeError,
    QuizSourceEmptyError,
    QuizTimeoutError,
)

QuestionType = Literal["mc", "multi", "short", "cloze"]
Difficulty = Literal["easy", "medium", "hard"]

QUESTION_TYPES: tuple[QuestionType, ...] = ("mc", "multi", "short", "cloze")
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")
DEFAULT_QUIZ_MODEL_NAME = "mistral-large-4"
QUIZ_PROMPT_TOKEN_LIMIT = 100_000
QUIZ_SOURCE_TOKEN_BUDGET = 80_000
MIN_QUOTE_CHARS = 15
MAX_QUESTIONS = 10
QUIZ_REPAIR_ROUNDS = 2
CLOZE_BLANK = "____"
QUIZ_ERROR_INVALID_OUTPUT = "invalid_output"
QUIZ_ERROR_EMPTY_SOURCE = "empty_source"
QUIZ_ERROR_TIMEOUT = "timeout"
QUIZ_ERROR_NOT_CONFIGURED = "not_configured"
QUIZ_ERROR_TOO_LARGE = "too_large"
QUIZ_ERROR_FAILED = "failed"
QUIZ_ERROR_CODES = frozenset(
    {
        QUIZ_ERROR_INVALID_OUTPUT,
        QUIZ_ERROR_EMPTY_SOURCE,
        QUIZ_ERROR_TIMEOUT,
        QUIZ_ERROR_NOT_CONFIGURED,
        QUIZ_ERROR_TOO_LARGE,
        QUIZ_ERROR_FAILED,
    }
)
_COMMAND_IN_PROGRESS = frozenset({"new", "queued", "running"})

BLOOM_BY_DIFFICULTY: dict[Difficulty, str] = {
    "easy": "remember",
    "medium": "apply",
    "hard": "analyze",
}

_FROZEN = ConfigDict(frozen=True)


class McOption(BaseModel):
    model_config = _FROZEN
    id: str
    text: str


class McPrompt(BaseModel):
    model_config = _FROZEN
    type: Literal["mc"]
    question_id: str
    position: int
    stem: str
    options: tuple[McOption, ...]


class MultiPrompt(BaseModel):
    model_config = _FROZEN
    type: Literal["multi"]
    question_id: str
    position: int
    stem: str
    options: tuple[McOption, ...]


class ShortPrompt(BaseModel):
    model_config = _FROZEN
    type: Literal["short"]
    question_id: str
    position: int
    stem: str


class ClozePrompt(BaseModel):
    model_config = _FROZEN
    type: Literal["cloze"]
    question_id: str
    position: int
    stem: str


Prompt = Annotated[
    Union[McPrompt, MultiPrompt, ShortPrompt, ClozePrompt],
    Field(discriminator="type"),
]


class SealedMc(BaseModel):
    model_config = _FROZEN
    type: Literal["mc"]
    question_id: str
    position: int
    stem: str
    options: tuple[McOption, ...]
    correct_option_id: str
    explanation: str
    quote: str
    source_id: str


class SealedMulti(BaseModel):
    model_config = _FROZEN
    type: Literal["multi"]
    question_id: str
    position: int
    stem: str
    options: tuple[McOption, ...]
    correct_option_ids: tuple[str, ...]
    explanation: str
    quote: str
    source_id: str


class SealedShort(BaseModel):
    model_config = _FROZEN
    type: Literal["short"]
    question_id: str
    position: int
    stem: str
    accepted: tuple[str, ...]
    explanation: str
    quote: str
    source_id: str


class SealedCloze(BaseModel):
    model_config = _FROZEN
    type: Literal["cloze"]
    question_id: str
    position: int
    stem: str
    accepted: tuple[str, ...]
    explanation: str
    quote: str
    source_id: str


SealedQuestion = Union[SealedMc, SealedMulti, SealedShort, SealedCloze]


class McSubmission(BaseModel):
    model_config = _FROZEN
    type: Literal["mc"]
    option_id: str


class MultiSubmission(BaseModel):
    model_config = _FROZEN
    type: Literal["multi"]
    option_ids: tuple[str, ...]


class ShortSubmission(BaseModel):
    model_config = _FROZEN
    type: Literal["short"]
    text: str


class ClozeSubmission(BaseModel):
    model_config = _FROZEN
    type: Literal["cloze"]
    text: str


Submission = Annotated[
    Union[McSubmission, MultiSubmission, ShortSubmission, ClozeSubmission],
    Field(discriminator="type"),
]


class McReveal(BaseModel):
    model_config = _FROZEN
    type: Literal["mc"]
    correct_option_id: str


class MultiReveal(BaseModel):
    model_config = _FROZEN
    type: Literal["multi"]
    correct_option_ids: tuple[str, ...]


class ShortReveal(BaseModel):
    model_config = _FROZEN
    type: Literal["short"]
    accepted: tuple[str, ...]


class ClozeReveal(BaseModel):
    model_config = _FROZEN
    type: Literal["cloze"]
    accepted: tuple[str, ...]


Reveal = Annotated[
    Union[McReveal, MultiReveal, ShortReveal, ClozeReveal],
    Field(discriminator="type"),
]


class CardResult(BaseModel):
    model_config = _FROZEN
    correct: bool
    reveal: Reveal


class Score(BaseModel):
    model_config = _FROZEN
    correct: int
    total: int


class InProgress(BaseModel):
    model_config = _FROZEN
    kind: Literal["in_progress"]
    answered: int
    total: int


class Finished(BaseModel):
    model_config = _FROZEN
    kind: Literal["finished"]
    score: Score


Progress = Annotated[Union[InProgress, Finished], Field(discriminator="kind")]


class QuizSettings(BaseModel):
    model_config = _FROZEN
    source_ids: tuple[str, ...]
    num_questions: int
    difficulty: Difficulty
    topic_prompt: str | None
    question_types: tuple[QuestionType, ...]
    language: str
    model_id: str


class StoredAnswer(BaseModel):
    model_config = _FROZEN
    question_id: str
    submission: Submission


class GeneratedQuestion(BaseModel):
    """Model JSON for one question. Indexes, not option ids."""

    model_config = _FROZEN
    type: QuestionType
    stem: str
    options: tuple[str, ...] = ()
    correct_indexes: tuple[int, ...] = ()
    accepted: tuple[str, ...] = ()
    explanation: str
    quote: str
    source_id: str


class GeneratedDeck(BaseModel):
    model_config = _FROZEN
    questions: tuple[GeneratedQuestion, ...]


class QuestionFault(NamedTuple):
    """One question the model must rewrite. question_index -1 means the deck itself."""

    question_index: int
    reason: str


def configured_model_name() -> str:
    name = os.environ.get("OPEN_NOTEBOOK_QUIZ_MODEL", "").strip()
    return name or DEFAULT_QUIZ_MODEL_NAME


def bloom_for(difficulty: Difficulty) -> str:
    return BLOOM_BY_DIFFICULTY[difficulty]


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def public_error_code(value: str | None) -> str | None:
    if value in QUIZ_ERROR_CODES:
        return value
    return None


def quiz_failure_code(error: BaseException) -> str:
    if isinstance(error, QuizTimeoutError):
        return QUIZ_ERROR_TIMEOUT
    if isinstance(error, QuizModelOutputError):
        return QUIZ_ERROR_INVALID_OUTPUT
    if isinstance(error, QuizPromptTooLargeError):
        return QUIZ_ERROR_TOO_LARGE
    if isinstance(error, QuizSourceEmptyError):
        return QUIZ_ERROR_EMPTY_SOURCE
    if isinstance(error, ConfigurationError):
        return QUIZ_ERROR_NOT_CONFIGURED
    return QUIZ_ERROR_FAILED


def truncate_slices(
    texts: Mapping[str, str],
    max_tokens: int,
    count_tokens: Callable[[str], int],
) -> dict[str, str]:
    if not texts:
        raise QuizSourceEmptyError("quiz context has no sources")
    if max_tokens < 0:
        raise InvalidInputError("quiz token budget is negative")
    share = max_tokens // len(texts)
    slices: dict[str, str] = {}
    for source_id, text in texts.items():
        slices[source_id] = _prefix_within(text, share, count_tokens)
    return slices


def _prefix_within(text: str, share: int, count_tokens: Callable[[str], int]) -> str:
    if count_tokens(text) <= share:
        if text == "":
            raise QuizSourceEmptyError("source text is empty")
        return text
    low = 0
    high = len(text)
    best = 0
    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[:mid]) <= share:
            best = mid
            low = mid
        else:
            high = mid - 1
    if best == 0:
        raise QuizPromptTooLargeError("source slice is empty")
    return text[:best]


def parse_settings(
    *,
    source_ids: Sequence[str],
    num_questions: int,
    difficulty: str,
    topic_prompt: str | None,
    question_types: Sequence[str],
    language: str,
    model_id: str,
) -> QuizSettings:
    if isinstance(num_questions, bool) or not isinstance(num_questions, int):
        raise InvalidInputError("num_questions must be an integer from 1 to 10")
    if not 1 <= num_questions <= MAX_QUESTIONS:
        raise InvalidInputError("num_questions must be an integer from 1 to 10")
    if difficulty not in DIFFICULTIES:
        raise InvalidInputError("difficulty must be easy, medium, or hard")
    if not isinstance(language, str) or not language.strip():
        raise InvalidInputError("language is required")
    if not isinstance(model_id, str) or not model_id.strip():
        raise InvalidInputError("model_id is required")

    chosen_types: list[QuestionType] = []
    for raw_type in question_types:
        if raw_type not in QUESTION_TYPES:
            raise InvalidInputError(
                "question_types must be a subset of mc, multi, short, cloze"
            )
        typed = cast(QuestionType, raw_type)
        if typed not in chosen_types:
            chosen_types.append(typed)
    if not chosen_types:
        raise InvalidInputError("question_types must include at least one type")

    seen_sources: list[str] = []
    for raw_source in source_ids:
        source_id = str(raw_source).strip()
        if not source_id or source_id in seen_sources:
            continue
        seen_sources.append(source_id)
    if not seen_sources:
        raise InvalidInputError("at least one source is required")

    topic = None
    if topic_prompt is not None and str(topic_prompt).strip():
        topic = str(topic_prompt).strip()

    return QuizSettings(
        source_ids=tuple(seen_sources),
        num_questions=num_questions,
        difficulty=cast(Difficulty, difficulty),
        topic_prompt=topic,
        question_types=tuple(chosen_types),
        language=language.strip(),
        model_id=model_id.strip(),
    )


def quote_is_verbatim(quote: str, sent_slice: str) -> bool:
    return len(quote) >= MIN_QUOTE_CHARS and quote in sent_slice


def question_fault(
    question: GeneratedQuestion,
    *,
    allowed_types: frozenset[QuestionType],
    slices: Mapping[str, str],
) -> str | None:
    """Why this question cannot be kept, or None when it passes."""
    try:
        if question.type not in allowed_types:
            raise QuizModelOutputError(f"question type {question.type} is not allowed")
        if question.source_id not in slices:
            raise QuizModelOutputError("question cites a source that was not sent")
        if not quote_is_verbatim(question.quote, slices[question.source_id]):
            raise QuizModelOutputError(
                "quote is not a verbatim slice of the source text"
            )
        if not question.explanation.strip():
            raise QuizModelOutputError("explanation is empty")
        _validate_generated_key(question)
    except QuizModelOutputError as error:
        return str(error)
    return None


def faults_for(
    questions: Sequence[GeneratedQuestion],
    *,
    num_questions: int,
    allowed_types: frozenset[QuestionType],
    slices: Mapping[str, str],
) -> tuple[QuestionFault, ...]:
    """Every failing question. A question_index of -1 means the deck cannot be aligned."""
    if len(questions) != num_questions:
        return (QuestionFault(-1, "question count does not match the quiz settings"),)
    faults: list[QuestionFault] = []
    for index, question in enumerate(questions):
        reason = question_fault(question, allowed_types=allowed_types, slices=slices)
        if reason is not None:
            faults.append(QuestionFault(index, reason))
    return tuple(faults)


def nothing_kept(
    questions: Sequence[GeneratedQuestion],
    faults: Sequence[QuestionFault],
) -> bool:
    """True when no question can be saved and the next call must rewrite the deck."""
    if not faults:
        return False
    if any(fault.question_index < 0 for fault in faults):
        return True
    return len(faults) == len(questions)


def validate_deck(
    questions: Sequence[GeneratedQuestion],
    *,
    num_questions: int,
    allowed_types: frozenset[QuestionType],
    slices: Mapping[str, str],
) -> None:
    faults = faults_for(
        questions,
        num_questions=num_questions,
        allowed_types=allowed_types,
        slices=slices,
    )
    if faults:
        raise QuizModelOutputError(faults[0].reason)


def load_generated_questions(cleaned_json: str) -> tuple[GeneratedQuestion, ...]:
    try:
        payload = json.loads(cleaned_json)
        deck = GeneratedDeck.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as error:
        logger.warning(f"quiz model output failed schema checks: {error}")
        raise QuizModelOutputError("quiz JSON did not match the schema") from error
    return deck.questions


def merge_repaired(
    questions: Sequence[GeneratedQuestion],
    faults: Sequence[QuestionFault],
    replacements: Sequence[GeneratedQuestion],
) -> tuple[GeneratedQuestion, ...]:
    """Put each replacement into the fault's question_index. Other questions stay."""
    if len(replacements) != len(faults):
        raise QuizModelOutputError("repair did not return the failing questions")
    merged = list(questions)
    for fault, replacement in zip(faults, replacements):
        if fault.question_index < 0 or fault.question_index >= len(merged):
            raise QuizModelOutputError("repair did not return the failing questions")
        merged[fault.question_index] = replacement
    return tuple(merged)


def full_repair_prompt(original: str, error: str) -> str:
    return (
        f"{original}\n\nThe previous JSON failed validation: {error}\n"
        "Return only the JSON object."
    )


def unusable_repair_prompt(
    original: str,
    questions: Sequence[GeneratedQuestion],
    faults: Sequence[QuestionFault],
) -> str:
    """Full rewrite used only when no question from the last reply can be kept."""
    reason = faults[0].reason if faults else "quiz JSON did not match the schema"
    lines = [
        full_repair_prompt(original, reason).rstrip(),
        "",
        "No question could be kept. Rewrite the whole quiz.",
    ]
    indexed = [fault for fault in faults if 0 <= fault.question_index < len(questions)]
    if indexed:
        for fault in indexed:
            lines.append(f"Index {fault.question_index} failed: {fault.reason}")
            lines.append(questions[fault.question_index].model_dump_json())
    else:
        for index, question in enumerate(questions):
            lines.append(f"Index {index} could not be kept.")
            lines.append(question.model_dump_json())
    return "\n".join(lines).rstrip() + "\n"


def partial_repair_prompt(
    original: str,
    questions: Sequence[GeneratedQuestion],
    faults: Sequence[QuestionFault],
) -> str:
    indexed = [fault for fault in faults if fault.question_index >= 0]
    lines = [
        original,
        "",
        "Rewrite only the failing questions below. "
        f"Return a JSON object whose questions array has exactly {len(indexed)} "
        "questions, in the listed order. "
        "Do not include questions that already passed.",
        "Return only the JSON object.",
        "",
    ]
    for fault in indexed:
        lines.append(f"Index {fault.question_index} failed: {fault.reason}")
        lines.append(questions[fault.question_index].model_dump_json())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _validate_generated_key(question: GeneratedQuestion) -> None:
    if question.type == "mc":
        _require_choice_indexes(question, exact=1)
        return
    if question.type == "multi":
        _require_choice_indexes(question, exact=None)
        return
    if question.options or question.correct_indexes:
        raise QuizModelOutputError("short and cloze questions do not take options")
    if not question.accepted or any(not item.strip() for item in question.accepted):
        raise QuizModelOutputError("accepted answers must be non-blank")
    if question.type == "cloze" and CLOZE_BLANK not in question.stem:
        raise QuizModelOutputError("cloze stem must contain ____")


def _require_choice_indexes(question: GeneratedQuestion, *, exact: int | None) -> None:
    if len(question.options) < 2:
        raise QuizModelOutputError("choice questions need at least two options")
    if any(not option.strip() for option in question.options):
        raise QuizModelOutputError("options must be non-blank")
    indexes = question.correct_indexes
    if len(indexes) != len(set(indexes)):
        raise QuizModelOutputError("correct indexes must be unique")
    option_count = len(question.options)
    if any(index < 0 or index >= option_count for index in indexes):
        raise QuizModelOutputError("correct index is out of range")
    if exact is not None:
        if len(indexes) != exact:
            raise QuizModelOutputError(
                "multiple choice needs exactly one correct index"
            )
        return
    if not indexes or len(indexes) >= option_count:
        raise QuizModelOutputError("multi-select needs a non-empty proper subset")


def parse_generated(
    cleaned_json: str,
    *,
    num_questions: int,
    allowed_types: frozenset[QuestionType],
    slices: Mapping[str, str],
) -> tuple[GeneratedQuestion, ...]:
    questions = load_generated_questions(cleaned_json)
    validate_deck(
        questions,
        num_questions=num_questions,
        allowed_types=allowed_types,
        slices=slices,
    )
    return questions


def seal_deck(
    questions: Sequence[GeneratedQuestion],
    rng: object,
    new_id: Callable[[], str],
) -> tuple[SealedQuestion, ...]:
    shuffle = getattr(rng, "shuffle", None)
    if not callable(shuffle):
        raise ValueError("rng must provide shuffle")
    sealed: list[SealedQuestion] = []
    for position, question in enumerate(questions, start=1):
        question_id = new_id()
        if question.type in ("mc", "multi"):
            pairs: list[tuple[str, str]] = [
                (new_id(), text) for text in question.options
            ]
            correct_ids = [pairs[index][0] for index in question.correct_indexes]
            shuffle(pairs)
            options = tuple(
                McOption(id=option_id, text=text) for option_id, text in pairs
            )
            shared = _shared_sealed(question, question_id, position)
            if question.type == "mc":
                sealed.append(
                    SealedMc(
                        type="mc",
                        options=options,
                        correct_option_id=correct_ids[0],
                        **shared,
                    )
                )
            else:
                sealed.append(
                    SealedMulti(
                        type="multi",
                        options=options,
                        correct_option_ids=tuple(correct_ids),
                        **shared,
                    )
                )
            continue
        shared = _shared_sealed(question, question_id, position)
        accepted = tuple(question.accepted)
        if question.type == "short":
            sealed.append(SealedShort(type="short", accepted=accepted, **shared))
        else:
            sealed.append(SealedCloze(type="cloze", accepted=accepted, **shared))
    return tuple(sealed)


class _SealedFields(TypedDict):
    question_id: str
    position: int
    stem: str
    explanation: str
    quote: str
    source_id: str


def _shared_sealed(
    question: GeneratedQuestion, question_id: str, position: int
) -> _SealedFields:
    return {
        "question_id": question_id,
        "position": position,
        "stem": question.stem,
        "explanation": question.explanation,
        "quote": question.quote,
        "source_id": question.source_id,
    }


def to_prompt(question: SealedQuestion) -> Prompt:
    if isinstance(question, SealedMc):
        return McPrompt(
            type="mc",
            question_id=question.question_id,
            position=question.position,
            stem=question.stem,
            options=question.options,
        )
    if isinstance(question, SealedMulti):
        return MultiPrompt(
            type="multi",
            question_id=question.question_id,
            position=question.position,
            stem=question.stem,
            options=question.options,
        )
    if isinstance(question, SealedShort):
        return ShortPrompt(
            type="short",
            question_id=question.question_id,
            position=question.position,
            stem=question.stem,
        )
    return ClozePrompt(
        type="cloze",
        question_id=question.question_id,
        position=question.position,
        stem=question.stem,
    )


def grade_one(question: SealedQuestion, submission: Submission) -> bool:
    if submission.type != question.type:
        raise ValueError("submission type does not match the question")
    if isinstance(question, SealedMc) and isinstance(submission, McSubmission):
        return submission.option_id == question.correct_option_id
    if isinstance(question, SealedMulti) and isinstance(submission, MultiSubmission):
        return set(submission.option_ids) == set(question.correct_option_ids)
    if isinstance(question, (SealedShort, SealedCloze)) and isinstance(
        submission, (ShortSubmission, ClozeSubmission)
    ):
        submitted = normalize_text(submission.text)
        if not submitted:
            return False
        return any(normalize_text(item) == submitted for item in question.accepted)
    raise ValueError("submission type does not match the question")


def reveal_of(question: SealedQuestion) -> Reveal:
    if isinstance(question, SealedMc):
        return McReveal(type="mc", correct_option_id=question.correct_option_id)
    if isinstance(question, SealedMulti):
        return MultiReveal(type="multi", correct_option_ids=question.correct_option_ids)
    if isinstance(question, SealedShort):
        return ShortReveal(type="short", accepted=question.accepted)
    return ClozeReveal(type="cloze", accepted=question.accepted)


def merge_answer(
    existing: Sequence[StoredAnswer], incoming: StoredAnswer
) -> tuple[StoredAnswer, ...]:
    for answer in existing:
        if answer.question_id == incoming.question_id:
            return tuple(existing)
    return tuple(existing) + (incoming,)


def align_answers(
    questions: Sequence[SealedQuestion], answers: Sequence[StoredAnswer]
) -> tuple[StoredAnswer, ...]:
    by_id: dict[str, StoredAnswer] = {}
    for answer in answers:
        if answer.question_id not in by_id:
            by_id[answer.question_id] = answer
    return tuple(
        by_id[question.question_id]
        for question in questions
        if question.question_id in by_id
    )


def score_attempt(
    questions: Sequence[SealedQuestion], answers: Sequence[StoredAnswer]
) -> Score:
    aligned = align_answers(questions, answers)
    questions_by_id = {question.question_id: question for question in questions}
    correct = 0
    for answer in aligned:
        if grade_one(questions_by_id[answer.question_id], answer.submission):
            correct += 1
    return Score(correct=correct, total=len(questions))


def progress_for(
    questions: Sequence[SealedQuestion], answers: Sequence[StoredAnswer]
) -> Progress:
    aligned = align_answers(questions, answers)
    if questions and len(aligned) == len(questions):
        return Finished(kind="finished", score=score_attempt(questions, answers))
    return InProgress(kind="in_progress", answered=len(aligned), total=len(questions))


def template_data(
    settings: QuizSettings,
    slices: Mapping[str, str],
    bloom: str,
) -> dict[str, object]:
    return {
        "bloom": bloom,
        "language": settings.language,
        "num_questions": settings.num_questions,
        "difficulty": settings.difficulty,
        "question_types": list(settings.question_types),
        "QUESTION_TYPES": list(QUESTION_TYPES),
        "topic_prompt": settings.topic_prompt or "",
        "cloze_blank": CLOZE_BLANK,
        "min_quote_chars": MIN_QUOTE_CHARS,
        "slices": [
            {"source_id": source_id, "text": text} for source_id, text in slices.items()
        ],
    }


def row_to_sealed(row: object) -> SealedQuestion:
    """Parse a stored question row into the sealed class for its type."""
    question_type = _row_str(row, "type")
    options = _row_options(row)
    correct_ids = tuple(_row_str_list(row, "correct_option_ids"))
    accepted = tuple(_row_str_list(row, "accepted"))
    shared = _SealedFields(
        question_id=_row_str(row, "question_id"),
        position=_row_int(row, "position"),
        stem=_row_str(row, "stem"),
        explanation=_row_str(row, "explanation"),
        quote=_row_str(row, "quote"),
        source_id=_row_str(row, "source_id"),
    )
    if question_type == "mc":
        if len(correct_ids) != 1 or accepted or len(options) < 2:
            raise ValueError("mc row key does not match the question")
        if correct_ids[0] not in {option.id for option in options}:
            raise ValueError("mc correct id is not an option")
        return SealedMc(
            type="mc",
            options=options,
            correct_option_id=correct_ids[0],
            **shared,
        )
    if question_type == "multi":
        option_ids = {option.id for option in options}
        correct_set = set(correct_ids)
        if (
            accepted
            or not correct_set
            or not correct_set < option_ids
            or len(correct_set) != len(correct_ids)
        ):
            raise ValueError("multi row key does not match the question")
        return SealedMulti(
            type="multi",
            options=options,
            correct_option_ids=correct_ids,
            **shared,
        )
    if question_type in ("short", "cloze"):
        if options or correct_ids or not accepted:
            raise ValueError("text row key does not match the question")
        if question_type == "cloze" and CLOZE_BLANK not in shared["stem"]:
            raise ValueError("cloze stem must contain ____")
        if question_type == "short":
            return SealedShort(type="short", accepted=accepted, **shared)
        return SealedCloze(type="cloze", accepted=accepted, **shared)
    raise ValueError(f"unknown question type {question_type}")


def _row_value(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name)


def _row_str(row: object, name: str) -> str:
    value = _row_value(row, name)
    if value is None:
        return ""
    return str(value)


def _row_int(row: object, name: str) -> int:
    value = _row_value(row, name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} is not an int")
    return value


def _row_str_list(row: object, name: str) -> list[str]:
    value = _row_value(row, name) or []
    if not isinstance(value, list):
        raise ValueError(f"{name} is not a list")
    return [str(item) for item in value]


def _row_options(row: object) -> tuple[McOption, ...]:
    value = _row_value(row, "options") or []
    if not isinstance(value, list):
        raise ValueError("options is not a list")
    options: list[McOption] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("option is not an object")
        options.append(McOption(id=str(item.get("id")), text=str(item.get("text"))))
    return tuple(options)


class PlayCard(BaseModel):
    model_config = _FROZEN
    prompt: Prompt
    result: CardResult | None = None


class Verdict(BaseModel):
    model_config = _FROZEN
    attempt_id: str
    question_id: str
    result: CardResult
    progress: Progress


class Explanation(BaseModel):
    model_config = _FROZEN
    explanation: str
    quote: str
    source_id: str


class QuizSummary(BaseModel):
    model_config = _FROZEN
    id: str
    status: str
    error_message: str | None = None
    num_questions: int
    difficulty: Difficulty
    topic_prompt: str | None = None
    language: str
    source_ids: list[str]


class QuizPending(QuizSummary):
    model_config = ConfigDict(frozen=True, extra="forbid")


class QuizReady(QuizSummary):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["completed"] = "completed"
    cards: list[PlayCard]
    progress: Progress


class Quiz(ObjectModel):
    table_name: ClassVar[str] = "quiz"
    nullable_fields: ClassVar[set[str]] = {
        "command",
        "topic_prompt",
        "error_message",
    }
    notebook: str
    command: str | None = None
    model_id: str
    source_ids: list[str]
    num_questions: int
    difficulty: str
    topic_prompt: str | None = None
    question_types: list[str]
    language: str
    status: str | None = None
    error_message: str | None = None

    def _prepare_save_data(self) -> dict[str, Any]:
        data = super()._prepare_save_data()
        data["notebook"] = ensure_record_id(data["notebook"])
        data["model_id"] = ensure_record_id(data["model_id"])
        if data.get("command"):
            data["command"] = ensure_record_id(data["command"])
        data["source_ids"] = [
            ensure_record_id(source_id) for source_id in data["source_ids"]
        ]
        return data


class QuizQuestion(ObjectModel):
    table_name: ClassVar[str] = "quiz_question"
    quiz: str
    run_id: str
    question_id: str
    position: int
    type: str
    stem: str
    options: list[dict[str, str]] = Field(default_factory=list)
    correct_option_ids: list[str] = Field(default_factory=list)
    accepted: list[str] = Field(default_factory=list)
    explanation: str
    quote: str
    source_id: str

    def _prepare_save_data(self) -> dict[str, Any]:
        data = super()._prepare_save_data()
        data["quiz"] = ensure_record_id(data["quiz"])
        data["source_id"] = ensure_record_id(data["source_id"])
        return data


class QuizAttempt(ObjectModel):
    table_name: ClassVar[str] = "quiz_attempt"
    quiz: str
    token: str
    answers: list[dict[str, Any]] = Field(default_factory=list)

    def _prepare_save_data(self) -> dict[str, Any]:
        data = super()._prepare_save_data()
        data["quiz"] = ensure_record_id(data["quiz"])
        return data


class QuizAnswer(ObjectModel):
    table_name: ClassVar[str] = "quiz_answer"
    quiz: str
    attempt: str
    question_id: str
    submission: dict[str, Any]

    def _prepare_save_data(self) -> dict[str, Any]:
        data = super()._prepare_save_data()
        data["quiz"] = ensure_record_id(data["quiz"])
        data["attempt"] = ensure_record_id(data["attempt"])
        return data


def _summary(quiz: Quiz, status: str, error_message: str | None) -> QuizSummary:
    if not quiz.id:
        raise ValueError("quiz has no id")
    if status == "failed":
        error_message = public_error_code(error_message) or QUIZ_ERROR_FAILED
    else:
        error_message = None
    return QuizSummary(
        id=str(quiz.id),
        status=status,
        error_message=error_message,
        num_questions=quiz.num_questions,
        difficulty=cast(Difficulty, quiz.difficulty),
        topic_prompt=quiz.topic_prompt,
        language=quiz.language,
        source_ids=[str(source_id) for source_id in quiz.source_ids],
    )


async def _read_command(quiz: Quiz) -> tuple[str, str | None]:
    if not quiz.command:
        return "unknown", None
    try:
        from surreal_commands import get_command_status

        status = await get_command_status(str(quiz.command))
        if not status:
            return "unknown", None
        raw_status = status.status
        text = str(getattr(raw_status, "value", raw_status))
        error_message = getattr(status, "error_message", None) or None
        if text != "failed" or not isinstance(error_message, str):
            error_message = None
        return text, error_message
    except Exception as error:
        logger.warning(f"Failed to get command status for {quiz.command}: {error}")
        return "unknown", None


def _persisted_status(quiz: Quiz) -> str | None:
    status = quiz.status
    if isinstance(status, str) and status.strip():
        return status
    return None


async def resolve_status(quiz: Quiz) -> tuple[str, str | None]:
    persisted = _persisted_status(quiz)
    if persisted == "completed":
        return "completed", None
    if persisted == "failed":
        return "failed", public_error_code(quiz.error_message) or QUIZ_ERROR_FAILED
    command_status, _command_error = await _read_command(quiz)
    if command_status == "completed":
        return "completed", None
    if command_status == "failed" and persisted != "queued":
        return "failed", public_error_code(quiz.error_message) or QUIZ_ERROR_FAILED
    if command_status in _COMMAND_IN_PROGRESS:
        return command_status, None
    if persisted:
        return persisted, None
    return "unknown", None


async def resolve_quiz_model_id(name: str) -> str:
    from open_notebook.ai.models import Model

    models = await Model.get_models_by_type("language")
    for model in models:
        if model.name == name and model.id:
            return str(model.id)
    raise ConfigurationError(
        f"No language model named {name}. "
        "Set OPEN_NOTEBOOK_QUIZ_MODEL to a model name from Manage Models."
    )


async def _linked_source_ids(notebook_id: str) -> set[str]:
    rows = await repo_query(
        "SELECT VALUE in FROM reference WHERE out = $notebook",
        {"notebook": ensure_record_id(notebook_id)},
    )
    return {str(item) for item in rows} if rows else set()


async def create_quiz(notebook_id: str, settings: QuizSettings) -> QuizSummary:
    notebook = await Notebook.get(notebook_id)
    if not notebook.id:
        raise NotFoundError("notebook not found")
    linked_ids = await _linked_source_ids(str(notebook.id))
    if any(source_id not in linked_ids for source_id in settings.source_ids):
        raise InvalidInputError("sources must belong to this notebook")
    quiz = Quiz(
        notebook=str(notebook.id),
        model_id=settings.model_id,
        source_ids=list(settings.source_ids),
        num_questions=settings.num_questions,
        difficulty=settings.difficulty,
        topic_prompt=settings.topic_prompt,
        question_types=list(settings.question_types),
        language=settings.language,
        status="new",
    )
    await quiz.save()
    try:
        command_id = await _submit_generation(quiz)
        quiz.command = command_id
        quiz.status = "queued"
        await quiz.save()
    except Exception:
        try:
            await quiz.delete()
        except Exception:
            logger.exception(f"failed to delete quiz {quiz.id} after submit failed")
        raise
    return _summary(quiz, "queued", None)


async def _submit_generation(quiz: Quiz) -> str:
    import commands.quiz_commands  # noqa: F401
    from api.command_service import CommandService

    if not quiz.id:
        raise NotFoundError("quiz not found")
    return await CommandService.submit_command_job(
        "open_notebook",
        "generate_quiz",
        {"quiz_id": str(quiz.id)},
    )


async def retry_quiz(quiz_id: str) -> QuizSummary:
    quiz = await Quiz.get(quiz_id)
    status, code = await resolve_status(quiz)
    if status != "failed":
        raise InvalidInputError("quiz is not failed")
    if quiz.status != "failed":
        quiz.status = "failed"
        quiz.error_message = code or QUIZ_ERROR_FAILED
        await quiz.save()
    claimed = await repo_query(
        "UPDATE $id SET status = 'queued', error_message = NONE "
        "WHERE status = 'failed'",
        {"id": ensure_record_id(str(quiz.id))},
    )
    if not claimed:
        raise InvalidInputError("quiz is not failed")
    quiz.status = "queued"
    quiz.error_message = None
    try:
        command_id = await _submit_generation(quiz)
    except Exception:
        quiz.status = "failed"
        quiz.error_message = QUIZ_ERROR_FAILED
        try:
            await quiz.save()
        except Exception:
            logger.exception(f"could not store failure for quiz {quiz.id}")
        raise
    quiz.command = command_id
    await quiz.save()
    return _summary(quiz, "queued", None)


async def list_quizzes(notebook_id: str) -> list[QuizSummary]:
    notebook = await Notebook.get(notebook_id)
    rows = await repo_query(
        "SELECT * FROM quiz WHERE notebook = $notebook ORDER BY created DESC",
        {"notebook": ensure_record_id(str(notebook.id))},
    )
    summaries: list[QuizSummary] = []
    for row in rows:
        quiz = Quiz(**row)
        status, error_message = await resolve_status(quiz)
        summaries.append(_summary(quiz, status, error_message))
    return summaries


async def _load_deck(quiz_id: str) -> tuple[SealedQuestion, ...]:
    rows = await repo_query(
        "SELECT * FROM quiz_question WHERE quiz = $quiz ORDER BY position ASC",
        {"quiz": ensure_record_id(quiz_id)},
    )
    if not rows:
        return ()
    run_ids = {str(row.get("run_id")) for row in rows}
    chosen = rows
    if len(run_ids) > 1:
        newest = max(rows, key=lambda row: str(row.get("created") or ""))
        run_id = str(newest.get("run_id"))
        chosen = [row for row in rows if str(row.get("run_id")) == run_id]
    sealed = [row_to_sealed(row) for row in chosen]
    sealed.sort(key=lambda question: question.position)
    return tuple(sealed)


async def _find_attempt(quiz_id: str, token: str) -> QuizAttempt | None:
    rows = await repo_query(
        "SELECT * FROM quiz_attempt WHERE quiz = $quiz AND token = $attempt_token LIMIT 1",
        {"quiz": ensure_record_id(quiz_id), "attempt_token": token},
    )
    if not rows:
        return None
    return QuizAttempt(**rows[0])


def _submission_payload(submission: Submission) -> dict[str, Any]:
    if isinstance(submission, McSubmission):
        return {"type": "mc", "option_id": submission.option_id}
    if isinstance(submission, MultiSubmission):
        return {"type": "multi", "option_ids": list(submission.option_ids)}
    if isinstance(submission, ShortSubmission):
        return {"type": "short", "text": submission.text}
    return {"type": "cloze", "text": submission.text}


def _submission_from_payload(payload: Mapping[str, Any]) -> Submission:
    kind = payload.get("type")
    if kind == "mc":
        return McSubmission(type="mc", option_id=str(payload.get("option_id") or ""))
    if kind == "multi":
        raw_ids = payload.get("option_ids") or []
        if not isinstance(raw_ids, list):
            raise ValueError("stored option ids are invalid")
        return MultiSubmission(
            type="multi", option_ids=tuple(str(item) for item in raw_ids)
        )
    if kind == "short":
        return ShortSubmission(type="short", text=str(payload.get("text") or ""))
    if kind == "cloze":
        return ClozeSubmission(type="cloze", text=str(payload.get("text") or ""))
    raise ValueError("stored submission is invalid")


def _answer_payload(answer: StoredAnswer) -> dict[str, Any]:
    return {
        "question_id": answer.question_id,
        "submission": _submission_payload(answer.submission),
    }


def _answers_from_attempt(attempt: QuizAttempt) -> tuple[StoredAnswer, ...]:
    stored: list[StoredAnswer] = []
    for item in attempt.answers or []:
        if not isinstance(item, Mapping):
            continue
        submission = item.get("submission")
        if not isinstance(submission, Mapping):
            raise ValueError("stored submission is invalid")
        stored.append(
            StoredAnswer(
                question_id=str(item.get("question_id") or ""),
                submission=_submission_from_payload(submission),
            )
        )
    return tuple(stored)


async def _answer_rows(
    attempt_id: str, question_id: str | None = None
) -> list[dict[str, Any]]:
    if question_id is None:
        query = "SELECT * FROM quiz_answer WHERE attempt = $attempt"
        params: dict[str, Any] = {"attempt": ensure_record_id(attempt_id)}
    else:
        query = (
            "SELECT * FROM quiz_answer WHERE attempt = $attempt "
            "AND question_id = $question_id LIMIT 1"
        )
        params = {
            "attempt": ensure_record_id(attempt_id),
            "question_id": question_id,
        }
    rows = await repo_query(query, params)
    return [row for row in rows if isinstance(row, dict)]


def _stored_from_row(row: Mapping[str, Any]) -> StoredAnswer:
    submission = row.get("submission")
    if not isinstance(submission, Mapping):
        raise ValueError("stored submission is invalid")
    return StoredAnswer(
        question_id=str(row.get("question_id") or ""),
        submission=_submission_from_payload(submission),
    )


async def _answers_for(attempt: QuizAttempt) -> tuple[StoredAnswer, ...]:
    rows: list[dict[str, Any]] = []
    if attempt.id:
        rows = await _answer_rows(str(attempt.id))
    by_id = {answer.question_id: answer for answer in _answers_from_attempt(attempt)}
    for row in rows:
        stored = _stored_from_row(row)
        by_id[stored.question_id] = stored
    return tuple(by_id.values())


async def _insert_answer(
    attempt: QuizAttempt, question_id: str, submission: Submission
) -> None:
    if not attempt.id:
        raise NotFoundError("attempt not found")
    row = QuizAnswer(
        quiz=str(attempt.quiz),
        attempt=str(attempt.id),
        question_id=question_id,
        submission=_submission_payload(submission),
    )
    try:
        await row.save()
    except Exception as error:
        existing = await _answer_rows(str(attempt.id), question_id)
        if existing:
            raise ConflictError("question already answered") from error
        raise


async def _ready_question(
    quiz_id: str, question_id: str
) -> tuple[Quiz, tuple[SealedQuestion, ...], SealedQuestion]:
    quiz = await Quiz.get(quiz_id)
    status, _error_message = await resolve_status(quiz)
    if status != "completed":
        raise InvalidInputError("quiz is not ready")
    questions = await _load_deck(str(quiz.id))
    question = next(
        (item for item in questions if item.question_id == question_id), None
    )
    if question is None:
        raise NotFoundError("question not found")
    return quiz, questions, question


async def start_attempt(quiz_id: str) -> str:
    quiz = await Quiz.get(quiz_id)
    status, _error_message = await resolve_status(quiz)
    if status != "completed":
        raise InvalidInputError("quiz is not ready")
    questions = await _load_deck(str(quiz.id))
    if not questions:
        raise InvalidInputError("quiz is not ready")
    token = str(uuid.uuid4())
    attempt = QuizAttempt(quiz=str(quiz.id), token=token, answers=[])
    await attempt.save()
    return token


async def load_play(quiz_id: str, attempt_id: str | None) -> QuizPending | QuizReady:
    quiz = await Quiz.get(quiz_id)
    status, error_message = await resolve_status(quiz)
    summary = _summary(quiz, status, error_message)
    if status != "completed":
        return QuizPending.model_validate(summary.model_dump())
    questions = await _load_deck(str(quiz.id))
    answers: tuple[StoredAnswer, ...] = ()
    if attempt_id:
        attempt = await _find_attempt(str(quiz.id), attempt_id)
        if attempt is not None:
            answers = await _answers_for(attempt)
    by_id = {answer.question_id: answer for answer in answers}
    cards: list[PlayCard] = []
    for question in questions:
        stored = by_id.get(question.question_id)
        result = None
        if stored is not None:
            result = CardResult(
                correct=grade_one(question, stored.submission),
                reveal=reveal_of(question),
            )
        cards.append(PlayCard(prompt=to_prompt(question), result=result))
    data = summary.model_dump()
    return QuizReady(
        id=data["id"],
        status="completed",
        error_message=data["error_message"],
        num_questions=data["num_questions"],
        difficulty=data["difficulty"],
        topic_prompt=data["topic_prompt"],
        language=data["language"],
        source_ids=data["source_ids"],
        cards=cards,
        progress=progress_for(questions, answers),
    )


async def commit_answer(
    quiz_id: str,
    attempt_id: str,
    question_id: str,
    submission: Submission,
) -> Verdict:
    if not attempt_id.strip():
        raise InvalidInputError("attempt_id is required")
    quiz, questions, question = await _ready_question(quiz_id, question_id)
    attempt = await _find_attempt(str(quiz.id), attempt_id)
    if attempt is None or not attempt.id:
        raise NotFoundError("attempt not found")
    if submission.type != question.type:
        raise InvalidInputError("submission type does not match the question")
    existing = await _answers_for(attempt)
    if any(answer.question_id == question_id for answer in existing):
        raise ConflictError("question already answered")
    await _insert_answer(attempt, question_id, submission)
    stored = StoredAnswer(question_id=question_id, submission=submission)
    merged = existing + (stored,)
    return Verdict(
        attempt_id=attempt_id,
        question_id=question_id,
        result=CardResult(
            correct=grade_one(question, stored.submission),
            reveal=reveal_of(question),
        ),
        progress=progress_for(questions, merged),
    )


async def explain_committed(
    quiz_id: str, attempt_id: str, question_id: str
) -> Explanation:
    quiz, _questions, question = await _ready_question(quiz_id, question_id)
    attempt = await _find_attempt(str(quiz.id), attempt_id)
    if attempt is None:
        raise NotFoundError("attempt not found")
    answers = await _answers_for(attempt)
    if not any(answer.question_id == question_id for answer in answers):
        raise InvalidInputError("answer this question before asking for an explanation")
    return Explanation(
        explanation=question.explanation,
        quote=question.quote,
        source_id=question.source_id,
    )


def _stored_key(
    question: SealedQuestion,
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    if isinstance(question, SealedMc):
        return (
            [{"id": option.id, "text": option.text} for option in question.options],
            [question.correct_option_id],
            [],
        )
    if isinstance(question, SealedMulti):
        return (
            [{"id": option.id, "text": option.text} for option in question.options],
            list(question.correct_option_ids),
            [],
        )
    if isinstance(question, (SealedShort, SealedCloze)):
        return ([], [], list(question.accepted))
    raise ValueError("unknown sealed question")


async def replace_questions(quiz_id: str, deck: Sequence[SealedQuestion]) -> None:
    run_id = str(uuid.uuid4())
    for question in deck:
        options, correct_ids, accepted = _stored_key(question)
        row = QuizQuestion(
            quiz=quiz_id,
            run_id=run_id,
            question_id=question.question_id,
            position=question.position,
            type=question.type,
            stem=question.stem,
            options=options,
            correct_option_ids=correct_ids,
            accepted=accepted,
            explanation=question.explanation,
            quote=question.quote,
            source_id=question.source_id,
        )
        await row.save()
    # Insert the new run first so a crash cannot leave the quiz with no deck.
    await repo_query(
        "DELETE quiz_question WHERE quiz = $quiz AND run_id != $run_id",
        {"quiz": ensure_record_id(quiz_id), "run_id": run_id},
    )
