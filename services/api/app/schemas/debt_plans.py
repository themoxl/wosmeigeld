"""Request-/Response-Schemas fuer Tilgungsplaene (Plan Abschnitt 7.3).

`debt_plans` ist das gemeinsame Modell fuer Projektdarlehen und
Bestandsschulden (Plan 4.8) -- dieselben Schemas bedienen beide Pfade.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

DebtPlanStatus = Literal["draft", "offered", "active", "paid", "cancelled"]
AmortizationMode = Literal["fixed_payment", "initial_repayment", "full_repayment_term"]
RateKind = Literal["contracted", "assumed"]
DrawStatus = Literal["planned", "actual"]
FeeKind = Literal["upfront", "ongoing", "other"]
SpecialRepaymentStatus = Literal["planned", "actual"]
DebtDrawMode = Literal["immediate", "cost_plan", "manual"]


def _check_amortization_fields(
    mode: AmortizationMode,
    fixed_payment_minor: int | None,
    initial_repayment_rate_bps: int | None,
    target_term_months: int | None,
) -> None:
    if mode == "fixed_payment":
        if fixed_payment_minor is None:
            raise ValueError(
                "fixed_payment_minor ist bei amortization_mode=fixed_payment erforderlich"
            )
        if initial_repayment_rate_bps is not None or target_term_months is not None:
            raise ValueError("initial_repayment_rate_bps/target_term_months nur bei anderem Modus")
    elif mode == "initial_repayment":
        if initial_repayment_rate_bps is None:
            raise ValueError(
                "initial_repayment_rate_bps ist bei "
                "amortization_mode=initial_repayment erforderlich"
            )
        if fixed_payment_minor is not None or target_term_months is not None:
            raise ValueError("fixed_payment_minor/target_term_months nur bei anderem Modus")
    else:
        if target_term_months is None:
            raise ValueError(
                "target_term_months ist bei amortization_mode=full_repayment_term erforderlich"
            )
        if fixed_payment_minor is not None or initial_repayment_rate_bps is not None:
            raise ValueError("fixed_payment_minor/initial_repayment_rate_bps nur bei anderem Modus")


class DebtPlanCreate(BaseModel):
    project_financing_source_id: UUID | None = None
    asset_debt_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    creditor_label: str | None = None
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    status: DebtPlanStatus = "draft"
    principal_limit_minor: int = Field(gt=0)
    opening_balance_minor: int = Field(default=0, ge=0)
    starts_on: str
    payment_interval_months: Literal[1, 3, 6, 12] = 1
    amortization_mode: AmortizationMode
    fixed_payment_minor: int | None = Field(default=None, gt=0)
    initial_repayment_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    target_term_months: int | None = Field(default=None, ge=1, le=600)
    repayment_free_months: int = Field(default=0, ge=0, le=120)
    payment_free_months: int = Field(default=0, ge=0, le=120)
    capitalize_interest_during_payment_free: bool = True
    commitment_free_months: int = Field(default=0, ge=0, le=120)
    commitment_rate_bps_per_month: int = Field(default=0, ge=0, le=10000)
    recast_payment_on_rate_change: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "DebtPlanCreate":
        if (self.project_financing_source_id is None) == (self.asset_debt_id is None):
            raise ValueError(
                "genau eines von project_financing_source_id/asset_debt_id ist erforderlich"
            )
        _check_amortization_fields(
            self.amortization_mode,
            self.fixed_payment_minor,
            self.initial_repayment_rate_bps,
            self.target_term_months,
        )
        return self


class DebtPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    creditor_label: str | None = None
    status: DebtPlanStatus | None = None
    principal_limit_minor: int | None = Field(default=None, gt=0)
    opening_balance_minor: int | None = Field(default=None, ge=0)
    starts_on: str | None = None
    payment_interval_months: Literal[1, 3, 6, 12] | None = None
    amortization_mode: AmortizationMode | None = None
    fixed_payment_minor: int | None = Field(default=None, gt=0)
    initial_repayment_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    target_term_months: int | None = Field(default=None, ge=1, le=600)
    repayment_free_months: int | None = Field(default=None, ge=0, le=120)
    payment_free_months: int | None = Field(default=None, ge=0, le=120)
    capitalize_interest_during_payment_free: bool | None = None
    commitment_free_months: int | None = Field(default=None, ge=0, le=120)
    commitment_rate_bps_per_month: int | None = Field(default=None, ge=0, le=10000)
    recast_payment_on_rate_change: bool | None = None
    clear_fields: list[str] = Field(default_factory=list)


class DebtPlan(BaseModel):
    id: UUID
    project_financing_source_id: UUID | None
    asset_debt_id: UUID | None
    name: str
    creditor_label: str | None
    currency: str
    status: DebtPlanStatus
    principal_limit_minor: int
    opening_balance_minor: int
    starts_on: str
    payment_interval_months: Literal[1, 3, 6, 12]
    amortization_mode: AmortizationMode
    fixed_payment_minor: int | None
    initial_repayment_rate_bps: int | None
    target_term_months: int | None
    repayment_free_months: int
    payment_free_months: int
    capitalize_interest_during_payment_free: bool
    commitment_free_months: int
    commitment_rate_bps_per_month: int
    recast_payment_on_rate_change: bool
    version: int
    created_at: datetime
    updated_at: datetime


class RatePeriodInput(BaseModel):
    starts_after_month: int = Field(ge=0)
    ends_after_month: int | None = Field(default=None, ge=0)
    nominal_rate_bps_annual: int = Field(ge=0)
    rate_kind: RateKind = "contracted"
    label: str | None = None


class RatePeriod(RatePeriodInput):
    id: UUID


class RatePeriodsReplace(BaseModel):
    periods: list[RatePeriodInput] = Field(default_factory=list)


class DrawInput(BaseModel):
    occurs_after_month: int = Field(ge=0)
    amount_minor: int = Field(gt=0)
    status: DrawStatus = "planned"


class Draw(DrawInput):
    id: UUID


class DrawsReplace(BaseModel):
    draws: list[DrawInput] = Field(default_factory=list)


class FeeInput(BaseModel):
    occurs_after_month: int = Field(ge=0)
    amount_minor: int = Field(gt=0)
    fee_kind: FeeKind = "upfront"
    financed: bool = False
    label: str | None = None


class Fee(FeeInput):
    id: UUID


class FeesReplace(BaseModel):
    fees: list[FeeInput] = Field(default_factory=list)


class SpecialRepaymentInput(BaseModel):
    occurs_after_month: int = Field(ge=0)
    amount_minor: int = Field(gt=0)
    status: SpecialRepaymentStatus = "planned"
    label: str | None = None


class SpecialRepayment(SpecialRepaymentInput):
    id: UUID


class SpecialRepaymentsReplace(BaseModel):
    special_repayments: list[SpecialRepaymentInput] = Field(default_factory=list)


class DebtPlanDetail(DebtPlan):
    """Vollstaendige, editierbare Kreditkonfiguration.

    Die Kindtabellen gehoeren fachlich zum Plan. Ein Editor darf sie nicht aus
    einer berechneten Schedule rueckwaerts erraten, weil dabei insbesondere
    Zinsarten, geplante Abrufe und Sondertilgungen verloren gehen.
    """

    draw_mode: DebtDrawMode = "immediate"
    rate_periods: list[RatePeriod] = Field(default_factory=list)
    draws: list[Draw] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list)
    special_repayments: list[SpecialRepayment] = Field(default_factory=list)


class DebtPlanConfigurationInput(BaseModel):
    """Plan und Kindtabellen fuer einen transaktionalen Vollersatz."""

    name: str = Field(min_length=1, max_length=160)
    creditor_label: str | None = None
    status: DebtPlanStatus = "draft"
    principal_limit_minor: int = Field(gt=0)
    starts_on: str
    payment_interval_months: Literal[1, 3, 6, 12] = 1
    amortization_mode: AmortizationMode
    fixed_payment_minor: int | None = Field(default=None, gt=0)
    initial_repayment_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    target_term_months: int | None = Field(default=None, ge=1, le=600)
    repayment_free_months: int = Field(default=0, ge=0, le=120)
    payment_free_months: int = Field(default=0, ge=0, le=120)
    capitalize_interest_during_payment_free: bool = True
    commitment_free_months: int = Field(default=0, ge=0, le=120)
    commitment_rate_bps_per_month: int = Field(default=0, ge=0, le=10000)
    recast_payment_on_rate_change: bool = False
    draw_mode: DebtDrawMode = "immediate"
    rate_periods: list[RatePeriodInput] = Field(min_length=1)
    draws: list[DrawInput] = Field(default_factory=list)
    fees: list[FeeInput] = Field(default_factory=list)
    special_repayments: list[SpecialRepaymentInput] = Field(default_factory=list)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate(self) -> "DebtPlanConfigurationInput":
        _check_amortization_fields(
            self.amortization_mode,
            self.fixed_payment_minor,
            self.initial_repayment_rate_bps,
            self.target_term_months,
        )
        ordered = sorted(self.rate_periods, key=lambda period: period.starts_after_month)
        if ordered[0].starts_after_month != 0:
            raise ValueError("Die erste Zinsphase muss in Monat 0 beginnen")
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.ends_after_month is None:
                raise ValueError("Nur die letzte Zinsphase darf ohne Endmonat sein")
            if current.starts_after_month != previous.ends_after_month + 1:
                raise ValueError("Zinsphasen muessen lueckenlos aufeinander folgen")
        if self.draw_mode == "immediate" and self.draws:
            raise ValueError("Sofortauszahlung darf keine zusaetzlichen Abrufe enthalten")
        if sum(draw.amount_minor for draw in self.draws) > self.principal_limit_minor:
            raise ValueError("Die Summe der Abrufe ueberschreitet den Darlehensrahmen")
        return self


class DebtScheduleRow(BaseModel):
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
    warnings: list[str]


class DebtSchedule(BaseModel):
    debt_plan_id: UUID
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
    warnings: list[str]
    rows: list[DebtScheduleRow]
