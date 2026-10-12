"""Analyse-Abfragevertrag (docs/ANALYSE-EXPLORER-PLAN.md, Baustein B).

Drei Sorten Test, entsprechend den drei Zusagen des Bausteins:

1. Der Kompiler ist eine reine Funktion - Feld-/Aggregat-/Operatorpruefung und
   die Injektionsfestigkeit laufen ohne Datenbank.
2. Aggregat-Gleichstand: analytics_service und get_budget_summary liefern fuer
   denselben Zeitraum dieselben Summen. Das ist ein Dauertest, keine einmalige
   Pruefung - zwei Aggregationspfade, die auseinanderlaufen, sind der Grund,
   warum "Diagramm und Tabelle widersprechen sich".
3. Invarianten: die Drilldown-Summe entspricht dem Aggregat der Ebene darueber,
   und top_n mit Restzeile aendert die Gesamtsumme nicht.
"""

import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.analytics import (
    AnalyticsQuery,
    Condition,
    DimensionRef,
    FilterNode,
    MeasureRef,
    PeriodSpec,
)
from app.services import analytics_service
from app.services.analytics_service import QueryCompilationError, compile_query
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

HOUSEHOLD = uuid.uuid4()
TODAY = date(2026, 7, 15)

GROCERY = "Lebensmittel"
RESTAURANT = "Restaurant"
SALARY = "Gehalt & Lohn"
# Standardpaket v1, Gruppe "Transfers": default_type='transfer'.
TRANSFER_SAVING = "Sparen, Investieren & Tilgung"


# ---------------------------------------------------------------------------
# 1. Kompiler - ohne Datenbank
# ---------------------------------------------------------------------------


def _sum_amount(**kwargs: Any) -> AnalyticsQuery:
    return AnalyticsQuery(measures=[MeasureRef(field="amount", aggregate="sum")], **kwargs)


def test_unknown_field_is_rejected_with_code() -> None:
    query = _sum_amount(group_by=[DimensionRef(field="gibt_es_nicht")])
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, TODAY)
    assert [i.code for i in exc.value.issues] == ["field_unknown"]


def test_all_issues_are_reported_at_once() -> None:
    """Die Oberflaeche soll alle Fehler auf einmal zeigen koennen, statt sie
    nacheinander zu entdecken."""
    query = AnalyticsQuery(
        group_by=[DimensionRef(field="nope"), DimensionRef(field="auch_nicht")],
        measures=[MeasureRef(field="amount", aggregate="sum")],
    )
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, TODAY)
    assert len(exc.value.issues) == 2


def test_non_groupable_field_is_rejected() -> None:
    query = _sum_amount(group_by=[DimensionRef(field="purpose")])
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, TODAY)
    assert exc.value.issues[0].code == "field_not_groupable"


def test_aggregate_must_be_allowed_for_field() -> None:
    query = AnalyticsQuery(measures=[MeasureRef(field="payee", aggregate="median")])
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, TODAY)
    assert exc.value.issues[0].code == "aggregate_not_allowed"


def test_granularity_only_on_date_fields() -> None:
    query = _sum_amount(group_by=[DimensionRef(field="category", granularity="month")])
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, TODAY)
    assert exc.value.issues[0].code == "granularity_not_allowed"


def test_order_key_must_belong_to_the_query() -> None:
    query = AnalyticsQuery(
        group_by=[DimensionRef(field="category")],
        measures=[MeasureRef(field="amount", aggregate="sum")],
        order_by=[{"key": "sum_gibtsnicht", "direction": "desc"}],  # type: ignore[list-item]
    )
    with pytest.raises(QueryCompilationError) as exc:
        compile_query(query, HOUSEHOLD, TODAY)
    assert exc.value.issues[0].code == "order_key_unknown"
    # Die Meldung nennt die gueltigen Schluessel - danach korrigiert sich das
    # KI-Feld (sonst wiederholt ein Modell "date" statt "date:month").
    assert "Gültige Schlüssel: category" in exc.value.issues[0].message


@pytest.mark.parametrize(
    "payload",
    [
        "x'; drop table transactions; --",
        "%' or '1'='1",
        "\\'; delete from accounts; --",
    ],
)
def test_filter_values_never_reach_the_statement(payload: str) -> None:
    """Der einzige Weg fuer einen Wert in das Statement ist ein gebundener
    Parameter. Ein Angriffsversuch landet als Zeichenkette im Parameter, nicht
    im SQL-Text."""
    query = _sum_amount(
        where=FilterNode(condition=Condition(field="payee", op="contains", value=payload))
    )
    compiled = compile_query(query, HOUSEHOLD, TODAY)
    assert payload not in compiled.sql
    assert "drop table" not in compiled.sql.lower()
    assert "delete from" not in compiled.sql.lower()
    assert any(payload in str(v) for v in compiled.params.values())


def test_search_text_never_reaches_the_statement() -> None:
    compiled = compile_query(_sum_amount(search="'; drop table t; --"), HOUSEHOLD, TODAY)
    assert "drop table" not in compiled.sql.lower()


def test_like_wildcards_in_search_are_escaped() -> None:
    """Ein Prozentzeichen im Suchtext ist ein Zeichen, kein Platzhalter -
    sonst liefert die Suche nach '100%' die gesamte Tabelle."""
    compiled = compile_query(_sum_amount(search="100%"), HOUSEHOLD, TODAY)
    assert any(r"100\%" in str(v) for v in compiled.params.values())


def test_transfers_are_excluded_by_default() -> None:
    """Interne Umbuchungen verzerren jede Auswertung."""
    assert "not t.is_transfer" in compile_query(_sum_amount(), HOUSEHOLD, TODAY).sql
    with_transfers = compile_query(_sum_amount(include_transfers=True), HOUSEHOLD, TODAY)
    assert "not t.is_transfer" not in with_transfers.sql


def test_archived_categories_stay_in_history_by_default() -> None:
    """Eine historische Zuordnung auf eine archivierte Kategorie darf nicht
    aus der Auswertung fallen."""
    assert "c.is_archived" not in compile_query(_sum_amount(), HOUSEHOLD, TODAY).sql


def test_row_order_has_a_unique_tiebreaker() -> None:
    """Ohne eindeutigen zweiten Sortierschluessel waere die
    Keyset-Paginierung nicht stabil."""
    compiled = compile_query(
        AnalyticsQuery(grain="rows", fields=["date", "amount"]), HOUSEHOLD, TODAY
    )
    assert "s.id" in compiled.sql.split("order by")[1]


def test_period_last_n_months_includes_current_month() -> None:
    query = _sum_amount(period=PeriodSpec(kind="last_n_months", n=3))
    start, end = analytics_service.resolve_period(query, date(2026, 7, 15))
    assert start == date(2026, 5, 1)
    assert end == date(2026, 7, 31)


def test_period_year_spans_the_whole_year() -> None:
    query = _sum_amount(period=PeriodSpec(kind="year", year=2025))
    start, end = analytics_service.resolve_period(query, TODAY)
    assert (start, end) == (date(2025, 1, 1), date(2025, 12, 31))


def test_query_id_is_stable_and_scoped() -> None:
    """Der Hash ist Cache-Schluessel und Beleg in KI-Antworten - er muss
    reproduzierbar und haushaltsgebunden sein."""
    a = analytics_service.compute_query_id(_sum_amount(), HOUSEHOLD)
    b = analytics_service.compute_query_id(_sum_amount(), HOUSEHOLD)
    c = analytics_service.compute_query_id(_sum_amount(), uuid.uuid4())
    assert a == b
    assert a != c


def test_field_catalog_covers_the_default_row_fields() -> None:
    catalog = analytics_service.get_field_catalog()
    keys = {f.key for f in catalog.fields}
    assert set(catalog.default_row_fields) <= keys


# ---------------------------------------------------------------------------
# 2./3. Gegen die Datenbank
# ---------------------------------------------------------------------------


@pytest.fixture()
def headers() -> dict[str, str]:
    token = _signup(f"anly-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households",
        json={"name": "Analyse-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(headers: dict[str, str]) -> str:
    return str(
        client.post(
            "/v1/accounts",
            json={"name": "Girokonto", "account_type": "checking"},
            headers=headers,
        ).json()["id"]
    )


def _category_id(headers: dict[str, str], name: str) -> str:
    for group in client.get("/v1/categories", headers=headers).json():
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{name}' nicht gefunden")


def _set_unbudgeted(headers: dict[str, str], category_id: str) -> None:
    """Schalter "Im Budget anzeigen" aus - PATCH verlangt die aktuelle Version."""
    catalog = client.get("/v1/categories/catalog", headers=headers).json()
    version = next(
        category["version"]
        for group in catalog["groups"]
        for category in group["categories"]
        if str(category["id"]) == category_id
    )
    response = client.patch(
        f"/v1/categories/{category_id}",
        json={"is_budgeted": False},
        headers={**headers, "If-Match": f'"{version}"'},
    )
    assert response.status_code == 200, response.text


def _tx(
    headers: dict[str, str],
    account_id: str,
    booked_date: str,
    amount_minor: int,
    category_id: str | None = None,
    payee: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": account_id,
        "booked_date": booked_date,
        "amount_minor": amount_minor,
    }
    if category_id:
        payload["category_id"] = category_id
    if payee:
        payload["payee_label"] = payee
    response = client.post("/v1/transactions", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.fixture()
def seeded(headers: dict[str, str], account_id: str) -> dict[str, str]:
    """Ein kleiner, vollstaendig bekannter Datenbestand im Juni 2026."""
    grocery = _category_id(headers, GROCERY)
    restaurant = _category_id(headers, RESTAURANT)
    salary = _category_id(headers, SALARY)

    _tx(headers, account_id, "2026-06-02", -4250, grocery, "REWE")
    _tx(headers, account_id, "2026-06-09", -3100, grocery, "Aldi")
    _tx(headers, account_id, "2026-06-16", -1200, restaurant, "Pizzeria")
    _tx(headers, account_id, "2026-06-25", 250000, salary, "Arbeitgeber")
    # Ohne Kategorie - taucht in Kategorieauswertungen als "leer" auf.
    _tx(headers, account_id, "2026-06-27", -999, None, "Unbekannt")
    return {"grocery": grocery, "restaurant": restaurant, "salary": salary}


def _run(headers: dict[str, str], query: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/v1/analytics", json=query, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_group_by_category_sums_correctly(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    result = _run(
        headers,
        {
            "grain": "aggregate",
            "group_by": [{"field": "category"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
            "period": {"kind": "year", "year": 2026},
        },
    )
    by_category = {row["category"]: row["sum_amount"] for row in result["rows"]}
    assert by_category[seeded["grocery"]] == -7350
    assert by_category[seeded["restaurant"]] == -1200
    assert by_category[seeded["salary"]] == 250000
    # Der unkategorisierte Split verschwindet nicht, er gruppiert unter NULL.
    assert by_category[None] == -999


def test_labels_are_resolved_for_id_dimensions(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    """Eine Gruppierung nach Kategorie liefert den Namen mit - sonst muesste
    jeder Client einen zweiten Abruf machen, nur um die Achse zu beschriften."""
    result = _run(
        headers,
        {
            "grain": "aggregate",
            "group_by": [{"field": "category"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
            "period": {"kind": "year", "year": 2026},
        },
    )
    labels = {row["category"]: row.get("category_label") for row in result["rows"]}
    assert labels[seeded["grocery"]] == GROCERY


def test_total_row_matches_sum_of_rows(headers: dict[str, str], seeded: dict[str, str]) -> None:
    """Die Invariante, auf der der Drilldown beruht: die Summe der
    Detailebene entspricht dem Aggregat darueber."""
    result = _run(
        headers,
        {
            "grain": "aggregate",
            "group_by": [{"field": "category"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
            "period": {"kind": "year", "year": 2026},
        },
    )
    assert sum(row["sum_amount"] for row in result["rows"]) == result["total_row"]["sum_amount"]


def test_top_n_with_rest_keeps_the_total(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    """Ein Diagramm mit "Top N + Uebrige" hat dieselbe Gesamtsumme wie die
    zugehoerige Tabelle - sonst widersprechen sich beide."""
    result = _run(
        headers,
        {
            "grain": "aggregate",
            "group_by": [{"field": "category"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
            "period": {"kind": "year", "year": 2026},
            "top_n": {"limit": 2, "include_rest": True},
        },
    )
    assert result["truncated"] is True
    assert [limit["kind"] for limit in result["applied_limits"]] == ["top_n"]
    assert sum(row["sum_amount"] for row in result["rows"]) == result["total_row"]["sum_amount"]
    assert result["rows"][-1]["category_label"] == "Übrige"


def test_monthly_grouping_and_avg_per_month(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    result = _run(
        headers,
        {
            "grain": "aggregate",
            "group_by": [{"field": "date", "granularity": "month"}],
            "measures": [
                {"field": "amount", "aggregate": "sum"},
                {"field": "amount", "aggregate": "avg_per_month"},
            ],
            "period": {"kind": "year", "year": 2026},
        },
    )
    june = next(r for r in result["rows"] if str(r["date:month"]).startswith("2026-06"))
    assert june["sum_amount"] == 250000 - 4250 - 3100 - 1200 - 999
    assert result["months_covered"] == 12


def test_search_covers_payee_and_purpose(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    result = _run(
        headers,
        {
            "grain": "rows",
            "search": "rewe",
            "period": {"kind": "year", "year": 2026},
        },
    )
    assert result["row_count"] == 1
    assert result["rows"][0]["payee"] == "REWE"


def test_row_paging_is_stable_and_reports_the_limit(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    """Die Grenze wird ausgewiesen, statt stillschweigend zu greifen - genau
    der Fehler, den das feste limit:200 im Buchungsregister macht."""
    first = _run(
        headers,
        {"grain": "rows", "limit": 2, "period": {"kind": "year", "year": 2026}},
    )
    assert first["row_count"] == 2
    assert first["truncated"] is True
    assert first["applied_limits"][0]["kind"] == "row_limit"
    assert first["next_cursor"]

    second = _run(
        headers,
        {
            "grain": "rows",
            "limit": 2,
            "cursor": first["next_cursor"],
            "period": {"kind": "year", "year": 2026},
        },
    )
    first_ids = {row["split_id"] for row in first["rows"]}
    second_ids = {row["split_id"] for row in second["rows"]}
    assert not (first_ids & second_ids)


def test_facets_respect_other_filters_but_not_themselves(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    """Das Excel-Verhalten: die Werteliste einer Spalte beruecksichtigt die
    uebrigen Filter, aber nicht den eigenen."""
    response = client.post(
        "/v1/analytics/facets",
        json={
            "field": "category",
            "query": {
                "grain": "aggregate",
                "measures": [{"field": "amount", "aggregate": "sum"}],
                "period": {"kind": "year", "year": 2026},
                "where": {
                    "condition": {
                        "field": "category",
                        "op": "in",
                        "value": [seeded["grocery"]],
                    }
                },
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    values = {v["value"] for v in response.json()["values"]}
    # Trotz des Kategoriefilters erscheinen alle Kategorien - sonst koennte
    # man den Filter nie erweitern.
    assert seeded["restaurant"] in values
    assert seeded["salary"] in values


def test_validate_reports_issues_without_touching_the_database(
    headers: dict[str, str],
) -> None:
    response = client.post(
        "/v1/analytics/validate",
        json={
            "grain": "aggregate",
            "group_by": [{"field": "gibt_es_nicht"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["issues"][0]["code"] == "field_unknown"


def test_invalid_query_is_rejected_with_structured_issues(
    headers: dict[str, str],
) -> None:
    response = client.post(
        "/v1/analytics",
        json={
            "grain": "aggregate",
            "group_by": [{"field": "gibt_es_nicht"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "analytics_query_invalid"


def test_analytics_is_household_scoped(headers: dict[str, str], seeded: dict[str, str]) -> None:
    """Ein fremder Haushalt sieht keine Zeile - RLS plus die explizite
    household_id-Bedingung im Kompiler."""
    other_token = _signup(
        f"anly-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    other_household = client.post(
        "/v1/households", json={"name": "Fremd"}, headers=_auth_headers(other_token)
    ).json()
    other_headers = {**_auth_headers(other_token), "X-Household-Id": other_household["id"]}

    result = _run(
        other_headers,
        {
            "grain": "aggregate",
            "group_by": [{"field": "category"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
            "period": {"kind": "year", "year": 2026},
        },
    )
    assert result["rows"] == []


def test_csv_export_is_excel_ready(headers: dict[str, str], seeded: dict[str, str]) -> None:
    response = client.post(
        "/v1/analytics/export",
        json={"grain": "rows", "period": {"kind": "year", "year": 2026}},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.text
    assert body.startswith("﻿")
    assert "sep=;" in body
    assert "REWE" in body


# ---------------------------------------------------------------------------
# Gleichstand mit den bestehenden Auswertungen
# ---------------------------------------------------------------------------


def test_cashflow_matches_budget_summary(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    """Der abgeloeste N+1-Pfad muss dieselben Zahlen liefern wie der
    Monatsabschluss - sonst hat der Umbau die Bedeutung veraendert."""
    trend = client.get("/v1/reports/cashflow-trend?months=24", headers=headers).json()
    june = next(
        (m for m in trend["months"] if m["year"] == 2026 and m["month"] == 6), None
    )
    assert june is not None

    summary = client.get("/v1/budgets/2026/6", headers=headers).json()
    assert june["income_minor"] == summary["total_income_minor"]
    assert june["expense_minor"] == summary["total_spent_minor"]


def test_cashflow_excludes_the_same_categories_as_the_budget(
    headers: dict[str, str], seeded: dict[str, str], account_id: str
) -> None:
    """Regressionstest: der ausgeschriebene Cashflow-Pfad rechnete zu viel mit.

    get_budget_summary nimmt Transferkategorien und nicht budgetierte
    Kategorien ausdruecklich aus der Ist-Summe - eine Depoteinzahlung ist keine
    Ausgabe, sondern Geld, das den Haushalt nur wechselt. Der Cashflow-Trend
    filterte dagegen nur 'income' heraus und zeigte fuer denselben Monat eine
    andere Ausgabensumme als die Budgetseite.
    """
    transfer = _category_id(headers, TRANSFER_SAVING)
    _tx(headers, account_id, "2026-06-05", -50000, transfer, "Depotbank")

    # Eine Ausgabenkategorie, die der Nutzer aus dem Budget genommen hat.
    unbudgeted = _category_id(headers, RESTAURANT)
    _set_unbudgeted(headers, unbudgeted)

    trend = client.get("/v1/reports/cashflow-trend?months=24", headers=headers).json()
    june = next(m for m in trend["months"] if m["year"] == 2026 and m["month"] == 6)
    summary = client.get("/v1/budgets/2026/6", headers=headers).json()

    assert june["expense_minor"] == summary["total_spent_minor"]
    # Weder die Umbuchung (500,00) noch die Pizzeria (12,00) sind darin. Der
    # unkategorisierte Split aus "seeded" (9,99, "Unbekannt") zaehlt dagegen
    # in beiden Berichten konsistent als Ausgabe mit.
    assert june["expense_minor"] == 4250 + 3100 + 999
    assert june["income_minor"] == summary["total_income_minor"]


def test_cashflow_drilldown_rows_sum_to_the_displayed_values(
    headers: dict[str, str], seeded: dict[str, str], account_id: str
) -> None:
    """Vorzeichen und Cashflow-Rolle sind nicht dasselbe.

    Eine Erstattung auf einer Ausgabenkategorie bleibt im Ausgaben-Drilldown;
    eine negative Gehaltskorrektur bleibt bei den Einnahmen. Ausgeschlossene
    Kategorien (Transfer, abgewaehlt aus dem Budget) gehoeren nicht in den
    Saldo-Drilldown - ein negativer unkategorisierter Split dagegen schon: er
    zaehlt als Ausgabe, deckungsgleich mit budget_service.uncategorized_spent_minor.
    """
    _tx(headers, account_id, "2026-06-26", 2000, seeded["grocery"], "Erstattung")
    _tx(headers, account_id, "2026-06-26", -10000, seeded["salary"], "Gehaltskorrektur")
    _tx(headers, account_id, "2026-06-26", 3000, None, "Unkategorisierter Zufluss")
    _tx(headers, account_id, "2026-06-26", -4000, None, "Unkategorisierte Belastung")

    transfer = _category_id(headers, TRANSFER_SAVING)
    _tx(headers, account_id, "2026-06-26", -50000, transfer, "Depot")

    restaurant = _category_id(headers, RESTAURANT)
    _set_unbudgeted(headers, restaurant)

    trend = client.get("/v1/reports/cashflow-trend?months=24", headers=headers).json()
    june = next(m for m in trend["months"] if m["year"] == 2026 and m["month"] == 6)

    def drilldown(role: str | list[str]) -> dict[str, Any]:
        return _run(
            headers,
            {
                "grain": "rows",
                "period": {
                    "kind": "custom",
                    "from_date": "2026-06-01",
                    "to_date": "2026-06-30",
                },
                "where": {
                    "condition": {
                        "field": "cashflow_role",
                        "op": "in" if isinstance(role, list) else "eq",
                        "value": role,
                    }
                },
                "only_on_budget": True,
                "limit": 200,
            },
        )

    income_rows = drilldown("income")["rows"]
    expense_rows = drilldown("expense")["rows"]
    net_rows = drilldown(["income", "expense"])["rows"]

    assert sum(row["amount"] for row in income_rows) == june["income_minor"]
    assert sum(row["amount"] for row in expense_rows) == -june["expense_minor"]
    assert sum(row["amount"] for row in net_rows) == june["net_minor"]
    assert {row["payee"] for row in income_rows} >= {
        "Arbeitgeber",
        "Gehaltskorrektur",
        "Unkategorisierter Zufluss",
    }
    assert "Erstattung" not in {row["payee"] for row in income_rows}
    assert "Erstattung" in {row["payee"] for row in expense_rows}
    # Negativer unkategorisierter Split: zaehlt als Ausgabe, nicht als
    # Einnahme, und ist damit auch im Saldo-Drilldown vertreten.
    assert "Unkategorisierte Belastung" in {row["payee"] for row in expense_rows}
    assert "Unkategorisierte Belastung" not in {row["payee"] for row in income_rows}
    assert "Unkategorisierte Belastung" in {row["payee"] for row in net_rows}
    assert "Depot" not in {row["payee"] for row in net_rows}


def test_a_planned_amount_keeps_an_unbudgeted_category_in_both_sums(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    """Die Gegenprobe: ein Planbetrag haelt die Zeile im Budget sichtbar - und
    damit auch im Cashflow."""
    restaurant = _category_id(headers, RESTAURANT)
    _set_unbudgeted(headers, restaurant)
    allocation = client.put(
        "/v1/budgets/2026/6/allocations",
        json={"allocations": [{"category_id": restaurant, "planned_minor": 5000}]},
        headers=headers,
    )
    assert allocation.status_code == 200, allocation.text

    trend = client.get("/v1/reports/cashflow-trend?months=24", headers=headers).json()
    june = next(m for m in trend["months"] if m["year"] == 2026 and m["month"] == 6)
    summary = client.get("/v1/budgets/2026/6", headers=headers).json()
    # Inklusive des unkategorisierten Splits aus "seeded" (9,99, "Unbekannt").
    assert june["expense_minor"] == summary["total_spent_minor"] == 4250 + 3100 + 1200 + 999


def test_annual_matrix_matches_budget_summary(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    matrix = client.get("/v1/budgets/2026/matrix", headers=headers).json()
    summary = client.get("/v1/budgets/2026/6", headers=headers).json()

    per_category_spent = {
        str(row["category_id"]): row["months"][5]["spent_minor"] for row in matrix["rows"]
    }
    for group in summary["groups"]:
        for category in group["categories"]:
            assert per_category_spent.get(str(category["category_id"]), 0) == (
                category["spent_minor"]
            )


def test_analytics_sum_matches_budget_summary_spent(
    headers: dict[str, str], seeded: dict[str, str]
) -> None:
    """Der neue Aggregationsdienst und der Budgetkern stimmen ueberein."""
    summary = client.get("/v1/budgets/2026/6", headers=headers).json()
    result = _run(
        headers,
        {
            "grain": "aggregate",
            "group_by": [{"field": "category"}],
            "measures": [{"field": "amount", "aggregate": "sum"}],
            "period": {"kind": "custom", "from_date": "2026-06-01", "to_date": "2026-06-30"},
            "only_on_budget": True,
        },
    )
    analytics_by_category = {row["category"]: row["sum_amount"] for row in result["rows"]}
    for group in summary["groups"]:
        for category in group["categories"]:
            if category["default_type"] == "income":
                continue
            expected = -category["spent_minor"]
            if expected == 0:
                continue
            assert analytics_by_category.get(str(category["category_id"])) == expected
