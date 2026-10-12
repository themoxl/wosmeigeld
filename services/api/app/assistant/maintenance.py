"""Aufraeumen im KI-Feld - laeuft im Wiederherstellungsjob der
KI-Warteschlange (worker/ai/task_processor.py, recover_stale_tasks).

* Verwaiste Laeufe: Lauf noch queued/running, seine Aufgabe aber beendet oder
  geloescht (Worker abgestuerzt, Aufgabe hart abgebrochen). Sie werden mit
  der passenden Abschlusszeile beendet - sonst wartete ein offener Strom ewig.
* Aufbewahrung (E6): Chats, deren letzte Aenderung laenger zurueckliegt als
  ai_household_settings.assistant_retention_days, werden geloescht (samt
  Nachrichten, Laeufen und Werkzeugergebnissen).

Ohne RLS (Worker); jede Abfrage nennt ihre Bedingungen selbst.
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant import store
from app.assistant.events import EventBus, get_event_bus, run_channel
from app.llm import reasons

logger = logging.getLogger(__name__)


async def finish_orphaned_runs(session: AsyncSession, bus: EventBus | None = None) -> int:
    """Beendet Laeufe, deren Aufgabe nicht mehr laeuft. Committet selbst und
    veroeffentlicht danach die Abschlusszeilen."""
    rows = (
        await session.execute(
            text(
                "select r.id, t.status as task_status, t.error_message as task_error "
                "from assistant_runs r left join ai_tasks t on t.id = r.ai_task_id "
                "where r.status in ('queued', 'running') "
                "and (t.id is null or t.status not in ('queued', 'running')) "
                "for update of r skip locked"
            )
        )
    ).all()
    published: list[tuple[UUID, dict[str, object]]] = []
    for row in rows:
        run_id = UUID(str(row.id))
        if row.task_status == "cancelled":
            status, end_reason = "cancelled", "cancelled"
            code: str | None = reasons.MODEL_CANCELLED
            message_text: str | None = "Vom Nutzer abgebrochen."
        else:
            status, end_reason = "failed", "failed"
            code = "assistant_worker_lost"
            message_text = (
                "Der Verarbeitungsdienst hat den KI-Lauf nicht zu Ende gebracht"
                + (f" ({row.task_error})." if row.task_error else ".")
                + " Bitte erneut versuchen."
            )
        message = await store.finish_run_rows(
            session,
            run_id=run_id,
            status=status,
            end_reason=end_reason,
            error_code=code,
            error_message=message_text,
        )
        if message is None:
            continue
        event = await store.append_event(
            session,
            run_id,
            store.final_event(store.message_of(message), error_code=code, error_text=message_text),
        )
        published.append((run_id, event))
    await session.commit()
    await _publish(published, bus)
    return len(published)


async def _publish(items: list[tuple[UUID, dict[str, object]]], bus: EventBus | None) -> None:
    """Nach dem Commit veroeffentlichen. Ein selbst geholter Bus wird danach
    geschlossen: der Wiederherstellungsjob laeuft jedes Mal in einer neuen
    Ereignisschleife."""
    if not items:
        return
    target = bus or get_event_bus()
    try:
        for run_id, event in items:
            try:
                await target.publish(run_channel(run_id), event)
            except Exception:  # noqa: BLE001 - der Strom findet die Zeile in der Datenbank
                logger.warning("Stromzeile des KI-Laufs %s nicht veroeffentlicht", run_id)
    finally:
        if bus is None:
            await target.close()


async def announce_queue_positions(
    session: AsyncSession, household_id: UUID, bus: EventBus | None = None
) -> int:
    """Neuer Warteplatz fuer jede wartende Frage des Haushalts, wenn er sich
    geaendert hat (die Warteschlange ist eine Aufgabe weiter). Committet
    selbst und veroeffentlicht danach."""
    rows = (
        await session.execute(
            text(
                "select r.id as run_id, t.id as task_id, "
                "(select e.payload->>'queue_position' from assistant_run_events e "
                " where e.run_id = r.id and e.type = 'status' order by e.seq desc limit 1) "
                "as last_position "
                "from assistant_runs r join ai_tasks t on t.id = r.ai_task_id "
                "where r.household_id = :h and r.status = 'queued' and t.status = 'queued'"
            ),
            {"h": str(household_id)},
        )
    ).all()
    published: list[tuple[UUID, dict[str, object]]] = []
    for row in rows:
        position = await store.queue_position(session, household_id, UUID(str(row.task_id)))
        if row.last_position is not None and str(position) == str(row.last_position):
            continue
        event = await store.append_event(
            session,
            UUID(str(row.run_id)),
            {"type": "status", "status": "queued", "queue_position": position},
        )
        published.append((UUID(str(row.run_id)), event))
    await session.commit()
    await _publish(published, bus)
    return len(published)


async def purge_expired_chats(session: AsyncSession) -> int:
    """Loescht Chats jenseits der Aufbewahrung ihres Haushalts. Ein Chat mit
    offenem Lauf bleibt, bis der Lauf endet."""
    deleted = (
        await session.execute(
            text(
                "delete from assistant_chats c using ai_household_settings s "
                "where s.household_id = c.household_id "
                "and c.updated_at < now() - make_interval(days => s.assistant_retention_days) "
                "and not exists (select 1 from assistant_runs r where r.chat_id = c.id "
                "and r.status in ('queued', 'running')) returning c.id"
            )
        )
    ).all()
    await session.commit()
    return len(deleted)
