"""Request-/Response-Schemas fuer das Vorhaben-Modul (Plan Abschnitt 7.1-7.4).

Foerderung (7.5) und Import/Export (7.6) folgen in P3/P4. Tilgungsplaene
selbst leben in `schemas/debt_plans.py` (gemeinsames Modell mit
Bestandsschulden); hier nur die Finanzierungsquelle, die auf einen Plan
verweist."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.debt_plans import DebtPlanConfigurationInput, DebtPlanDetail, DrawInput

ProjectType = Literal[
    "home_build", "property_purchase", "renovation", "vehicle",
    "solar", "education", "business", "other",
]
ProjectStatus = Literal[
    "idea", "planning", "approved", "active", "completed", "cancelled", "archived"
]
ScenarioKind = Literal["baseline", "optimistic", "stress", "custom"]
ScenarioStatus = Literal["draft", "selected", "superseded"]
ContingencyMode = Literal["none", "percent", "fixed"]
CostClass = Literal["direct", "soft", "transition", "other"]
CostKind = Literal["cash", "non_cash"]
CostItemStatus = Literal["estimated", "quoted", "committed", "partially_paid", "paid", "cancelled"]
CalculationMode = Literal["fixed", "quantity", "percentage"]
PercentageBasis = Literal["item", "section_direct", "project_direct"]
ScheduleEntryStatus = Literal["planned", "cancelled"]


class MajorProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    project_type: ProjectType
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    country_code: str = Field(default="DE", pattern=r"^[A-Z]{2}$")
    region_code: str | None = None
    postal_code: str | None = None
    municipality: str | None = None
    planned_start_on: date | None = None
    planned_completion_on: date | None = None
    note: str | None = None
    template_key: str | None = None


class MajorProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    region_code: str | None = None
    postal_code: str | None = None
    municipality: str | None = None
    planned_start_on: date | None = None
    planned_completion_on: date | None = None
    actual_start_on: date | None = None
    actual_completion_on: date | None = None
    note: str | None = None
    clear_fields: list[str] = Field(default_factory=list)


class MajorProject(BaseModel):
    id: UUID
    name: str
    project_type: ProjectType
    status: ProjectStatus
    currency: str
    country_code: str
    region_code: str | None
    postal_code: str | None
    municipality: str | None
    planned_start_on: date | None
    planned_completion_on: date | None
    actual_start_on: date | None
    actual_completion_on: date | None
    result_asset_id: UUID | None
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectListItem(BaseModel):
    id: UUID
    name: str
    project_type: ProjectType
    status: ProjectStatus
    selected_scenario_id: UUID | None
    selected_scenario_name: str | None
    project_cash_cost_minor: int | None
    planned_completion_on: date | None
    updated_at: datetime


class ProjectTransitionRequest(BaseModel):
    to_status: ProjectStatus
    note: str | None = None


class ProjectDeleteImpact(BaseModel):
    scenario_count: int
    has_actual_costs: bool
    hard_delete_allowed: bool
    reasons: list[str]


class ProjectScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scenario_kind: ScenarioKind = "baseline"
    as_of_date: date | None = None
    minimum_liquidity_reserve_minor: int = Field(default=0, ge=0)
    calculation_horizon_months: int = Field(default=360, ge=1, le=600)
    contingency_mode: ContingencyMode = "none"
    contingency_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    contingency_fixed_minor: int | None = Field(default=None, ge=0)
    template_key: str | None = None

    @model_validator(mode="after")
    def _validate_contingency(self) -> "ProjectScenarioCreate":
        _check_contingency_fields(
            self.contingency_mode, self.contingency_rate_bps, self.contingency_fixed_minor
        )
        return self


class ProjectScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    minimum_liquidity_reserve_minor: int | None = Field(default=None, ge=0)
    calculation_horizon_months: int | None = Field(default=None, ge=1, le=600)
    contingency_mode: ContingencyMode | None = None
    contingency_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    contingency_fixed_minor: int | None = Field(default=None, ge=0)
    clear_fields: list[str] = Field(default_factory=list)


class ProjectScenario(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    scenario_kind: ScenarioKind
    status: ScenarioStatus
    source_scenario_id: UUID | None
    as_of_date: date
    contingency_mode: ContingencyMode
    contingency_rate_bps: int | None
    contingency_fixed_minor: int | None
    minimum_liquidity_reserve_minor: int
    calculation_horizon_months: int
    selected_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ScenarioCloneRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scenario_kind: ScenarioKind = "custom"
    stress_cost_increase_bps: int | None = Field(default=None, ge=0, le=10000)
    stress_delay_months: int | None = Field(default=None, ge=0, le=120)
    stress_followup_rate_increase_bps: int | None = Field(default=None, ge=0, le=2000)
    exclude_unsecured_funding: bool = False
    request_id: UUID | None = None


class CostSectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    semantic_key: str | None = None
    cost_class: CostClass = "direct"
    sort_order: int = 0
    planned_start_on: date | None = None
    planned_end_on: date | None = None
    note: str | None = None


class CostSectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    cost_class: CostClass | None = None
    sort_order: int | None = None
    planned_start_on: date | None = None
    planned_end_on: date | None = None
    note: str | None = None
    clear_fields: list[str] = Field(default_factory=list)


class CostSection(BaseModel):
    id: UUID
    scenario_id: UUID
    semantic_key: str | None
    name: str
    cost_class: CostClass
    sort_order: int
    planned_start_on: date | None
    planned_end_on: date | None
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class CostSectionReorder(BaseModel):
    ordered_section_ids: list[UUID]


def _check_calculation_mode_fields(
    mode: CalculationMode,
    estimate_manual_minor: int | None,
    quantity: Decimal | None,
    unit_price_minor: int | None,
    percentage_rate_bps: int | None,
    percentage_basis: PercentageBasis | None,
    percentage_basis_item_id: UUID | None,
) -> None:
    if mode == "fixed":
        if estimate_manual_minor is None:
            raise ValueError("estimate_manual_minor ist bei calculation_mode=fixed erforderlich")
        if quantity is not None or unit_price_minor is not None:
            raise ValueError(
                "quantity/unit_price_minor sind nur bei calculation_mode=quantity erlaubt"
            )
        if percentage_rate_bps is not None or percentage_basis is not None:
            raise ValueError(
                "percentage_* Felder sind nur bei calculation_mode=percentage erlaubt"
            )
    elif mode == "quantity":
        if quantity is None or unit_price_minor is None:
            raise ValueError(
                "quantity und unit_price_minor sind bei calculation_mode=quantity erforderlich"
            )
        if estimate_manual_minor is not None:
            raise ValueError("estimate_manual_minor ist nur bei calculation_mode=fixed erlaubt")
    else:
        if percentage_rate_bps is None or percentage_basis is None:
            raise ValueError(
                "percentage_rate_bps und percentage_basis sind bei "
                "calculation_mode=percentage erforderlich"
            )
        if (percentage_basis == "item") != (percentage_basis_item_id is not None):
            raise ValueError(
                "percentage_basis_item_id ist genau bei percentage_basis=item erforderlich"
            )


def _check_contingency_fields(
    mode: ContingencyMode, rate_bps: int | None, fixed_minor: int | None
) -> None:
    if mode == "none" and (rate_bps is not None or fixed_minor is not None):
        raise ValueError("contingency_rate_bps/contingency_fixed_minor nur bei mode != none")
    if mode == "percent" and rate_bps is None:
        raise ValueError("contingency_rate_bps ist bei contingency_mode=percent erforderlich")
    if mode == "fixed" and fixed_minor is None:
        raise ValueError("contingency_fixed_minor ist bei contingency_mode=fixed erforderlich")


class CostItemCreate(BaseModel):
    section_id: UUID
    name: str = Field(min_length=1, max_length=160)
    semantic_key: str | None = None
    cost_kind: CostKind = "cash"
    status: CostItemStatus = "estimated"
    calculation_mode: CalculationMode = "fixed"
    estimate_manual_minor: int | None = Field(default=None, ge=0)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit_label: str | None = None
    unit_price_minor: int | None = Field(default=None, ge=0)
    percentage_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    percentage_basis: PercentageBasis | None = None
    percentage_basis_item_id: UUID | None = None
    quoted_amount_minor: int | None = Field(default=None, ge=0)
    committed_amount_minor: int | None = Field(default=None, ge=0)
    contingency_eligible: bool = True
    funding_tags: list[str] = Field(default_factory=list)
    vendor_label: str | None = None
    expected_start_on: date | None = None
    expected_end_on: date | None = None
    sort_order: int = 0
    note: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "CostItemCreate":
        _check_calculation_mode_fields(
            self.calculation_mode,
            self.estimate_manual_minor,
            self.quantity,
            self.unit_price_minor,
            self.percentage_rate_bps,
            self.percentage_basis,
            self.percentage_basis_item_id,
        )
        return self


class CostItemUpdate(BaseModel):
    section_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    cost_kind: CostKind | None = None
    status: CostItemStatus | None = None
    calculation_mode: CalculationMode | None = None
    estimate_manual_minor: int | None = Field(default=None, ge=0)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit_label: str | None = None
    unit_price_minor: int | None = Field(default=None, ge=0)
    percentage_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    percentage_basis: PercentageBasis | None = None
    percentage_basis_item_id: UUID | None = None
    quoted_amount_minor: int | None = Field(default=None, ge=0)
    committed_amount_minor: int | None = Field(default=None, ge=0)
    contingency_eligible: bool | None = None
    funding_tags: list[str] | None = None
    vendor_label: str | None = None
    expected_start_on: date | None = None
    expected_end_on: date | None = None
    sort_order: int | None = None
    note: str | None = None
    clear_fields: list[str] = Field(default_factory=list)


class CostItem(BaseModel):
    id: UUID
    scenario_id: UUID
    section_id: UUID
    semantic_key: str | None
    name: str
    cost_kind: CostKind
    status: CostItemStatus
    calculation_mode: CalculationMode
    estimate_manual_minor: int | None
    quantity: Decimal | None
    unit_label: str | None
    unit_price_minor: int | None
    percentage_rate_bps: int | None
    percentage_basis: PercentageBasis | None
    percentage_basis_item_id: UUID | None
    quoted_amount_minor: int | None
    committed_amount_minor: int | None
    contingency_eligible: bool
    funding_tags: list[str]
    vendor_label: str | None
    expected_start_on: date | None
    expected_end_on: date | None
    sort_order: int
    note: str | None
    # Provenienz des aktuellen Schaetzwerts (Plan 13.8 "kein unbelegter
    # KI-Text"): 'ai' nur, solange estimate_manual_minor unveraendert seit der
    # KI-Uebernahme ist - eine spaetere manuelle Bearbeitung setzt beides
    # wieder auf 'manual' zurueck (siehe update_cost_item).
    estimate_source: Literal["manual", "ai"]
    ai_operation_id: UUID | None
    ai_confidence: float | None
    ai_rationale: str | None
    estimate_minor: int
    forecast_minor: int
    actual_amount_minor: int
    version: int
    created_at: datetime
    updated_at: datetime


class CostScheduleEntryInput(BaseModel):
    due_on: date
    amount_minor: int = Field(gt=0)
    label: str | None = None
    sort_order: int = 0


class CostScheduleReplace(BaseModel):
    entries: list[CostScheduleEntryInput] = Field(default_factory=list)


class CostScheduleEntry(BaseModel):
    id: UUID
    due_on: date
    amount_minor: int
    status: ScheduleEntryStatus
    label: str | None
    sort_order: int


class ScenarioCalculation(BaseModel):
    scenario_id: UUID
    scenario_version: int
    input_fingerprint: str
    engine_version: str
    as_of_date: date
    estimated_cost_minor: int
    quoted_cost_minor: int
    committed_cost_minor: int
    forecast_cash_cost_minor: int
    non_cash_cost_minor: int
    contingency_minor: int
    project_cash_cost_minor: int
    project_economic_cost_minor: int
    actual_cost_minor: int
    remaining_forecast_minor: int
    per_item_estimate_minor: dict[str, int]
    per_item_forecast_minor: dict[str, int]
    warnings: list[str]


class ScenarioComparisonRow(BaseModel):
    scenario_id: UUID
    name: str
    scenario_kind: ScenarioKind
    status: ScenarioStatus
    estimated_cost_minor: int
    forecast_cash_cost_minor: int
    contingency_minor: int
    project_cash_cost_minor: int
    project_economic_cost_minor: int
    warnings: list[str]


class ScenarioComparison(BaseModel):
    rows: list[ScenarioComparisonRow]


class WishlistConvertResponse(BaseModel):
    project_id: UUID
    already_existed: bool


# ---------------------------------------------------------------------------
# Finanzierungsquellen (Plan 4.7/7.3)
# ---------------------------------------------------------------------------

FinancingSourceType = Literal[
    "cash_equity", "asset_sale", "commercial_loan", "subsidized_loan",
    "public_grant", "private_contribution", "non_cash_equity", "other",
]
FinancingSourceStatus = Literal[
    "idea", "requested", "likely", "approved", "contracted", "received", "rejected"
]


class FinancingSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    source_type: FinancingSourceType
    status: FinancingSourceStatus = "idea"
    planned_amount_minor: int = Field(ge=0)
    principal_limit_minor: int | None = Field(default=None, ge=0)
    available_on: date | None = None
    account_id: UUID | None = None
    savings_goal_id: UUID | None = None
    asset_id: UUID | None = None
    funding_program_version_id: UUID | None = None
    gross_amount_minor: int | None = Field(default=None, ge=0)
    deductions_minor: int | None = Field(default=None, ge=0)
    sort_order: int = 0
    note: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "FinancingSourceCreate":
        if self.source_type == "cash_equity" and self.account_id is None:
            raise ValueError("account_id ist bei source_type=cash_equity erforderlich")
        if self.source_type == "asset_sale" and (
            self.gross_amount_minor is not None and self.deductions_minor is not None
        ):
            expected = self.gross_amount_minor - self.deductions_minor
            if self.planned_amount_minor != expected:
                raise ValueError(
                    "planned_amount_minor muss bei asset_sale gross_amount_minor - "
                    "deductions_minor entsprechen"
                )
        return self


class FinancingSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: FinancingSourceStatus | None = None
    planned_amount_minor: int | None = Field(default=None, ge=0)
    principal_limit_minor: int | None = Field(default=None, ge=0)
    available_on: date | None = None
    account_id: UUID | None = None
    savings_goal_id: UUID | None = None
    asset_id: UUID | None = None
    gross_amount_minor: int | None = Field(default=None, ge=0)
    deductions_minor: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    note: str | None = None
    clear_fields: list[str] = Field(default_factory=list)


class CostDrawPreview(BaseModel):
    draws: list[DrawInput] = Field(default_factory=list)
    scheduled_cost_minor: int = 0
    allocated_minor: int = 0
    unallocated_credit_minor: int = 0
    uncovered_cost_minor: int = 0


class CostDrawPreviewInput(BaseModel):
    principal_limit_minor: int = Field(gt=0)
    starts_on: date
    draw_mode: Literal["immediate", "cost_plan", "manual"] = "cost_plan"
    draws: list[DrawInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_draws(self) -> "CostDrawPreviewInput":
        if self.draw_mode != "manual" and self.draws:
            raise ValueError("Abrufe duerfen nur im manuellen Modus angegeben werden")
        if sum(draw.amount_minor for draw in self.draws) > self.principal_limit_minor:
            raise ValueError("Abrufe duerfen den Darlehensrahmen nicht ueberschreiten")
        return self


class FinancingSource(BaseModel):
    id: UUID
    scenario_id: UUID
    name: str
    source_type: FinancingSourceType
    status: FinancingSourceStatus
    planned_amount_minor: int
    principal_limit_minor: int | None
    available_on: date | None
    account_id: UUID | None
    savings_goal_id: UUID | None
    asset_id: UUID | None
    funding_program_version_id: UUID | None
    gross_amount_minor: int | None
    deductions_minor: int | None
    sort_order: int
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    has_debt_plan: bool = False
    # Aus `source_snapshot.loan_terms` uebernommene Programmkonditionen (Laufzeit,
    # tilgungsfreie Zeit, ggf. Zinssatz). Fuellt den Tilgungsplan-Dialog vor, damit
    # bei einer uebernommenen Foerderung nur noch fehlt, was der Katalog wirklich
    # nicht kennt.
    funding_loan_terms: dict[str, Any] | None = None
    selected_loan_terms: dict[str, Any] | None = None
    debt_plan: DebtPlanDetail | None = None
    cost_draw_preview: CostDrawPreview | None = None


class FinancingConfigurationCreate(BaseModel):
    source: FinancingSourceCreate
    debt_plan: DebtPlanConfigurationInput | None = None
    selected_loan_terms: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate(self) -> "FinancingConfigurationCreate":
        is_loan = self.source.source_type in ("commercial_loan", "subsidized_loan")
        if is_loan != (self.debt_plan is not None):
            raise ValueError("Kreditquellen benoetigen eine vollstaendige Kreditkonfiguration")
        return self


class FinancingConfigurationUpdate(BaseModel):
    source: FinancingSourceUpdate
    debt_plan: DebtPlanConfigurationInput
    selected_loan_terms: dict[str, Any] | None = None


class FinancingConfigurationResponse(BaseModel):
    source: FinancingSource
    cost_draw_preview: CostDrawPreview


class EquityAvailabilityAccount(BaseModel):
    account_id: UUID
    account_name: str
    current_balance_minor: int
    reserved_by_other_projects_minor: int
    available_minor: int


class EquityAvailability(BaseModel):
    accounts: list[EquityAvailabilityAccount]


# ---------------------------------------------------------------------------
# Haushalts-, Ziel- und Folgekostenannahmen (Plan 4.9/7.4)
# ---------------------------------------------------------------------------

CashflowSource = Literal["manual", "history", "mixed"]


class CashflowAssumptionsInput(BaseModel):
    starting_liquidity_minor: int = Field(ge=0)
    monthly_net_income_minor: int = Field(ge=0)
    monthly_living_costs_minor: int = Field(ge=0)
    monthly_current_housing_minor: int = Field(ge=0)
    monthly_existing_debt_service_minor: int = Field(ge=0)
    monthly_other_commitments_minor: int = Field(ge=0)
    analysis_lookback_months: int = Field(default=12, ge=3, le=24)
    source: CashflowSource = "manual"


class CashflowAssumptions(CashflowAssumptionsInput):
    scenario_id: UUID
    source_snapshot: dict[str, object]
    updated_at: datetime


class CashflowSuggestion(BaseModel):
    starting_liquidity_minor: int
    monthly_net_income_minor: int
    monthly_living_costs_minor: int
    monthly_existing_debt_service_minor: int
    analysis_lookback_months: int
    source_snapshot: dict[str, object]


RecurringImpactType = Literal["income", "expense_increase", "expense_decrease"]
RecurringImpactFrequency = Literal["monthly", "quarterly", "semiannual", "yearly", "once"]
RecurringImpactConfidence = Literal["contracted", "estimated", "speculative"]


class RecurringImpactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    impact_type: RecurringImpactType
    amount_minor: int = Field(gt=0)
    frequency: RecurringImpactFrequency
    starts_on: date
    ends_on: date | None = None
    confidence: RecurringImpactConfidence = "estimated"
    include_in_affordability: bool = True
    note: str | None = None


class RecurringImpactUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    impact_type: RecurringImpactType | None = None
    amount_minor: int | None = Field(default=None, gt=0)
    frequency: RecurringImpactFrequency | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    confidence: RecurringImpactConfidence | None = None
    include_in_affordability: bool | None = None
    note: str | None = None
    clear_fields: list[str] = Field(default_factory=list)


class RecurringImpact(BaseModel):
    id: UUID
    scenario_id: UUID
    name: str
    impact_type: RecurringImpactType
    amount_minor: int
    frequency: RecurringImpactFrequency
    starts_on: date
    ends_on: date | None
    confidence: RecurringImpactConfidence
    include_in_affordability: bool
    note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class GoalAssumptionInput(BaseModel):
    savings_goal_id: UUID
    withdrawal_minor: int = Field(default=0, ge=0)
    withdrawal_on: date | None = None
    protected_minimum_minor: int = Field(default=0, ge=0)
    baseline_monthly_contribution_minor: int = Field(default=0, ge=0)
    project_monthly_contribution_minor: int = Field(default=0, ge=0)


class GoalAssumption(GoalAssumptionInput):
    id: UUID
    scenario_id: UUID
    created_at: datetime
    updated_at: datetime


class GoalAssumptionImpact(BaseModel):
    savings_goal_id: UUID
    goal_name: str
    current_amount_minor: int
    target_amount_minor: int
    baseline_reach_month: int | None
    project_reach_month: int | None
    delay_months: int | None
    reachable: bool
    protection_breached: bool
    max_free_withdrawal_minor: int


# ---------------------------------------------------------------------------
# Finanzierungsluecke, Leistbarkeit, Zeitreihe (Plan 5.7/5.8/7.4)
# ---------------------------------------------------------------------------


class FundingGap(BaseModel):
    cash_required_until_completion_minor: int
    planned_cash_funding_minor: int
    secured_cash_funding_minor: int
    planned_gap_minor: int
    secured_gap_minor: int


class AffordabilityMetrics(BaseModel):
    lowest_reserve_minor: int
    lowest_reserve_month: int
    negative_months_count: int
    peak_debt_service_minor: int
    monthly_surplus_before_minor: int
    monthly_surplus_after_minor: int
    first_shortfall_month: int | None
    reserve_breached: bool


class ScenarioFinancialSummary(BaseModel):
    scenario_id: UUID
    funding_gap: FundingGap
    affordability: AffordabilityMetrics | None
    goal_impacts: list[GoalAssumptionImpact]
    warnings: list[str]


class CashflowMonthRow(BaseModel):
    month: int
    baseline_income_minor: int
    baseline_living_costs_minor: int
    current_housing_minor: int
    existing_debt_service_minor: int
    other_commitments_minor: int
    project_cost_outflows_minor: int
    loan_and_grant_inflows_minor: int
    new_debt_service_minor: int
    recurring_project_impacts_minor: int
    goal_contributions_minor: int
    net_household_cashflow_minor: int
    liquid_reserve_end_minor: int


class ScenarioTimeline(BaseModel):
    scenario_id: UUID
    months: list[CashflowMonthRow]


# ---------------------------------------------------------------------------
# Projektabschluss (Plan 7.4)
# ---------------------------------------------------------------------------


class CompletionPreview(BaseModel):
    project_id: UUID
    already_applied: bool
    result_asset_name: str
    result_asset_value_minor: int
    debt_plans_to_activate: list[UUID]


class CompletionApplyResponse(BaseModel):
    project_id: UUID
    result_asset_id: UUID
    activated_debt_plan_ids: list[UUID]
    already_applied: bool


# ---------------------------------------------------------------------------
# Ist-Kosten-Zuordnung (Plan 4.6/7.2/P4)
# ---------------------------------------------------------------------------

ActualSource = Literal["transaction", "manual"]


class CostActualCreate(BaseModel):
    source: ActualSource
    amount_minor: int
    transaction_id: UUID | None = None
    occurred_on: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check_source_fields(self) -> "CostActualCreate":
        if self.source == "transaction":
            if self.transaction_id is None:
                raise ValueError("transaction_id erforderlich bei source=transaction")
            if self.occurred_on is not None:
                raise ValueError("occurred_on nicht erlaubt bei source=transaction")
        else:
            if self.occurred_on is None:
                raise ValueError("occurred_on erforderlich bei source=manual")
            if self.transaction_id is not None:
                raise ValueError("transaction_id nicht erlaubt bei source=manual")
        return self


class CostActual(BaseModel):
    id: UUID
    cost_item_id: UUID
    source: ActualSource
    transaction_id: UUID | None
    amount_minor: int
    occurred_on: date | None
    note: str | None
    is_archived_link: bool
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class TransactionCandidate(BaseModel):
    transaction_id: UUID
    account_id: UUID
    booked_date: date
    amount_minor: int
    payee_label: str | None
    note: str | None
    allocated_minor: int
    remaining_minor: int


class BulkAssignActualsItem(BaseModel):
    cost_item_id: UUID
    transaction_id: UUID
    amount_minor: int = Field(gt=0)
    note: str | None = None


class BulkAssignActualsRequest(BaseModel):
    items: list[BulkAssignActualsItem] = Field(min_length=1, max_length=500)


class BulkAssignActualsResult(BaseModel):
    created: list[CostActual]


# ---------------------------------------------------------------------------
# Uebersicht und Berichte (Plan 11.7/P5)
#
# Beide Modelle sind reine Aggregationen ueber bereits vorhandene
# Rechenfunktionen (calculate_scenario/calculate_financial_summary/
# debt_plan_service.calculate_schedule) -- keine zweite Kostenrechnung, damit
# Web/Dashboard und Vorhaben-Detailseite fuer dasselbe Projekt nie
# auseinanderlaufen koennen.
# ---------------------------------------------------------------------------


class ProjectDashboardNextPayment(BaseModel):
    project_id: UUID
    project_name: str
    cost_item_name: str
    due_on: date
    amount_minor: int


class ProjectDashboardWarning(BaseModel):
    project_id: UUID
    project_name: str
    code: str


class ProjectDashboardSummary(BaseModel):
    active_project_count: int
    next_payment: ProjectDashboardNextPayment | None
    total_secured_gap_minor: int
    top_warning: ProjectDashboardWarning | None


class ProjectReportRow(BaseModel):
    project_id: UUID
    project_name: str
    project_type: ProjectType
    scenario_id: UUID
    scenario_name: str
    forecast_cost_minor: int
    actual_cost_minor: int
    variance_minor: int
    financing_cost_minor: int
    warnings: list[str]


# ---------------------------------------------------------------------------
# KI-Kostenschaetzung (Nachtrag zum Plan nach Abschluss von P0-P5, siehe
# VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt "Nachtrag")
#
# Freitext-Projektbeschreibung -> Schaetzwertvorschlaege fuer die
# VORHANDENEN Kostenpositionen eines Szenarios (aus Vorlage oder manuell
# angelegt) - bewusst keine neuen Positionen/Abschnitte, das waere ein
# zweiter, riskanterer Funktionsumfang. Dieselbe Vorschau/Anwenden-Mechanik
# wie Kategorienvorschlaege (schemas/category.py), aber projekt-/
# szenariogebunden statt baumweit.
# ---------------------------------------------------------------------------


class CostAiSuggestionRequest(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    use_web_search: bool = False


class CostAiSearchSnippet(BaseModel):
    title: str
    url: str
    snippet: str


class CostAiItemSuggestion(BaseModel):
    cost_item_id: UUID
    section_name: str
    item_name: str
    current_estimate_minor: int
    # None, wenn das Modell fuer diese Position keine Schaetzung geliefert hat
    # - die Oberflaeche zeigt sie dann unveraendert, ohne Annehmen-Option.
    proposed_estimate_minor: int | None
    confidence: float
    rationale: str


class CostAiSuggestionPreview(BaseModel):
    operation_id: UUID
    expires_at: datetime
    scenario_id: UUID
    model_name: str | None
    search_requested: bool
    search_performed: bool
    # Erklaert eine Luecke zwischen search_requested und search_performed
    # (z. B. keine SearXNG-URL hinterlegt) - Plan 13.8 "kein unbelegter
    # KI-Text" gilt auch fuer stillschweigend uebergangene Anfragen.
    search_unavailable_reason: str | None = None
    search_snippets: list[CostAiSearchSnippet] = Field(default_factory=list)
    # Technischer Ausgang des Modell-Pipelines. `no_suggestions` ist eine
    # gueltige fachliche Enthaltung; timeout/invalid_response/model_error sind
    # dagegen Fehler und duerfen in der UI niemals wie "konnte nichts
    # schaetzen" aussehen.
    generation_status: Literal[
        "success", "no_suggestions", "timeout", "invalid_response", "model_error"
    ] = "success"
    error_code: str | None = None
    error_message: str | None = None
    suggestions: list[CostAiItemSuggestion]


class CostAiSuggestionAcceptItem(BaseModel):
    cost_item_id: UUID
    # Uebernommener Wert - per Default der Modellvorschlag, aber vom Nutzer
    # vor dem Bestaetigen ueberschreibbar (dieselbe "KI schlaegt vor, Mensch
    # entscheidet"-Zusicherung wie bei Kategorienvorschlaegen).
    estimate_minor: int = Field(ge=0)


class CostAiSuggestionApply(BaseModel):
    operation_id: UUID
    accepted: list[CostAiSuggestionAcceptItem] = Field(default_factory=list)
    request_id: str | None = Field(default=None, max_length=100)


class CostAiSuggestionResultItem(BaseModel):
    cost_item_id: UUID
    applied: bool
    # Grund, warum eine Position NICHT uebernommen wurde (z. B. inzwischen auf
    # calculation_mode != fixed umgestellt) - leer bei applied=True.
    skipped_reason: str | None = None


class CostAiSuggestionResult(BaseModel):
    updated_count: int
    items: list[CostAiSuggestionResultItem]
    idempotent_replay: bool = False
