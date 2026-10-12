"""KI-Feld: Chat zu den eigenen Finanzen (docs/KI-FELD-SCHNITTSTELLE.md).

Chats sind privat fuer ihren Verfasser (404 fuer fremde), Betrachter duerfen
fragen (E6) - deshalb get_household_session, nicht require_household_editor.
Jede Frage ist eine Aufgabe der KI-Warteschlange (E4); ausgefuehrt wird sie
im Worker (app/assistant/runner.py), live verfolgt ueber den NDJSON-Strom.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant import service, store
from app.assistant.events import Event, EventBus, get_event_bus
from app.assistant.streaming import run_stream
from app.auth.dependencies import get_current_user, get_household_session
from app.auth.jwt import AuthenticatedUser
from app.core.db import get_session_factory
from app.schemas.assistant import (
    CancelResponse,
    ChatCreate,
    ChatDetail,
    ChatListItem,
    ChatUpdate,
    CompactRequest,
    CompactResponse,
    RetryRequest,
    RunLog,
    SendMessage,
    SendResponse,
)
from app.services.worker_dispatch import process_import_actor  # noqa: F401

logger = logging.getLogger(__name__)

# Die sys.path-Kopplung zu services/worker steht in
# app/services/worker_dispatch.py - der Import oben loest sie mit aus.
from worker.actors import drain_ai_task_queue as drain_queue_actor  # noqa: E402

router = APIRouter(prefix="/assistant", tags=["assistant"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
Bus = Annotated[EventBus, Depends(get_event_bus)]


def _http(error: service.AssistantError) -> HTTPException:
    if error.status_code == status.HTTP_404_NOT_FOUND:
        return HTTPException(status_code=404, detail="assistant_not_found")
    return HTTPException(
        status_code=error.status_code, detail={"code": error.code, "message": error.message}
    )


def _wake(household_id: UUID) -> None:
    """Weckt die Warteschlange des Haushalts (Muster aus routes/ai_tasks.py).
    Scheitert das, rollt die Anfrage zurueck - keine Frage ohne Verarbeitung."""
    try:
        drain_queue_actor.send(str(household_id))
    except Exception as exc:
        logger.exception("KI-Warteschlange konnte nicht geweckt werden")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Die KI-Frage konnte nicht eingereiht werden: Redis oder der "
                "Verarbeitungsdienst ist nicht erreichbar."
            ),
        ) from exc


def _publish_later(
    background: BackgroundTasks, bus: EventBus, items: list[tuple[str, Event]]
) -> None:
    """Erst nach dem Commit der Anfrage veroeffentlichen (Regel 5): die
    Hintergrundaufgabe laeuft nach dem Ende der Abhaengigkeit."""

    async def publish() -> None:
        for channel, event in items:
            try:
                await bus.publish(channel, event)
            except Exception:  # noqa: BLE001 - Strom und Worker pruefen auch die Datenbank
                logger.warning("Ereignis fuer %s nicht veroeffentlicht", channel)

    if items:
        background.add_task(publish)


@router.get("/chats", response_model=list[ChatListItem])
async def list_chats(session_ctx: HouseholdSession, user: CurrentUser) -> list[ChatListItem]:
    db, household_id = session_ctx
    return [ChatListItem(**item) for item in await store.list_chats(db, household_id, user.id)]


@router.post("/chats", response_model=ChatDetail, status_code=status.HTTP_201_CREATED)
async def create_chat(
    payload: ChatCreate, session_ctx: HouseholdSession, user: CurrentUser
) -> ChatDetail:
    db, household_id = session_ctx
    chat = await service.create_chat(db, household_id, user.id, payload.title)
    return ChatDetail(**await store.chat_detail(db, chat))


@router.get("/chats/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: UUID, session_ctx: HouseholdSession, user: CurrentUser) -> ChatDetail:
    db, household_id = session_ctx
    try:
        chat = await service.require_chat(db, household_id, user.id, chat_id)
    except service.AssistantError as exc:
        raise _http(exc) from exc
    return ChatDetail(**await store.chat_detail(db, chat))


@router.patch("/chats/{chat_id}", response_model=ChatDetail)
async def update_chat(
    chat_id: UUID, payload: ChatUpdate, session_ctx: HouseholdSession, user: CurrentUser
) -> ChatDetail:
    db, household_id = session_ctx
    try:
        chat = await service.require_chat(db, household_id, user.id, chat_id, lock=True)
    except service.AssistantError as exc:
        raise _http(exc) from exc
    changes = {name: getattr(payload, name) for name in payload.model_fields_set}
    chat = await service.update_chat(db, chat, changes)
    return ChatDetail(**await store.chat_detail(db, chat))


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: UUID,
    session_ctx: HouseholdSession,
    user: CurrentUser,
    background: BackgroundTasks,
    bus: Bus,
) -> Response:
    db, household_id = session_ctx
    try:
        outcome = await service.delete_chat(db, household_id, user.id, chat_id)
    except service.AssistantError as exc:
        raise _http(exc) from exc
    _publish_later(background, bus, outcome.publish)
    return Response(status_code=status.HTTP_204_NO_CONTENT, background=background)


@router.post(
    "/chats/{chat_id}/messages", response_model=SendResponse, status_code=status.HTTP_201_CREATED
)
async def send_message(
    chat_id: UUID,
    payload: SendMessage,
    session_ctx: HouseholdSession,
    user: CurrentUser,
    response: Response,
) -> SendResponse:
    db, household_id = session_ctx
    try:
        outcome = await service.send_message(
            db,
            household_id,
            user.id,
            chat_id,
            content=payload.content,
            request_id=payload.request_id,
            reasoning_override=payload.reasoning_override,
            override_given="reasoning_override" in payload.model_fields_set,
        )
    except service.AssistantError as exc:
        raise _http(exc) from exc
    if outcome.wake:
        _wake(household_id)
    else:
        response.status_code = status.HTTP_200_OK
    return SendResponse(**outcome.body)


@router.post("/chats/{chat_id}/compact", response_model=CompactResponse)
async def compact_chat(
    chat_id: UUID, payload: CompactRequest, session_ctx: HouseholdSession, user: CurrentUser
) -> CompactResponse:
    db, household_id = session_ctx
    try:
        outcome = await service.compact_chat(
            db, household_id, user.id, chat_id, focus=payload.focus
        )
    except service.AssistantError as exc:
        raise _http(exc) from exc
    _wake(household_id)
    return CompactResponse(**outcome.body)


@router.get("/runs/{run_id}", response_model=RunLog)
async def get_run(run_id: UUID, session_ctx: HouseholdSession, user: CurrentUser) -> RunLog:
    db, household_id = session_ctx
    try:
        run = await service.require_run(db, household_id, user.id, run_id)
    except service.AssistantError as exc:
        raise _http(exc) from exc
    return RunLog(**await store.run_log(db, run))


@router.post("/runs/{run_id}/cancel", response_model=CancelResponse)
async def cancel_run(
    run_id: UUID,
    session_ctx: HouseholdSession,
    user: CurrentUser,
    background: BackgroundTasks,
    bus: Bus,
) -> CancelResponse:
    db, household_id = session_ctx
    try:
        outcome = await service.cancel_run(db, household_id, user.id, run_id)
    except service.AssistantError as exc:
        raise _http(exc) from exc
    _publish_later(background, bus, outcome.publish)
    return CancelResponse(**outcome.body)


@router.post("/runs/{run_id}/retry", response_model=SendResponse, status_code=201)
async def retry_run(
    run_id: UUID, payload: RetryRequest, session_ctx: HouseholdSession, user: CurrentUser
) -> SendResponse:
    db, household_id = session_ctx
    try:
        outcome = await service.retry_run(
            db,
            household_id,
            user.id,
            run_id,
            reasoning_override=payload.reasoning_override,
            override_given="reasoning_override" in payload.model_fields_set,
        )
    except service.AssistantError as exc:
        raise _http(exc) from exc
    _wake(household_id)
    return SendResponse(**outcome.body)


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: UUID,
    session_ctx: HouseholdSession,
    user: CurrentUser,
    bus: Bus,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """NDJSON-Strom. Mitgliedschaft (Abhaengigkeit) und Eigentum am Lauf
    werden VOR dem Strom geprueft; der Generator oeffnet eigene Sitzungen."""
    db, household_id = session_ctx
    try:
        await service.require_run(db, household_id, user.id, run_id)
    except service.AssistantError as exc:
        raise _http(exc) from exc
    return StreamingResponse(
        run_stream(
            run_id=run_id,
            household_id=household_id,
            user_id=user.id,
            after=after,
            bus=bus,
            session_factory=get_session_factory(),
        ),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
