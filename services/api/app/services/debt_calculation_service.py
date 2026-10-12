"""Reiner Darlehensrechner ohne DB-Zugriff (Plan Abschnitt 5, 5.1-5.5).

Gemeinsames Modell fuer Projektdarlehen und Bestandsschulden (`debt_plans`).
Nimmt typisierte, unveraenderliche Pydantic-Modelle entgegen und liefert
deterministische, centgenaue Ergebnisse. Kein `date.today()`-Aufruf: die
Zeitachse ist rein monatszaehlend ab Monat 0 (`starts_on` des Plans ist reine
Metadateninformation fuer die aufrufende Schicht, nicht Eingabe des Kerns).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_HORIZON_MONTHS = 600

RateKind = Literal["contracted", "assumed"]
AmortizationMode = Literal["fixed_payment", "initial_repayment", "full_repayment_term"]


class DebtCalculationError(ValueError):
    """Traegt den stabilen Warncode aus Plan 5.11 fuer die 422-Antwort der API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RatePeriodInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    starts_after_month: int = Field(ge=0)
    ends_after_month: int | None = Field(default=None, ge=0)
    nominal_rate_bps_annual: int = Field(ge=0)
    rate_kind: RateKind = "contracted"


class DrawInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    occurs_after_month: int = Field(ge=0)
    amount_minor: int = Field(gt=0)


class FeeInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    occurs_after_month: int = Field(ge=0)
    amount_minor: int = Field(gt=0)
    financed: bool = False


class SpecialRepaymentInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    occurs_after_month: int = Field(ge=0)
    amount_minor: int = Field(gt=0)


class DebtPlanInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal_limit_minor: int = Field(gt=0)
    opening_balance_minor: int = Field(ge=0)
    payment_interval_months: Literal[1, 3, 6, 12] = 1
    amortization_mode: AmortizationMode
    fixed_payment_minor: int | None = None
    initial_repayment_rate_bps: int | None = None
    target_term_months: int | None = None
    repayment_free_months: int = Field(default=0, ge=0, le=120)
    payment_free_months: int = Field(default=0, ge=0, le=120)
    capitalize_interest_during_payment_free: bool = True
    commitment_free_months: int = Field(default=0, ge=0, le=120)
    commitment_rate_bps_per_month: int = Field(default=0, ge=0, le=10000)
    calculation_horizon_months: int = Field(default=360, ge=1, le=MAX_HORIZON_MONTHS)
    rate_periods: tuple[RatePeriodInput, ...]
    draws: tuple[DrawInput, ...] = ()
    fees: tuple[FeeInput, ...] = ()
    special_repayments: tuple[SpecialRepaymentInput, ...] = ()


class MonthlyDebtRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: int
    opening_balance_minor: int
    draw_minor: int
    capitalized_fee_minor: int
    interest_accrued_minor: int
    interest_paid_minor: int
    interest_due_carry_minor: int
    commitment_interest_accrued_minor: int
    commitment_interest_paid_minor: int
    scheduled_payment_minor: int
    principal_payment_minor: int
    special_repayment_minor: int
    overpayment_minor: int
    cash_outflow_minor: int
    closing_balance_minor: int
    nominal_rate_bps_annual: int
    rate_kind: RateKind
    warnings: tuple[str, ...] = ()


class DebtScheduleSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_drawn_minor: int
    total_interest_minor: int
    total_capitalized_interest_minor: int
    total_commitment_interest_minor: int
    total_fees_minor: int
    total_cash_payments_minor: int
    ending_balance_minor: int
    payoff_month: int | None
    peak_payment_minor: int
    first_negative_amortization_month: int | None
    warnings: tuple[str, ...]
    rows: tuple[MonthlyDebtRow, ...]


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _monthly_rate(nominal_rate_bps_annual: int) -> Decimal:
    return Decimal(nominal_rate_bps_annual) / Decimal(10_000) / Decimal(12)


def _rate_period_for_month(periods: tuple[RatePeriodInput, ...], month: int) -> RatePeriodInput:
    for period in periods:
        if period.starts_after_month <= month and (
            period.ends_after_month is None or month <= period.ends_after_month
        ):
            return period
    raise DebtCalculationError("loan_rate_gap", f"Keine Ratephase deckt Monat {month} ab.")


def _resolve_base_payment(plan: DebtPlanInput) -> int:
    """Periodenrate fuer 'fixed_payment'/'initial_repayment'. 'full_repayment_term'
    wird separat je Abrufanker geloest (siehe _solve_full_repayment_term_payment)."""
    if plan.amortization_mode == "fixed_payment":
        assert plan.fixed_payment_minor is not None
        return plan.fixed_payment_minor
    if plan.amortization_mode == "initial_repayment":
        assert plan.initial_repayment_rate_bps is not None
        first_period = _rate_period_for_month(plan.rate_periods, 0)
        annual_bps = first_period.nominal_rate_bps_annual + plan.initial_repayment_rate_bps
        annual_payment = Decimal(plan.principal_limit_minor) * Decimal(annual_bps) / Decimal(10_000)
        period_payment = annual_payment * Decimal(plan.payment_interval_months) / Decimal(12)
        return _round_minor(period_payment)
    raise AssertionError(
        "full_repayment_term wird ueber _solve_full_repayment_term_payment geloest"
    )


def _solve_full_repayment_term_payment(
    plan: DebtPlanInput,
    anchor_month: int,
    anchor_balance: int,
) -> int:
    """Kleinste Periodenrate, die den ab `anchor_month` gezogenen Saldo
    innerhalb `target_term_months` per ganzzahliger Bisektion auf null tilgt
    (Plan 5.4, full_repayment_term). Vereinfachte Teilsimulation ohne Tilgungs-
    /Zahlungsfreiheit -- die vollstaendige Karenzlogik lebt in der Hauptschleife
    und wird fuer den (seltenen) Recast-Fall bewusst nicht dupliziert."""
    assert plan.target_term_months is not None
    if anchor_balance <= 0:
        return 0
    interval = plan.payment_interval_months
    target = plan.target_term_months

    def months_to_payoff(payment_minor: int) -> int | None:
        balance = Decimal(anchor_balance)
        elapsed = 0
        max_periods = (target // interval) + 2
        for _ in range(max_periods):
            interest_exact = Decimal(0)
            month = anchor_month + elapsed
            for offset in range(interval):
                period = _rate_period_for_month(plan.rate_periods, month + offset)
                interest_exact += balance * _monthly_rate(period.nominal_rate_bps_annual)
            interest_due = _round_minor(interest_exact)
            if payment_minor <= interest_due:
                return None
            principal = payment_minor - interest_due
            balance -= principal
            elapsed += interval
            if balance <= 0:
                return elapsed
        return None

    low, high = 1, max(anchor_balance, 1)
    # Obergrenze verdoppeln, bis sie nachweislich innerhalb der Zielrestlaufzeit tilgt.
    while months_to_payoff(high) is None or (months_to_payoff(high) or target + 1) > target:
        high *= 2
        if high > anchor_balance * 1_000_000:
            raise DebtCalculationError(
                "loan_rate_gap", "full_repayment_term konvergiert nicht: Zinsdaten pruefen."
            )
    while low < high:
        mid = (low + high) // 2
        result = months_to_payoff(mid)
        if result is not None and result <= target:
            high = mid
        else:
            low = mid + 1
    return low


def calculate_debt_schedule(plan: DebtPlanInput) -> DebtScheduleSummary:
    """Simuliert den Tilgungsplan Monat fuer Monat nach der in Plan 5.3
    festgelegten Reihenfolge. Wirft `DebtCalculationError` mit stabilem Code
    bei Abruflimit-Ueberschreitung oder Ratephasenluecke."""
    horizon = min(plan.calculation_horizon_months, MAX_HORIZON_MONTHS)
    rate_periods = tuple(sorted(plan.rate_periods, key=lambda p: p.starts_after_month))

    total_planned_draws = plan.opening_balance_minor + sum(d.amount_minor for d in plan.draws)
    if total_planned_draws > plan.principal_limit_minor:
        raise DebtCalculationError(
            "loan_draws_exceed_limit",
            f"Summe der Abrufe ({total_planned_draws}) uebersteigt das Limit "
            f"({plan.principal_limit_minor}).",
        )

    draws_by_month: dict[int, int] = {}
    for draw in plan.draws:
        draws_by_month[draw.occurs_after_month] = (
            draws_by_month.get(draw.occurs_after_month, 0) + draw.amount_minor
        )
    fees_by_month: dict[int, list[FeeInput]] = {}
    for fee in plan.fees:
        fees_by_month.setdefault(fee.occurs_after_month, []).append(fee)
    special_by_month: dict[int, int] = {}
    for repayment in plan.special_repayments:
        special_by_month[repayment.occurs_after_month] = (
            special_by_month.get(repayment.occurs_after_month, 0) + repayment.amount_minor
        )

    last_draw_month = max((d.occurs_after_month for d in plan.draws), default=0)
    base_payment: int | None = None
    if plan.amortization_mode in ("fixed_payment", "initial_repayment"):
        base_payment = _resolve_base_payment(plan)

    balance = Decimal(plan.opening_balance_minor)
    total_drawn = plan.opening_balance_minor
    interest_carry_exact = Decimal(0)
    commitment_carry_exact = Decimal(0)

    rows: list[MonthlyDebtRow] = []
    summary_warnings: set[str] = set()
    total_interest = 0
    total_capitalized_interest = 0
    total_commitment_interest = 0
    total_fees = 0
    total_cash_payments = 0
    peak_payment = 0
    first_negative_amortization_month: int | None = None
    payoff_month: int | None = None
    recast_payment_for_full_term: int | None = None

    for month in range(horizon):
        opening_balance = balance
        row_warnings: list[str] = []

        draw_amount = draws_by_month.get(month, 0)
        balance += draw_amount
        total_drawn += draw_amount

        month_fees = fees_by_month.get(month, ())
        financed_fee_minor = sum(f.amount_minor for f in month_fees if f.financed)
        cash_fee_minor = sum(f.amount_minor for f in month_fees if not f.financed)
        balance += financed_fee_minor
        total_fees += financed_fee_minor + cash_fee_minor

        if (
            balance <= 0
            and month not in draws_by_month
            and not any(d.occurs_after_month > month for d in plan.draws)
        ):
            break

        rate_period = _rate_period_for_month(rate_periods, month)
        if rate_period.rate_kind == "assumed":
            row_warnings.append("assumed_followup_rate")

        interest_accrued_exact = balance * _monthly_rate(rate_period.nominal_rate_bps_annual)
        interest_carry_exact += interest_accrued_exact

        undrawn_minor = max(plan.principal_limit_minor - total_drawn, 0)
        commitment_accrued_this_month = Decimal(0)
        if month >= plan.commitment_free_months and plan.commitment_rate_bps_per_month > 0:
            commitment_accrued_this_month = Decimal(undrawn_minor) * (
                Decimal(plan.commitment_rate_bps_per_month) / Decimal(10_000)
            )
            commitment_carry_exact += commitment_accrued_this_month

        is_payment_month = (month + 1) % plan.payment_interval_months == 0
        interest_due_minor = 0
        interest_paid_minor = 0
        commitment_paid_minor = 0
        capitalized_interest_minor = 0
        scheduled_payment_minor = 0
        principal_payment_minor = 0

        in_payment_free = month < plan.payment_free_months
        in_repayment_free = month < plan.repayment_free_months and not in_payment_free

        if is_payment_month:
            interest_due_minor = _round_minor(interest_carry_exact)
            interest_carry_exact = Decimal(0)
            if plan.commitment_rate_bps_per_month > 0:
                commitment_paid_minor = _round_minor(commitment_carry_exact)
                commitment_carry_exact = Decimal(0)

            if in_payment_free:
                if plan.capitalize_interest_during_payment_free:
                    balance += interest_due_minor
                    capitalized_interest_minor = interest_due_minor
                else:
                    row_warnings.append("payment_free_interest_open")
            elif in_repayment_free:
                interest_paid_minor = interest_due_minor
                scheduled_payment_minor = interest_paid_minor
            else:
                if plan.amortization_mode == "full_repayment_term":
                    if recast_payment_for_full_term is None or month >= last_draw_month:
                        if month >= last_draw_month and recast_payment_for_full_term is None:
                            assert plan.target_term_months is not None
                            recast_payment_for_full_term = _solve_full_repayment_term_payment(
                                plan, month, int(balance)
                            )
                    payment_amount = recast_payment_for_full_term or 0
                else:
                    assert base_payment is not None
                    payment_amount = base_payment

                scheduled_payment_minor = payment_amount
                if payment_amount < interest_due_minor:
                    interest_paid_minor = payment_amount
                    principal_payment_minor = 0
                    row_warnings.append("loan_negative_amortization")
                    if first_negative_amortization_month is None:
                        first_negative_amortization_month = month
                else:
                    interest_paid_minor = interest_due_minor
                    principal_payment_minor = min(payment_amount - interest_due_minor, int(balance))

            balance -= principal_payment_minor

        special_repayment_minor = special_by_month.get(month, 0)
        overpayment_minor = 0
        if special_repayment_minor > 0:
            if special_repayment_minor > balance:
                overpayment_minor = special_repayment_minor - int(balance)
                special_repayment_minor = int(balance)
            balance -= special_repayment_minor

        if balance <= 0:
            balance = Decimal(0)

        cash_outflow_minor = (
            interest_paid_minor
            + principal_payment_minor
            + commitment_paid_minor
            + cash_fee_minor
            + special_repayment_minor
        )

        total_interest += interest_paid_minor
        total_capitalized_interest += capitalized_interest_minor
        total_commitment_interest += commitment_paid_minor
        total_cash_payments += cash_outflow_minor
        # Fuer die Liquiditaetsspitze zaehlt jeder reale Abfluss. Insbesondere
        # ein endfaelliges Darlehen hat im Schlussmonat neben der normalen
        # Zinsrate eine grosse Sondertilgung, die nicht in
        # `scheduled_payment_minor` steckt.
        peak_payment = max(peak_payment, cash_outflow_minor)
        summary_warnings.update(row_warnings)

        rows.append(
            MonthlyDebtRow(
                month=month,
                opening_balance_minor=int(opening_balance),
                draw_minor=draw_amount,
                capitalized_fee_minor=financed_fee_minor,
                interest_accrued_minor=_round_minor(interest_accrued_exact),
                interest_paid_minor=interest_paid_minor,
                interest_due_carry_minor=_round_minor(interest_carry_exact),
                commitment_interest_accrued_minor=_round_minor(commitment_accrued_this_month),
                commitment_interest_paid_minor=commitment_paid_minor,
                scheduled_payment_minor=scheduled_payment_minor,
                principal_payment_minor=principal_payment_minor,
                special_repayment_minor=special_repayment_minor,
                overpayment_minor=overpayment_minor,
                cash_outflow_minor=cash_outflow_minor,
                closing_balance_minor=int(balance),
                nominal_rate_bps_annual=rate_period.nominal_rate_bps_annual,
                rate_kind=rate_period.rate_kind,
                warnings=tuple(row_warnings),
            )
        )

        if balance == 0 and not any(d.occurs_after_month > month for d in plan.draws):
            payoff_month = month
            break

    if payoff_month is None and rows and rows[-1].closing_balance_minor > 0:
        summary_warnings.add("calculation_horizon_reached")

    return DebtScheduleSummary(
        total_drawn_minor=total_drawn,
        total_interest_minor=total_interest,
        total_capitalized_interest_minor=total_capitalized_interest,
        total_commitment_interest_minor=total_commitment_interest,
        total_fees_minor=total_fees,
        total_cash_payments_minor=total_cash_payments,
        ending_balance_minor=int(balance),
        payoff_month=payoff_month,
        peak_payment_minor=peak_payment,
        first_negative_amortization_month=first_negative_amortization_month,
        warnings=tuple(sorted(summary_warnings)),
        rows=tuple(rows),
    )
