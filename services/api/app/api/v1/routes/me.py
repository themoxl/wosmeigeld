from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_db_session
from app.auth.jwt import AuthenticatedUser
from app.schemas.household import Membership
from app.schemas.profile import Me, ProfileUpdate

router = APIRouter(tags=["profile"])


async def _ensure_profile_row(user: AuthenticatedUser, session: AsyncSession) -> None:
    # Kein DB-Trigger auf auth.users (vermeidet Eingriffe ins auth-Schema):
    # das Profil wird beim ersten authentifizierten Aufruf angelegt.
    await session.execute(
        text("insert into profiles (user_id) values (:user_id) on conflict (user_id) do nothing"),
        {"user_id": str(user.id)},
    )


async def _load_me(user: AuthenticatedUser, session: AsyncSession) -> Me:
    profile_row = (
        await session.execute(
            text(
                "select user_id, display_name, language, timezone, currency, date_format "
                "from profiles where user_id = :user_id"
            ),
            {"user_id": str(user.id)},
        )
    ).one()

    households_result = await session.execute(
        text(
            "select h.id as household_id, h.name, hm.role from households h "
            "join household_members hm on hm.household_id = h.id "
            "where hm.user_id = :user_id and hm.status = 'active' "
            "order by h.created_at"
        ),
        {"user_id": str(user.id)},
    )
    households = [Membership(**row._mapping) for row in households_result]

    return Me(**dict(profile_row._mapping), households=households)


@router.get("/me", response_model=Me)
async def get_me(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Me:
    await _ensure_profile_row(user, session)
    return await _load_me(user, session)


@router.patch("/me", response_model=Me)
async def update_me(
    payload: ProfileUpdate,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Me:
    await _ensure_profile_row(user, session)
    if payload.date_format is not None:
        await session.execute(
            text("update profiles set date_format = :date_format where user_id = :user_id"),
            {"date_format": payload.date_format, "user_id": str(user.id)},
        )
    return await _load_me(user, session)
