"""Zusammenfuehren von Vertraegen.

Der typische Fall: derselbe Musterfunk-Vertrag steht dreimal in der Liste,
weil eine Preisanpassung fuer die Erkennung wie ein neuer Betrag aussah. Nach
dem Zusammenfuehren ist es ein Vertrag mit einer Preistreppe.

Zwei Unique-Indizes machen den Vorgang heikel und werden hier ausdruecklich
geprueft: `contract_occurrences (contract_id, due_date)` und die eine offene
Preisstufe je Vertrag.

Der zweite Teil ab "Vertragsschluessel" deckt P5.3/Teil B ab: die
Zusammenfuehrung setzt den Vertragsschluessel deterministisch aus den
Verwendungszwecken der beteiligten Buchungen. Ohne ihn bleibt `identity_score`
im Abgleich bei 0.60, waehrend eine Preisaenderung 0.80 verlangt - die
guenstigere Zahlung wird abgelehnt und der Vertrag meldet faelschlich "Zahlung
ausgeblieben".
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"cm-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Merge-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def account_id(household_headers: dict[str, str]) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    return str(account["id"])


def _create(
    headers: dict[str, str],
    name: str,
    amount_minor: int,
    days_ago: int,
    *,
    provider_label: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "amount_minor": amount_minor,
        "frequency": "monthly",
        "next_due_date": (date.today() - timedelta(days=days_ago)).isoformat(),
    }
    if provider_label is not None:
        body["provider_label"] = provider_label
    response = client.post("/v1/contracts", json=body, headers=headers)
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


async def _seed_transaction(
    household_id: str,
    account_id: str,
    booked_date: date,
    amount_minor: int,
    *,
    payee_label: str,
    purpose_raw: str,
) -> str:
    """Eine Buchung direkt in der Datenbank.

    `household_id` steht ausdruecklich im Insert: die Testsitzung laeuft als
    postgres ohne RLS, dort liefert der Spalten-Default current_household_id()
    NULL.
    """
    rows = await _rows(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "payee_label, purpose_raw) values (:household_id, :account_id, :booked_date, "
        ":amount_minor, :payee_label, :purpose_raw) returning id",
        household_id=household_id,
        account_id=account_id,
        booked_date=booked_date,
        amount_minor=amount_minor,
        payee_label=payee_label,
        purpose_raw=purpose_raw,
    )
    return str(rows[0]["id"])


def _contract(headers: dict[str, str], contract_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/contracts/{contract_id}", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_merge_preview_reports_what_would_move(household_headers: dict[str, str]) -> None:
    target = _create(household_headers, "Musterfunk Festnetz", 3995, 5)
    source = _create(household_headers, "Musterfunk Festnetz alt", 4995, 90)

    preview = client.post(
        "/v1/contracts/merge/preview",
        json={"target_id": target["id"], "source_ids": [source["id"]]},
        headers=household_headers,
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["target_name"] == "Musterfunk Festnetz"
    assert body["source_names"] == ["Musterfunk Festnetz alt"]
    assert body["moved_history_entries"] == 1
    assert body["blocking_reason"] is None
    assert [step["amount_minor"] for step in body["amount_steps"]] == [4995, 3995]


def test_merge_builds_one_contract_with_a_price_ladder(
    household_headers: dict[str, str],
) -> None:
    """Der Musterfunk-Fall: aus drei Zeilen wird ein Vertrag, dessen Verlauf die
    Preisstufen in zeitlicher Reihenfolge zeigt."""
    alt = _create(household_headers, "Musterfunk 49,95", 4995, 120)
    mittel = _create(household_headers, "Musterfunk 43,95", 4395, 60)
    aktuell = _create(household_headers, "Musterfunk 39,95", 3995, 5)

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": aktuell["id"],
            "source_ids": [alt["id"], mittel["id"]],
            "request_id": "merge-musterfunk-1",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["merged_count"] == 2
    assert body["moved_history_entries"] == 2
    assert body["contract"]["amount_minor"] == 3995

    remaining = client.get("/v1/contracts", headers=household_headers).json()
    assert [item["id"] for item in remaining] == [aktuell["id"]]

    history = client.get(
        f"/v1/contracts/{aktuell['id']}/history", headers=household_headers
    ).json()
    assert [entry["amount_minor"] for entry in history] == [4995, 4395, 3995]
    # Genau eine offene Stufe, alle anderen geschlossen und lueckenlos.
    assert [entry["valid_to"] is None for entry in history] == [False, False, True]
    assert history[1]["delta_minor"] == -600
    assert history[2]["delta_minor"] == -400

    events = client.get(
        f"/v1/contracts/{aktuell['id']}/events", headers=household_headers
    ).json()
    merged_events = [event for event in events if event["kind"] == "merged"]
    assert len(merged_events) == 1


def test_merge_is_idempotent_per_request_id(household_headers: dict[str, str]) -> None:
    target = _create(household_headers, "Klarna aktuell", 5800, 5)
    source = _create(household_headers, "Klarna alt", 900, 60)
    payload = {
        "target_id": target["id"],
        "source_ids": [source["id"]],
        "request_id": "merge-klarna-1",
    }

    first = client.post("/v1/contracts/merge", json=payload, headers=household_headers)
    assert first.status_code == 200
    assert first.json()["merged_count"] == 1

    second = client.post("/v1/contracts/merge", json=payload, headers=household_headers)
    assert second.status_code == 200
    assert second.json()["merged_count"] == 0

    history = client.get(
        f"/v1/contracts/{target['id']}/history", headers=household_headers
    ).json()
    assert len(history) == 2


@pytest.mark.asyncio
async def test_merge_resolves_colliding_due_dates(household_headers: dict[str, str]) -> None:
    """Beide Vertraege haben eine Faelligkeit am selben Tag. Der bestaetigte
    Treffer muss den offenen schlagen, sonst geht die Buchungsverknuepfung
    verloren."""
    household_id = household_headers["X-Household-Id"]
    target = _create(household_headers, "Strom neu", 12300, 5)
    source = _create(household_headers, "Strom alt", 14700, 60)
    collision = date.today() - timedelta(days=30)

    await _insert_occurrence(household_id, target["id"], collision, "missing")
    await _insert_occurrence(household_id, source["id"], collision, "matched")
    await _insert_occurrence(
        household_id, source["id"], collision - timedelta(days=30), "matched"
    )

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-strom-1",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text

    occurrences = client.get(
        f"/v1/contracts/{target['id']}/occurrences", headers=household_headers
    ).json()
    by_date = {item["due_date"]: item["status"] for item in occurrences}
    assert by_date[collision.isoformat()] == "matched"
    assert len(occurrences) == 2


@pytest.mark.asyncio
async def test_merge_resolves_due_dates_colliding_between_two_sources(
    household_headers: dict[str, str],
) -> None:
    """Zwei Quellen mit derselben Faelligkeit, das Ziel hat fuer den Tag keine.

    Genau die Lage nach einer Dreifacherkennung: die Buchung gehoert per
    contract_occurrences_transaction_uniq einem Vertrag, die beiden anderen
    bekommen vom naechtlichen Abgleich eine 'missing'-Zeile zum selben
    Faelligkeitsdatum. Ohne Aufloesung zwischen den Quellen verletzte das
    Verschieben contract_occurrences_due_uniq: HTTP 500 und ein Rollback der
    gesamten Zusammenfuehrung.
    """
    household_id = household_headers["X-Household-Id"]
    target = _create(household_headers, "Musterfunk aktuell", 3995, 5)
    source_a = _create(household_headers, "Musterfunk Kopie A", 3995, 60)
    source_b = _create(household_headers, "Musterfunk Kopie B", 3995, 90)
    collision = date.today() - timedelta(days=45)

    await _insert_occurrence(household_id, source_a["id"], collision, "missing")
    await _insert_occurrence(household_id, source_b["id"], collision, "matched")
    await _insert_occurrence(
        household_id, source_b["id"], collision - timedelta(days=30), "missing"
    )

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source_a["id"], source_b["id"]],
            "request_id": "merge-musterfunk-collision",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["merged_count"] == 2

    occurrences = client.get(
        f"/v1/contracts/{target['id']}/occurrences", headers=household_headers
    ).json()
    by_date = {item["due_date"]: item["status"] for item in occurrences}
    # Der bestaetigte Treffer schlaegt die ausgebliebene Zahlung - auch dann,
    # wenn beide aus Quellvertraegen stammen.
    assert by_date[collision.isoformat()] == "matched"
    assert len(occurrences) == 2


def test_merge_rejects_target_in_sources(household_headers: dict[str, str]) -> None:
    target = _create(household_headers, "Vertrag", 1000, 5)
    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [target["id"]],
            "request_id": "merge-self",
        },
        headers=household_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "contract_merge_target_in_sources"


def test_merge_blocks_mixed_directions(household_headers: dict[str, str]) -> None:
    """Eine Einnahme und eine Ausgabe zusammenzufuehren waere kein Vertrag mehr,
    sondern ein Vorzeichenfehler."""
    target = _create(household_headers, "Ausgabe", 1000, 5)
    income = client.post(
        "/v1/contracts",
        json={
            "name": "Einnahme",
            "amount_minor": 2000,
            "direction": "income",
            "frequency": "monthly",
            "next_due_date": date.today().isoformat(),
        },
        headers=household_headers,
    ).json()

    preview = client.post(
        "/v1/contracts/merge/preview",
        json={"target_id": target["id"], "source_ids": [income["id"]]},
        headers=household_headers,
    ).json()
    assert preview["blocking_reason"] == "contract_merge_direction_mismatch"


def test_merge_with_unknown_source_is_404(household_headers: dict[str, str]) -> None:
    target = _create(household_headers, "Vertrag", 1000, 5)
    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [str(uuid.uuid4())],
            "request_id": "merge-missing",
        },
        headers=household_headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "contract_not_found"


# ---------------------------------------------------------------------------
# Vertragsschluessel beim Zusammenfuehren (Teil B)
# ---------------------------------------------------------------------------

# Verwendungszwecke eines Telefonanbieters in der MT940-Schreibweise der Bank
# (Abschnitt 19.3). Wesentlich daran: die Mandatsreferenz ist ueber zwei
# Unterfelder gebrochen (?23/?24). Erst `unwrap_structured_purpose` fuegt sie
# ohne Trennzeichen wieder zusammen - eine naive Suche nach "MREF+" fiele auf
# das halbe Praefix herein.
MUSTERFUNK_PAYEE = "Musterfunk Deutschland GmbH"
FESTNETZ_KEY = "DE000200000100000000000000001234567"
MOBILFUNK_KEY = "DE000200000100000000000000007654321"


def _musterfunk_purpose(mandate_head: str, mandate_tail: str, receipt: str) -> str:
    return (
        "105?00EINZUGSERMAECHTIGUNG?10931?20EREF+Zahlbeleg " + receipt + " "
        "?21KREF+2026010112345678901234?22000000000000 "
        "?23MREF+" + mandate_head + "?24" + mandate_tail + " "
        "?25CRED+DE98ZZZ09999999999?26PURP+OTHR ?27SVWZ+RECHNUNG"
    )


def _festnetz_purpose(receipt: str) -> str:
    return _musterfunk_purpose("DE00020000010000000000", "0000001234567", receipt)


def _mobilfunk_purpose(receipt: str) -> str:
    return _musterfunk_purpose("DE00020000010000000000", "0000007654321", receipt)


async def _seed_festnetz_history(household_id: str, account_id: str) -> None:
    """Die Ausgangslage: sechs Festnetzabbuchungen, in der Mitte von 49,95 auf
    39,95 gesunken, alle mit derselben Mandatsreferenz - und keine einzige davon
    einem Vertrag zugeordnet, weil der Abgleich ohne Vertragsschluessel bei
    identity_score 0.60 haengenbleibt."""
    for index, amount in enumerate((-4995, -4995, -4995, -3995, -3995, -3995)):
        await _seed_transaction(
            household_id,
            account_id,
            date.today() - timedelta(days=30 * (6 - index)),
            amount,
            payee_label=MUSTERFUNK_PAYEE,
            purpose_raw=_festnetz_purpose(f"36902107997{index}"),
        )


@pytest.mark.asyncio
async def test_merge_writes_the_shared_mandate_reference_as_contract_key(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Zwei Zeilen desselben Festnetzvertrags.

    Beide Zeilen sind Legacy-Uebernahmen ohne Schluessel, ohne Zeitraum und ohne
    eine einzige zugeordnete Buchung. Die gemeinsame Mandatsreferenz steht
    trotzdem fest - sie kommt aus den Buchungen desselben Empfaengers auf den
    Betragsstufen der beteiligten Vertraege.
    """
    household_id = household_headers["X-Household-Id"]
    await _seed_festnetz_history(household_id, account_id)

    target = _create(
        household_headers, "Musterfunk 39,95", 3995, 5, provider_label=MUSTERFUNK_PAYEE
    )
    source = _create(
        household_headers, "Musterfunk 49,95", 4995, 95, provider_label=MUSTERFUNK_PAYEE
    )
    assert target["contract_key"] is None

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-key-festnetz",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["contract"]["contract_key"] == FESTNETZ_KEY
    assert response.json()["contract"]["contract_key_source"] == "mandate"

    merged = _contract(household_headers, target["id"])
    assert merged["contract_key"] == FESTNETZ_KEY
    assert merged["contract_key_source"] == "mandate"

    # Das Ereignis haelt die Herkunft fest, ohne die der Schluessel spaeter nicht
    # mehr erklaerbar waere.
    events = client.get(
        f"/v1/contracts/{target['id']}/events", headers=household_headers
    ).json()
    merged_event = next(event for event in events if event["kind"] == "merged")
    assert merged_event["payload"]["contract_key"] == FESTNETZ_KEY
    assert merged_event["payload"]["contract_key_source"] == "mandate"


@pytest.mark.asyncio
async def test_merge_writes_no_key_when_the_bookings_name_two_references(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Festnetz und Mobilfunk zusammenzufuehren ist fachlich fragwuerdig - der
    Nutzer hat es aber bestaetigt, also laeuft es durch. Nur der Schluessel
    bleibt leer, weil die Buchungen zwei verschiedene Referenzen nennen.

    Der Test schuetzt zugleich vor einer naheliegenden Abkuerzung: `extract_
    contract_key` allein liefert fuer diese Mischung die Glaeubiger-ID
    DE98ZZZ09999999999, weil sie in jeder Buchung steht. Die benennt den
    ANBIETER und nicht den Vertrag - als contract_key waere sie schaedlicher als
    gar keiner.
    """
    household_id = household_headers["X-Household-Id"]
    for index in range(3):
        await _seed_transaction(
            household_id,
            account_id,
            date.today() - timedelta(days=30 * (3 - index)),
            -3995,
            payee_label=MUSTERFUNK_PAYEE,
            purpose_raw=_festnetz_purpose(f"39938245648{index}"),
        )
        await _seed_transaction(
            household_id,
            account_id,
            date.today() - timedelta(days=30 * (3 - index) + 2),
            -9990,
            payee_label=MUSTERFUNK_PAYEE,
            purpose_raw=_mobilfunk_purpose(f"38420153158{index}"),
        )

    target = _create(
        household_headers, "Musterfunk Festnetz", 3995, 5, provider_label=MUSTERFUNK_PAYEE
    )
    source = _create(
        household_headers, "Musterfunk Mobil", 9990, 8, provider_label=MUSTERFUNK_PAYEE
    )

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-key-ambiguous",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["merged_count"] == 1

    merged = _contract(household_headers, target["id"])
    assert merged["contract_key"] is None
    assert merged["contract_key_source"] is None
    assert merged["contract_key"] not in (FESTNETZ_KEY, MOBILFUNK_KEY, "DE98ZZZ09999999999")


@pytest.mark.asyncio
async def test_merge_prefers_the_bookings_that_really_hang_on_the_contracts(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Haengt an den Faelligkeiten eine Buchung, ist das der Beleg - und keine
    gleichnamige, gleich hohe Buchung daneben, die niemand diesem Vertrag
    zugeordnet hat. Ohne diesen Vorrang waere das Ergebnis mehrdeutig und der
    Schluessel bliebe leer."""
    household_id = household_headers["X-Household-Id"]
    target = _create(
        household_headers, "Musterfunk 39,95", 3995, 5, provider_label=MUSTERFUNK_PAYEE
    )
    source = _create(
        household_headers, "Musterfunk 49,95", 4995, 95, provider_label=MUSTERFUNK_PAYEE
    )

    belegt = date.today() - timedelta(days=95)
    transaction_id = await _seed_transaction(
        household_id,
        account_id,
        belegt,
        -4995,
        payee_label=MUSTERFUNK_PAYEE,
        purpose_raw=_festnetz_purpose("369021079974"),
    )
    await _insert_occurrence(
        household_id, source["id"], belegt, "matched", transaction_id=transaction_id
    )
    # Gleicher Empfaenger, gleiche Betragsstufe, aber andere Referenz und
    # keinem Vertrag zugeordnet.
    await _seed_transaction(
        household_id,
        account_id,
        date.today() - timedelta(days=65),
        -4995,
        payee_label=MUSTERFUNK_PAYEE,
        purpose_raw=_mobilfunk_purpose("384201531582"),
    )

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-key-linked-wins",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text

    merged = _contract(household_headers, target["id"])
    assert merged["contract_key"] == FESTNETZ_KEY
    assert merged["contract_key_source"] == "mandate"


@pytest.mark.asyncio
async def test_merge_never_touches_a_manual_contract_key(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Leitsatz 5: eine automatische Ableitung verliert immer gegen eine
    manuelle Zuordnung. Auch dann, wenn die Buchungen etwas anderes sagen."""
    household_id = household_headers["X-Household-Id"]
    await _seed_festnetz_history(household_id, account_id)

    target = _create(
        household_headers, "Musterfunk 39,95", 3995, 5, provider_label=MUSTERFUNK_PAYEE
    )
    source = _create(
        household_headers, "Musterfunk 49,95", 4995, 95, provider_label=MUSTERFUNK_PAYEE
    )
    patched = client.patch(
        f"/v1/contracts/{target['id']}",
        json={"contract_key": "VON-HAND-4711", "contract_key_source": "manual"},
        headers={**household_headers, "If-Match": f'"{target["version"]}"'},
    )
    assert patched.status_code == 200, patched.text

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-key-manual-target",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text

    merged = _contract(household_headers, target["id"])
    assert merged["contract_key"] == "VON-HAND-4711"
    assert merged["contract_key_source"] == "manual"


@pytest.mark.asyncio
async def test_merge_does_not_overrule_a_manual_key_on_a_source(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Auch ein manuell gesetzter Schluessel an einer QUELLE sperrt die
    Ableitung. Ihn stillschweigend durch einen abgeleiteten zu ersetzen waere
    dieselbe Bevormundung wie am Ziel; ihn zu uebernehmen ist eine eigene
    Nutzeraktion ("Vertragsschluessel korrigieren")."""
    household_id = household_headers["X-Household-Id"]
    await _seed_festnetz_history(household_id, account_id)

    target = _create(
        household_headers, "Musterfunk 39,95", 3995, 5, provider_label=MUSTERFUNK_PAYEE
    )
    source = _create(
        household_headers, "Musterfunk 49,95", 4995, 95, provider_label=MUSTERFUNK_PAYEE
    )
    patched = client.patch(
        f"/v1/contracts/{source['id']}",
        json={"contract_key": "VON-HAND-0815", "contract_key_source": "manual"},
        headers={**household_headers, "If-Match": f'"{source["version"]}"'},
    )
    assert patched.status_code == 200, patched.text

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-key-manual-source",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text

    merged = _contract(household_headers, target["id"])
    assert merged["contract_key"] is None
    assert merged["contract_key_source"] is None


@pytest.mark.asyncio
async def test_merge_leaves_a_key_alone_that_another_contract_already_carries(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ein Schluessel benennt genau einen Vertrag. Traegt ihn schon ein
    unbeteiligter, sichtbarer Vertrag, behaupteten nach dem Schreiben zwei
    Vertraege im Abgleich dieselbe Identitaet von 1.00."""
    household_id = household_headers["X-Household-Id"]
    await _seed_festnetz_history(household_id, account_id)

    besetzer = _create(household_headers, "Musterfunk Zweitzeile", 3995, 40)
    patched = client.patch(
        f"/v1/contracts/{besetzer['id']}",
        json={"contract_key": FESTNETZ_KEY, "contract_key_source": "mandate"},
        headers={**household_headers, "If-Match": f'"{besetzer["version"]}"'},
    )
    assert patched.status_code == 200, patched.text

    target = _create(
        household_headers, "Musterfunk 39,95", 3995, 5, provider_label=MUSTERFUNK_PAYEE
    )
    source = _create(
        household_headers, "Musterfunk 49,95", 4995, 95, provider_label=MUSTERFUNK_PAYEE
    )

    response = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-key-taken",
        },
        headers=household_headers,
    )
    assert response.status_code == 200, response.text

    merged = _contract(household_headers, target["id"])
    assert merged["contract_key"] is None
    assert _contract(household_headers, besetzer["id"])["contract_key"] == FESTNETZ_KEY


@pytest.mark.asyncio
async def test_undo_restores_the_contract_key_state_exactly(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ein Undo, das den Schluessel stehen liesse, waere ein Fehler: der
    zurueckgeholte Quellvertrag und das Ziel behaupteten danach beide, dieselben
    Buchungen zu besitzen."""
    household_id = household_headers["X-Household-Id"]
    await _seed_festnetz_history(household_id, account_id)

    target = _create(
        household_headers, "Musterfunk 39,95", 3995, 5, provider_label=MUSTERFUNK_PAYEE
    )
    source = _create(
        household_headers, "Musterfunk 49,95", 4995, 95, provider_label=MUSTERFUNK_PAYEE
    )

    merged = client.post(
        "/v1/contracts/merge",
        json={
            "target_id": target["id"],
            "source_ids": [source["id"]],
            "request_id": "merge-key-undo",
        },
        headers=household_headers,
    )
    assert merged.status_code == 200, merged.text
    merge_id = merged.json()["merge_id"]
    assert _contract(household_headers, target["id"])["contract_key"] == FESTNETZ_KEY

    undo = client.post(f"/v1/contracts/merges/{merge_id}/undo", headers=household_headers)
    assert undo.status_code == 200, undo.text
    assert undo.json()["contract"]["contract_key"] is None
    assert undo.json()["contract"]["contract_key_source"] is None

    restored = _contract(household_headers, target["id"])
    assert restored["contract_key"] is None
    assert restored["contract_key_source"] is None
    assert _contract(household_headers, source["id"])["contract_key"] is None
