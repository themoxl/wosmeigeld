"""Datenbankzugriff des KI-Felds und die Vertragsformen daraus.

Gemeinsam fuer API (RLS-Sitzung des Nutzers) und Worker (ohne RLS): jede
Abfrage filtert deshalb ausdruecklich nach household_id und, wo es um Chats
geht, nach created_by - RLS ist die zweite Schicht, nicht die einzige.

Formen wie in docs/KI-FELD-SCHNITTSTELLE.md; alles JSON-faehig (Zeitstempel
als ISO-Text), damit dieselben Dicts in Stromzeilen und Antworten passen.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

Event = dict[str, Any]

TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})

_MESSAGE_COLUMNS = (
    "id, seq, role, kind, content, status, run_id, reply_to_message_id, request_id, "
    "created_at, error_code, error_message, numbers, context_meter, run_summary"
)
_RUN_COLUMNS = (
    "id, household_id, created_by, chat_id, message_id, question_message_id, ai_task_id, "
    "mode, status, end_reason, reasoning_override, focus, retry_of_run_id, model_configured, "
    "model_answering, window_loaded, window_cap, window_effective, window_source, "
    "reasoning_sent, reasoning_capability, error_code, error_message, last_seq, summary_nr, "
    "summary_text, summary_covers_until_message_id, summary_tokens_before, "
    "summary_tokens_after, summary_unverified, created_at, started_at, finished_at"
)
_CHAT_COLUMNS = (
    "id, household_id, created_by, title, reasoning_override, last_message_seq, last_tool_key, "
    "summary_run_id, active_compaction_run_id, created_at, updated_at, last_message_at"
)


class RunGone(LookupError):
    """Der Lauf existiert nicht mehr (Chat geloescht)."""


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_param(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- Sitzungen


@asynccontextmanager
async def user_session(
    session_factory: async_sessionmaker[AsyncSession], *, user_id: UUID, household_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Kurze Sitzung mit den RLS-Rechten des Nutzers (wie
    app/auth/dependencies.py) - fuer den Strom-Generator, der eigene
    Sitzungen braucht, weil die der Abhaengigkeiten beim Streamen schon
    geschlossen sind."""
    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        await session.execute(
            text("select set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps({"sub": str(user_id), "role": "authenticated"})},
        )
        await session.execute(
            text("select set_config('app.household_id', :household_id, true)"),
            {"household_id": str(household_id)},
        )
        yield session


# ---------------------------------------------------------------- Zeilen


@dataclass(frozen=True)
class RunRow:
    id: UUID
    household_id: UUID
    created_by: UUID
    chat_id: UUID
    message_id: UUID
    question_message_id: UUID | None
    ai_task_id: UUID | None
    mode: str
    status: str
    reasoning_override: str | None
    focus: str | None
    last_seq: int

    @staticmethod
    def of(row: Row[Any]) -> "RunRow":
        return RunRow(
            id=UUID(str(row.id)),
            household_id=UUID(str(row.household_id)),
            created_by=UUID(str(row.created_by)),
            chat_id=UUID(str(row.chat_id)),
            message_id=UUID(str(row.message_id)),
            question_message_id=(
                UUID(str(row.question_message_id)) if row.question_message_id else None
            ),
            ai_task_id=UUID(str(row.ai_task_id)) if row.ai_task_id else None,
            mode=str(row.mode),
            status=str(row.status),
            reasoning_override=row.reasoning_override,
            focus=row.focus,
            last_seq=int(row.last_seq),
        )


async def load_run_row(
    session: AsyncSession,
    run_id: UUID,
    *,
    household_id: UUID | None = None,
    user_id: UUID | None = None,
    lock: bool = False,
) -> Row[Any] | None:
    conditions = ["id = :id"]
    params: dict[str, Any] = {"id": str(run_id)}
    if household_id is not None:
        conditions.append("household_id = :household_id")
        params["household_id"] = str(household_id)
    if user_id is not None:
        conditions.append("created_by = :user_id")
        params["user_id"] = str(user_id)
    sql = f"select {_RUN_COLUMNS} from assistant_runs where {' and '.join(conditions)}"
    if lock:
        sql += " for update"
    return (await session.execute(text(sql), params)).first()


async def load_chat_row(
    session: AsyncSession,
    chat_id: UUID,
    *,
    household_id: UUID,
    user_id: UUID | None,
    lock: bool = False,
) -> Row[Any] | None:
    sql = f"select {_CHAT_COLUMNS} from assistant_chats where id = :id and household_id = :h"
    params: dict[str, Any] = {"id": str(chat_id), "h": str(household_id)}
    if user_id is not None:
        sql += " and created_by = :user_id"
        params["user_id"] = str(user_id)
    if lock:
        sql += " for update"
    return (await session.execute(text(sql), params)).first()


async def message_rows(session: AsyncSession, chat_id: UUID, household_id: UUID) -> list[Row[Any]]:
    """Alle Nachrichten eines Chats in seq-Reihenfolge (auch Verdichtungen)."""
    return list(
        (
            await session.execute(
                text(
                    f"select {_MESSAGE_COLUMNS} from assistant_messages "
                    "where chat_id = :chat_id and household_id = :h order by seq"
                ),
                {"chat_id": str(chat_id), "h": str(household_id)},
            )
        ).all()
    )


async def message_row(session: AsyncSession, message_id: UUID, household_id: UUID) -> Row[Any]:
    return (
        await session.execute(
            text(
                f"select {_MESSAGE_COLUMNS} from assistant_messages "
                "where id = :id and household_id = :h"
            ),
            {"id": str(message_id), "h": str(household_id)},
        )
    ).one()


# ---------------------------------------------------------------- Formen


def message_of(row: Row[Any]) -> dict[str, Any]:
    """Vertragsform ChatMessage."""
    return {
        "id": str(row.id),
        "seq": int(row.seq),
        "role": str(row.role),
        "content": str(row.content or ""),
        "status": str(row.status),
        "run_id": str(row.run_id) if row.run_id else None,
        "created_at": iso(row.created_at),
        "error_code": row.error_code,
        "error_message": row.error_message,
        "numbers": row.numbers,
        "context_meter": row.context_meter,
        "run_summary": row.run_summary,
    }


def summary_of(row: Row[Any] | None) -> dict[str, Any] | None:
    """Vertragsform ChatSummary aus dem Lauf, der sie erzeugt hat."""
    if row is None or not row.summary_text or row.summary_covers_until_message_id is None:
        return None
    return {
        "nr": int(row.summary_nr or 1),
        "covers_until_message_id": str(row.summary_covers_until_message_id),
        "text": str(row.summary_text),
        "run_id": str(row.id),
        "tokens_before": row.summary_tokens_before,
        "tokens_after": row.summary_tokens_after,
        "unverified_numbers": int(row.summary_unverified or 0),
        "created_at": iso(row.finished_at or row.created_at),
    }


async def chat_summary_run(session: AsyncSession, chat: Row[Any]) -> Row[Any] | None:
    if chat.summary_run_id is None:
        return None
    return await load_run_row(
        session, UUID(str(chat.summary_run_id)), household_id=UUID(str(chat.household_id))
    )


async def reasoning_capability_of(session: AsyncSession, household_id: UUID) -> str:
    """Was das Modell zuletzt ueber sein Denken meldete (letzter Lauf des
    Haushalts mit Angabe). Ohne Lauf: unknown - die Anzeige fragt dafuer
    nicht bei jedem Abruf den Modellserver."""
    value = (
        await session.execute(
            text(
                "select reasoning_capability from assistant_runs "
                "where household_id = :h and reasoning_capability is not null "
                "order by coalesce(started_at, created_at) desc limit 1"
            ),
            {"h": str(household_id)},
        )
    ).scalar_one_or_none()
    return str(value) if value in ("switchable", "always", "none", "unknown") else "unknown"


async def chat_detail(session: AsyncSession, chat: Row[Any]) -> dict[str, Any]:
    """Vertragsform ChatDetail."""
    household_id = UUID(str(chat.household_id))
    rows = await message_rows(session, UUID(str(chat.id)), household_id)
    messages = [message_of(row) for row in rows if row.kind == "chat"]
    meter = next(
        (message["context_meter"] for message in reversed(messages) if message["context_meter"]),
        None,
    )
    return {
        "id": str(chat.id),
        "title": str(chat.title),
        "reasoning_override": chat.reasoning_override,
        "reasoning_capability": await reasoning_capability_of(session, household_id),
        "summary": summary_of(await chat_summary_run(session, chat)),
        "active_compaction_run_id": (
            str(chat.active_compaction_run_id) if chat.active_compaction_run_id else None
        ),
        "messages": messages,
        "context_meter": meter,
    }


async def list_chats(
    session: AsyncSession, household_id: UUID, user_id: UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "select c.id, c.title, c.created_at, c.updated_at, c.last_message_at, "
                "(select count(*) from assistant_messages m where m.chat_id = c.id "
                " and m.kind = 'chat') as message_count "
                "from assistant_chats c where c.household_id = :h and c.created_by = :u "
                "order by c.updated_at desc, c.created_at desc"
            ),
            {"h": str(household_id), "u": str(user_id)},
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "title": str(row.title),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
            "last_message_at": iso(row.last_message_at),
            "message_count": int(row.message_count),
        }
        for row in rows
    ]


# ---------------------------------------------------------------- Zaehler


async def next_message_seq(session: AsyncSession, chat_id: UUID, count: int = 1) -> int:
    """Reserviert `count` Nachrichtennummern und liefert die erste."""
    last = (
        await session.execute(
            text(
                "update assistant_chats set last_message_seq = last_message_seq + :n "
                "where id = :id returning last_message_seq"
            ),
            {"n": count, "id": str(chat_id)},
        )
    ).scalar_one()
    return int(last) - count + 1


async def append_event(session: AsyncSession, run_id: UUID, payload: Event) -> Event:
    """Speichert eine Stromzeile mit der naechsten seq des Laufs.

    Die seq kommt aus "update assistant_runs ... returning": die Zeilensperre
    des Laufs reiht API (Abbruch eines wartenden Laufs) und Worker
    hintereinander - zwei Schreiber bekommen nie dieselbe Nummer. Der
    Aufrufer committet und veroeffentlicht danach (Regel 5: done/error erst
    nach dem Commit)."""
    row = (
        await session.execute(
            text(
                "update assistant_runs set last_seq = last_seq + 1, updated_at = now() "
                "where id = :id returning last_seq, household_id, created_by"
            ),
            {"id": str(run_id)},
        )
    ).first()
    if row is None:
        raise RunGone(run_id)
    event = {**payload, "seq": int(row.last_seq)}
    await session.execute(
        text(
            "insert into assistant_run_events "
            "(household_id, created_by, run_id, seq, type, payload) "
            "values (:h, :u, :run_id, :seq, :type, cast(:payload as jsonb))"
        ),
        {
            "h": str(row.household_id),
            "u": str(row.created_by),
            "run_id": str(run_id),
            "seq": event["seq"],
            "type": str(event.get("type") or "event"),
            "payload": _json_param(event),
        },
    )
    return event


async def events_after(
    session: AsyncSession, run_id: UUID, household_id: UUID, after: int
) -> list[Event]:
    rows = (
        await session.execute(
            text(
                "select payload from assistant_run_events "
                "where run_id = :run_id and household_id = :h and seq > :after order by seq"
            ),
            {"run_id": str(run_id), "h": str(household_id), "after": after},
        )
    ).all()
    return [dict(row.payload) for row in rows]


def is_final(event: Event) -> bool:
    return event.get("type") in ("done", "error")


# ---------------------------------------------------------------- Warteschlange


async def queue_position(session: AsyncSession, household_id: UUID, ai_task_id: UUID) -> int:
    """Wie viele Aufgaben des Haushalts vor dieser dran sind (0 = laeuft
    sofort): wartende mit kleinerer queue_order plus eine laufende."""
    row = (
        await session.execute(
            text(
                "select t.status, "
                "(select count(*) from ai_tasks q where q.household_id = t.household_id "
                " and q.status = 'queued' and q.queue_order < t.queue_order) as ahead, "
                "exists (select 1 from ai_tasks r where r.household_id = t.household_id "
                " and r.status = 'running' and r.id <> t.id) as busy "
                "from ai_tasks t where t.id = :id and t.household_id = :h"
            ),
            {"id": str(ai_task_id), "h": str(household_id)},
        )
    ).first()
    if row is None or row.status != "queued":
        return 0
    return int(row.ahead) + (1 if row.busy else 0)


# ---------------------------------------------------------------- Laufprotokoll


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


async def run_log(session: AsyncSession, run: Row[Any]) -> dict[str, Any]:
    """Vertragsform RunLog."""
    run_id = str(run.id)
    household = str(run.household_id)
    calls = (
        await session.execute(
            text(
                "select nr, step, retry_of, retry_reason, started_at, duration_ms, ttft_ms, "
                "finish_reason, model, budget, tools_offered, prompt_tokens, completion_tokens, "
                "reasoning_tokens, tokens_per_s, answer_text, tool_call_names, error "
                "from assistant_run_calls where run_id = :run_id and household_id = :h order by nr"
            ),
            {"run_id": run_id, "h": household},
        )
    ).all()
    tools = (
        await session.execute(
            text(
                "select key, call_nr, tool_name, args, status, result_text, row_count, duration_ms "
                "from assistant_tool_results where run_id = :run_id and household_id = :h "
                "order by key_nr"
            ),
            {"run_id": run_id, "h": household},
        )
    ).all()
    events = (
        await session.execute(
            text(
                "select seq, payload from assistant_run_events "
                "where run_id = :run_id and household_id = :h and type = 'event' order by seq"
            ),
            {"run_id": run_id, "h": household},
        )
    ).all()
    if run.started_at is not None:
        end = run.finished_at or datetime.now(UTC)
        duration = max(0, int((end - run.started_at).total_seconds() * 1000))
    else:
        duration = 0
    return {
        "id": run_id,
        "chat_id": str(run.chat_id),
        "message_id": str(run.message_id),
        "mode": str(run.mode),
        "status": str(run.status),
        "end_reason": run.end_reason,
        "model_configured": run.model_configured,
        "model_answering": run.model_answering,
        "window": {
            "loaded": run.window_loaded,
            "cap": run.window_cap,
            "effective": run.window_effective,
            "source": run.window_source,
        },
        "reasoning_sent": run.reasoning_sent,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "totals": {
            "calls": len(calls),
            "tool_calls": len(tools),
            "prompt_tokens": sum(int(call.prompt_tokens or 0) for call in calls),
            "completion_tokens": sum(int(call.completion_tokens or 0) for call in calls),
            "reasoning_tokens": sum(int(call.reasoning_tokens or 0) for call in calls),
            "duration_ms": duration,
        },
        "error_code": run.error_code,
        "error_message": run.error_message,
        "calls": [
            {
                "nr": int(call.nr),
                "step": str(call.step),
                "retry_of": _int_or_none(call.retry_of),
                "retry_reason": call.retry_reason,
                "started_at": iso(call.started_at),
                "duration_ms": _int_or_none(call.duration_ms),
                "ttft_ms": _int_or_none(call.ttft_ms),
                "finish_reason": call.finish_reason,
                "model": call.model,
                "budget": call.budget,
                "tools_offered": list(call.tools_offered or []),
                "usage": {
                    "prompt": _int_or_none(call.prompt_tokens),
                    "completion": _int_or_none(call.completion_tokens),
                    "reasoning": _int_or_none(call.reasoning_tokens),
                },
                "tokens_per_s": (
                    float(call.tokens_per_s) if call.tokens_per_s is not None else None
                ),
                "answer_text": call.answer_text,
                "tool_call_names": list(call.tool_call_names or []),
                "error": call.error,
            }
            for call in calls
        ],
        "tools": [
            {
                "key": str(tool.key),
                "call_nr": int(tool.call_nr or 0),
                "tool_name": str(tool.tool_name),
                "args": dict(tool.args or {}),
                "status": str(tool.status),
                "result_text": str(tool.result_text or ""),
                "row_count": _int_or_none(tool.row_count),
                "duration_ms": _int_or_none(tool.duration_ms),
            }
            for tool in tools
        ],
        "events": [
            {
                "seq": int(event.seq),
                "kind": str(event.payload.get("kind") or "hinweis"),
                "level": (
                    event.payload.get("level")
                    if event.payload.get("level") in ("info", "warnung", "fehler")
                    else "info"
                ),
                "message": str(event.payload.get("message") or ""),
                "details": event.payload.get("details"),
                "call_nr": event.payload.get("call_nr"),
            }
            for event in events
        ],
    }


# ---------------------------------------------------------------- Abschluss


async def finish_run_rows(
    session: AsyncSession,
    *,
    run_id: UUID,
    status: str,
    end_reason: str | None,
    error_code: str | None,
    error_message: str | None,
    content: str | None = None,
    numbers: dict[str, Any] | None = None,
    context_meter: dict[str, Any] | None = None,
    run_summary: dict[str, Any] | None = None,
) -> Row[Any] | None:
    """Lauf und Antwortnachricht abschliessen (ein Statement je Tabelle).
    Nur ein noch offener Lauf wird geaendert; liefert die Nachricht."""
    run = (
        await session.execute(
            text(
                "update assistant_runs set status = :status, end_reason = :end_reason, "
                "error_code = :error_code, error_message = :error_message, "
                "finished_at = coalesce(finished_at, now()), updated_at = now() "
                "where id = :id and status in ('queued', 'running') "
                "returning message_id, household_id, chat_id, mode"
            ),
            {
                "status": status,
                "end_reason": end_reason,
                "error_code": error_code,
                "error_message": error_message,
                "id": str(run_id),
            },
        )
    ).first()
    if run is None:
        return None
    await session.execute(
        text(
            "update assistant_messages set status = :status, "
            "content = coalesce(:content, content), error_code = :error_code, "
            "error_message = :error_message, numbers = cast(:numbers as jsonb), "
            "context_meter = coalesce(cast(:meter as jsonb), context_meter), "
            "run_summary = cast(:summary as jsonb), updated_at = now() where id = :id"
        ),
        {
            "status": status,
            "content": content,
            "error_code": error_code,
            "error_message": error_message,
            "numbers": _json_param(numbers),
            "meter": _json_param(context_meter),
            "summary": _json_param(run_summary),
            "id": str(run.message_id),
        },
    )
    await session.execute(
        text(
            "update assistant_chats set updated_at = now(), last_message_at = now(), "
            "active_compaction_run_id = case when active_compaction_run_id = :run_id "
            "then null else active_compaction_run_id end where id = :chat_id"
        ),
        {"run_id": str(run_id), "chat_id": str(run.chat_id)},
    )
    return await message_row(session, UUID(str(run.message_id)), UUID(str(run.household_id)))


def final_event(
    message: dict[str, Any], *, error_code: str | None, error_text: str | None
) -> Event:
    """Abschlusszeile: done (auch bei Abbruch, Regel 7) oder error."""
    if message.get("status") == "failed":
        return {
            "type": "error",
            "code": error_code or "failed",
            "message": error_text or "Die Antwort ist fehlgeschlagen.",
            "message_obj": message,
        }
    return {"type": "done", "message": message}
