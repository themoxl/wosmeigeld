"""Uebersichtskarte und Vorhaben-Berichtsregister (Plan 11.7/P5).

Beide Endpunkte sind reine Aggregationen ueber bereits getestete
Rechenfunktionen (calculate_scenario/calculate_financial_summary/
debt_plan_service.calculate_schedule, siehe test_project_reference_cases.py
und test_debt_plans.py) - hier wird nur die Aggregation selbst geprueft:
welche Projekte zaehlen als "aktiv", dass ein ueberdecktes Szenario die
Luecke eines anderen nicht rechnerisch ausgleicht, welche Warnung als
"wichtigste" gilt, und dass Forecast/Ist/Abweichung/Finanzierungskosten im
Berichtsregister exakt den Werten entsprechen, die die Detailseite fuer
dasselbe Projekt zeigen wuerde.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"projdash-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-8")
    household = client.post(
        "/v1/households", json={"name": "Dashboard-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_project(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Dashboardprojekt", "project_type": "home_build"}
    body.update(overrides)
    response = client.post("/v1/projects", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _baseline_scenario(headers: dict[str, str], project_id: str) -> dict[str, Any]:
    scenarios = client.get(f"/v1/projects/{project_id}/scenarios", headers=headers).json()
    assert len(scenarios) == 1
    return dict(scenarios[0])


def _create_section(headers: dict[str, str], project_id: str, scenario_id: str) -> dict[str, Any]:
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-sections",
        json={"name": "Abschnitt", "cost_class": "direct"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _create_cash_item(
    headers: dict[str, str],
    project_id: str,
    scenario_id: str,
    section_id: str,
    amount_minor: int,
    name: str = "Position",
) -> dict[str, Any]:
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-items",
        json={
            "name": name,
            "section_id": section_id,
            "calculation_mode": "fixed",
            "estimate_manual_minor": amount_minor,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _schedule(
    headers: dict[str, str],
    project_id: str,
    item: dict[str, Any],
    due_on: str,
    amount_minor: int,
) -> None:
    response = client.put(
        f"/v1/projects/{project_id}/cost-items/{item['id']}/schedule",
        json={"entries": [{"due_on": due_on, "amount_minor": amount_minor}]},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _select_scenario(headers: dict[str, str], project_id: str, scenario_id: str) -> None:
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/select", headers=headers
    )
    assert response.status_code == 200, response.text


def _create_account(headers: dict[str, str], opening_balance_minor: int) -> dict[str, Any]:
    response = client.post(
        "/v1/accounts",
        json={
            "name": "Tagesgeld",
            "account_type": "savings",
            "opening_balance_minor": opening_balance_minor,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_dashboard_and_reports_on_household_without_projects(
    household_headers: dict[str, str],
) -> None:
    summary = client.get("/v1/projects/dashboard-summary", headers=household_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json() == {
        "active_project_count": 0,
        "next_payment": None,
        "total_secured_gap_minor": 0,
        "top_warning": None,
    }

    reports = client.get("/v1/projects/reports-overview", headers=household_headers)
    assert reports.status_code == 200, reports.text
    assert reports.json() == []


def test_dashboard_summary_excludes_closed_projects_and_does_not_net_gaps(
    household_headers: dict[str, str],
) -> None:
    # Projekt A: keine Finanzierung -> volle gesicherte Luecke, spaete Zahlung.
    project_a = _create_project(household_headers, name="Rohbau", project_type="home_build")
    scenario_a = _baseline_scenario(household_headers, project_a["id"])
    section_a = _create_section(household_headers, project_a["id"], scenario_a["id"])
    item_a = _create_cash_item(
        household_headers, project_a["id"], scenario_a["id"], section_a["id"],
        amount_minor=200_000_00, name="Rohbauleistung",
    )
    _schedule(household_headers, project_a["id"], item_a, "2027-06-01", 200_000_00)
    _select_scenario(household_headers, project_a["id"], scenario_a["id"])

    # Projekt B: ueberdeckt (Finanzierung > Forecast) -> NEGATIVE gesicherte
    # Luecke, die die Luecke von A nicht ausgleichen darf; faellige Zahlung
    # liegt VOR der von A.
    project_b = _create_project(household_headers, name="Fenster", project_type="renovation")
    scenario_b = _baseline_scenario(household_headers, project_b["id"])
    section_b = _create_section(household_headers, project_b["id"], scenario_b["id"])
    item_b = _create_cash_item(
        household_headers, project_b["id"], scenario_b["id"], section_b["id"],
        amount_minor=30_000_00, name="Fenstereinbau",
    )
    account_b = _create_account(household_headers, opening_balance_minor=50_000_00)
    financing_b = client.post(
        f"/v1/projects/{project_b['id']}/scenarios/{scenario_b['id']}/financing-sources",
        json={
            "name": "Erspartes",
            "source_type": "cash_equity",
            "status": "received",
            "planned_amount_minor": 50_000_00,
            "account_id": account_b["id"],
        },
        headers=household_headers,
    )
    assert financing_b.status_code == 201, financing_b.text
    _schedule(household_headers, project_b["id"], item_b, "2027-02-01", 30_000_00)
    _select_scenario(household_headers, project_b["id"], scenario_b["id"])

    # Projekt C: sofort storniert (idea -> cancelled) - traegt trotz frueherer,
    # groesserer Zahlung und fehlender Finanzierung NICHTS zur Uebersicht bei.
    project_c = _create_project(household_headers, name="Verworfen", project_type="other")
    scenario_c = _baseline_scenario(household_headers, project_c["id"])
    section_c = _create_section(household_headers, project_c["id"], scenario_c["id"])
    item_c = _create_cash_item(
        household_headers, project_c["id"], scenario_c["id"], section_c["id"],
        amount_minor=1_000_000_00, name="Verworfene Position",
    )
    _schedule(household_headers, project_c["id"], item_c, "2026-09-01", 900_000_00)
    cancel = client.post(
        f"/v1/projects/{project_c['id']}/transition",
        json={"to_status": "cancelled"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert cancel.status_code == 200, cancel.text

    summary = client.get("/v1/projects/dashboard-summary", headers=household_headers)
    assert summary.status_code == 200, summary.text
    body = summary.json()

    assert body["active_project_count"] == 2

    # Naechste Zahlung: B (02/2027) liegt vor A (06/2027); C ist storniert und
    # damit trotz der frueheren, groesseren Zahlung nicht "naechste Zahlung".
    assert body["next_payment"] == {
        "project_id": project_b["id"],
        "project_name": "Fenster",
        "cost_item_name": "Fenstereinbau",
        "due_on": "2027-02-01",
        "amount_minor": 30_000_00,
    }

    # Nur A traegt eine positive Luecke bei (200.000 EUR); B's negative Luecke
    # (-20.000 EUR) darf das NICHT auf 180.000 EUR verrechnen.
    assert body["total_secured_gap_minor"] == 200_000_00

    # A hat keine Finanzierung -> "secured_funding_gap" ist die schwerwiegendste
    # Warnung im Haushalt (haerter als z. B. cost_schedule_incomplete).
    assert body["top_warning"] == {
        "project_id": project_a["id"],
        "project_name": "Rohbau",
        "code": "secured_funding_gap",
    }

    # Ein anderer Haushalt sieht nichts davon.
    other_token = _signup(f"projdash-other-{uuid.uuid4().hex}@finanzplaner.test", "other-horse-8")
    other_household = client.post(
        "/v1/households", json={"name": "Fremdhaushalt"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}
    other_summary = client.get("/v1/projects/dashboard-summary", headers=other_headers)
    assert other_summary.status_code == 200, other_summary.text
    assert other_summary.json()["active_project_count"] == 0


def test_reports_overview_forecast_actual_variance_and_financing_cost(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers, name="Fuhrpark", project_type="vehicle")
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(
        household_headers, project["id"], scenario["id"], section["id"],
        amount_minor=50_000_00, name="Fahrzeug",
    )
    _schedule(household_headers, project["id"], item, "2027-03-01", 50_000_00)

    actual = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "manual", "amount_minor": 20_000_00, "occurred_on": "2026-12-01"},
        headers=household_headers,
    )
    assert actual.status_code == 201, actual.text

    source = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Autokredit",
            "source_type": "commercial_loan",
            "planned_amount_minor": 40_000_00,
        },
        headers=household_headers,
    )
    assert source.status_code == 201, source.text

    plan = client.post(
        "/v1/debt-plans",
        json={
            "project_financing_source_id": source.json()["id"],
            "name": "Autokredit",
            "principal_limit_minor": 40_000_00,
            "opening_balance_minor": 40_000_00,
            "starts_on": "2027-03-01",
            "amortization_mode": "fixed_payment",
            "fixed_payment_minor": 1_000_00,
        },
        headers=household_headers,
    )
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]

    rate_periods = client.put(
        f"/v1/debt-plans/{plan_id}/rate-periods",
        json={"periods": [{"starts_after_month": 0, "nominal_rate_bps_annual": 400}]},
        headers=household_headers,
    )
    assert rate_periods.status_code == 200, rate_periods.text

    expected_schedule = client.get(
        f"/v1/debt-plans/{plan_id}/schedule", headers=household_headers
    )
    assert expected_schedule.status_code == 200, expected_schedule.text
    expected_body = expected_schedule.json()
    expected_financing_cost = (
        expected_body["total_interest_minor"]
        + expected_body["total_commitment_interest_minor"]
        + expected_body["total_fees_minor"]
    )
    assert expected_financing_cost > 0

    # Projekt ohne ausgewaehltes Szenario: erscheint NICHT im Bericht.
    unselected_project = _create_project(
        household_headers, name="Unentschieden", project_type="other"
    )

    _select_scenario(household_headers, project["id"], scenario["id"])

    reports = client.get("/v1/projects/reports-overview", headers=household_headers)
    assert reports.status_code == 200, reports.text
    rows = reports.json()

    assert all(row["project_id"] != unselected_project["id"] for row in rows)

    row = next(r for r in rows if r["project_id"] == project["id"])
    assert row["project_type"] == "vehicle"
    assert row["scenario_id"] == scenario["id"]
    assert row["forecast_cost_minor"] == 50_000_00
    assert row["actual_cost_minor"] == 20_000_00
    assert row["variance_minor"] == 30_000_00
    assert row["financing_cost_minor"] == expected_financing_cost
