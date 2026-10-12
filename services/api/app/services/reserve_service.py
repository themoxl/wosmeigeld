"""Persistente Ruecklagenstaende, Monats-Sollwerte und atomare Bewegungen."""

from collections import defaultdict
from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.reserves import (
    ReserveAllocationInput,
    ReserveBatchResponse,
    ReserveDepositRequest,
    ReserveDistributionPreviewResponse,
    ReserveMovementResponse,
    ReserveOverviewResponse,
    ReservePotResponse,
    ReserveReallocationRequest,
    ReserveWithdrawalRequest,
)
from app.services import contract_service
from app.services.savings_planning import calculate_savings_plan


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month(value: date) -> date:
    return contract_service.add_months(value, 1, day=1)


def _ceil_div(value: int, divisor: int) -> int:
    return 0 if value <= 0 else (value + divisor - 1) // divisor


def _month_distance(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def resolve_contract_due(
    *,
    next_due_date: date,
    frequency: str,
    due_rule: str,
    due_day: int | None,
    reserve_started_on: date,
    selected_month: date,
    today: date | None = None,
) -> date:
    """Resolve a stale due anchor without writing it back.

    A due date earlier in the current month is already past and belongs to the
    following cycle. Historical views only compare against their month and the
    explicit activation date, so opening an old month never jumps to today.
    """
    current_day = today or date.today()
    due = next_due_date
    threshold = max(selected_month, reserve_started_on)
    if selected_month.year == current_day.year and selected_month.month == current_day.month:
        threshold = max(threshold, current_day)
    while due < threshold:
        due = contract_service.next_due_date(
            due, frequency, due_rule=due_rule, due_day=due_day
        )
    return due


def fixed_recommendation(
    *, target_minor: int, opening_balance_minor: int, due_date: date, selected_month: date
) -> int:
    """Amount still needed divided by deposit opportunities before the due month."""
    remaining = max(target_minor - opening_balance_minor, 0)
    opportunities = max(_month_distance(selected_month, due_date), 1)
    return _ceil_div(remaining, opportunities)


def reserve_status(
    *, recommended_minor: int, deposited_minor: int, skipped: bool
) -> Literal["open", "partial", "done", "skipped"]:
    if deposited_minor >= recommended_minor and recommended_minor > 0:
        return "done"
    if deposited_minor > 0:
        return "partial"
    if skipped:
        return "skipped"
    return "done" if recommended_minor == 0 else "open"


async def _load_movements(
    session: AsyncSession, household_id: UUID, pot_ids: list[UUID], month: date
) -> tuple[dict[UUID, dict[str, int]], dict[UUID, list[ReserveMovementResponse]]]:
    if not pot_ids:
        return {}, {}
    month_after = _next_month(month)
    rows = (
        await session.execute(
            text(
                "select rm.id, rm.pot_id, rm.batch_id, rm.amount_minor, "
                "rb.occurred_on, rb.kind, rb.note, rm.created_at "
                "from reserve_movements rm join reserve_batches rb "
                "on rb.id = rm.batch_id and rb.household_id = rm.household_id "
                "where rm.household_id = :household_id and rm.pot_id = any(:pot_ids) "
                "order by rb.occurred_on desc, rm.created_at desc"
            ),
            {
                "household_id": str(household_id),
                "pot_ids": [str(item) for item in pot_ids],
            },
        )
    ).all()
    totals: dict[UUID, dict[str, int]] = defaultdict(
        lambda: {"before": 0, "through": 0, "positive": 0, "withdrawn": 0}
    )
    history: dict[UUID, list[ReserveMovementResponse]] = defaultdict(list)
    for row in rows:
        amount = int(row.amount_minor)
        if row.occurred_on < month:
            totals[row.pot_id]["before"] += amount
        if row.occurred_on < month_after:
            totals[row.pot_id]["through"] += amount
        if month <= row.occurred_on < month_after:
            if amount > 0:
                totals[row.pot_id]["positive"] += amount
            else:
                totals[row.pot_id]["withdrawn"] += abs(amount)
        if len(history[row.pot_id]) < 12:
            history[row.pot_id].append(
                ReserveMovementResponse(
                    id=row.id,
                    batch_id=row.batch_id,
                    occurred_on=row.occurred_on,
                    kind=row.kind,
                    amount_minor=amount,
                    note=row.note,
                    created_at=row.created_at,
                )
            )
    return totals, history


async def get_reserve_overview(
    session: AsyncSession,
    household_id: UUID,
    *,
    year: int,
    month: int,
    today: date | None = None,
) -> ReserveOverviewResponse:
    """Read-only snapshot. In particular, this function never creates a pot or movement."""
    selected = _month_start(year, month)
    contracts = (
        await session.execute(
            text(
                "select rp.id as pot_id, c.id, c.name, c.amount_minor, c.frequency, "
                "c.next_due_date, c.due_rule, c.due_day, c.reserve_started_on, "
                "c.reserve_starting_amount_minor, c.reserve_enabled "
                "from reserve_pots rp join contracts c "
                "on c.id = rp.contract_id and c.household_id = rp.household_id "
                "where rp.household_id = :household_id and rp.kind = 'contract' "
                "and rp.is_active and c.merged_into_id is null "
                "and c.direction = 'expense' and c.status in ('active', 'cancelled') "
                "and c.frequency in ('quarterly', 'semiannual', 'yearly') "
                "and c.reserve_enabled and c.reserve_started_on is not null "
                "and (c.ends_on is null or c.ends_on >= :selected) "
                "order by c.next_due_date, lower(c.name)"
            ),
            {"household_id": str(household_id), "selected": selected},
        )
    ).all()
    goals = (
        await session.execute(
            text(
                "select rp.id as pot_id, g.id, g.name, g.target_amount_minor, "
                "g.starting_amount_minor, g.target_date, g.planning_mode, "
                "g.monthly_contribution_minor, g.include_in_planning, g.status "
                "from reserve_pots rp join savings_goals g "
                "on g.id = rp.goal_id and g.household_id = rp.household_id "
                "where rp.household_id = :household_id and rp.kind = 'goal' "
                "and rp.is_active and g.status = 'active' order by g.priority, lower(g.name)"
            ),
            {"household_id": str(household_id)},
        )
    ).all()
    free = (
        await session.execute(
            text(
                "select id as pot_id from reserve_pots where household_id = :household_id "
                "and kind = 'free' and is_active"
            ),
            {"household_id": str(household_id)},
        )
    ).first()
    pot_ids = [row.pot_id for row in contracts] + [row.pot_id for row in goals]
    if free is not None:
        pot_ids.append(free.pot_id)
    movements, history = await _load_movements(session, household_id, pot_ids, selected)
    skipped_rows = (
        await session.execute(
            text(
                "select pot_id from reserve_month_decisions where household_id = :household_id "
                "and year = :year and month = :month and decision = 'skip'"
            ),
            {"household_id": str(household_id), "year": year, "month": month},
        )
    ).all()
    skipped = {row.pot_id for row in skipped_rows}

    # Existing split allocations remain a separate, established source for goal
    # balances. We only add ledger movements; starting amounts are never copied
    # into the ledger and therefore cannot be counted twice.
    goal_split_rows = (
        await session.execute(
            text(
                "select ts.goal_id, "
                "coalesce(sum(case when t.booked_date < :selected "
                "then ts.amount_minor else 0 end),0) before_minor, "
                "coalesce(sum(case when t.booked_date < :month_after "
                "then ts.amount_minor else 0 end),0) through_minor "
                "from transaction_splits ts join transactions t on t.id = ts.transaction_id "
                "where ts.household_id = :household_id and ts.goal_id is not null "
                "and not t.is_archived group by ts.goal_id"
            ),
            {
                "household_id": str(household_id),
                "selected": selected,
                "month_after": _next_month(selected),
            },
        )
    ).all()
    goal_splits = {
        row.goal_id: (int(row.before_minor), int(row.through_minor)) for row in goal_split_rows
    }

    result: list[ReservePotResponse] = []
    for row in contracts:
        movement = movements.get(row.pot_id, {})
        opening = int(row.reserve_starting_amount_minor) + movement.get("before", 0)
        balance = int(row.reserve_starting_amount_minor) + movement.get("through", 0)
        due = resolve_contract_due(
            next_due_date=row.next_due_date,
            frequency=row.frequency,
            due_rule=row.due_rule,
            due_day=row.due_day,
            reserve_started_on=row.reserve_started_on,
            selected_month=selected,
            today=today,
        )
        recommended = fixed_recommendation(
            target_minor=int(row.amount_minor),
            opening_balance_minor=opening,
            due_date=due,
            selected_month=selected,
        )
        next_selected = _next_month(selected)
        next_due = due
        next_balance = balance
        if due.year == selected.year and due.month == selected.month:
            next_due = contract_service.next_due_date(
                due, row.frequency, due_rule=row.due_rule, due_day=row.due_day
            )
            next_balance = max(balance - int(row.amount_minor), 0)
        next_recommended = fixed_recommendation(
            target_minor=int(row.amount_minor),
            opening_balance_minor=next_balance,
            due_date=next_due,
            selected_month=next_selected,
        )
        deposited = movement.get("positive", 0)
        result.append(
            ReservePotResponse(
                id=row.pot_id,
                kind="contract",
                source_id=row.id,
                name=row.name,
                frequency=row.frequency,
                target_amount_minor=int(row.amount_minor),
                next_due_date=due,
                recommended_minor=recommended,
                deposited_minor=deposited,
                withdrawn_minor=movement.get("withdrawn", 0),
                open_minor=max(recommended - deposited, 0),
                balance_minor=balance,
                next_month_recommended_minor=next_recommended,
                status=reserve_status(
                    recommended_minor=recommended,
                    deposited_minor=deposited,
                    skipped=row.pot_id in skipped,
                ),
                is_included_in_planning=True,
                movements=history.get(row.pot_id, []),
            )
        )

    for row in goals:
        movement = movements.get(row.pot_id, {})
        split_before, split_through = goal_splits.get(row.id, (0, 0))
        opening = int(row.starting_amount_minor) + split_before + movement.get("before", 0)
        balance = int(row.starting_amount_minor) + split_through + movement.get("through", 0)
        plan = calculate_savings_plan(
            target_minor=int(row.target_amount_minor),
            current_minor=opening,
            planning_mode=row.planning_mode,
            target_date=row.target_date,
            monthly_contribution_minor=row.monthly_contribution_minor,
            include_in_planning=True,
            as_of_date=selected,
        )
        next_plan = calculate_savings_plan(
            target_minor=int(row.target_amount_minor),
            current_minor=balance,
            planning_mode=row.planning_mode,
            target_date=row.target_date,
            monthly_contribution_minor=row.monthly_contribution_minor,
            include_in_planning=True,
            as_of_date=_next_month(selected),
        )
        recommended = plan.monthly_required_minor
        deposited = movement.get("positive", 0)
        result.append(
            ReservePotResponse(
                id=row.pot_id,
                kind="goal",
                source_id=row.id,
                name=row.name,
                target_amount_minor=int(row.target_amount_minor),
                target_date=row.target_date,
                recommended_minor=recommended,
                deposited_minor=deposited,
                withdrawn_minor=movement.get("withdrawn", 0),
                open_minor=max(recommended - deposited, 0),
                balance_minor=balance,
                next_month_recommended_minor=next_plan.monthly_required_minor,
                status=reserve_status(
                    recommended_minor=recommended,
                    deposited_minor=deposited,
                    skipped=row.pot_id in skipped,
                ),
                is_included_in_planning=bool(row.include_in_planning),
                movements=history.get(row.pot_id, []),
            )
        )

    if free is not None:
        movement = movements.get(free.pot_id, {})
        result.append(
            ReservePotResponse(
                id=free.pot_id,
                kind="free",
                source_id=None,
                name="Freie Rücklage",
                recommended_minor=0,
                deposited_minor=movement.get("positive", 0),
                withdrawn_minor=movement.get("withdrawn", 0),
                open_minor=0,
                balance_minor=movement.get("through", 0),
                next_month_recommended_minor=0,
                status="done",
                is_included_in_planning=False,
                movements=history.get(free.pot_id, []),
            )
        )

    fixed = sum(item.recommended_minor for item in result if item.kind == "contract")
    goal = sum(
        item.recommended_minor
        for item in result
        if item.kind == "goal" and item.is_included_in_planning
    )
    deposited = sum(item.deposited_minor for item in result)
    return ReserveOverviewResponse(
        year=year,
        month=month,
        recommended_minor=fixed + goal,
        deposited_minor=deposited,
        open_minor=sum(
            item.open_minor
            for item in result
            if item.kind == "contract" or item.is_included_in_planning
        ),
        total_balance_minor=sum(item.balance_minor for item in result),
        fixed_reserve_recommended_minor=fixed,
        goal_reserve_recommended_minor=goal,
        free_reserve_balance_minor=next(
            (item.balance_minor for item in result if item.kind == "free"), 0
        ),
        pots=result,
    )


async def ensure_reserve_pots(session: AsyncSession, household_id: UUID) -> None:
    """Write-path repair for objects created after an older local migration run."""
    params = {"household_id": str(household_id)}
    await session.execute(
        text(
            "insert into reserve_pots (household_id, kind) values (:household_id, 'free') "
            "on conflict do nothing"
        ),
        params,
    )
    await session.execute(
        text(
            "insert into reserve_pots (household_id, kind, contract_id) "
            "select household_id, 'contract', id from contracts "
            "where household_id = :household_id and direction = 'expense' "
            "and frequency in ('quarterly','semiannual','yearly') on conflict do nothing"
        ),
        params,
    )
    await session.execute(
        text(
            "insert into reserve_pots (household_id, kind, goal_id) "
            "select household_id, 'goal', id from savings_goals "
            "where household_id = :household_id on conflict do nothing"
        ),
        params,
    )


async def _pot_rows_for_update(
    session: AsyncSession, household_id: UUID, pot_ids: list[UUID]
) -> dict[UUID, Any]:
    rows = (
        await session.execute(
            text(
                "select id, kind, contract_id, goal_id from reserve_pots "
                "where household_id = :household_id "
                "and id = any(:pot_ids) and is_active for update"
            ),
            {"household_id": str(household_id), "pot_ids": [str(item) for item in pot_ids]},
        )
    ).all()
    result = {row.id: row for row in rows}
    if len(result) != len(set(pot_ids)):
        raise HTTPException(status_code=404, detail="reserve_pot_not_found")
    return result


async def _balances(
    session: AsyncSession, household_id: UUID, pot_ids: list[UUID]
) -> dict[UUID, int]:
    rows = (
        await session.execute(
            text(
                "select rp.id, "
                "case when rp.kind = 'contract' then coalesce(c.reserve_starting_amount_minor,0) "
                "when rp.kind = 'goal' then coalesce(g.starting_amount_minor,0) + "
                "coalesce((select sum(ts.amount_minor) from transaction_splits ts "
                "join transactions t on t.id=ts.transaction_id where ts.goal_id=g.id "
                "and ts.household_id=rp.household_id and not t.is_archived),0) else 0 end + "
                "coalesce(sum(rm.amount_minor),0) balance_minor "
                "from reserve_pots rp left join contracts c on c.id=rp.contract_id "
                "left join savings_goals g on g.id=rp.goal_id "
                "left join reserve_movements rm on rm.pot_id=rp.id "
                "and rm.household_id=rp.household_id "
                "where rp.household_id=:household_id and rp.id=any(:pot_ids) "
                "group by rp.id,rp.kind,c.reserve_starting_amount_minor,"
                "g.starting_amount_minor,g.id"
            ),
            {"household_id": str(household_id), "pot_ids": [str(item) for item in pot_ids]},
        )
    ).all()
    return {row.id: int(row.balance_minor) for row in rows}


async def _create_batch(
    session: AsyncSession,
    household_id: UUID,
    *,
    occurred_on: date,
    kind: str,
    total_minor: int,
    note: str | None,
    movements: list[tuple[UUID, int]],
    created_by: UUID | None,
    occurrence_id: UUID | None = None,
) -> ReserveBatchResponse:
    try:
        row = (
            await session.execute(
                text(
                    "insert into reserve_batches "
                    "(household_id,occurred_on,kind,total_minor,note,created_by,"
                    "contract_occurrence_id) values "
                    "(:household_id,:occurred_on,:kind,:total_minor,:note,"
                    ":created_by,:occurrence_id) "
                    "returning id,occurred_on,kind,total_minor,note"
                ),
                {
                    "household_id": str(household_id),
                    "occurred_on": occurred_on,
                    "kind": kind,
                    "total_minor": total_minor,
                    "note": note,
                    "created_by": str(created_by) if created_by else None,
                    "occurrence_id": str(occurrence_id) if occurrence_id else None,
                },
            )
        ).one()
        for pot_id, amount in movements:
            await session.execute(
                text(
                    "insert into reserve_movements (household_id,batch_id,pot_id,amount_minor) "
                    "values (:household_id,:batch_id,:pot_id,:amount)"
                ),
                {
                    "household_id": str(household_id),
                    "batch_id": str(row.id),
                    "pot_id": str(pot_id),
                    "amount": amount,
                },
            )
    except IntegrityError as err:
        raise HTTPException(status_code=409, detail="reserve_movement_conflict") from err
    return ReserveBatchResponse(
        id=row.id,
        occurred_on=row.occurred_on,
        kind=row.kind,
        total_minor=int(row.total_minor),
        note=row.note,
        allocations=[
            ReserveAllocationInput(pot_id=pot_id, amount_minor=abs(amount))
            for pot_id, amount in movements
        ],
    )


async def create_deposit(
    session: AsyncSession,
    household_id: UUID,
    payload: ReserveDepositRequest,
    *,
    created_by: UUID | None,
) -> ReserveBatchResponse:
    await ensure_reserve_pots(session, household_id)
    pot_ids = [item.pot_id for item in payload.allocations]
    await _pot_rows_for_update(session, household_id, pot_ids)
    return await _create_batch(
        session,
        household_id,
        occurred_on=payload.occurred_on,
        kind="deposit",
        total_minor=payload.total_minor,
        note=payload.note,
        movements=[(item.pot_id, item.amount_minor) for item in payload.allocations],
        created_by=created_by,
    )


async def create_withdrawal(
    session: AsyncSession,
    household_id: UUID,
    payload: ReserveWithdrawalRequest,
    *,
    created_by: UUID | None,
) -> ReserveBatchResponse:
    await ensure_reserve_pots(session, household_id)
    pots = await _pot_rows_for_update(session, household_id, [payload.pot_id])
    if payload.contract_occurrence_id is not None:
        occurrence = (
            await session.execute(
                text(
                    "select contract_id from contract_occurrences "
                    "where household_id = :household_id and id = :occurrence_id for update"
                ),
                {
                    "household_id": str(household_id),
                    "occurrence_id": str(payload.contract_occurrence_id),
                },
            )
        ).first()
        pot = pots[payload.pot_id]
        if occurrence is None or pot.contract_id != occurrence.contract_id:
            raise HTTPException(status_code=400, detail="reserve_occurrence_pot_mismatch")
    balance = (await _balances(session, household_id, [payload.pot_id])).get(payload.pot_id, 0)
    if payload.amount_minor > balance:
        raise HTTPException(status_code=409, detail="reserve_balance_insufficient")
    return await _create_batch(
        session,
        household_id,
        occurred_on=payload.occurred_on,
        kind="withdrawal",
        total_minor=payload.amount_minor,
        note=payload.note,
        movements=[(payload.pot_id, -payload.amount_minor)],
        created_by=created_by,
        occurrence_id=payload.contract_occurrence_id,
    )


async def create_reallocation(
    session: AsyncSession,
    household_id: UUID,
    payload: ReserveReallocationRequest,
    *,
    created_by: UUID | None,
) -> ReserveBatchResponse:
    await ensure_reserve_pots(session, household_id)
    free = (
        await session.execute(
            text(
                "select id from reserve_pots where household_id=:household_id "
                "and kind='free' and is_active for update"
            ),
            {"household_id": str(household_id)},
        )
    ).one()
    if any(item.pot_id == free.id for item in payload.allocations):
        raise HTTPException(status_code=400, detail="free_reserve_cannot_target_itself")
    target_ids = [item.pot_id for item in payload.allocations]
    await _pot_rows_for_update(session, household_id, target_ids)
    total = sum(item.amount_minor for item in payload.allocations)
    free_balance = (await _balances(session, household_id, [free.id])).get(free.id, 0)
    if total > free_balance:
        raise HTTPException(status_code=409, detail="reserve_balance_insufficient")
    return await _create_batch(
        session,
        household_id,
        occurred_on=payload.occurred_on,
        kind="reallocation",
        total_minor=total,
        note=payload.note,
        movements=[(free.id, -total)]
        + [(item.pot_id, item.amount_minor) for item in payload.allocations],
        created_by=created_by,
    )


async def set_skip(
    session: AsyncSession, household_id: UUID, *, pot_id: UUID, year: int, month: int
) -> None:
    await ensure_reserve_pots(session, household_id)
    await _pot_rows_for_update(session, household_id, [pot_id])
    await session.execute(
        text(
            "insert into reserve_month_decisions (household_id,pot_id,year,month,decision) "
            "values (:household_id,:pot_id,:year,:month,'skip') "
            "on conflict (household_id,pot_id,year,month) do update "
            "set decision='skip',updated_at=now()"
        ),
        {
            "household_id": str(household_id),
            "pot_id": str(pot_id),
            "year": year,
            "month": month,
        },
    )


async def clear_skip(
    session: AsyncSession, household_id: UUID, *, pot_id: UUID, year: int, month: int
) -> None:
    """Undo an explicit monthly skip; idempotent for already-open months."""
    await _pot_rows_for_update(session, household_id, [pot_id])
    await session.execute(
        text(
            "delete from reserve_month_decisions where household_id = :household_id "
            "and pot_id = :pot_id and year = :year and month = :month"
        ),
        {
            "household_id": str(household_id),
            "pot_id": str(pot_id),
            "year": year,
            "month": month,
        },
    )


async def preview_distribution(
    session: AsyncSession,
    household_id: UUID,
    *,
    year: int,
    month: int,
    total_minor: int,
) -> ReserveDistributionPreviewResponse:
    overview = await get_reserve_overview(
        session, household_id, year=year, month=month
    )
    remaining = total_minor
    allocations: list[ReserveAllocationInput] = []
    for pot in overview.pots:
        if pot.kind == "free" or pot.open_minor <= 0 or remaining <= 0:
            continue
        amount = min(pot.open_minor, remaining)
        allocations.append(ReserveAllocationInput(pot_id=pot.id, amount_minor=amount))
        remaining -= amount
    return ReserveDistributionPreviewResponse(
        total_minor=total_minor,
        allocations=allocations,
        free_remainder_minor=remaining,
    )
