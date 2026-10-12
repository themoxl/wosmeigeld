import secrets
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_db_session,
    get_household_session,
    require_household_owner,
)
from app.auth.jwt import AuthenticatedUser
from app.schemas.household import (
    Household,
    HouseholdCreate,
    HouseholdInvite,
    HouseholdInviteCreate,
    HouseholdInvitePreview,
    HouseholdInviteRedeem,
    HouseholdMember,
    HouseholdUpdate,
)

router = APIRouter(prefix="/households", tags=["households"])

_HOUSEHOLD_COLUMNS = (
    "h.id, h.name, h.base_currency, h.timezone, h.budget_method, "
    "h.fiscal_year_start_month, h.status, hm.role"
)

_INVITE_COLUMNS = (
    "id, household_id, code, role, created_by, created_at, expires_at, "
    "revoked_at, used_by, used_at"
)

# Ohne 0/O/1/I/L (Verwechslungsgefahr bei manueller Eingabe).
_INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_INVITE_CODE_LENGTH = 8

_INVITE_ERROR_STATUS: dict[str, tuple[int, str]] = {
    "INVITE_NOT_FOUND": (status.HTTP_404_NOT_FOUND, "invite_not_found"),
    "INVITE_REVOKED": (status.HTTP_410_GONE, "invite_revoked"),
    "INVITE_ALREADY_USED": (status.HTTP_410_GONE, "invite_already_used"),
    "INVITE_EXPIRED": (status.HTTP_410_GONE, "invite_expired"),
}

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Umbenennen und Einladungsverwaltung sind laut Schema owner-only (RLS-Policy
# households_owner_update bzw. household_invites_*). Die Rolle steht dank
# get_household_session() bereits in der Session, das Gate kostet keine
# zusaetzliche Abfrage (siehe app/auth/dependencies.py).
HouseholdOwnerSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_owner)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _generate_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_CODE_ALPHABET) for _ in range(_INVITE_CODE_LENGTH))


def _raise_invite_http_error(exc: DBAPIError) -> NoReturn:
    """Uebersetzt die von preview_household_invite()/redeem_household_invite()
    (supabase/migrations/0001_schema.sql) geworfenen Postgres-Exceptions in
    passende HTTP-Antworten, anhand der dort gesetzten message-Marker."""
    message = str(exc.orig)
    for marker, (http_status, detail) in _INVITE_ERROR_STATUS.items():
        if marker in message:
            raise HTTPException(status_code=http_status, detail=detail) from exc
    raise exc


@router.get("", response_model=list[Household])
async def list_households(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[Household]:
    result = await session.execute(
        text(
            f"select {_HOUSEHOLD_COLUMNS} from households h "
            "join household_members hm on hm.household_id = h.id "
            "where hm.user_id = :user_id and hm.status = 'active' "
            "order by h.created_at"
        ),
        {"user_id": str(user.id)},
    )
    return [Household(**row._mapping) for row in result]


@router.post("", response_model=Household, status_code=201)
async def create_household(
    payload: HouseholdCreate,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Household:
    household_id = (
        await session.execute(text("select create_household(:name) as id"), {"name": payload.name})
    ).scalar_one()
    # Der freie Rücklagentopf gehört zur Haushaltsanlage. GET /reserves bleibt
    # dadurch strikt read-only und muss beim ersten Öffnen nichts nachschreiben.
    await session.execute(
        text("select set_config('app.household_id', :household_id, true)"),
        {"household_id": str(household_id)},
    )
    await session.execute(
        text(
            "insert into reserve_pots (household_id, kind) "
            "values (:household_id, 'free') on conflict do nothing"
        ),
        {"household_id": str(household_id)},
    )

    row = (
        await session.execute(
            text(
                f"select {_HOUSEHOLD_COLUMNS} from households h "
                "join household_members hm on hm.household_id = h.id "
                "where h.id = :household_id and hm.user_id = :user_id"
            ),
            {"household_id": household_id, "user_id": str(user.id)},
        )
    ).one()
    return Household(**row._mapping)


@router.patch("", response_model=Household)
async def rename_current_household(
    payload: HouseholdUpdate,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ctx: HouseholdOwnerSession,
) -> Household:
    """Benennt den per X-Household-Id gewaehlten Haushalt um. Nur fuer
    `owner` moeglich - das prueft schon require_household_owner(). Die
    Auswertung des UPDATE-Ergebnisses bleibt als zweite Schicht bestehen:
    RLS-Policy `households_owner_update` (supabase/migrations/0001_schema.sql)
    laesst das UPDATE sonst still 0 Zeilen betreffen (Abschnitt 5, Punkt 5:
    API prueft zusaetzlich)."""
    session, household_id = ctx
    updated = (
        await session.execute(
            text(
                "update households set name = :name, updated_at = now() "
                "where id = :household_id returning id"
            ),
            {"name": payload.name, "household_id": str(household_id)},
        )
    ).first()
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="only_owner_can_rename_household"
        )

    row = (
        await session.execute(
            text(
                f"select {_HOUSEHOLD_COLUMNS} from households h "
                "join household_members hm on hm.household_id = h.id "
                "where h.id = :household_id and hm.user_id = :user_id"
            ),
            {"household_id": str(household_id), "user_id": str(user.id)},
        )
    ).one()
    return Household(**row._mapping)


@router.get("/members", response_model=list[HouseholdMember])
async def list_household_members(ctx: HouseholdSession) -> list[HouseholdMember]:
    session, household_id = ctx
    rows = await session.execute(
        text(
            "select hm.user_id, hm.role, hm.status, hm.invited_at, hm.joined_at, "
            "p.display_name from household_members hm "
            "left join profiles p on p.user_id = hm.user_id "
            "where hm.household_id = :household_id and hm.status = 'active' "
            "order by hm.joined_at nulls first"
        ),
        {"household_id": str(household_id)},
    )
    return [HouseholdMember(**r._mapping) for r in rows]


@router.post("/invites", response_model=HouseholdInvite, status_code=201)
async def create_household_invite(
    payload: HouseholdInviteCreate, user: CurrentUser, ctx: HouseholdOwnerSession
) -> HouseholdInvite:
    session, household_id = ctx
    row = (
        await session.execute(
            text(
                "insert into household_invites "
                "(household_id, code, role, created_by, expires_at) "
                "values (:household_id, :code, :role, :created_by, now() + interval '7 days') "
                f"returning {_INVITE_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "code": _generate_invite_code(),
                "role": payload.role,
                "created_by": str(user.id),
            },
        )
    ).one()
    return HouseholdInvite(**row._mapping)


@router.get("/invites", response_model=list[HouseholdInvite])
async def list_household_invites(ctx: HouseholdOwnerSession) -> list[HouseholdInvite]:
    session, household_id = ctx
    rows = await session.execute(
        text(
            f"select {_INVITE_COLUMNS} from household_invites "
            "where household_id = :household_id order by created_at desc"
        ),
        {"household_id": str(household_id)},
    )
    return [HouseholdInvite(**r._mapping) for r in rows]


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_household_invite(invite_id: UUID, ctx: HouseholdOwnerSession) -> None:
    session, household_id = ctx
    updated = (
        await session.execute(
            text(
                "update household_invites set revoked_at = now() "
                "where id = :invite_id and household_id = :household_id "
                "and used_at is null and revoked_at is null returning id"
            ),
            {"invite_id": str(invite_id), "household_id": str(household_id)},
        )
    ).first()
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invite_not_found")


@router.get("/invites/redeem/{code}", response_model=HouseholdInvitePreview)
async def preview_household_invite(
    code: str, user: CurrentUser, session: DbSession
) -> HouseholdInvitePreview:
    try:
        row = (
            await session.execute(
                text("select * from preview_household_invite(:code)"), {"code": code}
            )
        ).one()
    except DBAPIError as exc:
        _raise_invite_http_error(exc)
    return HouseholdInvitePreview(**row._mapping)


@router.post("/invites/redeem", response_model=Household)
async def redeem_household_invite(
    payload: HouseholdInviteRedeem, user: CurrentUser, session: DbSession
) -> Household:
    try:
        household_id = (
            await session.execute(
                text("select redeem_household_invite(:code) as id"), {"code": payload.code}
            )
        ).scalar_one()
    except DBAPIError as exc:
        _raise_invite_http_error(exc)

    row = (
        await session.execute(
            text(
                f"select {_HOUSEHOLD_COLUMNS} from households h "
                "join household_members hm on hm.household_id = h.id "
                "where h.id = :household_id and hm.user_id = :user_id"
            ),
            {"household_id": household_id, "user_id": str(user.id)},
        )
    ).one()
    return Household(**row._mapping)
