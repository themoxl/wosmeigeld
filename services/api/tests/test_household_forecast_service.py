"""Reiner Rechenkern der Liquiditaetsvorschau - ohne Datenbank, mit
erfundenen Eingaben und immer explizitem `as_of`
(docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 14).
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.schemas.forecast import ForecastChange
from app.services.household_forecast_service import (
    ForecastContext,
    ForecastContractTerm,
    ForecastInput,
    ScheduledFlow,
    VariableMonth,
    apply_changes,
    calculate_forecast,
    compare_forecasts,
    contribution_of,
    group_upcoming,
    tax_class_hint_applies,
)

AS_OF = date(2026, 8, 13)


def _input(**overrides: Any) -> ForecastInput:
    base: dict[str, Any] = {
        "as_of": AS_OF,
        "horizon_months": 3,
        "daily_until": date(2026, 11, 13),
        "opening_liquidity_minor": 100_000,
        "scheduled": (),
        "variable_monthly": (),
        "contract_terms": (),
        "safety_buffer_minor": 0,
        "income_monthly_minor": 0,
        "fixed_monthly_minor": 0,
        "context": ForecastContext(last_booking_on=AS_OF, income_count=1),
    }
    base.update(overrides)
    return ForecastInput(**base)


def _flow(
    on: date, amount: int, label: str = "Posten", source_id: UUID | None = None
) -> ScheduledFlow:
    return ScheduledFlow(
        on=on,
        amount_minor=amount,
        label=label,
        source_kind="contract" if source_id is not None else "simulation",
        source_id=source_id,
    )


# ---------------------------------------------------------------------------
# Grundrechnung
# ---------------------------------------------------------------------------


def test_balance_walks_day_by_day_and_horizon_is_inclusive() -> None:
    data = _input(
        horizon_months=1,
        daily_until=date(2026, 9, 13),
        scheduled=(_flow(date(2026, 8, 20), -25_000),),
    )
    result = calculate_forecast(data)

    assert result.daily[0].on == AS_OF
    assert result.daily[-1].on == date(2026, 9, 13)
    assert len(result.daily) == 32
    assert result.daily[0].balance_minor == 100_000
    assert result.daily[7].on == date(2026, 8, 20)
    assert result.daily[7].opening_balance_minor == 100_000
    assert result.daily[7].balance_minor == 75_000
    assert result.metrics.lowest_balance_minor == 75_000


def test_variable_expenses_are_spread_over_the_days_of_the_month() -> None:
    data = _input(
        horizon_months=1,
        daily_until=date(2026, 9, 13),
        variable_monthly=(
            VariableMonth(year=2026, month=8, planned_minor=31_000, booked_minor=0),
            VariableMonth(year=2026, month=9, planned_minor=30_000, booked_minor=0),
        ),
    )
    result = calculate_forecast(data)

    # Laufender Monat: 31.000 auf 19 verbleibende Tage (13. bis 31.).
    assert result.daily[0].variable_minor == 1_632
    assert result.monthly[0].variable_minor == 1_632 * 19
    # Folgemonat: voller Ansatz auf 30 Tage, geliefert werden 13 Tage davon.
    assert result.daily[19].variable_minor == 1_000
    assert result.monthly[1].variable_minor == 13_000


def test_current_month_only_forecasts_the_remainder() -> None:
    """Was bis heute ausgegeben wurde, steckt bereits im Startsaldo
    (kritische Regel 2)."""
    spent = _input(
        horizon_months=1,
        daily_until=date(2026, 9, 13),
        variable_monthly=(
            VariableMonth(year=2026, month=8, planned_minor=31_000, booked_minor=31_000),
        ),
    )
    untouched = _input(
        horizon_months=1,
        daily_until=date(2026, 9, 13),
        variable_monthly=(
            VariableMonth(year=2026, month=8, planned_minor=31_000, booked_minor=0),
        ),
    )
    assert calculate_forecast(spent).monthly[0].variable_minor == 0
    assert calculate_forecast(untouched).monthly[0].variable_minor > 0
    # Ueberbuchung darf keine negative Ausgabe erzeugen.
    over = _input(
        horizon_months=1,
        daily_until=date(2026, 9, 13),
        variable_monthly=(
            VariableMonth(year=2026, month=8, planned_minor=31_000, booked_minor=99_000),
        ),
    )
    assert calculate_forecast(over).monthly[0].variable_minor == 0


def test_monthly_rows_split_income_fixed_and_variable() -> None:
    data = _input(
        horizon_months=1,
        daily_until=date(2026, 9, 13),
        scheduled=(
            _flow(date(2026, 8, 28), 200_000, "Gehalt"),
            _flow(date(2026, 8, 20), -50_000, "Miete"),
        ),
        variable_monthly=(
            VariableMonth(year=2026, month=8, planned_minor=19_000, booked_minor=0),
        ),
    )
    result = calculate_forecast(data)
    august = result.monthly[0]
    assert august.income_minor == 200_000
    assert august.fixed_minor == 50_000
    assert august.variable_minor == 19_000
    assert august.net_minor == 131_000
    assert august.closing_balance_minor == 231_000


def test_metrics_report_shortfall_gap_and_recovery() -> None:
    data = _input(
        horizon_months=2,
        daily_until=date(2026, 10, 13),
        opening_liquidity_minor=10_000,
        safety_buffer_minor=50_000,
        scheduled=(
            _flow(date(2026, 8, 20), -30_000, "Grosse Rechnung"),
            _flow(date(2026, 8, 25), 100_000, "Gehalt"),
        ),
    )
    metrics = calculate_forecast(data).metrics

    assert metrics.first_below_buffer_on == AS_OF
    assert metrics.first_negative_on == date(2026, 8, 20)
    assert metrics.lowest_balance_minor == -20_000
    assert metrics.lowest_balance_on == date(2026, 8, 20)
    assert metrics.recovery_on == date(2026, 8, 25)
    assert metrics.days_below_zero == 5
    assert metrics.needed_against_negative_minor == 20_000
    assert metrics.needed_for_buffer_minor == 70_000


def test_healthy_run_reports_no_gap_and_no_negative_needs() -> None:
    data = _input(safety_buffer_minor=10_000)
    metrics = calculate_forecast(data).metrics
    assert metrics.first_negative_on is None
    assert metrics.first_below_buffer_on is None
    assert metrics.recovery_on is None
    assert metrics.needed_against_negative_minor == 0
    assert metrics.needed_for_buffer_minor == 0


def test_cause_is_the_largest_outflow_day_within_thirty_days() -> None:
    data = _input(
        horizon_months=2,
        daily_until=date(2026, 10, 13),
        opening_liquidity_minor=100_000,
        scheduled=(
            # Weit vor dem Fenster - darf nicht gewaehlt werden.
            _flow(date(2026, 8, 14), -1_000, "Alt"),
            _flow(date(2026, 9, 1), -60_000, "Miete"),
            _flow(date(2026, 9, 1), -70_000, "Versicherung"),
            _flow(date(2026, 9, 20), -20_000, "Klein"),
        ),
    )
    result = calculate_forecast(data)
    assert result.metrics.first_negative_on == date(2026, 9, 1)
    assert result.cause is not None
    assert result.cause.on == date(2026, 9, 1)
    assert result.cause.total_minor == -130_000
    assert {flow.label for flow in result.cause.flows} == {"Miete", "Versicherung"}


def test_cause_is_none_without_shortfall() -> None:
    assert calculate_forecast(_input()).cause is None


def test_reference_metrics_use_normalised_monthly_values() -> None:
    data = _input(
        income_monthly_minor=300_000,
        fixed_monthly_minor=150_000,
        opening_liquidity_minor=450_000,
        variable_monthly=(
            VariableMonth(year=2026, month=8, planned_minor=60_000),
            VariableMonth(year=2026, month=9, planned_minor=60_000),
            VariableMonth(year=2026, month=10, planned_minor=60_000),
            VariableMonth(year=2026, month=11, planned_minor=60_000),
        ),
    )
    metrics = calculate_forecast(data).metrics
    assert metrics.variable_monthly_minor == 60_000
    assert metrics.average_monthly_surplus_minor == 90_000
    assert metrics.savings_rate_percent == 30.0
    assert metrics.fixed_cost_ratio_percent == 50.0
    assert metrics.reserve_without_income_months == 3.0


def test_bound_reserve_is_reported_but_never_subtracted() -> None:
    """`reserve_pots` hat keinen Kontobezug (kritische Regel 5)."""
    data = _input(bound_reserve_minor=34_000)
    result = calculate_forecast(data)
    assert result.bound_reserve_minor == 34_000
    assert result.opening_liquidity_minor == 100_000
    assert result.daily[0].balance_minor == 100_000


def test_granularity_month_omits_the_daily_series_but_keeps_the_metrics() -> None:
    data = _input(
        scheduled=(_flow(date(2026, 9, 1), -200_000),),
    )
    day = calculate_forecast(data, granularity="day")
    month = calculate_forecast(data, granularity="month")
    assert month.daily == ()
    assert month.monthly == day.monthly
    assert month.metrics == day.metrics


def test_daily_until_truncates_the_delivered_series_but_not_the_metrics() -> None:
    data = _input(
        horizon_months=6,
        daily_until=date(2026, 8, 20),
        scheduled=(_flow(date(2026, 12, 1), -500_000),),
    )
    result = calculate_forecast(data)
    assert result.daily[-1].on == date(2026, 8, 20)
    assert result.metrics.first_negative_on == date(2026, 12, 1)
    assert len(result.monthly) == 7


# ---------------------------------------------------------------------------
# Warnungen und Datenqualitaet
# ---------------------------------------------------------------------------


def _codes(data: ForecastInput) -> set[str]:
    return {warning.code for warning in calculate_forecast(data).warnings}


def test_stale_account_data_and_quality_level() -> None:
    data = _input(context=ForecastContext(last_booking_on=date(2026, 7, 1), income_count=1))
    result = calculate_forecast(data)
    assert "stale_account_data" in {w.code for w in result.warnings}
    assert result.quality.level == "insufficient"


def test_no_income_sources_is_critical() -> None:
    data = _input(context=ForecastContext(last_booking_on=AS_OF, income_count=0))
    result = calculate_forecast(data)
    warning = next(w for w in result.warnings if w.code == "no_income_sources")
    assert warning.level == "critical"
    assert result.quality.level == "insufficient"


def test_income_below_booked_average() -> None:
    context = ForecastContext(
        last_booking_on=AS_OF, income_count=1, booked_income_monthly_minor=300_000
    )
    assert "income_below_booked" in _codes(_input(context=context, income_monthly_minor=200_000))
    assert "income_below_booked" not in _codes(
        _input(context=context, income_monthly_minor=290_000)
    )


def test_variable_basis_warnings_are_mutually_exclusive() -> None:
    from_history = ForecastContext(
        last_booking_on=AS_OF, income_count=1, variable_from_history=True
    )
    without = ForecastContext(
        last_booking_on=AS_OF,
        income_count=1,
        variable_from_history=True,
        variable_without_basis=True,
    )
    assert _codes(_input(context=from_history)) == {"variable_from_history"}
    assert _codes(_input(context=without)) == {"no_variable_basis"}


def test_quality_is_good_only_without_reservations() -> None:
    good = _input(context=ForecastContext(last_booking_on=AS_OF, income_count=1))
    limited = _input(
        context=ForecastContext(
            last_booking_on=AS_OF, income_count=1, excluded_account_names=("Tagesgeld",)
        )
    )
    assert calculate_forecast(good).quality.level == "good"
    assert calculate_forecast(limited).quality.level == "limited"


def test_horizon_beyond_contracts_is_informational() -> None:
    data = _input(
        context=ForecastContext(
            last_booking_on=AS_OF, income_count=1, contracts_ending_in_horizon=2
        )
    )
    warning = next(
        w for w in calculate_forecast(data).warnings if w.code == "horizon_beyond_contracts"
    )
    assert warning.level == "info"
    assert warning.context["contract_count"] == 2


def test_below_zero_and_below_buffer_carry_the_missing_amount() -> None:
    data = _input(
        opening_liquidity_minor=0,
        safety_buffer_minor=50_000,
        scheduled=(_flow(date(2026, 8, 20), -10_000),),
    )
    warnings = {w.code: w for w in calculate_forecast(data).warnings}
    assert warnings["below_zero"].level == "critical"
    assert warnings["below_zero"].context["needed_minor"] == 10_000
    assert warnings["below_buffer"].level == "watch"
    assert warnings["below_buffer"].context["needed_minor"] == 60_000


# ---------------------------------------------------------------------------
# Naechste Zahlungen
# ---------------------------------------------------------------------------


def test_upcoming_groups_by_day_and_marks_heavy_days() -> None:
    data = _input(
        scheduled=(
            _flow(date(2026, 8, 20), -12_000, "Versicherung"),
            _flow(date(2026, 8, 20), -15_000, "Strom"),
            _flow(date(2026, 8, 22), -2_000, "Streaming"),
            _flow(date(2026, 8, 25), -120_000, "Miete"),
        ),
    )
    groups = group_upcoming(calculate_forecast(data), until=date(2026, 8, 31))
    assert [group.on for group in groups] == [
        date(2026, 8, 20),
        date(2026, 8, 22),
        date(2026, 8, 25),
    ]
    # Zwei Posten ab 100 EUR.
    assert groups[0].is_heavy is True
    assert groups[0].total_minor == -27_000
    assert groups[0].balance_after_minor == 73_000
    assert groups[1].is_heavy is False
    # Eine einzelne Position ab 1.000 EUR Tagessumme.
    assert groups[2].is_heavy is True


def test_event_marker_follows_the_day_movement() -> None:
    data = _input(
        scheduled=(
            _flow(date(2026, 8, 20), -30_000, "Gross"),
            _flow(date(2026, 8, 22), -1_000, "Klein"),
        ),
    )
    days = {day.on: day for day in calculate_forecast(data).daily}
    assert days[date(2026, 8, 20)].is_event is True
    assert days[date(2026, 8, 22)].is_event is False


# ---------------------------------------------------------------------------
# Was-waere-wenn
# ---------------------------------------------------------------------------

CONTRACT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _simulation_base() -> ForecastInput:
    return _input(
        horizon_months=6,
        daily_until=date(2027, 2, 13),
        opening_liquidity_minor=100_000,
        scheduled=(
            _flow(date(2026, 8, 28), 300_000, "Gehalt", CONTRACT_ID),
            _flow(date(2026, 9, 28), 300_000, "Gehalt", CONTRACT_ID),
            _flow(date(2026, 10, 28), 300_000, "Gehalt", CONTRACT_ID),
            _flow(date(2026, 11, 28), 300_000, "Gehalt", CONTRACT_ID),
            _flow(date(2026, 12, 28), 300_000, "Gehalt", CONTRACT_ID),
            _flow(date(2027, 1, 28), 300_000, "Gehalt", CONTRACT_ID),
        ),
        contract_terms=(
            ForecastContractTerm(
                contract_id=CONTRACT_ID,
                label="Gehalt",
                direction="income",
                amount_minor=300_000,
                frequency="monthly",
            ),
        ),
        income_monthly_minor=300_000,
        variable_monthly=(
            VariableMonth(year=2026, month=month, planned_minor=60_000)
            for month in (8, 9, 10, 11, 12)
        ),
    )


def test_simulation_without_changes_equals_base_field_by_field() -> None:
    """Leitsatz 2: ein Szenario ist dieselbe Vorschau mit Aenderungen davor."""
    base = _simulation_base()
    assert apply_changes(base, ()) == base

    base_result = calculate_forecast(base)
    simulated = calculate_forecast(apply_changes(base, ()))
    assert simulated == base_result
    for field in type(base_result.metrics).model_fields:
        assert getattr(simulated.metrics, field) == getattr(base_result.metrics, field), field

    comparison = compare_forecasts(base_result, simulated)
    assert comparison.surplus_delta_minor == 0
    assert comparison.balance_delta_24_months_minor == 0
    assert comparison.first_negative_shift_days is None
    assert comparison.resolves_shortfall is False


def test_negative_delta_expresses_a_salary_cut() -> None:
    base = _simulation_base()
    change = ForecastChange(
        change_type="income_change",
        target_contract_id=CONTRACT_ID,
        label="Gehalt Person A",
        amount_mode="delta",
        amount_minor=-50_000,
        frequency="monthly",
        starts_on=date(2026, 10, 1),
    )
    changed = apply_changes(base, (change,))

    amounts = {flow.on: flow.amount_minor for flow in changed.scheduled}
    assert amounts[date(2026, 8, 28)] == 300_000
    assert amounts[date(2026, 9, 28)] == 300_000
    assert amounts[date(2026, 10, 28)] == 250_000
    assert amounts[date(2027, 1, 28)] == 250_000
    assert changed.income_monthly_minor == 250_000
    assert calculate_forecast(changed).metrics.average_monthly_surplus_minor < (
        calculate_forecast(base).metrics.average_monthly_surplus_minor
    )


def test_negative_delta_on_an_expense_reduces_the_cost() -> None:
    """Ein negativer Delta muss eine Kostenreduktion ausdruecken (Plan 6.2) -
    reine Vorzeichenaddition machte die Ausgabe stattdessen teurer."""
    expense_id = uuid4()
    base = _input(
        scheduled=(_flow(date(2026, 9, 1), -50_000, "Miete", expense_id),),
        contract_terms=(
            ForecastContractTerm(
                contract_id=expense_id,
                label="Miete",
                direction="expense",
                amount_minor=50_000,
                frequency="monthly",
            ),
        ),
        fixed_monthly_minor=50_000,
    )
    change = ForecastChange(
        change_type="expense_change",
        target_contract_id=expense_id,
        label="Miete",
        amount_mode="delta",
        amount_minor=-10_000,
        frequency="monthly",
        starts_on=date(2026, 9, 1),
    )
    changed = apply_changes(base, (change,))
    assert changed.scheduled[0].amount_minor == -40_000
    assert changed.fixed_monthly_minor == 40_000


def test_change_leaves_earlier_due_dates_untouched() -> None:
    base = _simulation_base()
    change = ForecastChange(
        change_type="income_change",
        target_contract_id=CONTRACT_ID,
        label="Gehalt",
        amount_mode="absolute",
        amount_minor=400_000,
        frequency="monthly",
        starts_on=date(2026, 11, 1),
    )
    amounts = {flow.on: flow.amount_minor for flow in apply_changes(base, (change,)).scheduled}
    assert amounts[date(2026, 10, 28)] == 300_000
    assert amounts[date(2026, 11, 28)] == 400_000


def test_percent_mode_scales_the_amount() -> None:
    base = _simulation_base()
    change = ForecastChange(
        change_type="income_change",
        target_contract_id=CONTRACT_ID,
        label="Gehalt",
        amount_mode="percent",
        amount_percent=Decimal("10"),
        frequency="monthly",
        starts_on=date(2026, 8, 1),
    )
    changed = apply_changes(base, (change,))
    assert changed.scheduled[0].amount_minor == 330_000
    assert changed.income_monthly_minor == 330_000


def test_end_removes_all_due_dates_from_starts_on() -> None:
    base = _simulation_base()
    change = ForecastChange(
        change_type="income_end",
        target_contract_id=CONTRACT_ID,
        label="Gehalt endet",
        frequency="monthly",
        starts_on=date(2026, 11, 1),
    )
    changed = apply_changes(base, (change,))
    assert [flow.on for flow in changed.scheduled] == [
        date(2026, 8, 28),
        date(2026, 9, 28),
        date(2026, 10, 28),
    ]
    assert changed.income_monthly_minor == 0


def test_add_creates_a_series_over_the_contract_due_date_logic() -> None:
    base = _simulation_base()
    change = ForecastChange(
        change_type="income_add",
        label="Person B, Teilzeit 25 h",
        amount_mode="absolute",
        amount_minor=145_000,
        frequency="monthly",
        due_day=15,
        income_kind="salary",
        starts_on=date(2026, 10, 1),
    )
    changed = apply_changes(base, (change,))
    added = [flow.on for flow in changed.scheduled if flow.label == "Person B, Teilzeit 25 h"]
    assert added[:3] == [date(2026, 10, 15), date(2026, 11, 15), date(2026, 12, 15)]
    assert changed.income_monthly_minor == 445_000


def test_one_off_creates_exactly_one_flow() -> None:
    base = _simulation_base()
    change = ForecastChange(
        change_type="one_off",
        label="Autoreparatur",
        amount_mode="absolute",
        amount_minor=-80_000,
        frequency="once",
        starts_on=date(2026, 10, 5),
    )
    changed = apply_changes(base, (change,))
    added = [flow for flow in changed.scheduled if flow.label == "Autoreparatur"]
    assert len(added) == 1
    assert added[0].on == date(2026, 10, 5)
    # Eine Einmalzahlung hat keine Monatswirkung.
    assert changed.fixed_monthly_minor == base.fixed_monthly_minor


def test_a_new_recurring_expense_never_raises_the_variable_budget() -> None:
    """Der Doppelzaehlungsfehler des Prototyps (kritische Regel 4): eine neue
    wiederkehrende Ausgabe ist bereits ein Flow."""
    base = _simulation_base()
    change = ForecastChange(
        change_type="expense_add",
        label="Kinderbetreuung",
        amount_mode="absolute",
        amount_minor=-28_000,
        frequency="monthly",
        due_day=1,
        starts_on=date(2026, 10, 1),
    )
    changed = apply_changes(base, (change,))
    assert changed.variable_monthly == base.variable_monthly
    base_metrics = calculate_forecast(base).metrics
    changed_metrics = calculate_forecast(changed).metrics
    assert changed_metrics.variable_monthly_minor == base_metrics.variable_monthly_minor
    # Genau eine Wirkung: die Fixkosten steigen, der variable Ansatz nicht.
    assert changed_metrics.fixed_monthly_minor - base_metrics.fixed_monthly_minor == 28_000
    assert (
        base_metrics.average_monthly_surplus_minor
        - changed_metrics.average_monthly_surplus_minor
    ) == 28_000


def test_changes_stack_in_order() -> None:
    base = _simulation_base()
    first = ForecastChange(
        change_type="income_change",
        target_contract_id=CONTRACT_ID,
        label="Gehalt",
        amount_mode="delta",
        amount_minor=20_000,
        frequency="monthly",
        starts_on=date(2026, 9, 1),
    )
    second = ForecastChange(
        change_type="income_change",
        target_contract_id=CONTRACT_ID,
        label="Gehalt",
        amount_mode="delta",
        amount_minor=10_000,
        frequency="monthly",
        starts_on=date(2026, 12, 1),
    )
    amounts = {
        flow.on: flow.amount_minor for flow in apply_changes(base, (first, second)).scheduled
    }
    assert amounts[date(2026, 8, 28)] == 300_000
    assert amounts[date(2026, 9, 28)] == 320_000
    assert amounts[date(2026, 12, 28)] == 330_000
    assert amounts[date(2027, 1, 28)] == 330_000


def test_contribution_is_computed_from_a_run_with_only_that_change() -> None:
    base = _input(
        horizon_months=3,
        daily_until=date(2026, 11, 13),
        opening_liquidity_minor=0,
        scheduled=(_flow(date(2026, 9, 1), -100_000, "Miete"),),
    )
    base_result = calculate_forecast(base)
    assert base_result.metrics.first_negative_on == date(2026, 9, 1)

    rescue = ForecastChange(
        change_type="income_add",
        label="Nebenjob",
        amount_mode="absolute",
        amount_minor=150_000,
        frequency="monthly",
        due_day=1,
        income_kind="salary",
        starts_on=date(2026, 8, 20),
    )
    too_small = ForecastChange(
        change_type="one_off",
        label="Kleiner Zuschuss",
        amount_mode="absolute",
        amount_minor=20_000,
        frequency="once",
        starts_on=date(2026, 8, 20),
    )

    strong = contribution_of(base, base_result, rescue, 0)
    assert strong.change_index == 0
    assert strong.monthly_minor == 150_000
    assert strong.resolves_shortfall is True
    assert strong.first_negative_on is None
    assert strong.shift_days is None

    weak = contribution_of(base, base_result, too_small, 1)
    assert weak.resolves_shortfall is False
    assert weak.first_negative_on == date(2026, 9, 1)
    assert weak.shift_days == 0


def test_tax_class_hint_only_for_a_second_salary_of_another_person() -> None:
    """Seit Stufe 6 haengen Art und Person am Vertrag - der Hinweis fragt
    danach, statt sich mit "irgendeine Einnahme existiert" zu begnuegen."""
    person_a_id = uuid4()
    person_b_id = uuid4()
    base = _simulation_base()
    salary_base = base.model_copy(
        update={
            "contract_terms": tuple(
                term.model_copy(update={"income_kind": "salary", "person_id": person_a_id})
                for term in base.contract_terms
            )
        }
    )
    salary = ForecastChange(
        change_type="income_add",
        label="Person B",
        amount_mode="absolute",
        amount_minor=145_000,
        frequency="monthly",
        income_kind="salary",
        person_id=person_b_id,
        starts_on=date(2026, 10, 1),
    )
    other = ForecastChange(
        change_type="income_add",
        label="Miete Einnahme",
        amount_mode="absolute",
        amount_minor=50_000,
        frequency="monthly",
        income_kind="rental",
        starts_on=date(2026, 10, 1),
    )
    assert tax_class_hint_applies(salary_base, (salary,)) is True
    assert tax_class_hint_applies(salary_base, (other,)) is False

    # Zweitjob DERSELBEN Person: die Steuerklassenkombination bleibt, wie sie ist.
    second_job = salary.model_copy(update={"person_id": person_a_id})
    assert tax_class_hint_applies(salary_base, (second_job,)) is False

    # Ein Gehalt ohne gepflegte Person zaehlt weiter als "andere Person" - ein
    # leeres Feld ist keine Aussage ueber Gleichheit.
    unassigned = salary_base.model_copy(
        update={
            "contract_terms": tuple(
                term.model_copy(update={"person_id": None})
                for term in salary_base.contract_terms
            )
        }
    )
    assert tax_class_hint_applies(unassigned, (salary,)) is True

    # Bestand vor Stufe 6: ohne `income_kind` ist nicht bekannt, dass es sich
    # um ein Gehalt handelt - dann wird auch nichts behauptet.
    assert tax_class_hint_applies(base, (salary,)) is False
    without_income = base.model_copy(update={"contract_terms": ()})
    assert tax_class_hint_applies(without_income, (salary,)) is False


# ---------------------------------------------------------------------------
# Validierung des Aenderungsmodells
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        # target_contract_id nur bei *_change und *_end
        {"change_type": "income_add", "target_contract_id": CONTRACT_ID, "amount_mode": "absolute",
         "amount_minor": 1_000, "income_kind": "salary"},
        {"change_type": "income_change", "amount_mode": "absolute", "amount_minor": 1_000},
        # amount_mode bei *_end verboten
        {"change_type": "income_end", "target_contract_id": CONTRACT_ID,
         "amount_mode": "absolute", "amount_minor": 1_000},
        # frequency=once nur bei one_off
        {"change_type": "expense_add", "amount_mode": "absolute", "amount_minor": -1_000,
         "frequency": "once"},
        # income_kind nur bei income_add, dort aber Pflicht
        {"change_type": "income_add", "amount_mode": "absolute", "amount_minor": 1_000},
        {"change_type": "expense_add", "amount_mode": "absolute", "amount_minor": -1_000,
         "income_kind": "salary"},
        # Vorzeichen muss bei absolute zur Richtung passen
        {"change_type": "income_add", "amount_mode": "absolute", "amount_minor": -1_000,
         "income_kind": "salary"},
        {"change_type": "expense_add", "amount_mode": "absolute", "amount_minor": 1_000},
        # percent verlangt amount_percent und verbietet amount_minor
        {"change_type": "one_off", "amount_mode": "percent", "amount_minor": 1_000,
         "frequency": "once"},
    ],
)
def test_invalid_changes_are_rejected(payload: dict[str, Any]) -> None:
    body: dict[str, Any] = {"label": "Test", "starts_on": date(2026, 9, 1)}
    body.update(payload)
    with pytest.raises(ValueError):
        ForecastChange(**body)


def test_ends_on_must_not_precede_starts_on() -> None:
    with pytest.raises(ValueError):
        ForecastChange(
            change_type="one_off",
            label="Test",
            amount_mode="absolute",
            amount_minor=-1_000,
            frequency="once",
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 8, 1),
        )


def test_add_respects_ends_on() -> None:
    base = _simulation_base()
    change = ForecastChange(
        change_type="expense_add",
        label="Befristete Ausgabe",
        amount_mode="absolute",
        amount_minor=-10_000,
        frequency="monthly",
        due_day=1,
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 11, 1),
    )
    added = [
        flow.on
        for flow in apply_changes(base, (change,)).scheduled
        if flow.label == "Befristete Ausgabe"
    ]
    assert added == [date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)]


def test_horizon_beyond_the_maximum_is_rejected() -> None:
    with pytest.raises(ValueError):
        _input(horizon_months=61)


# --- Kennzahlen nach 3/6/12/24 Monaten -------------------------------------
# Vorher waren das feste Tagesoffsets (91/182/364) und fuer 24 Monate
# schlicht der letzte Tag der Reihe. Beides geht daneben: die Offsets
# verschieben sich mit den Monatslaengen um Tage, an denen Gehalt und
# Lastschriften liegen, und der 24-Monats-Wert zeigte bei laengerem Horizont
# den Stand am Horizontende.


def test_horizon_metrics_use_calendar_months_not_fixed_day_offsets() -> None:
    # AS_OF ist der 13.08.2026: drei Kalendermonate spaeter ist der
    # 13.11.2026, 91 Tage spaeter waere der 12.11.2026 - der Posten faellt
    # genau dazwischen.
    data = _input(
        horizon_months=6,
        daily_until=date(2027, 2, 13),
        scheduled=(_flow(date(2026, 11, 13), -40_000),),
    )

    metrics = calculate_forecast(data).metrics

    assert metrics.balance_in_3_months_minor == 60_000


def test_twentyfour_month_metric_stops_at_month_24_on_a_longer_horizon() -> None:
    data = _input(
        horizon_months=60,
        daily_until=date(2026, 9, 13),
        scheduled=(_flow(date(2029, 1, 15), -40_000),),
    )

    metrics = calculate_forecast(data).metrics

    # Der Abfluss liegt im 30. Monat: nach 24 Monaten ist er noch nicht da.
    assert metrics.balance_in_24_months_minor == 100_000
    assert metrics.balance_in_12_months_minor == 100_000
    # Er liegt aber im Horizont - sonst wuerde der Test auch ohne Korrektur
    # gruen, weil es gar nichts zu verwechseln gaebe.
    assert metrics.lowest_balance_minor == 60_000


def test_metric_falls_back_to_the_last_day_on_a_short_horizon() -> None:
    data = _input(scheduled=(_flow(date(2026, 9, 1), -40_000),))

    metrics = calculate_forecast(data).metrics

    assert metrics.balance_in_24_months_minor == 60_000


# --- Befristete Aenderungen (ends_on) --------------------------------------


def _rent_base() -> tuple[UUID, ForecastInput]:
    expense_id = uuid4()
    return expense_id, _input(
        horizon_months=12,
        daily_until=date(2027, 8, 13),
        scheduled=tuple(
            _flow(date(2026, month, 1), -50_000, "Miete", expense_id)
            for month in (9, 10, 11, 12)
        ),
        contract_terms=(
            ForecastContractTerm(
                contract_id=expense_id,
                label="Miete",
                direction="expense",
                amount_minor=50_000,
                frequency="monthly",
            ),
        ),
        fixed_monthly_minor=50_000,
    )


def test_limited_change_falls_back_to_the_contract_amount_after_ends_on() -> None:
    expense_id, base = _rent_base()
    change = ForecastChange(
        change_type="expense_change",
        target_contract_id=expense_id,
        label="Miete voruebergehend gemindert",
        amount_mode="delta",
        amount_minor=-10_000,
        frequency="monthly",
        starts_on=date(2026, 9, 1),
        ends_on=date(2026, 10, 31),
    )

    amounts = {flow.on: flow.amount_minor for flow in apply_changes(base, (change,)).scheduled}

    assert amounts[date(2026, 9, 1)] == -40_000
    assert amounts[date(2026, 10, 1)] == -40_000
    assert amounts[date(2026, 11, 1)] == -50_000
    assert amounts[date(2026, 12, 1)] == -50_000


def test_limited_change_leaves_the_monthly_reference_values_alone() -> None:
    """Ueberschuss, Sparquote und Fixkostenquote beschreiben den Dauerzustand.

    Eine Minderung ueber zwei Monate veraendert ihn nicht - sie gehoert in die
    Tagesreihe. Ohne ends_on ist die Aenderung dagegen dauerhaft und muss sich
    in den Monatswerten niederschlagen.
    """
    expense_id, base = _rent_base()
    common: dict[str, Any] = {
        "change_type": "expense_change",
        "target_contract_id": expense_id,
        "label": "Miete",
        "amount_mode": "delta",
        "amount_minor": -10_000,
        "frequency": "monthly",
        "starts_on": date(2026, 9, 1),
    }

    limited = apply_changes(base, (ForecastChange(**common, ends_on=date(2026, 10, 31)),))
    permanent = apply_changes(base, (ForecastChange(**common),))

    assert limited.fixed_monthly_minor == 50_000
    assert permanent.fixed_monthly_minor == 40_000
