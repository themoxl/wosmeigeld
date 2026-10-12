"""Einnahmen nach Person und Art (Liquiditaetsplan, Stufe 6).

Gegen das lokale Supabase, kein Mocking (Muster: `test_reserves_api.py`).
Geprueft wird vor allem die Normierung: jeder Betrag laeuft je Vertrag durch
`contract_service.monthly_minor` und wird erst danach summiert. Wuerde
stattdessen erst summiert und dann normiert, wichen diese Zahlen von Budget,
Berichten und Vorschau um Rundungscent ab - und dieselbe Groesse haette in der
App zwei Werte.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import contract_service
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

TODAY = date.today()


def _headers(name: str) -> dict[str, str]:
    token = _signup(f"income-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-6")
    household = client.post(
        "/v1/households", json={"name": name}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def household_headers() -> dict[str, str]:
    return _headers("Einnahmen")


@pytest.fixture()
def other_headers() -> dict[str, str]:
    return _headers("Fremder Haushalt")


def _create_person(headers: dict[str, str], name: str) -> str:
    response = client.post("/v1/people", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _create_contract(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Gehalt",
        "amount_minor": 300_000,
        "frequency": "monthly",
        "next_due_date": TODAY.replace(day=1).isoformat(),
        "direction": "income",
        "contract_type": "income",
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _summary(headers: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get("/v1/contracts/income-summary", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_income_is_grouped_by_person_and_kind(household_headers: dict[str, str]) -> None:
    person_a_id = _create_person(household_headers, "Person A")
    person_b_id = _create_person(household_headers, "Person B")

    _create_contract(
        household_headers,
        name="Gehalt Person A",
        amount_minor=300_000,
        income_kind="salary",
        person_id=person_a_id,
    )
    _create_contract(
        household_headers,
        name="Gehalt Person B",
        amount_minor=145_000,
        income_kind="salary",
        person_id=person_b_id,
    )
    # Jaehrliche Einnahme: hier trennt sich die Normierung von der Summe.
    _create_contract(
        household_headers,
        name="Miete Ferienwohnung",
        amount_minor=600_000,
        frequency="yearly",
        income_kind="rental",
        person_id=person_b_id,
    )
    # Ohne Art und ohne Person - der Bestand vor dieser Stufe.
    _create_contract(household_headers, name="Nebenjob", amount_minor=40_000)
    # Eine Ausgabe darf in keiner Einnahmengruppe auftauchen.
    _create_contract(
        household_headers,
        name="Miete Wohnung",
        amount_minor=100_000,
        direction="expense",
        contract_type="rent",
    )

    payload = _summary(household_headers)

    rental_monthly = contract_service.monthly_minor(600_000, "yearly")
    assert rental_monthly == 50_000
    assert payload["monthly_income_minor"] == 300_000 + 145_000 + rental_monthly + 40_000
    assert payload["monthly_fixed_minor"] == 100_000

    by_person = {bucket["person_name"]: bucket for bucket in payload["by_person"]}
    assert by_person["Person A"]["monthly_minor"] == 300_000
    assert by_person["Person A"]["contract_count"] == 1
    assert by_person["Person B"]["monthly_minor"] == 145_000 + rental_monthly
    assert by_person["Person B"]["contract_count"] == 2
    assert by_person["Person B"]["person_is_active"] is True

    # Die Restgruppe steht immer zuletzt: sie ist keine Person.
    assert payload["by_person"][-1]["person_id"] is None
    assert payload["by_person"][-1]["person_name"] is None
    assert payload["by_person"][-1]["monthly_minor"] == 40_000

    person_b_kinds = {
        item["income_kind"]: item["monthly_minor"] for item in by_person["Person B"]["by_kind"]
    }
    assert person_b_kinds == {"salary": 145_000, "rental": rental_monthly}

    by_kind = {item["income_kind"]: item["monthly_minor"] for item in payload["by_kind"]}
    assert by_kind == {"salary": 445_000, "rental": rental_monthly, None: 40_000}
    # "Noch keine Art" zuletzt - eine offene Aufgabe, keine Kategorie.
    assert payload["by_kind"][-1]["income_kind"] is None


def test_normalisation_runs_per_contract_before_summing(
    household_headers: dict[str, str],
) -> None:
    """Drei Quartalseinnahmen zu je 1.000,01 EUR.

    Je Vertrag normiert ergibt das 3 x 33.334 Cent; erst summiert und dann
    normiert waeren es 100.000 Cent. Der Unterschied ist genau der Rundungscent,
    um den Vorschau und Budget sonst auseinanderlaufen.
    """
    for index in range(3):
        _create_contract(
            household_headers,
            name=f"Quartalseinnahme {index}",
            amount_minor=100_001,
            frequency="quarterly",
            income_kind="capital",
        )

    per_contract = contract_service.monthly_minor(100_001, "quarterly")
    assert per_contract == 33_334
    assert _summary(household_headers)["monthly_income_minor"] == 3 * per_contract


def test_trend_shows_an_ending_income_against_running_fixed_costs(
    household_headers: dict[str, str],
) -> None:
    """Der Fall, fuer den die Entwicklung ueberhaupt da ist: eine befristete
    Einnahme faellt weg, die Fixkosten laufen weiter."""
    ends_on = contract_service.add_months(TODAY.replace(day=1), 3, day=1) - timedelta(days=1)
    _create_contract(
        household_headers,
        name="Elterngeld",
        amount_minor=180_000,
        income_kind="benefit",
        ends_on=ends_on.isoformat(),
    )
    _create_contract(
        household_headers,
        name="Miete",
        amount_minor=100_000,
        direction="expense",
        contract_type="rent",
    )

    payload = _summary(household_headers)
    trend = payload["trend"]
    assert len(trend) == 24
    assert trend[0]["period_start"] == TODAY.replace(day=1).isoformat()

    assert trend[0]["income_minor"] == 180_000
    assert trend[0]["fixed_minor"] == 100_000
    assert trend[0]["net_minor"] == 80_000
    # Im vierten Monat ist das Elterngeld ausgelaufen, die Miete nicht.
    assert trend[3]["income_minor"] == 0
    assert trend[3]["fixed_minor"] == 100_000
    assert trend[3]["net_minor"] == -100_000


def test_months_parameter_is_bounded(household_headers: dict[str, str]) -> None:
    assert len(_summary(household_headers, months=6)["trend"]) == 6
    assert (
        client.get(
            "/v1/contracts/income-summary", params={"months": 61}, headers=household_headers
        ).status_code
        == 422
    )


def test_a_deleted_person_keeps_her_income_visible(household_headers: dict[str, str]) -> None:
    person_b_id = _create_person(household_headers, "Person B")
    _create_contract(
        household_headers, name="Gehalt Person B", income_kind="salary", person_id=person_b_id
    )
    assert client.delete(f"/v1/people/{person_b_id}", headers=household_headers).status_code == 204

    bucket = _summary(household_headers)["by_person"][0]
    # Nicht "Ohne Person": der Vertrag gehoert weiterhin Person B, sie ist nur
    # geloescht - und genau das soll die Anzeige unterscheiden koennen.
    assert bucket["person_name"] == "Person B"
    assert bucket["person_is_active"] is False


def test_income_summary_is_isolated_per_household(
    household_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    other_person = _create_person(other_headers, "Fremde Person")
    _create_contract(
        other_headers,
        name="Fremdes Gehalt",
        amount_minor=999_000,
        income_kind="salary",
        person_id=other_person,
    )

    own = _summary(household_headers)
    assert own["monthly_income_minor"] == 0
    assert own["by_person"] == []
    assert own["by_kind"] == []
    assert all(point["income_minor"] == 0 for point in own["trend"])

    assert _summary(other_headers)["monthly_income_minor"] == 999_000
