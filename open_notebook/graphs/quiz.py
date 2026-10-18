import random
import uuid

from ai_prompter import Prompter
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.ai.reasoning import ReasoningTask
from open_notebook.domain.quiz import (
    QUIZ_PROMPT_TOKEN_LIMIT,
    QUIZ_REPAIR_ROUNDS,
    GeneratedDeck,
    GeneratedQuestion,
    QuestionType,
    QuizSettings,
    SealedQuestion,
    bloom_for,
    faults_for,
    full_repair_prompt,
    load_generated_questions,
    merge_repaired,
    nothing_kept,
    partial_repair_prompt,
    seal_deck,
    template_data,
    unusable_repair_prompt,
)
from open_notebook.exceptions import (
    OpenNotebookError,
    QuizModelOutputError,
    QuizPromptTooLargeError,
)
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
        raise QuizPromptTooLargeError(
            f"quiz prompt has {counted} tokens, above {QUIZ_PROMPT_TOKEN_LIMIT}"
        )


async def _reply(model: BaseChatModel, prompt: str) -> str:
    message = await model.ainvoke(prompt)
    return clean_thinking_content(extract_text_content(message.content))


def _load_questions(
    text: str,
) -> tuple[tuple[GeneratedQuestion, ...] | None, QuizModelOutputError | None]:
    try:
        return load_generated_questions(text), None
    except QuizModelOutputError as error:
        return None, error


async def _questions_with_repair(
    model: BaseChatModel,
    prompt: str,
    *,
    num_questions: int,
    allowed_types: frozenset[QuestionType],
    slices: dict[str, str],
) -> tuple[GeneratedQuestion, ...]:
    """Keep questions that pass. Rewrite only the failures, at most twice.

    A reply that cannot be parsed, or that contains no keepable question, is
    rewritten once as a whole deck. That rewrite counts as one of the two
    repair rounds.
    """
    rounds = 0
    full_rewrites = 0

    async def ask(text: str) -> str:
        _refuse_oversized(text)
        return await _reply(model, text)

    questions, error = _load_questions(await ask(prompt))
    if questions is None:
        rounds += 1
        full_rewrites += 1
        if error is None:
            raise QuizModelOutputError("quiz JSON did not match the schema")
        questions, error = _load_questions(
            await ask(full_repair_prompt(prompt, str(error)))
        )
        if questions is None:
            if error is None:
                raise QuizModelOutputError("quiz JSON did not match the schema")
            raise error

    while rounds < QUIZ_REPAIR_ROUNDS:
        faults = faults_for(
            questions,
            num_questions=num_questions,
            allowed_types=allowed_types,
            slices=slices,
        )
        if not faults:
            return questions
        rounds += 1
        if nothing_kept(questions, faults):
            if full_rewrites >= 1:
                raise QuizModelOutputError(faults[0].reason)
            full_rewrites += 1
            questions, error = _load_questions(
                await ask(unusable_repair_prompt(prompt, questions, faults))
            )
            if questions is None:
                if error is None:
                    raise QuizModelOutputError("quiz JSON did not match the schema")
                raise error
            continue
        indexed = tuple(fault for fault in faults if fault.index >= 0)
        repaired, error = _load_questions(
            await ask(partial_repair_prompt(prompt, questions, indexed))
        )
        if repaired is None or len(repaired) != len(indexed):
            continue
        questions = merge_repaired(questions, indexed, repaired)

    faults = faults_for(
        questions,
        num_questions=num_questions,
        allowed_types=allowed_types,
        slices=slices,
    )
    if faults:
        raise QuizModelOutputError(faults[0].reason)
    return questions


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
            task=ReasoningTask.QUIZ,
            max_tokens=8192,
            structured={"type": "json"},
        )

        questions = await _questions_with_repair(
            model,
            prompt,
            num_questions=settings.num_questions,
            allowed_types=allowed,
            slices=slices,
        )
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
