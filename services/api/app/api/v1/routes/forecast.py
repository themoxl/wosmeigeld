"""HTTP-API der Liquiditaetsvorschau
(docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 7).

Die Vorschau ist eine reine Ableitung und speichert nichts (Leitsatz 1);
geschrieben wird ausschliesslich `forecast_settings`. Die Grenzen fuer
Horizont und Tagesfenster stehen als Konstanten im Rechenkern, nicht als
Zahlen hier - die `Query`-Schranken bilden sie nur ab.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.schemas.forecast import (
    ForecastDayGroup,
    ForecastGranularity,
    ForecastResponse,
    ForecastSettings,
    ForecastSettingsInput,
    ForecastSimulationRequest,
    ForecastSimulationResponse,
)
from app.services import household_forecast_service

router = APIRouter(prefix="/forecast", tags=["forecast"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Nur die Einstellungen sind schreibend - 403 statt stillem Nichtstun, wenn die
# Rolle nicht schreiben darf.
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get("", response_model=ForecastResponse)
async def get_forecast(
    session_ctx: HouseholdSession,
    horizon_months: int | None = Query(default=None, ge=1, le=60),
    granularity: ForecastGranularity = Query(default="day"),
    include_savings: bool = Query(default=False),
) -> ForecastResponse:
    db, household_id = session_ctx
    try:
        return await household_forecast_service.build_forecast(
            db,
            household_id,
            as_of=date.today(),
            horizon_months=horizon_months,
            granularity=granularity,
            include_savings=include_savings,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/upcoming", response_model=list[ForecastDayGroup])
async def get_upcoming(
    session_ctx: HouseholdSession,
    days: int = Query(default=90, ge=1, le=365),
) -> list[ForecastDayGroup]:
    db, household_id = session_ctx
    try:
        return await household_forecast_service.build_upcoming(
            db, household_id, as_of=date.today(), days=days
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/settings", response_model=ForecastSettings)
async def get_settings(session_ctx: HouseholdSession) -> ForecastSettings:
    db, household_id = session_ctx
    return await household_forecast_service.get_forecast_settings(db, household_id)


@router.put("/settings", response_model=ForecastSettings)
async def put_settings(
    payload: ForecastSettingsInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ForecastSettings:
    db, household_id = session_ctx
    try:
        return await household_forecast_service.update_forecast_settings(
            db, household_id, payload, updated_by=user.id
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/simulate", response_model=ForecastSimulationResponse)
async def simulate(
    payload: ForecastSimulationRequest,
    session_ctx: HouseholdSession,
) -> ForecastSimulationResponse:
    """Basis, Simulation und Differenz aus EINEM Aufruf und derselben Eingabe.

    Ausdruecklich lesend: ein Szenario aendert niemals Echtdaten (Leitsatz 3),
    und es wird nichts gespeichert (Abschnitt 6.1).
    """
    db, household_id = session_ctx
    try:
        return await household_forecast_service.simulate_forecast(
            db, household_id, as_of=date.today(), payload=payload
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
