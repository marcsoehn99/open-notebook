import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from ai_prompter import Prompter
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from loguru import logger
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.ai.reasoning import ReasoningTask
from open_notebook.config import llm_timeout_seconds
from open_notebook.domain.quiz import (
    QUIZ_PROMPT_TOKEN_LIMIT,
    QUIZ_REPAIR_ROUNDS,
    Fault,
    GeneratedDeck,
    GeneratedQuestion,
    QuestionFault,
    QuestionType,
    QuizSettings,
    SealedQuestion,
    bloom_for,
    faults_for,
    kept_questions,
    load_generated_questions,
    merge_repaired,
    nothing_kept,
    repair_prompt_data,
    repeats_kept_stem,
    seal_deck,
    template_data,
)
from open_notebook.exceptions import (
    OpenNotebookError,
    QuizModelOutputError,
    QuizPromptTooLargeError,
    QuizTimeoutError,
)
from open_notebook.utils import clean_thinking_content, token_count
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content


class QuizState(TypedDict, total=False):
    quiz_id: str
    model_id: str
    slices: dict[str, str]
    settings: QuizSettings
    deadline: float
    deck: tuple[SealedQuestion, ...]


def _now() -> float:
    return time.monotonic()


@dataclass
class RepairBudget:
    """Repair calls already spent, and how long one more call may take."""

    rounds: int = 0
    full_rewrites: int = 0
    deadline: float | None = None
    call_estimate: float = 0.0

    def out_of_time(self, now: float) -> bool:
        if self.deadline is None:
            return False
        return now + self.call_estimate > self.deadline

    def note_call(self, seconds: float) -> None:
        if seconds > self.call_estimate:
            self.call_estimate = seconds


def _refuse_oversized(prompt: str) -> None:
    counted = token_count(prompt)
    if counted > QUIZ_PROMPT_TOKEN_LIMIT:
        raise QuizPromptTooLargeError(
            f"quiz prompt has {counted} tokens, above {QUIZ_PROMPT_TOKEN_LIMIT}"
        )


def _call_timeout(budget: RepairBudget) -> float | None:
    if budget.deadline is None:
        return None
    return budget.deadline - _now()


def _one_http_attempt(model: BaseChatModel) -> BaseChatModel:
    # langchain_mistralai stops after max_retries attempts. The default of 5
    # lets one ainvoke spend five HTTP timeouts.
    if not isinstance(getattr(model, "max_retries", None), int):
        return model
    copy = getattr(model, "model_copy", None)
    if not callable(copy):
        return model
    return copy(update={"max_retries": 1})


async def _reply(model: BaseChatModel, prompt: str, *, timeout: float | None) -> str:
    if timeout is None:
        message = await model.ainvoke(prompt)
    else:
        message = await asyncio.wait_for(model.ainvoke(prompt), timeout=max(0, timeout))
    return clean_thinking_content(extract_text_content(message.content))


Ask = Callable[[str], Awaitable[str]]


def _question_faults(
    faults: tuple[Fault, ...] | list[Fault],
) -> tuple[QuestionFault, ...]:
    return tuple(fault for fault in faults if isinstance(fault, QuestionFault))


def _render_repair(data: dict[str, object]) -> str:
    return Prompter(prompt_template="quiz/repair").render(data=data)


def _make_ask(model: BaseChatModel, budget: RepairBudget) -> Ask:
    async def ask(text: str) -> str:
        _refuse_oversized(text)
        started = _now()
        try:
            reply = await _reply(model, text, timeout=_call_timeout(budget))
        except TimeoutError as error:
            raise QuizTimeoutError("quiz generation timed out") from error
        budget.note_call(_now() - started)
        return reply

    return ask


async def _full_rewrite(
    ask: Ask,
    budget: RepairBudget,
    *,
    mode: Literal["full", "unusable"],
    error: str,
    questions: tuple[GeneratedQuestion, ...],
    faults: tuple[Fault, ...],
    kept: tuple[GeneratedQuestion, ...],
    slices: dict[str, str],
    format_instructions: str,
    expected_count: int,
    settings: QuizSettings,
) -> tuple[GeneratedQuestion, ...]:
    budget.rounds += 1
    budget.full_rewrites += 1
    rendered = _render_repair(
        repair_prompt_data(
            mode=mode,
            error=error,
            expected_count=expected_count,
            questions=questions,
            faults=faults,
            kept=kept,
            slices=slices,
            format_instructions=format_instructions,
            settings=settings,
        )
    )
    try:
        return load_generated_questions(await ask(rendered))
    except QuizModelOutputError as failure:
        logger.warning(f"quiz repair round {budget.rounds} was not applied: {failure}")
        raise


async def _partial_round(
    ask: Ask,
    budget: RepairBudget,
    *,
    questions: tuple[GeneratedQuestion, ...],
    failing: tuple[QuestionFault, ...],
    kept: tuple[GeneratedQuestion, ...],
    num_questions: int,
    slices: dict[str, str],
    format_instructions: str,
    settings: QuizSettings,
) -> tuple[GeneratedQuestion, ...]:
    in_place = len(questions) == num_questions
    expected = len(failing) if in_place else num_questions - len(kept)
    if expected <= 0:
        return tuple(kept[:num_questions])
    budget.rounds += 1
    rendered = _render_repair(
        repair_prompt_data(
            mode="partial",
            error="",
            expected_count=expected,
            questions=questions,
            faults=failing,
            kept=kept,
            slices=slices,
            format_instructions=format_instructions,
            settings=settings,
        )
    )
    try:
        repaired = load_generated_questions(await ask(rendered))
    except QuizModelOutputError as failure:
        logger.warning(f"quiz repair round {budget.rounds} was not applied: {failure}")
        return questions
    if len(repaired) != expected:
        logger.warning(
            f"quiz repair round {budget.rounds} was not applied: "
            f"reply has {len(repaired)} questions, expected {expected}"
        )
        return questions
    if repeats_kept_stem(repaired, kept):
        logger.warning(
            f"quiz repair round {budget.rounds} was not applied: "
            "repaired question repeats a kept stem"
        )
        return questions
    if in_place:
        return merge_repaired(questions, failing, repaired)
    return tuple([*kept, *repaired])[:num_questions]


async def _first_reply(
    ask: Ask,
    budget: RepairBudget,
    *,
    prompt: str,
    slices: dict[str, str],
    format_instructions: str,
    settings: QuizSettings,
) -> tuple[GeneratedQuestion, ...]:
    try:
        return load_generated_questions(await ask(prompt))
    except QuizModelOutputError as failure:
        if budget.out_of_time(_now()):
            logger.warning(
                "quiz repair skipped: "
                f"{budget.call_estimate:.0f}s does not fit before the job "
                "deadline, and the first reply was unreadable."
            )
            raise
        return await _full_rewrite(
            ask,
            budget,
            mode="full",
            error=str(failure),
            questions=(),
            faults=(),
            kept=(),
            slices=slices,
            format_instructions=format_instructions,
            expected_count=0,
            settings=settings,
        )


async def _questions_with_repair(
    model: BaseChatModel,
    prompt: str,
    *,
    num_questions: int,
    allowed_types: frozenset[QuestionType],
    slices: dict[str, str],
    deadline: float | None,
    format_instructions: str,
    settings: QuizSettings,
) -> tuple[GeneratedQuestion, ...]:
    """Keep questions that pass. Ask only for failures or missing questions.

    A reply that cannot be parsed, or that contains no keepable question, is
    rewritten once as a whole deck. That rewrite counts as one of the two
    repair rounds. A round is skipped when the job deadline cannot fit another
    model call. If at least one question is already valid, that deck is returned.
    """
    budget = RepairBudget(
        deadline=deadline,
        call_estimate=llm_timeout_seconds(),
    )
    ask = _make_ask(_one_http_attempt(model), budget)
    questions = await _first_reply(
        ask,
        budget,
        prompt=prompt,
        slices=slices,
        format_instructions=format_instructions,
        settings=settings,
    )
    while True:
        faults = faults_for(
            questions,
            num_questions=num_questions,
            allowed_types=allowed_types,
            slices=slices,
        )
        kept = kept_questions(questions, faults)
        failing = _question_faults(faults)
        if len(kept) >= num_questions:
            return tuple(kept[:num_questions])
        if not faults:
            return questions
        if budget.rounds >= QUIZ_REPAIR_ROUNDS:
            raise QuizModelOutputError(faults[0].reason)
        if budget.out_of_time(_now()):
            if kept:
                logger.warning(
                    "quiz repair skipped: "
                    f"{budget.call_estimate:.0f}s does not fit before the "
                    f"job deadline. Keeping {len(kept)} valid question(s)."
                )
                return tuple(kept)
            logger.warning(
                "quiz repair skipped: "
                f"{budget.call_estimate:.0f}s does not fit before the job "
                "deadline, and no question is valid."
            )
            raise QuizModelOutputError(faults[0].reason)
        try:
            if nothing_kept(questions, faults):
                if budget.full_rewrites >= 1:
                    raise QuizModelOutputError(faults[0].reason)
                questions = await _full_rewrite(
                    ask,
                    budget,
                    mode="unusable",
                    error=faults[0].reason,
                    questions=questions,
                    faults=faults,
                    kept=(),
                    slices=slices,
                    format_instructions=format_instructions,
                    expected_count=num_questions,
                    settings=settings,
                )
                continue
            questions = await _partial_round(
                ask,
                budget,
                questions=questions,
                failing=failing,
                kept=kept,
                num_questions=num_questions,
                slices=slices,
                format_instructions=format_instructions,
                settings=settings,
            )
        except QuizTimeoutError:
            if kept:
                logger.warning(
                    "quiz repair skipped: "
                    "the model call reached the job deadline. "
                    f"Keeping {len(kept)} valid question(s)."
                )
                return tuple(kept)
            raise


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
            deadline=state.get("deadline"),
            format_instructions=parser.get_format_instructions(),
            settings=settings,
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
