"""Projekt- und Doppelzaehlungsreferenzen (Plan 15.2, P2-Teilmenge).

Deckt die Finanzierungs-/Haushalts-/Zielwirkungs-Faelle ab: Kostenformel-
Referenzen (Forecast-Vorrang, Storno, Puffer) sind bereits in
`test_project_cost_calculation.py` (reiner Rechenkern) und
`test_projects_crud.py` (API) abgedeckt; Istkosten-Faelle (Erstattung,
Teilzuordnung, archivierte Buchung) folgen mit der P4-Zuordnungs-API.
"""

import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"projref-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-5")
    household = client.post(
        "/v1/households", json={"name": "Referenz-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_project(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Referenzprojekt", "project_type": "home_build"}
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
    headers: dict[str, str], project_id: str, scenario_id: str, section_id: str, amount_minor: int
) -> dict[str, Any]:
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-items",
        json={
            "name": "Position",
            "section_id": section_id,
            "calculation_mode": "fixed",
            "estimate_manual_minor": amount_minor,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _schedule_full_amount(
    headers: dict[str, str], project_id: str, item: dict[str, Any], due_on: str = "2027-01-01"
) -> None:
    response = client.put(
        f"/v1/projects/{project_id}/cost-items/{item['id']}/schedule",
        json={"entries": [{"due_on": due_on, "amount_minor": item["forecast_minor"]}]},
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


def _financial_summary(
    headers: dict[str, str], project_id: str, scenario_id: str
) -> dict[str, Any]:
    response = client.get(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/financial-summary", headers=headers
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Konto + Sparziel: niemals Kontostand + Zielstand + Quelle summieren
# ---------------------------------------------------------------------------


def test_account_and_savings_goal_are_not_double_counted(
    household_headers: dict[str, str],
) -> None:
    account = _create_account(household_headers, opening_balance_minor=50_000_00)
    goal = client.post(
        "/v1/goals",
        json={
            "name": "Baukasse",
            "target_amount_minor": 30_000_00,
            "starting_amount_minor": 30_000_00,
            "account_id": account["id"],
        },
        headers=household_headers,
    ).json()

    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    source = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Eigenkapital aus Baukasse",
            "source_type": "cash_equity",
            "status": "approved",
            "planned_amount_minor": 20_000_00,
            "account_id": account["id"],
            "savings_goal_id": goal["id"],
        },
        headers=household_headers,
    )
    assert source.status_code == 201, source.text

    summary = _financial_summary(household_headers, project["id"], scenario["id"])
    counted = summary["funding_gap"]["planned_cash_funding_minor"]
    assert counted == 20_000_00
    assert counted not in (50_000_00, 30_000_00, 80_000_00)


# ---------------------------------------------------------------------------
# Zwei Projekte reservieren dasselbe Konto ueber den freien Betrag hinaus
# ---------------------------------------------------------------------------


def test_two_projects_overallocating_the_same_account_are_flagged(
    household_headers: dict[str, str],
) -> None:
    account = _create_account(household_headers, opening_balance_minor=30_000_00)

    def _setup_selected_project() -> tuple[dict[str, Any], dict[str, Any]]:
        project = _create_project(household_headers)
        scenario = _baseline_scenario(household_headers, project["id"])
        section = _create_section(household_headers, project["id"], scenario["id"])
        item = _create_cash_item(
            household_headers, project["id"], scenario["id"], section["id"], 20_000_00
        )
        _schedule_full_amount(household_headers, project["id"], item)
        financing = client.post(
            f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
            json={
                "name": "Eigenkapital",
                "source_type": "cash_equity",
                "status": "approved",
                "planned_amount_minor": 20_000_00,
                "account_id": account["id"],
            },
            headers=household_headers,
        )
        assert financing.status_code == 201, financing.text
        _select_scenario(household_headers, project["id"], scenario["id"])
        return project, scenario

    project_a, scenario_a = _setup_selected_project()
    project_b, scenario_b = _setup_selected_project()

    summary_a = _financial_summary(household_headers, project_a["id"], scenario_a["id"])
    summary_b = _financial_summary(household_headers, project_b["id"], scenario_b["id"])
    assert "equity_overallocated" in summary_a["warnings"]
    assert "equity_overallocated" in summary_b["warnings"]


# ---------------------------------------------------------------------------
# Zuschuss vs. Foerderkredit: beide decken die Luecke, nur der Kredit erzeugt Schuld
# ---------------------------------------------------------------------------


def test_grant_and_subsidized_loan_both_close_the_gap_only_loan_creates_debt(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])

    grant = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Zuschuss",
            "source_type": "public_grant",
            "status": "approved",
            "planned_amount_minor": 20_000_00,
        },
        headers=household_headers,
    ).json()
    loan_source = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Foerderkredit",
            "source_type": "subsidized_loan",
            "status": "approved",
            "planned_amount_minor": 100_000_00,
        },
        headers=household_headers,
    ).json()

    debt_plan = client.post(
        "/v1/debt-plans",
        json={
            "project_financing_source_id": loan_source["id"],
            "name": "Foerderkredit-Tilgungsplan",
            "principal_limit_minor": 100_000_00,
            "starts_on": "2027-01-01",
            "amortization_mode": "full_repayment_term",
            "target_term_months": 240,
        },
        headers=household_headers,
    )
    assert debt_plan.status_code == 201, debt_plan.text

    summary = _financial_summary(household_headers, project["id"], scenario["id"])
    assert summary["funding_gap"]["planned_cash_funding_minor"] == 120_000_00
    assert summary["funding_gap"]["secured_cash_funding_minor"] == 120_000_00

    sources = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        headers=household_headers,
    ).json()
    by_id = {s["id"]: s for s in sources}
    assert by_id[grant["id"]]["has_debt_plan"] is False
    assert by_id[loan_source["id"]]["has_debt_plan"] is True


# ---------------------------------------------------------------------------
# Tilgung: Kapitalrueckzahlung erhoeht die Cash-Projektkosten nicht nochmals
# ---------------------------------------------------------------------------


def test_loan_principal_repayment_does_not_inflate_cash_cost(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(
        household_headers, project["id"], scenario["id"], section["id"], 100_000_00
    )
    _schedule_full_amount(household_headers, project["id"], item)

    loan_source = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Vollfinanzierung",
            "source_type": "commercial_loan",
            "status": "approved",
            "planned_amount_minor": 100_000_00,
        },
        headers=household_headers,
    ).json()
    debt_plan = client.post(
        "/v1/debt-plans",
        json={
            "project_financing_source_id": loan_source["id"],
            "name": "Baudarlehen",
            "principal_limit_minor": 100_000_00,
            "starts_on": "2027-01-01",
            "amortization_mode": "full_repayment_term",
            "target_term_months": 240,
        },
        headers=household_headers,
    ).json()
    client.put(
        f"/v1/debt-plans/{debt_plan['id']}/rate-periods",
        json={"periods": [{"starts_after_month": 0, "nominal_rate_bps_annual": 350}]},
        headers=household_headers,
    )

    summary = _financial_summary(household_headers, project["id"], scenario["id"])
    # Die Restschuld verursacht ueber die Laufzeit deutlich mehr Cashabfluss
    # als 100.000 EUR (Zins), aber die Projektkosten selbst bleiben exakt bei
    # den Kostenpositionen -- Tilgung ist ein Finanzierungs-, kein Kostenposten.
    assert summary["funding_gap"]["cash_required_until_completion_minor"] == 100_000_00


# ---------------------------------------------------------------------------
# Gesicherte vs. geplante Finanzierungsluecke
# ---------------------------------------------------------------------------


def test_secured_vs_planned_funding_gap(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(
        household_headers, project["id"], scenario["id"], section["id"], 200_000_00
    )
    _schedule_full_amount(household_headers, project["id"], item)

    client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Genehmigter Zuschuss",
            "source_type": "public_grant",
            "status": "approved",
            "planned_amount_minor": 100_000_00,
        },
        headers=household_headers,
    )
    client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/financing-sources",
        json={
            "name": "Beantragter Zuschuss",
            "source_type": "public_grant",
            "status": "requested",
            "planned_amount_minor": 50_000_00,
        },
        headers=household_headers,
    )

    summary = _financial_summary(household_headers, project["id"], scenario["id"])
    gap = summary["funding_gap"]
    assert gap["secured_gap_minor"] == 100_000_00
    assert gap["planned_gap_minor"] == 50_000_00


# ---------------------------------------------------------------------------
# Schutzreserve: geplante Entnahme darf das Schutzminimum nicht verletzen
# ---------------------------------------------------------------------------


def test_goal_protection_reserve_is_flagged_when_breached(
    household_headers: dict[str, str],
) -> None:
    goal = client.post(
        "/v1/goals",
        json={
            "name": "Notgroschen",
            "target_amount_minor": 20_000_00,
            "starting_amount_minor": 20_000_00,
        },
        headers=household_headers,
    ).json()

    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    saved = client.put(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/goal-assumptions",
        json={
            "savings_goal_id": goal["id"],
            "withdrawal_minor": 10_000_00,
            "protected_minimum_minor": 15_000_00,
        },
        headers=household_headers,
    )
    assert saved.status_code == 200, saved.text

    summary = _financial_summary(household_headers, project["id"], scenario["id"])
    assert "goal_protection_breached" in summary["warnings"]
    impact = next(
        g for g in summary["goal_impacts"] if g["savings_goal_id"] == goal["id"]
    )
    assert impact["protection_breached"] is True
    assert impact["max_free_withdrawal_minor"] == 5_000_00


# ---------------------------------------------------------------------------
# Zielverzoegerung durch Entnahme und reduzierten Folgebeitrag
# ---------------------------------------------------------------------------


def test_goal_delay_from_withdrawal_and_reduced_contribution(
    household_headers: dict[str, str],
) -> None:
    goal = client.post(
        "/v1/goals",
        json={
            "name": "Altersvorsorge",
            "target_amount_minor": 50_000_00,
            "starting_amount_minor": 20_000_00,
        },
        headers=household_headers,
    ).json()

    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    as_of = date.fromisoformat(scenario["as_of_date"])
    next_month = (
        date(as_of.year + 1, 1, 1) if as_of.month == 12 else date(as_of.year, as_of.month + 1, 1)
    )

    client.put(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/goal-assumptions",
        json={
            "savings_goal_id": goal["id"],
            "withdrawal_minor": 10_000_00,
            "withdrawal_on": next_month.isoformat(),
            "baseline_monthly_contribution_minor": 500_00,
            "project_monthly_contribution_minor": 300_00,
        },
        headers=household_headers,
    )

    summary = _financial_summary(household_headers, project["id"], scenario["id"])
    impact = next(
        g for g in summary["goal_impacts"] if g["savings_goal_id"] == goal["id"]
    )
    assert impact["baseline_reach_month"] == 60
    assert impact["project_reach_month"] == 134
    assert impact["delay_months"] == 74
