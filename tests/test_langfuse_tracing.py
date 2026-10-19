"""Langfuse tracing stays off unless both API keys are set."""

import builtins
import json
import os
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from esperanto import AIFactory, LanguageModel
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langfuse import Langfuse
from langfuse._client.resource_manager import LangfuseResourceManager
from loguru import logger
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from open_notebook.ai.langfuse_tracing import (
    DEFAULT_LANGFUSE_HOST,
    _install_trace_mask,
    langfuse_callbacks,
    mask_langfuse_payload,
    parse_langfuse_settings,
    parse_trace_tags,
    with_langfuse_callbacks,
)
from open_notebook.ai.provision import provision_langchain_model
from open_notebook.ai.reasoning import ReasoningTask
from open_notebook.graphs.chat import ThreadState, call_model_with_messages
from open_notebook.graphs.quiz import _reply
from open_notebook.graphs.transformation import run_transformation

_LANGFUSE_ENV = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "LANGFUSE_BASE_URL",
)


def _clear_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LANGFUSE_ENV:
        monkeypatch.delenv(name, raising=False)


@contextmanager
def _forbid_langfuse_import(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("langfuse."):
            raise AssertionError(f"imported {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    yield


def test_keys_unset_returns_empty_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_langfuse_env(monkeypatch)
    model = object()

    with _forbid_langfuse_import(monkeypatch):
        assert langfuse_callbacks() == []
        assert with_langfuse_callbacks(model) is model  # type: ignore[arg-type]

    assert os.environ.get("LANGFUSE_HOST", "") == ""


def test_unset_keys_leave_an_existing_host_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_HOST", "https://kept.example")

    with _forbid_langfuse_import(monkeypatch):
        assert langfuse_callbacks() == []

    assert os.environ["LANGFUSE_HOST"] == "https://kept.example"


@pytest.mark.parametrize(
    "env",
    [
        {"LANGFUSE_PUBLIC_KEY": "pk-lf-placeholder"},
        {"LANGFUSE_SECRET_KEY": "sk-lf-placeholder"},
        {
            "LANGFUSE_PUBLIC_KEY": "   ",
            "LANGFUSE_SECRET_KEY": "sk-lf-placeholder",
        },
        {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-placeholder",
            "LANGFUSE_SECRET_KEY": "   ",
        },
    ],
)
def test_incomplete_keys_return_empty_callbacks(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    _clear_langfuse_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with _forbid_langfuse_import(monkeypatch):
        assert langfuse_callbacks() == []
        assert parse_langfuse_settings(os.environ) is None


def test_default_host_when_keys_are_set_and_host_is_blank() -> None:
    settings = parse_langfuse_settings(
        {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-placeholder",
            "LANGFUSE_SECRET_KEY": "sk-lf-placeholder",
            "LANGFUSE_HOST": "  ",
            "LANGFUSE_BASE_URL": "",
        }
    )
    assert settings is not None
    assert settings.host == DEFAULT_LANGFUSE_HOST


def test_base_url_is_used_when_host_is_unset() -> None:
    settings = parse_langfuse_settings(
        {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-placeholder",
            "LANGFUSE_SECRET_KEY": "sk-lf-placeholder",
            "LANGFUSE_BASE_URL": "https://langfuse.example",
        }
    )
    assert settings is not None
    assert settings.host == "https://langfuse.example"


def test_configured_call_attaches_handler_on_the_langchain_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from esperanto import AIFactory
    from langchain_core.callbacks import BaseCallbackHandler

    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-placeholder")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-placeholder")
    # Register the key so teardown restores the process value after we write it.
    monkeypatch.setenv("LANGFUSE_HOST", "")

    class Sentinel(BaseCallbackHandler):
        pass

    handler = Sentinel()
    constructed: dict[str, object] = {}

    class _Langfuse:
        def __init__(self, **kwargs: object) -> None:
            constructed.update(kwargs)

    package = types.ModuleType("langfuse")
    package.__path__ = []  # type: ignore[attr-defined]
    package.Langfuse = _Langfuse  # type: ignore[attr-defined]
    langchain_mod = types.ModuleType("langfuse.langchain")
    langchain_mod.CallbackHandler = lambda: handler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", package)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", langchain_mod)

    model = AIFactory.create_language(
        "anthropic", "claude-sonnet-5", config={"api_key": "test"}
    ).to_langchain()
    traced = with_langfuse_callbacks(model)

    assert traced is not model
    assert traced.callbacks == [handler]
    assert not model.callbacks
    assert os.environ["LANGFUSE_HOST"] == DEFAULT_LANGFUSE_HOST
    assert constructed["mask"] is mask_langfuse_payload
    assert constructed["host"] == DEFAULT_LANGFUSE_HOST


def test_mask_drops_thinking_from_the_payload_langfuse_sends() -> None:
    secret = "SECRET-REASONING-9f3a"
    nested = "NESTED-THINKING-TEXT"
    reasoning = "ALSO-SECRET-REASONING"
    tagged = "TAGGED-THINKING-BLOCK"
    payload = {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": [{"type": "text", "text": nested}],
                "text": secret,
            },
            {"type": "reasoning", "reasoning": reasoning, "text": reasoning},
            {"type": "text", "text": f"<think>{tagged}</think>The visible answer"},
        ],
        "additional_kwargs": {},
    }

    sent = mask_langfuse_payload(data=payload)
    encoded = json.dumps(sent)

    assert secret not in encoded
    assert nested not in encoded
    assert reasoning not in encoded
    assert tagged not in encoded
    assert "The visible answer" in encoded
    assert sent["content"][0]["type"] == "text"


def test_host_wins_over_base_url() -> None:
    settings = parse_langfuse_settings(
        {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-placeholder",
            "LANGFUSE_SECRET_KEY": "sk-lf-placeholder",
            "LANGFUSE_HOST": "https://host.example",
            "LANGFUSE_BASE_URL": "https://base.example",
        }
    )
    assert settings is not None
    assert settings.host == "https://host.example"


def test_parse_trace_tags_trims_and_drops_empty_parts() -> None:
    assert parse_trace_tags({}) == ()
    assert parse_trace_tags(
        {"OPEN_NOTEBOOK_TRACE_TAGS": " alpha , , beta , alpha "}
    ) == (
        "alpha",
        "beta",
    )


def test_tracing_off_ignores_trace_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("OPEN_NOTEBOOK_TRACE_TAGS", "should-not-attach")
    model = object()

    with _forbid_langfuse_import(monkeypatch):
        traced = with_langfuse_callbacks(
            model,  # type: ignore[arg-type]
            on_task="quiz",
            reasoning_effort="none",
        )
        assert traced is model


def _enable_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_langfuse_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-placeholder")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-placeholder")
    monkeypatch.setenv("LANGFUSE_HOST", "")

    class _Langfuse:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

    package = types.ModuleType("langfuse")
    package.__path__ = []  # type: ignore[attr-defined]
    package.Langfuse = _Langfuse  # type: ignore[attr-defined]
    langchain_mod = types.ModuleType("langfuse.langchain")

    class _Handler:
        pass

    langchain_mod.CallbackHandler = _Handler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", package)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", langchain_mod)


class _CopyChat:
    def __init__(self) -> None:
        self.model_kwargs: dict[str, object] | None = {
            "response_format": {"type": "json_object"}
        }
        self.tags: list[str] | None = ["kept"]
        self.metadata: dict[str, object] | None = {"caller": "kept"}
        self.callbacks: list[object] | None = None

    def model_copy(
        self, *, update: dict[str, object] | None = None, deep: bool = False
    ) -> "_CopyChat":
        del deep
        copied = _CopyChat()
        if isinstance(self.model_kwargs, dict):
            copied.model_kwargs = dict(self.model_kwargs)
        else:
            copied.model_kwargs = self.model_kwargs
        copied.tags = list(self.tags) if self.tags is not None else None
        copied.metadata = dict(self.metadata) if self.metadata is not None else None
        copied.callbacks = list(self.callbacks) if self.callbacks else None
        if update:
            for key, value in update.items():
                setattr(copied, key, value)
        return copied


@dataclass
class _StubLanguage(LanguageModel):
    def _get_models(self):
        return []

    @property
    def provider(self) -> str:
        return "mistral"

    def _get_default_model(self) -> str:
        return "mistral-large-4"

    def to_langchain(self) -> _CopyChat:
        return self._chat

    def chat_complete(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError

    async def achat_complete(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError


def _patch_get_model(monkeypatch: pytest.MonkeyPatch, chat: _CopyChat) -> None:
    language = _StubLanguage(model_name="mistral-large-4")
    language._chat = chat

    async def get_model(model_id: str, **kwargs: object) -> _StubLanguage:
        del model_id, kwargs
        return language

    monkeypatch.setattr("open_notebook.ai.provision.model_manager.get_model", get_model)


def test_traced_model_carries_tags_and_run_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_tracing(monkeypatch)
    monkeypatch.setenv("OPEN_NOTEBOOK_TRACE_TAGS", " alpha , , beta , alpha ")
    chat = _CopyChat()

    traced = with_langfuse_callbacks(chat, on_task="quiz", reasoning_effort="none")

    assert traced is not chat
    assert chat.tags == ["kept"]
    assert chat.metadata == {"caller": "kept"}
    assert traced.tags == ["kept", "alpha", "beta"]
    assert traced.metadata == {
        "caller": "kept",
        "on_task": "quiz",
        "reasoning_effort": "none",
        "langfuse_tags": ["alpha", "beta"],
    }


@pytest.mark.asyncio
async def test_provision_records_sent_effort_and_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_tracing(monkeypatch)
    monkeypatch.delenv("OPEN_NOTEBOOK_REASONING_QUIZ", raising=False)
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", "low")
    monkeypatch.setenv("OPEN_NOTEBOOK_TRACE_TAGS", "variant-a")
    chat = _CopyChat()
    _patch_get_model(monkeypatch, chat)
    result = await provision_langchain_model(
        "hello",
        "model:1",
        "transformation",
        task=ReasoningTask.QUIZ,
    )

    assert chat.model_kwargs == {"response_format": {"type": "json_object"}}
    assert chat.metadata == {"caller": "kept"}
    assert chat.tags == ["kept"]
    assert result.model_kwargs["reasoning_effort"] == "none"
    assert result.tags == ["kept", "variant-a"]
    assert result.metadata["on_task"] == "quiz"
    assert result.metadata["reasoning_effort"] == "none"
    assert result.metadata["langfuse_tags"] == ["variant-a"]
    assert result.metadata["caller"] == "kept"


@pytest.mark.asyncio
async def test_provision_records_provider_default_when_nothing_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_tracing(monkeypatch)
    monkeypatch.delenv("OPEN_NOTEBOOK_TRACE_TAGS", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_REASONING_QUIZ", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_REASONING_DEFAULT", raising=False)
    chat = _CopyChat()
    chat.model_kwargs = None
    _patch_get_model(monkeypatch, chat)
    with_task = await provision_langchain_model(
        "hello",
        "model:1",
        "transformation",
        task=ReasoningTask.QUIZ,
    )
    without_task = await provision_langchain_model(
        "hello",
        "model:1",
        "transformation",
    )
    monkeypatch.setenv("OPEN_NOTEBOOK_REASONING_QUIZ", "low")
    asked_but_not_sent = await provision_langchain_model(
        "hello",
        "model:1",
        "transformation",
        task=ReasoningTask.QUIZ,
    )

    assert chat.model_kwargs is None
    assert chat.metadata == {"caller": "kept"}
    assert "langfuse_tags" not in with_task.metadata
    assert with_task.tags == ["kept"]
    assert with_task.metadata["on_task"] == "quiz"
    assert with_task.metadata["reasoning_effort"] == "provider_default"
    assert without_task.metadata["on_task"] == "none"
    assert without_task.metadata["reasoning_effort"] == "provider_default"
    assert asked_but_not_sent.model_kwargs is None
    assert asked_but_not_sent.metadata["reasoning_effort"] == "provider_default"


SPAN_THINKING = "SPAN-THINKING-SECRET-19a"
SOURCE_LEAD = "The paper says"
SOURCE_TAIL = "KEEP-SOURCE-TAIL-4b"
SOURCE_TEXT = f"{SOURCE_LEAD} </think> {SOURCE_TAIL}"
_SPAN_KEY = "pk-lf-span-mask-test"
_span_client: Langfuse | None = None
_span_exporter: InMemorySpanExporter | None = None


class ScriptedChat(BaseChatModel):
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=[
                            {
                                "type": "thinking",
                                "thinking": [{"type": "text", "text": SPAN_THINKING}],
                            },
                            {
                                "type": "text",
                                "text": f"<think>{SPAN_THINKING}</think>Visible answer",
                            },
                        ]
                    )
                )
            ]
        )

    @property
    def _llm_type(self) -> str:
        return "scripted"


def test_mask_cleans_assistant_strings_and_keeps_source_text() -> None:
    secret = "TAGGED-SECRET-2c"
    payload = [
        {"role": "user", "content": SOURCE_TEXT},
        {
            "role": "user",
            "content": [
                {"type": "thinking", "thinking": [{"type": "text", "text": secret}]}
            ],
        },
        {"role": "assistant", "content": f"<think>{secret}</think>Visible answer"},
        {"note": f"meta {SOURCE_TEXT}"},
    ]

    sent = mask_langfuse_payload(data=payload)
    encoded = json.dumps(sent)

    assert secret not in encoded
    assert SOURCE_LEAD in encoded
    assert SOURCE_TAIL in encoded
    assert sent[2]["content"] == "Visible answer"
    assert sent[3]["note"] == f"meta {SOURCE_TEXT}"


def _warning_text(call) -> str:
    records: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        call()
    finally:
        logger.remove(sink)
    return "\n".join(records)


def test_mask_install_warns_when_the_client_is_missing() -> None:
    text = _warning_text(lambda: _install_trace_mask("pk-lf-not-registered"))
    assert "thinking mask was not installed" in text
    assert "no client is registered for this key" in text


def test_mask_install_warns_when_the_module_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "langfuse._client.resource_manager", raising=False)
    text = _warning_text(lambda: _install_trace_mask("pk-lf-not-registered"))
    assert "client module is not loaded" in text


def test_mask_install_warns_when_the_registry_is_not_a_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LangfuseResourceManager, "_instances", None)
    text = _warning_text(lambda: _install_trace_mask("pk-lf-not-registered"))
    assert "client registry is missing" in text


def _reject_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected network call to {request.url}")


@pytest.fixture
def recorded_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Langfuse, InMemorySpanExporter]]:
    global _span_client, _span_exporter
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", _SPAN_KEY)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-placeholder")
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.invalid")
    monkeypatch.setenv("LANGFUSE_MEDIA_UPLOAD_ENABLED", "false")
    if _span_client is None:
        exporter = InMemorySpanExporter()
        _span_exporter = exporter
        _span_client = Langfuse(
            public_key=_SPAN_KEY,
            secret_key="sk-lf-placeholder",
            host="https://langfuse.invalid",
            mask=mask_langfuse_payload,
            span_exporter=exporter,
            tracer_provider=TracerProvider(),
            httpx_client=httpx.Client(transport=httpx.MockTransport(_reject_network)),
        )
    assert _span_client is not None
    assert _span_exporter is not None
    assert list(LangfuseResourceManager._instances) == [_SPAN_KEY]
    _span_exporter.clear()
    yield _span_client, _span_exporter


def _patch_scripted_model(monkeypatch: pytest.MonkeyPatch) -> None:
    language_model = AIFactory.create_language(
        "anthropic", "claude-sonnet-5", config={"api_key": "test"}
    )

    async def get_model(*args: object, **kwargs: object) -> LanguageModel:
        del args, kwargs
        return language_model

    monkeypatch.setattr("open_notebook.ai.provision.model_manager.get_model", get_model)
    monkeypatch.setattr(
        "open_notebook.ai.provision.model_manager.get_default_model", get_model
    )
    monkeypatch.setattr(
        type(language_model), "to_langchain", lambda self: ScriptedChat()
    )


def _chat_state() -> ThreadState:
    return {
        "messages": [HumanMessage(content=SOURCE_TEXT)],
        "notebook": None,
        "context": None,
        "context_config": None,
        "model_override": None,
    }


def _assert_masked_span(client: Langfuse, exporter: InMemorySpanExporter) -> str:
    client.flush()
    spans = exporter.get_finished_spans()
    blob = json.dumps(
        [dict(span.attributes) for span in spans if span.attributes],
        default=str,
    )
    assert spans, "no span exported"
    assert SPAN_THINKING not in blob
    assert SOURCE_LEAD in blob
    assert SOURCE_TAIL in blob
    assert "Visible answer" in blob
    return blob


def test_chat_span_omits_thinking(
    recorded_spans: tuple[Langfuse, InMemorySpanExporter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, exporter = recorded_spans
    _patch_scripted_model(monkeypatch)
    call_model_with_messages(_chat_state(), {"configurable": {}})
    blob = _assert_masked_span(client, exporter)
    assert "chat" in blob


@pytest.mark.asyncio
async def test_transformation_span_omits_thinking(
    recorded_spans: tuple[Langfuse, InMemorySpanExporter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, exporter = recorded_spans
    _patch_scripted_model(monkeypatch)
    with patch(
        "open_notebook.graphs.transformation.DefaultPrompts",
        return_value=SimpleNamespace(transformation_instructions=None),
    ):
        result = await run_transformation(
            {
                "input_text": SOURCE_TEXT,
                "transformation": SimpleNamespace(title="Summary", prompt="Summarize"),
            },
            {"configurable": {}},
        )
    assert result["output"] == "Visible answer"
    blob = _assert_masked_span(client, exporter)
    assert "transformation" in blob


@pytest.mark.asyncio
async def test_quiz_span_omits_thinking(
    recorded_spans: tuple[Langfuse, InMemorySpanExporter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, exporter = recorded_spans
    _patch_scripted_model(monkeypatch)
    model = await provision_langchain_model(
        SOURCE_TEXT,
        "model:1",
        "transformation",
        task=ReasoningTask.QUIZ,
    )
    visible = await _reply(model, SOURCE_TEXT, timeout=None)
    assert visible == "Visible answer"
    blob = _assert_masked_span(client, exporter)
    assert "quiz" in blob
