"""Reiner Haushalts-Cashflow- und Leistbarkeitsrechner ohne DB-Zugriff
(Plan 5.7/5.8). Kombiniert Kostenplan-Abschlaege, Finanzierungszuflues,
Darlehens-Cashflows (aus `debt_calculation_service`) und Folgekosten zu einer
Monatsreihe ab `scenario.as_of_date`.

Vereinfachung (dokumentiert statt versteckt): der noch nicht verbrauchte
Kostenpuffer wird hier nicht monatsgenau als schrumpfende Reserve modelliert
(Plan 5.8, Absatz "gebundene Projektreserve") -- er fliesst nur als einmaliger
Abzug am Horizontende in `contingency_reserved_minor` der Zusammenfassung ein.
Diese Vereinfachung ist explizit vermerkt, damit sie spaeter gezielt
nachgezogen werden kann, statt sie durch stillschweigend falsche Praezision
zu verdecken.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RecurringImpactType = Literal["income", "expense_increase", "expense_decrease"]
RecurringImpactFrequency = Literal["monthly", "quarterly", "semiannual", "yearly", "once"]


class CashflowAssumptionsInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    starting_liquidity_minor: int = Field(ge=0)
    monthly_net_income_minor: int = Field(ge=0)
    monthly_living_costs_minor: int = Field(ge=0)
    monthly_current_housing_minor: int = Field(ge=0)
    monthly_existing_debt_service_minor: int = Field(ge=0)
    monthly_other_commitments_minor: int = Field(ge=0)
    minimum_liquidity_reserve_minor: int = Field(ge=0)


class CostOutflow(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: int = Field(ge=0)
    amount_minor: int = Field(ge=0)


class FinancingInflow(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: int = Field(ge=0)
    amount_minor: int = Field(ge=0)
    secured: bool


class DebtCashflow(BaseModel):
    """Pro-Monat-Cashflow eines einzelnen Darlehens, wie von
    `debt_calculation_service.calculate_debt_schedule` geliefert (bereits vom
    Aufrufer auf `month` reindiziert -- draw_minor zaehlt hier als
    Finanzierungszufluss, nicht nochmal in cash_outflow_minor enthalten)."""

    model_config = ConfigDict(frozen=True)

    month: int = Field(ge=0)
    draw_minor: int = Field(ge=0)
    cash_outflow_minor: int = Field(ge=0)


class RecurringImpactInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    impact_type: RecurringImpactType
    amount_minor: int = Field(gt=0)
    frequency: RecurringImpactFrequency
    starts_on_month: int = Field(ge=0)
    ends_on_month: int | None = Field(default=None, ge=0)
    include_in_affordability: bool = True


class GoalContribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    monthly_amount_minor: int = Field(ge=0)
    starts_on_month: int = Field(ge=0)


class HouseholdCashflowInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    assumptions: CashflowAssumptionsInput
    horizon_months: int = Field(ge=1, le=600)
    cost_outflows: tuple[CostOutflow, ...] = ()
    planned_financing_inflows: tuple[FinancingInflow, ...] = ()
    secured_financing_inflows: tuple[FinancingInflow, ...] = ()
    debt_cashflows: tuple[DebtCashflow, ...] = ()
    recurring_impacts: tuple[RecurringImpactInput, ...] = ()
    goal_contributions: tuple[GoalContribution, ...] = ()
    contingency_minor: int = Field(default=0, ge=0)
    contingency_reservation_start_month: int = Field(default=0, ge=0)


class MonthlyCashflowRow(BaseModel):
    model_config = ConfigDict(frozen=True)

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


class AffordabilitySummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    lowest_reserve_minor: int
    lowest_reserve_month: int
    negative_months_count: int
    peak_debt_service_minor: int
    monthly_surplus_before_minor: int
    monthly_surplus_after_minor: int
    first_shortfall_month: int | None
    reserve_breached: bool
    warnings: tuple[str, ...]
    rows: tuple[MonthlyCashflowRow, ...]


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _recurring_signed_amount_for_month(impact: RecurringImpactInput, month: int) -> int:
    """Vorzeichen: income/expense_decrease verbessern den Cashflow, +;
    expense_increase verschlechtert ihn, -. `once` wirkt nur im Startmonat."""
    if month < impact.starts_on_month:
        return 0
    if impact.ends_on_month is not None and month > impact.ends_on_month:
        return 0

    is_due = False
    if impact.frequency == "once":
        is_due = month == impact.starts_on_month
    else:
        step = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}[impact.frequency]
        is_due = (month - impact.starts_on_month) % step == 0

    if not is_due:
        return 0
    sign = 1 if impact.impact_type in ("income", "expense_decrease") else -1
    return sign * impact.amount_minor


def calculate_household_cashflow(data: HouseholdCashflowInput) -> AffordabilitySummary:
    cost_by_month: dict[int, int] = {}
    for outflow in data.cost_outflows:
        cost_by_month[outflow.month] = cost_by_month.get(outflow.month, 0) + outflow.amount_minor

    planned_inflow_by_month: dict[int, int] = {}
    for inflow in data.planned_financing_inflows:
        planned_inflow_by_month[inflow.month] = (
            planned_inflow_by_month.get(inflow.month, 0) + inflow.amount_minor
        )

    debt_draw_by_month: dict[int, int] = {}
    debt_outflow_by_month: dict[int, int] = {}
    for row in data.debt_cashflows:
        debt_draw_by_month[row.month] = debt_draw_by_month.get(row.month, 0) + row.draw_minor
        debt_outflow_by_month[row.month] = (
            debt_outflow_by_month.get(row.month, 0) + row.cash_outflow_minor
        )

    contribution_by_month: dict[int, int] = {}
    for goal in data.goal_contributions:
        for m in range(goal.starts_on_month, data.horizon_months):
            contribution_by_month[m] = contribution_by_month.get(m, 0) + goal.monthly_amount_minor

    rows: list[MonthlyCashflowRow] = []
    reserve = Decimal(data.assumptions.starting_liquidity_minor)
    lowest_reserve = reserve
    lowest_reserve_month = 0
    negative_months = 0
    peak_debt_service = 0
    first_shortfall_month: int | None = None
    contingency_reserved = False

    for month in range(data.horizon_months):
        project_cost_outflows = cost_by_month.get(month, 0)
        loan_and_grant_inflows = planned_inflow_by_month.get(month, 0) + debt_draw_by_month.get(
            month, 0
        )
        new_debt_service = debt_outflow_by_month.get(month, 0)
        recurring_signed = sum(
            _recurring_signed_amount_for_month(impact, month)
            for impact in data.recurring_impacts
            if impact.include_in_affordability
        )
        goal_contributions = contribution_by_month.get(month, 0)

        net = (
            data.assumptions.monthly_net_income_minor
            - data.assumptions.monthly_living_costs_minor
            - data.assumptions.monthly_current_housing_minor
            - data.assumptions.monthly_existing_debt_service_minor
            - data.assumptions.monthly_other_commitments_minor
            - project_cost_outflows
            + loan_and_grant_inflows
            - new_debt_service
            + recurring_signed
            - goal_contributions
        )
        reserve += Decimal(net)

        if month >= data.contingency_reservation_start_month and not contingency_reserved:
            reserve -= Decimal(data.contingency_minor)
            contingency_reserved = True

        if reserve < lowest_reserve:
            lowest_reserve = reserve
            lowest_reserve_month = month
        if net < 0:
            negative_months += 1
        below_reserve = reserve < data.assumptions.minimum_liquidity_reserve_minor
        if below_reserve and first_shortfall_month is None:
            first_shortfall_month = month
        peak_debt_service = max(peak_debt_service, new_debt_service)

        rows.append(
            MonthlyCashflowRow(
                month=month,
                baseline_income_minor=data.assumptions.monthly_net_income_minor,
                baseline_living_costs_minor=data.assumptions.monthly_living_costs_minor,
                current_housing_minor=data.assumptions.monthly_current_housing_minor,
                existing_debt_service_minor=data.assumptions.monthly_existing_debt_service_minor,
                other_commitments_minor=data.assumptions.monthly_other_commitments_minor,
                project_cost_outflows_minor=project_cost_outflows,
                loan_and_grant_inflows_minor=loan_and_grant_inflows,
                new_debt_service_minor=new_debt_service,
                recurring_project_impacts_minor=recurring_signed,
                goal_contributions_minor=goal_contributions,
                net_household_cashflow_minor=net,
                liquid_reserve_end_minor=_round_minor(reserve),
            )
        )

    surplus_before = (
        data.assumptions.monthly_net_income_minor
        - data.assumptions.monthly_living_costs_minor
        - data.assumptions.monthly_current_housing_minor
        - data.assumptions.monthly_existing_debt_service_minor
        - data.assumptions.monthly_other_commitments_minor
    )
    # Bezugsmonat ist der mit der hoechsten Kapitaldienstlast, nicht Monat 0.
    # Monat 0 war falsch: dort ist das Darlehen in aller Regel noch gar nicht
    # abgerufen, der Kapitaldienst also 0 - "mit Vorhaben" zeigte dann exakt
    # denselben Wert wie "ohne Vorhaben". Die wiederkehrenden Auswirkungen
    # desselben Monats gehoeren dazu: Folgekosten wie Grundsteuer oder
    # Ruecklage belasten dauerhaft, eine ab einem Stichtag geplante
    # Gehaltsaenderung entlastet dauerhaft. Fehlten sie hier, waere jede
    # eingeplante Veraenderung in der Kennzahl unsichtbar, obwohl die
    # Monatsreihe sie laengst korrekt fuehrt.
    if rows:
        reference = max(rows, key=lambda row: row.new_debt_service_minor)
        surplus_after = (
            surplus_before
            + reference.recurring_project_impacts_minor
            - reference.new_debt_service_minor
        )
    else:
        surplus_after = surplus_before

    warnings: list[str] = []
    reserve_breached = lowest_reserve < data.assumptions.minimum_liquidity_reserve_minor
    if reserve_breached:
        warnings.append("liquidity_reserve_breached")
    if negative_months > 0:
        warnings.append("funding_gap")

    return AffordabilitySummary(
        lowest_reserve_minor=_round_minor(lowest_reserve),
        lowest_reserve_month=lowest_reserve_month,
        negative_months_count=negative_months,
        peak_debt_service_minor=peak_debt_service,
        monthly_surplus_before_minor=surplus_before,
        monthly_surplus_after_minor=surplus_after,
        first_shortfall_month=first_shortfall_month,
        reserve_breached=reserve_breached,
        warnings=tuple(warnings),
        rows=tuple(rows),
    )
