"""Reine Kostenformeln ohne DB-Zugriff (Plan Abschnitt 5.6 und 4.4).

Berechnet Schaetzung/Forecast je Kostenposition sowie die Aggregate eines
Szenarios. Nimmt typisierte, unveraenderliche Pydantic-Modelle entgegen und
liefert deterministische, centgenaue Ergebnisse -- keine DB-Zugriffe, kein
`date.today()`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

CalculationMode = Literal["fixed", "quantity", "percentage"]
PercentageBasis = Literal["item", "section_direct", "project_direct"]
CostItemStatus = Literal["estimated", "quoted", "committed", "partially_paid", "paid", "cancelled"]
ContingencyMode = Literal["none", "percent", "fixed"]

ALLOWED_FUNDING_TAGS = frozenset(
    {
        "land_purchase", "construction", "planning", "permits", "acquisition_fees",
        "energy_efficiency", "heating", "renewable_energy", "accessibility", "renovation",
        "equipment", "transition", "other",
    }
)


class CostFormulaCycleError(ValueError):
    """422 `cost_formula_cycle` (Plan 4.4): Prozentpositionen duerfen nicht,
    auch nicht indirekt, Basis anderer Prozentpositionen sein."""

    code = "cost_formula_cycle"


class CostItemInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    section_id: str
    cost_kind: Literal["cash", "non_cash"] = "cash"
    status: CostItemStatus = "estimated"
    calculation_mode: CalculationMode
    estimate_manual_minor: int | None = None
    quantity: Decimal | None = None
    unit_price_minor: int | None = None
    percentage_rate_bps: int | None = None
    percentage_basis: PercentageBasis | None = None
    percentage_basis_item_id: str | None = None
    quoted_amount_minor: int | None = None
    committed_amount_minor: int | None = None
    contingency_eligible: bool = True
    actual_amount_minor: int = 0


class ScenarioContingencyInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: ContingencyMode = "none"
    rate_bps: int | None = None
    fixed_minor: int | None = None


class CostCalculationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_item_estimate_minor: dict[str, int]
    per_item_forecast_minor: dict[str, int]
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


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _forecast_for_item(item: CostItemInput, estimate_minor: int) -> int:
    """Plan 2.1 'Aktuelles Kostenforecast': Auftrag > Angebot > Schaetzung;
    bei `cancelled` immer 0."""
    if item.status == "cancelled":
        return 0
    if item.committed_amount_minor is not None:
        return item.committed_amount_minor
    if item.quoted_amount_minor is not None:
        return item.quoted_amount_minor
    return estimate_minor


def resolve_cost_estimates(items: tuple[CostItemInput, ...]) -> dict[str, int]:
    """Loest die Schaetzung jeder Position auf, inklusive rekursiver
    Prozentbasis. Wirft `CostFormulaCycleError` bei einem direkten oder
    indirekten Zyklus ueber `percentage_basis_item_id`."""
    items_by_id = {item.id: item for item in items}
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def resolve(item_id: str) -> int:
        if item_id in memo:
            return memo[item_id]
        if item_id not in items_by_id:
            raise CostFormulaCycleError(f"Unbekannte Basis-Position: {item_id}")
        if item_id in visiting:
            raise CostFormulaCycleError(
                f"Zyklische Prozentbasis entdeckt bei Position {item_id}."
            )
        visiting.add(item_id)
        item = items_by_id[item_id]

        if item.calculation_mode == "fixed":
            value = item.estimate_manual_minor or 0
        elif item.calculation_mode == "quantity":
            assert item.quantity is not None and item.unit_price_minor is not None
            value = _round_minor(item.quantity * Decimal(item.unit_price_minor))
        else:
            assert item.percentage_rate_bps is not None and item.percentage_basis is not None
            if item.percentage_basis == "item":
                assert item.percentage_basis_item_id is not None
                basis_value = resolve(item.percentage_basis_item_id)
            elif item.percentage_basis == "section_direct":
                basis_value = sum(
                    resolve(other.id)
                    for other in items
                    if other.section_id == item.section_id
                    and other.calculation_mode != "percentage"
                    and other.status != "cancelled"
                )
            else:
                basis_value = sum(
                    resolve(other.id)
                    for other in items
                    if other.calculation_mode != "percentage" and other.status != "cancelled"
                )
            value = _round_minor(
                Decimal(basis_value) * Decimal(item.percentage_rate_bps) / Decimal(10_000)
            )

        visiting.discard(item_id)
        memo[item_id] = value
        return value

    for item in items:
        resolve(item.id)
    return memo


_NO_CONTINGENCY = ScenarioContingencyInput()


def calculate_project_costs(
    items: tuple[CostItemInput, ...],
    contingency: ScenarioContingencyInput | None = None,
) -> CostCalculationResult:
    """Aggregiert Schaetzung, Forecast, Puffer und Istkosten eines Szenarios
    (Plan 5.6). Cash- und Non-Cash-Kosten bleiben getrennt (Leitsatz 5)."""
    contingency = contingency if contingency is not None else _NO_CONTINGENCY
    estimates = resolve_cost_estimates(items)
    forecasts = {item.id: _forecast_for_item(item, estimates[item.id]) for item in items}

    cash_items = [item for item in items if item.cost_kind == "cash"]
    non_cash_items = [item for item in items if item.cost_kind == "non_cash"]

    estimated_cost_minor = sum(estimates[item.id] for item in cash_items)
    quoted_cost_minor = sum(
        item.quoted_amount_minor if item.quoted_amount_minor is not None else estimates[item.id]
        for item in cash_items
        if item.status != "cancelled"
    )
    committed_cost_minor = sum(
        item.committed_amount_minor
        if item.committed_amount_minor is not None
        else (
            item.quoted_amount_minor
            if item.quoted_amount_minor is not None
            else estimates[item.id]
        )
        for item in cash_items
        if item.status != "cancelled"
    )
    forecast_cash_cost_minor = sum(forecasts[item.id] for item in cash_items)
    non_cash_cost_minor = sum(forecasts[item.id] for item in non_cash_items)

    eligible_base = sum(
        forecasts[item.id]
        for item in cash_items
        if item.contingency_eligible and item.status != "cancelled"
    )
    if contingency.mode == "none":
        contingency_minor = 0
    elif contingency.mode == "percent":
        assert contingency.rate_bps is not None
        contingency_minor = _round_minor(
            Decimal(eligible_base) * Decimal(contingency.rate_bps) / Decimal(10_000)
        )
    else:
        assert contingency.fixed_minor is not None
        contingency_minor = contingency.fixed_minor

    project_cash_cost_minor = forecast_cash_cost_minor + contingency_minor
    project_economic_cost_minor = project_cash_cost_minor + non_cash_cost_minor
    actual_cost_minor = sum(item.actual_amount_minor for item in items)
    remaining_forecast_minor = forecast_cash_cost_minor - actual_cost_minor

    return CostCalculationResult(
        per_item_estimate_minor=estimates,
        per_item_forecast_minor=forecasts,
        estimated_cost_minor=estimated_cost_minor,
        quoted_cost_minor=quoted_cost_minor,
        committed_cost_minor=committed_cost_minor,
        forecast_cash_cost_minor=forecast_cash_cost_minor,
        non_cash_cost_minor=non_cash_cost_minor,
        contingency_minor=contingency_minor,
        project_cash_cost_minor=project_cash_cost_minor,
        project_economic_cost_minor=project_economic_cost_minor,
        actual_cost_minor=actual_cost_minor,
        remaining_forecast_minor=remaining_forecast_minor,
    )
