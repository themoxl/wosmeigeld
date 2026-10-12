"""Referenzfaelle fuer die Budgetmathematik.

Jeder Fall ist von Hand durchgerechnet und prueft Plan, Ist, Uebertrag und
Rest je Kategorie, Gruppe und Gesamt sowie die 50/30/20-Buckets und die
Sparquote. Grundregeln, gegen die hier abgesichert wird:

* Ausgaben liegen negativ in transaction_splits.amount_minor, Einnahmen
  positiv (siehe app/schemas/transaction.py).
* `spent_minor` ist der positive Nettoverbrauch einer AUSGABEN-Kategorie.
  Einnahmekategorien haben immer `spent_minor == 0`; ihr Ist steht in
  `income_actual_minor`. Ein Gehalt darf niemals als Ausgabe zaehlen.
* Eine Erstattung (positiver Split auf einer Ausgabenkategorie) senkt den
  Verbrauch dieser Kategorie und ist keine Einnahme.
* Rest = Plan + Uebertrag - Ist, und zwar auf allen drei Ebenen.
* Als ZEILE erscheint nur, was budgetiert ist: `default_type = 'transfer'`
  nie, `is_budgeted = false` nur mit Planbetrag oder Uebertrag. Das
  Standardpaket setzt is_budgeted fuer Einnahmekategorien auf false - die
  Faelle, die eine Einnahmezeile pruefen, legen den Schalter deshalb
  ausdruecklich um (_enable_in_budget).
"""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

Headers = dict[str, str]
Json = dict[str, object]

# Namen aus dem deutschen Standardpaket v1
# (supabase/migrations/0001_schema.sql). Ein neuer Haushalt
# bekommt diese Taxonomie ueber install_category_taxonomy().
CAT_SALARY = "Gehalt & Lohn"
CAT_GROCERIES = "Lebensmittel"
CAT_RENT = "Miete & Hausgeld"
CAT_CULTURE = "Kultur, Kino & Veranstaltungen"

GROUP_INCOME = "Einnahmen"
GROUP_HOUSING = "Wohnen & Haushalt"
GROUP_FOOD = "Lebensmittel & Gastronomie"
GROUP_LIFESTYLE = "Freizeit, Konsum & Reisen"

_REQUIRED_CATEGORIES = (CAT_SALARY, CAT_GROCERIES, CAT_RENT, CAT_CULTURE)


@pytest.fixture()
def household_headers() -> Headers:
    token = _signup(f"budgetref-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Budget-Referenzhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: Headers) -> str:
    acc = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return str(acc["id"])


@pytest.fixture()
def categories(household_headers: Headers) -> dict[str, str]:
    """Seed-Kategorien nach Namen: 'Gehalt & Lohn' (income),
    'Lebensmittel'/'Miete & Hausgeld' (needs),
    'Kultur, Kino & Veranstaltungen' (wants)."""
    groups = client.get("/v1/categories", headers=household_headers).json()
    by_name = {c["name"]: str(c["id"]) for g in groups for c in g["categories"]}
    missing = [name for name in _REQUIRED_CATEGORIES if name not in by_name]
    assert not missing, f"Standard-Kategorien fehlen im Seed: {missing}"
    return by_name


def _book(
    headers: Headers,
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


def _enable_in_budget(headers: Headers, category_id: str) -> None:
    """Blendet eine Kategorie im Budget ein.

    Das Standardpaket liefert alle Einnahmekategorien mit is_budgeted = false
    aus, das Budget zeigt sie deshalb nicht. Fuer die Referenzfaelle, die die
    Einnahmezeile selbst nachrechnen, wird der Schalter hier umgelegt - genau
    so, wie es ein Nutzer in der Kategorienverwaltung taete."""
    groups = client.get("/v1/categories", headers=headers).json()
    version = next(
        c["version"] for g in groups for c in g["categories"] if str(c["id"]) == category_id
    )
    res = client.patch(
        f"/v1/categories/{category_id}",
        json={"is_budgeted": True},
        headers={**headers, "If-Match": f'"{version}"'},
    )
    assert res.status_code == 200, res.text


def _allocate(headers: Headers, year: int, month: int, allocations: list[Json]) -> Json:
    res = client.put(
        f"/v1/budgets/{year}/{month}/allocations",
        json={"allocations": allocations},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def _summary(headers: Headers, year: int, month: int) -> Json:
    res = client.get(f"/v1/budgets/{year}/{month}", headers=headers)
    assert res.status_code == 200, res.text
    return dict(res.json())


def _group(summary: Json, name: str) -> Json:
    groups = summary["groups"]
    assert isinstance(groups, list)
    for grp in groups:
        if grp["group_name"] == name:
            return dict(grp)
    raise AssertionError(f"Gruppe '{name}' nicht in der Zusammenfassung")


def _cat(summary: Json, name: str) -> Json:
    groups = summary["groups"]
    assert isinstance(groups, list)
    for grp in groups:
        for cat in grp["categories"]:
            if cat["category_name"] == name:
                return dict(cat)
    raise AssertionError(f"Kategorie '{name}' nicht in der Zusammenfassung")


def _num(summary: Json, key: str) -> int:
    """Zahlenfeld der JSON-Antwort.

    Die Antwort wird als dict[str, object] gefuehrt; ohne diese Verengung
    rechnet mypy Cent-Betraege als `object` zusammen. Das isinstance ist
    zugleich die Zusicherung, dass Geldwerte ganzzahlige Minor Units bleiben
    und nie als Float durchrutschen.
    """
    value = summary[key]
    assert isinstance(value, int), f"{key} ist kein ganzzahliger Centbetrag: {value!r}"
    return value


def _assert_totals_consistent(summary: Json) -> None:
    """Invarianten, die in jedem Fall gelten muessen.

    Unkategorisierte negative Splits zaehlen NUR auf Gesamtebene mit
    (total_spent = sum(group.total_spent) + uncategorized_spent) - keine
    Gruppe und kein 50/30/20-Bucket hat sie je gesehen.
    """
    groups = summary["groups"]
    assert isinstance(groups, list)
    uncategorized = _num(summary, "uncategorized_spent_minor")
    assert summary["total_planned_minor"] == sum(g["total_planned_minor"] for g in groups)
    assert summary["total_rollover_minor"] == sum(g["total_rollover_minor"] for g in groups)
    assert (
        summary["total_spent_minor"] == sum(g["total_spent_minor"] for g in groups) + uncategorized
    )
    assert (
        summary["total_remaining_minor"]
        == sum(g["total_remaining_minor"] for g in groups) - uncategorized
    )
    assert _num(summary, "total_remaining_minor") == (
        _num(summary, "total_planned_minor")
        + _num(summary, "total_rollover_minor")
        - _num(summary, "total_spent_minor")
    )
    # Buckets summieren sich auf bucket_classified_spent_minor, nicht auf
    # total_spent_minor - Unkategorisiertes gehoert zu keinem Bucket.
    assert _num(summary, "bucket_classified_spent_minor") == (
        _num(summary, "bucket_needs_minor")
        + _num(summary, "bucket_wants_minor")
        + _num(summary, "bucket_savings_minor")
    )
    # Mit dem vierten Topf (Ausgabenkategorien ohne Bucket) geht die Rechnung
    # dagegen restlos auf - erst dadurch ist eine 50/30/20-Anzeige
    # nachvollziehbar, statt eine unerklaerte Luecke zu lassen.
    assert _num(summary, "total_planned_minor") == (
        _num(summary, "bucket_needs_planned_minor")
        + _num(summary, "bucket_wants_planned_minor")
        + _num(summary, "bucket_savings_planned_minor")
        + _num(summary, "bucket_unassigned_planned_minor")
    )
    assert _num(summary, "total_spent_minor") == (
        _num(summary, "bucket_needs_minor")
        + _num(summary, "bucket_wants_minor")
        + _num(summary, "bucket_savings_minor")
        + _num(summary, "bucket_unassigned_spent_minor")
        + uncategorized
    )
    # Die Einnahmeseite wird parallel, aber getrennt gefuehrt. Bezugspunkt ist
    # hier ausschliesslich die Gruppensumme: unkategorisierte Zufluesse stecken
    # zwar in total_income_minor, gehoeren aber zu keinem Plan.
    for field in (
        "total_planned_income_minor",
        "total_rollover_income_minor",
        "total_income_remaining_minor",
    ):
        assert summary[field] == sum(g[field] for g in groups)
    for grp in groups:
        assert grp["total_planned_minor"] == sum(
            c["planned_minor"] for c in grp["categories"] if c["default_type"] != "income"
        )
        assert grp["total_planned_income_minor"] == sum(
            c["planned_minor"] for c in grp["categories"] if c["default_type"] == "income"
        )
        assert grp["total_rollover_income_minor"] == sum(
            c["rollover_minor"] for c in grp["categories"] if c["default_type"] == "income"
        )
        assert (
            grp["total_income_remaining_minor"]
            == grp["total_planned_income_minor"]
            + grp["total_rollover_income_minor"]
            - grp["total_income_actual_minor"]
        )
        assert grp["total_spent_minor"] == sum(c["spent_minor"] for c in grp["categories"])
        assert grp["total_income_actual_minor"] == sum(
            c["income_actual_minor"] for c in grp["categories"]
        )
        assert (
            grp["total_remaining_minor"]
            == grp["total_planned_minor"] + grp["total_rollover_minor"] - grp["total_spent_minor"]
        )
        for cat in grp["categories"]:
            # Einnahme und Verbrauch schliessen sich gegenseitig aus.
            assert cat["spent_minor"] == 0 or cat["income_actual_minor"] == 0


def test_case_a_income_and_expenses(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """(a) 3.000 EUR Gehalt, 2.000 EUR Ausgaben.

    Von Hand: Ist-Ausgaben 50.000 + 120.000 + 30.000 = 200.000 Cent,
    Ist-Einnahmen 300.000 Cent. Das Gehalt darf NICHT im Verbrauch landen,
    sonst waeren es 500.000 Cent Ausgaben und -66,7 % Sparquote.
    Sparquote = (300.000 - 200.000) / 300.000 = 33,3 %.
    """
    _enable_in_budget(household_headers, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-05", 300000, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-07", -50000, categories[CAT_GROCERIES])
    _book(household_headers, account_id, "2026-03-01", -120000, categories[CAT_RENT])
    _book(household_headers, account_id, "2026-03-20", -30000, categories[CAT_CULTURE])

    _allocate(
        household_headers,
        2026,
        3,
        [
            {"category_id": categories[CAT_GROCERIES], "planned_minor": 60000},
            {"category_id": categories[CAT_RENT], "planned_minor": 120000},
            {"category_id": categories[CAT_CULTURE], "planned_minor": 25000},
        ],
    )
    summary = _summary(household_headers, 2026, 3)

    assert summary["total_income_minor"] == 300000
    assert summary["total_planned_minor"] == 205000
    assert summary["total_rollover_minor"] == 0
    assert summary["total_spent_minor"] == 200000
    assert summary["total_remaining_minor"] == 5000
    assert summary["savings_rate_percent"] == 33.3

    # 50/30/20: needs 170.000, wants 30.000, savings 0 von 200.000 Cent.
    assert summary["bucket_needs_minor"] == 170000
    assert summary["bucket_wants_minor"] == 30000
    assert summary["bucket_savings_minor"] == 0
    assert summary["bucket_needs_percent"] == 85.0
    assert summary["bucket_wants_percent"] == 15.0
    assert summary["bucket_savings_percent"] == 0.0

    gehalt = _cat(summary, CAT_SALARY)
    assert gehalt["spent_minor"] == 0
    assert gehalt["income_actual_minor"] == 300000

    lebensmittel = _cat(summary, CAT_GROCERIES)
    assert lebensmittel["planned_minor"] == 60000
    assert lebensmittel["spent_minor"] == 50000
    assert lebensmittel["remaining_minor"] == 10000
    assert lebensmittel["percent_used"] == 83.3

    einnahmen = _group(summary, GROUP_INCOME)
    assert einnahmen["total_planned_minor"] == 0
    assert einnahmen["total_spent_minor"] == 0
    assert einnahmen["total_income_actual_minor"] == 300000
    # Ohne Einnahmeplan ist der "Rest" negativ: es kam mehr herein als
    # geplant war (0 + 0 - 300.000). Dieselbe Leserichtung wie
    # remaining_minor auf der Einnahmekategorie.
    assert einnahmen["total_planned_income_minor"] == 0
    assert einnahmen["total_income_remaining_minor"] == -300000

    # Die beiden Bedarfs-Kategorien liegen in unterschiedlichen Gruppen; je
    # Gruppe ist es die einzige gebuchte Kategorie, die Gruppensumme
    # entspricht also der Kategoriesumme (zusammen 180.000/170.000/10.000).
    lebensmittel_gruppe = _group(summary, GROUP_FOOD)
    assert lebensmittel_gruppe["total_planned_minor"] == 60000
    assert lebensmittel_gruppe["total_spent_minor"] == 50000
    assert lebensmittel_gruppe["total_remaining_minor"] == 10000

    wohnen = _group(summary, GROUP_HOUSING)
    assert wohnen["total_planned_minor"] == 120000
    assert wohnen["total_spent_minor"] == 120000
    assert wohnen["total_remaining_minor"] == 0

    wuensche = _group(summary, GROUP_LIFESTYLE)
    assert wuensche["total_planned_minor"] == 25000
    assert wuensche["total_spent_minor"] == 30000
    assert wuensche["total_remaining_minor"] == -5000

    _assert_totals_consistent(summary)


def test_case_b_refund_on_expense_category_is_not_income(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """(b) Erstattung auf einer Ausgabenkategorie.

    Von Hand: -50.000 + 12.000 = -38.000 Cent Ist auf Lebensmittel, also
    38.000 Cent Verbrauch. Die 12.000 Cent sind KEINE Einnahme - sonst
    stuenden 112.000 Cent Einnahmen und die Sparquote waere zu hoch.
    Sparquote = (100.000 - 38.000) / 100.000 = 62,0 %.
    """
    _book(household_headers, account_id, "2026-04-01", 100000, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-04-03", -50000, categories[CAT_GROCERIES])
    _book(household_headers, account_id, "2026-04-18", 12000, categories[CAT_GROCERIES])

    _allocate(
        household_headers,
        2026,
        4,
        [{"category_id": categories[CAT_GROCERIES], "planned_minor": 40000}],
    )
    summary = _summary(household_headers, 2026, 4)

    assert summary["total_income_minor"] == 100000
    assert summary["total_spent_minor"] == 38000
    assert summary["total_planned_minor"] == 40000
    assert summary["total_remaining_minor"] == 2000
    assert summary["savings_rate_percent"] == 62.0
    assert summary["bucket_needs_minor"] == 38000
    assert summary["bucket_needs_percent"] == 100.0

    lebensmittel = _cat(summary, CAT_GROCERIES)
    assert lebensmittel["spent_minor"] == 38000
    assert lebensmittel["income_actual_minor"] == 0
    assert lebensmittel["remaining_minor"] == 2000
    assert lebensmittel["percent_used"] == 95.0

    _assert_totals_consistent(summary)


def test_case_c_rollover_positive_and_negative(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """(c) Uebertrag in beide Richtungen.

    Mai: Lebensmittel Plan 50.000, Ist 20.000 -> Rest +30.000.
         Freizeit Plan 10.000, Ist 25.000 -> Rest -15.000.
    Nach dem Abschluss stehen beide Restbetraege als Uebertrag im Juni.
    Juni (Plan wie im Mai, kein Ist):
         Lebensmittel Rest = 50.000 + 30.000 - 0 = 80.000
         Freizeit     Rest = 10.000 - 15.000 - 0 = -5.000
         Gesamt Rest  = 60.000 + 15.000 - 0 = 75.000
    """
    _allocate(
        household_headers,
        2026,
        5,
        [
            {
                "category_id": categories[CAT_GROCERIES],
                "planned_minor": 50000,
                "rollover_rule": "accumulate",
            },
            {
                "category_id": categories[CAT_CULTURE],
                "planned_minor": 10000,
                "rollover_rule": "accumulate",
            },
        ],
    )
    _book(household_headers, account_id, "2026-05-04", -20000, categories[CAT_GROCERIES])
    _book(household_headers, account_id, "2026-05-11", -25000, categories[CAT_CULTURE])

    may = _summary(household_headers, 2026, 5)
    assert _cat(may, CAT_GROCERIES)["remaining_minor"] == 30000
    assert _cat(may, CAT_CULTURE)["remaining_minor"] == -15000
    assert may["total_remaining_minor"] == 15000
    _assert_totals_consistent(may)

    close_res = client.post("/v1/budgets/2026/5/close", headers=household_headers)
    assert close_res.status_code == 200, close_res.text
    assert close_res.json()["rollovers_created"] == 2

    _allocate(
        household_headers,
        2026,
        6,
        [
            {"category_id": categories[CAT_GROCERIES], "planned_minor": 50000},
            {"category_id": categories[CAT_CULTURE], "planned_minor": 10000},
        ],
    )
    june = _summary(household_headers, 2026, 6)

    lebensmittel = _cat(june, CAT_GROCERIES)
    assert lebensmittel["rollover_minor"] == 30000
    assert lebensmittel["remaining_minor"] == 80000

    freizeit = _cat(june, CAT_CULTURE)
    assert freizeit["rollover_minor"] == -15000
    assert freizeit["remaining_minor"] == -5000

    # Je Gruppe ist es die einzige gebuchte Kategorie, die Gruppensumme
    # entspricht deshalb der Kategoriesumme.
    assert _group(june, GROUP_FOOD)["total_rollover_minor"] == 30000
    assert _group(june, GROUP_FOOD)["total_remaining_minor"] == 80000
    assert _group(june, GROUP_LIFESTYLE)["total_rollover_minor"] == -15000
    assert _group(june, GROUP_LIFESTYLE)["total_remaining_minor"] == -5000

    assert june["total_planned_minor"] == 60000
    assert june["total_rollover_minor"] == 15000
    assert june["total_spent_minor"] == 0
    assert june["total_remaining_minor"] == 75000
    _assert_totals_consistent(june)


def test_case_d_uncategorized_positive_split_counts_as_income(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """(d) Unkategorisierte Splits.

    Ein positiver Zufluss ohne Kategorie ist eine Einnahme (25.000 Cent). Ein
    negativer Split ohne Kategorie gehoert zu keinem Budgettopf (keiner
    Gruppe, keinem 50/30/20-Bucket) - er zaehlt trotzdem als Ausgabe auf
    Gesamtebene, sonst wuerde eine unkategorisierte Importausgabe den
    Budgetrest kuenstlich erhoehen (docs/MOBILE-APP-REDESIGN-PLAN.md,
    Abschnitt 2 und 6). Er landet zusaetzlich noch in der Review-Inbox.
    Ist-Ausgaben gesamt: 10.000 (Lebensmittel) + 8.000 (unkategorisiert) =
    18.000 Cent. Sparquote = (25.000 - 18.000) / 25.000 = 28,0 %.
    """
    _book(household_headers, account_id, "2026-07-02", 25000, None)
    _book(household_headers, account_id, "2026-07-09", -8000, None)
    _book(household_headers, account_id, "2026-07-15", -10000, categories[CAT_GROCERIES])

    summary = _summary(household_headers, 2026, 7)

    assert summary["total_income_minor"] == 25000
    assert summary["uncategorized_spent_minor"] == 8000
    assert summary["total_spent_minor"] == 18000
    assert summary["savings_rate_percent"] == 28.0

    # Die Kategoriesumme (Gruppen, Buckets) sieht die 8.000 Cent nicht - nur
    # die Lebensmittel-Kategorie steckt dort drin.
    lebensmittel_gruppe = _group(summary, GROUP_FOOD)
    assert lebensmittel_gruppe["total_spent_minor"] == 10000
    assert summary["bucket_needs_minor"] == 10000
    assert summary["bucket_classified_spent_minor"] == 10000

    _assert_totals_consistent(summary)


def test_case_f_plan_rollover_categorized_and_uncategorized_together(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """(f) Budgetinvariante mit allen vier Bestandteilen gleichzeitig: Plan,
    Uebertrag, kategorisierte UND unkategorisierte Importausgaben.

    August: Plan 30.000, Ist 10.000 (kategorisiert) -> Rest 20.000, das nach
    Abschluss als Uebertrag in den September wandert.
    September: Plan 40.000 + Uebertrag 20.000, dazu 15.000 Cent kategorisierte
    und 7.000 Cent unkategorisierte Ausgaben.
    Rest = 40.000 + 20.000 - (15.000 + 7.000) = 38.000 Cent.
    """
    _allocate(
        household_headers,
        2026,
        8,
        [
            {
                "category_id": categories[CAT_GROCERIES],
                "planned_minor": 30000,
                "rollover_rule": "accumulate",
            }
        ],
    )
    _book(household_headers, account_id, "2026-08-05", -10000, categories[CAT_GROCERIES])

    august = _summary(household_headers, 2026, 8)
    assert _cat(august, CAT_GROCERIES)["remaining_minor"] == 20000
    assert august["uncategorized_spent_minor"] == 0
    _assert_totals_consistent(august)

    close_res = client.post("/v1/budgets/2026/8/close", headers=household_headers)
    assert close_res.status_code == 200, close_res.text

    _allocate(
        household_headers,
        2026,
        9,
        [{"category_id": categories[CAT_GROCERIES], "planned_minor": 40000}],
    )
    _book(household_headers, account_id, "2026-09-03", -15000, categories[CAT_GROCERIES])
    _book(household_headers, account_id, "2026-09-10", -7000, None)

    september = _summary(household_headers, 2026, 9)
    lebensmittel = _cat(september, CAT_GROCERIES)
    assert lebensmittel["rollover_minor"] == 20000
    assert lebensmittel["spent_minor"] == 15000
    assert lebensmittel["remaining_minor"] == 40000 + 20000 - 15000

    assert september["total_planned_minor"] == 40000
    assert september["total_rollover_minor"] == 20000
    assert september["uncategorized_spent_minor"] == 7000
    assert september["total_spent_minor"] == 15000 + 7000
    assert september["total_remaining_minor"] == 40000 + 20000 - (15000 + 7000)
    _assert_totals_consistent(september)


def test_case_e_overspend(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """(e) Ueberschreitung: Plan 20.000, Ist 35.000 -> Rest -15.000, 175 %."""
    _book(household_headers, account_id, "2026-02-02", 100000, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-02-06", -35000, categories[CAT_GROCERIES])

    _allocate(
        household_headers,
        2026,
        2,
        [{"category_id": categories[CAT_GROCERIES], "planned_minor": 20000}],
    )
    summary = _summary(household_headers, 2026, 2)

    lebensmittel = _cat(summary, CAT_GROCERIES)
    assert lebensmittel["spent_minor"] == 35000
    assert lebensmittel["remaining_minor"] == -15000
    assert lebensmittel["percent_used"] == 175.0

    assert _group(summary, GROUP_FOOD)["total_remaining_minor"] == -15000
    assert summary["total_planned_minor"] == 20000
    assert summary["total_spent_minor"] == 35000
    assert summary["total_remaining_minor"] == -15000
    assert summary["savings_rate_percent"] == 65.0
    _assert_totals_consistent(summary)


def test_income_plan_is_visible_without_moving_the_budget(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """Der gemeldete Fehler: ein Planbetrag auf einer Einnahmekategorie wirkte
    sich nirgends aus.

    Er erscheint jetzt in total_planned_income_minor (Gruppe und Gesamt), darf
    aber keine einzige Ausgabengroesse bewegen. Waere er in
    total_planned_minor gelandet, stuenden 6.899 EUR mehr "Budgetrest" da -
    und ueber goal_service.fetch_free_monthly_minor wuerde die Kaufampel der
    Wunschliste jeden Wunsch gruen faerben.
    """
    _enable_in_budget(household_headers, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-05", 300000, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-07", -50000, categories[CAT_GROCERIES])
    before = _allocate(
        household_headers,
        2026,
        3,
        [{"category_id": categories[CAT_GROCERIES], "planned_minor": 60000}],
    )
    assert before["total_planned_income_minor"] == 0

    after = _allocate(
        household_headers,
        2026,
        3,
        [{"category_id": categories[CAT_SALARY], "planned_minor": 689900}],
    )

    einnahmen = _group(after, GROUP_INCOME)
    assert einnahmen["total_planned_income_minor"] == 689900
    assert einnahmen["total_rollover_income_minor"] == 0
    # Noch ausstehend: 689.900 geplant, 300.000 eingegangen.
    assert einnahmen["total_income_remaining_minor"] == 389900
    # Die Gruppenzeile fuehrt weiterhin keinen Budgettopf.
    assert einnahmen["total_planned_minor"] == 0
    assert einnahmen["total_remaining_minor"] == 0

    assert after["total_planned_income_minor"] == 689900
    assert after["total_income_remaining_minor"] == 389900

    # Kein Ausgabenwert bewegt sich - das ist der eigentliche Vertrag dieses
    # Felds.
    unchanged = (
        "total_income_minor",
        "total_planned_minor",
        "total_rollover_minor",
        "total_spent_minor",
        "total_remaining_minor",
        "savings_rate_percent",
        "bucket_needs_minor",
        "bucket_wants_minor",
        "bucket_savings_minor",
        "bucket_needs_planned_minor",
        "bucket_wants_planned_minor",
        "bucket_savings_planned_minor",
        "bucket_unassigned_planned_minor",
        "bucket_unassigned_spent_minor",
    )
    for field in unchanged:
        assert after[field] == before[field], field
    assert after["total_planned_minor"] == 60000
    assert after["total_remaining_minor"] == 10000

    _assert_totals_consistent(after)


def test_buckets_split_plan_and_actual_without_a_remainder(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """Die vier 50/30/20-Toepfe teilen Plan und Ist restlos auf.

    Von Hand: needs 40.000 Plan / 30.000 Ist (Lebensmittel), wants 20.000 /
    25.000 (Kultur), savings 0 / 0, ohne Bucket 15.000 / 12.000 (eigens
    angelegte Kategorie). Dazu 7.000 Cent unkategorisiert - die gehoeren zu
    keinem der vier Toepfe und werden nur auf Gesamtebene addiert.
    """
    catalog = client.get("/v1/categories", headers=household_headers).json()
    housing_group = next(g for g in catalog if g["name"] == GROUP_HOUSING)
    created = client.post(
        "/v1/categories",
        json={
            "group_id": housing_group["id"],
            "name": "Ohne Bucket",
            "default_type": "expense",
        },
        headers=household_headers,
    )
    assert created.status_code == 201, created.text
    unassigned_id = str(created.json()["id"])
    assert created.json()["budget_bucket"] is None

    _book(household_headers, account_id, "2026-10-04", -30000, categories[CAT_GROCERIES])
    _book(household_headers, account_id, "2026-10-06", -25000, categories[CAT_CULTURE])
    _book(household_headers, account_id, "2026-10-08", -12000, unassigned_id)
    _book(household_headers, account_id, "2026-10-11", -7000, None)

    summary = _allocate(
        household_headers,
        2026,
        10,
        [
            {"category_id": categories[CAT_GROCERIES], "planned_minor": 40000},
            {"category_id": categories[CAT_CULTURE], "planned_minor": 20000},
            {"category_id": unassigned_id, "planned_minor": 15000},
        ],
    )

    assert summary["bucket_needs_planned_minor"] == 40000
    assert summary["bucket_wants_planned_minor"] == 20000
    assert summary["bucket_savings_planned_minor"] == 0
    assert summary["bucket_unassigned_planned_minor"] == 15000
    assert summary["total_planned_minor"] == 75000

    assert summary["bucket_needs_minor"] == 30000
    assert summary["bucket_wants_minor"] == 25000
    assert summary["bucket_savings_minor"] == 0
    assert summary["bucket_unassigned_spent_minor"] == 12000
    # Unkategorisiertes ist eine eigene Groesse und faellt ausdruecklich NICHT
    # in den Topf "ohne Bucket": dort stehen Kategorien, hier steht gar keine.
    assert summary["uncategorized_spent_minor"] == 7000
    assert summary["bucket_classified_spent_minor"] == 55000
    assert summary["total_spent_minor"] == 74000

    _assert_totals_consistent(summary)


def test_annual_matrix_keeps_income_out_of_spent(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """Die Jahresmatrix erbt die Trennung: das Gehalt steht in
    income_actual_minor, nicht in spent_minor."""
    _enable_in_budget(household_headers, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-05", 300000, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-07", -50000, categories[CAT_GROCERIES])

    matrix = client.get("/v1/budgets/2026/matrix", headers=household_headers).json()
    rows = {row["category_name"]: row for row in matrix["rows"]}
    for name in (CAT_SALARY, CAT_GROCERIES):
        assert name in rows, f"Kategorie '{name}' fehlt in der Jahresmatrix"

    gehalt = rows[CAT_SALARY]
    assert gehalt["total_spent_minor"] == 0
    assert gehalt["total_income_actual_minor"] == 300000
    assert gehalt["months"][2]["income_actual_minor"] == 300000
    assert gehalt["months"][2]["spent_minor"] == 0

    lebensmittel = rows[CAT_GROCERIES]
    assert lebensmittel["total_spent_minor"] == 50000
    assert lebensmittel["total_income_actual_minor"] == 0
    assert lebensmittel["months"][2]["spent_minor"] == 50000


def test_annual_matrix_carries_group_identity_and_sort_order(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """Die Matrix liefert die Gruppenzugehoerigkeit mit.

    Ohne group_id/sort_order muesste die Oberflaeche die Zeilen ueber den
    Gruppennamen zusammenfassen und alphabetisch sortieren - dann stuenden
    Jahresmatrix und Monatsbudget in verschiedener Reihenfolge. Die Zeilen
    kommen bereits sortiert; die Ordnungszahlen belegen das nachpruefbar.
    """
    _enable_in_budget(household_headers, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-05", 300000, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-03-07", -50000, categories[CAT_GROCERIES])

    matrix = client.get("/v1/budgets/2026/matrix", headers=household_headers).json()
    rows = matrix["rows"]
    assert rows

    order = [(row["group_sort_order"], row["category_sort_order"]) for row in rows]
    assert order == sorted(order)

    # Dieselbe group_id wie im Monatsbudget - sonst waeren es zwei
    # Gruppenbegriffe unter einem Namen.
    summary = _summary(household_headers, 2026, 3)
    summary_groups = summary["groups"]
    assert isinstance(summary_groups, list)
    group_ids = {str(g["group_id"]): str(g["group_name"]) for g in summary_groups}
    matrix_rows = {row["category_name"]: row for row in rows}
    for name in (CAT_SALARY, CAT_GROCERIES):
        row = matrix_rows[name]
        assert str(row["group_id"]) in group_ids
        assert group_ids[str(row["group_id"])] == row["group_name"]


def _all_category_names(summary: Json) -> set[str]:
    groups = summary["groups"]
    assert isinstance(groups, list)
    return {str(cat["category_name"]) for grp in groups for cat in grp["categories"]}


def test_transfer_categories_never_appear_in_the_budget(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """Eine interne Umbuchung ist keine Ausgabe.

    Die drei Transferkategorien des Standardpakets stehen weder im
    Monatsbudget noch in der Jahresmatrix - auch dann nicht, wenn auf ihnen
    gebucht wurde. Ihr Betrag darf entsprechend auch nicht im Verbrauch
    landen, sonst waere die Sparquote falsch."""
    groups = client.get("/v1/categories", headers=household_headers).json()
    transfer_names = {
        c["name"] for g in groups for c in g["categories"] if c["default_type"] == "transfer"
    }
    assert transfer_names, "Standardpaket ohne Transferkategorien"
    transfer_id = str(
        next(
            c["id"]
            for g in groups
            for c in g["categories"]
            if c["default_type"] == "transfer"
        )
    )

    _book(household_headers, account_id, "2026-09-04", -40000, transfer_id)
    _book(household_headers, account_id, "2026-09-06", -10000, categories[CAT_GROCERIES])

    summary = _summary(household_headers, 2026, 9)
    assert not (_all_category_names(summary) & transfer_names)
    # Nur die Lebensmittel zaehlen: die 40.000 Cent Umbuchung sind kein
    # Verbrauch.
    assert summary["total_spent_minor"] == 10000
    _assert_totals_consistent(summary)

    matrix = client.get("/v1/budgets/2026/matrix", headers=household_headers).json()
    assert not ({row["category_name"] for row in matrix["rows"]} & transfer_names)

    # Ein Planbetrag darauf waere unsichtbar - deshalb abgelehnt statt still
    # weggeschrieben.
    res = client.put(
        "/v1/budgets/2026/9/allocations",
        json={"allocations": [{"category_id": transfer_id, "planned_minor": 5000}]},
        headers=household_headers,
    )
    assert res.status_code == 400, res.text
    assert "Transferkategorien" in res.json()["detail"]


def test_non_budgeted_category_is_hidden_until_it_gets_a_plan(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """is_budgeted steuert die Zeile, nicht die Einnahmesumme.

    Das Gehalt ist im Standardpaket nicht budgetiert: es taucht als Zeile
    nicht auf, zaehlt aber weiter in total_income_minor und in die Sparquote.
    Sobald ein Planbetrag darauf liegt, ist die Zeile wieder da - sonst
    liesse sich ein versehentlich gesetzter Plan nicht mehr zuruecknehmen."""
    _book(household_headers, account_id, "2026-11-03", 200000, categories[CAT_SALARY])
    _book(household_headers, account_id, "2026-11-08", -50000, categories[CAT_GROCERIES])

    summary = _summary(household_headers, 2026, 11)
    assert CAT_SALARY not in _all_category_names(summary)
    # Die Gruppe "Einnahmen" faellt gleich mit weg: eine Gruppenzeile aus
    # lauter Nullen ist keine Information.
    summary_groups = summary["groups"]
    assert isinstance(summary_groups, list)
    assert GROUP_INCOME not in {str(g["group_name"]) for g in summary_groups}
    assert summary["total_income_minor"] == 200000
    assert summary["total_spent_minor"] == 50000
    assert summary["savings_rate_percent"] == 75.0
    _assert_totals_consistent(summary)

    _allocate(
        household_headers,
        2026,
        11,
        [{"category_id": categories[CAT_SALARY], "planned_minor": 200000}],
    )
    with_plan = _summary(household_headers, 2026, 11)
    gehalt = _cat(with_plan, CAT_SALARY)
    assert gehalt["income_actual_minor"] == 200000
    assert gehalt["spent_minor"] == 0
    # Eine geplante Einnahme ist kein Budgettopf: der Plan bleibt aus
    # total_planned_minor heraus - sichtbar wird er in der eigenen
    # Einnahmesumme (siehe test_income_plan_is_visible_without_moving_the_budget).
    assert with_plan["total_planned_minor"] == 0
    assert with_plan["total_planned_income_minor"] == 200000
    assert with_plan["total_income_remaining_minor"] == 0
    _assert_totals_consistent(with_plan)


def test_overview_report_separates_income_and_expense(
    household_headers: Headers, account_id: str, categories: dict[str, str]
) -> None:
    """Der Uebersichtsbericht liest die Budgetzusammenfassung des laufenden
    Monats - die Trennung muss bis dorthin durchschlagen."""
    today = date.today()
    booked = today.replace(day=1).isoformat()
    _book(household_headers, account_id, booked, 300000, categories[CAT_SALARY])
    _book(household_headers, account_id, booked, -200000, categories[CAT_RENT])

    overview = client.get("/v1/reports/overview", headers=household_headers).json()
    assert overview["total_income_minor"] == 300000
    assert overview["total_expense_minor"] == 200000
    assert overview["net_savings_minor"] == 100000
    assert overview["savings_rate_percent"] == 33.3

    # Das Gehalt darf nicht als Top-Ausgabenkategorie auftauchen.
    top_names = [c["category_name"] for c in overview["top_expense_categories"]]
    assert CAT_SALARY not in top_names
    assert top_names[0] == CAT_RENT

    current = overview["cashflow_last_6_months"][-1]
    assert current["income_minor"] == 300000
    assert current["expense_minor"] == 200000
    assert current["net_minor"] == 100000
