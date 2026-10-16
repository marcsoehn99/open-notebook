"""Quiz jobs, attempts, and answer writes. No database and no model network."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from open_notebook.domain.quiz import (
    McSubmission,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    ShortSubmission,
    commit_answer,
    explain_committed,
    load_play,
    quiz_failure_code,
    resolve_status,
    retry_quiz,
    start_attempt,
)
from open_notebook.exceptions import (
    ConfigurationError,
    ConflictError,
    InvalidInputError,
    NetworkError,
    NotFoundError,
    QuizModelOutputError,
    QuizPromptTooLargeError,
    QuizSourceEmptyError,
    QuizTimeoutError,
)


def _quiz(**overrides: object) -> Quiz:
    payload: dict[str, object] = {
        "id": "quiz:1",
        "notebook": "notebook:1",
        "command": "command:1",
        "model_id": "model:1",
        "source_ids": ["source:essay"],
        "num_questions": 2,
        "difficulty": "medium",
        "topic_prompt": None,
        "question_types": ["mc"],
        "language": "de",
        "status": "completed",
        "error_message": None,
    }
    payload.update(overrides)
    return Quiz(**payload)  # type: ignore[arg-type]


def _question(question_id: str, position: int) -> dict[str, object]:
    return {
        "question_id": question_id,
        "position": position,
        "type": "mc",
        "stem": f"Which city is {question_id}?",
        "options": [
            {"id": "opt-a", "text": "Lyon"},
            {"id": "opt-b", "text": "Paris"},
        ],
        "correct_option_ids": ["opt-b"],
        "accepted": [],
        "explanation": "The sentence names it.",
        "quote": "capital is Paris",
        "source_id": "source:essay",
        "run_id": "run-1",
    }


def _tail(value: object) -> str:
    return str(value).split(":")[-1].strip("⟨⟩")


class World:
    def __init__(self) -> None:
        self.quiz = _quiz()
        self.questions = [_question("q1", 1), _question("q2", 2)]
        self.attempts: dict[str, QuizAttempt] = {}
        self.answers: dict[tuple[str, str], dict[str, object]] = {}
        self.jobs: list[dict[str, object]] = []


def _install(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    async def fake_get(cls: type, quiz_id: str) -> Quiz:
        del cls
        if str(quiz_id) != str(world.quiz.id):
            raise NotFoundError("quiz not found")
        return world.quiz

    async def quiz_save(self: Quiz) -> None:
        del self

    async def attempt_save(self: QuizAttempt) -> None:
        self.id = self.id or f"quiz_attempt:{len(world.attempts) + 1}"
        world.attempts[self.token] = self

    async def answer_save(self: QuizAnswer) -> None:
        await asyncio.sleep(0)
        key = (_tail(self.attempt), self.question_id)
        if key in world.answers:
            raise RuntimeError("already contains")
        self.id = f"quiz_answer:{len(world.answers) + 1}"
        world.answers[key] = dict(self.submission)

    async def fake_query(
        query: str, vars: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
        params = vars or {}
        if "UPDATE" in query and "status = 'queued'" in query:
            if world.quiz.status != "failed":
                return []
            world.quiz.status = "queued"
            world.quiz.error_message = None
            return [{"id": world.quiz.id, "status": "queued"}]
        if "quiz_question" in query:
            return list(world.questions)
        if "quiz_attempt" in query:
            token = params.get("token")
            attempt = world.attempts.get(str(token))
            if attempt is None:
                return []
            return [
                {
                    "id": attempt.id,
                    "quiz": attempt.quiz,
                    "token": attempt.token,
                    "answers": list(attempt.answers),
                }
            ]
        if "quiz_answer" in query:
            attempt_id = _tail(params.get("attempt"))
            question_id = params.get("question_id")
            rows: list[dict[str, object]] = []
            for (stored_attempt, stored_question), submission in world.answers.items():
                if _tail(stored_attempt) != attempt_id:
                    continue
                if question_id is not None and stored_question != question_id:
                    continue
                rows.append(
                    {
                        "question_id": stored_question,
                        "submission": submission,
                        "attempt": stored_attempt,
                    }
                )
            return rows
        return []

    async def fake_submit(
        module_name: str,
        command_name: str,
        command_args: dict[str, object],
        context: object = None,
    ) -> str:
        del module_name, command_name, context
        world.jobs.append(command_args)
        return "command:2"

    async def unknown_command(quiz_row: Quiz) -> tuple[str, None]:
        del quiz_row
        return "unknown", None

    monkeypatch.setattr(Quiz, "get", classmethod(fake_get))
    monkeypatch.setattr(Quiz, "save", quiz_save)
    monkeypatch.setattr(QuizAttempt, "save", attempt_save)
    monkeypatch.setattr(QuizAnswer, "save", answer_save)
    monkeypatch.setattr("open_notebook.domain.quiz.repo_query", fake_query)
    monkeypatch.setattr("open_notebook.domain.quiz._read_command", unknown_command)
    monkeypatch.setattr(
        "api.command_service.CommandService.submit_command_job",
        fake_submit,
    )


def _mc(option_id: str = "opt-a") -> McSubmission:
    return McSubmission(type="mc", option_id=option_id)


def test_failure_codes_hide_model_text():
    assert quiz_failure_code(QuizTimeoutError("quiz generation timed out")) == "timeout"
    assert (
        quiz_failure_code(QuizModelOutputError("quote is not a verbatim slice"))
        == "invalid_output"
    )
    assert quiz_failure_code(QuizSourceEmptyError("source has no text")) == "empty_source"
    assert (
        quiz_failure_code(QuizPromptTooLargeError("quiz prompt has 9 tokens"))
        == "too_large"
    )
    assert quiz_failure_code(ConfigurationError("missing model")) == "not_configured"
    assert quiz_failure_code(RuntimeError("socket hang up")) == "failed"


@pytest.mark.asyncio
async def test_non_terminal_status_stays_in_progress(monkeypatch):
    quiz = _quiz(status="running", error_message="1 validation error for GeneratedDeck")

    async def unknown(quiz_row: Quiz) -> tuple[str, None]:
        del quiz_row
        return "unknown", None

    monkeypatch.setattr("open_notebook.domain.quiz._read_command", unknown)
    status, error = await resolve_status(quiz)
    assert status == "running"
    assert error is None

    queued = _quiz(status="queued")
    status, error = await resolve_status(queued)
    assert status == "queued"
    assert error is None

    missing = _quiz(status=None)
    status, error = await resolve_status(missing)
    assert status == "unknown"
    assert error is None

    async def failed_command(quiz_row: Quiz) -> tuple[str, str]:
        del quiz_row
        return "failed", "1 validation error for GeneratedDeck"

    monkeypatch.setattr("open_notebook.domain.quiz._read_command", failed_command)
    retrying = _quiz(status="queued")
    status, error = await resolve_status(retrying)
    assert status == "queued"
    assert error is None
    running = _quiz(
        status="running",
        error_message="1 validation error for GeneratedDeck",
    )
    status, error = await resolve_status(running)
    assert status == "failed"
    assert error == "failed"


@pytest.mark.asyncio
async def test_failed_status_drops_raw_model_text():
    quiz = _quiz(
        status="failed",
        error_message="1 validation error for GeneratedDeck\nstem\n  Field required",
    )
    status, error = await resolve_status(quiz)
    assert status == "failed"
    assert error == "failed"


@pytest.mark.asyncio
async def test_unknown_attempt_does_not_reveal_or_create(monkeypatch):
    world = World()
    _install(monkeypatch, world)
    with pytest.raises(NotFoundError):
        await commit_answer("quiz:1", "client-minted", "q1", _mc())
    assert world.attempts == {}
    assert world.answers == {}


@pytest.mark.asyncio
async def test_parallel_answers_both_stick_and_a_repeat_is_409(monkeypatch):
    world = World()
    _install(monkeypatch, world)
    token = await start_attempt("quiz:1")
    assert token in world.attempts
    raced = await asyncio.gather(
        commit_answer("quiz:1", token, "q1", _mc("opt-a")),
        commit_answer("quiz:1", token, "q1", _mc("opt-b")),
        return_exceptions=True,
    )
    saved = [item for item in raced if not isinstance(item, Exception)]
    rejected = [item for item in raced if isinstance(item, ConflictError)]
    assert len(saved) == 1
    assert len(rejected) == 1
    assert saved[0].result.reveal.type == "mc"
    q1_key = (_tail(world.attempts[token].id), "q1")
    assert world.answers[q1_key]["option_id"] in {"opt-a", "opt-b"}
    follow = await asyncio.gather(
        commit_answer("quiz:1", token, "q1", _mc("opt-b")),
        commit_answer("quiz:1", token, "q2", _mc("opt-b")),
        return_exceptions=True,
    )
    assert any(isinstance(item, ConflictError) for item in follow)
    stored = [item for item in follow if not isinstance(item, Exception)]
    assert len(stored) == 1
    assert stored[0].question_id == "q2"
    assert len(world.answers) == 2


@pytest.mark.asyncio
async def test_explain_requires_a_server_attempt_and_an_answer(monkeypatch):
    world = World()
    _install(monkeypatch, world)
    with pytest.raises(NotFoundError):
        await explain_committed("quiz:1", "missing", "q1")
    token = await start_attempt("quiz:1")
    with pytest.raises(InvalidInputError):
        await explain_committed("quiz:1", token, "q1")
    await commit_answer("quiz:1", token, "q1", _mc())
    explained = await explain_committed("quiz:1", token, "q1")
    assert explained.quote == "capital is Paris"


@pytest.mark.asyncio
async def test_type_mismatch_does_not_reveal(monkeypatch):
    world = World()
    _install(monkeypatch, world)
    token = await start_attempt("quiz:1")
    with pytest.raises(InvalidInputError):
        await commit_answer(
            "quiz:1",
            token,
            "q1",
            ShortSubmission(type="short", text="Paris"),
        )
    assert world.answers == {}


@pytest.mark.asyncio
async def test_load_play_hides_the_key_for_an_unknown_attempt(monkeypatch):
    world = World()
    _install(monkeypatch, world)
    play = await load_play("quiz:1", "forged-attempt")
    body = play.model_dump_json()
    assert "correct_option_id" not in body
    assert "capital is Paris" not in body
    failed = _quiz(
        status="failed",
        error_message="1 validation error for GeneratedDeck",
    )
    world.quiz = failed
    pending = await load_play("quiz:1", None)
    assert pending.status == "failed"
    assert pending.error_message == "failed"
    assert "validation error" not in pending.model_dump_json()


@pytest.mark.asyncio
async def test_retry_submits_one_job_from_failed(monkeypatch):
    world = World()
    world.quiz.status = "failed"
    world.quiz.error_message = "timeout"
    _install(monkeypatch, world)
    summary = await retry_quiz("quiz:1")
    assert summary.status == "queued"
    assert summary.error_message is None
    assert world.jobs == [{"quiz_id": "quiz:1"}]
    assert world.quiz.command == "command:2"
    with pytest.raises(InvalidInputError):
        await retry_quiz("quiz:1")
    assert world.jobs == [{"quiz_id": "quiz:1"}]


@pytest.mark.asyncio
async def test_retry_adopts_a_failed_command_when_the_row_has_no_status(monkeypatch):
    world = World()
    world.quiz.status = None
    world.quiz.error_message = "1 validation error for GeneratedDeck"
    _install(monkeypatch, world)

    async def failed_command(quiz_row: Quiz) -> tuple[str, str]:
        del quiz_row
        return "failed", "1 validation error for GeneratedDeck"

    monkeypatch.setattr("open_notebook.domain.quiz._read_command", failed_command)
    summary = await retry_quiz("quiz:1")
    assert summary.status == "queued"
    assert summary.error_message is None
    assert world.jobs == [{"quiz_id": "quiz:1"}]
    assert "validation error" not in str(world.quiz.error_message)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    world = World()
    _install(monkeypatch, world)
    from api.main import app

    with patch("api.main._run_database_migrations", new_callable=AsyncMock):
        with TestClient(app) as client:
            yield client, world


def test_router_attempt_codes(api):
    client, world = api
    forged = client.post(
        "/api/quizzes/quiz:1/attempts",
        json={
            "attempt_id": "client-minted",
            "question_id": "q1",
            "submission": {"type": "mc", "option_id": "opt-b"},
        },
    )
    assert forged.status_code == 404
    assert "correct_option_id" not in forged.text
    assert world.attempts == {}

    started = client.post("/api/quizzes/quiz:1/attempts/start")
    assert started.status_code == 200
    token = started.json()["attempt_id"]

    wrong_type = client.post(
        "/api/quizzes/quiz:1/attempts",
        json={
            "attempt_id": token,
            "question_id": "q1",
            "submission": {"type": "short", "text": "Paris"},
        },
    )
    assert wrong_type.status_code == 400
    assert "correct_option_id" not in wrong_type.text

    graded = client.post(
        "/api/quizzes/quiz:1/attempts",
        json={
            "attempt_id": token,
            "question_id": "q1",
            "submission": {"type": "mc", "option_id": "opt-a"},
        },
    )
    assert graded.status_code == 200
    assert graded.json()["result"]["reveal"]["correct_option_id"] == "opt-b"

    again = client.post(
        "/api/quizzes/quiz:1/attempts",
        json={
            "attempt_id": token,
            "question_id": "q1",
            "submission": {"type": "mc", "option_id": "opt-b"},
        },
    )
    assert again.status_code == 409
    assert "correct_option_id" not in again.text
    assert world.answers[(_tail(world.attempts[token].id), "q1")]["option_id"] == "opt-a"

    early = client.post(
        "/api/quizzes/quiz:1/questions/q2/explain",
        json={"attempt_id": token},
    )
    assert early.status_code == 400

    world.quiz.status = "failed"
    world.quiz.error_message = "1 validation error for GeneratedDeck"
    failed = client.get("/api/quizzes/quiz:1")
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["error_message"] == "failed"
    assert "validation error" not in failed.text

    retried = client.post("/api/quizzes/quiz:1/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"


def _command_quiz() -> Quiz:
    return _quiz(
        status="queued",
        num_questions=1,
        question_types=["short"],
        source_ids=["source:essay"],
    )


def _patch_command(monkeypatch: pytest.MonkeyPatch, quiz: Quiz, text: str):
    calls: list[object] = []
    replaced: list[object] = []

    class _Source:
        def __init__(self, full_text: str) -> None:
            self.full_text = full_text

    async def fake_get(cls: type, quiz_id: str) -> Quiz:
        del cls, quiz_id
        return quiz

    async def source_get(cls: type, source_id: str) -> _Source:
        del cls, source_id
        return _Source(text)

    async def save(self: Quiz) -> None:
        del self

    async def invoke(payload: object) -> dict[str, object]:
        calls.append(payload)
        return {"deck": ("sealed",)}

    async def replace(quiz_id: str, deck: object) -> None:
        del quiz_id
        replaced.append(deck)

    from open_notebook.domain.notebook import Source

    monkeypatch.setattr(Quiz, "get", classmethod(fake_get))
    monkeypatch.setattr(Quiz, "save", save)
    monkeypatch.setattr(Source, "get", classmethod(source_get))
    monkeypatch.setattr("commands.quiz_commands.quiz_graph.ainvoke", invoke)
    monkeypatch.setattr("commands.quiz_commands.replace_questions", replace)
    return calls, replaced


@pytest.mark.asyncio
async def test_generate_command_success_and_failures(monkeypatch):
    from commands.quiz_commands import (
        QUIZ_GENERATION_RETRY,
        GenerateQuizInput,
        generate_quiz_command,
    )

    assert QUIZ_GENERATION_RETRY["max_attempts"] == 1
    assert NetworkError not in QUIZ_GENERATION_RETRY["stop_on"]
    assert InvalidInputError in QUIZ_GENERATION_RETRY["stop_on"]
    assert QuizModelOutputError in QUIZ_GENERATION_RETRY["stop_on"]

    quiz = _command_quiz()
    calls, replaced = _patch_command(
        monkeypatch, quiz, "The capital is Paris today and the river is slow."
    )
    result = await generate_quiz_command(GenerateQuizInput(quiz_id="quiz:1"))
    assert result.success is True
    assert quiz.status == "completed"
    assert quiz.error_message is None
    assert len(calls) == 1
    assert replaced == [("sealed",)]

    empty = _command_quiz()
    calls, _replaced = _patch_command(monkeypatch, empty, "   ")
    with pytest.raises(QuizSourceEmptyError):
        await generate_quiz_command(GenerateQuizInput(quiz_id="quiz:1"))
    assert calls == []
    assert empty.status == "failed"
    assert empty.error_message == "empty_source"

    network = _command_quiz()

    async def fail(payload: object) -> dict[str, object]:
        del payload
        raise NetworkError("Could not connect to the AI provider")

    _patch_command(
        monkeypatch, network, "The capital is Paris today and the river is slow."
    )
    monkeypatch.setattr("commands.quiz_commands.quiz_graph.ainvoke", fail)
    with pytest.raises(NetworkError):
        await generate_quiz_command(GenerateQuizInput(quiz_id="quiz:1"))
    assert network.status == "failed"
    assert network.error_message == "failed"

    invalid = _command_quiz()

    async def bad_deck(payload: object) -> dict[str, object]:
        del payload
        raise QuizModelOutputError("quote is not a verbatim slice of the source text")

    _patch_command(
        monkeypatch, invalid, "The capital is Paris today and the river is slow."
    )
    monkeypatch.setattr("commands.quiz_commands.quiz_graph.ainvoke", bad_deck)
    with pytest.raises(QuizModelOutputError):
        await generate_quiz_command(GenerateQuizInput(quiz_id="quiz:1"))
    assert invalid.status == "failed"
    assert invalid.error_message == "invalid_output"

    hung = _command_quiz()

    async def sleep_forever(payload: object) -> dict[str, object]:
        del payload
        await asyncio.sleep(1)
        return {"deck": ()}

    _patch_command(
        monkeypatch, hung, "The capital is Paris today and the river is slow."
    )
    monkeypatch.setattr("commands.quiz_commands.quiz_graph.ainvoke", sleep_forever)
    monkeypatch.setattr("commands.quiz_commands.QUIZ_JOB_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(QuizTimeoutError):
        await generate_quiz_command(GenerateQuizInput(quiz_id="quiz:1"))
    assert hung.status == "failed"
    assert hung.error_message == "timeout"
