"""KI-Feld in der Warteschlange: der Aufgabentyp 'assistant_chat' geht an
app.assistant.runner.execute_run; der Wiederherstellungsjob beendet verwaiste
Laeufe mit ihrer Abschlusszeile und loescht Chats jenseits der Aufbewahrung.
"""

import json
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from app.assistant.events import MemoryBus, set_event_bus
from app.assistant.runner import RunResult
from sqlalchemy import text

from worker.ai.task_processor import drain_household_queue, recover_stale_tasks
from worker.db import get_session_factory


@pytest.fixture()
def bus() -> Iterator[MemoryBus]:
    memory = MemoryBus()
    set_event_bus(memory)
    try:
        yield memory
    finally:
        set_event_bus(None)


async def _execute(sql: str, **params: Any) -> list[dict[str, Any]]:
    async with get_session_factory()() as session, session.begin():
        result = await session.execute(text(sql), params)
        return [dict(row._mapping) for row in result] if result.returns_rows else []


async def _chat_with_run(
    *,
    run_status: str = "queued",
    task_status: str = "queued",
    chat_age_days: int = 0,
) -> dict[str, str]:
    """Haushalt, Nutzer, Chat mit Frage und Antwort, Lauf und Aufgabe."""
    ids = {name: str(uuid.uuid4()) for name in ("user", "household", "chat", "question",
                                                 "answer", "run", "task")}
    await _execute(
        "insert into auth.users (id, email) values (:id, :email)",
        id=ids["user"],
        email=f"kifeld-worker-{uuid.uuid4().hex}@finanzplaner.test",
    )
    await _execute(
        "insert into households (id, name, created_by) values (:id, 'KI-Feld Worker', :user)",
        id=ids["household"],
        user=ids["user"],
    )
    await _execute(
        "insert into assistant_chats (id, household_id, created_by, title, last_message_seq, "
        "updated_at) values (:id, :h, :u, 'Test', 2, now() - make_interval(days => :age))",
        id=ids["chat"],
        h=ids["household"],
        u=ids["user"],
        age=chat_age_days,
    )
    await _execute(
        "insert into assistant_messages (id, household_id, created_by, chat_id, seq, role, "
        "content, status) values (:id, :h, :u, :chat, 1, 'user', 'Frage?', 'done')",
        id=ids["question"],
        h=ids["household"],
        u=ids["user"],
        chat=ids["chat"],
    )
    await _execute(
        "insert into assistant_messages (id, household_id, created_by, chat_id, seq, role, "
        "status, reply_to_message_id) values (:id, :h, :u, :chat, 2, 'assistant', :status, "
        ":question)",
        id=ids["answer"],
        h=ids["household"],
        u=ids["user"],
        chat=ids["chat"],
        status=run_status,
        question=ids["question"],
    )
    await _execute(
        "insert into ai_tasks (id, household_id, task_type, input, route_path, created_by, "
        "status, started_at, heartbeat_at) values (:id, :h, 'assistant_chat', "
        "cast(:input as jsonb), '/assistent', :u, :status, "
        "case when :status = 'queued' then null else now() end, now())",
        id=ids["task"],
        h=ids["household"],
        u=ids["user"],
        status=task_status,
        input=json.dumps({"run_id": ids["run"], "chat_id": ids["chat"]}),
    )
    await _execute(
        "insert into assistant_runs (id, household_id, created_by, chat_id, message_id, "
        "question_message_id, ai_task_id, status, last_seq) values (:id, :h, :u, :chat, "
        ":answer, :question, :task, :status, 0)",
        id=ids["run"],
        h=ids["household"],
        u=ids["user"],
        chat=ids["chat"],
        answer=ids["answer"],
        question=ids["question"],
        task=ids["task"],
        status=run_status,
    )
    await _execute(
        "update assistant_messages set run_id = :run where id = :id",
        run=ids["run"],
        id=ids["answer"],
    )
    return ids


@pytest.mark.parametrize(
    ("run_status", "task_status", "message"),
    [
        ("done", "completed", None),
        ("failed", "failed", "model_budget_exhausted: Das Antwortbudget war aufgebraucht"),
        ("cancelled", "cancelled", None),
    ],
)
async def test_assistant_tasks_are_run_by_the_assistant_runner(
    bus: MemoryBus, run_status: str, task_status: str, message: str | None
) -> None:
    ids = await _chat_with_run()
    result = RunResult(
        status=run_status,
        error_code="model_budget_exhausted" if run_status == "failed" else None,
        error_message="Das Antwortbudget war aufgebraucht" if run_status == "failed" else None,
        model_name="fake-model",
        chat_id=uuid.UUID(ids["chat"]),
    )
    with patch("app.assistant.runner.execute_run", AsyncMock(return_value=result)) as runner:
        async with get_session_factory()() as session:
            await drain_household_queue(session, uuid.UUID(ids["household"]))
    assert runner.await_count == 1
    assert runner.await_args is not None
    assert runner.await_args.args == (uuid.UUID(ids["run"]),)
    assert "session_factory" in runner.await_args.kwargs
    task = (
        await _execute(
            "select status, model_name, error_message, result from ai_tasks where id = :id",
            id=ids["task"],
        )
    )[0]
    assert task["status"] == task_status
    assert task["model_name"] == "fake-model"
    assert task["error_message"] == message
    assert task["result"]["run_id"] == ids["run"]


async def test_a_task_ended_outside_the_runner_still_ends_its_run(bus: MemoryBus) -> None:
    """Hart abgebrochen ueber /v1/ai/tasks: die Aufgabe ist beendet, bevor
    der Lauf je startete. Der Wiederherstellungsjob beendet den Lauf mit
    seiner Abschlusszeile - sonst wartete ein offener Strom ewig."""
    ids = await _chat_with_run()
    await _execute(
        "update ai_tasks set cancel_requested = true, status = 'cancelled' where id = :id",
        id=ids["task"],
    )
    async with get_session_factory()() as session:
        await recover_stale_tasks(session)
    run = (await _execute("select status from assistant_runs where id = :id", id=ids["run"]))[0]
    assert run["status"] == "cancelled"


async def test_orphaned_runs_get_their_final_line(bus: MemoryBus) -> None:
    lost = await _chat_with_run(run_status="running", task_status="failed")
    await _execute(
        "update ai_tasks set error_message = 'Worker ohne Lebenszeichen' where id = :id",
        id=lost["task"],
    )
    cancelled = await _chat_with_run(run_status="queued", task_status="cancelled")
    alive = await _chat_with_run(run_status="running", task_status="running")

    async with get_session_factory()() as session:
        await recover_stale_tasks(session)

    lost_run = (
        await _execute(
            "select status, error_code, end_reason from assistant_runs where id = :id",
            id=lost["run"],
        )
    )[0]
    assert lost_run == {
        "status": "failed",
        "error_code": "assistant_worker_lost",
        "end_reason": "failed",
    }
    events = await _execute(
        "select seq, payload from assistant_run_events where run_id = :id order by seq",
        id=lost["run"],
    )
    assert events[-1]["payload"]["type"] == "error"
    assert events[-1]["payload"]["message_obj"]["status"] == "failed"
    assert "Worker ohne Lebenszeichen" in events[-1]["payload"]["message"]

    cancelled_run = (
        await _execute("select status from assistant_runs where id = :id", id=cancelled["run"])
    )[0]
    assert cancelled_run["status"] == "cancelled"
    final = await _execute(
        "select payload from assistant_run_events where run_id = :id order by seq desc limit 1",
        id=cancelled["run"],
    )
    assert final[0]["payload"]["type"] == "done"

    alive_run = (
        await _execute("select status from assistant_runs where id = :id", id=alive["run"])
    )[0]
    assert alive_run["status"] == "running"
    published = {event.get("type") for _channel, event in bus.published}
    assert {"done", "error"} <= published


async def test_waiting_questions_learn_their_new_queue_position(bus: MemoryBus) -> None:
    from app.assistant.maintenance import announce_queue_positions

    ids = await _chat_with_run()
    await _execute(
        "insert into assistant_run_events (household_id, created_by, run_id, seq, type, payload) "
        "values (:h, :u, :run, 1, 'status', cast(:payload as jsonb))",
        h=ids["household"],
        u=ids["user"],
        run=ids["run"],
        payload=json.dumps({"type": "status", "seq": 1, "status": "queued", "queue_position": 2}),
    )
    await _execute("update assistant_runs set last_seq = 1 where id = :id", id=ids["run"])
    await _execute(
        "insert into ai_tasks (household_id, task_type, route_path, status, started_at, "
        "heartbeat_at) values (:h, 'category_suggestions', '/kategorien', 'running', now(), "
        "now())",
        h=ids["household"],
    )
    async with get_session_factory()() as session:
        assert await announce_queue_positions(session, uuid.UUID(ids["household"]), bus) == 1
        # unveraendert: keine weitere Zeile
        assert await announce_queue_positions(session, uuid.UUID(ids["household"]), bus) == 0
    events = await _execute(
        "select seq, payload from assistant_run_events where run_id = :id order by seq",
        id=ids["run"],
    )
    assert [event["seq"] for event in events] == [1, 2]
    assert events[-1]["payload"]["queue_position"] == 1
    assert bus.published[-1][1]["queue_position"] == 1


async def test_chats_beyond_the_retention_are_deleted(bus: MemoryBus) -> None:
    old = await _chat_with_run(run_status="done", task_status="completed", chat_age_days=120)
    fresh = await _chat_with_run(run_status="done", task_status="completed", chat_age_days=5)
    busy = await _chat_with_run(run_status="running", task_status="running", chat_age_days=120)
    await _execute(
        "update ai_household_settings set assistant_retention_days = 30 where household_id = :h",
        h=fresh["household"],
    )

    async with get_session_factory()() as session:
        await recover_stale_tasks(session)

    remaining = {
        row["id"]
        for row in await _execute(
            "select id::text from assistant_chats where id = any(cast(:ids as uuid[]))",
            ids=[old["chat"], fresh["chat"], busy["chat"]],
        )
    }
    assert old["chat"] not in remaining  # 120 Tage > Vorgabe 90
    assert fresh["chat"] in remaining  # 5 Tage < 30
    assert busy["chat"] in remaining  # offener Lauf - erst danach
