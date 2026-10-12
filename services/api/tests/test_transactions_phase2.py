"""Phase 2 (Buchungsregister): Splits, Transfers, Kontoabstimmung.
Abnahmekriterium laut Architekturplan Abschnitt 17: "Salden, Splits und
Transfers erfuellen alle Invarianten"."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"p2-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Phase-2-Testhaushalt"}, headers=_auth_headers(token)
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


# Das deutsche Standardpaket (supabase/migrations/0001_schema.sql)
# hat die frueher hartcodierte Kategorie "Lebensmittel" durch
# "Lebensmittel" ersetzt.
_GROCERY_CATEGORY_NAME = "Lebensmittel"


def _lebensmittel_category_id(headers: dict[str, str]) -> str:
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == _GROCERY_CATEGORY_NAME:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{_GROCERY_CATEGORY_NAME}' nicht gefunden")


def test_split_sum_invariant_rejects_mismatched_total(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    tx = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": -5000},
        headers=household_headers,
    ).json()

    response = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={
            "amount_minor": -5000,
            "splits": [{"amount_minor": -3000}, {"amount_minor": -1000}],
        },
        headers=household_headers,
    )
    assert response.status_code == 422


def test_split_sum_invariant_accepts_matching_total_and_marks_is_split(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    category_id = _lebensmittel_category_id(household_headers)
    tx = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": -5000},
        headers=household_headers,
    ).json()

    response = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={
            "amount_minor": -5000,
            "splits": [
                {"amount_minor": -3000, "category_id": category_id},
                {"amount_minor": -2000},
            ],
        },
        headers=household_headers,
    )
    assert response.status_code == 200
    detail = response.json()
    assert sum(s["amount_minor"] for s in detail["splits"]) == detail["amount_minor"] == -5000
    assert detail["is_split"] is True
    assert detail["category_id"] is None  # mehrere Splits -> keine einzelne Kategorie im Register

    listed = client.get(
        f"/v1/transactions?account_id={account_id}", headers=household_headers
    ).json()
    row = next(t for t in listed if t["id"] == tx["id"])
    assert row["is_split"] is True
    assert row["category_id"] is None


def test_bulk_categorize_only_affects_single_split_transactions(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    category_id = _lebensmittel_category_id(household_headers)

    single = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": -1000},
        headers=household_headers,
    ).json()
    split = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": -2000},
        headers=household_headers,
    ).json()
    client.put(
        f"/v1/transactions/{split['id']}/splits",
        json={"amount_minor": -2000, "splits": [{"amount_minor": -1000}, {"amount_minor": -1000}]},
        headers=household_headers,
    )

    response = client.post(
        "/v1/transactions/bulk-categorize",
        json={"transaction_ids": [single["id"], split["id"]], "category_id": category_id},
        headers=household_headers,
    )
    assert response.status_code == 200
    result = response.json()
    assert result == {"updated": 1, "skipped": 1}

    updated_single = client.get(
        f"/v1/transactions/{single['id']}", headers=household_headers
    ).json()
    assert updated_single["category_id"] == category_id


def test_transfer_link_marks_both_neutral_and_unlink_resets(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    checking_id, savings_id = account_ids
    outgoing = client.post(
        "/v1/transactions",
        json={"account_id": checking_id, "booked_date": "2026-07-01", "amount_minor": -10000},
        headers=household_headers,
    ).json()
    incoming = client.post(
        "/v1/transactions",
        json={"account_id": savings_id, "booked_date": "2026-07-01", "amount_minor": 10000},
        headers=household_headers,
    ).json()

    link_response = client.post(
        "/v1/transfers/link",
        json={"outgoing_transaction_id": outgoing["id"], "incoming_transaction_id": incoming["id"]},
        headers=household_headers,
    )
    assert link_response.status_code == 201
    link = link_response.json()

    for tx_id in (outgoing["id"], incoming["id"]):
        detail = client.get(f"/v1/transactions/{tx_id}", headers=household_headers).json()
        assert detail["is_transfer"] is True

    # Doppelte Verknuepfung derselben Buchung muss abgelehnt werden.
    other = client.post(
        "/v1/transactions",
        json={"account_id": savings_id, "booked_date": "2026-07-02", "amount_minor": 500},
        headers=household_headers,
    ).json()
    conflict = client.post(
        "/v1/transfers/link",
        json={"outgoing_transaction_id": outgoing["id"], "incoming_transaction_id": other["id"]},
        headers=household_headers,
    )
    assert conflict.status_code == 409

    unlink_response = client.delete(f"/v1/transfers/link/{link['id']}", headers=household_headers)
    assert unlink_response.status_code == 204

    for tx_id in (outgoing["id"], incoming["id"]):
        detail = client.get(f"/v1/transactions/{tx_id}", headers=household_headers).json()
        assert detail["is_transfer"] is False


def test_transfer_link_requires_different_accounts(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    a = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": -100},
        headers=household_headers,
    ).json()
    b = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": 100},
        headers=household_headers,
    ).json()

    response = client.post(
        "/v1/transfers/link",
        json={"outgoing_transaction_id": a["id"], "incoming_transaction_id": b["id"]},
        headers=household_headers,
    )
    assert response.status_code == 400


def test_reconciliation_matches_and_marks_transactions_reconciled(
    household_headers: dict[str, str],
) -> None:
    account = client.post(
        "/v1/accounts",
        json={
            "name": "Abstimmkonto",
            "account_type": "checking",
            "opening_balance_minor": 100000,
        },
        headers=household_headers,
    ).json()

    tx1 = client.post(
        "/v1/transactions",
        json={"account_id": account["id"], "booked_date": "2026-07-01", "amount_minor": -2000},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/transactions",
        json={"account_id": account["id"], "booked_date": "2026-07-05", "amount_minor": -3000},
        headers=household_headers,
    )

    # Computed balance zum 2026-07-02: 100000 - 2000 = 98000.
    mismatch = client.post(
        f"/v1/accounts/{account['id']}/reconcile",
        json={"as_of_date": "2026-07-02", "statement_balance_minor": 90000},
        headers=household_headers,
    ).json()
    assert mismatch["status"] == "discrepancy"
    assert mismatch["difference_minor"] == 90000 - 98000

    unaffected = client.get(f"/v1/transactions/{tx1['id']}", headers=household_headers).json()
    assert unaffected["status"] == "booked"

    match = client.post(
        f"/v1/accounts/{account['id']}/reconcile",
        json={"as_of_date": "2026-07-02", "statement_balance_minor": 98000},
        headers=household_headers,
    ).json()
    assert match["status"] == "completed"
    assert match["difference_minor"] == 0

    reconciled_tx1 = client.get(f"/v1/transactions/{tx1['id']}", headers=household_headers).json()
    assert reconciled_tx1["status"] == "reconciled"


def _create_goal(headers: dict[str, str], name: str = "Notgroschen") -> str:
    goal = client.post(
        "/v1/goals",
        json={"name": name, "target_amount_minor": 100000},
        headers=headers,
    ).json()
    return str(goal["id"])


def test_split_goal_id_round_trips_via_splits_and_create(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    goal_id = _create_goal(household_headers)

    created = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-01",
            "amount_minor": 5000,
            "goal_id": goal_id,
        },
        headers=household_headers,
    ).json()
    detail = client.get(f"/v1/transactions/{created['id']}", headers=household_headers).json()
    assert detail["splits"][0]["goal_id"] == goal_id

    response = client.put(
        f"/v1/transactions/{created['id']}/splits",
        json={"amount_minor": 5000, "splits": [{"amount_minor": 5000, "goal_id": None}]},
        headers=household_headers,
    )
    assert response.json()["splits"][0]["goal_id"] is None


def test_unassigned_goal_splits_excludes_assigned_and_archived(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    goal_id = _create_goal(household_headers)

    unassigned = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": 1000},
        headers=household_headers,
    ).json()
    assigned = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-01",
            "amount_minor": 2000,
            "goal_id": goal_id,
        },
        headers=household_headers,
    ).json()
    archived = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": 3000},
        headers=household_headers,
    ).json()
    client.delete(f"/v1/transactions/{archived['id']}", headers=household_headers)

    candidates = client.get(
        "/v1/transactions/splits/unassigned-goal", headers=household_headers
    ).json()
    candidate_tx_ids = {c["transaction_id"] for c in candidates}
    assert unassigned["id"] in candidate_tx_ids
    assert assigned["id"] not in candidate_tx_ids
    assert archived["id"] not in candidate_tx_ids


def test_assign_goal_to_splits_bulk_and_unassign_and_foreign_household(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    goal_id = _create_goal(household_headers)

    tx1 = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": 1000},
        headers=household_headers,
    ).json()
    tx2 = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": 2000},
        headers=household_headers,
    ).json()
    split_id_1 = client.get(f"/v1/transactions/{tx1['id']}", headers=household_headers).json()[
        "splits"
    ][0]["id"]
    split_id_2 = client.get(f"/v1/transactions/{tx2['id']}", headers=household_headers).json()[
        "splits"
    ][0]["id"]

    assign_res = client.post(
        "/v1/transactions/splits/assign-goal",
        json={"split_ids": [split_id_1, split_id_2], "goal_id": goal_id},
        headers=household_headers,
    )
    assert assign_res.status_code == 200
    assert assign_res.json() == {"updated": 2, "skipped": 0}

    detail1 = client.get(f"/v1/transactions/{tx1['id']}", headers=household_headers).json()
    assert detail1["splits"][0]["goal_id"] == goal_id

    unassign_res = client.post(
        "/v1/transactions/splits/assign-goal",
        json={"split_ids": [split_id_1], "goal_id": None},
        headers=household_headers,
    )
    assert unassign_res.json() == {"updated": 1, "skipped": 0}
    detail1_after = client.get(f"/v1/transactions/{tx1['id']}", headers=household_headers).json()
    assert detail1_after["splits"][0]["goal_id"] is None

    other_household_token = _signup(
        f"p2-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2"
    )
    other_headers_auth = _auth_headers(other_household_token)
    other_household = client.post(
        "/v1/households", json={"name": "Anderer Haushalt"}, headers=other_headers_auth
    ).json()
    other_headers = {**other_headers_auth, "X-Household-Id": other_household["id"]}
    foreign_goal_res = client.post(
        "/v1/transactions/splits/assign-goal",
        json={"split_ids": [split_id_2], "goal_id": goal_id},
        headers=other_headers,
    )
    assert foreign_goal_res.status_code == 404


def _new_transaction(headers: dict[str, str], account_id: str, amount_minor: int = -1000) -> dict:
    return client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-01",
            "amount_minor": amount_minor,
        },
        headers=headers,
    ).json()


def test_new_transaction_starts_at_version_one_and_exposes_etag(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    created = client.post(
        "/v1/transactions",
        json={"account_id": account_id, "booked_date": "2026-07-01", "amount_minor": -1000},
        headers=household_headers,
    )
    assert created.status_code == 201
    assert created.json()["version"] == 1
    assert created.headers["ETag"] == '"1"'

    detail = client.get(f"/v1/transactions/{created.json()['id']}", headers=household_headers)
    assert detail.json()["version"] == 1
    assert detail.headers["ETag"] == '"1"'


def test_update_with_matching_version_succeeds_and_bumps_version(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    tx = _new_transaction(household_headers, account_id)

    response = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "Erste Aenderung", "expected_version": tx["version"]},
        headers=household_headers,
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["note"] == "Erste Aenderung"
    assert updated["version"] == tx["version"] + 1
    assert response.headers["ETag"] == f'"{updated["version"]}"'

    # Mit der frisch erhaltenen Version darf sofort weitergeschrieben werden.
    followup = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "Zweite Aenderung", "expected_version": updated["version"]},
        headers=household_headers,
    )
    assert followup.status_code == 200
    assert followup.json()["version"] == tx["version"] + 2


def test_update_with_stale_version_returns_409_with_current_server_state(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Kernfall der Konflikterkennung: zwei Geraete haben denselben Stand
    gelesen, das zweite schreibt mit veralteter Version und darf die Aenderung
    des ersten nicht ueberschreiben."""
    account_id, _ = account_ids
    tx = _new_transaction(household_headers, account_id)
    stale_version = tx["version"]

    first = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "Geraet A", "expected_version": stale_version},
        headers=household_headers,
    )
    assert first.status_code == 200

    second = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "Geraet B", "expected_version": stale_version},
        headers=household_headers,
    )
    assert second.status_code == 409
    body = second.json()
    assert body["detail"] == "version_conflict"
    assert body["expected_version"] == stale_version
    assert body["current_version"] == stale_version + 1
    # Der Serverstand liegt vollstaendig bei, damit der Client beide Staende
    # gegenueberstellen kann, ohne nachzuladen.
    assert body["current"]["note"] == "Geraet A"
    assert body["current"]["version"] == body["current_version"]
    assert body["current"]["splits"] != []

    # Die verworfene Schreiboperation darf nichts veraendert haben.
    persisted = client.get(f"/v1/transactions/{tx['id']}", headers=household_headers).json()
    assert persisted["note"] == "Geraet A"
    assert persisted["version"] == stale_version + 1

    # Mit der aus dem Fehlerkoerper gelernten Version gelingt der Retry.
    retry = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "Geraet B", "expected_version": body["current_version"]},
        headers=household_headers,
    )
    assert retry.status_code == 200
    assert retry.json()["note"] == "Geraet B"


def test_if_match_header_is_equivalent_to_expected_version_field(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    tx = _new_transaction(household_headers, account_id)

    ok = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "per Header"},
        headers={**household_headers, "If-Match": f'"{tx["version"]}"'},
    )
    assert ok.status_code == 200
    current_version = ok.json()["version"]

    stale = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "veraltet"},
        headers={**household_headers, "If-Match": f'"{tx["version"]}"'},
    )
    assert stale.status_code == 409
    assert stale.json()["current_version"] == current_version
    assert stale.headers["ETag"] == f'"{current_version}"'

    # Schwaches Tag und blosse Zahl werden ebenfalls akzeptiert.
    weak = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "schwaches Tag"},
        headers={**household_headers, "If-Match": f'W/"{current_version}"'},
    )
    assert weak.status_code == 200

    # Unbrauchbarer Header ist ein Client-Fehler - er darf nicht stillschweigend
    # als "keine Pruefung" durchgehen.
    invalid = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "kaputt"},
        headers={**household_headers, "If-Match": "*"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "invalid_if_match"

    # Widersprechen sich Header und Body, wird nicht geraten.
    contradiction = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "widerspruch", "expected_version": 1},
        headers={**household_headers, "If-Match": '"99"'},
    )
    assert contradiction.status_code == 400
    assert contradiction.json()["detail"] == "if_match_body_version_mismatch"


def test_splits_update_checks_version_and_bumps_even_without_amount_change(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """PUT .../splits aendert oft nur die Kategorien, nicht den Betrag - die
    Version muss trotzdem steigen, sonst bliebe genau diese Aenderung fuer
    andere Geraete unsichtbar."""
    account_id, _ = account_ids
    category_id = _lebensmittel_category_id(household_headers)
    tx = _new_transaction(household_headers, account_id, amount_minor=-5000)

    ok = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={
            "amount_minor": -5000,
            "splits": [{"amount_minor": -5000, "category_id": category_id}],
            "expected_version": tx["version"],
        },
        headers=household_headers,
    )
    assert ok.status_code == 200
    # Genau ein Schritt, obwohl der Betrag gleich blieb - der Client kann die
    # Version deshalb deterministisch fortschreiben.
    assert ok.json()["version"] == tx["version"] + 1
    assert ok.headers["ETag"] == f'"{ok.json()["version"]}"'

    conflict = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={
            "amount_minor": -5000,
            "splits": [{"amount_minor": -5000}],
            "expected_version": tx["version"],
        },
        headers=household_headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["current_version"] == ok.json()["version"]
    # Die abgelehnte Anfrage darf die Splits nicht angefasst haben.
    assert conflict.json()["current"]["splits"][0]["category_id"] == category_id


def test_version_check_reports_deletion_by_another_device_as_conflict(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    tx = _new_transaction(household_headers, account_id)

    assert (
        client.delete(f"/v1/transactions/{tx['id']}", headers=household_headers).status_code == 204
    )

    response = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "offline bearbeitet", "expected_version": tx["version"]},
        headers=household_headers,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "transaction_archived"
    assert body["current"]["is_archived"] is True


def test_version_check_is_optional_and_unknown_transaction_stays_404(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Bestandsclients kennen die Version noch nicht - ohne expected_version
    bzw. If-Match bleibt das Verhalten unveraendert."""
    account_id, _ = account_ids
    tx = _new_transaction(household_headers, account_id)

    blind = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "ohne Version"},
        headers=household_headers,
    )
    assert blind.status_code == 200
    assert blind.json()["version"] == tx["version"] + 1

    missing = client.patch(
        f"/v1/transactions/{uuid.uuid4()}",
        json={"note": "gibt es nicht", "expected_version": 1},
        headers=household_headers,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "transaction_not_found"


def test_version_does_not_bump_on_unchanged_write(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Ein PATCH, der nichts veraendert, darf keinen Zaehler bewegen - sonst
    erzeugte ein wiederholt zugestellter Offline-Request Phantomkonflikte auf
    anderen Geraeten."""
    account_id, _ = account_ids
    tx = _new_transaction(household_headers, account_id)

    first = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "unveraendert", "expected_version": tx["version"]},
        headers=household_headers,
    ).json()

    repeat = client.patch(
        f"/v1/transactions/{tx['id']}",
        json={"note": "unveraendert", "expected_version": first["version"]},
        headers=household_headers,
    )
    assert repeat.status_code == 200
    assert repeat.json()["version"] == first["version"]


def test_delete_transaction_unlinks_transfer_and_is_omitted_from_list(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    checking_id, savings_id = account_ids
    outgoing = client.post(
        "/v1/transactions",
        json={"account_id": checking_id, "booked_date": "2026-07-01", "amount_minor": -500},
        headers=household_headers,
    ).json()
    incoming = client.post(
        "/v1/transactions",
        json={"account_id": savings_id, "booked_date": "2026-07-01", "amount_minor": 500},
        headers=household_headers,
    ).json()
    client.post(
        "/v1/transfers/link",
        json={"outgoing_transaction_id": outgoing["id"], "incoming_transaction_id": incoming["id"]},
        headers=household_headers,
    )

    delete_response = client.delete(f"/v1/transactions/{outgoing['id']}", headers=household_headers)
    assert delete_response.status_code == 204

    listed = client.get(
        f"/v1/transactions?account_id={checking_id}", headers=household_headers
    ).json()
    assert all(t["id"] != outgoing["id"] for t in listed)

    sibling = client.get(f"/v1/transactions/{incoming['id']}", headers=household_headers).json()
    assert sibling["is_transfer"] is False


# ---------------------------------------------------------------------------
# Volltextsuche im Buchungsregister
#
# Die Suche laeuft serverseitig ueber den gesamten Bestand. Eine reine
# Browsersuche haette nur die geladene Seite durchsucht und damit "nicht
# gefunden" gemeldet, obwohl die Buchung existiert - der Fehler, der eine
# Suchfunktion wertlos macht.
# ---------------------------------------------------------------------------


def test_list_transactions_search_matches_payee_and_note(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-01",
            "amount_minor": -1200,
            "payee_label": "Nordlicht Baeckerei",
        },
        headers=household_headers,
    )
    client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-02",
            "amount_minor": -2400,
            "payee_label": "Suedwind Tankstelle",
            "note": "Quartalsabrechnung",
        },
        headers=household_headers,
    )

    by_payee = client.get("/v1/transactions?search=nordlicht", headers=household_headers).json()
    assert [t["payee_label"] for t in by_payee] == ["Nordlicht Baeckerei"]

    by_note = client.get("/v1/transactions?search=quartals", headers=household_headers).json()
    assert [t["payee_label"] for t in by_note] == ["Suedwind Tankstelle"]

    empty = client.get("/v1/transactions?search=gibtesnicht", headers=household_headers).json()
    assert empty == []


def test_list_transactions_search_escapes_like_wildcards(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Ein Prozentzeichen im Suchtext ist ein Zeichen, kein Platzhalter -
    sonst liefert die Suche nach "100%" das gesamte Register."""
    account_id, _ = account_ids
    client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-01",
            "amount_minor": -1000,
            "payee_label": "Rabatt 100% Aktion",
        },
        headers=household_headers,
    )
    client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-02",
            "amount_minor": -2000,
            "payee_label": "Ohne Sonderzeichen",
        },
        headers=household_headers,
    )

    hits = client.get("/v1/transactions?search=100%25", headers=household_headers).json()
    assert [t["payee_label"] for t in hits] == ["Rabatt 100% Aktion"]


def test_list_transactions_search_is_scoped_to_the_other_filters(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    checking_id, savings_id = account_ids
    for account_id in (checking_id, savings_id):
        client.post(
            "/v1/transactions",
            json={
                "account_id": account_id,
                "booked_date": "2026-07-01",
                "amount_minor": -500,
                "payee_label": "Doppelgaenger",
            },
            headers=household_headers,
        )

    scoped = client.get(
        f"/v1/transactions?search=doppelgaenger&account_id={savings_id}",
        headers=household_headers,
    ).json()
    assert len(scoped) == 1
    assert scoped[0]["account_id"] == savings_id


def test_list_transactions_search_regex_matches_pattern(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    account_id, _ = account_ids
    client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-01",
            "amount_minor": -1999,
            "payee_label": "Musterassekuranz Versicherung AG",
        },
        headers=household_headers,
    )
    client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-02",
            "amount_minor": -2400,
            "payee_label": "Suedwind Tankstelle",
        },
        headers=household_headers,
    )

    hits = client.get(
        "/v1/transactions?search_regex=mustera.?ssekuranz", headers=household_headers
    ).json()
    assert [t["payee_label"] for t in hits] == ["Musterassekuranz Versicherung AG"]


def test_list_transactions_search_regex_rejects_invalid_pattern(
    household_headers: dict[str, str],
) -> None:
    response = client.get(
        "/v1/transactions?search_regex=%28unclosed", headers=household_headers
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_regex_pattern"


def test_list_transactions_search_and_search_regex_are_mutually_exclusive(
    household_headers: dict[str, str],
) -> None:
    response = client.get(
        "/v1/transactions?search=foo&search_regex=bar", headers=household_headers
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "search_and_search_regex_mutually_exclusive"


# ---------------------------------------------------------------------------
# Loeschen einer Buchung loest, was auf sie zeigt
#
# Buchungen werden archiviert statt geloescht. Ohne Aufraeumen blieben die
# Verweise darauf bestehen und behaupteten weiter etwas ueber Geld, das im
# Bestand nicht mehr steht - eine Bestandsaufnahme fand dafuer fuenfzehn
# Lesepfade. Statt sie einzeln zu filtern, raeumt der Loeschpfad an der Quelle
# auf (app/services/transaction_detach_service.py).
# ---------------------------------------------------------------------------


async def _db_scalar(query: str, params: dict[str, object]) -> object:
    from sqlalchemy.sql import text

    from app.core.db import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        return (await session.execute(text(query), params)).scalar_one()


async def _db_execute(query: str, params: dict[str, object]) -> None:
    """Fuer Vorbedingungen, die mehr als eine Zeile anfassen."""
    from sqlalchemy.sql import text

    from app.core.db import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(query), params)
        await session.commit()


def _booking(headers: dict[str, str], account_id: str, amount_minor: int) -> dict[str, object]:
    return client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-04-02",
            "amount_minor": amount_minor,
            "payee_label": "MUSTER KRANKENVERSICHERUNG",
        },
        headers=headers,
    ).json()


async def test_delete_reopens_a_contract_period(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Der Fall, der die Vertragsseite zum Luegen brachte: die Faelligkeit stand
    weiter auf 'bezahlt', obwohl der Beleg aus dem Bestand genommen wurde."""
    account_id, _ = account_ids
    tx = _booking(household_headers, account_id, -1999)

    contract_id = client.post(
        "/v1/contracts",
        json={
            "name": "Muster Krankenversicherung",
            "amount_minor": 1999,
            "interval": "monthly",
            "next_due_date": "2026-05-02",
            "first_due_date": "2026-04-02",
        },
        headers=household_headers,
    ).json()["id"]
    await _db_execute(
        "insert into contract_occurrences "
        "(household_id, contract_id, due_date, status, transaction_id, booked_date, "
        " amount_minor, matched_at) "
        "values (cast(:household_id as uuid), cast(:contract_id as uuid), '2026-04-02', "
        "        'matched', cast(:tx as uuid), '2026-04-02', 1999, now()) "
        "on conflict (contract_id, due_date) do update set "
        "  status = 'matched', transaction_id = excluded.transaction_id, "
        "  booked_date = excluded.booked_date, amount_minor = excluded.amount_minor, "
        "  matched_at = excluded.matched_at",
        {
            "household_id": household_headers["X-Household-Id"],
            "contract_id": contract_id,
            "tx": tx["id"],
        },
    )

    entfernt = client.delete(f"/v1/transactions/{tx['id']}", headers=household_headers)
    assert entfernt.status_code == 204, entfernt.text

    zustand = await _db_scalar(
        "select status from contract_occurrences where contract_id = cast(:c as uuid) "
        "and due_date = '2026-04-02'",
        {"c": contract_id},
    )
    assert zustand == "missing"
    belegt = await _db_scalar(
        "select count(*) from contract_occurrences where contract_id = cast(:c as uuid) "
        "and transaction_id = cast(:tx as uuid)",
        {"c": contract_id, "tx": tx["id"]},
    )
    assert belegt == 0


async def test_delete_takes_the_reimbursement_allocation_with_it(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Sonst zeigte der Erstattungsvorgang weiter den vollen Betrag als
    'erhalten' und galt als abgeschlossen."""
    account_id, _ = account_ids
    tx = _booking(household_headers, account_id, -12000)

    person_id = client.post(
        "/v1/reimbursements/people",
        json={"display_name": "Testperson"},
        headers=household_headers,
    ).json()["id"]
    fall = client.post(
        "/v1/reimbursements/cases",
        json={
            "insured_person_id": person_id,
            "provider_name": "Zahnarztpraxis",
            "invoice_date": "2026-04-02",
            "billed_minor": 12000,
            "expected_minor": 12000,
        },
        headers=household_headers,
    )
    assert fall.status_code == 201, fall.text
    case_id = fall.json()["id"]
    zuordnung = client.post(
        f"/v1/reimbursements/cases/{case_id}/allocations",
        json={
            "transaction_id": tx["id"],
            "allocation_type": "provider_payment",
            "amount_minor": 12000,
        },
        headers=household_headers,
    )
    assert zuordnung.status_code in (200, 201), zuordnung.text

    entfernt = client.delete(f"/v1/transactions/{tx['id']}", headers=household_headers)
    assert entfernt.status_code == 204, entfernt.text

    offen = await _db_scalar(
        "select count(*) from health_transaction_allocations "
        "where transaction_id = cast(:tx as uuid)",
        {"tx": tx["id"]},
    )
    assert offen == 0
    # Und der Vorgang ist neu gerechnet, nicht bloss um eine Zeile leichter.
    allocations = client.get(
        f"/v1/reimbursements/cases/{case_id}/allocations", headers=household_headers
    ).json()
    assert allocations == []


async def test_delete_clears_the_transfer_flag_on_the_surviving_side(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Die Gegenbuchung behielt sonst das Umbuchungskennzeichen ohne Gegenstueck
    - und jede Auswertung filtert Umbuchungen heraus. Sie waere im Register
    sichtbar, in Budget und Berichten aber unsichtbar gewesen."""
    account_id, other_account_id = account_ids
    ausgang = _booking(household_headers, account_id, -10000)
    eingang = _booking(household_headers, other_account_id, 10000)

    link = client.post(
        "/v1/transfers/link",
        json={"outgoing_transaction_id": ausgang["id"], "incoming_transaction_id": eingang["id"]},
        headers=household_headers,
    )
    assert link.status_code in (200, 201), link.text

    assert (
        client.delete(f"/v1/transactions/{ausgang['id']}", headers=household_headers).status_code
        == 204
    )

    ueberlebende = client.get(f"/v1/transactions/{eingang['id']}", headers=household_headers).json()
    assert ueberlebende["is_transfer"] is False


async def test_delete_clears_the_transfer_flag_of_an_imported_pair(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Umbuchungen aus dem Import haben gar keine transfer_links-Zeile.

    Sowohl der Stapelcommit als auch die Review-Inbox setzen is_transfer auf
    beiden Seiten per UPDATE, ohne je zu verknuepfen. Ueber transfer_links war
    die Gegenbuchung deshalb nicht auffindbar - sie behielt das Kennzeichen ohne
    Gegenstueck und fiel damit aus Budget und Berichten, obwohl sie im Register
    sichtbar blieb. Auffindbar ist sie ueber die Staging-Zeile, die beide Seiten
    kennt."""
    account_id, other_account_id = account_ids
    importiert = _booking(household_headers, account_id, -10000)
    gegenbuchung = _booking(household_headers, other_account_id, 10000)

    # So sieht es nach einem Import mit erkanntem Transfer aus: beide Seiten
    # markiert, kein transfer_links-Eintrag, die Staging-Zeile kennt das Paar.
    await _db_execute(
        "update transactions set is_transfer = true "
        "where id in (cast(:a as uuid), cast(:b as uuid))",
        {"a": importiert["id"], "b": gegenbuchung["id"]},
    )
    # Lauf und Zeile in EINER Anweisung - zwei getrennte Sitzungen saehen den
    # noch nicht committeten Lauf nicht.
    await _db_execute(
        "with lauf as ("
        "  insert into import_runs (household_id, filename, file_hash, file_size_bytes, status) "
        "  values (cast(:h as uuid), 'transfer.csv', 'hash', 1, 'completed') returning id"
        ") "
        "insert into import_rows (household_id, import_run_id, row_number, raw_data, "
        " amount_minor, status, committed_transaction_id, duplicate_of_transaction_id) "
        "select cast(:h as uuid), lauf.id, 1, '{}'::jsonb, -10000, 'committed', "
        "       cast(:a as uuid), cast(:b as uuid) from lauf",
        {
            "h": household_headers["X-Household-Id"],
            "a": importiert["id"],
            "b": gegenbuchung["id"],
        },
    )

    entfernt = client.delete(
        f"/v1/transactions/{importiert['id']}", headers=household_headers
    )
    assert entfernt.status_code == 204, entfernt.text

    ueberlebende = client.get(
        f"/v1/transactions/{gegenbuchung['id']}", headers=household_headers
    ).json()
    assert ueberlebende["is_transfer"] is False


async def test_delete_removes_a_hand_assigned_contract_period_instead_of_reopening_it(
    household_headers: dict[str, str], account_ids: tuple[str, str]
) -> None:
    """Eine von Hand zugeordnete Faelligkeit sitzt auf dem BUCHUNGSTAG, nicht auf
    einer geplanten Faelligkeit. Als 'missing' stehengelassen behauptete sie eine
    Zahlung zum 17., die es im Raster des Vertrags nie gab."""
    account_id, _ = account_ids
    tx = _booking(household_headers, account_id, -1999)
    contract_id = client.post(
        "/v1/contracts",
        json={
            "name": "Miete",
            "amount_minor": 1999,
            "interval": "monthly",
            "next_due_date": "2026-09-01",
            "first_due_date": "2026-04-01",
        },
        headers=household_headers,
    ).json()["id"]

    # So sieht eine Handzuordnung aus (contract_service.apply_reassign): eine
    # Faelligkeit am Buchungstag plus ein Eintrag in den Handzuordnungen.
    await _db_execute(
        "insert into contract_occurrences (household_id, contract_id, due_date, status, "
        " transaction_id, booked_date, amount_minor, matched_at) "
        "values (cast(:h as uuid), cast(:c as uuid), '2026-04-02', 'matched', "
        "        cast(:tx as uuid), '2026-04-02', 1999, now()) "
        "on conflict (contract_id, due_date) do update set "
        "  status = 'matched', transaction_id = excluded.transaction_id, "
        "  amount_minor = excluded.amount_minor",
        {"h": household_headers["X-Household-Id"], "c": contract_id, "tx": tx["id"]},
    )
    await _db_execute(
        "insert into contract_assignment_overrides (household_id, transaction_id, contract_id) "
        "values (cast(:h as uuid), cast(:tx as uuid), cast(:c as uuid))",
        {"h": household_headers["X-Household-Id"], "c": contract_id, "tx": tx["id"]},
    )

    entfernt = client.delete(f"/v1/transactions/{tx['id']}", headers=household_headers)
    assert entfernt.status_code == 204, entfernt.text

    uebrig = await _db_scalar(
        "select count(*) from contract_occurrences where contract_id = cast(:c as uuid) "
        "and due_date = '2026-04-02'",
        {"c": contract_id},
    )
    assert uebrig == 0

    # Und der Terminplan des Vertrags bleibt unangetastet - kein Zurueckdrehen
    # ueber Perioden hinweg, die gar nicht betroffen waren.
    vertrag = client.get(f"/v1/contracts/{contract_id}", headers=household_headers).json()
    assert vertrag["next_due_date"] == "2026-09-01"
