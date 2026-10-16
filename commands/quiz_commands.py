from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.domain.notebook import Source
from open_notebook.domain.quiz import (
    QUIZ_SOURCE_TOKEN_BUDGET,
    Quiz,
    parse_settings,
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


class GenerateQuizInput(CommandInput):
    quiz_id: str


class GenerateQuizOutput(CommandOutput):
    success: bool
    quiz_id: str
    error_message: str = ""


@command(
    "generate_quiz",
    app="open_notebook",
    retry={
        "max_attempts": 2,
        "wait_strategy": "exponential_jitter",
        "wait_min": 1,
        "wait_max": 60,
        "stop_on": [
            ValueError,
            ConfigurationError,
            NotFoundError,
            InvalidInputError,
        ],
        "retry_log_level": "debug",
    },
)
async def generate_quiz_command(
    input_data: GenerateQuizInput,
) -> GenerateQuizOutput:
    quiz = await Quiz.get(input_data.quiz_id)
    if not quiz.id or not quiz.model_id:
        raise ValueError("quiz is missing an id or model")
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
            raise ValueError(f"source {source_id} has no text")
        texts[source_id] = full_text
    slices = truncate_slices(texts, QUIZ_SOURCE_TOKEN_BUDGET, token_count)
    result = await quiz_graph.ainvoke(  # type: ignore[call-overload]
        {
            "quiz_id": str(quiz.id),
            "model_id": str(quiz.model_id),
            "slices": slices,
            "settings": settings,
        }
    )
    await replace_questions(str(quiz.id), result["deck"])
    return GenerateQuizOutput(
        success=True, quiz_id=str(quiz.id), error_message=""
    )
