"""Kategoriezuordnung an Buchungen (docs/KATEGORISIERUNGSPLAN.md, Abschnitte
"Kategorie an Buchungen aendern" und "Manuelle Mehrfachzuordnung",
Lieferabschnitt 3).

Laeuft wie die uebrigen Tests gegen ein echtes lokales Supabase - inklusive
des Triggers reject_archived_category_reference() und der zusammengesetzten
Fremdschluessel aus supabase/migrations/0001_schema.sql.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

# Namen aus dem deutschen Standardpaket (Taxonomie v1). Ein neuer Haushalt
# bekommt es beim Anlegen installiert, feste UUIDs gibt es also nicht -
# gesucht wird ueber GET /v1/categories. Bewusst nur ASCII-Namen, damit die
# Testdatei unabhaengig von der Konsolen-Codepage lesbar bleibt.
GROCERY = "Lebensmittel"
RESTAURANT = "Restaurant"
FUEL = "Tanken & Laden"


def _household_headers(prefix: str) -> dict[str, str]:
    token = _signup(f"{prefix}-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households",
        json={"name": "Kategoriezuordnungs-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def headers() -> dict[str, str]:
    return _household_headers("cat")


@pytest.fixture()
def account_id(headers: dict[str, str]) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return str(account["id"])


def _category_id(headers: dict[str, str], name: str) -> str:
    groups = client.get("/v1/categories", headers=headers).json()
    for group in groups:
        for category in group["categories"]:
            if category["name"] == name:
                return str(category["id"])
    raise AssertionError(f"Standardkategorie '{name}' nicht gefunden")


def _new_transaction(
    headers: dict[str, str],
    account_id: str,
    *,
    amount_minor: int = -1000,
    category_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "account_id": account_id,
        "booked_date": "2026-07-01",
        "amount_minor": amount_minor,
    }
    if category_id is not None:
        payload["category_id"] = category_id
    response = client.post("/v1/transactions", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _split_transaction(headers: dict[str, str], account_id: str) -> dict[str, Any]:
    """Buchung mit zwei Splits - die Faelle, die eine Gesamtkategorie niemals
    still ueberschreiben darf."""
    tx = _new_transaction(headers, account_id, amount_minor=-5000)
    response = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={
            "amount_minor": -5000,
            "splits": [{"amount_minor": -3000}, {"amount_minor": -2000}],
            "expected_version": tx["version"],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _detail(headers: dict[str, str], transaction_id: str) -> dict[str, Any]:
    return dict(client.get(f"/v1/transactions/{transaction_id}", headers=headers).json())


async def _sql(query: str, params: dict[str, Any]) -> None:
    """Direkter Schreibzugriff fuer Zustaende, die (noch) kein Endpunkt
    herstellt: Archivierung und Merge-Tombstone. Die Session laeuft ohne
    SET ROLE authenticated, also als postgres mit BYPASSRLS."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(text(query), params)


async def _archive_category(category_id: str) -> None:
    await _sql(
        "update categories set is_archived = true, archived_at = now() where id = :id",
        {"id": category_id},
    )


async def _merge_category(source_id: str, target_id: str) -> None:
    """Merge-Tombstone von Hand: die Quelle bleibt archiviert erhalten und
    zeigt auf das aktive Ziel."""
    await _sql(
        "update categories set is_archived = true, archived_at = now(), "
        "merged_into_category_id = :target where id = :source",
        {"source": source_id, "target": target_id},
    )


# ---------------------------------------------------------------------------
# PUT /v1/transactions/{id}/category
# ---------------------------------------------------------------------------


def test_put_category_updates_single_split_and_bumps_version_by_one(
    headers: dict[str, str], account_id: str
) -> None:
    tx = _new_transaction(headers, account_id)
    grocery = _category_id(headers, GROCERY)

    response = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": grocery, "expected_version": tx["version"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category_id"] == grocery
    assert body["splits"][0]["category_id"] == grocery
    # Genau ein Schritt, damit der Client seine Version deterministisch
    # fortschreiben kann.
    assert body["version"] == tx["version"] + 1
    assert response.headers["ETag"] == f'"{body["version"]}"'

    # If-Match ist gleichwertig zum Body-Feld, und null entfernt die Kategorie.
    cleared = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": None},
        headers={**headers, "If-Match": f'"{body["version"]}"'},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["category_id"] is None
    assert cleared.json()["version"] == body["version"] + 1


def test_put_category_rejects_transaction_with_multiple_splits(
    headers: dict[str, str], account_id: str
) -> None:
    split_tx = _split_transaction(headers, account_id)
    grocery = _category_id(headers, GROCERY)

    response = client.put(
        f"/v1/transactions/{split_tx['id']}/category",
        json={"category_id": grocery, "expected_version": split_tx["version"]},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "transaction_has_multiple_splits"

    # Die vorhandene Aufteilung bleibt unangetastet.
    after = _detail(headers, split_tx["id"])
    assert len(after["splits"]) == 2
    assert all(s["category_id"] is None for s in after["splits"])
    assert after["version"] == split_tx["version"]


def test_put_category_with_stale_version_returns_conflict_body(
    headers: dict[str, str], account_id: str
) -> None:
    tx = _new_transaction(headers, account_id)
    grocery = _category_id(headers, GROCERY)
    restaurant = _category_id(headers, RESTAURANT)

    first = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": grocery, "expected_version": tx["version"]},
        headers=headers,
    )
    assert first.status_code == 200

    stale = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": restaurant, "expected_version": tx["version"]},
        headers=headers,
    )
    assert stale.status_code == 409
    body = stale.json()
    assert body["detail"] == "version_conflict"
    assert body["expected_version"] == tx["version"]
    assert body["current_version"] == tx["version"] + 1
    assert body["current"]["category_id"] == grocery
    assert stale.headers["ETag"] == f'"{body["current_version"]}"'

    # Der abgelehnte Request darf nichts geschrieben haben.
    persisted = _detail(headers, tx["id"])
    assert persisted["category_id"] == grocery
    assert persisted["version"] == tx["version"] + 1


def test_put_category_on_unknown_or_archived_transaction_is_404(
    headers: dict[str, str], account_id: str
) -> None:
    grocery = _category_id(headers, GROCERY)

    missing = client.put(
        f"/v1/transactions/{uuid.uuid4()}/category",
        json={"category_id": grocery},
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "transaction_not_found"

    tx = _new_transaction(headers, account_id)
    assert client.delete(f"/v1/transactions/{tx['id']}", headers=headers).status_code == 204

    archived = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": grocery},
        headers=headers,
    )
    assert archived.status_code == 404
    assert archived.json()["detail"] == "transaction_not_found"


def test_put_category_rejects_foreign_household_category(
    headers: dict[str, str], account_id: str
) -> None:
    tx = _new_transaction(headers, account_id)
    foreign_headers = _household_headers("cat-foreign")
    foreign_category = _category_id(foreign_headers, GROCERY)

    response = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": foreign_category, "expected_version": tx["version"]},
        headers=headers,
    )
    # 404 statt 403: die Existenz einer fremden Kategorie wird nicht verraten.
    assert response.status_code == 404
    assert response.json()["detail"] == "category_not_found"
    assert _detail(headers, tx["id"])["version"] == tx["version"]


async def test_put_category_rejects_archived_category(
    headers: dict[str, str], account_id: str
) -> None:
    tx = _new_transaction(headers, account_id)
    fuel = _category_id(headers, FUEL)
    await _archive_category(fuel)

    response = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": fuel, "expected_version": tx["version"]},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "category_inactive"
    # Die Version darf durch den abgelehnten Request nicht gestiegen sein.
    assert _detail(headers, tx["id"])["version"] == tx["version"]


async def test_put_category_canonicalizes_merged_category(
    headers: dict[str, str], account_id: str
) -> None:
    """Verspaeteter Offline-Request mit der ID einer inzwischen gemergten
    Kategorie: die fachliche Absicht ist eindeutig, geschrieben wird das aktive
    Merge-Ziel."""
    tx = _new_transaction(headers, account_id)
    source = _category_id(headers, FUEL)
    target = _category_id(headers, GROCERY)
    await _merge_category(source, target)

    response = client.put(
        f"/v1/transactions/{tx['id']}/category",
        json={"category_id": source, "expected_version": tx["version"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["category_id"] == target
    assert response.json()["splits"][0]["category_id"] == target


def test_create_and_replace_splits_route_category_through_resolver(
    headers: dict[str, str], account_id: str
) -> None:
    """Die Bestandspfade duerfen keine haushaltsfremde Kategorie mehr
    annehmen."""
    foreign_headers = _household_headers("cat-foreign2")
    foreign_category = _category_id(foreign_headers, GROCERY)

    created = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": "2026-07-01",
            "amount_minor": -1000,
            "category_id": foreign_category,
        },
        headers=headers,
    )
    assert created.status_code == 404
    assert created.json()["detail"] == "category_not_found"

    tx = _new_transaction(headers, account_id, amount_minor=-5000)
    splits = client.put(
        f"/v1/transactions/{tx['id']}/splits",
        json={
            "amount_minor": -5000,
            "splits": [
                {"amount_minor": -3000, "category_id": foreign_category},
                {"amount_minor": -2000},
            ],
            "expected_version": tx["version"],
        },
        headers=headers,
    )
    assert splits.status_code == 404
    # Kein Teilzustand: weder Version noch Splits duerfen sich geaendert haben.
    unchanged = _detail(headers, tx["id"])
    assert unchanged["version"] == tx["version"]
    assert len(unchanged["splits"]) == 1


# ---------------------------------------------------------------------------
# POST /v1/transactions/bulk-category/preview|apply
# ---------------------------------------------------------------------------


def _items_by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["transaction_id"]: item for item in result["items"]}


def test_bulk_preview_reports_outcomes_and_apply_writes_only_applied(
    headers: dict[str, str], account_id: str
) -> None:
    grocery = _category_id(headers, GROCERY)
    restaurant = _category_id(headers, RESTAURANT)

    to_apply = _new_transaction(headers, account_id)
    already = _new_transaction(headers, account_id, category_id=grocery)
    split_tx = _split_transaction(headers, account_id)
    conflicting = _new_transaction(headers, account_id, category_id=restaurant)
    archived = _new_transaction(headers, account_id)
    client.delete(f"/v1/transactions/{archived['id']}", headers=headers)
    unknown_id = str(uuid.uuid4())

    # Fremde Aenderung an genau einer Buchung, waehrend der Client seine alte
    # Version noch mitschickt.
    stale_version = conflicting["version"]
    bumped = client.patch(
        f"/v1/transactions/{conflicting['id']}",
        json={"note": "anderes Geraet", "expected_version": stale_version},
        headers=headers,
    )
    assert bumped.status_code == 200

    payload = {
        "transaction_ids": [
            to_apply["id"],
            already["id"],
            split_tx["id"],
            conflicting["id"],
            archived["id"],
            unknown_id,
        ],
        "category_id": grocery,
        "expected_versions": {
            to_apply["id"]: to_apply["version"],
            already["id"]: already["version"],
            conflicting["id"]: stale_version,
        },
    }

    preview = client.post(
        "/v1/transactions/bulk-category/preview", json=payload, headers=headers
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    preview_items = _items_by_id(preview_body)
    assert preview_items[to_apply["id"]]["status"] == "applied"
    assert preview_items[already["id"]] == {
        "transaction_id": already["id"],
        "status": "unchanged",
        "reason": "already_assigned",
        "current_version": already["version"],
    }
    assert preview_items[split_tx["id"]]["reason"] == "split_transaction"
    assert preview_items[split_tx["id"]]["status"] == "skipped"
    assert preview_items[conflicting["id"]]["status"] == "conflict"
    assert preview_items[conflicting["id"]]["reason"] == "version_conflict"
    assert preview_items[conflicting["id"]]["current_version"] == stale_version + 1
    assert preview_items[archived["id"]]["reason"] == "archived_transaction"
    assert preview_items[unknown_id]["reason"] == "not_found"
    assert preview_body["applied"] == 1
    assert preview_body["unchanged"] == 1
    assert preview_body["skipped"] == 3
    assert preview_body["conflict"] == 1

    # Die Vorschau darf nichts geschrieben haben - die Request-Transaktion wird
    # committet, ein versehentliches UPDATE bliebe also bestehen.
    still_untouched = _detail(headers, to_apply["id"])
    assert still_untouched["category_id"] is None
    assert still_untouched["version"] == to_apply["version"]

    apply = client.post("/v1/transactions/bulk-category/apply", json=payload, headers=headers)
    assert apply.status_code == 200, apply.text
    apply_body = apply.json()
    apply_items = _items_by_id(apply_body)
    assert apply_body["applied"] == 1
    assert apply_body["unchanged"] == 1
    assert apply_body["skipped"] == 3
    assert apply_body["conflict"] == 1
    assert apply_items[to_apply["id"]]["current_version"] == to_apply["version"] + 1

    applied_detail = _detail(headers, to_apply["id"])
    assert applied_detail["category_id"] == grocery
    assert applied_detail["version"] == to_apply["version"] + 1

    # Der Konflikt hat die uebrigen gueltigen Aenderungen nicht blockiert und
    # die konfliktbehaftete Buchung selbst nicht veraendert.
    conflicted_detail = _detail(headers, conflicting["id"])
    assert conflicted_detail["category_id"] == restaurant
    assert conflicted_detail["version"] == stale_version + 1

    # Split-Buchungen werden nie pauschal geaendert.
    split_detail = _detail(headers, split_tx["id"])
    assert all(s["category_id"] is None for s in split_detail["splits"])


def test_bulk_apply_is_safe_to_retry_with_refreshed_versions(
    headers: dict[str, str], account_id: str
) -> None:
    grocery = _category_id(headers, GROCERY)
    first_tx = _new_transaction(headers, account_id)
    second_tx = _new_transaction(headers, account_id)
    transaction_ids = [first_tx["id"], second_tx["id"]]

    first_run = client.post(
        "/v1/transactions/bulk-category/apply",
        json={
            "transaction_ids": transaction_ids,
            "category_id": grocery,
            "expected_versions": {
                first_tx["id"]: first_tx["version"],
                second_tx["id"]: second_tx["version"],
            },
        },
        headers=headers,
    ).json()
    assert first_run["applied"] == 2

    refreshed = {tx_id: _detail(headers, tx_id)["version"] for tx_id in transaction_ids}
    retry = client.post(
        "/v1/transactions/bulk-category/apply",
        json={
            "transaction_ids": transaction_ids,
            "category_id": grocery,
            "expected_versions": refreshed,
        },
        headers=headers,
    ).json()
    assert retry["applied"] == 0
    assert retry["unchanged"] == 2
    assert all(item["reason"] == "already_assigned" for item in retry["items"])
    # Ein Wiederholungslauf darf die Version nicht weiterzaehlen, sonst
    # erzeugte er auf anderen Geraeten Phantomkonflikte.
    assert {tx_id: _detail(headers, tx_id)["version"] for tx_id in transaction_ids} == refreshed


def test_bulk_removing_category_and_missing_expected_versions(
    headers: dict[str, str], account_id: str
) -> None:
    """Ohne expected_versions wird ungeprueft geschrieben (frisch gelesener
    Stand); category_id null entfernt die Kategorie."""
    grocery = _category_id(headers, GROCERY)
    tx = _new_transaction(headers, account_id, category_id=grocery)

    result = client.post(
        "/v1/transactions/bulk-category/apply",
        json={"transaction_ids": [tx["id"]], "category_id": None},
        headers=headers,
    ).json()
    assert result["applied"] == 1
    assert _detail(headers, tx["id"])["category_id"] is None


async def test_bulk_with_archived_target_category_fails_as_caller_error(
    headers: dict[str, str], account_id: str
) -> None:
    """Eine archivierte Zielkategorie ist kein Grund, 500 Buchungen einzeln zu
    ueberspringen - der ganze Vorgang wird abgelehnt."""
    tx = _new_transaction(headers, account_id)
    fuel = _category_id(headers, FUEL)
    await _archive_category(fuel)

    payload = {"transaction_ids": [tx["id"]], "category_id": fuel}
    for endpoint in ("preview", "apply"):
        response = client.post(
            f"/v1/transactions/bulk-category/{endpoint}", json=payload, headers=headers
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "category_inactive"

    assert _detail(headers, tx["id"])["category_id"] is None


def test_bulk_rejects_more_than_max_transactions(headers: dict[str, str]) -> None:
    response = client.post(
        "/v1/transactions/bulk-category/apply",
        json={"transaction_ids": [str(uuid.uuid4()) for _ in range(501)], "category_id": None},
        headers=headers,
    )
    assert response.status_code == 422
