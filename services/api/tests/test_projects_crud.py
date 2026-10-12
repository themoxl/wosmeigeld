"""Vorhaben-CRUD, Statusmatrix, Klon, Auswahl und Vergleich (Plan P1).

Laeuft wie alle API-Tests gegen ein laufendes lokales Supabase.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"proj-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-3")
    household = client.post(
        "/v1/households", json={"name": "Vorhaben-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_project(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Hausbau Musterstrasse", "project_type": "home_build"}
    body.update(overrides)
    response = client.post("/v1/projects", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _baseline_scenario(headers: dict[str, str], project_id: str) -> dict[str, Any]:
    scenarios = client.get(f"/v1/projects/{project_id}/scenarios", headers=headers).json()
    assert len(scenarios) == 1
    return dict(scenarios[0])


# ---------------------------------------------------------------------------
# Grundlegendes CRUD
# ---------------------------------------------------------------------------


def test_create_project_creates_baseline_scenario(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    assert project["status"] == "idea"
    assert project["version"] == 1

    scenario = _baseline_scenario(household_headers, project["id"])
    assert scenario["scenario_kind"] == "baseline"
    assert scenario["status"] == "draft"


def test_create_project_with_template_populates_cost_sections(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers, template_key="home_build_de")
    scenario = _baseline_scenario(household_headers, project["id"])

    sections = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
        headers=household_headers,
    ).json()
    assert len(sections) == 10
    assert {s["semantic_key"] for s in sections} >= {"grundstueck", "rohbau", "uebergangskosten"}

    items = client.get(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        headers=household_headers,
    ).json()
    assert len(items) == 10
    assert all(i["calculation_mode"] == "fixed" and i["estimate_minor"] == 0 for i in items)


def test_patch_requires_if_match_and_bumps_version(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)

    without_header = client.patch(
        f"/v1/projects/{project['id']}", json={"note": "x"}, headers=household_headers
    )
    assert without_header.status_code == 428

    stale = client.patch(
        f"/v1/projects/{project['id']}",
        json={"note": "x"},
        headers={**household_headers, "If-Match": '"7"'},
    )
    assert stale.status_code == 409
    assert stale.headers["ETag"] == '"1"'

    ok = client.patch(
        f"/v1/projects/{project['id']}",
        json={"note": "Bemerkung"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    assert ok.headers["ETag"] == '"2"'


def test_unknown_project_is_404(household_headers: dict[str, str]) -> None:
    missing = uuid.uuid4()
    assert client.get(f"/v1/projects/{missing}", headers=household_headers).status_code == 404
    assert client.delete(f"/v1/projects/{missing}", headers=household_headers).status_code == 404


def test_write_on_foreign_project_is_404(household_headers: dict[str, str]) -> None:
    foreign_token = _signup(
        f"proj-foreign-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-4"
    )
    foreign_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(foreign_token)
    ).json()
    foreign_headers = {**_auth_headers(foreign_token), "X-Household-Id": foreign_household["id"]}
    foreign_project = _create_project(foreign_headers)

    patch = client.patch(
        f"/v1/projects/{foreign_project['id']}",
        json={"note": "Fremdzugriff"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert patch.status_code == 404
    foreign_delete = client.delete(
        f"/v1/projects/{foreign_project['id']}", headers=household_headers
    )
    assert foreign_delete.status_code == 404


def test_viewer_may_read_but_not_write_projects(household_headers: dict[str, str]) -> None:
    household_id = household_headers["X-Household-Id"]
    viewer_headers = _invite_member(
        client, household_headers, household_id, "viewer", "proj-viewer"
    )
    project = _create_project(household_headers)

    assert client.get("/v1/projects", headers=viewer_headers).status_code == 200
    create = client.post(
        "/v1/projects", json={"name": "X", "project_type": "other"}, headers=viewer_headers
    )
    assert create.status_code == 403
    assert create.json()["detail"] == "household_role_readonly"

    patch = client.patch(
        f"/v1/projects/{project['id']}",
        json={"note": "x"},
        headers={**viewer_headers, "If-Match": '"1"'},
    )
    assert patch.status_code == 403


# ---------------------------------------------------------------------------
# Statusmatrix
# ---------------------------------------------------------------------------


def test_project_status_transitions_follow_matrix(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)

    invalid = client.post(
        f"/v1/projects/{project['id']}/transition",
        json={"to_status": "completed"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert invalid.status_code == 422

    to_planning = client.post(
        f"/v1/projects/{project['id']}/transition",
        json={"to_status": "planning"},
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert to_planning.status_code == 200
    assert to_planning.json()["status"] == "planning"
    assert to_planning.json()["version"] == 2


def test_hard_delete_allowed_only_for_idea_or_planning_without_actuals(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    impact = client.get(
        f"/v1/projects/{project['id']}/delete-impact", headers=household_headers
    ).json()
    assert impact["hard_delete_allowed"] is True

    delete = client.delete(f"/v1/projects/{project['id']}", headers=household_headers)
    assert delete.status_code == 204
    assert client.get(f"/v1/projects/{project['id']}", headers=household_headers).status_code == 404


# ---------------------------------------------------------------------------
# Kostenplan: Formeln, Vorrang, Zyklus
# ---------------------------------------------------------------------------


def _create_section(
    headers: dict[str, str], project_id: str, scenario_id: str, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Abschnitt", "cost_class": "direct"}
    body.update(overrides)
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-sections",
        json=body,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _create_item(
    headers: dict[str, str], project_id: str, scenario_id: str, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Position", "calculation_mode": "fixed", "estimate_manual_minor": 1000
    }
    body.update(overrides)
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-items", json=body, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_forecast_precedence_and_calculation_endpoint(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_item(
        household_headers, project["id"], scenario["id"], section_id=section["id"],
        estimate_manual_minor=100_00, quoted_amount_minor=110_00, committed_amount_minor=105_00,
    )
    assert item["forecast_minor"] == 105_00

    calc = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/calculate",
        headers=household_headers,
    ).json()
    assert calc["forecast_cash_cost_minor"] == 105_00
    assert calc["estimated_cost_minor"] == 100_00
    assert "cost_schedule_incomplete" in calc["warnings"]


def test_percentage_cycle_is_rejected_with_422(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item_a = _create_item(
        household_headers, project["id"], scenario["id"], section_id=section["id"]
    )
    item_b = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        json={
            "section_id": section["id"], "name": "B", "calculation_mode": "percentage",
            "percentage_rate_bps": 1000, "percentage_basis": "item",
            "percentage_basis_item_id": item_a["id"],
        },
        headers=household_headers,
    ).json()

    cyclic = client.patch(
        f"/v1/projects/{project['id']}/cost-items/{item_a['id']}",
        json={
            "calculation_mode": "percentage", "estimate_manual_minor": None,
            "percentage_rate_bps": 1000, "percentage_basis": "item",
            "percentage_basis_item_id": item_b["id"],
        },
        headers={**household_headers, "If-Match": '"1"'},
    )
    assert cyclic.status_code == 422
    assert cyclic.json()["detail"] == "cost_formula_cycle"


# ---------------------------------------------------------------------------
# Szenario: Klon, Auswahl, Vergleich (Kernabnahme P1)
# ---------------------------------------------------------------------------


def test_home_build_golden_path_clone_compare_select(household_headers: dict[str, str]) -> None:
    """Kernabnahme aus Plan 16 (P1): Hausbau aus Vorlage anlegen, in Basis und
    Kosten-Stress planen, centgenau vergleichen und auswaehlen; unvollstaendige
    Zahlungsplaene blockieren die Auswahl."""
    project = _create_project(household_headers, template_key="home_build_de")
    baseline = _baseline_scenario(household_headers, project["id"])

    items = client.get(
        f"/v1/projects/{project['id']}/scenarios/{baseline['id']}/cost-items",
        headers=household_headers,
    ).json()
    grundstueck_item = next(i for i in items if i["semantic_key"] == "grundstueck_kaufpreis")

    updated = client.patch(
        f"/v1/projects/{project['id']}/cost-items/{grundstueck_item['id']}",
        json={"estimate_manual_minor": 100_000_00},
        headers={**household_headers, "If-Match": f'"{grundstueck_item["version"]}"'},
    )
    assert updated.status_code == 200
    assert updated.json()["estimate_minor"] == 100_000_00

    # Auswahl mit unvollstaendigem Zahlungsplan wird blockiert.
    blocked = client.post(
        f"/v1/projects/{project['id']}/scenarios/{baseline['id']}/select", headers=household_headers
    )
    assert blocked.status_code == 422
    assert blocked.json()["detail"] == "cost_schedule_incomplete"

    # Vollstaendiger Zahlungsplan fuer alle zehn Vorlagen-Positionen.
    all_items = client.get(
        f"/v1/projects/{project['id']}/scenarios/{baseline['id']}/cost-items",
        headers=household_headers,
    ).json()
    for cost_item in all_items:
        forecast = cost_item["forecast_minor"]
        entries = [{"due_on": "2027-01-01", "amount_minor": forecast}] if forecast > 0 else []
        schedule = client.put(
            f"/v1/projects/{project['id']}/cost-items/{cost_item['id']}/schedule",
            json={"entries": entries},
            headers=household_headers,
        )
        assert schedule.status_code == 200, schedule.text

    selected = client.post(
        f"/v1/projects/{project['id']}/scenarios/{baseline['id']}/select", headers=household_headers
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["status"] == "selected"

    # Stress-Klon: +10% auf pufferfaehige Kosten.
    stress = client.post(
        f"/v1/projects/{project['id']}/scenarios/{baseline['id']}/clone",
        json={"name": "Kosten-Stress", "scenario_kind": "stress", "stress_cost_increase_bps": 1000},
        headers=household_headers,
    )
    assert stress.status_code == 201, stress.text
    stress_scenario = stress.json()

    comparison = client.get(
        f"/v1/projects/{project['id']}/comparison",
        params=[("scenario_id", baseline["id"]), ("scenario_id", stress_scenario["id"])],
        headers=household_headers,
    ).json()
    rows_by_id = {row["scenario_id"]: row for row in comparison["rows"]}
    baseline_cost = rows_by_id[baseline["id"]]["project_cash_cost_minor"]
    stress_cost = rows_by_id[stress_scenario["id"]]["project_cash_cost_minor"]
    # Nur der aufgestockte Grundstueckspreis: 100.000,00 EUR * 10 % = 10.000,00 EUR mehr.
    assert stress_cost - baseline_cost == 10_000_00


# ---------------------------------------------------------------------------
# Wunschliste -> Vorhaben
# ---------------------------------------------------------------------------


def test_convert_wishlist_item_to_project_is_idempotent(household_headers: dict[str, str]) -> None:
    wishlist_item = client.post(
        "/v1/wishlist",
        json={"name": "Neues Fahrrad", "price_minor": 250_000},
        headers=household_headers,
    ).json()

    first = client.post(
        f"/v1/wishlist/{wishlist_item['id']}/convert-to-project", headers=household_headers
    )
    assert first.status_code == 200, first.text
    project_id = first.json()["project_id"]
    assert first.json()["already_existed"] is False

    project = client.get(f"/v1/projects/{project_id}", headers=household_headers).json()
    assert project["name"] == "Neues Fahrrad"
    scenario = _baseline_scenario(household_headers, project_id)
    items = client.get(
        f"/v1/projects/{project_id}/scenarios/{scenario['id']}/cost-items",
        headers=household_headers,
    ).json()
    assert len(items) == 1
    assert items[0]["estimate_minor"] == 250_000

    second = client.post(
        f"/v1/wishlist/{wishlist_item['id']}/convert-to-project", headers=household_headers
    )
    assert second.status_code == 200
    assert second.json()["project_id"] == project_id
    assert second.json()["already_existed"] is True
