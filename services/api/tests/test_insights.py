"""Finanz-Empfehlungen.

Der Kern dieser Tests ist nicht "es kommt irgendetwas zurueck", sondern:
**die Zahlen stimmen und stammen aus den Buchungen.** Deshalb wird jeder
Detektor mit einem Bestand gefuettert, dessen erwartetes Ergebnis sich von
Hand nachrechnen laesst, und das Ergebnis auf den Cent geprueft.

Fuer die optionale Formulierung ist nur der Modellserver ersetzt
(tests/ki_modell_attrappe.py), der Gateway samt llm_calls-Zeile laeuft echt.
Geprueft wird vor allem: eine Formulierung mit eigenen Zahlen oder eine
abgeschnittene Antwort wird nie zum Ratschlag, und das Weglassen ist benannt.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import insights_service
from tests.ki_modell_attrappe import MODEL, ModelStub, answer, llm_calls, unavailable_target
from tests.test_household_isolation import _auth_headers, _invite_member, _signup

client = TestClient(app)


def _household_headers(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-8")
    household = client.post(
        "/v1/households",
        json={"name": "Empfehlungs-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def headers() -> dict[str, str]:
    return _household_headers("ins")


@pytest.fixture()
def account_id(headers: dict[str, str]) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return str(account["id"])


def _month_start(offset: int) -> date:
    """Erster Tag des Monats `offset` Monate VOR dem letzten vollstaendigen
    Monat. offset=0 ist damit der juengste Monat, den der Bericht auswertet."""
    today = date.today()
    last_complete = date(today.year, today.month, 1) - timedelta(days=1)
    year, month = last_complete.year, last_complete.month
    month -= offset
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _book(
    headers: dict[str, str],
    account_id: str,
    *,
    months_ago: int,
    amount_minor: int,
    category_id: str | None = None,
    payee: str | None = None,
    day: int = 10,
) -> str:
    payload: dict[str, Any] = {
        "account_id": account_id,
        "booked_date": _month_start(months_ago).replace(day=day).isoformat(),
        "amount_minor": amount_minor,
    }
    if category_id is not None:
        payload["category_id"] = category_id
    if payee is not None:
        payload["payee_label"] = payee
    response = client.post("/v1/transactions", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _category_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Kategorie '{name}' nicht gefunden")


def _report(headers: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get("/v1/insights", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _by_kind(report: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [item for item in report["insights"] if item["kind"] == kind]


def _create_contract(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Beispielvertrag",
        "amount_minor": 1000,
        "frequency": "monthly",
        # Vor dem Betrachtungszeitraum: die Anfangsstufe des Betragsverlaufs
        # gilt ab `min(heute, next_due_date)` und muss aelter sein als die
        # Erhoehung, sonst gibt es keine Vorstufe zum Vergleichen.
        "next_due_date": _month_start(6).replace(day=10).isoformat(),
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _raise_price(
    headers: dict[str, str], contract_id: str, *, amount_minor: int, valid_from: date
) -> None:
    response = client.post(
        f"/v1/contracts/{contract_id}/history",
        json={"amount_minor": amount_minor, "valid_from": valid_from.isoformat()},
        headers=headers,
    )
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Zeitfenster und Grundzahlen
# ---------------------------------------------------------------------------


def test_empty_household_reports_nothing_instead_of_failing(headers: dict[str, str]) -> None:
    report = _report(headers)
    assert report["months_covered"] == 0
    assert report["insights"] == []
    assert report["monthly_potential_minor"] == 0


def test_monthly_averages_come_from_the_bookings(
    headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(headers, "Lebensmittel")
    salary = _category_id(headers, "Gehalt & Lohn")
    for month in range(4):
        _book(headers, account_id, months_ago=month, amount_minor=300000, category_id=salary)
        _book(headers, account_id, months_ago=month, amount_minor=-100000, category_id=groceries)

    report = _report(headers, months=6)
    assert report["months_covered"] == 4
    assert report["monthly_income_minor"] == 300000
    assert report["monthly_expense_minor"] == 100000
    assert report["monthly_net_minor"] == 200000


def test_the_running_month_is_excluded_from_the_window(
    headers: dict[str, str], account_id: str
) -> None:
    """Ein halber Monat wuerde jeden Durchschnitt nach unten ziehen und einen
    Trend vortaeuschen, den es nicht gibt."""
    groceries = _category_id(headers, "Lebensmittel")
    _book(headers, account_id, months_ago=0, amount_minor=-50000, category_id=groceries)
    today = date.today()
    running = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": today.isoformat(),
            "amount_minor": -999999,
            "category_id": groceries,
        },
        headers=headers,
    )
    assert running.status_code == 201

    report = _report(headers)
    assert report["to_date"] < today.isoformat()
    assert report["monthly_expense_minor"] == 50000


# ---------------------------------------------------------------------------
# Detektoren
# ---------------------------------------------------------------------------


def test_category_trend_compares_a_category_with_its_own_baseline(
    headers: dict[str, str], account_id: str
) -> None:
    restaurant = _category_id(headers, "Restaurant")
    # Sechs Monate Grundlinie 50 EUR, die letzten drei Monate 150 EUR.
    for month in range(3, 9):
        _book(headers, account_id, months_ago=month, amount_minor=-5000, category_id=restaurant)
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-15000, category_id=restaurant)

    report = _report(headers, months=12)
    trends = _by_kind(report, "category_trend")
    assert len(trends) == 1
    trend = trends[0]
    assert trend["category_name"] == "Restaurant"
    # Ø letzte 3 Monate 150 EUR, Ø davor 50 EUR -> 100 EUR Potenzial.
    assert trend["amount_minor"] == 15000
    assert trend["monthly_potential_minor"] == 10000
    assert report["monthly_potential_minor"] == 10000
    assert trend["drilldown"]["field"] == "category"
    assert trend["drilldown"]["direction"] == "expense"


def test_a_stable_category_produces_no_trend(
    headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(headers, "Lebensmittel")
    for month in range(9):
        _book(headers, account_id, months_ago=month, amount_minor=-40000, category_id=groceries)

    assert _by_kind(_report(headers, months=12), "category_trend") == []


def test_price_increase_uses_the_median_of_earlier_payments(
    headers: dict[str, str], account_id: str
) -> None:
    streaming = _category_id(headers, "Streaming & digitale Medien")
    for month in range(1, 6):
        _book(
            headers,
            account_id,
            months_ago=month,
            amount_minor=-1000,
            category_id=streaming,
            payee="Streamdienst",
        )
    _book(
        headers,
        account_id,
        months_ago=0,
        amount_minor=-1400,
        category_id=streaming,
        payee="Streamdienst",
    )

    increases = _by_kind(_report(headers, months=12), "price_increase")
    assert len(increases) == 1
    assert increases[0]["amount_minor"] == 400
    assert increases[0]["drilldown"]["field"] == "payee"
    assert increases[0]["drilldown"]["value"] == "Streamdienst"


def test_many_bookings_per_month_are_not_read_as_a_subscription(
    headers: dict[str, str], account_id: str
) -> None:
    """Vier Einkaeufe im selben Supermarkt pro Monat sind kein Abo - eine
    "Preiserhoehung" waere dort eine Fehldeutung."""
    groceries = _category_id(headers, "Lebensmittel")
    for month in range(1, 6):
        for day in (3, 10, 17, 24):
            _book(
                headers,
                account_id,
                months_ago=month,
                amount_minor=-3000,
                category_id=groceries,
                payee="Supermarkt",
                day=day,
            )
    _book(
        headers,
        account_id,
        months_ago=0,
        amount_minor=-9000,
        category_id=groceries,
        payee="Supermarkt",
    )

    assert _by_kind(_report(headers, months=12), "price_increase") == []


def test_a_recurring_payment_is_normalised_to_a_month(
    headers: dict[str, str], account_id: str
) -> None:
    """Vierteljaehrlich gezahlt heisst nicht monatlich gespart.

    Das rohe Delta einer Quartalsrechnung ist das Dreifache dessen, was pro
    Monat frei wuerde - und genau diese Zahl stuende sonst in der Gesamtsumme.
    """
    utilities = _category_id(headers, "Strom, Gas & Heizung")
    for month in (9, 6, 3):
        _book(
            headers,
            account_id,
            months_ago=month,
            amount_minor=-9000,
            category_id=utilities,
            payee="Stadtwerke",
        )
    _book(
        headers,
        account_id,
        months_ago=0,
        amount_minor=-12000,
        category_id=utilities,
        payee="Stadtwerke",
    )

    report = _report(headers, months=12)
    increases = _by_kind(report, "price_increase")
    assert len(increases) == 1
    # Die Rechnung stieg um 30 EUR, pro Monat sind das 10 EUR.
    assert increases[0]["amount_minor"] == 3000
    assert increases[0]["monthly_potential_minor"] == 1000
    assert report["monthly_potential_minor"] == 1000


# ---------------------------------------------------------------------------
# Vertragsbezug
# ---------------------------------------------------------------------------


def test_a_contract_price_increase_is_exact_instead_of_estimated(
    headers: dict[str, str], account_id: str
) -> None:
    """Gibt es einen Vertrag, steht die Erhoehung mit Datum, altem und neuem
    Betrag im Betragsverlauf - der Median der letzten Buchungen ist dann nur
    noch die schlechtere von zwei verfuegbaren Antworten."""
    streaming = _category_id(headers, "Streaming & digitale Medien")
    for month in range(6):
        _book(
            headers,
            account_id,
            months_ago=month,
            amount_minor=-1000,
            category_id=streaming,
            payee="Streamdienst",
        )

    contract = _create_contract(
        headers,
        name="Streamdienst Abo",
        provider_label="Streamdienst",
        contract_type="subscription",
        amount_minor=1000,
    )
    _raise_price(
        headers,
        str(contract["id"]),
        amount_minor=1400,
        valid_from=_month_start(0).replace(day=10),
    )

    report = _report(headers, months=12)
    increases = _by_kind(report, "price_increase")
    assert len(increases) == 1
    increase = increases[0]
    assert increase["key"] == f"price_increase:contract:{contract['id']}"
    assert increase["amount_minor"] == 400
    assert increase["monthly_potential_minor"] == 400
    # Die Belege nennen Vorher/Nachher - das ist der sichtbare Unterschied
    # zwischen einer belegten und einer geschaetzten Aussage.
    labels = [item["label"] for item in increase["evidence"]]
    assert labels[:2] == ["Vorher", "Nachher"]
    assert [item["amount_minor"] for item in increase["evidence"][:2]] == [1000, 1400]
    assert increase["drilldown"]["contract_id"] == contract["id"]


def test_a_yearly_contract_increase_counts_per_month_not_per_year(
    headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(headers, "Lebensmittel")
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-40000, category_id=groceries)

    contract = _create_contract(
        headers,
        name="Hausratversicherung",
        contract_type="insurance",
        amount_minor=12000,
        frequency="yearly",
    )
    _raise_price(
        headers,
        str(contract["id"]),
        amount_minor=24000,
        valid_from=_month_start(0).replace(day=5),
    )

    report = _report(headers, months=12)
    increases = _by_kind(report, "price_increase")
    assert len(increases) == 1
    # 120 EUR mehr im Jahr sind 10 EUR mehr im Monat - nicht 120.
    assert increases[0]["amount_minor"] == 1000
    assert report["monthly_potential_minor"] == 1000


def test_a_contract_increase_is_not_reported_a_second_time_by_payee(
    headers: dict[str, str], account_id: str
) -> None:
    """Ohne den Ausschluss stuende dieselbe Erhoehung zweimal auf der Seite -
    und zweimal in der Gesamtsumme. Ein Empfaenger ohne Vertrag behaelt seine
    geschaetzte Meldung."""
    streaming = _category_id(headers, "Streaming & digitale Medien")
    gym = _category_id(headers, "Sport, Fitness & Vereine")
    for month in range(1, 6):
        _book(
            headers,
            account_id,
            months_ago=month,
            amount_minor=-1000,
            category_id=streaming,
            payee="Streamdienst",
        )
        _book(
            headers,
            account_id,
            months_ago=month,
            amount_minor=-3000,
            category_id=gym,
            payee="Fitnessstudio",
        )
    _book(
        headers,
        account_id,
        months_ago=0,
        amount_minor=-1400,
        category_id=streaming,
        payee="Streamdienst",
    )
    _book(
        headers,
        account_id,
        months_ago=0,
        amount_minor=-3600,
        category_id=gym,
        payee="Fitnessstudio",
    )

    contract = _create_contract(
        headers,
        name="Streamdienst Abo",
        provider_label="Streamdienst",
        contract_type="subscription",
        amount_minor=1000,
    )
    _raise_price(
        headers,
        str(contract["id"]),
        amount_minor=1400,
        valid_from=_month_start(0).replace(day=10),
    )

    report = _report(headers, months=12)
    increases = _by_kind(report, "price_increase")
    keys = {item["key"] for item in increases}
    assert keys == {f"price_increase:contract:{contract['id']}", "price_increase:Fitnessstudio"}
    # 4 EUR aus dem Vertrag, 6 EUR aus der Schaetzung - jede Erhoehung genau
    # einmal.
    assert report["monthly_potential_minor"] == 400 + 600


def test_price_increases_of_the_same_type_in_one_month_become_one_message(
    headers: dict[str, str], account_id: str
) -> None:
    """I13: fuenf Beitragsanpassungen zum Jahreswechsel sind eine Nachricht.
    Die Summe, um die es geht, stuende auf keiner der Einzelkarten."""
    groceries = _category_id(headers, "Lebensmittel")
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-40000, category_id=groceries)

    change_date = _month_start(0).replace(day=1)
    first = _create_contract(
        headers, name="Haftpflicht Nordwind", contract_type="insurance", amount_minor=1000
    )
    second = _create_contract(
        headers, name="Rechtsschutz Talblick", contract_type="insurance", amount_minor=2000
    )
    _raise_price(headers, str(first["id"]), amount_minor=1200, valid_from=change_date)
    _raise_price(headers, str(second["id"]), amount_minor=2400, valid_from=change_date)

    report = _report(headers, months=12)
    increases = _by_kind(report, "price_increase")
    assert len(increases) == 1
    bundle = increases[0]
    assert bundle["key"].startswith("price_increase:contract_type:insurance:")
    assert bundle["amount_minor"] == 600
    assert bundle["monthly_potential_minor"] == 600
    assert "Versicherungen" in bundle["title"]
    evidence = {item["label"]: item for item in bundle["evidence"]}
    assert evidence["Haftpflicht Nordwind"]["amount_minor"] == 200
    assert evidence["Rechtsschutz Talblick"]["amount_minor"] == 400
    assert evidence["Verträge"]["count"] == 2
    assert report["monthly_potential_minor"] == 600


@pytest.mark.asyncio
async def test_a_contract_without_payments_is_reported_but_promises_nothing(
    headers: dict[str, str], account_id: str
) -> None:
    """Ob das Geld schon gespart ist (Vertrag vorbei) oder die Lastschrift
    nachkommt, weiss der Detektor nicht - deshalb kein Monatspotenzial."""
    groceries = _category_id(headers, "Lebensmittel")
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-40000, category_id=groceries)

    contract = _create_contract(
        headers,
        name="Zeitschriftenabo",
        contract_type="subscription",
        amount_minor=1500,
        next_due_date=(date.today() + timedelta(days=5)).isoformat(),
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text("update contracts set missed_periods = 3 where id = :id"),
            {"id": contract["id"]},
        )
        await session.commit()

    report = _report(headers, months=12)
    unused = _by_kind(report, "contract_unused")
    assert len(unused) == 1
    assert unused[0]["key"] == f"contract_unused:{contract['id']}"
    assert unused[0]["amount_minor"] == 1500
    assert unused[0]["monthly_potential_minor"] is None
    assert unused[0]["evidence"][0]["count"] == 3
    assert unused[0]["drilldown"]["contract_id"] == contract["id"]
    assert report["monthly_potential_minor"] == 0


@pytest.mark.asyncio
async def test_a_single_missed_payment_is_not_reported(
    headers: dict[str, str], account_id: str
) -> None:
    """Dieselbe Schwelle wie die Review-Inbox: eine einzelne Luecke ist meistens
    eine verschobene Abbuchung."""
    groceries = _category_id(headers, "Lebensmittel")
    _book(headers, account_id, months_ago=0, amount_minor=-40000, category_id=groceries)

    contract = _create_contract(
        headers, name="Zeitschriftenabo", next_due_date=date.today().isoformat()
    )
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text("update contracts set missed_periods = 1 where id = :id"),
            {"id": contract["id"]},
        )
        await session.commit()

    assert _by_kind(_report(headers, months=12), "contract_unused") == []


def test_two_similar_contracts_of_the_same_type_are_flagged_as_possible_duplicates(
    headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(headers, "Lebensmittel")
    _book(headers, account_id, months_ago=0, amount_minor=-40000, category_id=groceries)

    first = _create_contract(
        headers,
        name="Hausrat",
        provider_label="Musterkasse Versicherung",
        contract_type="insurance",
        amount_minor=1200,
    )
    second = _create_contract(
        headers,
        name="Haftpflicht",
        provider_label="Musterkasse Versicherungen",
        contract_type="insurance",
        amount_minor=1300,
    )

    report = _report(headers, months=12)
    duplicates = _by_kind(report, "contract_duplicate")
    assert len(duplicates) == 1
    duplicate = duplicates[0]
    assert duplicate["severity"] == "warning"
    assert "doppelt versichert" in duplicate["title"]
    assert duplicate["amount_minor"] == 2500
    # Der Nutzer entscheidet, ob wirklich doppelt - der Detektor verspricht
    # kein Geld.
    assert duplicate["monthly_potential_minor"] is None
    assert report["monthly_potential_minor"] == 0
    assert duplicate["drilldown"]["contract_id"] == second["id"]
    assert {item["label"] for item in duplicate["evidence"]} == {"Hausrat", "Haftpflicht"}
    assert first["id"] in duplicate["key"]


def test_two_unrelated_contracts_of_the_same_type_are_not_flagged(
    headers: dict[str, str], account_id: str
) -> None:
    """Gleiche Vertragsart allein genuegt nicht - Haftpflicht und Kfz sind zwei
    Versicherungen, aber keine doppelte."""
    groceries = _category_id(headers, "Lebensmittel")
    _book(headers, account_id, months_ago=0, amount_minor=-40000, category_id=groceries)

    _create_contract(
        headers,
        name="Haftpflicht",
        provider_label="Nordwind Assekuranz",
        contract_type="insurance",
        amount_minor=1200,
    )
    _create_contract(
        headers,
        name="Kfz",
        provider_label="Talblick Gruppe",
        contract_type="insurance",
        amount_minor=1250,
    )

    assert _by_kind(_report(headers, months=12), "contract_duplicate") == []


def test_uncategorized_bookings_are_reported_first(
    headers: dict[str, str], account_id: str
) -> None:
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-2500)

    report = _report(headers)
    assert report["insights"][0]["kind"] == "uncategorized"
    assert report["insights"][0]["amount_minor"] == 7500
    assert report["insights"][0]["evidence"][0]["count"] == 3


def test_tax_deductible_sums_only_flagged_categories(
    headers: dict[str, str], account_id: str
) -> None:
    software = _category_id(headers, "Software, KI-Dienste & Lizenzen")
    groceries = _category_id(headers, "Lebensmittel")
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-2000, category_id=software)
        _book(headers, account_id, months_ago=month, amount_minor=-50000, category_id=groceries)

    tax = _by_kind(_report(headers), "tax_deductible")
    assert len(tax) == 1
    assert tax[0]["amount_minor"] == 6000


def test_wants_share_measures_against_the_50_30_20_rule(
    headers: dict[str, str], account_id: str
) -> None:
    needs = _category_id(headers, "Lebensmittel")
    wants = _category_id(headers, "Restaurant")
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-50000, category_id=needs)
        _book(headers, account_id, months_ago=month, amount_minor=-50000, category_id=wants)

    share = _by_kind(_report(headers), "wants_share")
    assert len(share) == 1
    # 3 Monate a 500 EUR Bedarf und 500 EUR Wunsch: Wunschanteil 50 %, Ziel
    # 30 % von 3000 EUR Gesamtausgaben = 900 EUR. Ueber der Marke liegen
    # 600 EUR, verteilt auf drei Monate also 200 EUR/Monat.
    assert share[0]["amount_minor"] == 20000


def test_savings_rate_warns_when_too_little_is_left(
    headers: dict[str, str], account_id: str
) -> None:
    salary = _category_id(headers, "Gehalt & Lohn")
    groceries = _category_id(headers, "Lebensmittel")
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=200000, category_id=salary)
        _book(headers, account_id, months_ago=month, amount_minor=-190000, category_id=groceries)

    rate = _by_kind(_report(headers), "savings_rate")
    assert len(rate) == 1
    assert rate[0]["severity"] == "warning"
    # 10 % von 2000 EUR waeren 200 EUR, uebrig bleiben 100 EUR -> 100 EUR
    # fehlen, verteilt auf drei Monate.
    assert rate[0]["amount_minor"] == 10000


def test_budget_overrun_uses_the_latest_planned_month(
    headers: dict[str, str], account_id: str
) -> None:
    restaurant = _category_id(headers, "Restaurant")
    month_start = _month_start(0)
    allocation = client.put(
        f"/v1/budgets/{month_start.year}/{month_start.month}/allocations",
        json={"allocations": [{"category_id": restaurant, "planned_minor": 10000}]},
        headers=headers,
    )
    assert allocation.status_code == 200, allocation.text

    _book(headers, account_id, months_ago=0, amount_minor=-16000, category_id=restaurant)

    overruns = _by_kind(_report(headers), "budget_overrun")
    assert len(overruns) == 1
    assert overruns[0]["amount_minor"] == 6000
    assert overruns[0]["severity"] == "warning"
    # Der Drilldown zeigt genau auf den geplanten Monat, nicht auf das
    # gesamte Zeitfenster.
    assert overruns[0]["drilldown"]["from_date"] == month_start.isoformat()


def test_budget_overrun_ignores_bookings_from_other_months(
    headers: dict[str, str], account_id: str
) -> None:
    """Regressionstest fuer den LEFT-JOIN-Bug: eine Buchung ausserhalb des
    betrachteten Monats durfte nie in die Ist-Summe einfliessen, tat es aber,
    weil der Monatsfilter in der ON-Bedingung eines LEFT JOIN stand statt in
    einem WHERE/FILTER - passende Zeilen wurden dadurch nie verworfen."""
    groceries = _category_id(headers, "Lebensmittel")
    month_start = _month_start(0)
    allocation = client.put(
        f"/v1/budgets/{month_start.year}/{month_start.month}/allocations",
        json={"allocations": [{"category_id": groceries, "planned_minor": 45000}]},
        headers=headers,
    )
    assert allocation.status_code == 200, allocation.text

    _book(headers, account_id, months_ago=0, amount_minor=-50000, category_id=groceries)
    # Eine deutlich groessere Buchung in einem anderen Monat derselben
    # Kategorie - darf die Ist-Summe des betrachteten Monats nicht beruehren.
    _book(headers, account_id, months_ago=3, amount_minor=-500000, category_id=groceries)

    overruns = _by_kind(_report(headers), "budget_overrun")
    assert len(overruns) == 1
    assert overruns[0]["evidence"][1]["label"] == "Ist"
    assert overruns[0]["evidence"][1]["amount_minor"] == 50000
    assert overruns[0]["amount_minor"] == 5000


def test_budget_overrun_follows_the_anchor_month(
    headers: dict[str, str], account_id: str
) -> None:
    """Ohne Anker zeigt der Bericht den neuesten Monat; mit anchor_year/
    anchor_month gezielt einen frueheren - so navigiert die Monatsansicht."""
    groceries = _category_id(headers, "Lebensmittel")
    older_month = _month_start(3)
    allocation = client.put(
        f"/v1/budgets/{older_month.year}/{older_month.month}/allocations",
        json={"allocations": [{"category_id": groceries, "planned_minor": 45000}]},
        headers=headers,
    )
    assert allocation.status_code == 200, allocation.text
    _book(headers, account_id, months_ago=3, amount_minor=-50000, category_id=groceries)
    # Neuere Buchung in einer anderen Kategorie, damit "juengster Monat mit
    # Daten" (der Default-Anker ohne anchor_year/anchor_month) tatsaechlich
    # der aktuelle statt der aeltere Monat ist.
    restaurant = _category_id(headers, "Restaurant")
    _book(headers, account_id, months_ago=0, amount_minor=-1000, category_id=restaurant)

    assert _by_kind(_report(headers), "budget_overrun") == []

    overruns = _by_kind(
        _report(headers, anchor_year=older_month.year, anchor_month=older_month.month),
        "budget_overrun",
    )
    assert len(overruns) == 1
    assert overruns[0]["amount_minor"] == 5000
    assert overruns[0]["drilldown"]["from_date"] == older_month.isoformat()


def test_small_amounts_are_not_counted_into_the_total_potential(
    headers: dict[str, str], account_id: str
) -> None:
    """Wie viel von vielen Kleinbetraegen vermeidbar ist, weiss der Haushalt -
    nicht der Detektor. Deshalb erscheinen sie, zaehlen aber nicht mit."""
    bakery = _category_id(headers, "Bäckerei & Café")
    for month in range(3):
        for day in (2, 5, 9, 13, 17, 21, 25, 28):
            _book(
                headers,
                account_id,
                months_ago=month,
                amount_minor=-450,
                category_id=bakery,
                day=day,
            )

    report = _report(headers)
    small = _by_kind(report, "small_amounts")
    assert len(small) == 1
    assert small[0]["amount_minor"] == 3600  # 24 x 4,50 EUR auf 3 Monate
    assert small[0]["monthly_potential_minor"] is None
    assert report["monthly_potential_minor"] == 0


# ---------------------------------------------------------------------------
# Optionale Formulierung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("advice", "expected"),
    [
        ("Kündige das teurere der beiden Abos.", True),
        # Eine eigene Zahl des Modells ist im Zweifel falsch - die richtige
        # steht bereits daneben.
        ("Du könntest 50 € im Monat sparen.", False),
        ("Streiche 3 Restaurantbesuche.", False),
        ("", False),
        ("x" * 300, False),
    ],
)
def test_advice_with_own_numbers_is_rejected(advice: str, expected: bool) -> None:
    assert insights_service._advice_is_clean(advice) is expected


def _restaurant_trend(headers: dict[str, str], account_id: str) -> None:
    """Bestand mit einem klaren Kategorietrend (Restaurant 50 -> 150 EUR) -
    ergibt mehrere Empfehlungen, zu denen formuliert werden kann."""
    restaurant = _category_id(headers, "Restaurant")
    for month in range(3, 9):
        _book(headers, account_id, months_ago=month, amount_minor=-5000, category_id=restaurant)
    for month in range(3):
        _book(headers, account_id, months_ago=month, amount_minor=-15000, category_id=restaurant)


def test_ai_advice_is_added_without_changing_any_number(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restaurant_trend(headers, account_id)
    without_ai = _report(headers, months=12)
    stub = ModelStub(
        monkeypatch, lambda body: answer({"advice": "Koch an zwei Abenden pro Woche selbst."})
    )

    with_ai = _report(headers, months=12, with_ai=True)
    assert with_ai["model_name"] == MODEL
    assert with_ai["ai_warnings"] == []
    assert all(item["ai_advice"] for item in with_ai["insights"])
    # Ueber den Gateway: strukturiert angefordert, Budget aus dem Fenster.
    assert len(stub.bodies) == len(with_ai["insights"])
    assert stub.bodies[0]["response_format"]["json_schema"]["name"] == "financial_advice"
    assert stub.bodies[0]["max_tokens"] > 0
    # Dieselben Zahlen wie ohne Modell - die Formulierung ist ein Zusatz,
    # keine zweite Rechnung.
    assert with_ai["monthly_potential_minor"] == without_ai["monthly_potential_minor"]
    for before, after in zip(without_ai["insights"], with_ai["insights"], strict=True):
        assert before["title"] == after["title"]
        assert before["amount_minor"] == after["amount_minor"]


def test_report_stays_complete_without_a_model(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Modell: stilles, aber sichtbar gekennzeichnetes Weglassen - der
    Bericht ist vollstaendig, die Ratschlaege fehlen, ai_warnings sagt warum."""
    _restaurant_trend(headers, account_id)
    stub = ModelStub(
        monkeypatch, lambda body: answer({"advice": "x"}), target=unavailable_target()
    )

    report = _report(headers, months=12, with_ai=True)
    assert report["model_name"] is None
    assert _by_kind(report, "category_trend")[0]["monthly_potential_minor"] == 10000
    assert all(item["ai_advice"] is None for item in report["insights"])
    assert [warning["code"] for warning in report["ai_warnings"]] == ["model_unavailable"]
    assert stub.bodies == []


@pytest.mark.parametrize(
    "content",
    [
        # Strukturiert, aber mitten im Satz abgeschnitten: frueher ging dieser
        # Text ohne schliessende Klammer als Ratschlag durch (keine Ziffer,
        # kurz genug).
        '{"advice": "Kündige das teu',
        # Prompt+Parse-Weg: abgeschnittener Klartext ist ebenso kein Satz.
        "Kündige das teu",
    ],
)
async def test_a_truncated_answer_never_becomes_advice(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """Befund K2: finish_reason=length heisst unvollstaendig - nie ein Rat.
    Das ist ein Zustand der Einstellung (Denken frisst das Budget), deshalb
    wird keine weitere Empfehlung gefragt und kein "ohne Denken" versucht."""
    _restaurant_trend(headers, account_id)
    assert len(_report(headers, months=12)["insights"]) >= 2
    stub = ModelStub(monkeypatch, lambda body: answer(content, finish_reason="length"))

    report = _report(headers, months=12, with_ai=True)
    assert all(item["ai_advice"] is None for item in report["insights"])
    assert report["model_name"] is None
    assert [warning["code"] for warning in report["ai_warnings"]] == ["model_budget_exhausted"]
    assert len(stub.bodies) == 1
    assert "reasoning_effort" not in stub.bodies[0]

    calls = await llm_calls(headers["X-Household-Id"], "insights_advice")
    assert [(c["status"], c["reason_code"], c["finish_reason"]) for c in calls] == [
        ("error", "model_budget_exhausted", "length")
    ]


async def test_advice_with_numbers_is_rejected_and_marked_but_the_run_goes_on(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine eigene Zahl des Modells betrifft nur diesen Ratschlag: er wird
    verworfen, die Protokollzeile markiert, die naechste Empfehlung trotzdem
    gefragt."""
    _restaurant_trend(headers, account_id)
    total = len(_report(headers, months=12)["insights"])
    assert total >= 2
    stub = ModelStub(monkeypatch, lambda body: answer({"advice": "Spare 50 Euro im Restaurant."}))

    report = _report(headers, months=12, with_ai=True)
    assert all(item["ai_advice"] is None for item in report["insights"])
    assert len(stub.bodies) == total
    assert [warning["code"] for warning in report["ai_warnings"]] == ["model_output_unusable"]
    assert f"{total} von {total}" in report["ai_warnings"][0]["message"]

    calls = await llm_calls(headers["X-Household-Id"], "insights_advice")
    assert {(c["status"], c["reason_code"]) for c in calls} == {("error", "model_output_unusable")}
    assert len(calls) == total


def test_ai_switched_off_is_marked_and_never_contacts_the_server(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _restaurant_trend(headers, account_id)
    stub = ModelStub(monkeypatch, lambda body: answer({"advice": "x"}))
    switched = client.put("/v1/ai/settings", json={"is_enabled": False}, headers=headers)
    assert switched.status_code == 200, switched.text

    report = _report(headers, months=12, with_ai=True)
    assert [warning["code"] for warning in report["ai_warnings"]] == ["ai_disabled"]
    assert stub.discoveries == 0
    assert stub.bodies == []


async def test_a_viewer_gets_advice_and_the_call_is_still_protocolled(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Betrachter duerfen Empfehlungen abrufen. Frueher erlaubte RLS das
    Protokoll (llm_calls, ai_model_measurements) nur owner/editor: der erste
    Eintrag scheiterte (42501 -> 403 fuer den ganzen Bericht), und eine eigene
    Sitzung an RLS vorbei war der Ausweg. Jetzt duerfen alle Mitglieder im
    eigenen Haushalt protokollieren und messen (0001_schema.sql) - in der
    Request-Sitzung, unter RLS. Das Protokoll ist vollstaendig, die Messung
    lernt mit."""
    _restaurant_trend(headers, account_id)
    viewer_headers = _invite_member(
        client, headers, headers["X-Household-Id"], "viewer", "ins-viewer"
    )
    stub = ModelStub(
        monkeypatch, lambda body: answer({"advice": "Koch an zwei Abenden pro Woche selbst."})
    )

    report = _report(viewer_headers, months=12, with_ai=True)
    assert report["model_name"] == MODEL
    assert all(item["ai_advice"] for item in report["insights"])

    calls = await llm_calls(headers["X-Household-Id"], "insights_advice")
    assert len(calls) == len(stub.bodies) == len(report["insights"])
    assert {call["status"] for call in calls} == {"success"}
    async with get_session_factory()() as session:
        measured = (
            await session.execute(
                text(
                    "select calls from ai_model_measurements "
                    "where household_id = :household_id and model_key = :model"
                ),
                {"household_id": headers["X-Household-Id"], "model": MODEL},
            )
        ).scalar_one()
    assert measured == len(calls)


async def test_a_viewers_rejected_advice_is_marked_in_the_protocol(
    headers: dict[str, str], account_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch das Nachtragen des fachlichen Ergebnisses (Ratschlag mit eigener
    Zahl verworfen) gelingt einem Betrachter - unter RLS, nur Status, Grund
    und Meldung."""
    _restaurant_trend(headers, account_id)
    viewer_headers = _invite_member(
        client, headers, headers["X-Household-Id"], "viewer", "ins-viewer-reject"
    )
    stub = ModelStub(monkeypatch, lambda body: answer({"advice": "Spare 50 Euro."}))

    report = _report(viewer_headers, months=12, with_ai=True)
    assert all(item["ai_advice"] is None for item in report["insights"])
    assert [warning["code"] for warning in report["ai_warnings"]] == ["model_output_unusable"]

    calls = await llm_calls(headers["X-Household-Id"], "insights_advice")
    assert len(calls) == len(stub.bodies) == len(report["insights"])
    assert {(call["status"], call["reason_code"]) for call in calls} == {
        ("error", "model_output_unusable")
    }
