"""Serieller Executor fuer die persistente KI-Warteschlange."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from worker.ai.processor import process_classification_run

# services/api (app.*) kommt ueber PYTHONPATH: services/worker/Dockerfile,
# pyproject.toml (pytest) und scripts/dev.py setzen ihn.

_VISIBILITY_ATTEMPTS = 10
_VISIBILITY_DELAY_SECONDS = 1.0

logger = logging.getLogger(__name__)


async def _event(
    session: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    message: str,
    *,
    stage: str | None = None,
    level: str = "info",
) -> None:
    await session.execute(
        text(
            "insert into ai_task_events (task_id, household_id, level, stage, message) "
            "values (:task_id, :household_id, :level, :stage, :message)"
        ),
        {
            "task_id": str(task_id),
            "household_id": str(household_id),
            "level": level,
            "stage": stage,
            "message": message,
        },
    )


async def _claim_next(
    session: AsyncSession, household_id: UUID
) -> tuple[Literal["claimed", "busy", "empty"], Row[Any] | None]:
    # Eine Transaktionssperre serialisiert nur den kurzen Claim. Modellaufrufe
    # laufen ohne gehaltene DB-Sperre; der partielle Unique-Index bleibt die
    # zweite Schutzschicht.
    await session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:household_id, 0))"),
        {"household_id": str(household_id)},
    )
    running = (
        await session.execute(
            text(
                "select id from ai_tasks where household_id = :household_id "
                "and status = 'running' limit 1"
            ),
            {"household_id": str(household_id)},
        )
    ).first()
    if running is not None:
        await session.commit()
        return "busy", None

    row = (
        await session.execute(
            text(
                "select id, task_type, input, classification_run_id from ai_tasks "
                "where household_id = :household_id and status = 'queued' "
                "and not cancel_requested order by queue_order asc "
                "for update skip locked limit 1"
            ),
            {"household_id": str(household_id)},
        )
    ).first()
    if row is None:
        await session.commit()
        return "empty", None

    claimed = (
        await session.execute(
            text(
                "update ai_tasks set status = 'running', current_stage = 'preparing', "
                "status_message = 'Aufgabe wird vorbereitet', "
                "started_at = coalesce(started_at, now()), "
                "heartbeat_at = now(), updated_at = now(), attempt_count = attempt_count + 1 "
                "where id = :id and household_id = :household_id and status = 'queued' "
                "returning id, task_type, input, classification_run_id"
            ),
            {"id": str(row.id), "household_id": str(household_id)},
        )
    ).first()
    if claimed is None:
        await session.rollback()
        return "empty", None
    await _event(
        session,
        household_id,
        UUID(str(claimed.id)),
        "Verarbeitung wurde gestartet.",
        stage="preparing",
    )
    await session.commit()
    return "claimed", claimed


async def _cancel_requested(session: AsyncSession, household_id: UUID, task_id: UUID) -> bool:
    row = (
        await session.execute(
            text(
                "select cancel_requested, status from ai_tasks "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(task_id), "household_id": str(household_id)},
        )
    ).first()
    return row is None or row.status != "running" or bool(row.cancel_requested)


async def _stage(
    session: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    stage: str,
    message: str,
) -> None:
    await session.execute(
        text(
            "update ai_tasks set current_stage = :stage, status_message = :message, "
            "heartbeat_at = now(), updated_at = now() where id = :id "
            "and household_id = :household_id and status = 'running'"
        ),
        {
            "id": str(task_id),
            "household_id": str(household_id),
            "stage": stage,
            "message": message,
        },
    )
    await _event(session, household_id, task_id, message, stage=stage)
    await session.commit()


async def _finish(
    session: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    task_status: Literal["awaiting_confirmation", "completed", "failed", "cancelled"],
    message: str,
    *,
    result: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    model_name: str | None = None,
    category_operation_id: UUID | None = None,
    error_message: str | None = None,
) -> None:
    await session.execute(
        text(
            "update ai_tasks set status = :status, current_stage = :stage, "
            "status_message = :message, result = cast(:result as jsonb), "
            "result_summary = cast(:summary as jsonb), model_name = :model_name, "
            "category_operation_id = coalesce(:category_operation_id, category_operation_id), "
            "error_message = :error_message, completed_at = now(), heartbeat_at = now(), "
            "updated_at = now() where id = :id and household_id = :household_id "
            "and status = 'running'"
        ),
        {
            "status": task_status,
            "stage": task_status,
            "message": message,
            "result": json.dumps(result, ensure_ascii=False) if result is not None else None,
            "summary": json.dumps(summary or {}, ensure_ascii=False),
            "model_name": model_name,
            "category_operation_id": (
                str(category_operation_id) if category_operation_id is not None else None
            ),
            "error_message": error_message,
            "id": str(task_id),
            "household_id": str(household_id),
        },
    )
    level = "error" if task_status == "failed" else "info"
    await _event(
        session,
        household_id,
        task_id,
        message,
        stage=task_status,
        level=level,
    )
    await session.commit()


async def _run_category(session: AsyncSession, household_id: UUID, task_id: UUID) -> None:
    from app.services import category_suggestion_service
    from app.services.ai_answers import AIStepError

    creator = (
        await session.execute(
            text("select created_by from ai_tasks where id = :id and household_id = :household_id"),
            {"id": str(task_id), "household_id": str(household_id)},
        )
    ).scalar_one_or_none()
    await _stage(
        session,
        household_id,
        task_id,
        "category_clusters",
        "Offene Buchungen werden gruppiert und mit der Taxonomie verglichen",
    )

    async def is_cancelled() -> bool:
        return await _cancel_requested(session, household_id, task_id)

    try:
        preview = await category_suggestion_service.build_preview(
            session,
            household_id,
            UUID(str(creator)) if creator is not None else None,
            ai_task_id=task_id,
            is_cancelled=is_cancelled,
        )
    except AIStepError as exc:
        # Benannter KI-Fehlschlag (kein Modell, Zeitbudget, nichts
        # Verwertbares ...): hier statt in _execute beenden, weil dort
        # zurueckgerollt wuerde - samt der llm_calls-Zeilen dieses Laufs.
        await _finish(
            session,
            household_id,
            task_id,
            "failed",
            "Die KI hat keine verwertbaren Kategorienvorschläge geliefert.",
            error_message=f"{exc.message} (Kennung: {exc.reason_code})",
        )
        return
    payload = preview.model_dump(mode="json")
    if await _cancel_requested(session, household_id, task_id):
        await _finish(
            session,
            household_id,
            task_id,
            "cancelled",
            "Aufgabe wurde nach dem laufenden Modellschritt abgebrochen.",
            result=payload,
            category_operation_id=preview.operation_id,
        )
        return
    count = len(preview.suggestions)
    await _finish(
        session,
        household_id,
        task_id,
        "awaiting_confirmation" if count else "completed",
        (
            f"{count} Kategorienvorschlag/-vorschlaege warten auf deine Pruefung."
            if count
            else "Keine neuen Kategorienvorschlaege gefunden."
        ),
        result=payload,
        summary={
            "suggestions": count,
            "uncategorized_transactions": preview.uncategorized_transactions,
            "clustered_transactions": preview.clustered_transactions,
        },
        model_name=preview.model_name,
        category_operation_id=preview.operation_id,
    )


async def _run_contract_duplicates(
    session: AsyncSession, household_id: UUID, task_id: UUID, task_input: dict[str, Any]
) -> None:
    from app.services import contract_ai_crosscheck_service

    await _stage(
        session,
        household_id,
        task_id,
        "contract_pairs",
        "Vertragspaare werden vorgefiltert und vom Modell geprueft",
    )
    result = await contract_ai_crosscheck_service.crosscheck_household(
        session,
        household_id,
        contract_ids=(
            [UUID(str(value)) for value in task_input["contract_ids"]]
            if task_input.get("contract_ids")
            else None
        ),
    )
    payload = result.model_dump(mode="json")
    if await _cancel_requested(session, household_id, task_id):
        await _finish(
            session,
            household_id,
            task_id,
            "cancelled",
            "Aufgabe wurde nach dem laufenden Modellschritt abgebrochen.",
            result=payload,
        )
        return
    count = len(result.items)
    await _finish(
        session,
        household_id,
        task_id,
        "awaiting_confirmation" if count else "completed",
        (
            f"{count} Vertragspaar/-paare warten auf deine Pruefung."
            if count
            else result.meldung or "Keine zu pruefenden Vertragspaare gefunden."
        ),
        result=payload,
        summary={"checked": result.geprueft, "skipped": result.uebersprungen},
        model_name=result.modell_name,
    )


async def _run_contract_transactions(
    session: AsyncSession, household_id: UUID, task_id: UUID, task_input: dict[str, Any]
) -> None:
    from app.services import contract_ai_crosscheck_service

    await _stage(
        session,
        household_id,
        task_id,
        "contract_transactions",
        "Freie Buchungen werden vorgefiltert und mit dem Vertrag verglichen",
    )
    result = await contract_ai_crosscheck_service.check_transactions_for_contract(
        session,
        household_id,
        UUID(str(task_input["contract_id"])),
        transaction_ids=(
            [UUID(str(value)) for value in task_input["transaction_ids"]]
            if task_input.get("transaction_ids")
            else None
        ),
    )
    payload = result.model_dump(mode="json")
    if await _cancel_requested(session, household_id, task_id):
        await _finish(
            session,
            household_id,
            task_id,
            "cancelled",
            "Aufgabe wurde nach dem laufenden Modellschritt abgebrochen.",
            result=payload,
        )
        return
    count = len(result.items)
    await _finish(
        session,
        household_id,
        task_id,
        "awaiting_confirmation" if count else "completed",
        (
            f"{count} Buchung(en) warten auf deine Pruefung."
            if count
            else result.meldung or "Keine passenden freien Buchungen gefunden."
        ),
        result=payload,
        summary={
            "checked": result.geprueft,
            "skipped": result.uebersprungen,
            "candidates": result.kandidaten_gesamt,
        },
        model_name=result.modell_name,
    )


async def _run_classification(
    session: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    classification_run_id: UUID | None,
) -> None:
    if classification_run_id is None:
        raise RuntimeError("Zur Klassifikationsaufgabe fehlt der verknuepfte Lauf.")
    await _stage(
        session,
        household_id,
        task_id,
        "preparing",
        "Kategorien und Modellfaehigkeiten werden vorbereitet",
    )
    await process_classification_run(session, classification_run_id, task_id)
    run = (
        await session.execute(
            text(
                "select status, total_count, processed_count, success_count, error_count, "
                "error_message from classification_runs where id = :run_id "
                "and household_id = :household_id"
            ),
            {"run_id": str(classification_run_id), "household_id": str(household_id)},
        )
    ).one()
    summary = {
        "total": run.total_count,
        "processed": run.processed_count,
        "success": run.success_count,
        "errors": run.error_count,
    }
    if run.status == "cancelled":
        await _finish(
            session,
            household_id,
            task_id,
            "cancelled",
            "Klassifikation wurde abgebrochen.",
            summary=summary,
        )
    elif run.status == "failed":
        await _finish(
            session,
            household_id,
            task_id,
            "failed",
            "Klassifikation ist fehlgeschlagen.",
            summary=summary,
            error_message=run.error_message,
        )
    else:
        await _finish(
            session,
            household_id,
            task_id,
            "awaiting_confirmation" if run.processed_count else "completed",
            (
                f"{run.processed_count} Buchung(en) wurden geprueft."
                if run.processed_count
                else "Keine offenen Buchungen gefunden."
            ),
            result={"classification_run_id": str(classification_run_id)},
            summary=summary,
        )


async def _run_project_cost_estimate(
    session: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    task_input: dict[str, Any],
) -> None:
    """Persistenter High-Reasoning-Kostenvorschlag mit sichtbaren Stufen."""
    from app.schemas.projects import CostAiSuggestionRequest
    from app.services import project_cost_ai_suggestion_service

    creator = (
        await session.execute(
            text("select created_by from ai_tasks where id = :id and household_id = :household_id"),
            {"id": str(task_id), "household_id": str(household_id)},
        )
    ).scalar_one_or_none()
    await _stage(
        session,
        household_id,
        task_id,
        "cost_preparing",
        "Kostenpositionen, KI-Einstellungen und Modell werden vorbereitet",
    )

    async def report_stage(stage: str, message: str) -> None:
        await _stage(session, household_id, task_id, stage, message)

    preview = await project_cost_ai_suggestion_service.build_preview(
        session,
        household_id,
        UUID(str(task_input["project_id"])),
        UUID(str(task_input["scenario_id"])),
        UUID(str(creator)) if creator is not None else None,
        CostAiSuggestionRequest(
            description=str(task_input["description"]),
            use_web_search=bool(task_input.get("use_web_search", False)),
        ),
        ai_task_id=task_id,
        stage_callback=report_stage,
    )
    payload = preview.model_dump(mode="json")
    proposed = sum(1 for item in preview.suggestions if item.proposed_estimate_minor is not None)
    summary = {
        "positions": len(preview.suggestions),
        "proposed": proposed,
        "generation_status": preview.generation_status,
    }
    if await _cancel_requested(session, household_id, task_id):
        await _finish(
            session,
            household_id,
            task_id,
            "cancelled",
            "Kostenschaetzung wurde nach dem laufenden Modellschritt abgebrochen.",
            result=payload,
            summary=summary,
            model_name=preview.model_name,
        )
    elif preview.error_code is not None:
        await _finish(
            session,
            household_id,
            task_id,
            "failed",
            "Die KI-Kostenschaetzung ist technisch fehlgeschlagen.",
            result=payload,
            summary=summary,
            model_name=preview.model_name,
            error_message=preview.error_message or preview.error_code,
        )
    elif proposed:
        await _finish(
            session,
            household_id,
            task_id,
            "awaiting_confirmation",
            f"{proposed} Kostenschaetzung(en) warten auf deine Pruefung.",
            result=payload,
            summary=summary,
            model_name=preview.model_name,
        )
    else:
        await _finish(
            session,
            household_id,
            task_id,
            "completed",
            "Das Modell hat nachvollziehbar keine Kostenschaetzung vorgeschlagen.",
            result=payload,
            summary=summary,
            model_name=preview.model_name,
        )


async def _run_health_document_extraction(
    session: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    task_input: dict[str, Any],
) -> None:
    from worker.health_documents.processor import process_queued_documents

    await _stage(
        session,
        household_id,
        task_id,
        "document_preparing",
        "Private Dokumentablage und Dateimetadaten werden vorbereitet",
    )

    async def report_stage(stage: str, message: str) -> None:
        await _stage(session, household_id, task_id, stage, message)

    async def is_cancelled() -> bool:
        return await _cancel_requested(session, household_id, task_id)

    preferred = task_input.get("document_id")
    run = await process_queued_documents(
        session,
        household_id,
        UUID(str(preferred)) if preferred else None,
        report_stage,
        is_cancelled,
        # Jede llm_calls-Zeile der Auswertung gehoert zu dieser Aufgabe.
        ai_task_id=task_id,
    )
    result = {"document_ids": run.document_ids}
    summary = {
        "processed": run.processed_count,
        "confirmed": run.confirmed_count,
        "duplicates": run.duplicate_count,
        "needs_review": run.review_count,
        "needs_attention": run.failed_count,
    }
    if run.cancelled:
        await _finish(
            session,
            household_id,
            task_id,
            "cancelled",
            "Dokumentauswertung wurde nach dem laufenden Schritt abgebrochen.",
            result=result,
            summary=summary,
            model_name=run.model_name,
        )
    elif run.review_count:
        await _finish(
            session,
            household_id,
            task_id,
            "awaiting_confirmation",
            f"{run.processed_count} Dokument(e) ausgewertet; "
            f"{run.review_count} warten auf Prüfung.",
            result=result,
            summary=summary,
            model_name=run.model_name,
        )
    elif run.processed_count:
        # Nichts blieb offen: entweder automatisch uebernommen oder als
        # fehlgeschlagen markiert. Ein "wartet auf Bestaetigung" waere hier
        # eine Aufforderung ohne zugehoerige Aufgabe.
        await _finish(
            session,
            household_id,
            task_id,
            "completed",
            f"{run.processed_count} Dokument(e) ausgewertet; "
            f"{run.confirmed_count} automatisch übernommen, "
            f"{run.duplicate_count} als Dublette erkannt.",
            result=result,
            summary=summary,
            model_name=run.model_name,
        )
    else:
        await _finish(
            session,
            household_id,
            task_id,
            "completed",
            "Keine offenen Gesundheitsdokumente gefunden.",
            result=result,
            summary=summary,
            model_name=run.model_name,
        )


async def _run_assistant_chat(
    session: AsyncSession, household_id: UUID, task_id: UUID, task_input: dict[str, Any]
) -> None:
    """Frage oder Verdichtung im KI-Feld (app/assistant/runner.py). Der Lauf
    haelt seinen Zustand selbst fest (assistant_runs, Strom); hier wird nur
    die Aufgabe abgeschlossen."""
    from app.assistant.runner import execute_run

    from worker.db import get_session_factory

    run_id = task_input.get("run_id")
    if not run_id:
        raise RuntimeError("Zur KI-Frage fehlt der Lauf (input.run_id).")
    await _stage(session, household_id, task_id, "assistant_running", "KI-Frage wird beantwortet")
    result = await execute_run(UUID(str(run_id)), session_factory=get_session_factory())
    outcome: dict[str, Any] = {
        "run_id": str(run_id),
        "chat_id": str(result.chat_id) if result.chat_id else task_input.get("chat_id"),
    }
    summary = {"run_status": result.status}
    if result.status == "done":
        await _finish(
            session,
            household_id,
            task_id,
            "completed",
            "KI-Frage beantwortet.",
            result=outcome,
            summary=summary,
            model_name=result.model_name,
        )
    elif result.status in ("cancelled", "missing"):
        await _finish(
            session,
            household_id,
            task_id,
            "cancelled",
            (
                "KI-Frage wurde abgebrochen."
                if result.status == "cancelled"
                else "Der Chat zu dieser KI-Frage existiert nicht mehr."
            ),
            result=outcome,
            summary=summary,
            model_name=result.model_name,
        )
    else:
        await _finish(
            session,
            household_id,
            task_id,
            "failed",
            "KI-Frage ist fehlgeschlagen.",
            result=outcome,
            summary=summary,
            model_name=result.model_name,
            error_message=(
                f"{result.error_code}: {result.error_message}"
                if result.error_code
                else result.error_message
            ),
        )


def _error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return str(detail)[:1000]  # fest: Fehlermeldung im Aufgabenprotokoll, kein Prompt
    return str(exc)[:1000] or exc.__class__.__name__  # fest: Fehlermeldung, kein Prompt


async def _execute(session: AsyncSession, household_id: UUID, task: Row[Any]) -> None:
    task_id = UUID(str(task.id))
    task_input = dict(task.input or {})
    try:
        if task.task_type == "assistant_chat":
            # Auch bei schon angefordertem Abbruch: der Lauf muss seine
            # Abschlusszeile schreiben, sonst wartet ein offener Strom ewig.
            await _run_assistant_chat(session, household_id, task_id, task_input)
        elif await _cancel_requested(session, household_id, task_id):
            await _finish(session, household_id, task_id, "cancelled", "Aufgabe wurde abgebrochen.")
        elif task.task_type == "category_suggestions":
            await _run_category(session, household_id, task_id)
        elif task.task_type == "transaction_classification":
            await _run_classification(
                session,
                household_id,
                task_id,
                (
                    UUID(str(task.classification_run_id))
                    if task.classification_run_id is not None
                    else None
                ),
            )
        elif task.task_type == "contract_duplicate_crosscheck":
            await _run_contract_duplicates(session, household_id, task_id, task_input)
        elif task.task_type == "contract_transaction_check":
            await _run_contract_transactions(session, household_id, task_id, task_input)
        elif task.task_type == "project_cost_estimate":
            await _run_project_cost_estimate(session, household_id, task_id, task_input)
        elif task.task_type == "health_document_extraction":
            await _run_health_document_extraction(session, household_id, task_id, task_input)
        else:
            raise RuntimeError(f"Unbekannter KI-Aufgabentyp: {task.task_type}")
    except Exception as exc:
        await session.rollback()
        message = _error_message(exc)
        await _finish(
            session,
            household_id,
            task_id,
            "failed",
            "Die KI-Aufgabe ist fehlgeschlagen.",
            error_message=message,
        )


async def drain_household_queue(session: AsyncSession, household_id: UUID) -> None:
    processed_any = False
    for attempt in range(_VISIBILITY_ATTEMPTS):
        claim_state, task = await _claim_next(session, household_id)
        if claim_state == "busy":
            return
        if task is not None:
            processed_any = True
            await _announce_queue(session, household_id)
            await _execute(session, household_id, task)
            # awaiting_confirmation ist kein Queue-Blocker: unmittelbar die
            # naechste Aufgabe nehmen, bis fuer den Haushalt nichts mehr wartet.
            continue
        if processed_any or attempt == _VISIBILITY_ATTEMPTS - 1:
            return
        await session.rollback()
        await asyncio.sleep(_VISIBILITY_DELAY_SECONDS)


async def recover_stale_tasks(session: AsyncSession) -> list[UUID]:
    """Gibt abgestuerzte Haushalte frei und liefert alle wartenden Haushalte."""
    stale = (
        await session.execute(
            text(
                "update ai_tasks set status = 'failed', current_stage = 'failed', "
                "status_message = 'Worker ohne Lebenszeichen', "
                "error_message = coalesce(error_message, "
                "'Der Verarbeitungsdienst hat seit ueber 30 Minuten kein "
                "Lebenszeichen gesendet.'), "
                "completed_at = now(), updated_at = now() where status = 'running' "
                "and coalesce(heartbeat_at, started_at, created_at) "
                "< now() - interval '30 minutes' "
                "returning id, household_id, classification_run_id"
            )
        )
    ).all()
    for row in stale:
        task_id = UUID(str(row.id))
        household_id = UUID(str(row.household_id))
        await _event(
            session,
            household_id,
            task_id,
            "Aufgabe wurde nach ausbleibendem Worker-Lebenszeichen beendet.",
            stage="failed",
            level="error",
        )
        if row.classification_run_id is not None:
            await session.execute(
                text(
                    "update classification_runs set status = 'failed', completed_at = now(), "
                    "cancel_requested = true, error_message = coalesce(error_message, "
                    "'Verknuepfte KI-Aufgabe ohne Worker-Lebenszeichen.') where id = :id "
                    "and household_id = :household_id and status in ('pending', 'running')"
                ),
                {"id": str(row.classification_run_id), "household_id": str(household_id)},
            )

    households = (
        await session.execute(
            text(
                "select distinct q.household_id from ai_tasks q where q.status = 'queued' "
                "and not exists (select 1 from ai_tasks r where r.household_id = q.household_id "
                "and r.status = 'running')"
            )
        )
    ).scalars()
    result = [UUID(str(value)) for value in households]
    await session.commit()
    await _tidy_assistant(session)
    return result


async def _announce_queue(session: AsyncSession, household_id: UUID) -> None:
    """KI-Feld: wartende Fragen erfahren ihren neuen Warteplatz (Stromzeile
    status queued), sobald eine Aufgabe des Haushalts startet."""
    from app.assistant.maintenance import announce_queue_positions

    try:
        await announce_queue_positions(session, household_id)
    except Exception:  # noqa: BLE001 - nur eine Anzeige; die Aufgabe laeuft trotzdem
        await session.rollback()
        logger.exception("Warteplaetze des KI-Felds nicht aktualisiert")


async def _tidy_assistant(session: AsyncSession) -> None:
    """KI-Feld: verwaiste Laeufe beenden (ihr Strom bekommt seine
    Abschlusszeile) und Chats jenseits der Aufbewahrung loeschen. Ein Fehler
    hier darf das Wecken der Warteschlangen nicht verhindern."""
    from app.assistant.maintenance import finish_orphaned_runs, purge_expired_chats

    try:
        await finish_orphaned_runs(session)
        await purge_expired_chats(session)
    except Exception:  # noqa: BLE001 - naechster Lauf in einer Minute
        await session.rollback()
        logger.exception("Aufraeumen des KI-Felds ist fehlgeschlagen")
