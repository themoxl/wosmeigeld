"""Finanz-Empfehlungen.

Ein einziger Endpunkt: Der Bericht wird bei jedem Aufruf frisch gerechnet.
Bewusst kein persistierter Lauf wie bei der Klassifikation - hier entsteht
nichts, was uebernommen werden muesste, und ein gespeicherter Stand waere am
naechsten Buchungsimport bereits veraltet.

`with_ai` steuert nur die Formulierung. Ohne Modell ist der Bericht
vollstaendig; die Zahlen entstehen in jedem Fall aus SQL.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session
from app.schemas.insights import InsightReport
from app.services import insights_service

router = APIRouter(prefix="/insights", tags=["insights"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]


@router.get("", response_model=InsightReport)
async def get_insights(
    ctx: HouseholdSession,
    months: Annotated[
        int,
        Query(
            ge=insights_service.MIN_MONTHS,
            le=insights_service.MAX_MONTHS,
            description="Betrachtungszeitraum in vollständigen Monaten.",
        ),
    ] = insights_service.DEFAULT_MONTHS,
    with_ai: Annotated[
        bool,
        Query(
            description=(
                "Lässt ein lokales Modell zu jeder Empfehlung einen Handlungssatz "
                "formulieren. Dauert spürbar länger und ändert keine einzige Zahl."
            )
        ),
    ] = False,
    anchor_year: Annotated[
        int | None,
        Query(
            ge=2000,
            le=2100,
            description=(
                "Jahr des betrachteten Monats. Ohne Angabe (zusammen mit anchor_month) "
                "gilt der neueste vollständige Monat mit Buchungen."
            ),
        ),
    ] = None,
    anchor_month: Annotated[
        int | None,
        Query(ge=1, le=12, description="Monat (1-12) des betrachteten Monats."),
    ] = None,
) -> InsightReport:
    session, household_id = ctx
    return await insights_service.build_report(
        session,
        household_id,
        months=months,
        with_ai=with_ai,
        anchor_year=anchor_year,
        anchor_month=anchor_month,
    )
