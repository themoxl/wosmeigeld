"""Dynamischer Foerderkatalog: kuratierter Katalog plus eigener Katalog des
Haushalts (Plan 7.5, Import-Plan S4).

Der Katalog ist nicht mehr rein global: `funding_programs.household_id is null`
ist der mitgelieferte, unveraenderliche Bestand, ein gesetzter Wert gehoert dem
Haushalt und stammt aus dem Paket-/Dateiimport. Deshalb brauchen auch die
lesenden Endpunkte den Haushaltskontext.

Projektbezogene Endpunkte (Foerderprofil, Matchlauf, Kombinationspruefung,
Uebernahme) liegen in `routes/projects.py`, wie die uebrigen `/projects/{id}/
...`-Routen."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import HouseholdSessionDep, require_household_editor
from app.schemas.funding import (
    CatalogStatus,
    FundingExportRequest,
    FundingImportRequest,
    FundingImportResult,
    FundingPackageMeta,
    FundingPackagePayload,
    FundingPreviewResponse,
    FundingProgramDetail,
    FundingProgramSummary,
    FundingProgramTermsUpdate,
)
from app.services import funding_catalog_service, funding_import_export_service

router = APIRouter(prefix="/funding", tags=["funding"])

HouseholdEditorDep = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]


# Reihenfolge beachten: `/packages` muss vor `/programs/{program_id}` stehen,
# sonst frisst der Pfadparameter das Literal.
@router.get("/packages", response_model=list[FundingPackageMeta])
async def list_funding_packages(ctx: HouseholdSessionDep) -> list[FundingPackageMeta]:
    session, household_id = ctx
    return await funding_import_export_service.list_packages(session, household_id)


@router.get("/packages/{package_id}", response_model=FundingPackagePayload)
async def get_funding_package(package_id: str, ctx: HouseholdSessionDep) -> FundingPackagePayload:
    return funding_import_export_service.get_package(package_id)


@router.get("/programs", response_model=list[FundingProgramSummary])
async def list_funding_programs(
    ctx: HouseholdSessionDep,
    country_code: str = Query(default="DE"),
    region_code: str | None = Query(default=None),
    project_type: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
) -> list[FundingProgramSummary]:
    session, household_id = ctx
    return await funding_catalog_service.list_funding_programs(
        session,
        household_id,
        country_code,
        region_code,
        project_type,
        include_inactive=include_inactive,
    )


@router.get("/programs/{program_id}", response_model=FundingProgramDetail)
async def get_funding_program(program_id: UUID, ctx: HouseholdSessionDep) -> FundingProgramDetail:
    session, household_id = ctx
    return await funding_catalog_service.get_funding_program(session, household_id, program_id)


@router.put("/programs/{program_id}/terms", response_model=FundingProgramDetail)
async def update_funding_program_terms(
    program_id: UUID, payload: FundingProgramTermsUpdate, ctx: HouseholdEditorDep
) -> FundingProgramDetail:
    """Haengt einen manuell gepflegten Konditionsstand als neue Version an."""
    session, household_id = ctx
    return await funding_import_export_service.update_program_terms(
        session, household_id, program_id, payload
    )


@router.get("/catalog-status", response_model=CatalogStatus)
async def get_catalog_status(ctx: HouseholdSessionDep) -> CatalogStatus:
    session, household_id = ctx
    return await funding_catalog_service.get_catalog_status(session, household_id)


@router.post("/import/preview", response_model=FundingPreviewResponse)
async def preview_funding_import(
    payload: FundingImportRequest, ctx: HouseholdEditorDep
) -> FundingPreviewResponse:
    """Schreibt nie. Liefert je Programm, was der Import taete."""
    session, household_id = ctx
    return await funding_import_export_service.preview_import(session, household_id, payload)


@router.post("/import", response_model=FundingImportResult)
async def run_funding_import(
    payload: FundingImportRequest, ctx: HouseholdEditorDep
) -> FundingImportResult:
    session, household_id = ctx
    return await funding_import_export_service.execute_import(session, household_id, payload)


@router.post("/export", response_model=FundingPackagePayload)
async def export_funding_programs(
    payload: FundingExportRequest, ctx: HouseholdSessionDep
) -> FundingPackagePayload:
    session, household_id = ctx
    return await funding_import_export_service.export_programs(session, household_id, payload)


@router.post("/programs/{program_id}/deactivate", response_model=FundingProgramDetail)
async def deactivate_funding_program(
    program_id: UUID, ctx: HouseholdEditorDep
) -> FundingProgramDetail:
    """Kein hartes Loeschen.

    `funding_program_versions` haengt mit `on delete cascade` am Programm,
    `project_funding_matches` wiederum an der Version, und
    `project_financing_sources.funding_program_version_id` steht auf
    `on delete set null`. Ein DELETE raeumte damit die Foerdercheck-Historie
    aller Vorhaben ab und kappte den Programmbezug bereits uebernommener
    Finanzierungsquellen. `is_active = false` nimmt das Programm aus dem
    Foerdercheck, ohne Bestehendes anzutasten.
    """
    session, household_id = ctx
    return await funding_import_export_service.deactivate_program(session, household_id, program_id)
