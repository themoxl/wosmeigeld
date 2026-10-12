"""Tilgungsplan-API (Plan 7.3): auch ausserhalb von Projekten nutzbar
(Bestandsschulden teilen sich dasselbe Modell, Plan 4.8)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_household_session, require_household_editor
from app.auth.jwt import AuthenticatedUser
from app.core.concurrency import entity_etag, parse_if_match
from app.schemas.debt_plans import (
    DebtPlan,
    DebtPlanCreate,
    DebtPlanDetail,
    DebtPlanUpdate,
    DebtSchedule,
    Draw,
    DrawsReplace,
    Fee,
    FeesReplace,
    RatePeriod,
    RatePeriodsReplace,
    SpecialRepayment,
    SpecialRepaymentsReplace,
)
from app.services import debt_plan_service

router = APIRouter(prefix="/debt-plans", tags=["debt-plans"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
HouseholdEditorSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_editor)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
IfMatchHeader = Annotated[str | None, Header(alias="If-Match")]


@router.get("", response_model=list[DebtPlan])
async def list_debt_plans(
    ctx: HouseholdSession, project_financing_source_id: UUID | None = Query(default=None)
) -> list[DebtPlan]:
    session, household_id = ctx
    return await debt_plan_service.list_debt_plans(
        session, household_id, project_financing_source_id=project_financing_source_id
    )


@router.post("", response_model=DebtPlan, status_code=201)
async def create_debt_plan(
    payload: DebtPlanCreate, ctx: HouseholdEditorSession, user: CurrentUser, response: Response
) -> DebtPlan:
    session, household_id = ctx
    plan = await debt_plan_service.create_debt_plan(session, household_id, user.id, payload)
    response.headers["ETag"] = entity_etag(plan.version)
    return plan


@router.get("/{debt_plan_id}", response_model=DebtPlanDetail)
async def get_debt_plan(
    debt_plan_id: UUID, ctx: HouseholdSession, response: Response
) -> DebtPlanDetail:
    session, household_id = ctx
    plan = await debt_plan_service.require_debt_plan_detail(session, household_id, debt_plan_id)
    response.headers["ETag"] = entity_etag(plan.version)
    return plan


@router.patch("/{debt_plan_id}", response_model=DebtPlan)
async def update_debt_plan(
    debt_plan_id: UUID,
    payload: DebtPlanUpdate,
    ctx: HouseholdEditorSession,
    response: Response,
    if_match: IfMatchHeader = None,
) -> DebtPlan:
    session, household_id = ctx
    expected_version = parse_if_match(if_match)
    plan = await debt_plan_service.update_debt_plan(
        session, household_id, debt_plan_id, payload, expected_version
    )
    response.headers["ETag"] = entity_etag(plan.version)
    return plan


@router.delete("/{debt_plan_id}", status_code=204)
async def delete_debt_plan(
    debt_plan_id: UUID, ctx: HouseholdEditorSession, user: CurrentUser
) -> None:
    session, household_id = ctx
    await debt_plan_service.delete_debt_plan(session, household_id, debt_plan_id, user.id)


@router.put("/{debt_plan_id}/rate-periods", response_model=list[RatePeriod])
async def replace_rate_periods(
    debt_plan_id: UUID, payload: RatePeriodsReplace, ctx: HouseholdEditorSession
) -> list[RatePeriod]:
    session, household_id = ctx
    return await debt_plan_service.replace_rate_periods(
        session, household_id, debt_plan_id, payload.periods
    )


@router.put("/{debt_plan_id}/draws", response_model=list[Draw])
async def replace_draws(
    debt_plan_id: UUID, payload: DrawsReplace, ctx: HouseholdEditorSession
) -> list[Draw]:
    session, household_id = ctx
    return await debt_plan_service.replace_draws(session, household_id, debt_plan_id, payload.draws)


@router.put("/{debt_plan_id}/fees", response_model=list[Fee])
async def replace_fees(
    debt_plan_id: UUID, payload: FeesReplace, ctx: HouseholdEditorSession
) -> list[Fee]:
    session, household_id = ctx
    return await debt_plan_service.replace_fees(session, household_id, debt_plan_id, payload.fees)


@router.put("/{debt_plan_id}/special-repayments", response_model=list[SpecialRepayment])
async def replace_special_repayments(
    debt_plan_id: UUID, payload: SpecialRepaymentsReplace, ctx: HouseholdEditorSession
) -> list[SpecialRepayment]:
    session, household_id = ctx
    return await debt_plan_service.replace_special_repayments(
        session, household_id, debt_plan_id, payload.special_repayments
    )


@router.get("/{debt_plan_id}/schedule", response_model=DebtSchedule)
async def get_schedule(
    debt_plan_id: UUID,
    ctx: HouseholdSession,
    horizon_months: int = Query(default=360, ge=1, le=600),
) -> DebtSchedule:
    session, household_id = ctx
    return await debt_plan_service.calculate_schedule(
        session, household_id, debt_plan_id, horizon_months=horizon_months
    )
