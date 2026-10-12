"""HTTP-API der gespeicherten Haushaltsszenarien
(docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 7).

`GET /scenarios/{id}/forecast` liefert absichtlich dieselbe Form wie
`POST /forecast/simulate`. Das ist keine Bequemlichkeit, sondern Leitsatz 2:
es gibt genau einen Rechenkern, und ein gespeichertes Szenario ist nichts
anderes als eine gespeicherte Aenderungsliste. Ein eigener Antworttyp haette
die Oberflaeche gezwungen, zweimal dieselbe Auswertung zu schreiben.

Nebenlaeufigkeit wie bei den Vertraegen: `PATCH` verlangt `If-Match`
(428 ohne Header, 409 bei veraltetem Stand) und liefert die neue Version als
`ETag`. `PUT /{id}/changes` ersetzt die Liste als Ganzes und hebt die Version
mit - ein Client mit altem ETag soll nicht glauben, seine Zeilen stuenden noch.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.core.concurrency import entity_etag, parse_if_match
from app.schemas.forecast import ForecastSimulationResponse
from app.schemas.scenario import (
    HouseholdScenario,
    HouseholdScenarioChangesInput,
    HouseholdScenarioCreate,
    HouseholdScenarioDetail,
    HouseholdScenarioUpdate,
    ScenarioComparisonResponse,
)
from app.services import household_scenario_service

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]


def _parse_ids(raw: str) -> list[UUID]:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    try:
        return [UUID(part) for part in parts]
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids muss eine kommagetrennte Liste von Szenario-IDs sein.",
        ) from err


# ---------------------------------------------------------------------------
# Auflisten, Anlegen
# ---------------------------------------------------------------------------


@router.get("", response_model=list[HouseholdScenario])
async def list_scenarios(ctx: HouseholdSession) -> list[HouseholdScenario]:
    session, household_id = ctx
    return await household_scenario_service.list_scenarios(session, household_id)


@router.post(
    "", response_model=HouseholdScenarioDetail, status_code=status.HTTP_201_CREATED
)
async def create_scenario(
    payload: HouseholdScenarioCreate,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> HouseholdScenarioDetail:
    session, household_id = ctx
    try:
        scenario = await household_scenario_service.create_scenario(
            session, household_id, payload, created_by=user.id
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


# ---------------------------------------------------------------------------
# Vergleich
#
# Steht VOR den Musterrouten: sonst versuchte FastAPI, "compare" als UUID zu
# lesen, und der Vergleich waere ein 422 auf den Pfadparameter.
# ---------------------------------------------------------------------------


@router.get("/compare", response_model=ScenarioComparisonResponse)
async def compare_scenarios(
    ctx: HouseholdSession,
    ids: Annotated[str, Query(min_length=1, max_length=400)],
    include_savings: bool = Query(default=False),
) -> ScenarioComparisonResponse:
    """Eine Basis, bis zu vier Szenarien - mehr Linien sind nicht mehr lesbar."""
    session, household_id = ctx
    try:
        return await household_scenario_service.compare_scenarios(
            session,
            household_id,
            _parse_ids(ids),
            as_of=date.today(),
            include_savings=include_savings,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err


# ---------------------------------------------------------------------------
# Einzelnes Szenario
# ---------------------------------------------------------------------------


@router.get("/{scenario_id}", response_model=HouseholdScenarioDetail)
async def get_scenario(
    scenario_id: UUID, ctx: HouseholdSession, response: Response
) -> HouseholdScenarioDetail:
    session, household_id = ctx
    scenario = await household_scenario_service.get_scenario(
        session, household_id, scenario_id
    )
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.patch("/{scenario_id}", response_model=HouseholdScenarioDetail)
async def update_scenario(
    scenario_id: UUID,
    payload: HouseholdScenarioUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> HouseholdScenarioDetail:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    try:
        scenario = await household_scenario_service.update_scenario(
            session, household_id, scenario_id, payload, expected_version=expected_version
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scenario(scenario_id: UUID, ctx: HouseholdEditorSession) -> None:
    session, household_id = ctx
    await household_scenario_service.delete_scenario(session, household_id, scenario_id)


@router.post(
    "/{scenario_id}/duplicate",
    response_model=HouseholdScenarioDetail,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_scenario(
    scenario_id: UUID,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> HouseholdScenarioDetail:
    """Kopie samt Zeilen - immer als Entwurf und immer mit Herkunft `manual`."""
    session, household_id = ctx
    scenario = await household_scenario_service.duplicate_scenario(
        session, household_id, scenario_id, created_by=user.id
    )
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.put("/{scenario_id}/changes", response_model=HouseholdScenarioDetail)
async def replace_changes(
    scenario_id: UUID,
    payload: HouseholdScenarioChangesInput,
    ctx: HouseholdEditorSession,
    response: Response,
) -> HouseholdScenarioDetail:
    """Ersetzt die Aenderungsliste vollstaendig (wie `PUT /budgets/.../allocations`)."""
    session, household_id = ctx
    try:
        scenario = await household_scenario_service.replace_changes(
            session, household_id, scenario_id, payload.changes
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    response.headers["ETag"] = entity_etag(scenario.version)
    return scenario


@router.get("/{scenario_id}/forecast", response_model=ForecastSimulationResponse)
async def get_scenario_forecast(
    scenario_id: UUID,
    ctx: HouseholdSession,
    include_savings: bool = Query(default=False),
) -> ForecastSimulationResponse:
    """Ausdruecklich lesend: ein Szenario aendert niemals Echtdaten (Leitsatz 3)."""
    session, household_id = ctx
    try:
        return await household_scenario_service.build_scenario_forecast(
            session,
            household_id,
            scenario_id,
            as_of=date.today(),
            include_savings=include_savings,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
