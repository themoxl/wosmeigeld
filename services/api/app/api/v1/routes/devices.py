"""Geraeteregistrierung fuer Push-Benachrichtigungen (Phase 9).

Nutzer-skopiert statt haushaltsskopiert (siehe supabase/migrations/0001_schema.sql,
Tabelle device_push_tokens): ein Geraet gehoert zu einer Person, nicht zu
einem einzelnen aktiven Haushalts-Request-Kontext. Deshalb `get_db_session`
statt `get_household_session` - dasselbe Muster wie /v1/me.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_db_session
from app.auth.jwt import AuthenticatedUser
from app.schemas.device import PushTokenRegister, PushTokenResponse, PushTokenUnregister

router = APIRouter(tags=["devices"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.post("/devices/push-token", response_model=PushTokenResponse, status_code=201)
async def register_push_token(
    payload: PushTokenRegister,
    user: CurrentUser,
    session: DbSession,
) -> PushTokenResponse:
    # on conflict (expo_token): deckt Re-Login auf demselben Geraet (anderer
    # Nutzer) und Neuinstallation (zuvor invalidierter Token wird
    # reaktiviert) ab.
    res = await session.execute(
        text(
            "insert into device_push_tokens (user_id, expo_token, platform, device_name, "
            "is_valid, last_seen_at, updated_at) "
            "values (:user_id, :expo_token, :platform, :device_name, true, now(), now()) "
            "on conflict (expo_token) do update "
            "set user_id = excluded.user_id, "
            "    platform = excluded.platform, "
            "    device_name = excluded.device_name, "
            "    is_valid = true, "
            "    last_seen_at = now(), "
            "    updated_at = now() "
            "returning id, platform, is_valid"
        ),
        {
            "user_id": str(user.id),
            "expo_token": payload.expo_token,
            "platform": payload.platform,
            "device_name": payload.device_name,
        },
    )
    row = res.one()
    return PushTokenResponse(**row._mapping)


@router.delete("/devices/push-token", status_code=204)
async def unregister_push_token(
    payload: PushTokenUnregister,
    user: CurrentUser,
    session: DbSession,
) -> None:
    # Idempotent: 0 betroffene Zeilen (z. B. bereits entfernt) ist kein
    # Fehler, sondern der gewuenschte Endzustand.
    await session.execute(
        text(
            "delete from device_push_tokens where expo_token = :expo_token and user_id = :user_id"
        ),
        {"expo_token": payload.expo_token, "user_id": str(user.id)},
    )
