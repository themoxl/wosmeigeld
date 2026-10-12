"""Deterministische Finanzmathematik fuer kalkulatorische Spartopf-Ruecklagen.

Die Funktionen in diesem Modul veraendern keine Buchungen. Sie leiten aus dem
aktuellen, split-basierten Spartopfstand lediglich Planwerte ab, die im Budget
als eigene Informationsschicht angezeigt werden.
"""

import calendar
import math
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PlanningMode = Literal["target_date", "monthly_amount"]
PlanningStatus = Literal["on_track", "overdue", "incomplete", "reached", "cancelled"]


@dataclass(frozen=True)
class SavingsPlanCalculation:
    monthly_required_minor: int
    effective_monthly_reserve_minor: int
    projected_completion_date: date | None
    planning_status: PlanningStatus


@dataclass(frozen=True)
class GoalReserveSummary:
    total_monthly_minor: int
    goal_count: int


def add_months(base: date, months: int) -> date:
    """Verschiebt ein Datum monatsgenau und kappt den Tag am Monatsende."""
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))


def calculate_monthly_required(
    target_minor: int,
    current_minor: int,
    target_date: date | None,
    *,
    as_of_date: date | None = None,
) -> int:
    """Monatsrate bis zum Zielmonat, immer centgenau nach oben gerundet.

    Einzahlungen werden ab dem naechsten Monatswechsel angenommen. Der Abstand
    August -> Juni betraegt damit zehn Monatsraten. Ein bereits faelliges Ziel
    wird als Nachholrate fuer genau einen Monat behandelt.
    """
    remaining = max(target_minor - current_minor, 0)
    if remaining == 0:
        return 0
    if target_date is None:
        return 0
    today = as_of_date or date.today()
    months = (target_date.year - today.year) * 12 + (target_date.month - today.month)
    return math.ceil(remaining / max(months, 1))


def calculate_projected_completion_date(
    target_minor: int,
    current_minor: int,
    monthly_contribution_minor: int | None,
    *,
    as_of_date: date | None = None,
) -> date | None:
    """Erster Tag des Monats nach der letzten angenommenen Monatsrate."""
    remaining = max(target_minor - current_minor, 0)
    today = as_of_date or date.today()
    if remaining == 0:
        return today
    if monthly_contribution_minor is None or monthly_contribution_minor <= 0:
        return None
    months = math.ceil(remaining / monthly_contribution_minor)
    return add_months(date(today.year, today.month, 1), months)


def calculate_savings_plan(
    *,
    target_minor: int,
    current_minor: int,
    planning_mode: PlanningMode,
    target_date: date | None,
    monthly_contribution_minor: int | None,
    include_in_planning: bool,
    goal_status: Literal["active", "reached", "cancelled"] = "active",
    as_of_date: date | None = None,
) -> SavingsPlanCalculation:
    today = as_of_date or date.today()
    remaining = max(target_minor - current_minor, 0)

    if goal_status == "cancelled":
        return SavingsPlanCalculation(0, 0, None, "cancelled")
    if goal_status == "reached" or remaining == 0:
        return SavingsPlanCalculation(0, 0, today, "reached")

    if planning_mode == "target_date":
        if target_date is None:
            return SavingsPlanCalculation(0, 0, None, "incomplete")
        monthly = calculate_monthly_required(
            target_minor,
            current_minor,
            target_date,
            as_of_date=today,
        )
        month_start = date(today.year, today.month, 1)
        plan_status: PlanningStatus = "overdue" if target_date < month_start else "on_track"
        return SavingsPlanCalculation(
            monthly_required_minor=monthly,
            effective_monthly_reserve_minor=monthly if include_in_planning else 0,
            projected_completion_date=target_date,
            planning_status=plan_status,
        )

    if monthly_contribution_minor is None or monthly_contribution_minor <= 0:
        return SavingsPlanCalculation(0, 0, None, "incomplete")
    return SavingsPlanCalculation(
        monthly_required_minor=monthly_contribution_minor,
        effective_monthly_reserve_minor=(
            monthly_contribution_minor if include_in_planning else 0
        ),
        projected_completion_date=calculate_projected_completion_date(
            target_minor,
            current_minor,
            monthly_contribution_minor,
            as_of_date=today,
        ),
        planning_status="on_track",
    )


def validate_included_plan(
    *,
    planning_mode: PlanningMode,
    target_date: date | None,
    monthly_contribution_minor: int | None,
    include_in_planning: bool,
) -> None:
    if not include_in_planning:
        return
    if planning_mode == "target_date" and target_date is None:
        raise ValueError("Ein einbezogener Spartopf im Datumsmodus braucht ein Zieldatum.")
    if planning_mode == "monthly_amount" and (
        monthly_contribution_minor is None or monthly_contribution_minor <= 0
    ):
        raise ValueError("Ein einbezogener Spartopf im Monatsmodus braucht einen positiven Betrag.")


async def fetch_included_goal_reserves(
    session: AsyncSession,
    household_id: UUID,
    *,
    as_of_date: date,
) -> GoalReserveSummary:
    """Summiert nur aktive, einbezogene und noch nicht erreichte Spartoepfe."""
    result = await session.execute(
        text(
            "select g.id, g.target_amount_minor, g.starting_amount_minor, g.target_date, "
            "g.planning_mode, g.monthly_contribution_minor, g.include_in_planning, g.status, "
            "coalesce(sum(case when t.id is not null and not t.is_archived "
            "then ts.amount_minor else 0 end), 0) as deposits_minor "
            "from savings_goals g "
            "left join transaction_splits ts on ts.goal_id = g.id "
            "and ts.household_id = g.household_id "
            "left join transactions t on t.id = ts.transaction_id "
            "where g.household_id = :household_id and g.include_in_planning "
            "and g.status = 'active' "
            "group by g.id, g.target_amount_minor, g.starting_amount_minor, g.target_date, "
            "g.planning_mode, g.monthly_contribution_minor, g.include_in_planning, g.status"
        ),
        {"household_id": str(household_id)},
    )

    total = 0
    count = 0
    for row in result:
        current_minor = int(row.starting_amount_minor) + int(row.deposits_minor)
        calculation = calculate_savings_plan(
            target_minor=int(row.target_amount_minor),
            current_minor=current_minor,
            planning_mode=row.planning_mode,
            target_date=row.target_date,
            monthly_contribution_minor=row.monthly_contribution_minor,
            include_in_planning=bool(row.include_in_planning),
            goal_status=row.status,
            as_of_date=as_of_date,
        )
        if calculation.effective_monthly_reserve_minor > 0:
            total += calculation.effective_monthly_reserve_minor
            count += 1
    return GoalReserveSummary(total_monthly_minor=total, goal_count=count)
