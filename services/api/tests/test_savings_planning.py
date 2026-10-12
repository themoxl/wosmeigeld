from datetime import date

import pytest

from app.services.savings_planning import (
    calculate_monthly_required,
    calculate_projected_completion_date,
    calculate_savings_plan,
    validate_included_plan,
)


def test_date_mode_rounds_up_and_uses_deterministic_month_distance() -> None:
    # August 2026 -> Juni 2027: zehn Raten, die Restcents werden aufgerundet.
    assert (
        calculate_monthly_required(
            150_001,
            0,
            date(2027, 6, 1),
            as_of_date=date(2026, 8, 9),
        )
        == 15_001
    )
    # Ueberfaellig: volle Restluecke als Nachholrate fuer einen Monat.
    assert (
        calculate_monthly_required(
            70_000,
            20_000,
            date(2025, 1, 1),
            as_of_date=date(2026, 8, 9),
        )
        == 50_000
    )


def test_monthly_mode_projects_completion_month_without_float_math() -> None:
    assert calculate_projected_completion_date(
        70_000,
        0,
        2_500,
        as_of_date=date(2026, 8, 9),
    ) == date(2028, 12, 1)


def test_reached_cancelled_and_excluded_goals_reserve_zero() -> None:
    common = {
        "target_minor": 100_000,
        "current_minor": 20_000,
        "planning_mode": "monthly_amount",
        "target_date": None,
        "monthly_contribution_minor": 5_000,
        "as_of_date": date(2026, 8, 9),
    }
    excluded = calculate_savings_plan(**common, include_in_planning=False)
    reached = calculate_savings_plan(
        **{**common, "current_minor": 100_000}, include_in_planning=True
    )
    cancelled = calculate_savings_plan(
        **common, include_in_planning=True, goal_status="cancelled"
    )
    assert excluded.effective_monthly_reserve_minor == 0
    assert reached.effective_monthly_reserve_minor == 0
    assert reached.planning_status == "reached"
    assert cancelled.effective_monthly_reserve_minor == 0
    assert cancelled.planning_status == "cancelled"


@pytest.mark.parametrize(
    ("mode", "target_date", "monthly"),
    [
        ("target_date", None, None),
        ("monthly_amount", None, None),
        ("monthly_amount", None, 0),
    ],
)
def test_included_plan_must_be_complete(
    mode: str,
    target_date: date | None,
    monthly: int | None,
) -> None:
    with pytest.raises(ValueError):
        validate_included_plan(
            planning_mode=mode,  # type: ignore[arg-type]
            target_date=target_date,
            monthly_contribution_minor=monthly,
            include_in_planning=True,
        )
