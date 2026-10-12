import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import AuthenticatedUser, decode_supabase_jwt
from app.core.config import get_settings
from app.core.db import get_session_factory

# Schluessel, unter dem die Haushaltsrolle des aufrufenden Nutzers in
# Session.info mitgefuehrt wird. Bewusst dort und nicht als dritte Komponente
# des Rueckgabetupels von get_household_session(): dieses Tupel wird in
# saemtlichen Routen als (Session, Haushalts-ID) entpackt.
HOUSEHOLD_ROLE_KEY = "household_role"

# Rollen aus dem Typ household_role (supabase/migrations/0001_schema.sql), die
# schreiben duerfen - deckungsgleich mit can_edit_household() im Schema. Die
# Pruefung hier ist die erste Schicht (Architekturplan Abschnitt 5, Punkt 5),
# RLS bleibt unveraendert die zweite.
_EDITOR_ROLES = frozenset({"owner", "editor"})


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedUser:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token"
        )
    token = authorization.removeprefix("Bearer ").strip()
    return decode_supabase_jwt(token, get_settings())


async def _set_authenticated_role(session: AsyncSession, user: AuthenticatedUser) -> None:
    """Bildet nach, was PostgREST fuer jeden Request tut, damit auth.uid()/
    auth.role() in RLS-Policies korrekt aufloesen - siehe supabase/migrations
    und docs/SICHERHEIT.md. Die API verbindet sich als "postgres" (Superuser,
    BYPASSRLS); BYPASSRLS gilt aber nur fuer die Login-Rolle, nicht fuer den
    per SET ROLE aktiven current_user, daher greift RLS ab hier wirklich.
    """
    await session.execute(text("SET LOCAL ROLE authenticated"))
    claims = json.dumps({"sub": str(user.id), "role": "authenticated"})
    await session.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"), {"claims": claims}
    )


async def get_db_session(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AsyncIterator[AsyncSession]:
    """DB-Session ohne Haushaltskontext, fuer Endpunkte, die auf keinen
    einzelnen Haushalt beschraenkt sind (z. B. /v1/me, /v1/households)."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await _set_authenticated_role(session, user)
        yield session


async def get_household_session(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    x_household_id: Annotated[UUID | None, Header(alias="X-Household-Id")] = None,
) -> AsyncIterator[tuple[AsyncSession, UUID]]:
    """DB-Session mit Haushaltskontext. Prueft die Mitgliedschaft explizit auf
    API-Ebene (Architekturplan Abschnitt 5, Punkt 5: "API-Endpunkte pruefen
    Rolle und Operation zusaetzlich") und setzt danach den Haushaltskontext
    als Session-GUC, den RLS als zweite Schutzschicht erneut prueft."""
    if x_household_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="missing_household_header"
        )

    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await _set_authenticated_role(session, user)

        # 'select role' statt 'select 1': dieselbe Abfrage, aber die Rolle ist
        # danach bekannt und muss fuer require_household_editor()/
        # require_household_owner() nicht erneut geholt werden.
        membership = await session.execute(
            text(
                "select role from household_members "
                "where household_id = :household_id and user_id = :user_id and status = 'active'"
            ),
            {"household_id": str(x_household_id), "user_id": str(user.id)},
        )
        member_row = membership.first()
        if member_row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="not_a_household_member"
            )
        session.info[HOUSEHOLD_ROLE_KEY] = str(member_row.role)

        await session.execute(
            text("SELECT set_config('app.household_id', :household_id, true)"),
            {"household_id": str(x_household_id)},
        )
        yield session, x_household_id


HouseholdSessionDep = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]


async def get_household_role(ctx: HouseholdSessionDep) -> str:
    """Haushaltsrolle des aufrufenden Nutzers - ohne zusaetzliche Abfrage,
    get_household_session() hat sie bei der Mitgliedschaftspruefung bereits
    mitgelesen. FastAPI cacht die Abhaengigkeit innerhalb eines Requests, es
    entsteht also auch keine zweite Session."""
    session, _ = ctx
    role = session.info.get(HOUSEHOLD_ROLE_KEY)
    if not isinstance(role, str):
        # Kann nur eintreten, wenn die Rolle nachtraeglich aus der Session
        # entfernt wurde - dann lieber verweigern als raten.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="household_role_unknown"
        )
    return role


HouseholdRoleDep = Annotated[str, Depends(get_household_role)]


async def require_household_editor(
    ctx: HouseholdSessionDep, role: HouseholdRoleDep
) -> tuple[AsyncSession, UUID]:
    """Gate fuer alle schreibenden Endpunkte eines Haushalts.

    Ohne dieses Gate laesst RLS ein UPDATE/DELETE eines 'viewer' zwar
    wirkungslos, aber fehlerfrei durchlaufen - der Handler antwortet dann mit
    204/200 und das Frontend zeigt einen Erfolg an, den es nicht gab. Die
    Rueckgabe ist unveraendert (Session, Haushalts-ID), damit Routen das Tupel
    weiterhin gleich entpacken."""
    if role not in _EDITOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="household_role_readonly"
        )
    return ctx


async def require_household_owner(
    ctx: HouseholdSessionDep, role: HouseholdRoleDep
) -> tuple[AsyncSession, UUID]:
    """Gate fuer Endpunkte, die laut Schema nur der Owner ausfuehren darf
    (Haushalt umbenennen, Einladungen verwalten)."""
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="household_owner_required"
        )
    return ctx
