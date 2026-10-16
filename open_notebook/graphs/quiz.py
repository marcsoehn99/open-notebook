import random
import uuid

from ai_prompter import Prompter
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.domain.quiz import (
    QUIZ_PROMPT_TOKEN_LIMIT,
    GeneratedDeck,
    GeneratedQuestion,
    QuizSettings,
    SealedQuestion,
    bloom_for,
    parse_generated,
    seal_deck,
    template_data,
)
from open_notebook.exceptions import OpenNotebookError
from open_notebook.utils import clean_thinking_content, token_count
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content


class QuizState(TypedDict, total=False):
    quiz_id: str
    model_id: str
    slices: dict[str, str]
    settings: QuizSettings
    deck: tuple[SealedQuestion, ...]


def _refuse_oversized(prompt: str) -> None:
    counted = token_count(prompt)
    if counted > QUIZ_PROMPT_TOKEN_LIMIT:
        raise ValueError(
            f"quiz prompt has {counted} tokens, above {QUIZ_PROMPT_TOKEN_LIMIT}"
        )


async def _reply(model: BaseChatModel, prompt: str) -> str:
    message = await model.ainvoke(prompt)
    return clean_thinking_content(extract_text_content(message.content))


async def generate_node(state: QuizState, config: RunnableConfig) -> dict:
    del config
    settings = state["settings"]
    slices = state["slices"]
    allowed = frozenset(settings.question_types)
    parser: PydanticOutputParser[GeneratedDeck] = PydanticOutputParser(
        pydantic_object=GeneratedDeck
    )
    prompter = Prompter(
        prompt_template="quiz/generate",
        parser=parser,  # type: ignore[arg-type]
    )
    prompt = prompter.render(
        data=template_data(settings, slices, bloom_for(settings.difficulty))
    )
    _refuse_oversized(prompt)
    try:
        model = await provision_langchain_model(
            prompt,
            state["model_id"],
            "transformation",
            max_tokens=8192,
            structured={"type": "json"},
        )

        async def _parsed(text: str) -> tuple[GeneratedQuestion, ...]:
            return parse_generated(
                text,
                num_questions=settings.num_questions,
                allowed_types=allowed,
                slices=slices,
            )

        try:
            questions = await _parsed(await _reply(model, prompt))
        except ValueError as error:
            repair = (
                f"{prompt}\n\nThe previous JSON failed validation: {error}\n"
                "Return only the JSON object."
            )
            _refuse_oversized(repair)
            questions = await _parsed(await _reply(model, repair))
        deck = seal_deck(questions, random.Random(), lambda: str(uuid.uuid4()))
        return {"deck": deck}
    except (ValueError, OpenNotebookError):
        raise
    except Exception as error:
        error_class, message = classify_error(error)
        raise error_class(message) from error


_graph = StateGraph(QuizState)
_graph.add_node("generate", generate_node)  # type: ignore[type-var]
_graph.add_edge(START, "generate")
_graph.add_edge("generate", END)
quiz_graph = _graph.compile()
