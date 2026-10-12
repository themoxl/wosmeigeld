import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(
        f"reserve-budget-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    household = client.post(
        "/v1/households", json={"name": "Rücklagen-Budget"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _categories(headers: dict[str, str]) -> dict[str, str]:
    groups = client.get("/v1/categories", headers=headers).json()
    return {item["name"]: item["id"] for group in groups for item in group["categories"]}


def _contract(headers: dict[str, str], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Vertrag",
        "amount_minor": 100_000,
        "frequency": "monthly",
        "next_due_date": "2026-09-15",
    }
    payload.update(overrides)
    response = client.post("/v1/contracts", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_forward_planning_layers_do_not_change_legacy_budget_math_or_double_count(
    household_headers: dict[str, str],
) -> None:
    categories = _categories(household_headers)
    rent_category = categories["Miete & Hausgeld"]
    groceries_category = categories["Lebensmittel"]
    allocation = client.put(
        "/v1/budgets/2026/9/allocations",
        json={
            "allocations": [
                {"category_id": rent_category, "planned_minor": 100_000},
                {"category_id": groceries_category, "planned_minor": 50_000},
            ]
        },
        headers=household_headers,
    )
    assert allocation.status_code == 200, allocation.text
    legacy_before = allocation.json()

    _contract(
        household_headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=300_000,
    )
    _contract(
        household_headers,
        name="Miete",
        category_id=rent_category,
        amount_minor=100_000,
    )
    _contract(
        household_headers,
        name="Hausrat Jahresbeitrag",
        amount_minor=120_000,
        frequency="yearly",
        next_due_date="2027-08-15",
        reserve_enabled=True,
        reserve_started_on="2026-08-15",
    )
    goal = client.post(
        "/v1/goals",
        json={
            "name": "Neues Handy",
            "target_amount_minor": 70_000,
            "planning_mode": "monthly_amount",
            "monthly_contribution_minor": 20_000,
            "include_in_planning": True,
        },
        headers=household_headers,
    )
    assert goal.status_code == 201, goal.text

    response = client.get("/v1/budgets/2026/9", headers=household_headers)
    assert response.status_code == 200, response.text
    summary = response.json()

    # Contract income is preferred even though the selected month has no
    # bookings. The rent allocation is reconciled with its contract and only
    # the unrelated groceries plan remains variable.
    assert summary["planning_expected_income_minor"] == 300_000
    assert summary["planning_income_basis"] == "contract"
    assert summary["regular_fixed_costs_monthly_minor"] == 100_000
    # September through the month before the August due date are eleven real
    # deposit opportunities. The legacy theoretical item below still exposes
    # its old 12-month normalization for compatibility, while the plan uses
    # the confirmed ledger balance and the actual remaining opportunities.
    assert summary["fixed_cost_reserves_monthly_minor"] == 10_910
    assert summary["variable_planned_budget_minor"] == 50_000
    assert summary["variable_planned_excluded_contract_minor"] == 100_000
    assert summary["goal_reserves_monthly_minor"] == 20_000
    assert summary["total_calculational_reserves_minor"] == 30_910
    assert summary["planning_truly_free_minor"] == 119_090
    assert summary["free_after_goal_reserves_minor"] == 119_090

    item = summary["fixed_cost_reserve_items"][0]
    assert item["contract_name"] == "Hausrat Jahresbeitrag"
    assert item["monthly_reserve_minor"] == 10_000
    assert item["accrued_minor"] == 10_000
    assert item["remaining_minor"] == 110_000

    # Additive planning data must not rewrite the accounting core.
    legacy_fields = (
        "total_income_minor",
        "total_planned_minor",
        "total_rollover_minor",
        "total_spent_minor",
        "total_remaining_minor",
        "savings_rate_percent",
        "bucket_needs_minor",
        "bucket_wants_minor",
        "bucket_savings_minor",
    )
    for field in legacy_fields:
        assert summary[field] == legacy_before[field]


def test_reserve_count_describes_the_same_set_as_the_reserve_amount(
    household_headers: dict[str, str],
) -> None:
    """Anzahl und Betrag der Fixkosten-Ruecklagen muessen zusammenpassen.

    Der Ruecklagenstart liegt hier NACH dem angezeigten Budgetmonat. Die
    theoretische Modellrechnung (fixed_cost_reserve_service) laesst den Vertrag
    dann weg, der Ruecklagentopf empfiehlt aber sehr wohl einen Betrag. Zaehlte
    das Feld die Modellposten, staende in der Oberflaeche "0 geglaettete
    Quartals-, Halbjahres- oder Jahreskosten" neben einem Abzug groesser null.
    """
    _contract(
        household_headers,
        name="Kfz-Versicherung",
        amount_minor=120_000,
        frequency="yearly",
        next_due_date="2027-08-15",
        reserve_enabled=True,
        reserve_started_on="2026-12-01",
    )

    summary = client.get("/v1/budgets/2026/9", headers=household_headers).json()
    assert summary["fixed_cost_reserves_monthly_minor"] > 0
    assert summary["fixed_cost_reserve_count"] > 0
    # Die Einzelposten stammen aus der anderen Quelle und duerfen hier leer
    # sein - genau deshalb taugen sie nicht als Zaehlung.
    assert summary["fixed_cost_reserve_items"] == []


def test_merged_contracts_are_excluded_from_fixed_cost_planning(
    household_headers: dict[str, str],
) -> None:
    first = _contract(household_headers, name="Miete alt", amount_minor=80_000)
    target = _contract(household_headers, name="Miete", amount_minor=100_000)
    preview = client.post(
        "/v1/contracts/merge/preview",
        json={"target_id": target["id"], "source_ids": [first["id"]]},
        headers=household_headers,
    )
    assert preview.status_code == 200, preview.text
    merged = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [first["id"]],
            "request_id": f"reserve-{uuid.uuid4().hex}",
        },
        headers=household_headers,
    )
    assert merged.status_code == 200, merged.text
    summary = client.get("/v1/budgets/2026/9", headers=household_headers).json()
    assert summary["regular_fixed_costs_monthly_minor"] == 100_000
    assert summary["fixed_costs_contract_count"] == 1
