"""GET /v1/reports/variable-costs - woraus die variablen Kosten bestehen.

Der Bericht ist das Gegenstueck zu `/v1/reports/fixed-costs-trend`. Seine
einzige wirklich heikle Zusage ist die Abgrenzung "variabel": genau das, was
die Liquiditaetsvorschau als variablen Monatsansatz rechnet, also
`CASHFLOW_EXPENSE_PREDICATE` ohne die vertragsgedeckten Kategorien. Weicht sie
ab, nennen Vorschau und Bericht dem Nutzer zwei verschiedene Zahlen fuer
dieselbe Aussage - deshalb haengen zwei Tests genau daran
(`test_categories_sum_up_to_the_monthly_average` und
`test_contract_covered_category_never_appears`).
"""

import uuid
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Namen aus dem deutschen Standardpaket v1 (supabase/migrations/0001_schema.sql).
CAT_GROCERIES = "Lebensmittel"
CAT_RESTAURANT = "Restaurant"
CAT_RENT = "Miete & Hausgeld"

MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
)


def _headers(name: str) -> dict[str, str]:
    token = _signup(f"varcost-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post("/v1/households", json={"name": name}, headers=_auth_headers(token))
    assert household.status_code == 201, household.text
    return {**_auth_headers(token), "X-Household-Id": household.json()["id"]}


@pytest.fixture()
def household_headers() -> dict[str, str]:
    return _headers("Variable-Kosten-Testhaushalt")


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    acc = client.post(
        "/v1/accounts",
        json={"name": "Hauptkonto", "account_type": "checking"},
        headers=household_headers,
    )
    assert acc.status_code == 201, acc.text
    return str(acc.json()["id"])


def _category_id(headers: dict[str, str], name: str) -> str:
    """Sucht eine Seed-Kategorie ueber ihren Namen - Positionen im
    Kategoriebaum sind kein stabiler Vertrag."""
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Default-Kategorie '{name}' nicht gefunden")


def _month_start(offset: int) -> date:
    """Erster Tag des Monats `offset` Monate vor dem laufenden."""
    today = date.today()
    index = today.year * 12 + (today.month - 1) - offset
    return date(index // 12, index % 12 + 1, 1)


def _book(
    headers: dict[str, str],
    account_id: str,
    booked_date: date,
    amount_minor: int,
    category_id: str | None = None,
) -> None:
    res = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": booked_date.isoformat(),
            "amount_minor": amount_minor,
            "category_id": category_id,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text


def _contract(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Beispielvertrag",
        "amount_minor": 100_000,
        "frequency": "monthly",
        "next_due_date": _month_start(6).isoformat(),
    }
    body.update(overrides)
    res = client.post("/v1/contracts", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return dict(res.json())


def _report(headers: dict[str, str], months: int = 12) -> dict[str, Any]:
    res = client.get(
        "/v1/reports/variable-costs", params={"months": months}, headers=headers
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def test_categories_sum_up_to_the_monthly_average(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Die Kernzusage der Tabelle: die Kategorien ergeben zusammen genau den
    ausgewiesenen Monatsschnitt.

    Krumme Betraege sind Absicht - je Kategorie einzeln gerundet fehlten oder
    entstuenden hier ein paar Cent, und die Tabelle widerspraeche ihrer eigenen
    Kopfzeile.
    """
    groceries = _category_id(household_headers, CAT_GROCERIES)
    restaurant = _category_id(household_headers, CAT_RESTAURANT)

    for offset in (1, 2, 3):
        _book(
            household_headers, account_id, _month_start(offset).replace(day=10), -30_000, groceries
        )
    _book(household_headers, account_id, _month_start(2).replace(day=11), -10_001, restaurant)
    # Ohne Kategorie ist eine eigene Zeile, kein Ausschluss.
    _book(household_headers, account_id, _month_start(3).replace(day=12), -5_000, None)

    data = _report(household_headers, months=3)

    assert data["months"] == 3
    assert data["generated_on"] == date.today().isoformat()
    # 105.001 Cent auf drei Monate.
    assert data["monthly_average_minor"] == 35_000
    assert sum(item["monthly_average_minor"] for item in data["categories"]) == 35_000

    by_name = {item["category_name"]: item for item in data["categories"]}
    assert set(by_name) == {CAT_GROCERIES, CAT_RESTAURANT, "(ohne Kategorie)"}
    assert by_name[CAT_GROCERIES]["monthly_average_minor"] == 30_000
    assert by_name[CAT_GROCERIES]["transaction_count"] == 3
    assert by_name[CAT_GROCERIES]["group_name"] is not None
    assert by_name["(ohne Kategorie)"]["category_id"] is None
    assert by_name["(ohne Kategorie)"]["group_name"] is None

    # Absteigend nach Monatsschnitt - der groesste Hebel steht oben.
    values = [item["monthly_average_minor"] for item in data["categories"]]
    assert values == sorted(values, reverse=True)

    # Der Anteil bezieht sich auf denselben Monatsschnitt.
    assert by_name[CAT_GROCERIES]["share_percent"] == 85.7

    # Ein Trendwert je Monat der Reihe, gleiche Reihenfolge.
    assert len(data["series"]) == 3
    for item in data["categories"]:
        assert len(item["trend"]) == 3
    assert by_name[CAT_GROCERIES]["trend"] == [30_000, 30_000, 30_000]
    # Der aelteste Monat der Reihe ist offset 3, der juengste offset 1.
    assert by_name["(ohne Kategorie)"]["trend"] == [5_000, 0, 0]
    assert by_name[CAT_RESTAURANT]["trend"] == [0, 10_001, 0]

    # Die Monatsreihe traegt dieselben Summen wie die Kategorien.
    assert [entry["variable_minor"] for entry in data["series"]] == [35_000, 40_001, 30_000]


def test_contract_covered_category_never_appears(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Was ein Vertrag deckt, ist Fixkosten - auch dann, wenn es als ganz
    normale Buchung auf einer Kategorie liegt.

    Ohne diesen Ausschluss zaehlte die Miete zweimal: einmal als
    `fixed_minor` aus dem Betragsverlauf und ein zweites Mal als variable
    Ausgabe. Genau dieselbe Abgrenzung wendet die Liquiditaetsvorschau an
    (`household_forecast_service.covered_category_ids`).
    """
    rent = _category_id(household_headers, CAT_RENT)
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _contract(
        household_headers,
        name="Miete",
        contract_type="rent",
        amount_minor=100_000,
        category_id=rent,
    )

    for offset in (1, 2):
        _book(household_headers, account_id, _month_start(offset).replace(day=3), -100_000, rent)
        _book(
            household_headers, account_id, _month_start(offset).replace(day=9), -20_000, groceries
        )

    data = _report(household_headers, months=2)

    names = {item["category_name"] for item in data["categories"]}
    assert CAT_RENT not in names
    assert names == {CAT_GROCERIES}
    assert data["monthly_average_minor"] == 20_000
    assert [entry["variable_minor"] for entry in data["series"]] == [20_000, 20_000]

    # Der Vertrag steht auf der anderen Seite der Rechnung: als Fixkosten aus
    # dem vereinbarten Betrag, nicht aus der Buchung.
    assert data["monthly_fixed_average_minor"] == 100_000
    assert [entry["fixed_minor"] for entry in data["series"]] == [100_000, 100_000]


def test_running_month_is_excluded_and_series_is_chronological(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Nur volle Monate. Der laufende Monat ist erst halb gebucht und wuerde
    den Schnitt nach unten ziehen - jede Sparanstrengung saehe erfolgreicher
    aus, als sie ist."""
    groceries = _category_id(household_headers, CAT_GROCERIES)
    _book(household_headers, account_id, _month_start(1).replace(day=5), -12_000, groceries)
    _book(household_headers, account_id, date.today(), -900_000, groceries)

    data = _report(household_headers, months=6)

    series = data["series"]
    assert len(series) == 6
    today = date.today()
    assert all((entry["year"], entry["month"]) != (today.year, today.month) for entry in series)

    last_full = date(today.year, today.month, 1) - timedelta(days=1)
    assert (series[-1]["year"], series[-1]["month"]) == (last_full.year, last_full.month)
    assert series[-1]["label"] == f"{MONTH_ABBR[last_full.month - 1]} {last_full.year}"

    for prev, nxt in pairwise(series):
        expected_month = 1 if prev["month"] == 12 else prev["month"] + 1
        expected_year = prev["year"] + 1 if prev["month"] == 12 else prev["year"]
        assert (nxt["year"], nxt["month"]) == (expected_year, expected_month)

    # 12.000 Cent auf sechs Monate; die 900.000 des laufenden Monats zaehlen nicht.
    assert data["monthly_average_minor"] == 2_000
    assert series[-1]["variable_minor"] == 12_000


def test_variable_costs_are_scoped_to_the_household() -> None:
    """Haushaltsisolation: der zweite Haushalt sieht nichts vom ersten."""
    first = _headers("Haushalt A")
    first_account = client.post(
        "/v1/accounts",
        json={"name": "Konto A", "account_type": "checking"},
        headers=first,
    ).json()["id"]
    _book(
        first,
        str(first_account),
        _month_start(1).replace(day=7),
        -44_000,
        _category_id(first, CAT_GROCERIES),
    )

    second = _headers("Haushalt B")
    assert _report(first, months=3)["monthly_average_minor"] > 0

    other = _report(second, months=3)
    assert other["monthly_average_minor"] == 0
    assert other["categories"] == []
    assert all(entry["variable_minor"] == 0 for entry in other["series"])


def test_month_bounds_and_default(household_headers: dict[str, str]) -> None:
    default_res = client.get("/v1/reports/variable-costs", headers=household_headers)
    assert default_res.status_code == 200, default_res.text
    assert default_res.json()["months"] == 12
    assert len(default_res.json()["series"]) == 12

    for invalid in (0, 37):
        res = client.get(
            "/v1/reports/variable-costs", params={"months": invalid}, headers=household_headers
        )
        assert res.status_code == 422
