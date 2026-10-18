"""Langfuse tracing stays off unless both API keys are set."""

import builtins
import json
import os
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from open_notebook.ai.langfuse_tracing import (
    DEFAULT_LANGFUSE_HOST,
    langfuse_callbacks,
    mask_langfuse_payload,
    parse_langfuse_settings,
    with_langfuse_callbacks,
)

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
