"""Quiz repair rewrites failing questions and keeps the ones that passed."""

import asyncio
import json
import time

import httpx
import pytest
from loguru import logger

from commands.quiz_commands import (
    QUIZ_JOB_TIMEOUT_SECONDS,
    GenerateQuizInput,
    generate_quiz_command,
)
from open_notebook.ai.reasoning import ReasoningTask
from open_notebook.domain.notebook import Source
from open_notebook.domain.quiz import QUIZ_SOURCE_TOKEN_BUDGET, Quiz, parse_settings
from open_notebook.exceptions import QuizModelOutputError, QuizTimeoutError
from open_notebook.graphs.quiz import generate_node
from open_notebook.utils import token_count

SOURCE = "source:essay"
GOOD_QUOTE = "capital is Paris"
RIVER_QUOTE = "the river is slow"
SLICE = "The capital is Paris today and the river is slow."
BAD_QUOTE = "this quote is not in the source"


def _settings():
    return parse_settings(
        source_ids=[SOURCE],
        num_questions=2,
        difficulty="medium",
        topic_prompt=None,
        question_types=["short"],
        language="en",
        model_id="model:quiz",
    )


def _question(stem: str, quote: str) -> dict[str, object]:
    return {
        "type": "short",
        "stem": stem,
        "accepted": ["Paris"],
        "explanation": "The sentence names it.",
        "quote": quote,
        "source_id": SOURCE,
    }


def _deck(*questions: dict[str, object]) -> str:
    return json.dumps({"questions": list(questions)})


class _Message:
    def __init__(self, content: object) -> None:
        self.content = content


class _ScriptedModel:
    def __init__(self, replies: list[object]) -> None:
        self._replies = replies
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> _Message:
        self.prompts.append(prompt)
        return _Message(self._replies[len(self.prompts) - 1])


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _ClockedModel(_ScriptedModel):
    def __init__(self, replies: list[object], clock: _Clock, step: float) -> None:
        super().__init__(replies)
        self._clock = clock
        self._step = step

    async def ainvoke(self, prompt: str) -> _Message:
        self._clock.t += self._step
        return await super().ainvoke(prompt)


class _ReadTimeoutRepair(_ScriptedModel):
    async def ainvoke(self, prompt: str) -> _Message:
        index = len(self.prompts)
        self.prompts.append(prompt)
        if index > 0:
            raise httpx.ReadTimeout("The read operation timed out")
        return _Message(self._replies[index])


class _HangingRepair(_ScriptedModel):
    def __init__(self, replies: list[object], hang_seconds: float) -> None:
        super().__init__(replies)
        self.hang_seconds = hang_seconds
        self.cancelled = False

    async def ainvoke(self, prompt: str) -> _Message:
        index = len(self.prompts)
        self.prompts.append(prompt)
        if index > 0:
            try:
                await asyncio.sleep(self.hang_seconds)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return _Message(self._replies[index])


class _RetryModel(_ScriptedModel):
    def __init__(
        self,
        replies: list[object],
        shared: dict[str, list[int]] | None = None,
    ) -> None:
        super().__init__(replies)
        self.max_retries = 5
        self._shared = shared if shared is not None else {"attempts": []}

    def model_copy(
        self, *, update: dict[str, object] | None = None, deep: bool = False
    ):
        del deep
        cloned = _RetryModel(self._replies, self._shared)
        cloned.prompts = self.prompts
        cloned.max_retries = self.max_retries
        retries = None if update is None else update.get("max_retries")
        if isinstance(retries, int):
            cloned.max_retries = retries
        return cloned

    async def ainvoke(self, prompt: str) -> _Message:
        self._shared["attempts"].append(self.max_retries)
        return await super().ainvoke(prompt)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[object],
    *,
    deadline: float | None = None,
    slices: dict[str, str] | None = None,
    model: _ScriptedModel | None = None,
    settings: object | None = None,
):
    scripted = model or _ScriptedModel(replies)
    seen: dict[str, object] = {}

    async def _provision(*args: object, **kwargs: object) -> _ScriptedModel:
        seen["kwargs"] = kwargs
        return scripted

    monkeypatch.setattr(
        "open_notebook.graphs.quiz.provision_langchain_model",
        _provision,
    )
    state: dict[str, object] = {
        "quiz_id": "quiz:1",
        "model_id": "model:1",
        "slices": slices or {SOURCE: SLICE},
        "settings": _settings() if settings is None else settings,
    }
    if deadline is not None:
        state["deadline"] = deadline
    result = await generate_node(state, {})
    return result, scripted.prompts, seen["kwargs"]


def _capture_warnings() -> tuple[list[str], int]:
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    return messages, sink


@pytest.mark.asyncio
async def test_repair_rewrites_only_the_failing_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    fixed = _question("Name the river.", RIVER_QUOTE)
    result, prompts, kwargs = await _run(
        monkeypatch,
        [_deck(good, bad), _deck(fixed)],
    )
    assert kwargs["task"] is ReasoningTask.QUIZ
    assert "temperature" not in kwargs
    assert len(prompts) == 2
    repair = prompts[1]
    assert "exactly 1" in repair
    assert "Name the river." in repair
    assert "quote is not a verbatim slice of the source text" in repair
    assert "Do not repeat these stems" in repair
    assert "Name the city." in repair
    assert prompts[0] not in repair
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]
    assert result["deck"][0].quote == GOOD_QUOTE
    assert result["deck"][1].quote == RIVER_QUOTE


@pytest.mark.asyncio
async def test_valid_deck_is_not_sent_again(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    other = _question("Name the river.", RIVER_QUOTE)
    result, prompts, _kwargs = await _run(monkeypatch, [_deck(good, other)])
    assert len(prompts) == 1
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


@pytest.mark.asyncio
async def test_exhausted_repair_returns_the_shorter_deck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    result, prompts, _kwargs = await _run(
        monkeypatch,
        [_deck(good, bad), _deck(bad), _deck(bad)],
    )
    assert len(prompts) == 3
    assert "exactly 1" in prompts[1]
    assert "exactly 1" in prompts[2]
    assert "Do not repeat these stems" in prompts[1]
    assert "Do not repeat these stems" in prompts[2]
    assert [card.stem for card in result["deck"]] == ["Name the city."]


@pytest.mark.asyncio
async def test_exhausted_repair_fails_when_no_question_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_city = _question("Name the city.", BAD_QUOTE)
    bad_river = _question("Name the river.", BAD_QUOTE)
    model = _ScriptedModel([_deck(bad_city, bad_river), _deck(bad_city, bad_river)])
    with pytest.raises(QuizModelOutputError):
        await _run(monkeypatch, [], model=model)
    assert len(model.prompts) == 2


@pytest.mark.asyncio
async def test_deck_with_no_keepable_question_is_rewritten_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_city = _question("Name the city.", BAD_QUOTE)
    bad_river = _question("Name the river.", BAD_QUOTE)
    fixed_city = _question("Name the city.", GOOD_QUOTE)
    fixed_river = _question("Name the river.", RIVER_QUOTE)
    result, prompts, _kwargs = await _run(
        monkeypatch,
        [_deck(bad_city, bad_river), _deck(fixed_city, fixed_river)],
    )
    assert len(prompts) == 2
    assert "No question could be kept" in prompts[1]
    assert "Ask 2 questions in en." in prompts[1]
    assert "exactly 1" not in prompts[1]
    assert "Name the city." in prompts[1]
    assert "Name the river." in prompts[1]
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


@pytest.mark.asyncio
async def test_second_unusable_deck_does_not_call_the_model_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _question("Name the city.", BAD_QUOTE)
    other = _question("Name the river.", BAD_QUOTE)
    model = _ScriptedModel([_deck(bad, other), _deck(bad, other), _deck(bad, other)])

    async def _provision(*args: object, **kwargs: object) -> _ScriptedModel:
        return model

    monkeypatch.setattr(
        "open_notebook.graphs.quiz.provision_langchain_model",
        _provision,
    )
    with pytest.raises(QuizModelOutputError):
        await generate_node(
            {
                "quiz_id": "quiz:1",
                "model_id": "model:1",
                "slices": {SOURCE: SLICE},
                "settings": _settings(),
            },
            {},
        )
    assert len(model.prompts) == 2


@pytest.mark.asyncio
async def test_unreadable_first_reply_is_rewritten_without_the_original_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    other = _question("Name the river.", RIVER_QUOTE)
    result, prompts, _kwargs = await _run(
        monkeypatch,
        ["not json", _deck(good, other)],
    )
    assert len(prompts) == 2
    assert "The previous JSON failed validation" in prompts[1]
    assert "Ask 2 questions in en." in prompts[1]
    assert "Use only these question types: ['short']." in prompts[1]
    assert "Difficulty is medium." in prompts[1]
    assert "have to apply the material." in prompts[1]
    assert "at least 15 characters" in prompts[1]
    assert "Write a quiz from the source text below." not in prompts[1]
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


@pytest.mark.asyncio
async def test_short_deck_asks_only_for_the_missing_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = _question("Name the city.", GOOD_QUOTE)
    missing = _question("Name the river.", RIVER_QUOTE)
    result, prompts, _kwargs = await _run(
        monkeypatch,
        [_deck(kept), _deck(missing)],
    )
    repair = prompts[1]
    assert len(prompts) == 2
    assert "exactly 1" in repair
    assert "Do not repeat these stems" in repair
    assert "Name the city." in repair
    assert "failed:" not in repair
    assert "Write a quiz from the source text below." not in repair
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


@pytest.mark.asyncio
async def test_repair_with_the_wrong_length_warns_and_uses_the_next_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    fixed = _question("Name the river.", RIVER_QUOTE)
    extra = _question("Name the extra.", RIVER_QUOTE)
    messages, sink = _capture_warnings()
    try:
        result, prompts, _kwargs = await _run(
            monkeypatch,
            [_deck(good, bad), _deck(fixed, extra), _deck(fixed)],
        )
    finally:
        logger.remove(sink)
    assert len(prompts) == 3
    assert any(
        "was not applied" in message and "expected 1" in message for message in messages
    )
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]
    assert result["deck"][1].quote == RIVER_QUOTE


@pytest.mark.asyncio
async def test_unreadable_repair_warns_and_uses_the_next_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    fixed = _question("Name the river.", RIVER_QUOTE)
    messages, sink = _capture_warnings()
    try:
        result, prompts, _kwargs = await _run(
            monkeypatch,
            [_deck(good, bad), "still not json", _deck(fixed)],
        )
    finally:
        logger.remove(sink)
    assert len(prompts) == 3
    assert any("was not applied" in message for message in messages)
    assert any("round 1" in message for message in messages)
    assert result["deck"][1].quote == RIVER_QUOTE


@pytest.mark.asyncio
async def test_question_that_fails_once_passes_on_the_second_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    fixed = _question("Name the river.", RIVER_QUOTE)
    result, prompts, _kwargs = await _run(
        monkeypatch,
        [_deck(good, bad), _deck(bad), _deck(fixed)],
    )
    assert len(prompts) == 3
    assert result["deck"][0].quote == GOOD_QUOTE
    assert result["deck"][1].quote == RIVER_QUOTE


@pytest.mark.asyncio
async def test_thinking_blocks_in_the_quiz_reply_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    other = _question("Name the river.", RIVER_QUOTE)
    hidden = "HIDDEN-QUIZ-THINKING"
    result, prompts, _kwargs = await _run(
        monkeypatch,
        [
            [
                {"type": "thinking", "thinking": [{"type": "text", "text": hidden}]},
                {"type": "reasoning", "reasoning": hidden, "text": hidden},
                {"type": "text", "text": _deck(good, other)},
            ]
        ],
    )
    assert len(prompts) == 1
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


@pytest.mark.asyncio
async def test_repaired_question_that_repeats_a_kept_stem_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    duplicate = _question("Name the city.", RIVER_QUOTE)
    fixed = _question("Name the river.", RIVER_QUOTE)
    messages, sink = _capture_warnings()
    try:
        result, prompts, _kwargs = await _run(
            monkeypatch,
            [_deck(good, bad), _deck(duplicate), _deck(fixed)],
        )
    finally:
        logger.remove(sink)
    assert len(prompts) == 3
    assert any("repeats a kept stem" in message for message in messages)
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


@pytest.mark.asyncio
async def test_deadline_keeps_valid_questions_without_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ESPERANTO_LLM_TIMEOUT", "180")
    clock = _Clock()
    monkeypatch.setattr("open_notebook.graphs.quiz._now", clock)
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    model = _ClockedModel([_deck(good, bad), _deck(bad)], clock, step=200)
    messages, sink = _capture_warnings()
    try:
        result, prompts, _kwargs = await _run(
            monkeypatch,
            [],
            deadline=float(QUIZ_JOB_TIMEOUT_SECONDS),
            model=model,
        )
    finally:
        logger.remove(sink)
    assert len(prompts) == 1
    assert clock.t <= QUIZ_JOB_TIMEOUT_SECONDS
    assert any("quiz repair skipped" in message for message in messages)
    assert [card.stem for card in result["deck"]] == ["Name the city."]


@pytest.mark.asyncio
async def test_repair_call_stops_when_the_job_deadline_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("open_notebook.graphs.quiz.llm_timeout_seconds", lambda: 0.05)
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    model = _HangingRepair([_deck(good, bad), _deck(bad)], hang_seconds=1.5)
    messages, sink = _capture_warnings()
    started = time.monotonic()
    try:
        result, prompts, _kwargs = await _run(
            monkeypatch,
            [],
            deadline=time.monotonic() + 0.6,
            model=model,
        )
    finally:
        logger.remove(sink)
    assert time.monotonic() - started < 2
    assert model.cancelled
    assert len(prompts) == 2
    assert any("quiz repair skipped" in message for message in messages)
    assert [card.stem for card in result["deck"]] == ["Name the city."]


def _queued_quiz() -> Quiz:
    return Quiz(
        id="quiz:1",
        notebook="notebook:1",
        command="command:1",
        model_id="model:1",
        source_ids=[SOURCE],
        num_questions=2,
        difficulty="medium",
        topic_prompt=None,
        question_types=["short"],
        language="en",
        status="queued",
        error_message=None,
    )


def _patch_generation(
    monkeypatch: pytest.MonkeyPatch, quiz: Quiz, model: _ScriptedModel
) -> list[tuple[object, ...]]:
    stored: list[tuple[object, ...]] = []

    async def fake_get(cls: type, quiz_id: str) -> Quiz:
        del cls, quiz_id
        return quiz

    class _Source:
        full_text = SLICE

    async def source_get(cls: type, source_id: str) -> _Source:
        del cls, source_id
        return _Source()

    async def save(self: Quiz) -> None:
        del self

    async def replace(quiz_id: str, deck: tuple[object, ...]) -> None:
        del quiz_id
        stored.append(deck)

    async def _provision(*args: object, **kwargs: object) -> _ScriptedModel:
        del args, kwargs
        return model

    monkeypatch.setattr(Quiz, "get", classmethod(fake_get))
    monkeypatch.setattr(Quiz, "save", save)
    monkeypatch.setattr(Source, "get", classmethod(source_get))
    monkeypatch.setattr("commands.quiz_commands.replace_questions", replace)
    monkeypatch.setattr(
        "open_notebook.graphs.quiz.provision_langchain_model",
        _provision,
    )
    return stored


@pytest.mark.asyncio
async def test_httpx_read_timeout_on_repair_completes_with_the_shorter_deck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    model = _ReadTimeoutRepair([_deck(good, bad)])
    quiz = _queued_quiz()
    stored = _patch_generation(monkeypatch, quiz, model)
    result = await generate_quiz_command(GenerateQuizInput(quiz_id="quiz:1"))
    assert result.success is True
    assert quiz.status == "completed"
    assert quiz.error_message is None
    assert len(model.prompts) == 2
    assert [card.stem for card in stored[0]] == ["Name the city."]


@pytest.mark.asyncio
async def test_httpx_read_timeout_with_no_valid_question_is_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_city = _question("Name the city.", BAD_QUOTE)
    bad_river = _question("Name the river.", BAD_QUOTE)
    model = _ReadTimeoutRepair([_deck(bad_city, bad_river)])
    quiz = _queued_quiz()
    _patch_generation(monkeypatch, quiz, model)
    with pytest.raises(QuizTimeoutError):
        await generate_quiz_command(GenerateQuizInput(quiz_id="quiz:1"))
    assert quiz.status == "failed"
    assert quiz.error_message == "timeout"


@pytest.mark.asyncio
async def test_quiz_call_uses_one_http_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    other = _question("Name the river.", RIVER_QUOTE)
    model = _RetryModel([_deck(good, other)])
    result, prompts, _kwargs = await _run(monkeypatch, [], model=model)
    assert model._shared["attempts"] == [1]
    assert len(prompts) == 1
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


def _mc(stem: str, quote: str) -> dict[str, object]:
    return {
        "type": "mc",
        "stem": stem,
        "options": ["Paris", "Lyon"],
        "correct_indexes": [0],
        "explanation": "The sentence names it.",
        "quote": quote,
        "source_id": SOURCE,
    }


def _german_mc_settings():
    return parse_settings(
        source_ids=[SOURCE],
        num_questions=2,
        difficulty="medium",
        topic_prompt="Hauptstadt",
        question_types=["mc"],
        language="de",
        model_id="model:quiz",
    )


@pytest.mark.asyncio
async def test_repair_prompt_keeps_language_type_difficulty_and_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _mc("Name the city.", GOOD_QUOTE)
    bad = _mc("Name the river.", BAD_QUOTE)
    fixed = _mc("Name the river.", RIVER_QUOTE)
    result, prompts, _kwargs = await _run(
        monkeypatch,
        [_deck(good, bad), _deck(fixed)],
        settings=_german_mc_settings(),
    )
    repair = prompts[1]
    assert len(prompts) == 2
    assert "The questions are in de." in repair
    assert "Use only these question types: ['mc']." in repair
    assert "Difficulty is medium." in repair
    assert "have to apply the material." in repair
    assert "Focus on this topic: Hauptstadt" in repair
    assert "verbatim substring" in repair
    assert "at least 15 characters" in repair
    assert "exactly 1" in repair
    assert "Ask 2 questions" not in repair
    assert "Write a quiz from the source text below." not in repair
    assert [card.stem for card in result["deck"]] == [
        "Name the city.",
        "Name the river.",
    ]


def _source_near_budget() -> str:
    head = "The capital is Paris today and the river is slow. "
    padding = "padding word " * 200
    text = head
    target = int(QUIZ_SOURCE_TOKEN_BUDGET * 0.9)
    while token_count(text) < target:
        text += padding
    return text + " TAIL-MARKER-SHOULD-NOT-BE-IN-REPAIR"


@pytest.mark.asyncio
async def test_large_source_repair_stays_small_and_inside_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ESPERANTO_LLM_TIMEOUT", "180")
    clock = _Clock()
    monkeypatch.setattr("open_notebook.graphs.quiz._now", clock)
    source = _source_near_budget()
    assert token_count(source) >= int(QUIZ_SOURCE_TOKEN_BUDGET * 0.9)
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    fixed = _question("Name the river.", RIVER_QUOTE)
    model = _ClockedModel(
        [_deck(good, bad), _deck(fixed)],
        clock,
        step=50,
    )
    result, prompts, _kwargs = await _run(
        monkeypatch,
        [],
        deadline=float(QUIZ_JOB_TIMEOUT_SECONDS),
        slices={SOURCE: source},
        model=model,
    )
    assert len(prompts) == 2
    assert "TAIL-MARKER-SHOULD-NOT-BE-IN-REPAIR" in prompts[0]
    assert "TAIL-MARKER-SHOULD-NOT-BE-IN-REPAIR" not in prompts[1]
    assert token_count(prompts[1]) * 10 < token_count(prompts[0])
    assert clock.t <= QUIZ_JOB_TIMEOUT_SECONDS
    assert [card.quote for card in result["deck"]] == [GOOD_QUOTE, RIVER_QUOTE]
