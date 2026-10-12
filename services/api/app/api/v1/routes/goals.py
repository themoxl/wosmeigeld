"""API-Endpunkte fuer Sparziele, Wunschliste, Vermögen & Schulden (Phase 7)."""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_household_session, require_household_editor
from app.auth.jwt import AuthenticatedUser
from app.schemas.goals import (
    AssetDebtCreate,
    AssetDebtResponse,
    AssetDebtUpdate,
    AssetValuationResponse,
    GoalStatus,
    NetWorthReportResponse,
    SavingsGoalCreate,
    SavingsGoalResponse,
    SavingsGoalUpdate,
    SavingsPlanPreviewRequest,
    SavingsPlanPreviewResponse,
    WishlistItemCreate,
    WishlistItemResponse,
    WishlistItemUpdate,
    WishlistSavingsPlanCreate,
    WishlistSavingsPlanResponse,
)
from app.schemas.projects import WishlistConvertResponse
from app.services import category_service, debt_plan_service, project_service
from app.services.goal_service import (
    calculate_payoff_months,
    evaluate_traffic_light,
    fetch_free_monthly_minor,
    fetch_goal_available_by_goal,
    fetch_goal_available_minor,
    fetch_goal_deposit_minor,
    fetch_goal_deposits_by_goal,
    fetch_net_worth_report,
)
from app.services.savings_planning import calculate_savings_plan, validate_included_plan

router = APIRouter(tags=["goals"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Schreibende Endpunkte: 403 statt stillem Nichtstun, wenn die Rolle nicht
# schreiben darf (siehe app/auth/dependencies.py).
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]

_GOAL_COLUMNS = (
    "id, name, target_amount_minor, starting_amount_minor, target_date, "
    "planning_mode, monthly_contribution_minor, include_in_planning, priority, "
    "account_id, category_id, status, created_at"
)
_WISHLIST_COLUMNS = "id, name, price_minor, priority, note, goal_id, status, created_at"
_ASSET_COLUMNS = (
    "id, name, type, asset_class, current_value_minor, interest_rate_percent, "
    "monthly_payment_minor, account_id, updated_at"
)
_VALUATION_COLUMNS = "id, asset_id, valued_on, value_minor, created_at"


def _to_goal_response(row: Any, deposits_minor: int) -> SavingsGoalResponse:
    current = row.starting_amount_minor + deposits_minor
    # Ein aktives Ziel wird automatisch als erreicht dargestellt, sobald sein
    # abgeleiteter Stand die Zielsumme deckt. Archivierte/beendete Ziele
    # behalten ihren expliziten Status. Die Ableitung bleibt reversibel, wenn
    # eine zugeordnete Buchung spaeter geloest oder korrigiert wird.
    effective_status: GoalStatus = (
        "cancelled"
        if row.status == "cancelled"
        else "reached"
        if current >= row.target_amount_minor
        else "active"
    )
    planning = calculate_savings_plan(
        target_minor=row.target_amount_minor,
        current_minor=current,
        planning_mode=row.planning_mode,
        target_date=row.target_date,
        monthly_contribution_minor=row.monthly_contribution_minor,
        include_in_planning=row.include_in_planning,
        goal_status=effective_status,
    )
    return SavingsGoalResponse(
        id=row.id,
        name=row.name,
        target_amount_minor=row.target_amount_minor,
        starting_amount_minor=row.starting_amount_minor,
        current_amount_minor=current,
        target_date=row.target_date,
        monthly_required_minor=planning.monthly_required_minor,
        planning_mode=row.planning_mode,
        monthly_contribution_minor=row.monthly_contribution_minor,
        include_in_planning=row.include_in_planning,
        priority=row.priority,
        effective_monthly_reserve_minor=planning.effective_monthly_reserve_minor,
        projected_completion_date=planning.projected_completion_date,
        planning_status=planning.planning_status,
        account_id=row.account_id,
        category_id=row.category_id,
        status=effective_status,
        created_at=row.created_at,
    )


def _to_wishlist_response(
    row: Any, free_monthly_minor: int, goal_available_minor: int = 0
) -> WishlistItemResponse:
    evaluation = evaluate_traffic_light(row.price_minor, free_monthly_minor, goal_available_minor)
    return WishlistItemResponse(
        id=row.id,
        name=row.name,
        price_minor=row.price_minor,
        priority=row.priority,
        note=row.note,
        goal_id=row.goal_id,
        status=row.status,
        buy_traffic_light=evaluation.status,
        coverage_ratio=evaluation.coverage_ratio,
        shortfall_minor=evaluation.shortfall_minor,
        earliest_purchase_date=evaluation.earliest_purchase_date,
        created_at=row.created_at,
    )


def _to_valuation_response(row: Any) -> AssetValuationResponse:
    return AssetValuationResponse(
        id=row.id,
        asset_id=row.asset_id,
        valued_on=row.valued_on,
        value_minor=row.value_minor,
        created_at=row.created_at,
    )


async def _record_asset_valuation(
    db: AsyncSession, household_id: UUID, asset_id: UUID, value_minor: int
) -> None:
    """Schreibt einen neuen Punkt der Bewertungshistorie, damit der bisherige
    Wert beim Ueberschreiben von current_value_minor nicht verloren geht."""
    await db.execute(
        text(
            "insert into asset_valuations (household_id, asset_id, value_minor) "
            "values (:household_id, :asset_id, :value_minor)"
        ),
        {
            "household_id": str(household_id),
            "asset_id": str(asset_id),
            "value_minor": value_minor,
        },
    )


async def _payoff_months_from_plan(
    db: AsyncSession, household_id: UUID, asset_id: UUID
) -> int | None:
    """Restlaufzeit aus `debt_plans`, wenn ein Plan existiert (Plan 14.4:
    `payoff_months_projected` wird bei vorhandenem Plan aus dem neuen
    Rechenkern abgeleitet). Gibt `None` zurueck, wenn (noch) kein Plan
    existiert, damit der Aufrufer auf die Legacy-Formel zurueckfallen kann."""
    plan_row = (
        await db.execute(
            text(
                "select id from debt_plans "
                "where asset_debt_id = :asset_id and household_id = :household_id"
            ),
            {"asset_id": str(asset_id), "household_id": str(household_id)},
        )
    ).first()
    if plan_row is None:
        return None
    try:
        schedule = await debt_plan_service.calculate_schedule(
            db, household_id, plan_row.id, horizon_months=600
        )
    except HTTPException:
        # Plan existiert, ist aber noch unvollstaendig konfiguriert (z.B. keine
        # Ratephase) -- Aufrufer faellt auf die Legacy-Formel zurueck, statt
        # die ganze Vermoegensliste mit einem 422 zu blockieren.
        return None
    return schedule.payoff_month


async def _to_asset_response(db: AsyncSession, household_id: UUID, row: Any) -> AssetDebtResponse:
    payoff: int | None = None
    if row.type == "debt":
        payoff = await _payoff_months_from_plan(db, household_id, row.id)
        if payoff is None:
            interest_rate = float(row.interest_rate_percent or 0.0)
            payoff = calculate_payoff_months(
                row.current_value_minor, row.monthly_payment_minor, interest_rate
            )
    return AssetDebtResponse(
        id=row.id,
        name=row.name,
        type=row.type,
        asset_class=row.asset_class,
        current_value_minor=row.current_value_minor,
        interest_rate_percent=float(row.interest_rate_percent or 0.0),
        monthly_payment_minor=row.monthly_payment_minor,
        account_id=row.account_id,
        payoff_months_projected=payoff,
        updated_at=row.updated_at,
    )


async def _create_legacy_debt_plan(db: AsyncSession, household_id: UUID, asset_row: Any) -> None:
    """Erzeugt bei einer neuen Schuld in derselben Transaktion einen
    einfachen `debt_plan` (Plan 14.4), damit `debt_plans` ab sofort die
    alleinige Rechengrundlage ist -- dieselbe Ableitung wie der P0-Backfill
    fuer Altdaten, nur fuer neu angelegte Schulden zum Zeitpunkt der Anlage."""
    has_payment = (asset_row.monthly_payment_minor or 0) > 0
    plan_row = (
        await db.execute(
            text(
                "insert into debt_plans "
                "(household_id, asset_debt_id, name, currency, status, "
                "principal_limit_minor, opening_balance_minor, starts_on, "
                "payment_interval_months, amortization_mode, fixed_payment_minor, "
                "target_term_months) "
                "values (:household_id, :asset_id, :name, 'EUR', 'active', "
                ":principal_limit_minor, :opening_balance_minor, current_date, 1, "
                ":amortization_mode, :fixed_payment_minor, :target_term_months) "
                "returning id"
            ),
            {
                "household_id": str(household_id),
                "asset_id": str(asset_row.id),
                "name": asset_row.name,
                "principal_limit_minor": max(asset_row.current_value_minor, 1),
                "opening_balance_minor": asset_row.current_value_minor,
                "amortization_mode": "fixed_payment" if has_payment else "full_repayment_term",
                "fixed_payment_minor": asset_row.monthly_payment_minor if has_payment else None,
                "target_term_months": None if has_payment else 360,
            },
        )
    ).first()
    assert plan_row is not None
    await db.execute(
        text(
            "insert into debt_rate_periods "
            "(household_id, debt_plan_id, starts_after_month, nominal_rate_bps_annual, "
            "rate_kind, label) "
            "values (:household_id, :debt_plan_id, 0, :nominal_rate_bps_annual, 'assumed', "
            "'Uebernommen aus assets_and_debts.interest_rate_percent')"
        ),
        {
            "household_id": str(household_id),
            "debt_plan_id": str(plan_row.id),
            "nominal_rate_bps_annual": round((asset_row.interest_rate_percent or 0) * 100),
        },
    )


async def _sync_legacy_debt_plan(
    db: AsyncSession, household_id: UUID, asset_row: Any, changed_fields: set[str]
) -> None:
    """Haelt den einfachen, aus ``assets_and_debts`` erzeugten Tilgungsplan
    mit den direkt editierbaren Schuldenfeldern synchron.

    Projektplaene koennen nach Projektabschluss ebenfalls auf einen
    ``asset_debt`` zeigen, bleiben aber fachlich eigenstaendige Plaene und
    werden hier bewusst nicht ueberschrieben.
    """
    plan_row = (
        await db.execute(
            text(
                "select id from debt_plans "
                "where asset_debt_id = :asset_id and household_id = :household_id "
                "and project_financing_source_id is null"
            ),
            {"asset_id": str(asset_row.id), "household_id": str(household_id)},
        )
    ).first()
    if plan_row is None:
        return

    plan_updates: list[str] = []
    params: dict[str, Any] = {
        "debt_plan_id": str(plan_row.id),
        "household_id": str(household_id),
    }
    if "name" in changed_fields:
        plan_updates.append("name = :name")
        params["name"] = asset_row.name
    if "current_value_minor" in changed_fields:
        plan_updates.extend(
            [
                "opening_balance_minor = :opening_balance_minor",
                "principal_limit_minor = greatest(principal_limit_minor, :principal_limit_minor)",
            ]
        )
        params["opening_balance_minor"] = asset_row.current_value_minor
        params["principal_limit_minor"] = max(asset_row.current_value_minor, 1)
    if "monthly_payment_minor" in changed_fields:
        has_payment = (asset_row.monthly_payment_minor or 0) > 0
        plan_updates.extend(
            [
                "amortization_mode = :amortization_mode",
                "fixed_payment_minor = :fixed_payment_minor",
                "initial_repayment_rate_bps = null",
                "target_term_months = :target_term_months",
            ]
        )
        params["amortization_mode"] = "fixed_payment" if has_payment else "full_repayment_term"
        params["fixed_payment_minor"] = asset_row.monthly_payment_minor if has_payment else None
        params["target_term_months"] = None if has_payment else 360

    if plan_updates:
        await db.execute(
            text(
                f"update debt_plans set {', '.join(plan_updates)} "
                "where id = :debt_plan_id and household_id = :household_id"
            ),
            params,
        )

    if "interest_rate_percent" in changed_fields:
        await db.execute(
            text(
                "update debt_rate_periods set nominal_rate_bps_annual = :rate_bps "
                "where debt_plan_id = :debt_plan_id and household_id = :household_id"
            ),
            {
                "rate_bps": round(float(asset_row.interest_rate_percent or 0.0) * 100),
                "debt_plan_id": str(plan_row.id),
                "household_id": str(household_id),
            },
        )


# --- SPARZIELE ---
@router.get("/goals", response_model=list[SavingsGoalResponse])
async def list_goals(session_ctx: HouseholdSession) -> list[SavingsGoalResponse]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {_GOAL_COLUMNS} from savings_goals "
            "where household_id = :household_id order by created_at desc"
        ),
        {"household_id": str(household_id)},
    )
    rows = res.all()
    deposits_map = await fetch_goal_deposits_by_goal(db, household_id)
    return [_to_goal_response(row, deposits_map.get(row.id, 0)) for row in rows]


@router.post("/goals/planning-preview", response_model=SavingsPlanPreviewResponse)
async def preview_goal_plan(
    payload: SavingsPlanPreviewRequest,
    session_ctx: HouseholdSession,
) -> SavingsPlanPreviewResponse:
    # Auch der zustandslose Rechner bleibt eine geschuetzte Haushaltsfunktion.
    _db, _household_id = session_ctx
    calculation = calculate_savings_plan(
        target_minor=payload.target_amount_minor,
        current_minor=payload.current_amount_minor,
        planning_mode=payload.planning_mode,
        target_date=payload.target_date,
        monthly_contribution_minor=payload.monthly_contribution_minor,
        include_in_planning=True,
        as_of_date=date.today(),
    )
    return SavingsPlanPreviewResponse(
        target_amount_minor=payload.target_amount_minor,
        current_amount_minor=payload.current_amount_minor,
        remaining_amount_minor=max(
            payload.target_amount_minor - payload.current_amount_minor,
            0,
        ),
        planning_mode=payload.planning_mode,
        monthly_required_minor=calculation.monthly_required_minor,
        effective_monthly_reserve_minor=calculation.effective_monthly_reserve_minor,
        projected_completion_date=calculation.projected_completion_date,
        planning_status=calculation.planning_status,
    )


@router.post("/goals", response_model=SavingsGoalResponse, status_code=status.HTTP_201_CREATED)
async def create_goal(
    payload: SavingsGoalCreate, session_ctx: HouseholdEditorSession
) -> SavingsGoalResponse:
    db, household_id = session_ctx
    category_id = await category_service.resolve_category_id(
        db, household_id, payload.category_id
    )
    res = await db.execute(
        text(
            "insert into savings_goals (household_id, name, target_amount_minor, "
            "starting_amount_minor, target_date, planning_mode, monthly_contribution_minor, "
            "include_in_planning, priority, account_id, category_id) "
            "values (:household_id, :name, :target, :starting, :t_date, :planning_mode, "
            ":monthly_contribution, :include_in_planning, :priority, :acc_id, :cat_id) "
            f"returning {_GOAL_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "name": payload.name,
            "target": payload.target_amount_minor,
            "starting": payload.starting_amount_minor,
            "t_date": payload.target_date,
            "planning_mode": payload.planning_mode,
            "monthly_contribution": payload.monthly_contribution_minor,
            "include_in_planning": payload.include_in_planning,
            "priority": payload.priority,
            "acc_id": str(payload.account_id) if payload.account_id else None,
            "cat_id": str(category_id) if category_id else None,
        },
    )
    row = res.one()
    await db.execute(
        text(
            "insert into reserve_pots (household_id, kind, goal_id) "
            "values (:household_id, 'goal', :goal_id) on conflict do nothing"
        ),
        {"household_id": str(household_id), "goal_id": str(row.id)},
    )
    return _to_goal_response(row, 0)


@router.patch("/goals/{goal_id}", response_model=SavingsGoalResponse)
async def update_goal(
    goal_id: UUID, payload: SavingsGoalUpdate, session_ctx: HouseholdEditorSession
) -> SavingsGoalResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    current_row = (
        await db.execute(
            text(
                f"select {_GOAL_COLUMNS} from savings_goals "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(goal_id), "household_id": str(household_id)},
        )
    ).first()
    if current_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal_not_found")
    if "category_id" in updates:
        updates["category_id"] = await category_service.resolve_category_id(
            db, household_id, updates["category_id"]
        )
    if "planning_mode" in updates and updates["planning_mode"] is None:
        raise HTTPException(status_code=400, detail="Planungsmodus darf nicht leer sein.")
    if "include_in_planning" in updates and updates["include_in_planning"] is None:
        raise HTTPException(status_code=400, detail="Planungsschalter darf nicht leer sein.")

    next_status = updates.get("status", current_row.status)
    try:
        validate_included_plan(
            planning_mode=updates.get("planning_mode", current_row.planning_mode),
            target_date=updates.get("target_date", current_row.target_date),
            monthly_contribution_minor=updates.get(
                "monthly_contribution_minor", current_row.monthly_contribution_minor
            ),
            include_in_planning=(
                updates.get("include_in_planning", current_row.include_in_planning)
                if next_status == "active"
                else False
            ),
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    row = current_row
    if updates:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(goal_id)
        updates["household_id"] = str(household_id)
        updated_row = (
            await db.execute(
                text(
                    f"update savings_goals set {set_clause} "
                    "where id = :id and household_id = :household_id "
                    f"returning {_GOAL_COLUMNS}"
                ),
                updates,
            )
        ).first()
        if updated_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal_not_found")
        row = updated_row
    deposits_minor = await fetch_goal_deposit_minor(db, household_id, goal_id)
    return _to_goal_response(row, deposits_minor)


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(goal_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    db, household_id = session_ctx
    # 'returning id' + Pruefung: ohne sie antwortet ein DELETE, das keine Zeile
    # getroffen hat (fremder Haushalt, bereits geloescht), mit 204 - das
    # Frontend rendert dann eine Loeschung, die nie stattgefunden hat.
    deleted = (
        await db.execute(
            text(
                "delete from savings_goals where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(goal_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="goal_not_found")


# --- WUNSCHLISTE & KAUFAMPEL ---
@router.get("/wishlist", response_model=list[WishlistItemResponse])
async def list_wishlist(session_ctx: HouseholdSession) -> list[WishlistItemResponse]:
    db, household_id = session_ctx
    free_monthly_minor = await fetch_free_monthly_minor(db, household_id)
    goal_available_map = await fetch_goal_available_by_goal(db, household_id)
    res = await db.execute(
        text(
            f"select {_WISHLIST_COLUMNS} from wishlist_items "
            "where household_id = :household_id "
            "order by case status when 'open' then 0 when 'deferred' then 1 "
            "when 'purchased' then 2 else 3 end, priority asc, created_at desc"
        ),
        {"household_id": str(household_id)},
    )
    return [
        _to_wishlist_response(
            row, free_monthly_minor, goal_available_map.get(row.goal_id, 0) if row.goal_id else 0
        )
        for row in res
    ]


@router.post("/wishlist", response_model=WishlistItemResponse, status_code=status.HTTP_201_CREATED)
async def create_wishlist_item(
    payload: WishlistItemCreate, session_ctx: HouseholdEditorSession
) -> WishlistItemResponse:
    db, household_id = session_ctx
    free_monthly_minor = await fetch_free_monthly_minor(db, household_id)
    res = await db.execute(
        text(
            "insert into wishlist_items (household_id, name, price_minor, priority, note, goal_id) "
            "values (:household_id, :name, :price, :priority, :note, :goal_id) "
            f"returning {_WISHLIST_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "name": payload.name,
            "price": payload.price_minor,
            "priority": payload.priority,
            "note": payload.note,
            "goal_id": str(payload.goal_id) if payload.goal_id else None,
        },
    )
    goal_available_minor = (
        await fetch_goal_available_minor(db, household_id, payload.goal_id)
        if payload.goal_id
        else 0
    )
    return _to_wishlist_response(res.one(), free_monthly_minor, goal_available_minor)


@router.patch("/wishlist/{item_id}", response_model=WishlistItemResponse)
async def update_wishlist_item(
    item_id: UUID, payload: WishlistItemUpdate, session_ctx: HouseholdEditorSession
) -> WishlistItemResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        row = (
            await db.execute(
                text(
                    f"select {_WISHLIST_COLUMNS} from wishlist_items "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(item_id), "household_id": str(household_id)},
            )
        ).first()
    else:
        # goal_id kommt als UUID aus dem Modell und muss wie ueberall sonst als
        # Text gebunden werden; None loest die Verknuepfung bewusst auf.
        if updates.get("goal_id") is not None:
            updates["goal_id"] = str(updates["goal_id"])
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(item_id)
        updates["household_id"] = str(household_id)
        row = (
            await db.execute(
                text(
                    f"update wishlist_items set {set_clause} "
                    "where id = :id and household_id = :household_id "
                    f"returning {_WISHLIST_COLUMNS}"
                ),
                updates,
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wishlist_item_not_found")
    free_monthly_minor = await fetch_free_monthly_minor(db, household_id)
    goal_available_minor = (
        await fetch_goal_available_minor(db, household_id, row.goal_id) if row.goal_id else 0
    )
    return _to_wishlist_response(row, free_monthly_minor, goal_available_minor)


@router.post(
    "/wishlist/{item_id}/start-savings-plan",
    response_model=WishlistSavingsPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_wishlist_savings_plan(
    item_id: UUID,
    payload: WishlistSavingsPlanCreate,
    session_ctx: HouseholdEditorSession,
) -> WishlistSavingsPlanResponse:
    """Legt Spartopf und Wunsch-Verknuepfung in derselben DB-Transaktion an."""
    db, household_id = session_ctx
    item = (
        await db.execute(
            text(
                f"select {_WISHLIST_COLUMNS} from wishlist_items "
                "where id = :id and household_id = :household_id"
            ),
            {"id": str(item_id), "household_id": str(household_id)},
        )
    ).first()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wishlist_item_not_found")
    if item.status not in {"open", "deferred"}:
        raise HTTPException(status_code=409, detail="wishlist_item_not_open")
    if item.goal_id is not None:
        linked = (
            await db.execute(
                text(
                    "select status from savings_goals "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(item.goal_id), "household_id": str(household_id)},
            )
        ).first()
        if linked is not None and linked.status != "cancelled":
            raise HTTPException(status_code=409, detail="wishlist_item_has_active_goal")

    goal_row = (
        await db.execute(
            text(
                "insert into savings_goals (household_id, name, target_amount_minor, "
                "starting_amount_minor, target_date, planning_mode, monthly_contribution_minor, "
                "include_in_planning, priority) values (:household_id, :name, :target, "
                ":starting, :target_date, :planning_mode, :monthly_contribution, "
                ":include_in_planning, :priority) "
                f"returning {_GOAL_COLUMNS}"
            ),
            {
                "household_id": str(household_id),
                "name": item.name,
                "target": item.price_minor,
                "starting": payload.starting_amount_minor,
                "target_date": payload.target_date,
                "planning_mode": payload.planning_mode,
                "monthly_contribution": payload.monthly_contribution_minor,
                "include_in_planning": payload.include_in_planning,
                "priority": item.priority,
            },
        )
    ).one()
    await db.execute(
        text(
            "insert into reserve_pots (household_id, kind, goal_id) "
            "values (:household_id, 'goal', :goal_id) on conflict do nothing"
        ),
        {"household_id": str(household_id), "goal_id": str(goal_row.id)},
    )
    updated_item = (
        await db.execute(
            text(
                "update wishlist_items set goal_id = :goal_id "
                "where id = :id and household_id = :household_id "
                f"returning {_WISHLIST_COLUMNS}"
            ),
            {
                "goal_id": str(goal_row.id),
                "id": str(item_id),
                "household_id": str(household_id),
            },
        )
    ).one()
    free_monthly_minor = await fetch_free_monthly_minor(db, household_id)
    return WishlistSavingsPlanResponse(
        goal=_to_goal_response(goal_row, 0),
        wishlist_item=_to_wishlist_response(
            updated_item,
            free_monthly_minor,
            payload.starting_amount_minor,
        ),
    )


@router.delete("/wishlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wishlist_item(item_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    db, household_id = session_ctx
    deleted = (
        await db.execute(
            text(
                "delete from wishlist_items where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(item_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="wishlist_item_not_found"
        )


@router.post("/wishlist/{item_id}/convert-to-project", response_model=WishlistConvertResponse)
async def convert_wishlist_item_to_project(
    item_id: UUID,
    session_ctx: HouseholdEditorSession,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> WishlistConvertResponse:
    """Wunsch -> Vorhaben (Plan 11.2). Idempotent: ein zweiter Aufruf liefert
    dasselbe Projekt statt einen zweiten anzulegen."""
    db, household_id = session_ctx
    project_id, already_existed = await project_service.convert_wishlist_to_project(
        db, household_id, item_id, user.id
    )
    return WishlistConvertResponse(project_id=project_id, already_existed=already_existed)


# --- VERMÖGEN & SCHULDEN ---
@router.get("/assets", response_model=list[AssetDebtResponse])
async def list_assets_and_debts(session_ctx: HouseholdSession) -> list[AssetDebtResponse]:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            f"select {_ASSET_COLUMNS} from assets_and_debts "
            "where household_id = :household_id order by type, name"
        ),
        {"household_id": str(household_id)},
    )
    return [await _to_asset_response(db, household_id, row) for row in res]


@router.post("/assets", response_model=AssetDebtResponse, status_code=status.HTTP_201_CREATED)
async def create_asset_or_debt(
    payload: AssetDebtCreate, session_ctx: HouseholdEditorSession
) -> AssetDebtResponse:
    db, household_id = session_ctx
    res = await db.execute(
        text(
            "insert into assets_and_debts (household_id, name, type, asset_class, "
            "current_value_minor, interest_rate_percent, monthly_payment_minor, account_id) "
            "values (:household_id, :name, :type, :class, :val, :rate, :pay, :acc_id) "
            f"returning {_ASSET_COLUMNS}"
        ),
        {
            "household_id": str(household_id),
            "name": payload.name,
            "type": payload.type,
            "class": payload.asset_class,
            "val": payload.current_value_minor,
            "rate": payload.interest_rate_percent,
            "pay": payload.monthly_payment_minor,
            "acc_id": str(payload.account_id) if payload.account_id else None,
        },
    )
    row = res.one()
    # Startbewertung als erster Punkt der Historie
    await _record_asset_valuation(db, household_id, row.id, row.current_value_minor)
    if row.type == "debt":
        await _create_legacy_debt_plan(db, household_id, row)
    return await _to_asset_response(db, household_id, row)


@router.patch("/assets/{item_id}", response_model=AssetDebtResponse)
async def update_asset_or_debt(
    item_id: UUID, payload: AssetDebtUpdate, session_ctx: HouseholdEditorSession
) -> AssetDebtResponse:
    db, household_id = session_ctx
    updates = payload.model_dump(exclude_unset=True)
    changed_fields = set(updates)
    # Merken, bevor updates um id/household_id ergaenzt wird
    value_changed = "current_value_minor" in updates
    if not updates:
        row = (
            await db.execute(
                text(
                    f"select {_ASSET_COLUMNS} from assets_and_debts "
                    "where id = :id and household_id = :household_id"
                ),
                {"id": str(item_id), "household_id": str(household_id)},
            )
        ).first()
    else:
        set_clause = ", ".join(f"{column} = :{column}" for column in updates)
        updates["id"] = str(item_id)
        updates["household_id"] = str(household_id)
        row = (
            await db.execute(
                text(
                    f"update assets_and_debts set {set_clause}, updated_at = now() "
                    "where id = :id and household_id = :household_id "
                    f"returning {_ASSET_COLUMNS}"
                ),
                updates,
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset_or_debt_not_found")
    if row.type == "debt" and changed_fields & {
        "name",
        "current_value_minor",
        "interest_rate_percent",
        "monthly_payment_minor",
    }:
        await _sync_legacy_debt_plan(db, household_id, row, changed_fields)
    if value_changed:
        # current_value_minor wird in-place ueberschrieben - die neue Bewertung
        # wird zusaetzlich historisiert, damit der Verlauf erhalten bleibt.
        await _record_asset_valuation(db, household_id, row.id, row.current_value_minor)
    return await _to_asset_response(db, household_id, row)


@router.get("/assets/{item_id}/valuations", response_model=list[AssetValuationResponse])
async def list_asset_valuations(
    item_id: UUID, session_ctx: HouseholdSession
) -> list[AssetValuationResponse]:
    """Bewertungshistorie einer Vermoegens-/Schuldenposition, neueste zuerst."""
    db, household_id = session_ctx
    exists = (
        await db.execute(
            text("select 1 from assets_and_debts where id = :id and household_id = :household_id"),
            {"id": str(item_id), "household_id": str(household_id)},
        )
    ).first()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset_or_debt_not_found")
    res = await db.execute(
        text(
            f"select {_VALUATION_COLUMNS} from asset_valuations "
            "where asset_id = :id and household_id = :household_id "
            "order by valued_on desc, created_at desc"
        ),
        {"id": str(item_id), "household_id": str(household_id)},
    )
    return [_to_valuation_response(row) for row in res]


@router.delete("/assets/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset_or_debt(item_id: UUID, session_ctx: HouseholdEditorSession) -> None:
    db, household_id = session_ctx
    # Einfache, fuer assets_and_debts erzeugte Legacy-Plaene gehoeren zur
    # Schuld und werden mit ihr geloescht. Projektplaene bleiben erhalten;
    # ihre optionale Verknuepfung zur spaeter erzeugten Schuld wird geloest.
    await db.execute(
        text(
            "delete from debt_plans where asset_debt_id = :id "
            "and household_id = :household_id and project_financing_source_id is null"
        ),
        {"id": str(item_id), "household_id": str(household_id)},
    )
    await db.execute(
        text(
            "update debt_plans set asset_debt_id = null where asset_debt_id = :id "
            "and household_id = :household_id and project_financing_source_id is not null"
        ),
        {"id": str(item_id), "household_id": str(household_id)},
    )
    deleted = (
        await db.execute(
            text(
                "delete from assets_and_debts where id = :id and household_id = :household_id "
                "returning id"
            ),
            {"id": str(item_id), "household_id": str(household_id)},
        )
    ).first()
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="asset_or_debt_not_found"
        )


@router.get("/reports/net-worth", response_model=NetWorthReportResponse)
async def get_net_worth_report(session_ctx: HouseholdSession) -> NetWorthReportResponse:
    db, household_id = session_ctx
    return await fetch_net_worth_report(db, household_id)
