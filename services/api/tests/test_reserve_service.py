from datetime import date

from app.services.reserve_service import (
    fixed_recommendation,
    reserve_status,
    resolve_contract_due,
)


def test_august_to_march_has_seven_deposit_opportunities() -> None:
    assert fixed_recommendation(
        target_minor=150_000,
        opening_balance_minor=0,
        due_date=date(2027, 3, 15),
        selected_month=date(2026, 8, 1),
    ) == 21_429


def test_due_month_requires_the_complete_remaining_amount() -> None:
    assert fixed_recommendation(
        target_minor=70_000,
        opening_balance_minor=12_500,
        due_date=date(2026, 8, 28),
        selected_month=date(2026, 8, 1),
    ) == 57_500


def test_past_due_in_current_month_advances_without_writing() -> None:
    due = resolve_contract_due(
        next_due_date=date(2026, 8, 5),
        frequency="quarterly",
        due_rule="day_of_month",
        due_day=5,
        reserve_started_on=date(2026, 7, 1),
        selected_month=date(2026, 8, 1),
        today=date(2026, 8, 10),
    )
    assert due == date(2026, 11, 5)


def test_positive_deposit_wins_over_skip_status() -> None:
    assert (
        reserve_status(recommended_minor=10_000, deposited_minor=2_500, skipped=True)
        == "partial"
    )
    assert reserve_status(recommended_minor=10_000, deposited_minor=10_000, skipped=True) == "done"
    assert reserve_status(recommended_minor=10_000, deposited_minor=0, skipped=True) == "skipped"
