"""Personen des Haushalts (Liquiditaetsplan, Stufe 6).

Bewusst schmal: anlegen, umbenennen, sortieren, loeschen. Personen sind kein
eigenes Fachobjekt mit Historie, sondern die Gruppierungsachse der Einnahmen -
alles darueber hinaus gehoert an den Vertrag.

Kein `If-Match` wie bei Vertraegen und Kategorien: `household_people` fuehrt
keine `version`, und bei drei Feldern ohne fachliche Folgeaenderungen waere
eine Konfliktbehandlung mehr Bedienlast als Schutz.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session, require_household_editor
from app.schemas.person import Person, PersonCreate, PersonUpdate
from app.services import person_service

router = APIRouter(prefix="/people", tags=["people"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]


@router.get("", response_model=list[Person])
async def list_people(
    ctx: HouseholdSession,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[Person]:
    """Standardmaessig nur aktive Personen. `include_inactive=true` liefert
    auch geloeschte - Vertraege behalten deren `person_id`, und ohne den Namen
    stuende die Einnahme in der Anzeige faelschlich unter "Ohne Person"."""
    session, household_id = ctx
    return await person_service.list_people(
        session, household_id, include_inactive=include_inactive
    )


@router.post("", response_model=Person, status_code=status.HTTP_201_CREATED)
async def create_person(payload: PersonCreate, ctx: HouseholdEditorSession) -> Person:
    session, household_id = ctx
    return await person_service.create_person(session, household_id, payload=payload)


@router.patch("/{person_id}", response_model=Person)
async def update_person(
    person_id: UUID, payload: PersonUpdate, ctx: HouseholdEditorSession
) -> Person:
    session, household_id = ctx
    return await person_service.update_person(
        session, household_id, person_id, payload=payload
    )


@router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(person_id: UUID, ctx: HouseholdEditorSession) -> None:
    """Soft-Delete ueber `is_active = false`: die Zuordnungen an den Vertraegen
    bleiben bestehen, und der Name wird fuer eine neue Person wieder frei."""
    session, household_id = ctx
    await person_service.deactivate_person(session, household_id, person_id)
