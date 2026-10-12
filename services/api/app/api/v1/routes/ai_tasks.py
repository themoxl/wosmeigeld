"""Persistente, pro Haushalt strikt serielle KI-Aufgaben."""

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.schemas.ai_task import AITask, AITaskCreate, AITaskDetail, AITaskEvent

worker_dir = Path(__file__).resolve().parents[5] / "worker"
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

from worker.actors import drain_ai_task_queue as drain_queue_actor  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/tasks", tags=["ai"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

_STALE_AFTER_MINUTES = 30
_TASK_COLUMNS = (
    "id, task_type, status, queue_order, request_id, input, result, result_summary, "
    "route_path, current_stage, progress_current, progress_total, status_message, "
    "model_name, error_message, cancel_requested, cancel_requested_at, attempt_count, "
    "category_operation_id, classification_run_id, created_at, started_at, completed_at, "
    "heartbeat_at, updated_at, "
    "(status = 'running' and coalesce(heartbeat_at, started_at, created_at) "
    "< now() - make_interval(mins => :stale_minutes)) as is_stale"
)


async def _select_task(
    db: AsyncSession,
    household_id: UUID,
    condition: str,
    params: dict[str, object] | None = None,
) -> Row[Any] | None:
    result = await db.execute(
        text(
            f"select {_TASK_COLUMNS} from ai_tasks "
            f"where household_id = :household_id and {condition} "
            "order by queue_order desc limit 1"
        ),
        {
            "household_id": str(household_id),
            "stale_minutes": _STALE_AFTER_MINUTES,
            **(params or {}),
        },
    )
    return result.first()


async def _event(
    db: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    message: str,
    *,
    stage: str | None = None,
    level: str = "info",
) -> None:
    await db.execute(
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


def _base_route(task_type: str, task_input: dict[str, Any] | None = None) -> str:
    if task_type == "category_suggestions":
        return "/kategorien"
    if task_type == "transaction_classification":
        return "/review"
    if task_type == "project_cost_estimate" and task_input:
        return f"/vorhaben/{task_input['project_id']}"
    if task_type == "health_document_extraction":
        return "/erstattungen"
    return "/fixkosten"


async def _create_classification_run(
    db: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    request_id: str | None,
) -> UUID:
    active = (
        await db.execute(
            text(
                "select id from classification_runs where household_id = :household_id "
                "and status in ('pending', 'running') limit 1"
            ),
            {"household_id": str(household_id)},
        )
    ).first()
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="classification_run_already_active",
        )

    count_row = (
        await db.execute(
            text(
                "select count(*), min(t.booked_date) from transactions t "
                "where t.household_id = :household_id and not t.is_archived "
                "and not t.is_transfer and (not exists (select 1 from transaction_splits s "
                "where s.transaction_id = t.id) or exists (select 1 from transaction_splits s "
                "where s.transaction_id = t.id and s.category_id is null))"
            ),
            {"household_id": str(household_id)},
        )
    ).one()
    return UUID(
        str(
            (
                await db.execute(
                    text(
                        "insert into classification_runs "
                        "(household_id, since_date, total_count, started_by, status, request_id) "
                        "values (:household_id, :since_date, :total_count, :started_by, "
                        "'pending', :request_id) returning id"
                    ),
                    {
                        "household_id": str(household_id),
                        "since_date": count_row[1] or date.today(),
                        "total_count": int(count_row[0]),
                        "started_by": str(user_id),
                        "request_id": f"task:{request_id}" if request_id else None,
                    },
                )
            ).scalar_one()
        )
    )


async def _enqueue(
    db: AsyncSession,
    household_id: UUID,
    user_id: UUID,
    payload: AITaskCreate,
    response: Response,
) -> AITask:
    if payload.request_id:
        known = await _select_task(
            db,
            household_id,
            "request_id = :request_id",
            {"request_id": payload.request_id},
        )
        if known is not None:
            response.status_code = status.HTTP_200_OK
            return AITask(**known._mapping)

    existing = await _select_task(
        db,
        household_id,
        "task_type = :task_type and status in ('queued', 'running')",
        {"task_type": payload.task_type},
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return AITask(**existing._mapping)

    try:
        async with db.begin_nested():
            classification_run_id: UUID | None = None
            if payload.task_type == "transaction_classification":
                classification_run_id = await _create_classification_run(
                    db, household_id, user_id, payload.request_id
                )

            task_id = UUID(
                str(
                    (
                        await db.execute(
                            text(
                                "insert into ai_tasks "
                                "(household_id, task_type, request_id, input, route_path, "
                                "classification_run_id, created_by, status_message) values "
                                "(:household_id, :task_type, :request_id, cast(:input as jsonb), "
                                ":route_path, :classification_run_id, :created_by, "
                                "'Wartet auf die Verarbeitung') returning id"
                            ),
                            {
                                "household_id": str(household_id),
                                "task_type": payload.task_type,
                                "request_id": payload.request_id,
                                "input": json.dumps(payload.input),
                                "route_path": _base_route(payload.task_type, payload.input),
                                "classification_run_id": (
                                    str(classification_run_id)
                                    if classification_run_id is not None
                                    else None
                                ),
                                "created_by": str(user_id),
                            },
                        )
                    ).scalar_one()
                )
            )
            route_path = f"{_base_route(payload.task_type, payload.input)}?ai_task={task_id}"
            if payload.task_type == "contract_transaction_check":
                route_path += f"&contract_id={payload.input['contract_id']}"
            elif payload.task_type == "project_cost_estimate":
                route_path += (
                    f"&tab=kosten&scenario_id={payload.input['scenario_id']}"
                )
            await db.execute(
                text("update ai_tasks set route_path = :route_path where id = :id"),
                {"route_path": route_path, "id": str(task_id)},
            )
            await _event(
                db,
                household_id,
                task_id,
                "Aufgabe wurde in die Warteschlange aufgenommen.",
                stage="queued",
            )
    except IntegrityError:
        existing = await _select_task(
            db,
            household_id,
            "task_type = :task_type and status in ('queued', 'running')",
            {"task_type": payload.task_type},
        )
        if existing is None:
            raise
        response.status_code = status.HTTP_200_OK
        return AITask(**existing._mapping)

    try:
        drain_queue_actor.send(str(household_id))
    except Exception as exc:
        logger.exception("KI-Warteschlange konnte nicht geweckt werden")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Die KI-Aufgabe konnte nicht eingereiht werden: Redis oder der "
                "Verarbeitungsdienst ist nicht erreichbar."
            ),
        ) from exc

    row = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    assert row is not None
    return AITask(**row._mapping)


@router.post("", response_model=AITask, status_code=201)
async def create_ai_task(
    payload: AITaskCreate,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> AITask:
    db, household_id = session_ctx
    return await _enqueue(db, household_id, user.id, payload, response)


@router.get("", response_model=list[AITask])
async def list_ai_tasks(
    session_ctx: HouseholdSession,
    task_status: Annotated[Literal["active", "all"], Query(alias="status")] = "all",
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[AITask]:
    db, household_id = session_ctx
    condition = (
        "status in ('queued', 'running', 'awaiting_confirmation')"
        if task_status == "active"
        else "true"
    )
    rows = await db.execute(
        text(
            f"select {_TASK_COLUMNS} from ai_tasks "
            f"where household_id = :household_id and {condition} "
            "order by case status when 'running' then 0 when 'queued' then 1 "
            "when 'awaiting_confirmation' then 2 else 3 end, "
            "case when status = 'queued' then queue_order end asc, created_at desc "
            "limit :limit offset :offset"
        ),
        {
            "household_id": str(household_id),
            "stale_minutes": _STALE_AFTER_MINUTES,
            "limit": limit,
            "offset": offset,
        },
    )
    return [AITask(**row._mapping) for row in rows]


@router.get("/{task_id}", response_model=AITaskDetail)
async def get_ai_task(task_id: UUID, session_ctx: HouseholdSession) -> AITaskDetail:
    db, household_id = session_ctx
    row = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_task_not_found")
    events = await db.execute(
        text(
            "select id, level, stage, message, details, created_at from ai_task_events "
            "where household_id = :household_id and task_id = :task_id "
            "order by created_at, id"
        ),
        {"household_id": str(household_id), "task_id": str(task_id)},
    )
    return AITaskDetail(
        **row._mapping,
        events=[AITaskEvent(**event._mapping) for event in events],
    )


async def _end_task_now(
    db: AsyncSession,
    household_id: UUID,
    task_id: UUID,
    classification_run_id: object | None,
    status_message: str,
    event_message: str,
    *,
    level: str = "info",
) -> None:
    """Beendet eine Aufgabe sofort als 'cancelled'.

    Der Worker schreibt ausschliesslich mit `and status = 'running'`; sobald
    diese Zeile 'cancelled' ist, laufen seine Fortschritts- und Abschluss-
    Updates ins Leere. Ein noch offener Modellaufruf laeuft zwar zu Ende, kann
    das Ergebnis aber nicht mehr ueberschreiben - und die Warteschlange des
    Haushalts ist sofort wieder frei."""
    await db.execute(
        text(
            "update ai_tasks set status = 'cancelled', cancel_requested = true, "
            "cancel_requested_at = coalesce(cancel_requested_at, now()), "
            "current_stage = 'cancelled', status_message = :message, "
            "completed_at = now(), updated_at = now() "
            "where id = :id and household_id = :household_id"
        ),
        {"id": str(task_id), "household_id": str(household_id), "message": status_message},
    )
    if classification_run_id:
        # Der verknuepfte Lauf muss mitgehen: bliebe er aktiv, blockierte
        # classification_runs_one_active_idx jeden Neustart, und der Worker
        # zaehlte weiter Buchungen zu einer Aufgabe, die es nicht mehr gibt.
        await db.execute(
            text(
                "update classification_runs set status = 'cancelled', "
                "cancel_requested = true, completed_at = now(), "
                "error_message = coalesce(error_message, 'Vom Nutzer abgebrochen.') "
                "where id = :id and household_id = :household_id "
                "and status in ('pending', 'running')"
            ),
            {"id": str(classification_run_id), "household_id": str(household_id)},
        )
    await _event(db, household_id, task_id, event_message, stage="cancelled", level=level)


@router.post("/{task_id}/cancel", response_model=AITask)
async def cancel_ai_task(
    task_id: UUID,
    session_ctx: HouseholdEditorSession,
    force: Annotated[bool, Query()] = False,
) -> AITask:
    """Bricht eine Aufgabe ab - regulaer oder, mit `force`, sofort.

    Ohne `force` bekommt eine laufende Aufgabe nur die Bitte gesetzt; der
    Worker beendet sie zwischen zwei Buchungen, damit kein halber Schreib-
    vorgang zurueckbleibt. Ein einzelner Modellaufruf darf allerdings so lange
    dauern wie das Zeitbudget je Anfrage (Einstellung request_timeout_seconds)
    - fuer den Nutzer sah der Abbruch
    deshalb wie ein Nichtereignis aus. `force` ist die Notbremse dagegen: sie
    beendet die Zeile sofort. Sie setzt eine bereits gestellte Bitte voraus
    (oder eine Aufgabe ohne Lebenszeichen), damit die schonende Variante der
    Normalfall bleibt."""
    db, household_id = session_ctx
    row = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_task_not_found")

    if row.status == "queued":
        await _end_task_now(
            db,
            household_id,
            task_id,
            row.classification_run_id,
            "Vom Nutzer abgebrochen",
            "Aufgabe wurde abgebrochen.",
        )
    elif row.status == "running":
        if force and not (row.cancel_requested or row.is_stale):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="ai_task_cancel_not_requested",
            )
        if force or row.is_stale:
            await _end_task_now(
                db,
                household_id,
                task_id,
                row.classification_run_id,
                "Sofort beendet",
                (
                    "Aufgabe wurde ohne Lebenszeichen des Verarbeitungsdienstes beendet."
                    if row.is_stale and not force
                    else "Aufgabe wurde auf Anweisung sofort beendet; ein noch offener "
                    "Modellaufruf wird verworfen."
                ),
                level="warning",
            )
        else:
            await db.execute(
                text(
                    "update ai_tasks set cancel_requested = true, "
                    "cancel_requested_at = coalesce(cancel_requested_at, now()), "
                    "status_message = "
                    "'Abbruch angefordert; der laufende Schritt wird beendet', "
                    "updated_at = now() where id = :id and household_id = :household_id"
                ),
                {"id": str(task_id), "household_id": str(household_id)},
            )
            if row.classification_run_id:
                await db.execute(
                    text(
                        "update classification_runs set cancel_requested = true "
                        "where id = :id and household_id = :household_id"
                    ),
                    {
                        "id": str(row.classification_run_id),
                        "household_id": str(household_id),
                    },
                )
            await _event(
                db,
                household_id,
                task_id,
                "Abbruch angefordert; der aktuelle Modellschritt wird noch sauber beendet.",
                stage=row.current_stage,
                level="warning",
            )

    updated = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    assert updated is not None
    return AITask(**updated._mapping)


@router.post("/{task_id}/complete", response_model=AITask)
async def complete_ai_task(task_id: UUID, session_ctx: HouseholdEditorSession) -> AITask:
    """Markiert ein geprueftes Ergebnis als erledigt; schreibt keine Fachdaten."""
    db, household_id = session_ctx
    row = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_task_not_found")
    if row.status == "awaiting_confirmation":
        await db.execute(
            text(
                "update ai_tasks set status = 'completed', status_message = 'Erledigt', "
                "completed_at = coalesce(completed_at, now()), updated_at = now() "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(task_id), "household_id": str(household_id)},
        )
        await _event(
            db,
            household_id,
            task_id,
            "Ergebnis wurde als erledigt markiert.",
            stage="completed",
        )
    updated = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    assert updated is not None
    return AITask(**updated._mapping)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_task(task_id: UUID, session_ctx: HouseholdEditorSession) -> Response:
    """Loescht einen abgeschlossenen Verlaufseintrag dauerhaft.

    Nur fuer 'completed', 'failed' und 'cancelled' - eine noch aktive Aufgabe
    (queued/running) muss erst abgebrochen werden, sonst verschwaende sie aus
    der Liste, waehrend der Worker noch an ihr schreibt."""
    db, household_id = session_ctx
    row = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_task_not_found")
    if row.status in ("queued", "running"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ai_task_not_deletable")
    await db.execute(
        text("delete from ai_tasks where id = :id and household_id = :household_id"),
        {"id": str(task_id), "household_id": str(household_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{task_id}/retry", response_model=AITask, status_code=201)
async def retry_ai_task(
    task_id: UUID,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> AITask:
    db, household_id = session_ctx
    row = await _select_task(db, household_id, "id = :id", {"id": str(task_id)})
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_task_not_found")
    if row.status not in ("failed", "cancelled") or row.task_type == "assistant_chat":
        # Eine KI-Frage wiederholt POST /v1/assistant/runs/{id}/retry (neue
        # Antwortnachricht zur selben Frage), nicht die Warteschlange.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ai_task_not_retryable")
    return await _enqueue(
        db,
        household_id,
        user.id,
        AITaskCreate(
            task_type=row.task_type,
            request_id=f"retry:{uuid4()}",
            input=dict(row.input),
        ),
        response,
    )
