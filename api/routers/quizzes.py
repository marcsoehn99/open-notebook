from typing import Annotated, Union

from fastapi import APIRouter
from pydantic import BaseModel, Field

from open_notebook.domain.quiz import (
    ClozeSubmission,
    Explanation,
    McSubmission,
    MultiSubmission,
    Quiz,
    QuizPending,
    QuizReady,
    QuizSummary,
    ShortSubmission,
    Verdict,
    commit_answer,
    configured_model_name,
    create_quiz,
    explain_committed,
    list_quizzes,
    load_play,
    parse_settings,
    resolve_quiz_model_id,
    retry_quiz,
    start_attempt,
)

# ObjectModel.get("quiz:...") resolves only after Quiz is imported.
_ = Quiz.table_name

router = APIRouter()

SubmissionBody = Annotated[
    Union[McSubmission, MultiSubmission, ShortSubmission, ClozeSubmission],
    Field(discriminator="type"),
]


class CreateQuizRequest(BaseModel):
    source_ids: list[str]
    num_questions: int
    difficulty: str
    topic_prompt: str | None = None
    question_types: list[str]
    language: str


class AttemptRequest(BaseModel):
    attempt_id: str
    question_id: str
    submission: SubmissionBody


class ExplainRequest(BaseModel):
    attempt_id: str


class AttemptStarted(BaseModel):
    attempt_id: str


@router.post("/notebooks/{notebook_id}/quizzes", response_model=QuizSummary)
async def post_quiz(notebook_id: str, body: CreateQuizRequest) -> QuizSummary:
    model_id = await resolve_quiz_model_id(configured_model_name())
    settings = parse_settings(
        source_ids=body.source_ids,
        num_questions=body.num_questions,
        difficulty=body.difficulty,
        topic_prompt=body.topic_prompt,
        question_types=body.question_types,
        language=body.language,
        model_id=model_id,
    )
    return await create_quiz(notebook_id, settings)


@router.get("/notebooks/{notebook_id}/quizzes", response_model=list[QuizSummary])
async def get_quizzes(notebook_id: str) -> list[QuizSummary]:
    return await list_quizzes(notebook_id)


@router.get("/quizzes/{quiz_id}", response_model=QuizReady | QuizPending)
async def get_quiz(
    quiz_id: str, attempt_id: str | None = None
) -> QuizReady | QuizPending:
    return await load_play(quiz_id, attempt_id)


@router.post("/quizzes/{quiz_id}/attempts/start", response_model=AttemptStarted)
async def post_attempt_start(quiz_id: str) -> AttemptStarted:
    attempt_id = await start_attempt(quiz_id)
    return AttemptStarted(attempt_id=attempt_id)


@router.post("/quizzes/{quiz_id}/retry", response_model=QuizSummary)
async def post_retry(quiz_id: str) -> QuizSummary:
    return await retry_quiz(quiz_id)


@router.post("/quizzes/{quiz_id}/attempts", response_model=Verdict)
async def post_attempt(quiz_id: str, body: AttemptRequest) -> Verdict:
    return await commit_answer(
        quiz_id, body.attempt_id, body.question_id, body.submission
    )


@router.post(
    "/quizzes/{quiz_id}/questions/{question_id}/explain",
    response_model=Explanation,
)
async def post_explain(
    quiz_id: str, question_id: str, body: ExplainRequest
) -> Explanation:
    return await explain_committed(quiz_id, body.attempt_id, question_id)
