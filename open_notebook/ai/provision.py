from esperanto import LanguageModel
from langchain_core.language_models.chat_models import BaseChatModel
from loguru import logger

from open_notebook.ai.langfuse_tracing import with_langfuse_callbacks
from open_notebook.ai.models import model_manager
from open_notebook.ai.reasoning import (
    ReasoningTask,
    apply_reasoning_effort,
    reasoning_effort_for,
)
from open_notebook.exceptions import ConfigurationError
from open_notebook.utils import token_count


async def provision_langchain_model(
    content,
    model_id,
    default_type,
    *,
    task: ReasoningTask | None = None,
    **kwargs,
) -> BaseChatModel:
    """
    Returns the best model to use based on the context size and on whether there is a specific model being requested in Config.
    If context > 105_000, returns the large_context_model
    If model_id is specified in Config, returns that model
    Otherwise, returns the default model for the given type
    """
    tokens = token_count(content)
    model = None
    selection_reason = ""

    if tokens > 105_000:
        selection_reason = f"large_context (content has {tokens} tokens)"
        logger.debug(
            f"Using large context model because the content has {tokens} tokens"
        )
        model = await model_manager.get_default_model("large_context", **kwargs)
    elif model_id:
        selection_reason = f"explicit model_id={model_id}"
        model = await model_manager.get_model(model_id, **kwargs)
    else:
        selection_reason = f"default for type={default_type}"
        model = await model_manager.get_default_model(default_type, **kwargs)

    logger.debug(f"Using model: {model}")

    if model is None:
        logger.error(
            f"Model provisioning failed: No model found. "
            f"Selection reason: {selection_reason}. "
            f"model_id={model_id}, default_type={default_type}. "
            f"Please check Manage → Models and ensure a default model is configured for '{default_type}'."
        )
        raise ConfigurationError(
            f"No model configured for {selection_reason}. "
            f"Please go to Manage → Models and configure a default model for '{default_type}'."
        )

    if not isinstance(model, LanguageModel):
        logger.error(
            f"Model type mismatch: Expected LanguageModel but got {type(model).__name__}. "
            f"Selection reason: {selection_reason}. "
            f"model_id={model_id}, default_type={default_type}."
        )
        raise ConfigurationError(
            f"Model is not a LanguageModel: {model}. "
            f"Please check that the model configured for '{default_type}' is a language model, not an embedding or speech model."
        )

    langchain_model = model.to_langchain()
    on_task = "none"
    sent_effort = "provider_default"
    if task is not None:
        on_task = task.value
        langchain_model, sent_effort = _copy_with_reasoning_effort(
            langchain_model,
            task,
            provider=model.provider,
            model_name=model.get_model_name() or "",
        )
    return with_langfuse_callbacks(
        langchain_model,
        on_task=on_task,
        reasoning_effort=sent_effort,
    )


def _copy_with_reasoning_effort(
    langchain_model: BaseChatModel,
    task: ReasoningTask,
    *,
    provider: str,
    model_name: str,
) -> tuple[BaseChatModel, str]:
    """Copy the model when effort is set, so the caller's kwargs stay unchanged.

    The second value is the effort written onto the request, or
    ``provider_default`` when the field is left off.
    """
    effort = reasoning_effort_for(task, provider=provider, model_name=model_name)
    if effort is None:
        return langchain_model, "provider_default"
    model_kwargs = getattr(langchain_model, "model_kwargs", None)
    if not isinstance(model_kwargs, dict):
        logger.warning(
            f"Not sending reasoning_effort={effort} for {task.value}: "
            f"model_kwargs is {type(model_kwargs).__name__}, not a dict."
        )
        return langchain_model, "provider_default"
    updated = dict(model_kwargs)
    apply_reasoning_effort(
        updated,
        task,
        provider=provider,
        model_name=model_name,
    )
    copied = langchain_model.model_copy(update={"model_kwargs": updated})
    return copied, effort
