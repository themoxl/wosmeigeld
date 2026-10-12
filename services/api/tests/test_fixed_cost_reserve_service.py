from datetime import date

from app.services.fixed_cost_reserve_service import calculate_fixed_cost_reserve


def _calculate(**overrides: object):
    values: dict[str, object] = {
        "contract_id": "contract-1",
        "contract_name": "Jahresversicherung",
        "direction": "expense",
        "frequency": "yearly",
        "period_amount_minor": 120_000,
        "next_due_date_value": date(2027, 8, 15),
        "due_rule": "day_of_month",
        "due_day": 15,
        "reserve_enabled": True,
        "reserve_started_on": date(2026, 8, 15),
        "reserve_starting_amount_minor": 0,
        "status": "active",
        "starts_on": None,
        "ends_on": None,
        "budget_month": date(2026, 9, 1),
    }
    values.update(overrides)
    return calculate_fixed_cost_reserve(**values)  # type: ignore[arg-type]


def test_complete_yearly_cycle_is_100_euro_per_month() -> None:
    result = _calculate()
    assert result is not None
    assert result.monthly_reserve_minor == 10_000
    assert result.accrued_minor == 10_000
    assert result.months_remaining == 11


def test_first_cycle_catches_up_over_five_months() -> None:
    result = _calculate(
        reserve_started_on=date(2027, 3, 10),
        budget_month=date(2027, 3, 1),
    )
    assert result is not None
    assert result.is_first_cycle is True
    assert result.monthly_reserve_minor == 24_000
    assert result.accrued_minor == 0
    assert result.months_remaining == 5


def test_first_cycle_uses_existing_starting_amount() -> None:
    result = _calculate(
        reserve_started_on=date(2027, 3, 10),
        reserve_starting_amount_minor=30_000,
        budget_month=date(2027, 3, 1),
    )
    assert result is not None
    assert result.monthly_reserve_minor == 18_000
    assert result.accrued_minor == 30_000


def test_quarterly_first_cycle_over_two_months() -> None:
    result = _calculate(
        frequency="quarterly",
        period_amount_minor=30_000,
        reserve_started_on=date(2027, 6, 3),
        budget_month=date(2027, 6, 1),
    )
    assert result is not None
    assert result.monthly_reserve_minor == 15_000
    assert result.months_remaining == 2


def test_due_month_is_covered_and_switches_to_sustainable_next_cycle_rate() -> None:
    result = _calculate(
        reserve_started_on=date(2027, 3, 10),
        budget_month=date(2027, 8, 1),
    )
    assert result is not None
    assert result.due_this_month is True
    assert result.is_covered is True
    assert result.accrued_minor == 120_000
    assert result.remaining_minor == 0
    # The 240 EUR catch-up ends with this invoice; the forward planning rate
    # is the normal 100 EUR for the next full yearly cycle.
    assert result.monthly_reserve_minor == 10_000


def test_later_cycle_does_not_repeat_initial_balance() -> None:
    result = _calculate(
        next_due_date_value=date(2028, 8, 15),
        reserve_started_on=date(2027, 3, 10),
        reserve_starting_amount_minor=30_000,
        budget_month=date(2027, 9, 1),
    )
    assert result is not None
    assert result.is_first_cycle is False
    assert result.monthly_reserve_minor == 10_000
    assert result.accrued_minor == 10_000


def test_ineligible_or_inactive_contracts_do_not_contribute() -> None:
    assert _calculate(direction="income") is None
    assert _calculate(frequency="monthly") is None
    assert _calculate(status="paused") is None
    assert _calculate(status="ended") is None
    assert _calculate(reserve_enabled=False) is None
    assert _calculate(starts_on=date(2027, 9, 1)) is None
    assert _calculate(ends_on=date(2026, 8, 31)) is None
