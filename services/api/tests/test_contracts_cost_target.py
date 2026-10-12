"""Kalender-Cashflow, Fixkostenziel und Senkungsszenarien.

Laeuft wie alle API-Tests gegen ein laufendes lokales Supabase.

Der erste Block ist ein Regressionstest gegen den Fehler, der diese Aenderung
ausgeloest hat: die alte Prognose rechnete in rollierenden 30- beziehungsweise
90-Tage-Fenstern. Ein monatlicher Vertrag fiel darin je nach Faelligkeitstag
ein- oder zweimal, ein Quartalsfenster enthielt drei oder vier Monatsraten, und
die ausgewiesene Summe stimmte mit keiner anderen Zahl der Anwendung ueberein.
"""

import calendar
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"cft-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Fixkostenziel-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Beispielvertrag",
        "amount_minor": 1999,
        "frequency": "monthly",
        "next_due_date": date.today().isoformat(),
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _month_start(anchor: date) -> date:
    return date(anchor.year, anchor.month, 1)


def _cashflow(headers: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get("/v1/contracts/cashflow", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Cashflow je Kalenderzeitraum
# ---------------------------------------------------------------------------


def test_monthly_contract_appears_once_per_calendar_month(
    household_headers: dict[str, str],
) -> None:
    """Kern der Korrektur.

    Der Vertrag ist am Monatsersten faellig - genau die Konstellation, in der
    das alte 30-Tage-Fenster ihn zweimal enthielt, sobald der Monat 30 Tage
    hat. Im Kalenderraster steht er in jedem Monat genau einmal.
    """
    _create(
        household_headers,
        name="Monatsabo",
        amount_minor=2000,
        frequency="monthly",
        next_due_date=_month_start(date.today()).isoformat(),
        due_day=1,
    )

    payload = _cashflow(household_headers, granularity="month", periods=6)

    assert payload["granularity"] == "month"
    assert len(payload["periods"]) == 6
    for period in payload["periods"]:
        assert period["expense_minor"] == 2000, period["label"]
        assert len(period["items"]) == 1, period["label"]


def test_quarter_bucket_holds_exactly_three_monthly_dues(
    household_headers: dict[str, str],
) -> None:
    """Ein Quartal sind drei Monatsraten - nicht drei oder vier je nach Kalender.

    Das erste Quartal ist ausgenommen: es beginnt am Quartalsersten und
    enthaelt je nach heutigem Datum bereits abgebuchte Raten, aber genauso
    zuverlaessig drei. Geprueft wird deshalb das ZWEITE Quartal, das in jedem
    Fall vollstaendig in der Zukunft liegt.
    """
    _create(
        household_headers,
        name="Monatsabo",
        amount_minor=1500,
        frequency="monthly",
        next_due_date=date.today().isoformat(),
        due_day=date.today().day if date.today().day <= 28 else 28,
    )

    payload = _cashflow(household_headers, granularity="quarter", periods=3)

    assert payload["granularity"] == "quarter"
    second = payload["periods"][1]
    assert second["expense_minor"] == 4500
    assert len(second["items"]) == 3


def test_current_month_includes_already_booked_due(
    household_headers: dict[str, str],
) -> None:
    """Der laufende Monat zaehlt die Rate mit, die diesen Monat schon lief.

    `next_due_date` steht nach einem Abgleich bereits im Folgemonat. Ohne den
    Rueckwaertsschritt in `due_dates_in_window` waere der laufende Monat leer,
    und die erste Spalte der Prognose widerspraeche der Fixkostensumme direkt
    daneben. `remaining_expense_minor` weist die schon gelaufene Rate zugleich
    als nicht mehr ausstehend aus.
    """
    today = date.today()
    if today.day < 3:
        pytest.skip("Am Monatsanfang gibt es keine bereits abgebuchte Rate im Monat.")

    _create(
        household_headers,
        name="Bereits abgebucht",
        amount_minor=3000,
        frequency="monthly",
        next_due_date=date(
            today.year + (today.month == 12),
            today.month % 12 + 1,
            2,
        ).isoformat(),
        due_day=2,
    )

    payload = _cashflow(household_headers, granularity="month", periods=2)
    current = payload["periods"][0]

    assert current["expense_minor"] == 3000
    assert current["remaining_expense_minor"] == 0
    assert current["items"][0]["due_date"] == date(today.year, today.month, 2).isoformat()


def test_cumulative_runs_over_periods_not_rows(household_headers: dict[str, str]) -> None:
    """Kumuliert wird ueber die Perioden, nicht ueber Einzelfaelligkeiten."""
    _create(
        household_headers,
        name="Miete",
        amount_minor=80000,
        frequency="monthly",
        next_due_date=_month_start(date.today()).isoformat(),
        due_day=1,
    )
    _create(
        household_headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=250000,
        frequency="monthly",
        next_due_date=_month_start(date.today()).isoformat(),
        due_day=1,
    )

    periods = _cashflow(household_headers, granularity="month", periods=3)["periods"]

    assert [period["net_minor"] for period in periods] == [170000, 170000, 170000]
    assert [period["cumulative_net_minor"] for period in periods] == [170000, 340000, 510000]


def test_yearly_contract_lands_in_its_own_month(household_headers: dict[str, str]) -> None:
    """Eine Jahrespraemie faellt in genau einen Monat und faelscht die uebrigen nicht."""
    today = date.today()
    due = date(today.year, today.month, 15) + timedelta(days=70)
    _create(
        household_headers,
        name="Jahrespraemie",
        contract_type="insurance",
        amount_minor=60000,
        frequency="yearly",
        next_due_date=due.isoformat(),
        due_day=due.day,
    )

    periods = _cashflow(household_headers, granularity="month", periods=6)["periods"]
    hits = [period for period in periods if period["expense_minor"] > 0]

    assert len(hits) == 1
    assert hits[0]["expense_minor"] == 60000
    assert hits[0]["period_start"] <= due.isoformat() <= hits[0]["period_end"]


def test_ended_contract_stops_at_its_end_date(household_headers: dict[str, str]) -> None:
    today = date.today()
    ends_on = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    contract = _create(
        household_headers,
        name="Laeuft aus",
        amount_minor=1000,
        frequency="monthly",
        next_due_date=_month_start(today).isoformat(),
        due_day=1,
        ends_on=ends_on.isoformat(),
    )
    assert contract["ends_on"] == ends_on.isoformat()

    periods = _cashflow(household_headers, granularity="month", periods=4)["periods"]

    assert periods[0]["expense_minor"] == 1000
    assert all(period["expense_minor"] == 0 for period in periods[1:])


# ---------------------------------------------------------------------------
# Fixkostenziel
# ---------------------------------------------------------------------------


def test_target_defaults_to_unconfigured(household_headers: dict[str, str]) -> None:
    """Ohne gesetztes Ziel meldet die Antwort das ausdruecklich.

    Sonst saehe die Oberflaeche einen Vorgabewert von 50 % und meldete eine
    Zielverfehlung, die niemand gesetzt hat.
    """
    response = client.get("/v1/contracts/cost-target", headers=household_headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["is_configured"] is False
    assert payload["target_ratio_percent"] == 50.0
    assert payload["current_monthly_minor"] == 0


def test_target_ratio_uses_contract_income_without_bookings(
    household_headers: dict[str, str],
) -> None:
    """Bezugsgroesse wie im Budget: ohne gebuchte Einnahmen zaehlt der Einnahmevertrag."""
    _create(
        household_headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=300000,
        frequency="monthly",
    )
    _create(household_headers, name="Miete", contract_type="rent", amount_minor=120000)

    put = client.put(
        "/v1/contracts/cost-target",
        json={"target_ratio_percent": 30, "target_date": "2027-01-01"},
        headers=household_headers,
    )
    assert put.status_code == 200, put.text
    payload = put.json()

    assert payload["is_configured"] is True
    assert payload["income_basis"] == "contract"
    assert payload["income_basis_minor"] == 300000
    assert payload["current_monthly_minor"] == 120000
    assert payload["current_ratio_percent"] == 40.0
    # 30 % von 3.000 EUR = 900 EUR; heute 1.200 EUR -> 300 EUR Luecke.
    assert payload["effective_target_monthly_minor"] == 90000
    assert payload["gap_monthly_minor"] == 30000
    assert payload["gap_yearly_minor"] == 360000
    assert payload["on_track"] is False


def test_absolute_target_wins_over_ratio(household_headers: dict[str, str]) -> None:
    _create(
        household_headers,
        name="Gehalt",
        direction="income",
        contract_type="income",
        amount_minor=300000,
        frequency="monthly",
    )
    _create(household_headers, name="Miete", contract_type="rent", amount_minor=120000)

    payload = client.put(
        "/v1/contracts/cost-target",
        json={"target_ratio_percent": 30, "target_monthly_minor": 150000},
        headers=household_headers,
    ).json()

    assert payload["effective_target_monthly_minor"] == 150000
    assert payload["gap_monthly_minor"] == -30000
    assert payload["on_track"] is True


def test_target_rejects_ratio_out_of_range(household_headers: dict[str, str]) -> None:
    response = client.put(
        "/v1/contracts/cost-target",
        json={"target_ratio_percent": 0},
        headers=household_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Szenarien
# ---------------------------------------------------------------------------


def _scenario_household(headers: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {
        "income": _create(
            headers,
            name="Gehalt",
            direction="income",
            contract_type="income",
            amount_minor=300000,
            frequency="monthly",
        ),
        "gym": _create(headers, name="Fitnessstudio", amount_minor=4000),
        "insurance": _create(
            headers,
            name="Hausrat",
            contract_type="insurance",
            amount_minor=12000,
            frequency="yearly",
        ),
    }


def test_scenario_computes_saving_and_leaves_contracts_untouched(
    household_headers: dict[str, str],
) -> None:
    """Ein Szenario ist eine Absicht, kein Vollzug.

    Die Fixkostensumme darf sich davon nicht bewegen - sonst zeigte das Budget
    eine Ersparnis an, die es noch gar nicht gibt.
    """
    contracts = _scenario_household(household_headers)
    before = client.get("/v1/contracts/summary", headers=household_headers).json()

    response = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Sparrunde 2026",
            "is_active": True,
            "items": [
                {"action": "cancel", "contract_id": contracts["gym"]["id"]},
                {
                    "action": "reduce",
                    "contract_id": contracts["insurance"]["id"],
                    "target_amount_minor": 6000,
                },
            ],
        },
        headers=household_headers,
    )
    assert response.status_code == 201, response.text
    scenario = response.json()

    # Fitnessstudio 40 EUR/Monat + Hausrat 120 -> 60 EUR/Jahr = 5 EUR/Monat.
    assert scenario["monthly_saving_minor"] == 4500
    assert scenario["yearly_saving_minor"] == 54000
    assert scenario["is_active"] is True
    assert scenario["resulting_monthly_minor"] == before["monthly_expense_minor"] - 4500

    after = client.get("/v1/contracts/summary", headers=household_headers).json()
    assert after["monthly_expense_minor"] == before["monthly_expense_minor"]

    gym = client.get(
        f"/v1/contracts/{contracts['gym']['id']}", headers=household_headers
    ).json()
    assert gym["status"] == "active"
    assert gym["amount_minor"] == 4000


def test_active_scenario_appears_in_target(household_headers: dict[str, str]) -> None:
    contracts = _scenario_household(household_headers)
    client.put(
        "/v1/contracts/cost-target",
        json={"target_ratio_percent": 15},
        headers=household_headers,
    )

    client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Plan A",
            "is_active": True,
            "items": [{"action": "cancel", "contract_id": contracts["gym"]["id"]}],
        },
        headers=household_headers,
    )

    target = client.get("/v1/contracts/cost-target", headers=household_headers).json()
    assert target["active_scenario"] is not None
    assert target["active_scenario"]["name"] == "Plan A"
    assert target["active_scenario"]["monthly_saving_minor"] == 4000


def test_only_one_scenario_stays_active(household_headers: dict[str, str]) -> None:
    contracts = _scenario_household(household_headers)
    first = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Plan A",
            "is_active": True,
            "items": [{"action": "cancel", "contract_id": contracts["gym"]["id"]}],
        },
        headers=household_headers,
    ).json()
    second = client.post(
        "/v1/contracts/scenarios",
        json={"name": "Plan B", "is_active": True, "items": []},
        headers=household_headers,
    ).json()

    scenarios = {
        item["id"]: item
        for item in client.get("/v1/contracts/scenarios", headers=household_headers).json()
    }
    assert scenarios[first["id"]]["is_active"] is False
    assert scenarios[second["id"]]["is_active"] is True


def test_patch_replaces_the_whole_item_list(household_headers: dict[str, str]) -> None:
    contracts = _scenario_household(household_headers)
    scenario = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Plan A",
            "items": [
                {"action": "cancel", "contract_id": contracts["gym"]["id"]},
                {
                    "action": "reduce",
                    "contract_id": contracts["insurance"]["id"],
                    "target_amount_minor": 6000,
                },
            ],
        },
        headers=household_headers,
    ).json()
    assert len(scenario["items"]) == 2

    updated = client.patch(
        f"/v1/contracts/scenarios/{scenario['id']}",
        json={"items": [{"action": "cancel", "contract_id": contracts["gym"]["id"]}]},
        headers=household_headers,
    )
    assert updated.status_code == 200, updated.text
    assert len(updated.json()["items"]) == 1
    assert updated.json()["monthly_saving_minor"] == 4000


def test_scenario_rejects_income_contract(household_headers: dict[str, str]) -> None:
    contracts = _scenario_household(household_headers)
    response = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Unsinn",
            "items": [{"action": "cancel", "contract_id": contracts["income"]["id"]}],
        },
        headers=household_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "contract_scenario_contract_not_expense"


def test_scenario_rejects_reduce_that_does_not_reduce(
    household_headers: dict[str, str],
) -> None:
    contracts = _scenario_household(household_headers)
    response = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Unsinn",
            "items": [
                {
                    "action": "reduce",
                    "contract_id": contracts["gym"]["id"],
                    "target_amount_minor": 5000,
                }
            ],
        },
        headers=household_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "contract_scenario_reduce_not_lower"


def test_scenario_rejects_reduce_without_amount(household_headers: dict[str, str]) -> None:
    contracts = _scenario_household(household_headers)
    response = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Unsinn",
            "items": [{"action": "reduce", "contract_id": contracts["gym"]["id"]}],
        },
        headers=household_headers,
    )
    assert response.status_code == 422


def test_scenario_rejects_the_same_contract_twice(household_headers: dict[str, str]) -> None:
    contracts = _scenario_household(household_headers)
    response = client.post(
        "/v1/contracts/scenarios",
        json={
            "name": "Doppelt",
            "items": [
                {"action": "cancel", "contract_id": contracts["gym"]["id"]},
                {
                    "action": "reduce",
                    "contract_id": contracts["gym"]["id"],
                    "target_amount_minor": 1000,
                },
            ],
        },
        headers=household_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "contract_scenario_duplicate_contract"


def test_delete_scenario(household_headers: dict[str, str]) -> None:
    scenario = client.post(
        "/v1/contracts/scenarios",
        json={"name": "Weg damit", "items": []},
        headers=household_headers,
    ).json()

    deleted = client.delete(
        f"/v1/contracts/scenarios/{scenario['id']}", headers=household_headers
    )
    assert deleted.status_code == 204

    missing = client.get(
        f"/v1/contracts/scenarios/{scenario['id']}", headers=household_headers
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "contract_scenario_not_found"
