"""Reine Darlehensreferenzfaelle aus Plan Abschnitt 15.1 -- centgenau, ohne
DB/API. Jeder Fall im Plan-Dokument entspricht genau einer Testfunktion hier.
"""

import pytest

from app.services.debt_calculation_service import (
    DebtCalculationError,
    DebtPlanInput,
    DrawInput,
    RatePeriodInput,
    SpecialRepaymentInput,
    calculate_debt_schedule,
)


def test_fixed_payment_reference_case() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=10_000_000,
        opening_balance_minor=10_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=80_000,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=360),),
        calculation_horizon_months=2,
    )
    result = calculate_debt_schedule(plan)
    m1, m2 = result.rows[0], result.rows[1]
    assert m1.interest_paid_minor == 30_000
    assert m1.principal_payment_minor == 50_000
    assert m1.closing_balance_minor == 9_950_000
    assert m2.interest_paid_minor == 29_850
    assert m2.principal_payment_minor == 50_150
    assert m2.closing_balance_minor == 9_899_850


def test_repayment_free_pays_interest_only_and_keeps_principal() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=10_000_000,
        opening_balance_minor=10_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=100_000,
        repayment_free_months=12,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=360),),
        calculation_horizon_months=12,
    )
    result = calculate_debt_schedule(plan)
    assert len(result.rows) == 12
    for row in result.rows:
        assert row.interest_paid_minor == 30_000
        assert row.principal_payment_minor == 0
        assert row.closing_balance_minor == 10_000_000
    assert result.ending_balance_minor == 10_000_000


def test_payment_free_capitalizes_interest_monthly_compounded() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=10_000_000,
        opening_balance_minor=10_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=100_000,
        payment_free_months=12,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=360),),
        calculation_horizon_months=12,
    )
    result = calculate_debt_schedule(plan)
    assert len(result.rows) == 12
    for row in result.rows:
        assert row.scheduled_payment_minor == 0
        assert row.cash_outflow_minor == 0
    assert result.ending_balance_minor == 10_366_001
    assert result.total_capitalized_interest_minor == 366_001


def test_initial_repayment_reference_case() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=20_000_000,
        opening_balance_minor=20_000_000,
        amortization_mode="initial_repayment",
        initial_repayment_rate_bps=200,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=300),),
        calculation_horizon_months=1,
    )
    result = calculate_debt_schedule(plan)
    m1 = result.rows[0]
    assert m1.scheduled_payment_minor == 83_333
    assert m1.interest_paid_minor == 50_000
    assert m1.principal_payment_minor == 33_333
    assert m1.closing_balance_minor == 19_966_667


def test_commitment_interest_on_undrawn_amount_after_free_months() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=20_000_000,
        opening_balance_minor=5_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=100_000,
        commitment_free_months=3,
        commitment_rate_bps_per_month=25,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=0),),
        calculation_horizon_months=5,
    )
    result = calculate_debt_schedule(plan)
    for row in result.rows[:3]:
        assert row.commitment_interest_paid_minor == 0
    assert result.rows[3].commitment_interest_paid_minor == 37_500
    assert result.rows[4].commitment_interest_paid_minor == 37_500


def test_special_repayment_larger_than_balance_never_goes_negative() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=1_000_000,
        opening_balance_minor=1_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=10_000,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=360),),
        special_repayments=(SpecialRepaymentInput(occurs_after_month=0, amount_minor=2_000_000),),
        calculation_horizon_months=1,
    )
    result = calculate_debt_schedule(plan)
    row = result.rows[0]
    assert row.closing_balance_minor == 0
    assert row.overpayment_minor > 0
    assert result.ending_balance_minor == 0
    assert result.payoff_month == 0


def test_negative_amortization_warns_and_terminates_within_horizon() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=10_000_000,
        opening_balance_minor=10_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=1_000,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=360),),
        calculation_horizon_months=6,
    )
    result = calculate_debt_schedule(plan)
    assert len(result.rows) == 6
    assert result.first_negative_amortization_month == 0
    assert "loan_negative_amortization" in result.rows[0].warnings
    assert all(row.principal_payment_minor == 0 for row in result.rows)
    assert result.ending_balance_minor == 10_000_000
    assert "calculation_horizon_reached" in result.warnings


def test_rate_phase_transition_to_assumed_followup_rate() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=10_000_000,
        opening_balance_minor=10_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=80_000,
        rate_periods=(
            RatePeriodInput(
                starts_after_month=0, ends_after_month=1,
                nominal_rate_bps_annual=360, rate_kind="contracted",
            ),
            RatePeriodInput(
                starts_after_month=2, nominal_rate_bps_annual=500, rate_kind="assumed"
            ),
        ),
        calculation_horizon_months=3,
    )
    result = calculate_debt_schedule(plan)
    assert result.rows[0].rate_kind == "contracted"
    assert result.rows[1].rate_kind == "contracted"
    assert result.rows[1].closing_balance_minor == 9_899_850
    assert result.rows[2].rate_kind == "assumed"
    assert "assumed_followup_rate" in result.rows[2].warnings


def test_draws_exceeding_limit_raise_422_code() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=1_000_000,
        opening_balance_minor=800_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=10_000,
        draws=(DrawInput(occurs_after_month=1, amount_minor=500_000),),
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=360),),
    )
    with pytest.raises(DebtCalculationError) as exc_info:
        calculate_debt_schedule(plan)
    assert exc_info.value.code == "loan_draws_exceed_limit"


def test_rate_period_gap_raises_422_code() -> None:
    plan = DebtPlanInput(
        principal_limit_minor=1_000_000,
        opening_balance_minor=1_000_000,
        amortization_mode="fixed_payment",
        fixed_payment_minor=10_000,
        rate_periods=(
            RatePeriodInput(starts_after_month=0, ends_after_month=0, nominal_rate_bps_annual=360),
        ),
        calculation_horizon_months=3,
    )
    with pytest.raises(DebtCalculationError) as exc_info:
        calculate_debt_schedule(plan)
    assert exc_info.value.code == "loan_rate_gap"


def test_full_repayment_term_pays_off_within_target_term() -> None:
    """Kein Referenzfall aus 15.1 (dort nicht tabelliert), aber die dritte
    amortization_mode-Variante muss ebenfalls nachweislich funktionieren."""
    plan = DebtPlanInput(
        principal_limit_minor=10_000_000,
        opening_balance_minor=10_000_000,
        amortization_mode="full_repayment_term",
        target_term_months=24,
        rate_periods=(RatePeriodInput(starts_after_month=0, nominal_rate_bps_annual=360),),
        calculation_horizon_months=24,
    )
    result = calculate_debt_schedule(plan)
    assert result.payoff_month is not None
    assert result.payoff_month <= 23
    assert result.ending_balance_minor == 0
