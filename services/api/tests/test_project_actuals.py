"""Ist-Kosten-Zuordnung: manuelle/transaktionsbasierte Zuordnung, DB-
Ueberbuchungsschutz, atomare Mehrfachallokation und archivierte-Link-Warnung
(Plan 4.6/7.2, P4-Abnahme)."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"actuals-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-8")
    household = client.post(
        "/v1/households", json={"name": "Ist-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_project(headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/v1/projects",
        json={"name": "Ist-Projekt", "project_type": "renovation"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _baseline_scenario(headers: dict[str, str], project_id: str) -> dict[str, Any]:
    scenarios = client.get(f"/v1/projects/{project_id}/scenarios", headers=headers).json()
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
    name: str = "Position",
) -> dict[str, Any]:
    response = client.post(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-items",
        json={
            "name": name,
            "section_id": section_id,
            "calculation_mode": "fixed",
            "estimate_manual_minor": 20_000_00,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _create_account(headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/v1/accounts", json={"name": "Girokonto", "account_type": "checking"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _create_transaction(
    headers: dict[str, str], account_id: str, amount_minor: int, payee_label: str = "Baumarkt"
) -> dict[str, Any]:
    response = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-03-01",
            "amount_minor": amount_minor,
            "payee_label": payee_label,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _get_item(
    headers: dict[str, str], project_id: str, scenario_id: str, item_id: str
) -> dict[str, Any]:
    items = client.get(
        f"/v1/projects/{project_id}/scenarios/{scenario_id}/cost-items", headers=headers
    ).json()
    return next(i for i in items if i["id"] == item_id)


def test_manual_actual_create_list_delete(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"])

    created = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "manual", "amount_minor": 1_500_00, "occurred_on": "2026-02-01",
              "note": "Bar bezahlt"},
        headers=household_headers,
    )
    assert created.status_code == 201, created.text
    actual = created.json()
    assert actual["is_archived_link"] is False
    assert actual["transaction_id"] is None

    listed = client.get(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals", headers=household_headers
    ).json()
    assert len(listed) == 1

    refreshed = _get_item(household_headers, project["id"], scenario["id"], item["id"])
    assert refreshed["actual_amount_minor"] == 1_500_00

    deleted = client.delete(
        f"/v1/projects/{project['id']}/cost-actuals/{actual['id']}", headers=household_headers
    )
    assert deleted.status_code == 204

    refreshed_after = _get_item(household_headers, project["id"], scenario["id"], item["id"])
    assert refreshed_after["actual_amount_minor"] == 0


def test_manual_actual_requires_matching_fields(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"])

    # source=manual verlangt occurred_on, verbietet transaction_id.
    response = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "manual", "amount_minor": 100_00},
        headers=household_headers,
    )
    assert response.status_code == 422


def test_transaction_actual_split_across_two_items_within_budget(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item_a = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "A")
    item_b = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "B")
    account = _create_account(household_headers)
    txn = _create_transaction(household_headers, account["id"], -10_000_00)

    first = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item_a['id']}/actuals",
        json={"source": "transaction", "amount_minor": 6_000_00, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item_b['id']}/actuals",
        json={"source": "transaction", "amount_minor": 4_000_00, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert second.status_code == 201, second.text

    refreshed_a = _get_item(household_headers, project["id"], scenario["id"], item_a["id"])
    refreshed_b = _get_item(household_headers, project["id"], scenario["id"], item_b["id"])
    assert refreshed_a["actual_amount_minor"] == 6_000_00
    assert refreshed_b["actual_amount_minor"] == 4_000_00


def test_transaction_actual_overbooking_is_rejected(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item_a = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "A")
    item_b = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "B")
    account = _create_account(household_headers)
    txn = _create_transaction(household_headers, account["id"], -10_000_00)

    first = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item_a['id']}/actuals",
        json={"source": "transaction", "amount_minor": 6_000_00, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert first.status_code == 201, first.text

    # 6.000 + 4.000,01 > 10.000 -> muss der DB-Trigger verhindern, nicht nur die API.
    over = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item_b['id']}/actuals",
        json={"source": "transaction", "amount_minor": 4_000_01, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert over.status_code == 422, over.text
    assert over.json()["detail"] == "actuals_overbooking"

    refreshed_b = _get_item(household_headers, project["id"], scenario["id"], item_b["id"])
    assert refreshed_b["actual_amount_minor"] == 0


def test_bulk_assign_is_atomic_on_overbooking(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item_a = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "A")
    item_b = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "B")
    account = _create_account(household_headers)
    txn = _create_transaction(household_headers, account["id"], -1_000_00)

    response = client.post(
        f"/v1/projects/{project['id']}/actuals/bulk-assign",
        json={
            "items": [
                {"cost_item_id": item_a["id"], "transaction_id": txn["id"], "amount_minor": 600_00},
                # zusammen 600 + 500 = 1.100 > 1.000 -> muss die GESAMTE Anfrage
                # scheitern lassen, nicht nur diese eine Zeile (Plan: "atomare
                # Betragspruefung").
                {"cost_item_id": item_b["id"], "transaction_id": txn["id"], "amount_minor": 500_00},
            ]
        },
        headers=household_headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "actuals_overbooking"

    refreshed_a = _get_item(household_headers, project["id"], scenario["id"], item_a["id"])
    refreshed_b = _get_item(household_headers, project["id"], scenario["id"], item_b["id"])
    assert refreshed_a["actual_amount_minor"] == 0
    assert refreshed_b["actual_amount_minor"] == 0


def test_bulk_assign_succeeds_within_budget(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item_a = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "A")
    item_b = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"], "B")
    account = _create_account(household_headers)
    txn = _create_transaction(household_headers, account["id"], -1_000_00)

    response = client.post(
        f"/v1/projects/{project['id']}/actuals/bulk-assign",
        json={
            "items": [
                {"cost_item_id": item_a["id"], "transaction_id": txn["id"], "amount_minor": 600_00},
                {"cost_item_id": item_b["id"], "transaction_id": txn["id"], "amount_minor": 400_00},
            ]
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["created"]) == 2


def test_transaction_candidates_filters_archived_and_fully_allocated(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    account = _create_account(household_headers)

    open_txn = _create_transaction(household_headers, account["id"], -500_00, "Offen")
    to_archive = _create_transaction(household_headers, account["id"], -200_00, "Archiviert")
    to_fill = _create_transaction(household_headers, account["id"], -300_00, "Voll")

    archived_delete = client.delete(
        f"/v1/transactions/{to_archive['id']}", headers=household_headers
    )
    assert archived_delete.status_code == 204

    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"])
    fill = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "transaction", "amount_minor": 300_00, "transaction_id": to_fill["id"]},
        headers=household_headers,
    )
    assert fill.status_code == 201, fill.text

    candidates = client.get(
        f"/v1/projects/{project['id']}/transaction-candidates", headers=household_headers
    ).json()
    candidate_ids = {c["transaction_id"] for c in candidates}
    assert open_txn["id"] in candidate_ids
    assert to_archive["id"] not in candidate_ids
    assert to_fill["id"] not in candidate_ids

    open_candidate = next(c for c in candidates if c["transaction_id"] == open_txn["id"])
    assert open_candidate["remaining_minor"] == 500_00
    assert open_candidate["allocated_minor"] == 0


def test_archiving_transaction_excludes_from_ist_but_keeps_link(
    household_headers: dict[str, str],
) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"])
    account = _create_account(household_headers)
    txn = _create_transaction(household_headers, account["id"], -2_000_00)

    created = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "transaction", "amount_minor": 2_000_00, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert created.status_code == 201, created.text

    before_archive = _get_item(household_headers, project["id"], scenario["id"], item["id"])
    assert before_archive["actual_amount_minor"] == 2_000_00

    archived = client.delete(f"/v1/transactions/{txn['id']}", headers=household_headers)
    assert archived.status_code == 204

    after_archive = _get_item(household_headers, project["id"], scenario["id"], item["id"])
    assert after_archive["actual_amount_minor"] == 0

    listed = client.get(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals", headers=household_headers
    ).json()
    assert len(listed) == 1
    assert listed[0]["is_archived_link"] is True
    assert listed[0]["transaction_id"] == txn["id"]


def test_delete_actual_does_not_delete_transaction(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"])
    account = _create_account(household_headers)
    txn = _create_transaction(household_headers, account["id"], -1_000_00)

    created = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "transaction", "amount_minor": 1_000_00, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert created.status_code == 201, created.text

    deleted = client.delete(
        f"/v1/projects/{project['id']}/cost-actuals/{created.json()['id']}",
        headers=household_headers,
    )
    assert deleted.status_code == 204

    still_there = client.get(f"/v1/transactions/{txn['id']}", headers=household_headers)
    assert still_there.status_code == 200
    assert still_there.json()["is_archived"] is False


def test_single_actual_cannot_exceed_transaction_magnitude(
    household_headers: dict[str, str],
) -> None:
    """Adversarielle Review (P4): eine einzelne Zeile (auch eine negative
    "Erstattung") darf fuer sich genommen nie groesser sein als der
    Buchungsbetrag - die reine Summenpruefung greift nur nach oben und wuerde
    einen einzelnen betragsmaessig unbegrenzten Ausreisser sonst durchlassen,
    solange nur diese eine Zeile existiert."""
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"])
    account = _create_account(household_headers)
    txn = _create_transaction(household_headers, account["id"], -1_000_00)

    too_large_refund = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "transaction", "amount_minor": -999_999_00, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert too_large_refund.status_code == 422, too_large_refund.text
    assert too_large_refund.json()["detail"] == "actuals_overbooking"

    within_bounds = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "transaction", "amount_minor": -500_00, "transaction_id": txn["id"]},
        headers=household_headers,
    )
    assert within_bounds.status_code == 201, within_bounds.text


def test_cost_actual_is_isolated_per_household(household_headers: dict[str, str]) -> None:
    project = _create_project(household_headers)
    scenario = _baseline_scenario(household_headers, project["id"])
    section = _create_section(household_headers, project["id"], scenario["id"])
    item = _create_cash_item(household_headers, project["id"], scenario["id"], section["id"])
    created = client.post(
        f"/v1/projects/{project['id']}/cost-items/{item['id']}/actuals",
        json={"source": "manual", "amount_minor": 100_00, "occurred_on": "2026-02-01"},
        headers=household_headers,
    )
    assert created.status_code == 201, created.text

    other_token = _signup(f"actuals-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-8")
    other_household = client.post(
        "/v1/households", json={"name": "Anderer Haushalt"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}

    forbidden = client.delete(
        f"/v1/projects/{project['id']}/cost-actuals/{created.json()['id']}", headers=other_headers
    )
    assert forbidden.status_code == 404
