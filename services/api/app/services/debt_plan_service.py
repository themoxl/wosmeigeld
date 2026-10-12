"""DB-Orchestrierung fuer Tilgungsplaene (Plan 7.3). Reine Rechenlogik lebt in
`debt_calculation_service.py`; dieser Dienst laedt/schreibt Zeilen und baut
die Bruecke zwischen `debt_plans` (+ Kindtabellen) und `DebtPlanInput`.
Gemeinsames Modell fuer Projektdarlehen (`project_financing_source_id`) und
Bestandsschulden (`asset_debt_id`) -- beide Pfade nutzen denselben Code."""

from typing import Any, Final
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.debt_plans import (
    DebtDrawMode,
    DebtPlan,
    DebtPlanCreate,
    DebtPlanDetail,
    DebtPlanUpdate,
    DebtSchedule,
    DebtScheduleRow,
    Draw,
    DrawInput,
    Fee,
    FeeInput,
    RatePeriod,
    RatePeriodInput,
    SpecialRepayment,
    SpecialRepaymentInput,
)
from app.services.audit_service import record_audit_event
from app.services.debt_calculation_service import (
    DebtCalculationError,
    DebtPlanInput,
    calculate_debt_schedule,
)
from app.services.debt_calculation_service import DrawInput as EngineDrawInput
from app.services.debt_calculation_service import FeeInput as EngineFeeInput
from app.services.debt_calculation_service import RatePeriodInput as EngineRatePeriodInput
from app.services.debt_calculation_service import (
    SpecialRepaymentInput as EngineSpecialRepaymentInput,
)

DEBT_PLAN_ENTITY_TYPE: Final = "debt_plan"
DEBT_PLAN_NOT_FOUND: Final = "debt_plan_not_found"

_DEBT_PLAN_COLUMNS = (
    "id, project_financing_source_id, asset_debt_id, name, creditor_label, currency, status, "
    "principal_limit_minor, opening_balance_minor, starts_on, payment_interval_months, "
    "amortization_mode, fixed_payment_minor, initial_repayment_rate_bps, target_term_months, "
    "repayment_free_months, payment_free_months, capitalize_interest_during_payment_free, "
    "commitment_free_months, commitment_rate_bps_per_month, recast_payment_on_rate_change, "
    "version, created_at, updated_at"
)


def _row_to_plan(row: Any) -> DebtPlan:
    data = dict(row._mapping)
    data["starts_on"] = data["starts_on"].isoformat()
    return DebtPlan(**data)


async def get_debt_plan_detail_by_source(
    session: AsyncSession, household_id: UUID, financing_source_id: UUID
) -> DebtPlanDetail | None:
    row = (
        await session.execute(
            text(
                f"select {_DEBT_PLAN_COLUMNS} from debt_plans "
                "where project_financing_source_id = :source_id and household_id = :household_id"
            ),
            {"source_id": str(financing_source_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        return None
    return await _row_to_plan_detail(session, row, draw_mode="immediate")


async def require_debt_plan_detail(
    session: AsyncSession,
    household_id: UUID,
    debt_plan_id: UUID,
    *,
    draw_mode: DebtDrawMode = "immediate",
) -> DebtPlanDetail:
    row = (
        await session.execute(
            text(
                f"select {_DEBT_PLAN_COLUMNS} from debt_plans "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(debt_plan_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DEBT_PLAN_NOT_FOUND)
    return await _row_to_plan_detail(session, row, draw_mode=draw_mode)


async def _row_to_plan_detail(
    session: AsyncSession, row: Any, *, draw_mode: DebtDrawMode
) -> DebtPlanDetail:
    plan = _row_to_plan(row)
    debt_plan_id = plan.id
    periods_result = await session.execute(
        text(
            "select id, starts_after_month, ends_after_month, nominal_rate_bps_annual, "
            "rate_kind, label from debt_rate_periods where debt_plan_id = :id "
            "order by starts_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    draws_result = await session.execute(
        text(
            "select id, occurs_after_month, amount_minor, status from debt_draws "
            "where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    fees_result = await session.execute(
        text(
            "select id, occurs_after_month, amount_minor, fee_kind, financed, label "
            "from debt_fees where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    special_result = await session.execute(
        text(
            "select id, occurs_after_month, amount_minor, status, label "
            "from debt_special_repayments where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    return DebtPlanDetail(
        **plan.model_dump(),
        draw_mode=draw_mode,
        rate_periods=[RatePeriod(**child._mapping) for child in periods_result],
        draws=[Draw(**child._mapping) for child in draws_result],
        fees=[Fee(**child._mapping) for child in fees_result],
        special_repayments=[SpecialRepayment(**child._mapping) for child in special_result],
    )


async def require_debt_plan(
    session: AsyncSession, household_id: UUID, debt_plan_id: UUID
) -> DebtPlan:
    row = (
        await session.execute(
            text(
                f"select {_DEBT_PLAN_COLUMNS} from debt_plans "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(debt_plan_id), "household_id": str(household_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DEBT_PLAN_NOT_FOUND)
    return _row_to_plan(row)


async def list_debt_plans(
    session: AsyncSession, household_id: UUID, *, project_financing_source_id: UUID | None = None
) -> list[DebtPlan]:
    conditions = ["household_id = :household_id"]
    params: dict[str, Any] = {"household_id": str(household_id)}
    if project_financing_source_id is not None:
        conditions.append("project_financing_source_id = :source_id")
        params["source_id"] = str(project_financing_source_id)
    result = await session.execute(
        text(
            f"select {_DEBT_PLAN_COLUMNS} from debt_plans where " + " and ".join(conditions) +
            " order by created_at"
        ),
        params,
    )
    return [_row_to_plan(row) for row in result]


async def create_debt_plan(
    session: AsyncSession, household_id: UUID, user_id: UUID, payload: DebtPlanCreate
) -> DebtPlan:
    row = (
        await session.execute(
            text(
                "insert into debt_plans "
                "(household_id, project_financing_source_id, asset_debt_id, name, creditor_label, "
                "currency, status, principal_limit_minor, opening_balance_minor, starts_on, "
                "payment_interval_months, amortization_mode, fixed_payment_minor, "
                "initial_repayment_rate_bps, target_term_months, repayment_free_months, "
                "payment_free_months, capitalize_interest_during_payment_free, "
                "commitment_free_months, commitment_rate_bps_per_month, "
                "recast_payment_on_rate_change) "
                "values (:household_id, :project_financing_source_id, :asset_debt_id, :name, "
                ":creditor_label, :currency, :status, :principal_limit_minor, "
                ":opening_balance_minor, :starts_on, :payment_interval_months, "
                ":amortization_mode, :fixed_payment_minor, :initial_repayment_rate_bps, "
                ":target_term_months, :repayment_free_months, :payment_free_months, "
                ":capitalize_interest_during_payment_free, :commitment_free_months, "
                ":commitment_rate_bps_per_month, :recast_payment_on_rate_change) "
                f"returning {_DEBT_PLAN_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "project_financing_source_id": (
                    str(payload.project_financing_source_id)
                    if payload.project_financing_source_id
                    else None
                ),
                "asset_debt_id": str(payload.asset_debt_id) if payload.asset_debt_id else None,
                "name": payload.name,
                "creditor_label": payload.creditor_label,
                "currency": payload.currency,
                "status": payload.status,
                "principal_limit_minor": payload.principal_limit_minor,
                "opening_balance_minor": payload.opening_balance_minor,
                "starts_on": payload.starts_on,
                "payment_interval_months": payload.payment_interval_months,
                "amortization_mode": payload.amortization_mode,
                "fixed_payment_minor": payload.fixed_payment_minor,
                "initial_repayment_rate_bps": payload.initial_repayment_rate_bps,
                "target_term_months": payload.target_term_months,
                "repayment_free_months": payload.repayment_free_months,
                "payment_free_months": payload.payment_free_months,
                "capitalize_interest_during_payment_free": (
                    payload.capitalize_interest_during_payment_free
                ),
                "commitment_free_months": payload.commitment_free_months,
                "commitment_rate_bps_per_month": payload.commitment_rate_bps_per_month,
                "recast_payment_on_rate_change": payload.recast_payment_on_rate_change,
            },
        )
    ).first()
    assert row is not None
    plan = _row_to_plan(row)
    await record_audit_event(
        session, household_id, user_id, DEBT_PLAN_ENTITY_TYPE, plan.id, "create",
        after=plan.model_dump(mode="json"),
    )
    return plan


def _assert_version(current_version: int, expected_version: int | None) -> None:
    from app.core.concurrency import raise_version_conflict

    if expected_version is not None and current_version != expected_version:
        raise_version_conflict(current_version)


async def update_debt_plan(
    session: AsyncSession,
    household_id: UUID,
    debt_plan_id: UUID,
    payload: DebtPlanUpdate,
    expected_version: int | None,
) -> DebtPlan:
    before = await require_debt_plan(session, household_id, debt_plan_id)
    _assert_version(before.version, expected_version)

    changes = payload.model_dump(exclude={"clear_fields"}, exclude_unset=True)
    for field in payload.clear_fields:
        changes[field] = None
    if changes:
        assignments = ", ".join(f"{key} = :{key}" for key in changes)
        await session.execute(
            text(f"update debt_plans set {assignments} where id = :id"),
            {**changes, "id": str(debt_plan_id)},
        )

    after = await require_debt_plan(session, household_id, debt_plan_id)
    await record_audit_event(
        session, household_id, None, DEBT_PLAN_ENTITY_TYPE, debt_plan_id, "update",
        before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
    )
    return after


async def delete_debt_plan(
    session: AsyncSession, household_id: UUID, debt_plan_id: UUID, user_id: UUID
) -> None:
    before = await require_debt_plan(session, household_id, debt_plan_id)
    await session.execute(
        text("delete from debt_plans where id = :id and household_id = :household_id"),
        {"id": str(debt_plan_id), "household_id": str(household_id)},
    )
    await record_audit_event(
        session, household_id, user_id, DEBT_PLAN_ENTITY_TYPE, debt_plan_id, "delete",
        before=before.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Ratephasen / Abrufe / Gebuehren / Sondertilgungen: atomarer Vollersatz
# ---------------------------------------------------------------------------


async def replace_rate_periods(
    session: AsyncSession, household_id: UUID, debt_plan_id: UUID, periods: list[RatePeriodInput]
) -> list[RatePeriod]:
    await require_debt_plan(session, household_id, debt_plan_id)
    await session.execute(
        text("delete from debt_rate_periods where debt_plan_id = :id"), {"id": str(debt_plan_id)}
    )
    for period in periods:
        await session.execute(
            text(
                "insert into debt_rate_periods "
                "(household_id, debt_plan_id, starts_after_month, ends_after_month, "
                "nominal_rate_bps_annual, rate_kind, label) "
                "values (:household_id, :debt_plan_id, :starts_after_month, :ends_after_month, "
                ":nominal_rate_bps_annual, :rate_kind, :label)"
            ),
            {
                "household_id": str(household_id),
                "debt_plan_id": str(debt_plan_id),
                **period.model_dump(),
            },
        )
    result = await session.execute(
        text(
            "select id, starts_after_month, ends_after_month, nominal_rate_bps_annual, "
            "rate_kind, label from debt_rate_periods where debt_plan_id = :id "
            "order by starts_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    return [RatePeriod(**row._mapping) for row in result]


async def replace_draws(
    session: AsyncSession, household_id: UUID, debt_plan_id: UUID, draws: list[DrawInput]
) -> list[Draw]:
    await require_debt_plan(session, household_id, debt_plan_id)
    await session.execute(
        text("delete from debt_draws where debt_plan_id = :id"), {"id": str(debt_plan_id)}
    )
    for draw in draws:
        await session.execute(
            text(
                "insert into debt_draws (household_id, debt_plan_id, occurs_after_month, "
                "amount_minor, status) "
                "values (:household_id, :debt_plan_id, :occurs_after_month, :amount_minor, :status)"
            ),
            {
                "household_id": str(household_id),
                "debt_plan_id": str(debt_plan_id),
                **draw.model_dump(),
            },
        )
    result = await session.execute(
        text(
            "select id, occurs_after_month, amount_minor, status from debt_draws "
            "where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    return [Draw(**row._mapping) for row in result]


async def replace_fees(
    session: AsyncSession, household_id: UUID, debt_plan_id: UUID, fees: list[FeeInput]
) -> list[Fee]:
    await require_debt_plan(session, household_id, debt_plan_id)
    await session.execute(
        text("delete from debt_fees where debt_plan_id = :id"), {"id": str(debt_plan_id)}
    )
    for fee in fees:
        await session.execute(
            text(
                "insert into debt_fees (household_id, debt_plan_id, occurs_after_month, "
                "amount_minor, fee_kind, financed, label) "
                "values (:household_id, :debt_plan_id, :occurs_after_month, :amount_minor, "
                ":fee_kind, :financed, :label)"
            ),
            {
                "household_id": str(household_id),
                "debt_plan_id": str(debt_plan_id),
                **fee.model_dump(),
            },
        )
    result = await session.execute(
        text(
            "select id, occurs_after_month, amount_minor, fee_kind, financed, label from debt_fees "
            "where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    return [Fee(**row._mapping) for row in result]


async def replace_special_repayments(
    session: AsyncSession,
    household_id: UUID,
    debt_plan_id: UUID,
    special_repayments: list[SpecialRepaymentInput],
) -> list[SpecialRepayment]:
    await require_debt_plan(session, household_id, debt_plan_id)
    await session.execute(
        text("delete from debt_special_repayments where debt_plan_id = :id"),
        {"id": str(debt_plan_id)},
    )
    for repayment in special_repayments:
        await session.execute(
            text(
                "insert into debt_special_repayments (household_id, debt_plan_id, "
                "occurs_after_month, amount_minor, status, label) "
                "values (:household_id, :debt_plan_id, :occurs_after_month, :amount_minor, "
                ":status, :label)"
            ),
            {
                "household_id": str(household_id),
                "debt_plan_id": str(debt_plan_id),
                **repayment.model_dump(),
            },
        )
    result = await session.execute(
        text(
            "select id, occurs_after_month, amount_minor, status, label "
            "from debt_special_repayments where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    return [SpecialRepayment(**row._mapping) for row in result]


# ---------------------------------------------------------------------------
# Berechnung
# ---------------------------------------------------------------------------


async def _load_engine_input(
    session: AsyncSession, household_id: UUID, debt_plan_id: UUID, *, horizon_months: int
) -> DebtPlanInput:
    plan = await require_debt_plan(session, household_id, debt_plan_id)

    periods_result = await session.execute(
        text(
            "select starts_after_month, ends_after_month, nominal_rate_bps_annual, rate_kind "
            "from debt_rate_periods where debt_plan_id = :id order by starts_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    rate_periods = tuple(
        EngineRatePeriodInput(
            starts_after_month=row.starts_after_month,
            ends_after_month=row.ends_after_month,
            nominal_rate_bps_annual=row.nominal_rate_bps_annual,
            rate_kind=row.rate_kind,
        )
        for row in periods_result
    )
    if not rate_periods:
        raise DebtCalculationError("loan_rate_gap", "Keine Ratephase hinterlegt.")

    draws_result = await session.execute(
        text(
            "select occurs_after_month, amount_minor from debt_draws "
            "where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    draws = tuple(EngineDrawInput(**row._mapping) for row in draws_result)

    fees_result = await session.execute(
        text(
            "select occurs_after_month, amount_minor, financed from debt_fees "
            "where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    fees = tuple(EngineFeeInput(**row._mapping) for row in fees_result)

    special_result = await session.execute(
        text(
            "select occurs_after_month, amount_minor from debt_special_repayments "
            "where debt_plan_id = :id order by occurs_after_month"
        ),
        {"id": str(debt_plan_id)},
    )
    special_repayments = tuple(
        EngineSpecialRepaymentInput(**row._mapping) for row in special_result
    )

    return DebtPlanInput(
        principal_limit_minor=plan.principal_limit_minor,
        opening_balance_minor=plan.opening_balance_minor,
        payment_interval_months=plan.payment_interval_months,
        amortization_mode=plan.amortization_mode,
        fixed_payment_minor=plan.fixed_payment_minor,
        initial_repayment_rate_bps=plan.initial_repayment_rate_bps,
        target_term_months=plan.target_term_months,
        repayment_free_months=plan.repayment_free_months,
        payment_free_months=plan.payment_free_months,
        capitalize_interest_during_payment_free=plan.capitalize_interest_during_payment_free,
        commitment_free_months=plan.commitment_free_months,
        commitment_rate_bps_per_month=plan.commitment_rate_bps_per_month,
        calculation_horizon_months=horizon_months,
        rate_periods=rate_periods,
        draws=draws,
        fees=fees,
        special_repayments=special_repayments,
    )


async def calculate_schedule(
    session: AsyncSession, household_id: UUID, debt_plan_id: UUID, *, horizon_months: int = 360
) -> DebtSchedule:
    try:
        engine_input = await _load_engine_input(
            session, household_id, debt_plan_id, horizon_months=horizon_months
        )
        summary = calculate_debt_schedule(engine_input)
    except DebtCalculationError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc

    return DebtSchedule(
        debt_plan_id=debt_plan_id,
        total_drawn_minor=summary.total_drawn_minor,
        total_interest_minor=summary.total_interest_minor,
        total_capitalized_interest_minor=summary.total_capitalized_interest_minor,
        total_commitment_interest_minor=summary.total_commitment_interest_minor,
        total_fees_minor=summary.total_fees_minor,
        total_cash_payments_minor=summary.total_cash_payments_minor,
        ending_balance_minor=summary.ending_balance_minor,
        payoff_month=summary.payoff_month,
        peak_payment_minor=summary.peak_payment_minor,
        first_negative_amortization_month=summary.first_negative_amortization_month,
        warnings=list(summary.warnings),
        rows=[
            DebtScheduleRow(**row.model_dump())
            for row in summary.rows
        ],
    )
