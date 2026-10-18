"""Reasoning effort stays off unless an environment variable asks for it."""

from dataclasses import dataclass

import pytest
from esperanto import LanguageModel
from loguru import logger

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.ai.reasoning import (
    ReasoningTask,
    apply_reasoning_effort,
    reasoning_effort_for,
    reasoning_env_name,
)

LARGE_4 = "mistral-large-4"


@pytest.fixture(autouse=True)
def _clear_reasoning_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for task in ReasoningTask:
        monkeypatch.delenv(reasoning_env_name(task), raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_REASONING_DEFAULT", raising=False)


def test_default_sends_nothing_for_large_4() -> None:
    assert (
        reasoning_effort_for(ReasoningTask.QUIZ, provider="mistral", model_name=LARGE_4)
        is None
    )
    kwargs = {"response_format": {"type": "json_object"}}
    apply_reasoning_effort(
        kwargs, ReasoningTask.QUIZ, provider="mistral", model_name=LARGE_4
    )
    assert kwargs == {"response_format": {"type": "json_object"}}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("none", "none"),
        ("minimal", "none"),
        ("low", "none"),
        ("LOW", "none"),
        ("medium", "high"),
        ("high", "high"),
        ("off", None),
        ("provider_default", None),
    ],
)
def test_quiz_env_maps_onto_documented_wire_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str | None
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", raw)
    assert (
        reasoning_effort_for(
            ReasoningTask.QUIZ, provider="mistral", model_name="mistral-large-4-0"
        )
        == expected
    )


def test_other_provider_and_other_mistral_model_send_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_DEFAULT", "high")
    assert (
        reasoning_effort_for(ReasoningTask.CHAT, provider="openai", model_name="gpt-4o")
        is None
    )
    assert (
        reasoning_effort_for(
            ReasoningTask.CHAT, provider="mistral", model_name="mistral-large-latest"
        )
        is None
    )
    assert (
        reasoning_effort_for(
            ReasoningTask.CHAT,
            provider="mistral",
            model_name="mistral-small-latest",
        )
        == "high"
    )
    assert (
        reasoning_effort_for(
            ReasoningTask.CHAT, provider="mistral", model_name="mistral-medium-3-5"
        )
        == "high"
    )


def test_task_override_beats_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_DEFAULT", "high")
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", "none")
    assert (
        reasoning_effort_for(ReasoningTask.QUIZ, provider="mistral", model_name=LARGE_4)
        == "none"
    )
    assert (
        reasoning_effort_for(ReasoningTask.CHAT, provider="mistral", model_name=LARGE_4)
        == "high"
    )


def test_invalid_value_warns_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", "turbo")
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        effort = reasoning_effort_for(
            ReasoningTask.QUIZ, provider="mistral", model_name=LARGE_4
        )
    finally:
        logger.remove(sink)
    assert effort is None
    assert any("turbo" in message for message in messages)


def test_invalid_task_value_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", "turbo")
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_DEFAULT", "low")
    assert (
        reasoning_effort_for(ReasoningTask.QUIZ, provider="mistral", model_name=LARGE_4)
        == "none"
    )


def test_apply_keeps_structured_output_beside_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", "high")
    kwargs = {"response_format": {"type": "json_object"}}
    apply_reasoning_effort(
        kwargs, ReasoningTask.QUIZ, provider="mistral", model_name=LARGE_4
    )
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["reasoning_effort"] == "high"


@dataclass
class _StubLanguage(LanguageModel):
    def _get_models(self):
        return []

    @property
    def provider(self) -> str:
        return self._provider_name

    def _get_default_model(self) -> str:
        return LARGE_4

    def to_langchain(self):
        return self._chat

    def chat_complete(self, *args, **kwargs):
        raise NotImplementedError

    async def achat_complete(self, *args, **kwargs):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_provision_omits_effort_and_keeps_response_format_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = type("Chat", (), {})()
    chat.model_kwargs = {"response_format": {"type": "json_object"}}
    language = _StubLanguage(model_name=LARGE_4)
    language._provider_name = "mistral"
    language._chat = chat
    seen: dict[str, object] = {}

    async def get_model(model_id: str, **kwargs: object) -> _StubLanguage:
        seen["model_id"] = model_id
        seen["kwargs"] = kwargs
        return language

    monkeypatch.setattr("open_notebook.ai.provision.model_manager.get_model", get_model)
    result = await provision_langchain_model(
        "hello",
        "model:1",
        "transformation",
        task=ReasoningTask.QUIZ,
        max_tokens=8192,
        structured={"type": "json"},
    )
    assert seen["kwargs"] == {"max_tokens": 8192, "structured": {"type": "json"}}
    assert "reasoning_effort" not in seen["kwargs"]
    assert result.model_kwargs["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in result.model_kwargs


class _CopyChat:
    """Stand-in with pydantic's model_copy so provision can avoid in-place kwargs."""

    def __init__(self, model_kwargs: dict[str, object]) -> None:
        self.model_kwargs = model_kwargs
        self.callbacks: list[object] | None = None

    def model_copy(
        self, *, update: dict[str, object] | None = None, deep: bool = False
    ) -> _CopyChat:
        del deep
        copied = _CopyChat(dict(self.model_kwargs))
        copied.callbacks = list(self.callbacks) if self.callbacks else None
        if update:
            for key, value in update.items():
                setattr(copied, key, value)
        return copied


@pytest.mark.asyncio
async def test_provision_sets_effort_only_when_env_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", "low")
    original = {"response_format": {"type": "json_object"}}
    chat = _CopyChat(original)
    language = _StubLanguage(model_name="mistral-large-4-0")
    language._provider_name = "mistral"
    language._chat = chat

    async def get_model(model_id: str, **kwargs: object) -> _StubLanguage:
        return language

    monkeypatch.setattr("open_notebook.ai.provision.model_manager.get_model", get_model)
    result = await provision_langchain_model(
        "hello",
        "model:1",
        "transformation",
        task=ReasoningTask.QUIZ,
        max_tokens=8192,
        structured={"type": "json"},
    )
    assert result is not chat
    assert original == {"response_format": {"type": "json_object"}}
    assert "reasoning_effort" not in chat.model_kwargs
    assert result.model_kwargs["reasoning_effort"] == "none"
    assert result.model_kwargs["response_format"] == {"type": "json_object"}
