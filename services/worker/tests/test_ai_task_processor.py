"""Claim-Regeln der persistenten KI-Warteschlange und der Kategorienlauf."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.services import category_suggestion_service
from app.services.ai_answers import AIStepError

from worker.ai.task_processor import _claim_next, _run_category, _run_health_document_extraction


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def first(self) -> Any:
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, *, running: bool = False, queued: bool = True) -> None:
        self.running = running
        self.queued = queued
        self.task = SimpleNamespace(
            id=uuid4(),
            task_type="category_suggestions",
            input={},
            classification_run_id=None,
        )
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(getattr(statement, "text", statement))
        self.statements.append(sql)
        if "select id from ai_tasks" in sql and "status = 'running'" in sql:
            return _Result([SimpleNamespace(id=uuid4())] if self.running else [])
        if "for update skip locked" in sql:
            return _Result([self.task] if self.queued else [])
        if "returning id, task_type" in sql:
            return _Result([self.task])
        return _Result([])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_claim_uses_database_fifo_and_skip_locked() -> None:
    household_id = uuid4()
    session = _Session()

    state, task = await _claim_next(session, household_id)  # type: ignore[arg-type]

    assert state == "claimed"
    assert task is not None and UUID(str(task.id)) == session.task.id
    claim_sql = next(sql for sql in session.statements if "for update skip locked" in sql)
    assert "order by queue_order asc" in claim_sql
    assert session.commits == 1


async def test_second_actor_leaves_when_household_already_has_running_task() -> None:
    session = _Session(running=True)

    state, task = await _claim_next(session, uuid4())  # type: ignore[arg-type]

    assert state == "busy"
    assert task is None
    assert not any("for update skip locked" in sql for sql in session.statements)


async def test_empty_queue_is_not_turned_into_a_running_task() -> None:
    session = _Session(queued=False)

    state, task = await _claim_next(session, uuid4())  # type: ignore[arg-type]

    assert state == "empty"
    assert task is None
    assert not any("update ai_tasks set status = 'running'" in sql for sql in session.statements)


class _ScalarResult(_Result):
    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None


class _RecordingSession(_Session):
    """Wie _Session, merkt sich zusaetzlich die Parameter jedes Statements."""

    def __init__(self) -> None:
        super().__init__()
        self.params: list[dict[str, Any] | None] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.params.append(params)
        return _ScalarResult((await super().execute(statement, params)).rows)


def _finish_params(session: _RecordingSession) -> dict[str, Any]:
    return next(
        params
        for sql, params in zip(session.statements, session.params, strict=True)
        if "update ai_tasks set status = :status" in sql and params is not None
    )


async def test_category_run_fails_with_the_named_reason_and_keeps_the_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein benannter KI-Fehlschlag beendet die Aufgabe mit Klartext und
    Kennung - ohne Rollback, damit die llm_calls-Zeilen des Laufs bleiben
    (in _execute wuerde zurueckgerollt)."""

    async def failing_preview(*args: Any, **kwargs: Any) -> Any:
        raise AIStepError("model_timeout", "Der Modellaufruf hat das Zeitbudget überschritten.")

    monkeypatch.setattr(category_suggestion_service, "build_preview", failing_preview)
    session = _RecordingSession()

    await _run_category(session, uuid4(), uuid4())  # type: ignore[arg-type]

    assert session.rollbacks == 0
    finished = _finish_params(session)
    assert finished["status"] == "failed"
    assert "(Kennung: model_timeout)" in finished["error_message"]
    assert "Zeitbudget" in finished["error_message"]


async def test_category_run_hands_task_and_cancel_check_to_the_cluster_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Vorschau bekommt die Aufgabe (llm_calls.ai_task_id) und eine
    Abbruchpruefung, die sie zwischen zwei Clustern fragt."""
    seen: dict[str, Any] = {}

    async def preview(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        seen["cancelled"] = await kwargs["is_cancelled"]()
        return SimpleNamespace(
            model_dump=lambda mode: {},
            operation_id=uuid4(),
            suggestions=[],
            uncategorized_transactions=0,
            clustered_transactions=0,
            model_name=None,
        )

    monkeypatch.setattr(category_suggestion_service, "build_preview", preview)
    task_id = uuid4()
    session = _RecordingSession()

    await _run_category(session, uuid4(), task_id)  # type: ignore[arg-type]

    assert seen["ai_task_id"] == task_id
    # Die Attrappe kennt die Aufgabe nicht -> gilt wie in _cancel_requested
    # als abgebrochen; entscheidend ist, dass die Pruefung echt fragt.
    assert seen["cancelled"] is True
    assert any("select cancel_requested, status from ai_tasks" in sql for sql in session.statements)


async def test_health_extraction_hands_its_task_to_the_document_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Dokumentauswertung bekommt die Aufgabe mit - sonst stuenden ihre
    llm_calls-Zeilen ohne ai_task_id im Protokoll und liessen sich aus dem
    Aufgabenverlauf nicht wiederfinden."""
    from worker.health_documents import processor

    seen: dict[str, Any] = {}

    async def run(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return processor.HealthExtractionRun(0, 0, 0, 0, 0, False, None, [])

    monkeypatch.setattr(processor, "process_queued_documents", run)
    task_id = uuid4()

    await _run_health_document_extraction(_RecordingSession(), uuid4(), task_id, {})  # type: ignore[arg-type]

    assert seen["ai_task_id"] == task_id
