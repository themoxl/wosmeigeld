"""Finanzierungsquellen, Haushaltsannahmen, Folgekosten, Zeitreihe und
Projektabschluss (Plan 7.3/7.4, P2)."""

import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"projfin-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-8")
    household = client.post(
        "/v1/households", json={"name": "Finanzierung-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_project(headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/v1/projects",
        json={"name": "Finanzierungsprojekt", "project_type": "home_build"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _baseline_scenario(headers: dict[str, str], project_id: str) -> dict[str, Any]:
    scenarios = client.get(f"/v1/projects/{project_id}/scenarios", headers=headers).json()
    return dict(scenarios[0])


def test_financing_source_crud_and_concurrency(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    created = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={"name": "Erspartes", "source_type": "cash_equity", "planned_amount_minor": 10_000_00},
        headers=household_headers,
    )
    assert created.status_code == 422  # cash_equity ohne account_id ist ungueltig

    account = client.post(
        "/v1/accounts", json={"name": "Giro", "account_type": "checking"}, headers=household_headers
    ).json()
    ok = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Erspartes",
            "source_type": "cash_equity",
            "planned_amount_minor": 10_000_00,
            "account_id": account["id"],
        },
        headers=household_headers,
    )
    assert ok.status_code == 201, ok.text
    source = ok.json()
    assert source["version"] == 1
    assert source["has_debt_plan"] is False

    listed = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    assert len(listed) == 1

    without_header = client.patch(
        f"/v1/projects/{project['id']}/financing-sources/{source['id']}",
        json={"note": "x"},
        headers=household_headers,
    )
    assert without_header.status_code == 428

    stale = client.patch(
        f"/v1/projects/{project['id']}/financing-sources/{source['id']}",
        json={"note": "x"},
        headers={**household_headers, "If-Match": '"9"'},
    )
    assert stale.status_code == 409

    updated = client.patch(
        f"/v1/projects/{project['id']}/financing-sources/{source['id']}",
        json={"status": "approved"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["status"] == "approved"

    deleted = client.delete(
        f"/v1/projects/{project['id']}/financing-sources/{source['id']}", headers=household_headers
    )
    assert deleted.status_code == 204
    assert (
        client.get(
            f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
            headers=household_headers,
        ).json()
        == []
    )


def test_equity_availability_lists_free_and_reserved_amounts(
    household_headers: dict[str, str],
) -> None:
    account = client.post(
        "/v1/accounts",
        json={"name": "Tagesgeld", "account_type": "savings", "opening_balance_minor": 10_000_00},
        headers=household_headers,
    ).json()
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Anteil",
            "source_type": "cash_equity",
            "planned_amount_minor": 4_000_00,
            "account_id": account["id"],
        },
        headers=household_headers,
    )

    availability = client.get(
        f"/v1/projects/{project['id']}/equity-availability", headers=household_headers
    ).json()
    entry = next(a for a in availability["accounts"] if a["account_id"] == account["id"])
    # Diese eigene, noch nicht ausgewaehlte Reservierung zaehlt fuer die
    # Sicht dieses Projekts nicht als "von anderen reserviert".
    assert entry["current_balance_minor"] == 10_000_00
    assert entry["reserved_by_other_projects_minor"] == 0
    assert entry["available_minor"] == 10_000_00


def test_cashflow_assumptions_suggest_and_save(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    suggestion = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/assumptions/suggest",
        headers=household_headers,
    )
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["monthly_net_income_minor"] == 0

    saved = client.put(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/assumptions",
        json={
            "starting_liquidity_minor": 20_000_00,
            "monthly_net_income_minor": 4_000_00,
            "monthly_living_costs_minor": 2_000_00,
            "monthly_current_housing_minor": 800_00,
            "monthly_existing_debt_service_minor": 0,
            "monthly_other_commitments_minor": 0,
        },
        headers=household_headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["monthly_net_income_minor"] == 4_000_00

    fetched_again = client.put(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/assumptions",
        json={
            "starting_liquidity_minor": 25_000_00,
            "monthly_net_income_minor": 4_000_00,
            "monthly_living_costs_minor": 2_000_00,
            "monthly_current_housing_minor": 800_00,
            "monthly_existing_debt_service_minor": 0,
            "monthly_other_commitments_minor": 0,
        },
        headers=household_headers,
    ).json()
    assert fetched_again["starting_liquidity_minor"] == 25_000_00  # Upsert, kein Duplikat


def test_recurring_impacts_crud(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    created = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/recurring-impacts",
        json={
            "name": "Neue Nebenkosten",
            "impact_type": "expense_increase",
            "amount_minor": 150_00,
            "frequency": "monthly",
            "starts_on": "2027-06-01",
        },
        headers=household_headers,
    )
    assert created.status_code == 201, created.text
    impact = created.json()

    listed = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/recurring-impacts",
        headers=household_headers,
    ).json()
    assert len(listed) == 1

    updated = client.patch(
        f"/v1/projects/{project['id']}/recurring-impacts/{impact['id']}",
        json={"amount_minor": 200_00},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert updated.status_code == 200
    assert updated.json()["amount_minor"] == 200_00

    deleted = client.delete(
        f"/v1/projects/{project['id']}/recurring-impacts/{impact['id']}", headers=household_headers
    )
    assert deleted.status_code == 204


def test_timeline_is_empty_without_assumptions_and_filled_after(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    timeline_url = f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/timeline"
    empty = client.get(timeline_url, headers=household_headers).json()
    assert empty["months"] == []

    client.put(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/assumptions",
        json={
            "starting_liquidity_minor": 20_000_00,
            "monthly_net_income_minor": 4_000_00,
            "monthly_living_costs_minor": 2_000_00,
            "monthly_current_housing_minor": 800_00,
            "monthly_existing_debt_service_minor": 0,
            "monthly_other_commitments_minor": 0,
        },
        headers=household_headers,
    )
    filled = client.get(timeline_url, headers=household_headers).json()
    assert len(filled["months"]) == scenario["calculation_horizon_months"]
    assert filled["months"][0]["liquid_reserve_end_minor"] > 0


def test_atomic_loan_configuration_hydrates_and_caps_cost_plan_draws(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
        json={"name": "Kauf und Sanierung", "cost_class": "direct"},
        headers=household_headers,
    ).json()
    item = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        json={
            "name": "Kauf zuerst, Dach spaeter",
            "section_id": section["id"],
            "calculation_mode": "fixed",
            "estimate_manual_minor": 150_000_00,
        },
        headers=household_headers,
    ).json()
    starts_on = date.today()
    later = starts_on.replace(year=starts_on.year + 3)
    schedule = client.put(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/schedule",
        json={
            "entries": [
                {"due_on": starts_on.isoformat(), "amount_minor": 100_000_00},
                {"due_on": later.isoformat(), "amount_minor": 50_000_00},
            ]
        },
        headers=household_headers,
    )
    assert schedule.status_code == 200, schedule.text

    endpoint = (
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}"
        "/financing-configurations"
    )
    created = client.post(
        endpoint,
        json={
            "source": {
                "name": "Foerderkredit – Variante 10/2/10",
                "source_type": "subsidized_loan",
                "planned_amount_minor": 120_000_00,
                "principal_limit_minor": 120_000_00,
                "available_on": starts_on.isoformat(),
            },
            "selected_loan_terms": {"variant_id": "10-2-10", "rate_as_of": "2026-08-01"},
            "debt_plan": {
                "name": "Foerderkredit – Variante 10/2/10",
                "principal_limit_minor": 120_000_00,
                "starts_on": starts_on.isoformat(),
                "amortization_mode": "initial_repayment",
                "initial_repayment_rate_bps": 200,
                "repayment_free_months": 24,
                "draw_mode": "cost_plan",
                "rate_periods": [
                    {
                        "starts_after_month": 0,
                        "ends_after_month": 119,
                        "nominal_rate_bps_annual": 275,
                        "rate_kind": "contracted",
                    },
                    {
                        "starts_after_month": 120,
                        "nominal_rate_bps_annual": 450,
                        "rate_kind": "assumed",
                    },
                ],
            },
        },
        headers=household_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert [draw["amount_minor"] for draw in body["source"]["debt_plan"]["draws"]] == [
        100_000_00,
        20_000_00,
    ]
    assert sum(draw["amount_minor"] for draw in body["source"]["debt_plan"]["draws"]) == 120_000_00
    assert body["cost_draw_preview"]["uncovered_cost_minor"] == 30_000_00
    assert body["source"]["selected_loan_terms"]["variant_id"] == "10-2-10"
    assert body["source"]["debt_plan"]["rate_periods"][1]["rate_kind"] == "assumed"

    # Erneutes Lesen liefert die Eingabedaten selbst, nicht eine aus dem
    # berechneten Verlauf erratene Teilmenge.
    listed = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    saved = listed[0]
    assert saved["debt_plan"]["repayment_free_months"] == 24
    assert len(saved["debt_plan"]["rate_periods"]) == 2
    assert len(saved["debt_plan"]["draws"]) == 2

    client.put(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/assumptions",
        json={
            "starting_liquidity_minor": 10_000_00,
            "monthly_net_income_minor": 4_000_00,
            "monthly_living_costs_minor": 2_000_00,
            "monthly_current_housing_minor": 700_00,
            "monthly_existing_debt_service_minor": 0,
            "monthly_other_commitments_minor": 0,
        },
        headers=household_headers,
    )
    timeline = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/timeline",
        headers=household_headers,
    ).json()
    assert sum(row["loan_and_grant_inflows_minor"] for row in timeline["months"]) == 120_000_00
    assert timeline["months"][0]["loan_and_grant_inflows_minor"] == 100_000_00
    assert any(
        row["month"] >= 35 and row["loan_and_grant_inflows_minor"] == 20_000_00
        for row in timeline["months"]
    )

    invalid = client.post(
        endpoint,
        json={
            "source": {
                "name": "Unvollstaendig",
                "source_type": "commercial_loan",
                "planned_amount_minor": 50_000_00,
            },
            "debt_plan": {
                "name": "Unvollstaendig",
                "principal_limit_minor": 50_000_00,
                "starts_on": starts_on.isoformat(),
                "amortization_mode": "initial_repayment",
                "initial_repayment_rate_bps": 200,
                "rate_periods": [],
            },
        },
        headers=household_headers,
    )
    assert invalid.status_code == 422
    listed_after = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    assert len(listed_after) == 1


def test_financing_draw_preview_uses_current_amount_start_and_mode(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
        json={"name": "Kauf und spaetere Arbeiten", "cost_class": "direct"},
        headers=household_headers,
    ).json()
    item = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        json={
            "name": "Zahlungsfolge",
            "section_id": section["id"],
            "calculation_mode": "fixed",
            "estimate_manual_minor": 150_000_00,
        },
        headers=household_headers,
    ).json()
    client.put(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/schedule",
        json={
            "entries": [
                {"due_on": "2027-01-01", "amount_minor": 100_000_00},
                {"due_on": "2030-01-01", "amount_minor": 50_000_00},
            ]
        },
        headers=household_headers,
    )
    endpoint = (
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}"
        "/financing-draw-preview"
    )

    first = client.post(
        endpoint,
        json={
            "principal_limit_minor": 110_000_00,
            "starts_on": "2027-01-01",
            "draw_mode": "cost_plan",
        },
        headers=household_headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["draws"] == [
        {"occurs_after_month": 0, "amount_minor": 100_000_00, "status": "planned"},
        {"occurs_after_month": 36, "amount_minor": 10_000_00, "status": "planned"},
    ]

    changed = client.post(
        endpoint,
        json={
            "principal_limit_minor": 120_000_00,
            "starts_on": "2028-01-01",
            "draw_mode": "cost_plan",
        },
        headers=household_headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["draws"] == [
        {"occurs_after_month": 0, "amount_minor": 100_000_00, "status": "planned"},
        {"occurs_after_month": 24, "amount_minor": 20_000_00, "status": "planned"},
    ]

    immediate = client.post(
        endpoint,
        json={
            "principal_limit_minor": 120_000_00,
            "starts_on": "2028-01-01",
            "draw_mode": "immediate",
        },
        headers=household_headers,
    )
    assert immediate.status_code == 200, immediate.text
    assert immediate.json()["draws"] == []
    assert immediate.json()["allocated_minor"] == 120_000_00


def test_completion_preview_and_apply_is_idempotent(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
        json={"name": "Abschnitt", "cost_class": "direct"},
        headers=household_headers,
    ).json()
    item = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        json={
            "name": "Position",
            "section_id": section["id"],
            "calculation_mode": "fixed",
            "estimate_manual_minor": 50_000_00,
        },
        headers=household_headers,
    ).json()
    client.put(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/schedule",
        json={"entries": [{"due_on": "2027-01-01", "amount_minor": item["forecast_minor"]}]},
        headers=household_headers,
    )
    client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/select", headers=household_headers
    )

    preview = client.post(
        f"/v1/projects/{project['id']}/complete/preview", headers=household_headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["already_applied"] is False
    assert preview.json()["result_asset_value_minor"] == 50_000_00

    first_apply = client.post(
        f"/v1/projects/{project['id']}/complete/apply", headers=household_headers
    )
    assert first_apply.status_code == 200, first_apply.text
    body = first_apply.json()
    assert body["already_applied"] is False
    asset_id = body["result_asset_id"]

    second_apply = client.post(
        f"/v1/projects/{project['id']}/complete/apply", headers=household_headers
    )
    assert second_apply.status_code == 200
    second_body = second_apply.json()
    assert second_body["already_applied"] is True
    assert second_body["result_asset_id"] == asset_id  # keine zweite Asset-Zeile

    assets = client.get("/v1/assets", headers=household_headers).json()
    matching = [a for a in assets if a["id"] == asset_id]
    assert len(matching) == 1
    assert matching[0]["current_value_minor"] == 50_000_00


def test_financing_source_write_on_foreign_project_is_404(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    source = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={"name": "Zuschuss", "source_type": "public_grant", "planned_amount_minor": 1_000_00},
        headers=household_headers,
    ).json()

    foreign_token = _signup(
        f"projfin-foreign-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-9"
    )
    foreign_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(foreign_token)
    ).json()
    foreign_headers = {**_auth_headers(foreign_token), "X-Household-Id": foreign_household["id"]}

    assert (
        client.patch(
            f"/v1/projects/{project['id']}/financing-sources/{source['id']}",
            json={"note": "x"},
            headers={**foreign_headers, "If-Match": '"1"'},
        ).status_code
        == 404
    )
    delete_url = f"/v1/projects/{project['id']}/financing-sources/{source['id']}"
    assert client.delete(delete_url, headers=foreign_headers).status_code == 404


def test_financing_source_viewer_cannot_write(household_headers: dict[str, str]) -> None:
    household_id = household_headers["X-Household-Id"]
    viewer_headers = _invite_member(client, household_headers, household_id, "viewer", "projfin-v")
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    create = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={"name": "Zuschuss", "source_type": "public_grant", "planned_amount_minor": 1_000_00},
        headers=viewer_headers,
    )
    assert create.status_code == 403
