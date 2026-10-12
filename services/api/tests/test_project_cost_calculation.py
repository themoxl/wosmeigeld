"""Reine Kostenformel-Tests fuer project_calculation_service (Plan 4.4/5.6).

Die vollen Doppelzaehlungs-Referenzfaelle aus Plan 15.2 (Konto+Sparziel, zwei
Projekte, Foerderkredit vs. Zuschuss, ...) brauchen die Finanzierungs-Services
aus P2 und werden dort ergaenzt. Hier: die reinen Kostenformeln selbst.
"""

import pytest

from app.services.project_calculation_service import (
    CostFormulaCycleError,
    CostItemInput,
    ScenarioContingencyInput,
    calculate_project_costs,
    resolve_cost_estimates,
)


def _item(**overrides: object) -> CostItemInput:
    base: dict[str, object] = {
        "id": "item-1",
        "section_id": "section-1",
        "calculation_mode": "fixed",
        "estimate_manual_minor": 10_000,
    }
    base.update(overrides)
    return CostItemInput(**base)  # type: ignore[arg-type]


def test_quantity_mode_rounds_half_up_to_minor_units() -> None:
    from decimal import Decimal

    item = _item(
        calculation_mode="quantity",
        estimate_manual_minor=None,
        quantity=Decimal("3.333"),
        unit_price_minor=100,
    )
    estimates = resolve_cost_estimates((item,))
    # 3.333 * 100 = 333.3 -> round half up -> 333
    assert estimates["item-1"] == 333


def test_percentage_basis_item_resolves_against_another_position() -> None:
    base = _item(id="base", estimate_manual_minor=100_000)
    percent = _item(
        id="percent",
        calculation_mode="percentage",
        estimate_manual_minor=None,
        percentage_rate_bps=1000,  # 10 %
        percentage_basis="item",
        percentage_basis_item_id="base",
    )
    estimates = resolve_cost_estimates((base, percent))
    assert estimates["percent"] == 10_000


def test_percentage_basis_section_direct_excludes_percentage_and_cancelled() -> None:
    direct_a = _item(id="a", section_id="s1", estimate_manual_minor=100_000)
    direct_b = _item(id="b", section_id="s1", estimate_manual_minor=50_000, status="cancelled")
    other_section = _item(id="c", section_id="s2", estimate_manual_minor=999_999)
    percent = _item(
        id="percent",
        section_id="s1",
        calculation_mode="percentage",
        estimate_manual_minor=None,
        percentage_rate_bps=1000,
        percentage_basis="section_direct",
    )
    estimates = resolve_cost_estimates((direct_a, direct_b, other_section, percent))
    # Nur direct_a zaehlt: b ist storniert, c liegt in anderem Abschnitt.
    assert estimates["percent"] == 10_000


def test_percentage_item_cycle_is_detected() -> None:
    a = _item(
        id="a",
        calculation_mode="percentage",
        estimate_manual_minor=None,
        percentage_rate_bps=1000,
        percentage_basis="item",
        percentage_basis_item_id="b",
    )
    b = _item(
        id="b",
        calculation_mode="percentage",
        estimate_manual_minor=None,
        percentage_rate_bps=1000,
        percentage_basis="item",
        percentage_basis_item_id="a",
    )
    with pytest.raises(CostFormulaCycleError):
        resolve_cost_estimates((a, b))


def test_forecast_precedence_committed_over_quoted_over_estimate() -> None:
    item = _item(
        estimate_manual_minor=100_00, quoted_amount_minor=110_00, committed_amount_minor=105_00
    )
    result = calculate_project_costs((item,))
    assert result.per_item_forecast_minor["item-1"] == 105_00


def test_cancelled_item_has_zero_forecast_but_keeps_history() -> None:
    item = _item(
        estimate_manual_minor=100_00, committed_amount_minor=105_00, status="cancelled"
    )
    result = calculate_project_costs((item,))
    assert result.per_item_forecast_minor["item-1"] == 0
    assert result.per_item_estimate_minor["item-1"] == 100_00


def test_reimbursement_reduces_actual_cost() -> None:
    item = _item(estimate_manual_minor=10_000_00, actual_amount_minor=10_000_00 - 1_000_00)
    result = calculate_project_costs((item,))
    assert result.actual_cost_minor == 9_000_00


def test_contingency_percent_only_applies_to_eligible_non_cancelled_cash_items() -> None:
    eligible = _item(id="eligible", estimate_manual_minor=100_000, contingency_eligible=True)
    ineligible = _item(id="ineligible", estimate_manual_minor=50_000, contingency_eligible=False)
    cancelled = _item(id="cancelled", estimate_manual_minor=999_999, status="cancelled")
    contingency = ScenarioContingencyInput(mode="percent", rate_bps=1000)
    result = calculate_project_costs((eligible, ineligible, cancelled), contingency)
    assert result.contingency_minor == 10_000
    assert result.project_cash_cost_minor == result.forecast_cash_cost_minor + 10_000


def test_secured_vs_planned_style_precedence_and_non_cash_kept_separate() -> None:
    cash_item = _item(id="cash", cost_kind="cash", estimate_manual_minor=100_000)
    non_cash_item = _item(id="non_cash", cost_kind="non_cash", estimate_manual_minor=20_000)
    result = calculate_project_costs((cash_item, non_cash_item))
    assert result.forecast_cash_cost_minor == 100_000
    assert result.non_cash_cost_minor == 20_000
    assert result.project_cash_cost_minor == 100_000
    assert result.project_economic_cost_minor == 120_000
