"""Optional Langfuse tracing for LangChain model calls.

Tracing stays off unless both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are
set. The Langfuse SDK is imported only then, so a process without keys never
loads the package and never opens a connection.
"""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from loguru import logger

from open_notebook.utils.text_utils import clean_thinking_content

DEFAULT_LANGFUSE_HOST = "https://cloud.langfuse.com"


@dataclass(frozen=True)
class LangfuseSettings:
    """Tracing is on. `host` is the base URL the SDK should use."""

    host: str


def _value(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip()


def parse_langfuse_settings(env: Mapping[str, str]) -> LangfuseSettings | None:
    """Return settings when both keys are present. Otherwise tracing is off."""
    if not _value(env, "LANGFUSE_PUBLIC_KEY") or not _value(env, "LANGFUSE_SECRET_KEY"):
        return None
    host = (
        _value(env, "LANGFUSE_HOST")
        or _value(env, "LANGFUSE_BASE_URL")
        or DEFAULT_LANGFUSE_HOST
    )
    return LangfuseSettings(host=host)


class _Dropped:
    """Marks a thinking part that must not be copied into a trace payload."""


_DROPPED = _Dropped()
_THINKING_PART_TYPES = frozenset({"thinking", "reasoning"})


_ASSISTANT_ROLES = frozenset({"assistant", "ai"})
_MESSAGE_ROLES = frozenset(
    {"assistant", "ai", "user", "human", "system", "tool", "function"}
)


def mask_langfuse_payload(*, data: Any, **kwargs: Any) -> Any:
    """Return a copy of a Langfuse field with thinking and reasoning removed.

    The Langfuse client calls this on input, output, and metadata before it
    stores the span. The return value is what gets exported.

    A block whose type is ``thinking`` or ``reasoning`` is dropped wherever it
    sits. ``<think>`` tags are removed only inside assistant messages, so a
    source or prompt that quotes those tags stays intact.
    """
    del kwargs
    masked = _mask_trace_value(data, clean_strings=False)
    if masked is _DROPPED:
        return ""
    return masked


def _message_role(value: dict[Any, Any]) -> str | None:
    role = value.get("role")
    if isinstance(role, str) and role.lower() in _MESSAGE_ROLES:
        return role.lower()
    kind = value.get("type")
    if isinstance(kind, str) and kind.lower() in _MESSAGE_ROLES:
        return kind.lower()
    return None


def _mask_trace_value(value: Any, *, clean_strings: bool) -> Any:
    if isinstance(value, str):
        if not clean_strings:
            return value
        return clean_thinking_content(value, size_limit=None)
    if isinstance(value, dict):
        part_type = value.get("type")
        if isinstance(part_type, str) and part_type in _THINKING_PART_TYPES:
            return _DROPPED
        role = _message_role(value)
        child_clean = role in _ASSISTANT_ROLES if role is not None else clean_strings
        masked: dict[Any, Any] = {}
        for key, item in value.items():
            child = _mask_trace_value(item, clean_strings=child_clean)
            if child is _DROPPED:
                continue
            masked[key] = child
        return masked
    if isinstance(value, list):
        return _mask_sequence(value, clean_strings=clean_strings)
    if isinstance(value, tuple):
        return tuple(_mask_sequence(list(value), clean_strings=clean_strings))
    return value


def _mask_sequence(values: list[Any], *, clean_strings: bool) -> list[Any]:
    kept: list[Any] = []
    for item in values:
        child = _mask_trace_value(item, clean_strings=clean_strings)
        if child is _DROPPED:
            continue
        kept.append(child)
    return kept


def _warn_mask_not_installed(detail: str) -> None:
    logger.warning(
        "Langfuse thinking mask was not installed ({}). "
        "Traces may include raw thinking text.",
        detail,
    )


def _install_trace_mask(public_key: str) -> None:
    """Point the process client at the thinking mask.

    Langfuse keeps one resource per public key and ignores ``mask`` on a later
    constructor. ``CallbackHandler`` then copies that stored mask. Assign it
    here so a client created earlier still drops thinking before export.
    """
    manager_module = sys.modules.get("langfuse._client.resource_manager")
    if manager_module is None:
        _warn_mask_not_installed("client module is not loaded")
        return
    manager = getattr(manager_module, "LangfuseResourceManager", None)
    instances = getattr(manager, "_instances", None)
    if not isinstance(instances, dict):
        _warn_mask_not_installed("client registry is missing")
        return
    instance = instances.get(public_key)
    if instance is None:
        _warn_mask_not_installed("no client is registered for this key")
        return
    instance.mask = mask_langfuse_payload


def parse_trace_tags(env: Mapping[str, str]) -> tuple[str, ...]:
    """Tags from ``OPEN_NOTEBOOK_TRACE_TAGS``. Empty parts are dropped."""
    seen: set[str] = set()
    tags: list[str] = []
    for part in env.get("OPEN_NOTEBOOK_TRACE_TAGS", "").split(","):
        tag = part.strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tuple(tags)


def _merged_tags(model: BaseChatModel, tags: tuple[str, ...]) -> list[str] | None:
    if not tags:
        return None
    current = model.tags if isinstance(model.tags, list) else []
    merged = list(current)
    seen = set(merged)
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        merged.append(tag)
    return merged


def _trace_metadata(
    model: BaseChatModel,
    *,
    tags: tuple[str, ...],
    on_task: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    current = model.metadata if isinstance(model.metadata, dict) else {}
    merged = dict(current)
    merged["on_task"] = on_task
    merged["reasoning_effort"] = reasoning_effort
    if tags:
        merged["langfuse_tags"] = list(tags)
    return merged


def langfuse_callbacks() -> list[BaseCallbackHandler]:
    """Callbacks for the current process. Empty when tracing is off."""
    settings = parse_langfuse_settings(os.environ)
    if settings is None:
        return []

    # The client reads LANGFUSE_HOST at construction. LANGFUSE_BASE_URL is
    # only an alias here, so copy the resolved host before the SDK is imported.
    os.environ["LANGFUSE_HOST"] = settings.host
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning(
            "Langfuse keys are set but the langfuse package is not installed. "
            "LLM calls will run without tracing."
        )
        return []
    try:
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        Langfuse(
            public_key=public_key,
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            host=settings.host,
            mask=mask_langfuse_payload,
        )
        _install_trace_mask(public_key)
        handler: BaseCallbackHandler = CallbackHandler()
    except Exception:
        # A broken tracer must not fail the model call it was meant to observe.
        logger.exception(
            "Langfuse tracing could not start. LLM calls will run without tracing."
        )
        return []
    return [handler]


def with_langfuse_callbacks(
    model: BaseChatModel,
    *,
    on_task: str = "none",
    reasoning_effort: str = "provider_default",
) -> BaseChatModel:
    """Return a model that reports calls to Langfuse.

    The same model is returned when tracing is off. When tracing is on, the
    copy carries LangChain tags from ``OPEN_NOTEBOOK_TRACE_TAGS`` and metadata
    ``langfuse_tags``, ``on_task``, and ``reasoning_effort``. ``on_task`` is a
    ``ReasoningTask`` value or ``none``. ``reasoning_effort`` is the value sent
    on the request, or ``provider_default`` when that field is absent.
    """
    callbacks = langfuse_callbacks()
    if not callbacks:
        return model
    tags = parse_trace_tags(os.environ)
    update: dict[str, Any] = {
        "callbacks": callbacks,
        "metadata": _trace_metadata(
            model,
            tags=tags,
            on_task=on_task,
            reasoning_effort=reasoning_effort,
        ),
    }
    merged_tags = _merged_tags(model, tags)
    if merged_tags is not None:
        update["tags"] = merged_tags
    try:
        # to_langchain() can return a cached instance. Copy it so concurrent
        # provisions do not share one callbacks list or one metadata dict.
        return model.model_copy(update=update)
    except Exception:
        logger.exception(
            "Could not attach Langfuse callbacks. The model call will run without tracing."
        )
        return model
