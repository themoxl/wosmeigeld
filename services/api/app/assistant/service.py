"""Was die API am KI-Feld aendert: Chats, Fragen, Wiederholen, Verdichten,
Abbrechen, Loeschen.

Alles laeuft in der RLS-Sitzung des Nutzers (get_household_session) - auch
Betrachter (E6). Jede Frage ist eine Aufgabe der haushaltsweiten
KI-Warteschlange (ai_tasks, task_type 'assistant_chat'); der Worker fuehrt
sie mit app/assistant/runner.py aus.

Was nach dem Commit auf den Bus muss (Regel 5), geben die Funktionen als
Liste (Kanal, Ereignis) zurueck - die Route veroeffentlicht sie als
Hintergrundaufgabe, die erst nach dem Commit der Abhaengigkeit laeuft.
"""

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.assistant import store
from app.assistant.events import cancel_channel, run_channel
from app.llm import reasons

Event = dict[str, Any]
DEFAULT_TITLE = "Neuer Chat"


class AssistantError(Exception):
    """Fachlicher Fehler mit HTTP-Status und benanntem Code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass
class Outcome:
    """Ergebnis einer Aenderung: Antwort, Weckruf fuer die Warteschlange und
    was nach dem Commit veroeffentlicht wird."""

    body: dict[str, Any]
    wake: bool = False
    publish: list[tuple[str, Event]] = field(default_factory=list)


def _not_found() -> AssistantError:
    return AssistantError(404, "assistant_not_found", "Chat oder Lauf nicht gefunden.")


async def require_chat(
    session: AsyncSession, household_id: UUID, user_id: UUID, chat_id: UUID, *, lock: bool = False
) -> Row[Any]:
    chat = await store.load_chat_row(
        session, chat_id, household_id=household_id, user_id=user_id, lock=lock
    )
    if chat is None:
        raise _not_found()
    return chat


async def require_run(
    session: AsyncSession, household_id: UUID, user_id: UUID, run_id: UUID, *, lock: bool = False
) -> Row[Any]:
    run = await store.load_run_row(
        session, run_id, household_id=household_id, user_id=user_id, lock=lock
    )
    if run is None:
        raise _not_found()
    return run


async def _require_enabled(session: AsyncSession, household_id: UUID) -> None:
    try:
        settings = await llm.load_settings(session, household_id)
    except llm.SettingsRowMissingError as exc:
        raise AssistantError(409, reasons.MODEL_SETTINGS_INVALID, str(exc)) from exc
    if not settings.is_enabled:
        raise AssistantError(409, reasons.AI_DISABLED, reasons.describe(reasons.AI_DISABLED))


# ---------------------------------------------------------------- Chats


async def create_chat(
    session: AsyncSession, household_id: UUID, user_id: UUID, title: str | None
) -> Row[Any]:
    chat_id = (
        await session.execute(
            text(
                "insert into assistant_chats (household_id, created_by, title) "
                "values (:h, :u, :title) returning id"
            ),
            {"h": str(household_id), "u": str(user_id), "title": (title or DEFAULT_TITLE).strip()},
        )
    ).scalar_one()
    return await require_chat(session, household_id, user_id, UUID(str(chat_id)))


async def update_chat(
    session: AsyncSession, chat: Row[Any], changes: dict[str, Any]
) -> Row[Any]:
    assignments = []
    params: dict[str, Any] = {"id": str(chat.id)}
    if "title" in changes and changes["title"] is not None:
        assignments.append("title = :title")
        params["title"] = str(changes["title"]).strip()
    if "reasoning_override" in changes:
        assignments.append("reasoning_override = :reasoning_override")
        params["reasoning_override"] = changes["reasoning_override"]
    if assignments:
        await session.execute(
            text(
                f"update assistant_chats set {', '.join(assignments)}, updated_at = now() "
                "where id = :id"
            ),
            params,
        )
    return await require_chat(
        session, UUID(str(chat.household_id)), UUID(str(chat.created_by)), UUID(str(chat.id))
    )


# ---------------------------------------------------------------- Warteschlange


async def _enqueue(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    *,
    chat_id: UUID,
    run_id: UUID,
    label: str,
) -> UUID:
    """Legt die ai_tasks-Zeile des Laufs an (eine je Lauf, keine
    Zusammenlegung wie bei den uebrigen Typen)."""
    task_id = (
        await session.execute(
            text(
                "insert into ai_tasks (household_id, task_type, request_id, input, route_path, "
                "created_by, status_message) values (:h, 'assistant_chat', :request_id, "
                "cast(:input as jsonb), :route, :u, 'Wartet auf die Verarbeitung') returning id"
            ),
            {
                "h": str(household_id),
                "request_id": f"assistant-run:{run_id}",
                "input": json.dumps({"run_id": str(run_id), "chat_id": str(chat_id)}),
                "route": f"/assistent?assistent={chat_id}",
                "u": str(user_id),
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "insert into ai_task_events (task_id, household_id, level, stage, message) "
            "values (:task_id, :h, 'info', 'queued', :message)"
        ),
        {"task_id": str(task_id), "h": str(household_id), "message": label},
    )
    await session.execute(
        text("update assistant_runs set ai_task_id = :task_id where id = :run_id"),
        {"task_id": str(task_id), "run_id": str(run_id)},
    )
    return UUID(str(task_id))


async def _new_run(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    *,
    chat_id: UUID,
    message_id: UUID,
    question_id: UUID | None,
    mode: str,
    reasoning_override: str | None,
    focus: str | None = None,
    retry_of: UUID | None = None,
) -> tuple[UUID, UUID, int]:
    """Lauf + Aufgabe + erste Stromzeile (status queued mit Warteplatz).
    Liefert (Lauf, Aufgabe, Warteplatz)."""
    run_id = UUID(
        str(
            (
                await session.execute(
                    text(
                        "insert into assistant_runs (household_id, created_by, chat_id, "
                        "message_id, question_message_id, mode, reasoning_override, focus, "
                        "retry_of_run_id) values (:h, :u, :chat_id, :message_id, :question_id, "
                        ":mode, :override, :focus, :retry_of) returning id"
                    ),
                    {
                        "h": str(household_id),
                        "u": str(user_id),
                        "chat_id": str(chat_id),
                        "message_id": str(message_id),
                        "question_id": str(question_id) if question_id else None,
                        "mode": mode,
                        "override": reasoning_override,
                        "focus": focus,
                        "retry_of": str(retry_of) if retry_of else None,
                    },
                )
            ).scalar_one()
        )
    )
    await session.execute(
        text("update assistant_messages set run_id = :run_id where id = :id"),
        {"run_id": str(run_id), "id": str(message_id)},
    )
    task_id = await _enqueue(
        session,
        household_id,
        user_id,
        chat_id=chat_id,
        run_id=run_id,
        label=(
            "KI-Frage wurde in die Warteschlange aufgenommen."
            if mode == "answer"
            else "Verdichtung des Chats wurde in die Warteschlange aufgenommen."
        ),
    )
    position = await store.queue_position(session, household_id, task_id)
    await store.append_event(
        session, run_id, {"type": "status", "status": "queued", "queue_position": position}
    )
    return run_id, task_id, position


async def _insert_message(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    *,
    chat_id: UUID,
    seq: int,
    role: str,
    status: str,
    content: str = "",
    kind: str = "chat",
    reply_to: UUID | None = None,
    request_id: str | None = None,
) -> UUID:
    return UUID(
        str(
            (
                await session.execute(
                    text(
                        "insert into assistant_messages (household_id, created_by, chat_id, seq, "
                        "role, kind, content, status, reply_to_message_id, request_id) values "
                        "(:h, :u, :chat_id, :seq, :role, :kind, :content, :status, :reply_to, "
                        ":request_id) returning id"
                    ),
                    {
                        "h": str(household_id),
                        "u": str(user_id),
                        "chat_id": str(chat_id),
                        "seq": seq,
                        "role": role,
                        "kind": kind,
                        "content": content,
                        "status": status,
                        "reply_to": str(reply_to) if reply_to else None,
                        "request_id": request_id,
                    },
                )
            ).scalar_one()
        )
    )


async def _touch_chat(session: AsyncSession, chat_id: UUID) -> None:
    await session.execute(
        text(
            "update assistant_chats set updated_at = now(), last_message_at = now() "
            "where id = :id"
        ),
        {"id": str(chat_id)},
    )


def _title_from(question: str) -> str:
    first = next((part.strip() for part in question.splitlines() if part.strip()), DEFAULT_TITLE)
    # fest: Titel aus der ersten Frage - Anzeige in der Chatliste, kein Promptmaterial
    return first if len(first) <= 80 else first[:79].rstrip() + "…"


async def _send_response(
    session: AsyncSession, household_id: UUID, question_id: UUID, answer_id: UUID
) -> dict[str, Any]:
    question = await store.message_row(session, question_id, household_id)
    answer = await store.message_row(session, answer_id, household_id)
    position = 0
    if answer.run_id is not None:
        run = await store.load_run_row(session, UUID(str(answer.run_id)), household_id=household_id)
        if run is not None and run.ai_task_id is not None and run.status == "queued":
            position = await store.queue_position(session, household_id, UUID(str(run.ai_task_id)))
    return {
        "user_message": store.message_of(question),
        "assistant_message": store.message_of(answer),
        "run_id": str(answer.run_id) if answer.run_id else "",
        "queue_position": position,
    }


async def _replayed_send(
    session: AsyncSession, household_id: UUID, chat_id: UUID, request_id: str
) -> dict[str, Any] | None:
    question = (
        await session.execute(
            text(
                "select id from assistant_messages where chat_id = :chat_id "
                "and request_id = :request_id and role = 'user'"
            ),
            {"chat_id": str(chat_id), "request_id": request_id},
        )
    ).first()
    if question is None:
        return None
    answer = (
        await session.execute(
            text(
                "select id from assistant_messages where reply_to_message_id = :id "
                "order by seq limit 1"
            ),
            {"id": str(question.id)},
        )
    ).first()
    if answer is None:
        return None
    return await _send_response(
        session, household_id, UUID(str(question.id)), UUID(str(answer.id))
    )


async def send_message(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    chat_id: UUID,
    *,
    content: str,
    request_id: str,
    reasoning_override: str | None,
    override_given: bool,
) -> Outcome:
    """Neue Frage: Nutzer- und Antwortnachricht, Lauf und Aufgabe. Gleiche
    request_id im selben Chat liefert dieselbe Antwort (idempotent)."""
    await require_chat(session, household_id, user_id, chat_id)
    replay = await _replayed_send(session, household_id, chat_id, request_id)
    if replay is not None:
        return Outcome(body=replay)
    await _require_enabled(session, household_id)
    question = content.strip()
    if not question:
        raise AssistantError(422, "assistant_empty_question", "Die Frage ist leer.")
    chat = await require_chat(session, household_id, user_id, chat_id, lock=True)
    try:
        async with session.begin_nested():
            seq = await store.next_message_seq(session, chat_id, 2)
            question_id = await _insert_message(
                session,
                household_id,
                user_id,
                chat_id=chat_id,
                seq=seq,
                role="user",
                status="done",
                content=question,
                request_id=request_id,
            )
            answer_id = await _insert_message(
                session,
                household_id,
                user_id,
                chat_id=chat_id,
                seq=seq + 1,
                role="assistant",
                status="queued",
                reply_to=question_id,
            )
    except IntegrityError:
        replay = await _replayed_send(session, household_id, chat_id, request_id)
        if replay is None:
            raise
        return Outcome(body=replay)
    await _new_run(
        session,
        household_id,
        user_id,
        chat_id=chat_id,
        message_id=answer_id,
        question_id=question_id,
        mode="answer",
        reasoning_override=reasoning_override if override_given else chat.reasoning_override,
    )
    await _touch_chat(session, chat_id)
    if chat.title == DEFAULT_TITLE and int(chat.last_message_seq) == 0:
        await session.execute(
            text("update assistant_chats set title = :title where id = :id"),
            {"title": _title_from(question), "id": str(chat_id)},
        )
    body = await _send_response(session, household_id, question_id, answer_id)
    return Outcome(body=body, wake=True)


async def retry_run(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    run_id: UUID,
    *,
    reasoning_override: str | None,
    override_given: bool,
) -> Outcome:
    """Neue Antwortnachricht zur selben Frage (Regel 10: dieselbe
    user_message)."""
    run = await require_run(session, household_id, user_id, run_id)
    if run.status in store.ACTIVE_STATUSES:
        raise AssistantError(409, "assistant_run_active", "Der Lauf ist noch nicht beendet.")
    if run.mode != "answer" or run.question_message_id is None:
        raise AssistantError(
            409, "assistant_run_not_retryable", "Nur Antworten lassen sich wiederholen."
        )
    await _require_enabled(session, household_id)
    chat_id = UUID(str(run.chat_id))
    chat = await require_chat(session, household_id, user_id, chat_id, lock=True)
    seq = await store.next_message_seq(session, chat_id, 1)
    question_id = UUID(str(run.question_message_id))
    answer_id = await _insert_message(
        session,
        household_id,
        user_id,
        chat_id=chat_id,
        seq=seq,
        role="assistant",
        status="queued",
        reply_to=question_id,
    )
    await _new_run(
        session,
        household_id,
        user_id,
        chat_id=chat_id,
        message_id=answer_id,
        question_id=question_id,
        mode="answer",
        reasoning_override=reasoning_override if override_given else chat.reasoning_override,
        retry_of=UUID(str(run.id)),
    )
    await _touch_chat(session, chat_id)
    body = await _send_response(session, household_id, question_id, answer_id)
    return Outcome(body=body, wake=True)


async def compact_chat(
    session: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    chat_id: UUID,
    *,
    focus: str | None,
) -> Outcome:
    """Verdichtung auf Knopfdruck als eigener Lauf mit Strom (Regel 8)."""
    chat = await require_chat(session, household_id, user_id, chat_id, lock=True)
    busy = (
        await session.execute(
            text(
                "select 1 from assistant_runs where chat_id = :chat_id "
                "and status in ('queued', 'running') limit 1"
            ),
            {"chat_id": str(chat_id)},
        )
    ).first()
    if busy is not None:
        raise AssistantError(
            409,
            "assistant_chat_busy",
            "Verdichten geht, wenn die Aufträge dieses Chats fertig sind.",
        )
    questions = (
        await session.execute(
            text(
                "select count(*) from assistant_messages where chat_id = :chat_id "
                "and role = 'user' and kind = 'chat'"
            ),
            {"chat_id": str(chat_id)},
        )
    ).scalar_one()
    if int(questions) < 2:
        raise AssistantError(
            409,
            "assistant_too_few_questions",
            "Zusammengefasst wird, was vor der letzten Frage steht - dafür braucht der Chat "
            "mindestens zwei Fragen.",
        )
    await _require_enabled(session, household_id)
    seq = await store.next_message_seq(session, chat_id, 1)
    message_id = await _insert_message(
        session,
        household_id,
        user_id,
        chat_id=chat_id,
        seq=seq,
        role="assistant",
        status="queued",
        kind="compaction",
    )
    run_id, _task_id, _position = await _new_run(
        session,
        household_id,
        user_id,
        chat_id=chat_id,
        message_id=message_id,
        question_id=None,
        mode="compact",
        reasoning_override=chat.reasoning_override,
        focus=(focus or "").strip() or None,
    )
    await session.execute(
        text("update assistant_chats set active_compaction_run_id = :run_id where id = :id"),
        {"run_id": str(run_id), "id": str(chat_id)},
    )
    return Outcome(body={"run_id": str(run_id)}, wake=True)


# ---------------------------------------------------------------- Abbrechen


async def _cancel(
    session: AsyncSession, household_id: UUID, run: Row[Any]
) -> tuple[str, list[tuple[str, Event]]]:
    """Wartend: sofort beenden (Aufgabe, Lauf, Nachricht, done-Zeile).
    Laufend: ai_tasks.cancel_requested + Abbruch-Kanal - der Worker schliesst
    die Verbindung zum Modell und endet mit done (status cancelled)."""
    if run.status not in store.ACTIVE_STATUSES:
        return str(run.status), []
    task = None
    if run.ai_task_id is not None:
        task = (
            await session.execute(
                text(
                    "select id, status from ai_tasks where id = :id and household_id = :h "
                    "for update"
                ),
                {"id": str(run.ai_task_id), "h": str(household_id)},
            )
        ).first()
    waiting = run.status == "queued" and (task is None or task.status == "queued")
    if waiting:
        if task is not None:
            await session.execute(
                text(
                    "update ai_tasks set status = 'cancelled', cancel_requested = true, "
                    "cancel_requested_at = coalesce(cancel_requested_at, now()), "
                    "current_stage = 'cancelled', status_message = 'Vom Nutzer abgebrochen', "
                    "completed_at = now(), updated_at = now() where id = :id and status = 'queued'"
                ),
                {"id": str(task.id)},
            )
            await session.execute(
                text(
                    "insert into ai_task_events (task_id, household_id, level, stage, message) "
                    "values (:task_id, :h, 'info', 'cancelled', 'Aufgabe wurde abgebrochen.')"
                ),
                {"task_id": str(task.id), "h": str(household_id)},
            )
        message = await store.finish_run_rows(
            session,
            run_id=UUID(str(run.id)),
            status="cancelled",
            end_reason="cancelled",
            error_code=reasons.MODEL_CANCELLED,
            error_message="Vom Nutzer abgebrochen.",
        )
        if message is None:
            return str(run.status), []
        event = await store.append_event(
            session,
            UUID(str(run.id)),
            store.final_event(
                store.message_of(message),
                error_code=reasons.MODEL_CANCELLED,
                error_text="Vom Nutzer abgebrochen.",
            ),
        )
        return "cancelled", [(run_channel(run.id), event)]
    if task is not None:
        await session.execute(
            text(
                "update ai_tasks set cancel_requested = true, "
                "cancel_requested_at = coalesce(cancel_requested_at, now()), "
                "status_message = 'Abbruch angefordert; die Verbindung zum Modell wird "
                "geschlossen', updated_at = now() where id = :id "
                "and status in ('queued', 'running')"
            ),
            {"id": str(task.id)},
        )
        await session.execute(
            text(
                "insert into ai_task_events (task_id, household_id, level, stage, message) "
                "values (:task_id, :h, 'warning', 'cancel', "
                "'Abbruch angefordert; die Verbindung zum Modell wird geschlossen.')"
            ),
            {"task_id": str(task.id), "h": str(household_id)},
        )
    return str(run.status), [(cancel_channel(run.id), {"type": "cancel"})]


async def cancel_run(
    session: AsyncSession, household_id: UUID, user_id: UUID, run_id: UUID
) -> Outcome:
    run = await require_run(session, household_id, user_id, run_id, lock=True)
    status, publish = await _cancel(session, household_id, run)
    return Outcome(body={"status": status}, publish=publish)


async def delete_chat(
    session: AsyncSession, household_id: UUID, user_id: UUID, chat_id: UUID
) -> Outcome:
    """Chat loeschen; laufende Auftraege werden vorher abgebrochen."""
    await require_chat(session, household_id, user_id, chat_id, lock=True)
    publish: list[tuple[str, Event]] = []
    active = (
        await session.execute(
            text(
                "select id from assistant_runs where chat_id = :chat_id "
                "and status in ('queued', 'running') for update"
            ),
            {"chat_id": str(chat_id)},
        )
    ).all()
    for row in active:
        run = await require_run(session, household_id, user_id, UUID(str(row.id)))
        _status, events = await _cancel(session, household_id, run)
        publish.extend(item for item in events if item[0] == cancel_channel(run.id))
    await session.execute(
        text("delete from assistant_chats where id = :id and household_id = :h"),
        {"id": str(chat_id), "h": str(household_id)},
    )
    return Outcome(body={}, publish=publish)
