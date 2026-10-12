"""Reversible Zusammenfuehrung und manuelle Buchungszuordnung.

Deckt P5.0 aus docs/VERTRAEGE-FIXKOSTEN-PLAN.md Abschnitt 19.7 ab: eine
Zusammenfuehrung darf nach der Bestaetigung nicht mehr endgueltig sein, und
eine einzelne Fehlzuordnung muss sich ohne Merge korrigieren lassen.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import contract_service
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

Headers = dict[str, str]


def test_every_restorable_column_has_a_cast_type() -> None:
    """Die Spaltenliste waechst automatisch, die Typtabelle nicht.

    `_RESTORABLE_CONTRACT_COLUMNS` leitet sich aus `_SNAPSHOT_COLUMNS` ab und
    nimmt jede neue Spalte von `contracts` von selbst auf.
    `_CONTRACT_COLUMN_TYPES` wird dagegen von Hand gepflegt. Wer eine Spalte
    ergaenzt und den Cast vergisst, merkt es sonst erst, wenn ein Nutzer eine
    Zusammenfuehrung rueckgaengig macht - dort schlaegt der fehlende
    Schluessel als KeyError durch und die Anfrage endet mit 500.

    Genau so sind die drei reserve_*-Spalten durchgerutscht.
    """
    ohne_typ = [
        column
        for column in contract_service._RESTORABLE_CONTRACT_COLUMNS
        if column not in contract_service._CONTRACT_COLUMN_TYPES
    ]

    assert ohne_typ == [], f"ohne Eintrag in _CONTRACT_COLUMN_TYPES: {ohne_typ}"


@pytest.fixture()
def household_headers() -> Headers:
    token = _signup(f"cmu-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Undo-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: Headers) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return str(account["id"])


def _create(headers: Headers, name: str, amount_minor: int, days_ago: int) -> dict[str, Any]:
    response = client.post(
        "/v1/contracts",
        json={
            "name": name,
            "amount_minor": amount_minor,
            "frequency": "monthly",
            "next_due_date": (date.today() - timedelta(days=days_ago)).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _execute(sql: str, **params: Any) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(text(sql), params)
        mapped = [dict(row._mapping) for row in result]
        await session.commit()
        return mapped


async def _insert_occurrence(
    household_id: str,
    contract_id: str,
    due_date: date,
    status: str,
    *,
    transaction_id: str | None = None,
) -> None:
    await _execute(
        "insert into contract_occurrences "
        "(household_id, contract_id, due_date, status, transaction_id) "
        "values (:household_id, :contract_id, :due_date, :status, "
        "cast(:transaction_id as uuid))",
        household_id=household_id,
        contract_id=contract_id,
        due_date=due_date,
        status=status,
        transaction_id=transaction_id,
    )


async def _insert_transaction(
    household_id: str, account_id: str, booked_date: date, amount_minor: int, payee_label: str
) -> str:
    rows = await _rows(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "payee_label) values (:household_id, :account_id, :booked_date, :amount_minor, "
        ":payee_label) returning id",
        household_id=household_id,
        account_id=account_id,
        booked_date=booked_date,
        amount_minor=amount_minor,
        payee_label=payee_label,
    )
    return str(rows[0]["id"])


def _merge(
    headers: Headers, target_id: str, source_ids: list[str], request_id: str
) -> dict[str, Any]:
    response = client.post(
        "/v1/contracts/merge",
        json={"target_id": target_id, "source_ids": source_ids, "request_id": request_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_restores_sources_history_and_occurrences_exactly(
    household_headers: Headers,
) -> None:
    household_id = household_headers["X-Household-Id"]
    target = _create(household_headers, "Musterfunk 39,95", 3995, 5)
    source = _create(household_headers, "Musterfunk 49,95", 4995, 90)
    occ_date = date.today() - timedelta(days=90)
    await _insert_occurrence(household_id, source["id"], occ_date, "matched")

    merged = _merge(household_headers, target["id"], [source["id"]], "merge-undo-1")
    merge_id = merged["merge_id"]
    assert merge_id is not None

    preview = client.get(
        f"/v1/contracts/merges/{merge_id}/undo-preview", headers=household_headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["can_undo"] is True
    assert preview.json()["already_undone"] is False

    undo = client.post(f"/v1/contracts/merges/{merge_id}/undo", headers=household_headers)
    assert undo.status_code == 200, undo.text
    assert undo.json()["restored_source_count"] == 1
    assert undo.json()["contract"]["amount_minor"] == 3995

    remaining_ids = {
        item["id"] for item in client.get("/v1/contracts", headers=household_headers).json()
    }
    assert remaining_ids == {target["id"], source["id"]}

    source_occurrences = client.get(
        f"/v1/contracts/{source['id']}/occurrences", headers=household_headers
    ).json()
    assert [item["due_date"] for item in source_occurrences] == [occ_date.isoformat()]

    target_history = client.get(
        f"/v1/contracts/{target['id']}/history", headers=household_headers
    ).json()
    assert [entry["amount_minor"] for entry in target_history] == [3995]

    source_history = client.get(
        f"/v1/contracts/{source['id']}/history", headers=household_headers
    ).json()
    assert [entry["amount_minor"] for entry in source_history] == [4995]

    target_events = client.get(
        f"/v1/contracts/{target['id']}/events", headers=household_headers
    ).json()
    kinds = [event["kind"] for event in target_events]
    assert "merged" in kinds and "merge_undone" in kinds


@pytest.mark.asyncio
async def test_undo_is_idempotent(household_headers: Headers) -> None:
    target = _create(household_headers, "Klarna aktuell", 5800, 5)
    source = _create(household_headers, "Klarna alt", 900, 60)
    merged = _merge(household_headers, target["id"], [source["id"]], "merge-undo-idem")
    merge_id = merged["merge_id"]

    first = client.post(f"/v1/contracts/merges/{merge_id}/undo", headers=household_headers)
    assert first.status_code == 200
    assert first.json()["restored_source_count"] == 1

    second = client.post(f"/v1/contracts/merges/{merge_id}/undo", headers=household_headers)
    assert second.status_code == 200
    assert second.json()["restored_source_count"] == 0

    preview = client.get(
        f"/v1/contracts/merges/{merge_id}/undo-preview", headers=household_headers
    ).json()
    assert preview["already_undone"] is True
    assert preview["can_undo"] is False


@pytest.mark.asyncio
async def test_undo_blocks_on_new_activity_since_merge(household_headers: Headers) -> None:
    household_id = household_headers["X-Household-Id"]
    target = _create(household_headers, "Strom neu", 12300, 5)
    source = _create(household_headers, "Strom alt", 14700, 60)
    merged = _merge(household_headers, target["id"], [source["id"]], "merge-undo-conflict")
    merge_id = merged["merge_id"]

    # Ein spaeterer Abgleichslauf matcht eine neue Faelligkeit auf das Ziel -
    # ein stiller Rollback wuerde diese Zeile mitreissen.
    await _insert_occurrence(household_id, target["id"], date.today(), "matched")

    preview = client.get(
        f"/v1/contracts/merges/{merge_id}/undo-preview", headers=household_headers
    ).json()
    assert preview["can_undo"] is False
    assert "new_activity_since_merge" in preview["conflict_reasons"]
    assert preview["new_occurrence_count"] == 1

    undo = client.post(f"/v1/contracts/merges/{merge_id}/undo", headers=household_headers)
    assert undo.status_code == 409
    assert undo.json()["detail"] == "contract_merge_undo_conflict"


def test_undo_unknown_merge_is_404(household_headers: Headers) -> None:
    response = client.post(
        f"/v1/contracts/merges/{uuid.uuid4()}/undo", headers=household_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "contract_merge_operation_not_found"


@pytest.mark.asyncio
async def test_undo_ladder_merge_restores_all_three_price_steps(
    household_headers: Headers,
) -> None:
    """Der Musterfunk-Fall rueckwaerts: drei Preisstufen zusammengefuehrt, dann
    wieder auseinandergezogen - genau der Ablauf, den ein Fehlklick bei der
    neuen Duplikatkarte (P5.2) verlangt."""
    alt = _create(household_headers, "Musterfunk 49,95", 4995, 120)
    mittel = _create(household_headers, "Musterfunk 43,95", 4395, 60)
    aktuell = _create(household_headers, "Musterfunk 39,95", 3995, 5)

    merged = _merge(
        household_headers, aktuell["id"], [alt["id"], mittel["id"]], "merge-undo-ladder"
    )
    merge_id = merged["merge_id"]

    undo = client.post(f"/v1/contracts/merges/{merge_id}/undo", headers=household_headers)
    assert undo.status_code == 200, undo.text
    assert undo.json()["restored_source_count"] == 2

    remaining_ids = {
        item["id"] for item in client.get("/v1/contracts", headers=household_headers).json()
    }
    assert remaining_ids == {alt["id"], mittel["id"], aktuell["id"]}

    for contract, amount in ((alt, 4995), (mittel, 4395), (aktuell, 3995)):
        history = client.get(
            f"/v1/contracts/{contract['id']}/history", headers=household_headers
        ).json()
        assert [entry["amount_minor"] for entry in history] == [amount]
        assert history[0]["valid_to"] is None


# ---------------------------------------------------------------------------
# Manuelle Buchungszuordnung ohne Merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_moves_transaction_to_another_contract(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    wrong = _create(household_headers, "Falscher Vertrag", 1999, 5)
    correct = _create(household_headers, "Richtiger Vertrag", 1999, 5)
    booked = date.today() - timedelta(days=5)
    transaction_id = await _insert_transaction(
        household_id, account_id, booked, -1999, "Anbieter X"
    )
    await _insert_occurrence(
        household_id, wrong["id"], booked, "matched", transaction_id=transaction_id
    )

    preview = client.post(
        f"/v1/contracts/transactions/{transaction_id}/reassign/preview",
        json={"target_contract_id": correct["id"]},
        headers=household_headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["current_contract_id"] == wrong["id"]
    assert body["target_contract_id"] == correct["id"]
    assert body["affects_occurrence"] is True

    result = client.post(
        f"/v1/contracts/transactions/{transaction_id}/reassign",
        json={"target_contract_id": correct["id"]},
        headers=household_headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["contract_id"] == correct["id"]

    wrong_occurrences = client.get(
        f"/v1/contracts/{wrong['id']}/occurrences", headers=household_headers
    ).json()
    assert wrong_occurrences == []

    correct_occurrences = client.get(
        f"/v1/contracts/{correct['id']}/occurrences", headers=household_headers
    ).json()
    assert len(correct_occurrences) == 1
    assert correct_occurrences[0]["transaction_id"] == transaction_id
    assert correct_occurrences[0]["status"] == "matched"


@pytest.mark.asyncio
async def test_reassign_to_none_clears_assignment(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    contract = _create(household_headers, "Vertrag", 999, 5)
    booked = date.today() - timedelta(days=3)
    transaction_id = await _insert_transaction(
        household_id, account_id, booked, -999, "Anbieter Y"
    )
    await _insert_occurrence(
        household_id, contract["id"], booked, "matched", transaction_id=transaction_id
    )

    result = client.post(
        f"/v1/contracts/transactions/{transaction_id}/reassign",
        json={"target_contract_id": None},
        headers=household_headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["contract_id"] is None

    occurrences = client.get(
        f"/v1/contracts/{contract['id']}/occurrences", headers=household_headers
    ).json()
    assert occurrences == []

    overrides = await _rows(
        "select contract_id from contract_assignment_overrides "
        "where household_id = :household_id and transaction_id = cast(:tx as uuid)",
        household_id=household_id,
        tx=transaction_id,
    )
    assert len(overrides) == 1
    assert overrides[0]["contract_id"] is None
