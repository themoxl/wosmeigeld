"""HTTP-API fuer das bestaetigte Ruecklagen-Ledger."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_household_session, require_household_editor
from app.auth.jwt import AuthenticatedUser
from app.schemas.reserves import (
    ReserveBatchResponse,
    ReserveDepositRequest,
    ReserveDistributionPreviewRequest,
    ReserveDistributionPreviewResponse,
    ReserveOverviewResponse,
    ReserveReallocationRequest,
    ReserveSkipRequest,
    ReserveWithdrawalRequest,
)
from app.services import reserve_service

router = APIRouter(prefix="/reserves", tags=["reserves"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get("/overview", response_model=ReserveOverviewResponse)
async def get_overview(
    session_ctx: HouseholdSession,
    year: int = Query(ge=2000, le=2200),
    month: int = Query(ge=1, le=12),
) -> ReserveOverviewResponse:
    db, household_id = session_ctx
    return await reserve_service.get_reserve_overview(
        db, household_id, year=year, month=month
    )


@router.post(
    "/deposits", response_model=ReserveBatchResponse, status_code=status.HTTP_201_CREATED
)
async def create_deposit(
    payload: ReserveDepositRequest,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ReserveBatchResponse:
    db, household_id = session_ctx
    return await reserve_service.create_deposit(
        db, household_id, payload, created_by=user.id
    )


@router.post(
    "/withdrawals", response_model=ReserveBatchResponse, status_code=status.HTTP_201_CREATED
)
async def create_withdrawal(
    payload: ReserveWithdrawalRequest,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ReserveBatchResponse:
    db, household_id = session_ctx
    return await reserve_service.create_withdrawal(
        db, household_id, payload, created_by=user.id
    )


@router.post(
    "/reallocations", response_model=ReserveBatchResponse, status_code=status.HTTP_201_CREATED
)
async def create_reallocation(
    payload: ReserveReallocationRequest,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> ReserveBatchResponse:
    db, household_id = session_ctx
    return await reserve_service.create_reallocation(
        db, household_id, payload, created_by=user.id
    )


@router.put("/month-decisions/skip", status_code=status.HTTP_204_NO_CONTENT)
async def skip_month(
    payload: ReserveSkipRequest, session_ctx: HouseholdEditorSession
) -> None:
    db, household_id = session_ctx
    await reserve_service.set_skip(
        db,
        household_id,
        pot_id=payload.pot_id,
        year=payload.year,
        month=payload.month,
    )


@router.delete("/month-decisions/skip", status_code=status.HTTP_204_NO_CONTENT)
async def undo_skip_month(
    payload: ReserveSkipRequest, session_ctx: HouseholdEditorSession
) -> None:
    db, household_id = session_ctx
    await reserve_service.clear_skip(
        db,
        household_id,
        pot_id=payload.pot_id,
        year=payload.year,
        month=payload.month,
    )


@router.post("/distribution-preview", response_model=ReserveDistributionPreviewResponse)
async def distribution_preview(
    payload: ReserveDistributionPreviewRequest,
    session_ctx: HouseholdSession,
) -> ReserveDistributionPreviewResponse:
    db, household_id = session_ctx
    return await reserve_service.preview_distribution(
        db,
        household_id,
        year=payload.year,
        month=payload.month,
        total_minor=payload.total_minor,
    )
