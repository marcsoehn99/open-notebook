"""Grade, shuffle, and quote checks with plain data. No database and no model."""

import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from open_notebook.domain.quiz import (
    QUESTION_TYPES,
    ClozeSubmission,
    Finished,
    GeneratedQuestion,
    InProgress,
    McOption,
    McSubmission,
    MultiSubmission,
    QuizPending,
    QuizReady,
    SealedCloze,
    SealedMc,
    SealedMulti,
    SealedQuestion,
    SealedShort,
    ShortSubmission,
    StoredAnswer,
    grade_one,
    merge_answer,
    parse_generated,
    parse_settings,
    progress_for,
    quote_is_verbatim,
    seal_deck,
    to_prompt,
    truncate_slices,
)
from open_notebook.exceptions import InvalidInputError

ROOT = Path(__file__).resolve().parents[1]

SOURCE = "source:essay"
QUOTE = "capital is Paris"
SLICE = "The capital is Paris today and the river is slow."
KEY_NAMES = (
    "correct_option_id",
    "correct_option_ids",
    "accepted",
    "quote",
    "explanation",
)


def _settings(**overrides: object):
    payload = {
        "source_ids": [SOURCE, SOURCE],
        "num_questions": 4,
        "difficulty": "medium",
        "topic_prompt": "  ",
        "question_types": list(QUESTION_TYPES),
        "language": " de ",
        "model_id": " model:quiz ",
    }
    payload.update(overrides)
    return parse_settings(**payload)  # type: ignore[arg-type]


def _generated(question_type: str, **overrides: object) -> GeneratedQuestion:
    payload: dict[str, object] = {
        "type": question_type,
        "stem": "Which city?",
        "options": (),
        "correct_indexes": (),
        "accepted": (),
        "explanation": "The sentence names it.",
        "quote": QUOTE,
        "source_id": SOURCE,
    }
    payload.update(overrides)
    return GeneratedQuestion(**payload)  # type: ignore[arg-type]


class _Reverse:
    def shuffle(self, items: list) -> None:
        items.reverse()


def _ids():
    issued: list[str] = []

    def new_id() -> str:
        issued.append(f"id{len(issued) + 1}")
        return issued[-1]

    return new_id


def _mc() -> SealedMc:
    return SealedMc(
        type="mc",
        question_id="q-mc",
        position=1,
        stem="Which city?",
        options=(
            McOption(id="opt-a", text="Lyon"),
            McOption(id="opt-b", text="Paris"),
        ),
        correct_option_id="opt-b",
        explanation="The sentence names it.",
        quote=QUOTE,
        source_id=SOURCE,
    )


def _multi() -> SealedMulti:
    return SealedMulti(
        type="multi",
        question_id="q-multi",
        position=2,
        stem="Which are cities?",
        options=(
            McOption(id="opt-a", text="Lyon"),
            McOption(id="opt-b", text="Paris"),
            McOption(id="opt-c", text="River"),
        ),
        correct_option_ids=("opt-a", "opt-b"),
        explanation="Both are cities.",
        quote=QUOTE,
        source_id=SOURCE,
    )


def _short() -> SealedShort:
    return SealedShort(
        type="short",
        question_id="q-short",
        position=3,
        stem="Name the city.",
        accepted=("Paris", "the city of Paris"),
        explanation="The sentence names it.",
        quote=QUOTE,
        source_id=SOURCE,
    )


def _cloze() -> SealedCloze:
    return SealedCloze(
        type="cloze",
        question_id="q-cloze",
        position=4,
        stem="The capital is ____.",
        accepted=("Paris",),
        explanation="The sentence names it.",
        quote=QUOTE,
        source_id=SOURCE,
    )


def _sealed_literal(cls: type) -> str:
    annotation = cls.model_fields["type"].annotation
    args = get_args(annotation)
    if args and isinstance(args[0], str):
        return args[0]
    raw = cls.__annotations__["type"]
    if isinstance(raw, str):
        for name in QUESTION_TYPES:
            if f'"{name}"' in raw or f"'{name}'" in raw:
                return name
    raise AssertionError(repr(annotation))


def test_question_types_lists_every_sealed_literal():
    literals = tuple(_sealed_literal(cls) for cls in get_args(SealedQuestion))
    assert QUESTION_TYPES == literals
    assert isinstance(QUESTION_TYPES, tuple)


def test_parse_settings_accepts_a_clean_subset_and_reads_question_types():
    settings = _settings()
    assert settings.question_types == QUESTION_TYPES
    assert settings.source_ids == (SOURCE,)
    assert settings.topic_prompt is None
    assert settings.language == "de"
    assert settings.model_id == "model:quiz"
    for question_type in QUESTION_TYPES:
        one = _settings(question_types=[question_type])
        assert one.question_types == (question_type,)


@pytest.mark.parametrize(
    "overrides",
    [
        {"num_questions": 0},
        {"num_questions": 11},
        {"num_questions": True},
        {"difficulty": "Easy"},
        {"question_types": []},
        {"question_types": ["flashcard"]},
        {"language": "  "},
        {"source_ids": []},
        {"source_ids": ["  ", ""]},
        {"model_id": " "},
    ],
)
def test_parse_settings_rejects_bad_input(overrides):
    with pytest.raises(InvalidInputError):
        _settings(**overrides)


def test_quote_is_verbatim_only_for_a_long_exact_substring():
    assert quote_is_verbatim(QUOTE, SLICE)
    assert quote_is_verbatim("x" * 15, "zz" + ("x" * 15) + "yy")
    assert not quote_is_verbatim("x" * 14, "x" * 20)
    assert not quote_is_verbatim("x" * 15, "y" * 20)
    assert not quote_is_verbatim("Capital is Paris", SLICE)
    assert not quote_is_verbatim("  hello world!!", "hello world!! and more")
    assert quote_is_verbatim("  hello world!!", "see  hello world!! here")


def test_truncate_slices_uses_an_equal_share():
    slices = truncate_slices(
        {"source:a": "aaaa", "source:b": "bbbbbb"},
        max_tokens=4,
        count_tokens=len,
    )
    assert slices == {"source:a": "aa", "source:b": "bb"}
    with pytest.raises(ValueError):
        truncate_slices({}, 10, len)


def test_seal_deck_shuffles_options_without_moving_the_correct_id():
    generated = _generated(
        "mc",
        options=("alpha", "beta", "gamma"),
        correct_indexes=(1,),
    )
    sealed = seal_deck((generated,), _Reverse(), _ids())
    question = sealed[0]
    assert isinstance(question, SealedMc)
    assert question.correct_option_id == "id3"
    assert [(option.id, option.text) for option in question.options] == [
        ("id4", "gamma"),
        ("id3", "beta"),
        ("id2", "alpha"),
    ]
    assert question.position == 1

    multi = seal_deck(
        (
            _generated(
                "multi",
                options=("alpha", "beta", "gamma"),
                correct_indexes=(0, 2),
            ),
        ),
        _Reverse(),
        _ids(),
    )[0]
    assert isinstance(multi, SealedMulti)
    assert set(multi.correct_option_ids) == {"id2", "id4"}
    assert {option.id: option.text for option in multi.options} == {
        "id4": "gamma",
        "id3": "beta",
        "id2": "alpha",
    }


def test_grade_one_scores_each_question_type():
    mc = _mc()
    assert grade_one(mc, McSubmission(type="mc", option_id="opt-b")) is True
    assert grade_one(mc, McSubmission(type="mc", option_id="opt-a")) is False

    multi = _multi()
    assert (
        grade_one(
            multi,
            MultiSubmission(type="multi", option_ids=("opt-b", "opt-a")),
        )
        is True
    )
    assert (
        grade_one(multi, MultiSubmission(type="multi", option_ids=("opt-a",)))
        is False
    )
    assert (
        grade_one(
            multi,
            MultiSubmission(type="multi", option_ids=("opt-a", "opt-b", "opt-c")),
        )
        is False
    )

    short = _short()
    assert grade_one(short, ShortSubmission(type="short", text="  PARIS ")) is True
    assert grade_one(short, ShortSubmission(type="short", text="paris.")) is False
    assert grade_one(short, ShortSubmission(type="short", text="   ")) is False

    cloze = _cloze()
    assert grade_one(cloze, ClozeSubmission(type="cloze", text="paris")) is True
    assert grade_one(cloze, ClozeSubmission(type="cloze", text="lyon")) is False
    with pytest.raises(ValueError):
        grade_one(mc, ShortSubmission(type="short", text="Paris"))


def test_merge_answer_keeps_the_first_submission():
    first = StoredAnswer(
        question_id="q-mc",
        submission=McSubmission(type="mc", option_id="opt-a"),
    )
    repeat = StoredAnswer(
        question_id="q-mc",
        submission=McSubmission(type="mc", option_id="opt-b"),
    )
    other = StoredAnswer(
        question_id="q-short",
        submission=ShortSubmission(type="short", text="Paris"),
    )
    locked = merge_answer((first,), repeat)
    assert locked == (first,)
    assert merge_answer(locked, repeat) == locked
    assert merge_answer((), first) == (first,)
    assert merge_answer((first,), other) == (first, other)


def test_progress_hides_the_score_until_every_question_is_answered():
    questions = (_mc(), _short())
    wrong = StoredAnswer(
        question_id="q-mc",
        submission=McSubmission(type="mc", option_id="opt-a"),
    )
    right = StoredAnswer(
        question_id="q-short",
        submission=ShortSubmission(type="short", text="Paris"),
    )
    partial = progress_for(questions, (wrong,))
    assert isinstance(partial, InProgress)
    assert partial.answered == 1
    assert partial.total == 2
    assert "score" not in partial.model_dump()

    finished = progress_for(questions, (wrong, right))
    assert isinstance(finished, Finished)
    assert finished.score.correct == 1
    assert finished.score.total == 2


def test_prompt_json_omits_the_answer_key():
    sealed = seal_deck(
        (
            _generated(
                "mc",
                options=("Lyon", "Paris"),
                correct_indexes=(1,),
            ),
            _generated("short", stem="Name it.", accepted=("Paris",)),
            _generated("cloze", stem="The capital is ____.", accepted=("Paris",)),
        ),
        _Reverse(),
        _ids(),
    )
    for question in sealed:
        payload = json.dumps(to_prompt(question).model_dump(mode="json"))
        for name in KEY_NAMES:
            assert name not in payload
        assert question.question_id in payload


def test_parse_generated_rejects_a_quote_that_is_not_in_the_slice():
    good = {
        "questions": [
            {
                "type": "short",
                "stem": "Name the city.",
                "accepted": ["Paris"],
                "explanation": "The sentence names it.",
                "quote": QUOTE,
                "source_id": SOURCE,
            }
        ]
    }
    parsed = parse_generated(
        json.dumps(good),
        num_questions=1,
        allowed_types=frozenset(QUESTION_TYPES),
        slices={SOURCE: SLICE},
    )
    assert parsed[0].type == "short"
    bad = json.dumps(good).replace(QUOTE, "not in the source")
    with pytest.raises(ValueError):
        parse_generated(
            bad,
            num_questions=1,
            allowed_types=frozenset({"short"}),
            slices={SOURCE: SLICE},
        )
    with pytest.raises(ValueError):
        parse_generated(
            "```json\n" + json.dumps(good) + "\n```",
            num_questions=1,
            allowed_types=frozenset(QUESTION_TYPES),
            slices={SOURCE: SLICE},
        )


def test_pending_play_has_no_cards_and_ready_is_completed():
    pending = QuizPending(
        id="quiz:1",
        status="running",
        num_questions=1,
        difficulty="medium",
        topic_prompt=None,
        language="de",
        source_ids=[SOURCE],
    )
    assert "cards" not in pending.model_fields
    with pytest.raises(ValidationError):
        QuizPending.model_validate({**pending.model_dump(), "cards": []})
    ready = QuizReady(
        id="quiz:1",
        status="completed",
        num_questions=1,
        difficulty="medium",
        topic_prompt=None,
        language="de",
        source_ids=[SOURCE],
        cards=[],
        progress=InProgress(kind="in_progress", answered=0, total=1),
    )
    assert ready.status == "completed"
    assert "cards" in ready.model_dump()


def test_router_does_not_name_sealed_or_generated_types():
    text = (ROOT / "api/routers/quizzes.py").read_text()
    assert "SealedMc" not in text
    assert "GeneratedQuestion" not in text


def test_prompt_template_reads_question_types():
    text = (ROOT / "prompts/quiz/generate.jinja").read_text()
    assert "QUESTION_TYPES" in text


@pytest.mark.asyncio
async def test_generate_node_rejects_invalid_json(monkeypatch):
    from open_notebook.graphs.quiz import generate_node

    class _Message:
        content = "not-json"

    class _Model:
        async def ainvoke(self, prompt: str) -> _Message:
            return _Message()

    async def _provision(*args: object, **kwargs: object) -> _Model:
        return _Model()

    monkeypatch.setattr(
        "open_notebook.graphs.quiz.provision_langchain_model",
        _provision,
    )
    with pytest.raises(ValueError):
        await generate_node(
            {
                "quiz_id": "quiz:1",
                "model_id": "model:1",
                "slices": {SOURCE: SLICE},
                "settings": _settings(),
            },
            {},
        )
