"""Optional reasoning effort for Mistral language-model calls.

Unset means today's request: no ``reasoning_effort`` field. An environment
variable can ask for a level. The wire values are the ones Mistral documents
for adjustable reasoning, ``none`` and ``high``.

https://docs.mistral.ai/studio/conversations/reasoning
"""

import os
from enum import StrEnum

from loguru import logger

# Documented Mistral wire values are only "none" and "high". "low" is not
# documented for these models, so the table sends it as "none". "off" and
# "provider_default" send no field at all.
REASONING_EFFORT_BY_LEVEL: dict[str, str | None] = {
    "none": "none",
    "minimal": "none",
    "low": "none",
    "medium": "high",
    "high": "high",
    "off": None,
    "provider_default": None,
}

_SUPPORTED_PREFIXES = ("mistral-large-4", "mistral-medium-3-5")
_SUPPORTED_EXACT = frozenset({"mistral-small-latest"})


class ReasoningTask(StrEnum):
    """Language-model jobs that can carry an optional reasoning level."""

    CHAT = "chat"
    ASK = "ask"
    SOURCE_CHAT = "source_chat"
    SHORT = "short"
    TRANSFORMATION = "transformation"
    QUIZ = "quiz"


def reasoning_env_name(task: ReasoningTask) -> str:
    return f"OPEN_NOTEBOOK_REASONING_{task.value.upper()}"


def reasoning_effort_for(
    task: ReasoningTask,
    *,
    provider: str,
    model_name: str,
) -> str | None:
    """Wire value to put on the request, or None when the field must be absent."""
    level = _level_from_env(task)
    if level is None:
        return None
    if not _mistral_reasoning_model(provider, model_name):
        return None
    return level


def apply_reasoning_effort(
    model_kwargs: dict[str, object],
    task: ReasoningTask,
    *,
    provider: str,
    model_name: str,
) -> None:
    """Set ``reasoning_effort`` on an existing kwargs dict, or leave it untouched.

    Structured output already stores ``response_format`` in this dict. The
    effort is a sibling key. Replacing the dict would drop that format.
    """
    effort = reasoning_effort_for(task, provider=provider, model_name=model_name)
    if effort is None:
        return
    model_kwargs["reasoning_effort"] = effort


def _level_from_env(task: ReasoningTask) -> str | None:
    task_raw = os.environ.get(reasoning_env_name(task))
    if task_raw is not None and task_raw.strip():
        parsed, valid = _parse_level(task_raw, reasoning_env_name(task))
        if valid:
            return parsed
    default_raw = os.environ.get("OPEN_NOTEBOOK_REASONING_DEFAULT")
    if default_raw is not None and default_raw.strip():
        parsed, valid = _parse_level(default_raw, "OPEN_NOTEBOOK_REASONING_DEFAULT")
        if valid:
            return parsed
    return None


def _parse_level(raw: str, source: str) -> tuple[str | None, bool]:
    key = raw.strip().lower()
    if key not in REASONING_EFFORT_BY_LEVEL:
        logger.warning(
            f"Ignoring invalid {source}={raw!r}. "
            "Falling back to the reasoning default, which sends no reasoning_effort."
        )
        return None, False
    return REASONING_EFFORT_BY_LEVEL[key], True


def _mistral_reasoning_model(provider: str, model_name: str) -> bool:
    if provider.strip().lower().replace("_", "-") != "mistral":
        return False
    name = model_name.strip().lower()
    if name in _SUPPORTED_EXACT:
        return True
    return any(name.startswith(prefix) for prefix in _SUPPORTED_PREFIXES)
