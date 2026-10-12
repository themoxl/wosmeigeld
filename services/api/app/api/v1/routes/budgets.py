"""API-Endpunkte fuer Budgetierung, Allokationen, Monatsabschluss und Matrix (Phase 5)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_session,
    require_household_editor,
)
from app.auth.jwt import AuthenticatedUser
from app.schemas.budgets import (
    AnnualMatrixResponse,
    BudgetSummary,
    BudgetUpdateInput,
    CloseMonthResponse,
)
from app.services.budget_service import (
    close_month,
    get_annual_matrix,
    get_budget_summary,
    update_budget_allocations,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Allokationen setzen und Monatsabschluss schreiben - 403 statt stillem
# Nichtstun, wenn die Rolle nicht schreiben darf.
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


@router.get("/{year}/matrix", response_model=AnnualMatrixResponse)
async def fetch_annual_matrix(
    year: int,
    session_ctx: HouseholdSession,
) -> AnnualMatrixResponse:
    if not (2000 <= year <= 2100):
        raise HTTPException(status_code=400, detail="Ungueltiges Jahr.")
    db, household_id = session_ctx
    return await get_annual_matrix(db, household_id, year)


@router.get("/{year}/{month}", response_model=BudgetSummary)
async def fetch_budget_summary(
    year: int,
    month: int,
    session_ctx: HouseholdSession,
) -> BudgetSummary:
    if not (2000 <= year <= 2100) or not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungueltiges Jahr oder Monat.")
    db, household_id = session_ctx
    return await get_budget_summary(db, household_id, year, month)


@router.put("/{year}/{month}/allocations", response_model=BudgetSummary)
async def set_budget_allocations(
    year: int,
    month: int,
    payload: BudgetUpdateInput,
    session_ctx: HouseholdEditorSession,
) -> BudgetSummary:
    if not (2000 <= year <= 2100) or not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungueltiges Jahr oder Monat.")
    db, household_id = session_ctx

    try:
        await update_budget_allocations(db, household_id, year, month, payload.allocations)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    return await get_budget_summary(db, household_id, year, month)


@router.post("/{year}/{month}/close", response_model=CloseMonthResponse)
async def trigger_close_month(
    year: int,
    month: int,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> CloseMonthResponse:
    if not (2000 <= year <= 2100) or not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungueltiges Jahr oder Monat.")
    db, household_id = session_ctx

    try:
        return await close_month(db, household_id, year, month, user.id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
