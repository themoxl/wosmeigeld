"""Reine Haushalts-Cashflow-/Leistbarkeitstests (Plan 5.7/5.8)."""

from app.services.household_cashflow_service import (
    CashflowAssumptionsInput,
    CostOutflow,
    DebtCashflow,
    FinancingInflow,
    GoalContribution,
    HouseholdCashflowInput,
    RecurringImpactInput,
    calculate_household_cashflow,
)


def _assumptions(**overrides: object) -> CashflowAssumptionsInput:
    base: dict[str, object] = {
        "starting_liquidity_minor": 1_000_000,
        "monthly_net_income_minor": 300_000,
        "monthly_living_costs_minor": 150_000,
        "monthly_current_housing_minor": 80_000,
        "monthly_existing_debt_service_minor": 0,
        "monthly_other_commitments_minor": 0,
        "minimum_liquidity_reserve_minor": 500_000,
    }
    base.update(overrides)
    return CashflowAssumptionsInput(**base)  # type: ignore[arg-type]


def test_baseline_surplus_accumulates_without_project_activity() -> None:
    data = HouseholdCashflowInput(assumptions=_assumptions(), horizon_months=3)
    result = calculate_household_cashflow(data)
    # 300.000 - 150.000 - 80.000 = 70.000 Ueberschuss/Monat.
    assert result.monthly_surplus_before_minor == 70_000
    assert [r.net_household_cashflow_minor for r in result.rows] == [70_000, 70_000, 70_000]
    assert result.rows[-1].liquid_reserve_end_minor == 1_000_000 + 3 * 70_000
    assert result.negative_months_count == 0
    assert not result.reserve_breached


def test_project_cost_outflow_reduces_reserve_in_its_month() -> None:
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=2,
        cost_outflows=(CostOutflow(month=1, amount_minor=200_000),),
    )
    result = calculate_household_cashflow(data)
    assert result.rows[0].project_cost_outflows_minor == 0
    assert result.rows[1].project_cost_outflows_minor == 200_000
    assert result.rows[1].net_household_cashflow_minor == 70_000 - 200_000


def test_debt_draw_and_service_do_not_double_count() -> None:
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=2,
        debt_cashflows=(
            DebtCashflow(month=0, draw_minor=10_000_000, cash_outflow_minor=0),
            DebtCashflow(month=1, draw_minor=0, cash_outflow_minor=50_000),
        ),
    )
    result = calculate_household_cashflow(data)
    assert result.rows[0].loan_and_grant_inflows_minor == 10_000_000
    assert result.rows[0].new_debt_service_minor == 0
    assert result.rows[1].loan_and_grant_inflows_minor == 0
    assert result.rows[1].new_debt_service_minor == 50_000
    assert result.peak_debt_service_minor == 50_000


def test_secured_grant_inflow_reduces_gap_via_planned_financing() -> None:
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=1,
        planned_financing_inflows=(FinancingInflow(month=0, amount_minor=25_000, secured=True),),
    )
    result = calculate_household_cashflow(data)
    assert result.rows[0].loan_and_grant_inflows_minor == 25_000


def test_recurring_income_and_expense_increase_have_opposite_signs() -> None:
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=1,
        recurring_impacts=(
            RecurringImpactInput(
                impact_type="income", amount_minor=10_000, frequency="monthly", starts_on_month=0
            ),
            RecurringImpactInput(
                impact_type="expense_increase",
                amount_minor=4_000,
                frequency="monthly",
                starts_on_month=0,
            ),
        ),
    )
    result = calculate_household_cashflow(data)
    assert result.rows[0].recurring_project_impacts_minor == 10_000 - 4_000


def test_speculative_impact_can_be_excluded_from_affordability() -> None:
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=1,
        recurring_impacts=(
            RecurringImpactInput(
                impact_type="income",
                amount_minor=999_999,
                frequency="monthly",
                starts_on_month=0,
                include_in_affordability=False,
            ),
        ),
    )
    result = calculate_household_cashflow(data)
    assert result.rows[0].recurring_project_impacts_minor == 0


def test_goal_contribution_is_a_recurring_outflow_from_its_start_month() -> None:
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=3,
        goal_contributions=(GoalContribution(monthly_amount_minor=5_000, starts_on_month=1),),
    )
    result = calculate_household_cashflow(data)
    assert result.rows[0].goal_contributions_minor == 0
    assert result.rows[1].goal_contributions_minor == 5_000
    assert result.rows[2].goal_contributions_minor == 5_000


def test_reserve_breach_is_detected_and_flagged() -> None:
    # income(300.000) - living(150.000) - housing(80.000) - cost(90.000) = -20.000;
    # Reserve faellt von 100.000 auf 80.000 - unter das Mindestreserveziel 90.000.
    data = HouseholdCashflowInput(
        assumptions=_assumptions(
            starting_liquidity_minor=100_000, minimum_liquidity_reserve_minor=90_000
        ),
        horizon_months=1,
        cost_outflows=(CostOutflow(month=0, amount_minor=90_000),),
    )
    result = calculate_household_cashflow(data)
    assert result.rows[0].liquid_reserve_end_minor == 80_000
    assert result.reserve_breached is True
    assert "liquidity_reserve_breached" in result.warnings
    assert result.first_shortfall_month == 0


def test_contingency_is_reserved_once_at_start_month() -> None:
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=3,
        contingency_minor=20_000,
        contingency_reservation_start_month=1,
    )
    result = calculate_household_cashflow(data)
    before = result.rows[0].liquid_reserve_end_minor
    after = result.rows[1].liquid_reserve_end_minor
    # Regulaerer Ueberschuss (70.000) minus einmaliger Pufferreservierung (20.000).
    assert after - before == 70_000 - 20_000
    third = result.rows[2].liquid_reserve_end_minor
    assert third - after == 70_000


def test_surplus_after_uses_peak_debt_month_not_month_zero() -> None:
    """Der Kapitaldienst startet fast nie in Monat 0.

    Vorher wurde `monthly_surplus_after_minor` starr aus Monat 0 gebildet -
    solange das Darlehen noch nicht abgerufen war, entsprach "mit Vorhaben"
    also exakt "ohne Vorhaben", und die Kachel behauptete, das Vorhaben koste
    nichts.
    """
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=4,
        debt_cashflows=(
            DebtCashflow(month=2, draw_minor=0, cash_outflow_minor=50_000),
            DebtCashflow(month=3, draw_minor=0, cash_outflow_minor=50_000),
        ),
    )
    result = calculate_household_cashflow(data)
    assert result.monthly_surplus_before_minor == 70_000
    assert result.monthly_surplus_after_minor == 70_000 - 50_000


def test_surplus_after_includes_planned_recurring_changes() -> None:
    """Eine ab einem Stichtag geplante Aenderung muss in der Kennzahl ankommen.

    Folgekosten (Grundsteuer, Ruecklage) belasten dauerhaft, eine zugesagte
    Gehaltserhoehung entlastet dauerhaft. Fehlten sie hier, waere jede
    eingeplante Veraenderung unsichtbar, obwohl die Monatsreihe sie fuehrt.
    """
    data = HouseholdCashflowInput(
        assumptions=_assumptions(),
        horizon_months=6,
        debt_cashflows=tuple(
            DebtCashflow(month=m, draw_minor=0, cash_outflow_minor=50_000) for m in range(2, 6)
        ),
        recurring_impacts=(
            RecurringImpactInput(
                impact_type="income",
                amount_minor=30_000,
                frequency="monthly",
                starts_on_month=1,
            ),
            RecurringImpactInput(
                impact_type="expense_increase",
                amount_minor=12_000,
                frequency="monthly",
                starts_on_month=0,
            ),
        ),
    )
    result = calculate_household_cashflow(data)
    # 70.000 Basis + 30.000 Gehalt - 12.000 Folgekosten - 50.000 Kapitaldienst.
    assert result.monthly_surplus_after_minor == 70_000 + 30_000 - 12_000 - 50_000


def test_surplus_after_equals_before_without_any_project_burden() -> None:
    data = HouseholdCashflowInput(assumptions=_assumptions(), horizon_months=3)
    result = calculate_household_cashflow(data)
    assert result.monthly_surplus_after_minor == result.monthly_surplus_before_minor
