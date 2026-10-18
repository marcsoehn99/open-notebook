"""Quiz repair rewrites failing questions and keeps the ones that passed."""

import json
from pathlib import Path

import pytest

from open_notebook.ai.reasoning import ReasoningTask
from open_notebook.domain.quiz import parse_settings
from open_notebook.exceptions import QuizModelOutputError
from open_notebook.graphs.quiz import generate_node

ROOT = Path(__file__).resolve().parents[1]
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
    def __init__(self, content: str) -> None:
        self.content = content


class _ScriptedModel:
    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> _Message:
        self.prompts.append(prompt)
        return _Message(self._replies[len(self.prompts) - 1])


async def _run(monkeypatch: pytest.MonkeyPatch, replies: list[str]):
    model = _ScriptedModel(replies)
    seen: dict[str, object] = {}

    async def _provision(*args: object, **kwargs: object) -> _ScriptedModel:
        seen["kwargs"] = kwargs
        return model

    monkeypatch.setattr(
        "open_notebook.graphs.quiz.provision_langchain_model",
        _provision,
    )
    result = await generate_node(
        {
            "quiz_id": "quiz:1",
            "model_id": "model:1",
            "slices": {SOURCE: SLICE},
            "settings": _settings(),
        },
        {},
    )
    return result, model.prompts, seen["kwargs"]


def test_quiz_job_timeout_stays_six_minutes() -> None:
    text = (ROOT / "commands/quiz_commands.py").read_text()
    assert "QUIZ_JOB_TIMEOUT_SECONDS = 6 * 60" in text


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
async def test_failing_repair_stops_after_two_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _question("Name the city.", GOOD_QUOTE)
    bad = _question("Name the river.", BAD_QUOTE)
    model = _ScriptedModel([_deck(good, bad), _deck(bad), _deck(bad)])

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
    assert len(model.prompts) == 3
    assert "exactly 1" in model.prompts[1]
    assert "exactly 1" in model.prompts[2]
    assert "Do not repeat these stems" in model.prompts[1]
    assert "Do not repeat these stems" in model.prompts[2]


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
