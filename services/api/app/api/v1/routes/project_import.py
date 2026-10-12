"""Kostenplanimport-API: Preview/Apply (Plan 10.2) und Mappingprofile (Plan
10.1). Eigener Router statt Erweiterung von routes/projects.py, um diese
grosse, haeufig angefasste Datei nicht weiter aufzublaehen."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_household_session, require_household_editor
from app.auth.jwt import AuthenticatedUser
from app.schemas.project_import import (
    CostImportApplyResult,
    CostImportMappingProfile,
    CostImportMappingProfileInput,
    CostImportPreviewResponse,
    CostImportRowResolution,
)
from app.services import project_cost_import_service

router = APIRouter(tags=["project-cost-import"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def _parse_mapping(mapping_json: str) -> dict[str, str]:
    try:
        parsed = json.loads(mapping_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid_mapping_json") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        raise HTTPException(status_code=400, detail="invalid_mapping_json")
    return parsed


def _parse_resolutions(resolutions_json: str) -> list[CostImportRowResolution]:
    try:
        raw = json.loads(resolutions_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid_resolutions_json") from exc
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="invalid_resolutions_json")
    try:
        return [CostImportRowResolution(**item) for item in raw]
    except (ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="invalid_resolutions_json") from exc


@router.post(
    "/projects/{project_id}/scenarios/{scenario_id}/cost-import/preview",
    response_model=CostImportPreviewResponse,
)
async def preview_cost_import(
    project_id: UUID,
    scenario_id: UUID,
    ctx: HouseholdEditorSession,
    file: UploadFile = File(...),  # noqa: B008
    mapping_json: str = Form(...),  # noqa: B008
    sheet_name: str | None = Form(default=None),  # noqa: B008
    header_row: int = Form(default=1),  # noqa: B008
) -> CostImportPreviewResponse:
    session, household_id = ctx
    contents = await file.read()
    mapping = _parse_mapping(mapping_json)
    return await project_cost_import_service.build_preview(
        session,
        household_id,
        project_id,
        scenario_id,
        file_bytes=contents,
        filename=file.filename or "import",
        column_mapping=mapping,
        sheet_name=sheet_name,
        header_row=header_row,
    )


@router.post(
    "/projects/{project_id}/scenarios/{scenario_id}/cost-import/apply",
    response_model=CostImportApplyResult,
)
async def apply_cost_import(
    project_id: UUID,
    scenario_id: UUID,
    ctx: HouseholdEditorSession,
    user: CurrentUser,
    file: UploadFile = File(...),  # noqa: B008
    mapping_json: str = Form(...),  # noqa: B008
    sheet_name: str | None = Form(default=None),  # noqa: B008
    header_row: int = Form(default=1),  # noqa: B008
    resolutions_json: str = Form(...),  # noqa: B008
    request_id: str = Form(...),  # noqa: B008
    preview_fingerprint: str = Form(...),  # noqa: B008
) -> CostImportApplyResult:
    session, household_id = ctx
    contents = await file.read()
    mapping = _parse_mapping(mapping_json)
    resolutions = _parse_resolutions(resolutions_json)
    return await project_cost_import_service.apply_import(
        session,
        household_id,
        project_id,
        scenario_id,
        user.id,
        file_bytes=contents,
        filename=file.filename or "import",
        column_mapping=mapping,
        sheet_name=sheet_name,
        header_row=header_row,
        resolutions=resolutions,
        request_id=request_id,
        preview_fingerprint=preview_fingerprint,
    )


@router.get("/cost-import-profiles", response_model=list[CostImportMappingProfile])
async def list_cost_import_profiles(ctx: HouseholdSession) -> list[CostImportMappingProfile]:
    session, household_id = ctx
    return await project_cost_import_service.list_mapping_profiles(session, household_id)


@router.post("/cost-import-profiles", response_model=CostImportMappingProfile, status_code=201)
async def create_cost_import_profile(
    payload: CostImportMappingProfileInput, ctx: HouseholdEditorSession
) -> CostImportMappingProfile:
    session, household_id = ctx
    return await project_cost_import_service.create_mapping_profile(
        session, household_id, payload
    )


@router.delete("/cost-import-profiles/{profile_id}", status_code=204)
async def delete_cost_import_profile(profile_id: UUID, ctx: HouseholdEditorSession) -> None:
    session, household_id = ctx
    await project_cost_import_service.delete_mapping_profile(session, household_id, profile_id)
