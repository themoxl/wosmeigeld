"""Integrationstests der Personenverwaltung gegen das lokale Supabase -
kein Mocking (Muster: `test_reserves_api.py`), inklusive Haushaltsisolation.

Der Schwerpunkt liegt auf den beiden Festlegungen der Stufe: Namen sind je
Haushalt eindeutig ohne Ruecksicht auf Gross- und Kleinschreibung, und
Loeschen ist ein Soft-Delete. Beides ist nur gegen die echte Datenbank
pruefbar - der partielle Unique-Index und der zusammengesetzte Fremdschluessel
sind genau die Teile, die ein Mock wegdefinieren wuerde.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_household_isolation import _auth_headers, _signup, _user_id_from_token

client = TestClient(app)


def _headers(name: str) -> dict[str, str]:
    token = _signup(f"people-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-7")
    household = client.post(
        "/v1/households", json={"name": name}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def household_headers() -> dict[str, str]:
    return _headers("Personen")


@pytest.fixture()
def other_headers() -> dict[str, str]:
    return _headers("Fremder Haushalt")


def _create_person(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Person B"}
    body.update(overrides)
    response = client.post("/v1/people", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_create_list_update_and_sort_order(household_headers: dict[str, str]) -> None:
    person_b = _create_person(household_headers, name="Person B", sort_order=2)
    person_a = _create_person(household_headers, name="Person A", sort_order=1)

    listed = client.get("/v1/people", headers=household_headers)
    assert listed.status_code == 200, listed.text
    # Sortierung nach sort_order, nicht nach Anlagezeitpunkt.
    assert [item["name"] for item in listed.json()] == ["Person A", "Person B"]
    assert all(item["is_active"] for item in listed.json())
    assert person_a["member_user_id"] is None

    renamed = client.patch(
        f"/v1/people/{person_b['id']}",
        json={"name": "  Person B   Maria  ", "sort_order": 0},
        headers=household_headers,
    )
    assert renamed.status_code == 200, renamed.text
    # Leerraum wird vereinheitlicht: sonst waeren "Person B  Maria" und
    # "Person B Maria" zwei verschiedene lower(name) und der Index liesse beide zu.
    assert renamed.json()["name"] == "Person B Maria"
    assert renamed.json()["sort_order"] == 0
    assert renamed.json()["updated_at"] >= renamed.json()["created_at"]


def test_same_name_in_different_spelling_is_rejected(
    household_headers: dict[str, str],
) -> None:
    """Der Kern der Stufe: "Person B" und "person b" sind derselbe Mensch."""
    _create_person(household_headers, name="Person B")

    duplicate = client.post("/v1/people", json={"name": "person b"}, headers=household_headers)
    assert duplicate.status_code == 409, duplicate.text
    # Verstaendliche Meldung statt eines abgebrochenen Statements: der Nutzer
    # soll erfahren, warum die Schreibweise nichts aendert.
    assert "bereits eine Person" in duplicate.json()["detail"]

    padded = client.post(
        "/v1/people", json={"name": "  PERSON B  "}, headers=household_headers
    )
    assert padded.status_code == 409, padded.text

    # Ein wirklich anderer Name bleibt erlaubt - der Index normalisiert nur
    # Gross- und Kleinschreibung, er raet nicht.
    distinct = client.post("/v1/people", json={"name": "Person B M."}, headers=household_headers)
    assert distinct.status_code == 201, distinct.text


def test_rename_into_an_existing_name_is_rejected(
    household_headers: dict[str, str],
) -> None:
    _create_person(household_headers, name="Person B")
    person_a = _create_person(household_headers, name="Person A")

    collision = client.patch(
        f"/v1/people/{person_a['id']}", json={"name": "PERSON B"}, headers=household_headers
    )
    assert collision.status_code == 409, collision.text

    # Umbenennen auf den eigenen Namen ist kein Konflikt.
    same = client.patch(
        f"/v1/people/{person_a['id']}", json={"name": "person a"}, headers=household_headers
    )
    assert same.status_code == 200, same.text
    assert same.json()["name"] == "person a"


def test_delete_is_a_soft_delete_and_frees_the_name(
    household_headers: dict[str, str],
) -> None:
    person_b = _create_person(household_headers, name="Person B")
    contract = client.post(
        "/v1/contracts",
        json={
            "name": "Gehalt Person B",
            "amount_minor": 250_000,
            "frequency": "monthly",
            "next_due_date": "2026-09-01",
            "direction": "income",
            "contract_type": "income",
            "income_kind": "salary",
            "person_id": person_b["id"],
        },
        headers=household_headers,
    )
    assert contract.status_code == 201, contract.text

    deleted = client.delete(f"/v1/people/{person_b['id']}", headers=household_headers)
    assert deleted.status_code == 204, deleted.text
    # Idempotent: zweimal loeschen ist kein Fehler.
    assert (
        client.delete(f"/v1/people/{person_b['id']}", headers=household_headers).status_code
        == 204
    )

    assert client.get("/v1/people", headers=household_headers).json() == []
    inactive = client.get(
        "/v1/people", params={"include_inactive": True}, headers=household_headers
    ).json()
    assert [(item["name"], item["is_active"]) for item in inactive] == [("Person B", False)]

    # Der Vertrag zeigt weiterhin auf die Person - ein hartes DELETE haette die
    # Zuordnung ueber "on delete set null" still fallen lassen.
    after = client.get(f"/v1/contracts/{contract.json()['id']}", headers=household_headers)
    assert after.json()["person_id"] == person_b["id"]

    # Und der Name ist wieder frei, weil der Unique-Index nur aktive Zeilen umfasst.
    again = client.post("/v1/people", json={"name": "person b"}, headers=household_headers)
    assert again.status_code == 201, again.text
    assert again.json()["id"] != person_b["id"]


def test_a_deleted_person_gets_no_new_contracts(household_headers: dict[str, str]) -> None:
    person_b = _create_person(household_headers, name="Person B")
    assert (
        client.delete(f"/v1/people/{person_b['id']}", headers=household_headers).status_code
        == 204
    )

    rejected = client.post(
        "/v1/contracts",
        json={
            "name": "Gehalt Person B",
            "amount_minor": 250_000,
            "frequency": "monthly",
            "next_due_date": "2026-09-01",
            "direction": "income",
            "person_id": person_b["id"],
        },
        headers=household_headers,
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == "person_inactive"


def test_person_of_another_household_is_invisible_and_unusable(
    household_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    foreign = _create_person(other_headers, name="Fremde Person")

    assert client.get("/v1/people", headers=household_headers).json() == []
    # 404 statt 403: die Existenz einer fremden Ressource wird nicht verraten.
    assert (
        client.patch(
            f"/v1/people/{foreign['id']}", json={"name": "Neu"}, headers=household_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/v1/people/{foreign['id']}", headers=household_headers).status_code == 404
    )

    # Der zusammengesetzte Fremdschluessel ist "initially deferred" - ohne die
    # Pruefung im Schreibpfad floege das erst beim COMMIT auf, also als 500.
    contract = client.post(
        "/v1/contracts",
        json={
            "name": "Gehalt",
            "amount_minor": 100_000,
            "frequency": "monthly",
            "next_due_date": "2026-09-01",
            "direction": "income",
            "person_id": foreign["id"],
        },
        headers=household_headers,
    )
    assert contract.status_code == 404, contract.text

    # Und der fremde Haushalt sieht seine Person unveraendert.
    assert [item["name"] for item in client.get("/v1/people", headers=other_headers).json()] == [
        "Fremde Person"
    ]


def test_the_same_name_may_exist_in_two_households(
    household_headers: dict[str, str], other_headers: dict[str, str]
) -> None:
    """Der Index ist auf `(household_id, lower(name))` - er sperrt Namen
    innerhalb eines Haushalts, nicht global."""
    assert _create_person(household_headers, name="Person B")["name"] == "Person B"
    assert _create_person(other_headers, name="Person B")["name"] == "Person B"


def test_member_user_id_must_belong_to_the_household(
    household_headers: dict[str, str],
) -> None:
    stranger = _signup(f"stranger-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-8")

    rejected = client.post(
        "/v1/people",
        json={"name": "Unbekannt", "member_user_id": _user_id_from_token(stranger)},
        headers=household_headers,
    )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"] == "person_member_unknown"

    # Der eigene Zugang ist dagegen erlaubt und wieder loesbar.
    own_id = client.get("/v1/me", headers=household_headers).json()["user_id"]
    linked = _create_person(household_headers, name="Ich", member_user_id=own_id)
    assert linked["member_user_id"] == own_id
    unlinked = client.patch(
        f"/v1/people/{linked['id']}", json={"member_user_id": None}, headers=household_headers
    )
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["member_user_id"] is None


def test_name_length_and_empty_name_are_rejected(household_headers: dict[str, str]) -> None:
    assert (
        client.post("/v1/people", json={"name": "   "}, headers=household_headers).status_code
        == 422
    )
    assert (
        client.post("/v1/people", json={"name": "A" * 81}, headers=household_headers).status_code
        == 422
    )
