import asyncio
import time

from loguru import logger
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.domain.notebook import Source
from open_notebook.domain.quiz import (
    QUIZ_ERROR_FAILED,
    QUIZ_SOURCE_TOKEN_BUDGET,
    Quiz,
    QuizModelOutputError,
    QuizSourceEmptyError,
    QuizTimeoutError,
    parse_settings,
    quiz_failure_code,
    replace_questions,
    truncate_slices,
)
from open_notebook.exceptions import (
    ConfigurationError,
    InvalidInputError,
    NotFoundError,
)
from open_notebook.graphs.quiz import quiz_graph
from open_notebook.utils import token_count

# One model run. A second run is POST /quizzes/{id}/retry, same as podcasts.
QUIZ_JOB_TIMEOUT_SECONDS = 6 * 60
QUIZ_GENERATION_RETRY = {
    "max_attempts": 1,
    "wait_strategy": "exponential_jitter",
    "wait_min": 1,
    "wait_max": 60,
    "stop_on": [
        ValueError,
        ConfigurationError,
        NotFoundError,
        InvalidInputError,
        QuizModelOutputError,
        QuizTimeoutError,
    ],
    "retry_log_level": "debug",
}


class GenerateQuizInput(CommandInput):
    quiz_id: str


class GenerateQuizOutput(CommandOutput):
    success: bool
    quiz_id: str
    error_message: str = ""


async def _write_status(quiz: Quiz, status: str, error_message: str | None) -> None:
    quiz.status = status
    quiz.error_message = error_message
    await quiz.save()


async def _build_deck(quiz: Quiz):
    if not quiz.id or not quiz.model_id:
        raise ConfigurationError("quiz is missing an id or model")
    settings = parse_settings(
        source_ids=quiz.source_ids,
        num_questions=quiz.num_questions,
        difficulty=quiz.difficulty,
        topic_prompt=quiz.topic_prompt,
        question_types=quiz.question_types,
        language=quiz.language,
        model_id=quiz.model_id,
    )
    texts: dict[str, str] = {}
    for source_id in settings.source_ids:
        source = await Source.get(source_id)
        full_text = source.full_text or ""
        if not full_text.strip():
            logger.warning(f"quiz source {source_id} has no text")
            raise QuizSourceEmptyError("source has no text")
        texts[source_id] = full_text
    slices = truncate_slices(texts, QUIZ_SOURCE_TOKEN_BUDGET, token_count)
    deadline = time.monotonic() + QUIZ_JOB_TIMEOUT_SECONDS
    try:
        result = await asyncio.wait_for(
            quiz_graph.ainvoke(
                {
                    "quiz_id": str(quiz.id),
                    "model_id": str(quiz.model_id),
                    "slices": slices,
                    "settings": settings,
                    "deadline": deadline,
                }
            ),
            timeout=QUIZ_JOB_TIMEOUT_SECONDS,
        )
    except TimeoutError as error:
        raise QuizTimeoutError("quiz generation timed out") from error
    return result["deck"]


@command(
    "generate_quiz",
    app="open_notebook",
    retry=QUIZ_GENERATION_RETRY,
)
async def generate_quiz_command(
    input_data: GenerateQuizInput,
) -> GenerateQuizOutput:
    quiz = await Quiz.get(input_data.quiz_id)
    try:
        await _write_status(quiz, "running", None)
        deck = await _build_deck(quiz)
        await replace_questions(str(quiz.id), deck)
        await _write_status(quiz, "completed", None)
    except Exception as error:
        code = quiz_failure_code(error)
        if code == QUIZ_ERROR_FAILED:
            logger.exception(f"quiz {quiz.id} failed")
        else:
            logger.warning(f"quiz {quiz.id} failed ({code}): {error}")
            if error.__cause__ is not None:
                logger.warning(f"quiz {quiz.id} cause: {error.__cause__}")
        try:
            await _write_status(quiz, "failed", code)
        except Exception:
            logger.exception(f"could not store failure for quiz {quiz.id}")
        raise
    return GenerateQuizOutput(success=True, quiz_id=str(quiz.id), error_message="")
