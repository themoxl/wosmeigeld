"""Deterministic planning for non-monthly expense-contract reserves.

The calculation is deliberately calendar-month based and uses minor-unit
integers only.  A first-cycle contribution starts in the calendar month after
``reserve_started_on``; after a due month the next cycle starts at that due
date.  Consequently March -> August has five deposits (April through August),
while one complete yearly cycle has twelve deposits.

These values are planning assumptions.  They never represent an account
balance and never create a transaction.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import contract_service

RESERVE_FREQUENCIES: Final = frozenset({"quarterly", "semiannual", "yearly"})
COUNTING_STATUSES: Final = frozenset({"active", "cancelled"})


@dataclass(frozen=True)
class FixedCostReserve:
    contract_id: Any
    contract_name: str
    frequency: str
    period_amount_minor: int
    next_due_date: date
    monthly_reserve_minor: int
    accrued_minor: int
    remaining_minor: int
    months_remaining: int
    due_this_month: bool
    is_covered: bool
    is_first_cycle: bool


def _month_index(value: date) -> int:
    return value.year * 12 + value.month


def _ceil_div(value: int, divisor: int) -> int:
    if value <= 0:
        return 0
    return (value + divisor - 1) // divisor


def is_reserve_eligible(direction: str, frequency: str) -> bool:
    return direction == "expense" and frequency in RESERVE_FREQUENCIES


def calculate_fixed_cost_reserve(
    *,
    contract_id: Any,
    contract_name: str,
    direction: str,
    frequency: str,
    period_amount_minor: int,
    next_due_date_value: date,
    due_rule: str,
    due_day: int | None,
    reserve_enabled: bool,
    reserve_started_on: date | None,
    reserve_starting_amount_minor: int,
    status: str,
    starts_on: date | None,
    ends_on: date | None,
    budget_month: date,
) -> FixedCostReserve | None:
    """Return the reserve state for the first day of ``budget_month``.

    The monthly contribution is the capacity required throughout the current
    cycle.  The theoretical accrued value includes the selected month's
    beginning-of-month deposit.  In the enablement month, however, no deposit
    is presumed: the first one happens in the following calendar month.
    """
    month_start = budget_month.replace(day=1)
    if not reserve_enabled or not is_reserve_eligible(direction, frequency):
        return None
    if reserve_started_on is None or status not in COUNTING_STATUSES:
        return None
    if reserve_started_on > contract_service.add_months(month_start, 1, day=1) - date.resolution:
        return None
    if starts_on is not None and starts_on >= contract_service.add_months(month_start, 1, day=1):
        return None
    if ends_on is not None and ends_on < month_start:
        return None

    due = next_due_date_value
    # next_due_date is usually already in the future, but historical budget
    # months and stale imports can put the anchor on either side.  Walk with
    # the canonical contract helpers so day 29/30/31 semantics stay identical.
    while due < month_start:
        due = contract_service.next_due_date(
            due, frequency, due_rule=due_rule, due_day=due_day
        )
    previous_due = contract_service.previous_due_date(
        due, frequency, due_rule=due_rule, due_day=due_day
    )

    first_cycle = previous_due < reserve_started_on <= due
    cycle_start = reserve_started_on if first_cycle else previous_due
    starting = min(reserve_starting_amount_minor, period_amount_minor) if first_cycle else 0
    contribution_months = max(_month_index(due) - _month_index(cycle_start), 1)
    monthly = _ceil_div(period_amount_minor - starting, contribution_months)
    elapsed = min(
        max(_month_index(month_start) - _month_index(cycle_start), 0),
        contribution_months,
    )
    accrued = min(period_amount_minor, starting + monthly * elapsed)
    remaining = max(period_amount_minor - accrued, 0)
    months_remaining = max(_month_index(due) - _month_index(month_start), 0)
    due_this_month = due.year == month_start.year and due.month == month_start.month
    is_covered = accrued >= period_amount_minor
    if due_this_month and is_covered:
        # The selected invoice is fully funded. Capacity planning now shows
        # the sustainable rate for the cycle beginning after this invoice,
        # not the expiring first-cycle catch-up rate. The item still keeps this
        # month's due/accrued/coverage facts so the real charge remains clear.
        following_due = contract_service.next_due_date(
            due, frequency, due_rule=due_rule, due_day=due_day
        )
        following_months = max(_month_index(following_due) - _month_index(due), 1)
        monthly = _ceil_div(period_amount_minor, following_months)

    return FixedCostReserve(
        contract_id=contract_id,
        contract_name=contract_name,
        frequency=frequency,
        period_amount_minor=period_amount_minor,
        next_due_date=due,
        monthly_reserve_minor=monthly,
        accrued_minor=accrued,
        remaining_minor=remaining,
        months_remaining=months_remaining,
        due_this_month=due_this_month,
        is_covered=is_covered,
        is_first_cycle=first_cycle,
    )


async def fetch_fixed_cost_reserves(
    session: AsyncSession, household_id: Any, *, budget_month: date
) -> list[FixedCostReserve]:
    """Load active household reserves, excluding merged-away contracts."""
    result = await session.execute(
        text(
            "select id, name, direction, frequency, amount_minor, next_due_date, "
            "due_rule, due_day, reserve_enabled, reserve_started_on, "
            "reserve_starting_amount_minor, status, starts_on, ends_on "
            "from contracts where household_id = :household_id "
            "and merged_into_id is null and reserve_enabled = true "
            "and direction = 'expense' "
            "and frequency = any(cast(:frequencies as text[]))"
        ),
        {"household_id": str(household_id), "frequencies": list(RESERVE_FREQUENCIES)},
    )
    reserves: list[FixedCostReserve] = []
    for row in result:
        calculation = calculate_fixed_cost_reserve(
            contract_id=row.id,
            contract_name=row.name,
            direction=row.direction,
            frequency=row.frequency,
            period_amount_minor=int(row.amount_minor),
            next_due_date_value=row.next_due_date,
            due_rule=row.due_rule,
            due_day=row.due_day,
            reserve_enabled=row.reserve_enabled,
            reserve_started_on=row.reserve_started_on,
            reserve_starting_amount_minor=int(row.reserve_starting_amount_minor),
            status=row.status,
            starts_on=row.starts_on,
            ends_on=row.ends_on,
            budget_month=budget_month,
        )
        if calculation is not None:
            reserves.append(calculation)
    return reserves
