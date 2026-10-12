"""Export-API fuer Vorhaben-Szenarien (Plan Abschnitt 10.3): XLSX-Arbeitsmappe
und CSV der Kostenpositionen. Eigener Router aus demselben Grund wie
`project_import.py`: routes/projects.py nicht weiter aufblaehen."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_household_session
from app.services import project_export_service, project_service

router = APIRouter(tags=["project-export"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/projects/{project_id}/scenarios/{scenario_id}/export.xlsx")
async def export_scenario_xlsx(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> Response:
    session, household_id = ctx
    content = await project_export_service.export_scenario_xlsx(
        session, household_id, project_id, scenario_id
    )
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="vorhaben-{scenario_id}.xlsx"'
        },
    )


@router.get("/projects/{project_id}/scenarios/{scenario_id}/export/cost-items.csv")
async def export_cost_items_csv(
    project_id: UUID, scenario_id: UUID, ctx: HouseholdSession
) -> Response:
    session, household_id = ctx
    await project_service.require_scenario(session, household_id, project_id, scenario_id)
    content = await project_export_service.export_cost_items_csv(
        session, household_id, scenario_id
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="kostenpositionen-{scenario_id}.csv"'
        },
    )
