"""Optional Langfuse tracing for LangChain model calls.

Tracing stays off unless both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are
set. The Langfuse SDK is imported only then, so a process without keys never
loads the package and never opens a connection.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from loguru import logger

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


def langfuse_callbacks() -> list[BaseCallbackHandler]:
    """Callbacks for the current process. Empty when tracing is off."""
    settings = parse_langfuse_settings(os.environ)
    if settings is None:
        return []

    # The client reads LANGFUSE_HOST at construction. LANGFUSE_BASE_URL is
    # only an alias here, so copy the resolved host before the SDK is imported.
    os.environ["LANGFUSE_HOST"] = settings.host
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning(
            "Langfuse keys are set but the langfuse package is not installed. "
            "LLM calls will run without tracing."
        )
        return []
    try:
        handler: BaseCallbackHandler = CallbackHandler()
    except Exception:
        # A broken tracer must not fail the model call it was meant to observe.
        logger.exception(
            "Langfuse tracing could not start. LLM calls will run without tracing."
        )
        return []
    return [handler]


def with_langfuse_callbacks(model: BaseChatModel) -> BaseChatModel:
    """Return a model that reports calls to Langfuse.

    The same model is returned when tracing is off.
    """
    callbacks = langfuse_callbacks()
    if not callbacks:
        return model
    try:
        # to_langchain() can return a cached instance. Copy it so concurrent
        # provisions do not share one callbacks list.
        return model.model_copy(update={"callbacks": callbacks})
    except Exception:
        logger.exception(
            "Could not attach Langfuse callbacks. The model call will run without tracing."
        )
        return model
