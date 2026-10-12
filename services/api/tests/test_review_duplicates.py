"""Die Dublettenliste der Review-Inbox: sichtbare Gegenbuchung und Sammelaktion.

Zwei Maengel, die zusammengehoeren und dieselbe Ursache hatten - die Liste war
nicht benutzbar, also blieb sie liegen:

1. Sie nannte nur die NEUE Zeile und verlangte "Dublette oder nicht", ohne zu
   sagen, wovon. Wer das beantworten wollte, musste in "Alle Buchungen"
   nachschlagen.
2. Jede Zeile war einzeln zu bestaetigen. Ein Import bringt sie zu Dutzenden.

Eine Liste, die weder verstaendlich noch abzuarbeiten ist, wird weggeklickt oder
ignoriert - und dann ist auch die echte Dublette darin verloren.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.sql import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def kopf() -> dict[str, str]:
    token = _signup(f"dup-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    haushalt = client.post(
        "/v1/households", json={"name": "Dublettenhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": haushalt["id"]}


@pytest.fixture()
def konto_id(kopf: dict[str, str]) -> str:
    return str(
        client.post(
            "/v1/accounts",
            json={"name": "Girokonto", "account_type": "checking"},
            headers=kopf,
        ).json()["id"]
    )


async def _sql(query: str, params: dict[str, Any]) -> Any:
    session_factory = get_session_factory()
    async with session_factory() as session:
        ergebnis = await session.execute(text(query), params)
        await session.commit()
        return ergebnis


async def _staging_dublette(
    kopf: dict[str, str], konto_id: str, gegen_transaktion: str, *, anzahl: int = 1
) -> list[str]:
    """Legt einen abgeschlossenen Importlauf mit offenen Dublettenzeilen an.

    Direkt in der Datenbank statt ueber einen echten Import: hier wird die
    ANZEIGE geprueft, nicht die Erkennung - die hat ihre eigenen Tests in
    services/worker/tests/test_imports_dedup.py.
    """
    haushalt = kopf["X-Household-Id"]
    lauf = (
        await _sql(
            "insert into import_runs (household_id, account_id, filename, file_hash, "
            " file_size_bytes, status) "
            "values (cast(:h as uuid), cast(:k as uuid), 'dubletten.csv', :hash, 1, 'ready') "
            "returning id",
            {"h": haushalt, "k": konto_id, "hash": uuid.uuid4().hex},
        )
    ).scalar_one()

    ids: list[str] = []
    for nummer in range(1, anzahl + 1):
        zeile = (
            await _sql(
                "insert into import_rows (household_id, import_run_id, row_number, raw_data, "
                " booked_date, amount_minor, currency, payee_raw, purpose, status, "
                " duplicate_of_transaction_id) "
                "values (cast(:h as uuid), cast(:lauf as uuid), :nr, '{}'::jsonb, "
                "        '2026-04-02', -1999, 'EUR', 'MUSTERKASSE', 'Beitrag April', "
                "        'duplicate_possible', cast(:gegen as uuid)) returning id",
                {"h": haushalt, "lauf": str(lauf), "nr": nummer, "gegen": gegen_transaktion},
            )
        ).scalar_one()
        ids.append(str(zeile))
    return ids


def _buchung(kopf: dict[str, str], konto_id: str) -> str:
    return str(
        client.post(
            "/v1/transactions",
            json={
                "account_id": konto_id,
                "booked_date": "2026-04-02",
                "amount_minor": -1999,
                "payee_label": "MUSTER KRANKENVERSICHERUNG",
            },
            headers=kopf,
        ).json()["id"]
    )


async def test_a_duplicate_shows_the_booking_it_is_a_duplicate_of(
    kopf: dict[str, str], konto_id: str
) -> None:
    """Ohne die Gegenbuchung ist die Frage nicht zu beantworten."""
    vorhanden = _buchung(kopf, konto_id)
    await _staging_dublette(kopf, konto_id, vorhanden)

    inbox = client.get("/v1/review", headers=kopf).json()
    dubletten = inbox["possible_duplicates"]
    assert len(dubletten) == 1

    gegen = dubletten[0]["duplicate_of"]
    assert gegen is not None, "Die Gegenbuchung fehlt - genau der gemeldete Mangel."
    assert gegen["transaction_id"] == vorhanden
    assert gegen["booked_date"] == "2026-04-02"
    assert gegen["amount_minor"] == -1999
    assert gegen["payee_label"] == "MUSTER KRANKENVERSICHERUNG"
    # Das Konto gehoert dazu: dieselbe Buchung auf einem anderen Konto waere
    # eine ganz andere Lage.
    assert gegen["account_name"] == "Girokonto"


async def test_a_duplicate_of_a_removed_booking_shows_no_counterpart(
    kopf: dict[str, str], konto_id: str
) -> None:
    """Wurde die Gegenbuchung entfernt, ist die Warnung gegenstandslos - dann
    darf die Oberflaeche keine Buchung vorspiegeln, die es nicht mehr gibt."""
    vorhanden = _buchung(kopf, konto_id)
    await _staging_dublette(kopf, konto_id, vorhanden)
    await _sql(
        "update transactions set is_archived = true where id = cast(:id as uuid)",
        {"id": vorhanden},
    )

    inbox = client.get("/v1/review", headers=kopf).json()
    assert inbox["possible_duplicates"][0]["duplicate_of"] is None


async def test_many_duplicates_can_be_confirmed_at_once(
    kopf: dict[str, str], konto_id: str
) -> None:
    vorhanden = _buchung(kopf, konto_id)
    zeilen = await _staging_dublette(kopf, konto_id, vorhanden, anzahl=5)

    antwort = client.post(
        "/v1/review/accept/duplicates",
        json={"import_row_ids": zeilen},
        headers=kopf,
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {"confirmed": 5, "skipped": 0}

    # Danach ist die Liste leer und die Zeilen sind erledigt.
    assert client.get("/v1/review", headers=kopf).json()["possible_duplicates"] == []


async def test_confirming_twice_counts_instead_of_failing(
    kopf: dict[str, str], konto_id: str
) -> None:
    """Eine veraltete Liste soll den Nutzer nicht von vorn anfangen lassen."""
    vorhanden = _buchung(kopf, konto_id)
    zeilen = await _staging_dublette(kopf, konto_id, vorhanden, anzahl=3)

    client.post("/v1/review/accept/duplicates", json={"import_row_ids": zeilen}, headers=kopf)
    zweiter = client.post(
        "/v1/review/accept/duplicates", json={"import_row_ids": zeilen}, headers=kopf
    ).json()
    assert zweiter == {"confirmed": 0, "skipped": 3}


def test_an_empty_selection_is_rejected(kopf: dict[str, str]) -> None:
    antwort = client.post("/v1/review/accept/duplicates", json={"import_row_ids": []}, headers=kopf)
    assert antwort.status_code == 422


def _resolve_input(pair: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "first_transaction_id": pair["first"]["transaction_id"],
        "second_transaction_id": pair["second"]["transaction_id"],
        "first_version": pair["first"]["version"],
        "second_version": pair["second"]["version"],
        "action": action,
    }


def test_already_booked_duplicates_are_visible_without_an_import(kopf, konto_id):
    first, second = _buchung(kopf, konto_id), _buchung(kopf, konto_id)
    scan = client.get("/v1/review/duplicates/scan", headers=kopf)
    assert scan.status_code == 200, scan.text
    result = scan.json()
    assert result["scanned_count"] == 2
    assert result["total_count"] == 1
    assert {result["items"][0][key]["transaction_id"] for key in ("first", "second")} == {
        first,
        second,
    }
    inbox = client.get("/v1/review", headers=kopf).json()
    assert inbox["possible_duplicates"] == []
    assert inbox["booked_duplicates"] == result["items"]
    assert inbox["booked_duplicate_count"] == 1
    # Rechecking does not create transactions or duplicate review items.
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json() == result


def test_keep_both_survives_rescan_and_archive_removes_only_chosen_booking(kopf, konto_id):
    _buchung(kopf, konto_id)
    _buchung(kopf, konto_id)
    pair = client.get("/v1/review/duplicates/scan", headers=kopf).json()["items"][0]
    response = client.post(
        "/v1/review/duplicates/resolve", headers=kopf, json=_resolve_input(pair, "keep_both")
    )
    assert response.status_code == 204, response.text
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["total_count"] == 0
    # A third copy must still be detected, despite the previous decision.
    third = _buchung(kopf, konto_id)
    scan = client.get("/v1/review/duplicates/scan", headers=kopf).json()
    assert scan["total_count"] == 2
    pair = scan["items"][0]
    action = "archive_first" if pair["first"]["transaction_id"] == third else "archive_second"
    response = client.post(
        "/v1/review/duplicates/resolve", headers=kopf, json=_resolve_input(pair, action)
    )
    assert response.status_code == 204, response.text
    scan = client.get("/v1/review/duplicates/scan", headers=kopf).json()
    assert scan["scanned_count"] == 2
    assert scan["total_count"] == 0


def test_resolution_checks_both_versions_before_archiving(kopf, konto_id):
    _buchung(kopf, konto_id)
    _buchung(kopf, konto_id)
    pair = client.get("/v1/review/duplicates/scan", headers=kopf).json()["items"][0]
    payload = _resolve_input(pair, "archive_second")
    payload["second_version"] += 1
    response = client.post("/v1/review/duplicates/resolve", headers=kopf, json=payload)
    assert response.status_code == 409
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["scanned_count"] == 2


async def test_committed_imports_are_checked_with_purpose_even_if_payee_missing(kopf, konto_id):
    first, second = _buchung(kopf, konto_id), _buchung(kopf, konto_id)
    await _sql(
        "update transactions set purpose_raw = 'Beitrag April', source = 'import' "
        "where id in (cast(:a as uuid), cast(:b as uuid))",
        {"a": first, "b": second},
    )
    await _sql(
        "update transactions set payee_label = null where id = cast(:id as uuid)", {"id": second}
    )
    assert client.get("/v1/review", headers=kopf).json()["booked_duplicate_count"] == 1


def test_cannot_resolve_bookings_from_another_household(kopf, konto_id):
    _buchung(kopf, konto_id)
    _buchung(kopf, konto_id)
    pair = client.get("/v1/review/duplicates/scan", headers=kopf).json()["items"][0]
    token = _signup(f"dup-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    other = client.post(
        "/v1/households", json={"name": "Anderer Haushalt"}, headers=_auth_headers(token)
    ).json()
    headers = {**_auth_headers(token), "X-Household-Id": other["id"]}
    assert client.get("/v1/review/duplicates/scan", headers=headers).json()["scanned_count"] == 0
    response = client.post(
        "/v1/review/duplicates/resolve",
        headers=headers,
        json=_resolve_input(pair, "archive_second"),
    )
    assert response.status_code == 404
    response = client.post(
        "/v1/review/duplicates/resolve-bulk",
        headers=headers,
        json={"items": [_resolve_input(pair, "archive_second")]},
    )
    assert response.status_code == 404


async def test_viewer_can_scan_but_cannot_resolve(kopf, konto_id):
    from tests.test_household_isolation import _user_id_from_token

    _buchung(kopf, konto_id)
    _buchung(kopf, konto_id)
    pair = client.get("/v1/review/duplicates/scan", headers=kopf).json()["items"][0]
    token = _signup(f"dup-viewer-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-9")
    await _sql(
        "insert into household_members (household_id, user_id, role, status) "
        "values (cast(:household as uuid), cast(:user_id as uuid), 'viewer', 'active')",
        {"household": kopf["X-Household-Id"], "user_id": _user_id_from_token(token)},
    )
    headers = {**_auth_headers(token), "X-Household-Id": kopf["X-Household-Id"]}
    assert client.get("/v1/review/duplicates/scan", headers=headers).json()["total_count"] == 1
    for action in ("keep_both", "archive_first", "archive_second"):
        response = client.post(
            "/v1/review/duplicates/resolve", headers=headers, json=_resolve_input(pair, action)
        )
        assert response.status_code == 403
        assert (
            client.post(
                "/v1/review/duplicates/resolve-bulk",
                headers=headers,
                json={"items": [_resolve_input(pair, action)]},
            ).status_code
            == 403
        )
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["total_count"] == 1


async def _bulk_pairs(kopf, konto_id, count=2):
    await _sql(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "currency, payee_label, source) "
        "select cast(:h as uuid), cast(:a as uuid), date '2026-04-02', -1999-n, "
        "'EUR', 'Sammeltest', 'manual' "
        "from generate_series(1, :count) n cross join generate_series(1, 2) copies",
        {"h": kopf["X-Household-Id"], "a": konto_id, "count": count},
    )
    return client.get("/v1/review/duplicates/scan", headers=kopf).json()["items"]


async def test_bulk_archives_169_pairs_in_one_request(kopf, konto_id):
    pairs = await _bulk_pairs(kopf, konto_id, 169)
    assert len(pairs) == 169
    response = client.post(
        "/v1/review/duplicates/resolve-bulk",
        headers=kopf,
        json={"items": [_resolve_input(pair, "archive_second") for pair in pairs]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"processed_count": 169, "archived_count": 169, "kept_count": 0}
    scan = client.get("/v1/review/duplicates/scan", headers=kopf).json()
    assert scan["scanned_count"] == 169
    assert scan["total_count"] == 0
    remaining = (
        (
            await _sql(
                "select id from transactions where household_id = :h and not is_archived",
                {"h": kopf["X-Household-Id"]},
            )
        )
        .scalars()
        .all()
    )
    assert {str(tx) for tx in remaining} == {pair["first"]["transaction_id"] for pair in pairs}


async def test_bulk_can_keep_multiple_pairs_without_archiving(kopf, konto_id):
    pairs = await _bulk_pairs(kopf, konto_id)
    response = client.post(
        "/v1/review/duplicates/resolve-bulk",
        headers=kopf,
        json={"items": [_resolve_input(pair, "keep_both") for pair in pairs]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"processed_count": 2, "archived_count": 0, "kept_count": 2}
    scan = client.get("/v1/review/duplicates/scan", headers=kopf).json()
    assert scan["scanned_count"] == 4 and scan["total_count"] == 0


async def test_bulk_stale_pair_leaves_entire_selection_untouched(kopf, konto_id):
    pairs = await _bulk_pairs(kopf, konto_id)
    items = [_resolve_input(pair, "archive_second") for pair in pairs]
    items[-1]["second_version"] += 1
    response = client.post(
        "/v1/review/duplicates/resolve-bulk", headers=kopf, json={"items": items}
    )
    assert response.status_code == 409
    scan = client.get("/v1/review/duplicates/scan", headers=kopf).json()
    assert scan["scanned_count"] == 4 and scan["total_count"] == 2


async def test_bulk_detaches_import_references_and_supports_earlier_side(kopf, konto_id):
    pairs = await _bulk_pairs(kopf, konto_id)
    removed = pairs[0]["first"]["transaction_id"]
    staging = await _staging_dublette(kopf, konto_id, removed, anzahl=2)
    response = client.post(
        "/v1/review/duplicates/resolve-bulk",
        headers=kopf,
        json={"items": [_resolve_input(pair, "archive_first") for pair in pairs]},
    )
    assert response.status_code == 200, response.text
    refs = (
        (
            await _sql(
                "select duplicate_of_transaction_id from import_rows "
                "where id = any(cast(:ids as uuid[]))",
                {"ids": staging},
            )
        )
        .scalars()
        .all()
    )
    assert refs == [None, None]
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["scanned_count"] == 2


def test_bulk_triple_copy_retains_one_booking(kopf, konto_id):
    for _ in range(3):
        _buchung(kopf, konto_id)
    pairs = client.get("/v1/review/duplicates/scan", headers=kopf).json()["items"]
    response = client.post(
        "/v1/review/duplicates/resolve-bulk",
        headers=kopf,
        json={"items": [_resolve_input(pair, "archive_second") for pair in pairs]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"processed_count": 3, "archived_count": 2, "kept_count": 0}
    assert client.get("/v1/review/duplicates/scan", headers=kopf).json()["scanned_count"] == 1
