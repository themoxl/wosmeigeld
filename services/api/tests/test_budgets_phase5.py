import uuid
from datetime import date
from itertools import pairwise
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Namen aus dem deutschen Standardpaket v1
# (supabase/migrations/0001_schema.sql).
CAT_GROCERIES = "Lebensmittel"
CAT_RENT = "Miete & Hausgeld"
CAT_SALARY = "Gehalt & Lohn"


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p5-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Phase-5-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    acc = client.post(
        "/v1/accounts",
        json={"name": "Hauptkonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return acc["id"]


def _category_id(headers: dict[str, str], name: str) -> str:
    """Sucht eine Seed-Kategorie ueber ihren Namen - Positionen im
    Kategoriebaum sind kein stabiler Vertrag."""
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Default-Kategorie '{name}' nicht gefunden")


def _book(
    headers: dict[str, str],
    account_id: str,
    booked_date: str,
    amount_minor: int,
    category_id: str | None = None,
) -> None:
    res = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": booked_date,
            "amount_minor": amount_minor,
            "category_id": category_id,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text


def _contract(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Beispielvertrag",
        "amount_minor": 1000,
        "frequency": "monthly",
        "next_due_date": "2026-06-01",
    }
    body.update(overrides)
    res = client.post("/v1/contracts", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return dict(res.json())


def test_budget_summary_and_allocations(household_headers: dict[str, str], account_id: str) -> None:
    # Bewusst eine AUSGABEN-Kategorie: total_planned_minor fuehrt nur
    # Ausgabenkategorien, geplante Einnahmen sind kein Budgettopf.
    cat_id = _category_id(household_headers, CAT_GROCERIES)

    b_res = client.get("/v1/budgets/2026/8", headers=household_headers)
    assert b_res.status_code == 200
    b_data = b_res.json()
    assert b_data["year"] == 2026
    assert b_data["month"] == 8
    assert b_data["status"] == "open"

    alloc_res = client.put(
        "/v1/budgets/2026/8/allocations",
        json={
            "allocations": [
                {
                    "category_id": cat_id,
                    "planned_minor": 50000,
                    "rollover_rule": "accumulate",
                    "note": "Lebensmittel Budget",
                }
            ]
        },
        headers=household_headers,
    )
    assert alloc_res.status_code == 200
    updated_b = alloc_res.json()
    assert updated_b["total_planned_minor"] == 50000


def test_annual_matrix_and_overview_report(
    household_headers: dict[str, str], account_id: str
) -> None:
    m_res = client.get("/v1/budgets/2026/matrix", headers=household_headers)
    assert m_res.status_code == 200
    m_data = m_res.json()
    assert m_data["year"] == 2026
    assert len(m_data["rows"]) >= 1
    assert len(m_data["rows"][0]["months"]) == 12

    ov_res = client.get("/v1/reports/overview", headers=household_headers)
    assert ov_res.status_code == 200
    ov_data = ov_res.json()
    assert "total_income_minor" in ov_data
    assert len(ov_data["cashflow_last_6_months"]) == 6


def test_cashflow_trend_report(household_headers: dict[str, str], account_id: str) -> None:
    trend_res = client.get(
        "/v1/reports/cashflow-trend", params={"months": 24}, headers=household_headers
    )
    assert trend_res.status_code == 200
    months = trend_res.json()["months"]
    assert len(months) == 24

    # Chronologisch aufsteigend: jeder Folgemonat ist genau einen Monat spaeter.
    for prev, nxt in pairwise(months):
        expected_month = 1 if prev["month"] == 12 else prev["month"] + 1
        expected_year = prev["year"] + 1 if prev["month"] == 12 else prev["year"]
        assert (nxt["year"], nxt["month"]) == (expected_year, expected_month)

    default_res = client.get("/v1/reports/cashflow-trend", headers=household_headers)
    assert len(default_res.json()["months"]) == 24

    invalid_res = client.get(
        "/v1/reports/cashflow-trend", params={"months": 0}, headers=household_headers
    )
    assert invalid_res.status_code == 422


# ---------------------------------------------------------------------------
# Fixkosten aus Vertraegen (Phase 4)
# ---------------------------------------------------------------------------


def test_contracts_do_not_move_a_single_booked_number(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Kern der Trennung: ein Vertrag ist eine Erwartung und darf die
    Ist-Rechnung nicht anfassen.

    Ohne diesen Test waere die naheliegende "Vereinheitlichung" - Vertraege in
    total_spent_minor oder in den Needs-Bucket zu addieren - unbemerkt
    moeglich. Sie waere eine Doppelzaehlung, sobald die Rate abgebucht ist.
    """
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _book(household_headers, account_id, "2026-06-12", -20000, groceries)
    before = client.get("/v1/budgets/2026/6", headers=household_headers).json()

    _contract(
        household_headers,
        name="Miete Musterstrasse",
        contract_type="rent",
        amount_minor=95000,
    )
    after = client.get("/v1/budgets/2026/6", headers=household_headers).json()

    for field in (
        "total_income_minor",
        "total_planned_minor",
        "total_rollover_minor",
        "total_spent_minor",
        "total_remaining_minor",
        "savings_rate_percent",
        "bucket_needs_minor",
        "bucket_wants_minor",
        "bucket_savings_minor",
        "bucket_needs_percent",
        "bucket_wants_percent",
        "bucket_savings_percent",
    ):
        assert after[field] == before[field], field

    assert before["fixed_costs_monthly_minor"] == 0
    assert after["fixed_costs_monthly_minor"] == 95000
    assert after["fixed_costs_contract_count"] == 1


def test_fixed_costs_normalize_every_frequency_to_one_month(
    household_headers: dict[str, str],
) -> None:
    _contract(household_headers, name="Abo", amount_minor=1200, frequency="monthly")
    _contract(household_headers, name="Praemie", amount_minor=120000, frequency="yearly")
    _contract(household_headers, name="Beitrag", amount_minor=9000, frequency="quarterly")

    summary = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    # 1200 + 120000/12 + 9000/3
    assert summary["fixed_costs_monthly_minor"] == 1200 + 10000 + 3000
    assert summary["fixed_costs_contract_count"] == 3


def test_cancelled_contract_counts_until_ends_on_ended_never(
    household_headers: dict[str, str],
) -> None:
    """Gekuendigt ist nicht beendet: bis zum Enddatum kostet der Vertrag Geld."""
    _contract(
        household_headers,
        name="Fitnessstudio",
        contract_type="membership",
        amount_minor=4900,
        status="cancelled",
        cancelled_on="2026-04-30",
        ends_on="2026-06-30",
    )
    # Ein beendeter Vertrag zaehlt in keinem Monat mit - auch nicht in einem,
    # der vor seinem Enddatum liegt.
    _contract(
        household_headers,
        name="Zeitung",
        contract_type="subscription",
        amount_minor=2500,
        status="ended",
        ends_on="2026-12-31",
    )

    june = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert june["fixed_costs_monthly_minor"] == 4900
    assert june["fixed_costs_contract_count"] == 1

    july = client.get("/v1/budgets/2026/7", headers=household_headers).json()
    assert july["fixed_costs_monthly_minor"] == 0
    assert july["fixed_costs_contract_count"] == 0


def test_a_contract_that_ends_mid_month_still_costs_that_month(
    household_headers: dict[str, str],
) -> None:
    """Regressionstest: der Stichtag war der Monatsletzte - fuer BEIDE Grenzen.

    Ein zum 9. gekuendigter Vertrag wird in diesem Monat noch einmal abgebucht,
    fiel aber schon am Monatsersten aus Kennzahl, verfuegbarem Rest und Quote.
    Die Vertragsseite (GET /v1/contracts/summary, Anker `today`) zeigte ihn
    dagegen weiter - zwei Fixkostensummen am selben Tag.
    """
    _contract(
        household_headers,
        name="Fitness gekuendigt",
        contract_type="membership",
        amount_minor=4900,
        status="cancelled",
        cancelled_on="2026-06-01",
        ends_on="2026-06-09",
    )

    june = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert june["fixed_costs_monthly_minor"] == 4900
    assert june["fixed_costs_contract_count"] == 1

    # Der Folgemonat kostet nichts mehr.
    july = client.get("/v1/budgets/2026/7", headers=household_headers).json()
    assert july["fixed_costs_monthly_minor"] == 0


def test_a_contract_that_starts_mid_month_counts_from_that_month(
    household_headers: dict[str, str],
) -> None:
    """Gegenstueck zur Endgrenze: der Beginn wird am Monatsletzten gemessen."""
    _contract(
        household_headers,
        name="Neuer Mobilfunk",
        contract_type="telecom",
        amount_minor=2000,
        starts_on="2026-06-20",
        next_due_date="2026-06-20",
    )
    may = client.get("/v1/budgets/2026/5", headers=household_headers).json()
    june = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert may["fixed_costs_monthly_minor"] == 0
    assert june["fixed_costs_monthly_minor"] == 2000


def test_ending_a_contract_records_an_end_date(
    household_headers: dict[str, str],
) -> None:
    """Regressionstest: "Beenden" liess `ends_on` leer.

    Das Zeilenmenue der Vertragsseite sendet nur den Status. Ohne Enddatum kennt
    der Fixkostentrend keine Grenze - die offene Preisstufe trifft jeden Monat
    des Rasters, und die Kernaussage des Berichts
    ("Fixkostenentwicklung ueber 24 Monate") kann nie sinken.
    """
    today = date.today()
    contract = _contract(
        household_headers,
        name="Altes Abo",
        contract_type="subscription",
        amount_minor=1999,
        next_due_date=today.isoformat(),
    )

    res = client.post(
        f"/v1/contracts/{contract['id']}/status",
        json={"status": "ended"},
        headers=household_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["ends_on"] == today.isoformat()

    # Ein mitgeschicktes Datum bleibt unangetastet.
    other = _contract(
        household_headers, name="Zweites Abo", amount_minor=500, next_due_date=today.isoformat()
    )
    explicit = client.post(
        f"/v1/contracts/{other['id']}/status",
        json={"status": "ended", "ends_on": "2026-01-31"},
        headers=household_headers,
    )
    assert explicit.json()["ends_on"] == "2026-01-31"


def test_fixed_costs_stichtag_is_the_budget_month_not_today(
    household_headers: dict[str, str],
) -> None:
    """Ein Vertrag, der spaeter beginnt, taucht im frueheren Monat nicht auf."""
    _contract(
        household_headers,
        name="Neuer Stromvertrag",
        contract_type="utility",
        amount_minor=8000,
        starts_on="2026-07-01",
        next_due_date="2026-07-01",
    )

    june = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    july = client.get("/v1/budgets/2026/7", headers=household_headers).json()
    assert june["fixed_costs_monthly_minor"] == 0
    assert july["fixed_costs_monthly_minor"] == 8000


def test_ratio_falls_back_to_contract_income_until_something_is_booked(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ohne gebuchte Einnahme traegt der Einnahmevertrag die Bezugsgroesse -
    sonst stuende am Monatsersten jedes Mal eine Quote von 0 %."""
    _contract(
        household_headers,
        name="Gehalt",
        contract_type="income",
        direction="income",
        amount_minor=400000,
    )
    _contract(household_headers, name="Miete", contract_type="rent", amount_minor=100000)

    planned = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert planned["fixed_costs_income_basis"] == "contract"
    assert planned["fixed_costs_income_monthly_minor"] == 400000
    # Der Einnahmevertrag verwaessert die Fixkostensumme nicht.
    assert planned["fixed_costs_monthly_minor"] == 100000
    assert planned["fixed_costs_contract_count"] == 1
    assert planned["fixed_costs_ratio_percent"] == 25.0
    assert planned["available_after_fixed_minor"] == 300000

    # Sobald Ist-Einnahmen vorliegen, gewinnen sie.
    _book(household_headers, account_id, "2026-06-01", 500000)
    booked = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert booked["fixed_costs_income_basis"] == "actual"
    assert booked["fixed_costs_ratio_percent"] == 20.0
    assert booked["available_after_fixed_minor"] == 400000


def test_income_basis_minor_exposes_the_denominator_of_every_ratio(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Die Bezugsgroesse selbst, nicht nur ihre Folgezahlen.

    Ohne sie kann die Oberflaeche das 50/30/20-ZIEL nicht in Euro ausrechnen -
    aus fixed_costs_ratio_percent laesst sie sich nur zurueckrechnen, solange
    die Quote ueberhaupt definiert ist. fixed_costs_income_basis sagt, welcher
    der beiden Wege genommen wurde.
    """
    _contract(
        household_headers,
        name="Gehalt",
        contract_type="income",
        direction="income",
        amount_minor=400000,
    )
    _contract(household_headers, name="Miete", contract_type="rent", amount_minor=100000)

    planned = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert planned["fixed_costs_income_basis"] == "contract"
    assert planned["income_basis_minor"] == 400000
    # Konsistent mit den beiden Zahlen, die es schon vorher gab.
    assert planned["available_after_fixed_minor"] == (
        planned["income_basis_minor"] - planned["fixed_costs_monthly_minor"]
    )
    assert planned["fixed_costs_ratio_percent"] == 25.0

    _book(household_headers, account_id, "2026-06-01", 500000)
    booked = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert booked["fixed_costs_income_basis"] == "actual"
    assert booked["income_basis_minor"] == 500000
    assert booked["income_basis_minor"] == booked["total_income_minor"]
    assert booked["available_after_fixed_minor"] == 400000


def test_income_basis_minor_is_zero_without_any_income_reference(
    household_headers: dict[str, str],
) -> None:
    """Weder Ist noch Vertrag: die Basis ist 0 und die Quote entfaellt. Die
    Oberflaeche muss vor jeder Prozentrechnung auf > 0 pruefen."""
    _contract(household_headers, name="Miete", contract_type="rent", amount_minor=100000)
    summary = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert summary["income_basis_minor"] == 0
    assert summary["fixed_costs_ratio_percent"] == 0.0


def test_ratio_is_zero_without_any_income_reference(
    household_headers: dict[str, str],
) -> None:
    _contract(household_headers, name="Miete", contract_type="rent", amount_minor=100000)
    summary = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert summary["fixed_costs_ratio_percent"] == 0.0
    assert summary["fixed_costs_income_basis"] == "contract"
    assert summary["available_after_fixed_minor"] == -100000


def test_income_basis_uses_actual_even_when_it_is_negative(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ein Erstattungsueberschuss (mehr zurueckgebucht als eingegangen) ist ein
    NEGATIVER Ist-Wert und trotzdem die korrekte Bezugsgroesse - der
    Vertragsfallback greift nur bei total_income_minor EXAKT 0, nicht bei
    "kleiner/gleich 0". Die Quote bleibt 0.0, weil sie nur fuer eine positive
    Basis berechnet wird (kein negativer oder ueber-100%-Wert)."""
    _contract(
        household_headers, name="Gehalt", contract_type="income", direction="income",
        amount_minor=400000,
    )
    _contract(household_headers, name="Miete", contract_type="rent", amount_minor=100000)

    salary = _category_id(household_headers, CAT_SALARY)
    # Eine Gehaltsrueckforderung uebersteigt die einzige Buchung des Monats:
    # total_income_minor wird negativ.
    _book(household_headers, account_id, "2026-06-05", -50000, salary)

    summary = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert summary["total_income_minor"] == -50000
    assert summary["fixed_costs_income_basis"] == "actual"
    assert summary["income_basis_minor"] == -50000
    # NICHT der Vertragsfallback (der ergaebe 25.0 bei 400.000 Basis).
    assert summary["fixed_costs_ratio_percent"] == 0.0
    assert summary["available_after_fixed_minor"] == -50000 - 100000


def test_income_basis_falls_back_to_contract_only_when_actual_is_exactly_zero(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Vertragsfallback greift NUR bei total_income_minor == 0 - auch
    dann, wenn dieser Nullwert aus bereits gebuchten, sich gegenseitig
    aufhebenden Splits entsteht und nicht aus schlichter Abwesenheit von
    Buchungen."""
    _contract(
        household_headers, name="Gehalt", contract_type="income", direction="income",
        amount_minor=400000,
    )
    salary = _category_id(household_headers, CAT_SALARY)
    _book(household_headers, account_id, "2026-06-05", 30000, salary)
    _book(household_headers, account_id, "2026-06-06", -30000, salary)

    summary = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert summary["total_income_minor"] == 0
    assert summary["fixed_costs_income_basis"] == "contract"
    assert summary["fixed_costs_income_monthly_minor"] == 400000


def test_overview_report_carries_the_fixed_cost_figures(
    household_headers: dict[str, str],
) -> None:
    today = date.today()
    _contract(
        household_headers,
        name="Versicherung",
        contract_type="insurance",
        amount_minor=6000,
        next_due_date=today.isoformat(),
    )

    overview = client.get("/v1/reports/overview", headers=household_headers).json()
    summary = client.get(
        f"/v1/budgets/{today.year}/{today.month}", headers=household_headers
    ).json()
    # Kein zweiter Datenpfad: die Uebersicht reicht die Zahlen des
    # Monatsabschlusses durch.
    assert overview["fixed_costs_monthly_minor"] == summary["fixed_costs_monthly_minor"]
    assert overview["available_after_fixed_minor"] == summary["available_after_fixed_minor"]
    assert overview["fixed_costs_ratio_percent"] == summary["fixed_costs_ratio_percent"]
    # Faellig heute, also innerhalb der Sieben-Tage-Vorschau.
    assert overview["upcoming_due_count"] >= 1


# ---------------------------------------------------------------------------
# Fixkostentrend
# ---------------------------------------------------------------------------


def test_fixed_costs_trend_reads_the_agreed_amount_not_the_booked_one(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Die Zeitreihe kommt aus contract_amount_history und ist damit exakt:
    sie zeigt die Preiserhoehung im Monat ihres Inkrafttretens, unabhaengig
    davon, ob und wann etwas gebucht wurde."""
    today = date.today()
    this_month = date(today.year, today.month, 1)
    last_month = (
        date(today.year - 1, 12, 1)
        if today.month == 1
        else date(today.year, today.month - 1, 1)
    )

    strom = _contract(
        household_headers,
        name="Stadtwerke Strom",
        contract_type="utility",
        amount_minor=5000,
        next_due_date=last_month.isoformat(),
    )
    _contract(
        household_headers,
        name="Hausrat",
        contract_type="insurance",
        amount_minor=12000,
        frequency="yearly",
        next_due_date=last_month.isoformat(),
    )
    # Preiserhoehung ab diesem Monat.
    raise_res = client.post(
        f"/v1/contracts/{strom['id']}/history",
        json={"amount_minor": 6500, "valid_from": this_month.isoformat()},
        headers=household_headers,
    )
    assert raise_res.status_code == 201, raise_res.text

    # Eine Buchung darf die Zeitreihe nicht beeinflussen.
    _book(household_headers, account_id, this_month.isoformat(), -33300)

    res = client.get(
        "/v1/reports/fixed-costs-trend", params={"months": 6}, headers=household_headers
    )
    assert res.status_code == 200, res.text
    months = res.json()["months"]
    assert len(months) == 6

    by_key = {(m["year"], m["month"]): m for m in months}
    prev = by_key[(last_month.year, last_month.month)]
    cur = by_key[(this_month.year, this_month.month)]

    # 12000 jaehrlich sind 1000 im Monat.
    assert prev["fixed_costs_minor"] == 5000 + 1000
    assert cur["fixed_costs_minor"] == 6500 + 1000
    assert prev["contract_count"] == 2
    assert cur["contract_count"] == 2

    assert {item["contract_type"]: item["amount_minor"] for item in cur["by_type"]} == {
        "utility": 6500,
        "insurance": 1000,
    }
    # Absteigend nach Betrag, damit der Treiber oben steht.
    assert [item["amount_minor"] for item in cur["by_type"]] == [6500, 1000]


def test_fixed_costs_trend_is_chronological_and_bounded(
    household_headers: dict[str, str],
) -> None:
    res = client.get(
        "/v1/reports/fixed-costs-trend", params={"months": 24}, headers=household_headers
    )
    assert res.status_code == 200
    months = res.json()["months"]
    assert len(months) == 24
    for prev, nxt in pairwise(months):
        expected_month = 1 if prev["month"] == 12 else prev["month"] + 1
        expected_year = prev["year"] + 1 if prev["month"] == 12 else prev["year"]
        assert (nxt["year"], nxt["month"]) == (expected_year, expected_month)

    # Ohne Vertraege ist die Reihe leer, aber vollstaendig - und ohne
    # Vorjahreswert gibt es keine Prozentaussage.
    assert all(m["fixed_costs_minor"] == 0 for m in months)
    assert all(m["yoy_percent"] is None for m in months)

    default_res = client.get("/v1/reports/fixed-costs-trend", headers=household_headers)
    assert len(default_res.json()["months"]) == 24

    for invalid in (0, 37):
        res = client.get(
            "/v1/reports/fixed-costs-trend",
            params={"months": invalid},
            headers=household_headers,
        )
        assert res.status_code == 422


def test_fixed_costs_trend_year_over_year(household_headers: dict[str, str]) -> None:
    """I10: der Jahresvergleich ist exakt und faellt aus derselben Abfrage ab -
    auch fuer den aeltesten ausgewiesenen Monat, dessen Vorjahresmonat gar
    nicht mehr in der Antwort steht."""
    today = date.today()
    start = date(today.year - 2, today.month, 1)
    contract = _contract(
        household_headers,
        name="Mobilfunk",
        contract_type="telecom",
        amount_minor=2000,
        next_due_date=start.isoformat(),
    )
    # Verdopplung genau zwoelf Monate spaeter.
    bump = date(today.year - 1, today.month, 1)
    res = client.post(
        f"/v1/contracts/{contract['id']}/history",
        json={"amount_minor": 4000, "valid_from": bump.isoformat()},
        headers=household_headers,
    )
    assert res.status_code == 201, res.text

    months = client.get(
        "/v1/reports/fixed-costs-trend", params={"months": 12}, headers=household_headers
    ).json()["months"]
    by_key = {(m["year"], m["month"]): m for m in months}
    # Der aelteste Monat der Antwort liegt elf Monate zurueck; sein
    # Vorjahresmonat gehoert zum Vorlauf, nicht zur Antwort.
    oldest = months[0]
    assert oldest["yoy_percent"] == 100.0
    assert by_key[(today.year, today.month)]["yoy_percent"] == 0.0


def test_close_month(household_headers: dict[str, str], account_id: str) -> None:
    c_res = client.post("/v1/budgets/2026/8/close", headers=household_headers)
    assert c_res.status_code == 200
    c_data = c_res.json()
    assert c_data["status"] == "closed"


def test_close_month_only_rolls_over_categories_with_accumulate_rule(
    household_headers: dict[str, str], account_id: str
) -> None:
    # Zwei AUSGABEN-Kategorien - nur die fuehren einen uebertragbaren
    # Budgettopf (Einnahmekategorien sind kein Budgettopf).
    accumulate_cat_id = _category_id(household_headers, CAT_GROCERIES)
    none_cat_id = _category_id(household_headers, CAT_RENT)

    client.put(
        "/v1/budgets/2026/9/allocations",
        json={
            "allocations": [
                {
                    "category_id": accumulate_cat_id,
                    "planned_minor": 10000,
                    "rollover_rule": "accumulate",
                },
                {
                    "category_id": none_cat_id,
                    "planned_minor": 10000,
                    "rollover_rule": "none",
                },
            ]
        },
        headers=household_headers,
    )

    close_res = client.post("/v1/budgets/2026/9/close", headers=household_headers)
    assert close_res.status_code == 200
    assert close_res.json()["rollovers_created"] == 1

    next_summary = client.get("/v1/budgets/2026/10", headers=household_headers).json()
    by_id = {
        cat["category_id"]: cat
        for grp in next_summary["groups"]
        for cat in grp["categories"]
    }
    assert by_id[accumulate_cat_id]["rollover_minor"] == 10000
    assert by_id[none_cat_id]["rollover_minor"] == 0


# ---------------------------------------------------------------------------
# Vormonatsvergleich, Vertrags-Forecast und Lebenszeit-Aktivitaet
# ---------------------------------------------------------------------------


def _category_row(summary: dict[str, Any], category_id: str) -> dict[str, Any]:
    for group in summary["groups"]:
        for category in group["categories"]:
            if category["category_id"] == category_id:
                return category
    raise AssertionError(f"Kategorie {category_id} nicht in der Budgetantwort enthalten")


def test_prev_month_actual_mirrors_last_months_booking(
    household_headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _book(household_headers, account_id, "2026-06-12", -20000, groceries)

    # Juli sieht das Juni-Ist als Vormonat - positiv, wie ein Ist-Wert auch
    # dargestellt wird (spent_minor ist ebenfalls positiv).
    july = client.get("/v1/budgets/2026/7", headers=household_headers).json()
    assert _category_row(july, groceries)["prev_month_actual_minor"] == 20000

    # Juni selbst hat (mangels Mai-Buchung) keinen Vormonatswert.
    june = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert _category_row(june, groceries)["prev_month_actual_minor"] == 0

    # Jahresgrenze: Januar 2027 muss Dezember 2026 als Vormonat nehmen statt
    # (fehlerhaft) Januar desselben Jahres.
    _book(household_headers, account_id, "2026-12-05", -5000, groceries)
    jan = client.get("/v1/budgets/2027/1", headers=household_headers).json()
    assert _category_row(jan, groceries)["prev_month_actual_minor"] == 5000


def test_contract_forecast_shows_full_due_amount_and_survives_booking(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Forecast und Ist sind unabhaengige Vergleichszahlen (siehe
    budget_service.get_budget_summary): der Forecast bleibt auch stehen,
    nachdem die Buchung tatsaechlich gelandet ist - er wird NICHT um bereits
    Gebuchtes bereinigt.
    """
    insurance = _category_id(household_headers, CAT_RENT)
    _contract(
        household_headers,
        name="KFZ-Versicherung",
        contract_type="insurance",
        category_id=insurance,
        amount_minor=48000,
        frequency="yearly",
        next_due_date="2026-03-15",
    )

    march = client.get("/v1/budgets/2026/3", headers=household_headers).json()
    march_row = _category_row(march, insurance)
    assert march_row["contract_forecast_minor"] == 48000
    assert march_row["spent_minor"] == 0
    # Traegt die Hover-Erklaerung im Frontend ("wieso wird hier etwas erwartet").
    assert len(march_row["contract_forecast_sources"]) == 1
    source = march_row["contract_forecast_sources"][0]
    assert source["contract_name"] == "KFZ-Versicherung"
    assert source["amount_minor"] == 48000
    assert source["due_date"] == "2026-03-15"

    # Ein Monat ohne Faelligkeit bleibt bei 0, auch die Quellenliste ist leer.
    april = client.get("/v1/budgets/2026/4", headers=household_headers).json()
    april_row = _category_row(april, insurance)
    assert april_row["contract_forecast_minor"] == 0
    assert april_row["contract_forecast_sources"] == []

    # Die Praemie wird tatsaechlich abgebucht - der Forecast bleibt trotzdem
    # stehen, nur Ist kommt jetzt dazu.
    _book(household_headers, account_id, "2026-03-15", -48000, insurance)
    march_after = client.get("/v1/budgets/2026/3", headers=household_headers).json()
    march_after_row = _category_row(march_after, insurance)
    assert march_after_row["contract_forecast_minor"] == 48000
    assert march_after_row["spent_minor"] == 48000


def test_contract_forecast_sources_has_one_entry_per_due_date(
    household_headers: dict[str, str],
) -> None:
    """Ein woechentlicher Vertrag kann im selben Monat mehrfach faellig sein -
    die Quellenliste zaehlt dann jede Faelligkeit einzeln, nicht nur den
    Vertrag, sonst erklaert der Hover die Summe nicht korrekt."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _contract(
        household_headers,
        name="Wochenmarkt-Abo",
        category_id=groceries,
        amount_minor=1500,
        frequency="weekly",
        next_due_date="2026-06-02",
    )

    june = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    june_row = _category_row(june, groceries)
    sources = june_row["contract_forecast_sources"]
    assert len(sources) > 1
    assert june_row["contract_forecast_minor"] == sum(s["amount_minor"] for s in sources)
    assert all(s["contract_name"] == "Wochenmarkt-Abo" for s in sources)


def test_has_activity_ever_survives_quiet_months_and_new_contracts(
    household_headers: dict[str, str], account_id: str
) -> None:
    groceries = _category_id(household_headers, CAT_GROCERIES)
    rent = _category_id(household_headers, CAT_RENT)

    # Ganz ohne Bewegung: weder Planbetrag noch Buchung noch Vertrag.
    fresh = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert _category_row(fresh, rent)["has_activity_ever"] is False

    # Einmalige Buchung im Februar - Juni hat selbst nichts, "jemals" bleibt
    # trotzdem wahr.
    _book(household_headers, account_id, "2026-02-10", -3000, groceries)
    june = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    june_row = _category_row(june, groceries)
    assert june_row["spent_minor"] == 0
    assert june_row["has_activity_ever"] is True

    # Ein gerade erst angelegter Jahresvertrag hat selbst noch nie eine
    # Buchung oder einen Planbetrag ausgeloest, ist aber trotzdem keine tote
    # Kategorie - sonst wuerde "jemals" ihn im ersten Jahr verpassen.
    _contract(
        household_headers,
        name="Hausratversicherung",
        contract_type="insurance",
        category_id=rent,
        amount_minor=36000,
        frequency="yearly",
        next_due_date="2026-09-01",
    )
    june_after_contract = client.get("/v1/budgets/2026/6", headers=household_headers).json()
    assert _category_row(june_after_contract, rent)["has_activity_ever"] is True
