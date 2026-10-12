"""GET /v1/transactions/page (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5
"Daten/API"): additiver, cursorbasierter Paging-Endpunkt fuer die neue
mobile Buchungsliste. GET /v1/transactions bleibt unveraendert - siehe
test_transactions_phase2.py fuer dessen bestehende Suchtests.

Deckt laut Aufgabenstellung ab: Suche, kombinierte Filter, Split-Kategorie,
"Ohne Kategorie", jede Sortierung, Cursor ohne Duplikate/Luecken durch alle
Seiten, Haushaltsisolierung.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

_ENDPOINT = "/v1/transactions/page"


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"pg-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Paging-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_ids(household_headers: dict[str, str]) -> tuple[str, str]:
    a = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    b = client.post(
        "/v1/accounts",
        json={"name": "Sparkonto", "account_type": "savings"},
        headers=household_headers,
    ).json()
    return a["id"], b["id"]


def _category_id_by_name(headers: dict[str, str], name: str) -> str:
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{name}' nicht gefunden")


def _create_tx(
    headers: dict[str, str],
    account_id: str,
    *,
    booked_date: str,
    amount_minor: int,
    payee_label: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": account_id,
        "booked_date": booked_date,
        "amount_minor": amount_minor,
    }
    if payee_label is not None:
        payload["payee_label"] = payee_label
    if note is not None:
        payload["note"] = note
    response = client.post("/v1/transactions", json=payload, headers=headers)
    assert response.status_code == 201
    return dict(response.json())


def _set_splits(
    headers: dict[str, str], tx: dict[str, Any], splits: list[dict[str, Any]]
) -> dict[str, Any]:
    response = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={"amount_minor": tx["amount_minor"], "splits": splits},
        headers=headers,
    )
    assert response.status_code == 200
    return dict(response.json())


def _page(headers: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get(_ENDPOINT, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _collect_all_pages(
    headers: dict[str, str], params: dict[str, Any], page_limit: int
) -> list[str]:
    collected: list[str] = []
    cursor: str | None = None
    for _ in range(100):
        request_params = {**params, "limit": page_limit}
        if cursor is not None:
            request_params["cursor"] = cursor
        response = client.get(_ENDPOINT, params=request_params, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        collected.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return collected
    raise AssertionError("Zu viele Seiten gelesen - vermutlich ein Endlosloop im Cursor")


# ---------------------------------------------------------------------------
# Antwortformat: identisch zum bestehenden Listen-Endpunkt
# ---------------------------------------------------------------------------


def test_page_item_matches_existing_list_endpoint_format(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    created = _create_tx(
        household_headers,
        account_id,
        booked_date="2026-07-01",
        amount_minor=-1234,
        payee_label="Formatvergleich GmbH",
    )

    legacy = client.get(
        f"/v1/transactions?account_id={account_id}", headers=household_headers
    ).json()
    legacy_row = next(t for t in legacy if t["id"] == created["id"])

    page = _page(household_headers, account_id=account_id)
    paged_row = next(t for t in page["items"] if t["id"] == created["id"])

    assert paged_row == legacy_row


# ---------------------------------------------------------------------------
# Suche
# ---------------------------------------------------------------------------


def test_search_matches_payee_and_note_and_is_scoped(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    _create_tx(
        household_headers,
        account_id,
        booked_date="2026-07-01",
        amount_minor=-1200,
        payee_label="Nordlicht Baeckerei",
    )
    _create_tx(
        household_headers,
        account_id,
        booked_date="2026-07-02",
        amount_minor=-2400,
        payee_label="Suedwind Tankstelle",
        note="Quartalsabrechnung",
    )

    by_payee = _page(household_headers, search="nordlicht")
    assert [t["payee_label"] for t in by_payee["items"]] == ["Nordlicht Baeckerei"]

    by_note = _page(household_headers, search="quartals")
    assert [t["payee_label"] for t in by_note["items"]] == ["Suedwind Tankstelle"]

    empty = _page(household_headers, search="gibtesnicht")
    assert empty["items"] == []
    assert empty["next_cursor"] is None


# ---------------------------------------------------------------------------
# Kombinierte Filter
# ---------------------------------------------------------------------------


def test_combined_account_date_and_direction_filters(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    checking_id, savings_id = account_ids
    # Treffer: richtiges Konto, im Zeitraum, Ausgabe.
    hit = _create_tx(
        household_headers, checking_id, booked_date="2026-07-15", amount_minor=-500
    )
    # Falsches Konto.
    _create_tx(household_headers, savings_id, booked_date="2026-07-15", amount_minor=-500)
    # Ausserhalb des Zeitraums.
    _create_tx(household_headers, checking_id, booked_date="2026-06-30", amount_minor=-500)
    _create_tx(household_headers, checking_id, booked_date="2026-08-01", amount_minor=-500)
    # Richtiges Konto/Zeitraum, aber Einnahme statt Ausgabe.
    _create_tx(household_headers, checking_id, booked_date="2026-07-16", amount_minor=500)

    result = _page(
        household_headers,
        account_id=checking_id,
        from_date="2026-07-01",
        to_date="2026-07-31",
        direction="expense",
    )
    assert [t["id"] for t in result["items"]] == [hit["id"]]


def test_direction_income_only_returns_positive_amounts(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    income = _create_tx(household_headers, account_id, booked_date="2026-07-01", amount_minor=3000)
    _create_tx(household_headers, account_id, booked_date="2026-07-01", amount_minor=-3000)

    result = _page(household_headers, account_id=account_id, direction="income")
    assert [t["id"] for t in result["items"]] == [income["id"]]


# ---------------------------------------------------------------------------
# Kategorie: EXISTS gegen transaction_splits (trifft auch Split-Buchungen)
# und exklusives "Ohne Kategorie"
# ---------------------------------------------------------------------------


def test_category_filter_matches_split_transactions_via_exists(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    grocery_id = _category_id_by_name(household_headers, "Lebensmittel")
    leisure_id = _category_id_by_name(household_headers, "Hobby, Gaming & Basteln")

    single_grocery = _create_tx(
        household_headers, account_id, booked_date="2026-07-01", amount_minor=-1000
    )
    _set_splits(
        household_headers, single_grocery, [{"amount_minor": -1000, "category_id": grocery_id}]
    )

    split_with_grocery = _create_tx(
        household_headers, account_id, booked_date="2026-07-02", amount_minor=-3000
    )
    _set_splits(
        household_headers,
        split_with_grocery,
        [
            {"amount_minor": -1000, "category_id": grocery_id},
            {"amount_minor": -2000, "category_id": leisure_id},
        ],
    )
    # Diese Buchung hat wegen mehrerer Splits KEIN einzelnes category_id-Feld
    # (siehe Transaction.category_id) - trotzdem muss der EXISTS-Filter sie
    # finden, weil einer ihrer Splits Lebensmittel traegt.
    detail = client.get(
        f"/v1/transactions/{split_with_grocery['id']}", headers=household_headers
    ).json()
    assert detail["category_id"] is None
    assert detail["is_split"] is True

    other_category_only = _create_tx(
        household_headers, account_id, booked_date="2026-07-03", amount_minor=-500
    )
    _set_splits(
        household_headers, other_category_only, [{"amount_minor": -500, "category_id": leisure_id}]
    )

    result = _page(household_headers, account_id=account_id, category_id=grocery_id)
    result_ids = {t["id"] for t in result["items"]}
    assert result_ids == {single_grocery["id"], split_with_grocery["id"]}


def test_uncategorized_filter_excludes_any_categorized_split(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    grocery_id = _category_id_by_name(household_headers, "Lebensmittel")

    fully_uncategorized = _create_tx(
        household_headers, account_id, booked_date="2026-07-01", amount_minor=-100
    )

    categorized = _create_tx(
        household_headers, account_id, booked_date="2026-07-02", amount_minor=-200
    )
    _set_splits(
        household_headers, categorized, [{"amount_minor": -200, "category_id": grocery_id}]
    )

    # Split-Buchung mit einem kategorisierten und einem unkategorisierten
    # Split gilt insgesamt NICHT als "ohne Kategorie".
    mixed_split = _create_tx(
        household_headers, account_id, booked_date="2026-07-03", amount_minor=-900
    )
    _set_splits(
        household_headers,
        mixed_split,
        [
            {"amount_minor": -400, "category_id": grocery_id},
            {"amount_minor": -500, "category_id": None},
        ],
    )

    fully_uncategorized_split = _create_tx(
        household_headers, account_id, booked_date="2026-07-04", amount_minor=-700
    )
    _set_splits(
        household_headers,
        fully_uncategorized_split,
        [{"amount_minor": -300, "category_id": None}, {"amount_minor": -400, "category_id": None}],
    )

    result = _page(household_headers, account_id=account_id, uncategorized=True)
    result_ids = {t["id"] for t in result["items"]}
    assert result_ids == {fully_uncategorized["id"], fully_uncategorized_split["id"]}


def test_category_id_and_uncategorized_are_mutually_exclusive(
    household_headers: dict[str, str],
) -> None:
    grocery_id = _category_id_by_name(household_headers, "Lebensmittel")
    response = client.get(
        _ENDPOINT,
        params={"category_id": grocery_id, "uncategorized": True},
        headers=household_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "category_id_and_uncategorized_mutually_exclusive"


# ---------------------------------------------------------------------------
# Sortierung: jede der fuenf Varianten in eindeutiger Reihenfolge
# ---------------------------------------------------------------------------


@pytest.fixture()
def sort_fixture_tx_ids(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> dict[str, str]:
    account_id, _ = account_ids
    specs: dict[str, dict[str, Any]] = {
        "a": {"booked_date": "2026-07-01", "amount_minor": -1000, "payee_label": "Alpha"},
        "b": {"booked_date": "2026-07-05", "amount_minor": -5000, "payee_label": "Bravo"},
        "c": {"booked_date": "2026-07-03", "amount_minor": 2000, "payee_label": "Charlie"},
        "d": {"booked_date": "2026-07-10", "amount_minor": -200, "payee_label": "Delta"},
    }
    return {
        key: _create_tx(household_headers, account_id, **spec)["id"]
        for key, spec in specs.items()
    }


@pytest.mark.parametrize(
    ("sort", "expected_order"),
    [
        ("date_desc", ["d", "b", "c", "a"]),
        ("date_asc", ["a", "c", "b", "d"]),
        ("amount_desc", ["c", "d", "a", "b"]),
        ("amount_asc", ["b", "a", "d", "c"]),
        ("payee_asc", ["a", "b", "c", "d"]),
    ],
)
def test_each_sort_returns_expected_global_order(
    household_headers: dict[str, str],
    account_ids: tuple[str, str],
    sort_fixture_tx_ids: dict[str, str],
    sort: str,
    expected_order: list[str],
) -> None:
    account_id, _ = account_ids
    result = _page(household_headers, account_id=account_id, sort=sort)
    got_ids = [t["id"] for t in result["items"]]
    expected_ids = [sort_fixture_tx_ids[key] for key in expected_order]
    assert got_ids == expected_ids
    assert result["next_cursor"] is None


# ---------------------------------------------------------------------------
# Cursor: stabile Tie-Breaker, keine Duplikate/Luecken durch alle Seiten
# ---------------------------------------------------------------------------


@pytest.fixture()
def tie_heavy_tx_ids(household_headers: dict[str, str], account_ids: tuple[str, str]) -> set[str]:
    """Datensatz mit bewussten Gleichstaenden (gleiches Datum/Betrag/Empfaenger
    mehrfach), damit jede Sortierung tatsaechlich auf ihre Tie-Breaker
    (created_at, dann id) angewiesen ist statt zufaellig stabil zu wirken."""
    account_id, _ = account_ids
    specs: list[dict[str, Any]] = [
        {"booked_date": "2026-07-01", "amount_minor": -1000, "payee_label": "Kaufhaus"},
        {"booked_date": "2026-07-01", "amount_minor": -1000, "payee_label": "Kaufhaus"},
        {"booked_date": "2026-07-01", "amount_minor": -2000, "payee_label": "Baeckerei"},
        {"booked_date": "2026-07-02", "amount_minor": -1000, "payee_label": "Kaufhaus"},
        {"booked_date": "2026-07-03", "amount_minor": 5000, "payee_label": "Arbeitgeber"},
        {"booked_date": "2026-07-03", "amount_minor": 5000, "payee_label": "Arbeitgeber"},
        {"booked_date": "2026-07-04", "amount_minor": -300, "payee_label": "Tankstelle"},
        {"booked_date": "2026-07-05", "amount_minor": -750, "payee_label": "Apotheke"},
        {"booked_date": "2026-07-05", "amount_minor": -750, "payee_label": "Apotheke"},
        {"booked_date": "2026-07-06", "amount_minor": -50, "payee_label": "Kiosk"},
        {"booked_date": "2026-07-07", "amount_minor": -125, "payee_label": "Drogerie"},
    ]
    return {_create_tx(household_headers, account_id, **spec)["id"] for spec in specs}


@pytest.mark.parametrize(
    "sort", ["date_desc", "date_asc", "amount_desc", "amount_asc", "payee_asc"]
)
def test_cursor_pagination_has_no_duplicates_or_gaps(
    household_headers: dict[str, str],
    account_ids: tuple[str, str],
    tie_heavy_tx_ids: set[str],
    sort: str,
) -> None:
    account_id, _ = account_ids
    base_params = {"account_id": account_id, "sort": sort}

    single_shot = _page(household_headers, **base_params, limit=100)
    assert single_shot["next_cursor"] is None
    single_shot_ids = [t["id"] for t in single_shot["items"]]
    assert set(single_shot_ids) == tie_heavy_tx_ids
    assert len(single_shot_ids) == len(tie_heavy_tx_ids)

    paged_ids = _collect_all_pages(household_headers, base_params, page_limit=3)
    assert paged_ids == single_shot_ids
    assert len(set(paged_ids)) == len(paged_ids)


def test_cursor_with_mismatched_sort_is_rejected(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    for i in range(4):
        _create_tx(
            household_headers,
            account_id,
            booked_date=f"2026-07-0{i + 1}",
            amount_minor=-100 * (i + 1),
        )

    first_page = _page(household_headers, account_id=account_id, sort="date_desc", limit=2)
    cursor = first_page["next_cursor"]
    assert cursor is not None

    response = client.get(
        _ENDPOINT,
        params={"account_id": account_id, "sort": "amount_desc", "limit": 2, "cursor": cursor},
        headers=household_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_cursor"


def test_garbage_cursor_is_rejected(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    response = client.get(
        _ENDPOINT,
        params={"account_id": account_id, "cursor": "not-a-real-cursor"},
        headers=household_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_cursor"


def test_limit_above_server_maximum_is_rejected(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    response = client.get(
        _ENDPOINT, params={"account_id": account_id, "limit": 101}, headers=household_headers
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Haushaltsisolierung
# ---------------------------------------------------------------------------


def test_transactions_of_other_households_never_appear(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    own = _create_tx(
        household_headers,
        account_id,
        booked_date="2026-07-01",
        amount_minor=-1000,
        payee_label="Eigener Haushalt",
    )

    other_token = _signup(
        f"pg-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    other_auth = _auth_headers(other_token)
    other_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=other_auth
    ).json()
    other_headers = {**other_auth, "X-Household-Id": other_household["id"]}
    other_account = client.post(
        "/v1/accounts",
        json={"name": "Fremdes Konto", "account_type": "checking"},
        headers=other_headers,
    ).json()
    foreign = _create_tx(
        other_headers,
        other_account["id"],
        booked_date="2026-07-01",
        amount_minor=-1000,
        payee_label="Fremder Haushalt",
    )

    own_result = _page(household_headers, search="Haushalt")
    own_ids = {t["id"] for t in own_result["items"]}
    assert own["id"] in own_ids
    assert foreign["id"] not in own_ids

    foreign_result = _page(other_headers, search="Haushalt")
    foreign_ids = {t["id"] for t in foreign_result["items"]}
    assert foreign["id"] in foreign_ids
    assert own["id"] not in foreign_ids

    # Auch ueber alle Sortierungen und ohne jeden Filter darf ein fremder
    # Haushalt nie auftauchen - unabhaengiges Kriterium von der Suche oben.
    for sort in ("date_desc", "date_asc", "amount_desc", "amount_asc", "payee_asc"):
        all_own = _page(household_headers, sort=sort, limit=100)
        assert all(t["id"] != foreign["id"] for t in all_own["items"])
