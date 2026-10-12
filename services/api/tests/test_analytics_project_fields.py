"""Analytics-Erweiterung um `project`/`project_cost_item` (Plan 11.6, P5).

Beide Felder sind bewusst nur filterbar, nie gruppierbar: project_cost_actuals
haengt wie transaction_splits unabhaengig an transaction_id, ein JOIN in der
gemeinsamen Split-Grain-Basis wuerde also die Buchungssumme vervielfachen,
sobald eine Buchung mehrere Splits UND mehrere Projektzuordnungen hat. Der
zentrale Beweis hier: genau dieser Fall (2 Splits x 2 Zuordnungen) liefert bei
einer ungefilterten Summenabfrage weiterhin die korrekte, nicht vervielfachte
Summe."""

import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.analytics import AnalyticsQuery, DimensionRef, MeasureRef
from app.services.analytics_service import QueryCompilationError, compile_query
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

HOUSEHOLD = uuid.uuid4()


def test_project_field_is_not_groupable() -> None:
    query = AnalyticsQuery(
        group_by=[DimensionRef(field="project")],
        measures=[MeasureRef(field="amount", aggregate="sum")],
    )
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, date(2026, 1, 1))
    assert exc.value.issues[0].code == "field_not_groupable"


def test_project_cost_item_field_is_not_groupable() -> None:
    query = AnalyticsQuery(
        group_by=[DimensionRef(field="project_cost_item")],
        measures=[MeasureRef(field="amount", aggregate="sum")],
    )
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, date(2026, 1, 1))
    assert exc.value.issues[0].code == "field_not_groupable"


@pytest.fixture()
def headers() -> dict[str, str]:
    token = _signup(f"analytics-proj-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-8")
    household = client.post(
        "/v1/households",
        json={"name": "Analytics-Projekt-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _category_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{name}' nicht gefunden")


def _run(headers: dict[str, str], query: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/analytics", json=query, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


@pytest.fixture()
def scenario_with_two_items_and_split_transaction(headers: dict[str, str]) -> dict[str, Any]:
    """Eine Buchung mit 2 Kategorie-Splits (-60/-40 EUR) UND 2 unabhaengigen
    Projektzuordnungen (dieselbe Buchung auf zwei Kostenpositionen verteilt) -
    der kritische Fall fuer eine moegliche Summenvervielfachung."""
    account = client.post(
        "/v1/accounts", json={"name": "Girokonto", "account_type": "checking"}, headers=headers
    ).json()
    project = client.post(
        "/v1/projects",
        json={"name": "Analytics-Projekt", "project_type": "renovation"},
        headers=headers,
    ).json()
    scenario = client.get(f"/v1/projects/{project['id']}/scenarios", headers=headers).json()[0]
    section = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-sections",
        json={"name": "Abschnitt", "cost_class": "direct"},
        headers=headers,
    ).json()
    item_a = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        json={
            "name": "Position A", "section_id": section["id"],
            "calculation_mode": "fixed", "estimate_manual_minor": 100_00,
        },
        headers=headers,
    ).json()
    item_b = client.post(
        f"/v1/projects/{project['id']}/scenarios/{scenario['id']}/cost-items",
        json={
            "name": "Position B", "section_id": section["id"],
            "calculation_mode": "fixed", "estimate_manual_minor": 100_00,
        },
        headers=headers,
    ).json()

    grocery = _category_id(headers, "Lebensmittel")
    restaurant = _category_id(headers, "Restaurant")
    txn = client.post(
        "/v1/transactions",
        json={
            "account_id": account["id"], "booked_date": "2026-06-10", "amount_minor": -10000,
            "payee_label": "Baumarkt",
        },
        headers=headers,
    ).json()
    split_response = client.put(
        f"/v1/transactions/{txn['id']}/splits",
        json={
            "amount_minor": -10000,
            "splits": [
                {"category_id": grocery, "amount_minor": -6000},
                {"category_id": restaurant, "amount_minor": -4000},
            ],
        },
        headers={**headers, "If-Match": f'"{txn["version"]}"'},
    )
    assert split_response.status_code == 200, split_response.text

    for item_id, amount in ((item_a["id"], 6000), (item_b["id"], 4000)):
        actual = client.post(
            f"/v1/projects/{project['id']}/cost-items/{item_id}/actuals",
            json={"source": "transaction", "amount_minor": amount, "transaction_id": txn["id"]},
            headers=headers,
        )
        assert actual.status_code == 201, actual.text

    return {"project_id": project["id"], "item_a_id": item_a["id"], "item_b_id": item_b["id"]}


def test_project_allocation_never_multiplies_the_total_sum(
    headers: dict[str, str], scenario_with_two_items_and_split_transaction: dict[str, Any]
) -> None:
    """Der zentrale Regressionstest: 2 Splits x 2 Projektzuordnungen auf
    derselben Buchung duerfen die ungefilterte Summe NICHT auf das
    Vierfache aufblaehen (Plan 11.6)."""
    result = _run(
        headers,
        {
            "grain": "aggregate",
            "group_by": [],
            "measures": [
                {"field": "amount", "aggregate": "sum"},
                {"field": "amount", "aggregate": "count"},
            ],
            "period": {"kind": "year", "year": 2026},
        },
    )
    row = result["rows"][0]
    assert row["sum_amount"] == -10000
    assert row["count_amount"] == 2  # zwei Splits, nicht vier Split-x-Zuordnung-Kombinationen


def test_filtering_by_project_excludes_unrelated_transactions(
    headers: dict[str, str], scenario_with_two_items_and_split_transaction: dict[str, Any]
) -> None:
    """Ein unabhaengiges, nicht zugeordnetes Konto/Buchung als Gegenprobe -
    ohne sie waere ein kaputter (nie greifender) Filter nicht von einem
    funktionierenden zu unterscheiden, weil die Fixture sonst nur die eine
    zugeordnete Buchung enthaelt."""
    project_id = scenario_with_two_items_and_split_transaction["project_id"]
    account = client.post(
        "/v1/accounts", json={"name": "Zweitkonto", "account_type": "checking"}, headers=headers
    ).json()
    client.post(
        "/v1/transactions",
        json={"account_id": account["id"], "booked_date": "2026-06-11", "amount_minor": -500},
        headers=headers,
    )

    result = _run(
        headers,
        {
            "grain": "rows",
            "group_by": [],
            "measures": [],
            "fields": ["amount"],
            "period": {"kind": "year", "year": 2026},
            "where": {"condition": {"field": "project", "op": "eq", "value": project_id}},
        },
    )
    assert len(result["rows"]) == 2
    assert sum(r["amount"] for r in result["rows"]) == -10000
    assert -500 not in [r["amount"] for r in result["rows"]]


def test_filtering_by_project_cost_item_returns_the_whole_transaction(
    headers: dict[str, str], scenario_with_two_items_and_split_transaction: dict[str, Any]
) -> None:
    """`project_cost_item` ist wie `project` an transaction_id verankert, nicht
    an split_id (die beiden Partitionierungen - nach Kategorie per Split, nach
    Projektposition per Zuordnung - sind unabhaengig voneinander). Filtern
    nach einer Kostenposition liefert deshalb bewusst ALLE Splits der
    Buchung, die diese Position ueberhaupt (mit-)finanziert, nicht nur den
    Split mit demselben Minor-Betrag."""
    item_a_id = scenario_with_two_items_and_split_transaction["item_a_id"]
    result = _run(
        headers,
        {
            "grain": "rows",
            "group_by": [],
            "measures": [],
            "fields": ["amount"],
            "period": {"kind": "year", "year": 2026},
            "where": {
                "condition": {"field": "project_cost_item", "op": "eq", "value": item_a_id}
            },
        },
    )
    assert sum(r["amount"] for r in result["rows"]) == -10000


def test_filtering_by_project_is_null_excludes_allocated_transaction(
    headers: dict[str, str], scenario_with_two_items_and_split_transaction: dict[str, Any]
) -> None:
    account = client.post(
        "/v1/accounts", json={"name": "Drittkonto", "account_type": "checking"}, headers=headers
    ).json()
    client.post(
        "/v1/transactions",
        json={"account_id": account["id"], "booked_date": "2026-06-11", "amount_minor": -500},
        headers=headers,
    )

    result = _run(
        headers,
        {
            "grain": "rows",
            "group_by": [],
            "measures": [],
            "fields": ["amount"],
            "period": {"kind": "year", "year": 2026},
            "where": {"condition": {"field": "project", "op": "is_null"}},
        },
    )
    amounts = [r["amount"] for r in result["rows"]]
    assert -500 in amounts
    assert -6000 not in amounts
    assert -4000 not in amounts
