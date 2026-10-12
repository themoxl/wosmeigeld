"""Empfaengerverknuepfung von Buchungen (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, 19.2).

Ausgangsbefund: `transactions.payee_id` blieb nach Import und Handanlage
durchgehend leer, weil keiner der beiden Pfade den Bankempfaenger je gegen
`payees` aufgeloest hat. Ohne diese Verknuepfung bleibt
`contract_matching_service.identity_score` bei 0,60 und die
Preisaenderungserkennung (Schwelle 0,80) kann grundsaetzlich nicht greifen.

Geprueft werden deshalb drei Dinge nebeneinander: dass die beiden Schreibpfade
verknuepfen, dass sie dabei nichts anlegen und nichts ueberschreiben, und dass
der Nachtragslauf fuer den Bestand ohne `--apply` wirklich nichts schreibt.

Laeuft gegen ein echtes lokales Supabase - keine Mocks; `payees` hat keinen
eigenen HTTP-Endpunkt, Empfaenger werden deshalb direkt per SQL angelegt.
"""

import asyncio
import uuid
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import payee_link_service
from tests.conftest import wait_for_import_processing
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

Headers = dict[str, str]

TODAY = date.today()

PAYEE_NAME = "Klarna Bank AB"
# Genau die Kuerzung, mit der eine Bank im Empfaengerfeld arbeitet (27
# Zeichen). Sie ist der Grund, warum "nur verknuepfen" die Luecke nicht
# vollstaendig schliesst - und der Grund, warum sie trotzdem richtig ist
# (siehe payee_link_service).
TRUNCATED_LABEL = "Allianz Krankenversicherung"


@pytest.fixture()
def household_headers() -> Headers:
    token = _signup(f"payeelink-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-4")
    household = client.post(
        "/v1/households", json={"name": "Empfaenger-Testhaushalt"}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


@pytest.fixture()
def second_household_headers() -> Headers:
    token = _signup(f"payeelink2-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-5")
    household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(token)
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


async def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(text(sql), params)
        mapped = [dict(row._mapping) for row in result]
        await session.commit()
        return mapped


async def _execute(sql: str, **params: Any) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def _create_payee(household_id: str, name: str) -> uuid.UUID:
    rows = await _rows(
        "insert into payees (household_id, name) "
        "values (cast(:household_id as uuid), :name) returning id",
        household_id=household_id,
        name=name,
    )
    return uuid.UUID(str(rows[0]["id"]))


async def _create_transaction(
    household_id: str, account_id: str, *, payee_label: str | None, payee_id: str | None = None
) -> uuid.UUID:
    """Buchung direkt per SQL - bewusst am Aufloesungspfad vorbei, damit der
    Nachtragslauf einen echten Altbestand vorfindet."""
    rows = await _rows(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "payee_id, payee_label) values (cast(:household_id as uuid), "
        "cast(:account_id as uuid), :booked_date, -1234, cast(:payee_id as uuid), :payee_label) "
        "returning id",
        household_id=household_id,
        account_id=account_id,
        booked_date=TODAY,
        payee_id=payee_id,
        payee_label=payee_label,
    )
    return uuid.UUID(str(rows[0]["id"]))


async def _payee_id_of(transaction_id: uuid.UUID) -> uuid.UUID | None:
    rows = await _rows(
        "select payee_id from transactions where id = cast(:id as uuid)", id=str(transaction_id)
    )
    value = rows[0]["payee_id"]
    return uuid.UUID(str(value)) if value is not None else None


def _csv(payee: str) -> bytes:
    return (
        "Datum;Betrag;Empfaenger;Verwendungszweck\r\n"
        f"{TODAY.strftime('%d.%m.%Y')};-12,34;{payee};Rate 1 von 3\r\n"
    ).encode()


def _import_and_commit(headers: Headers, account_id: str, payee: str) -> None:
    run_id = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": (f"{uuid.uuid4().hex}.csv", _csv(payee), "text/csv")},
        headers=headers,
    ).json()["id"]
    wait_for_import_processing()
    response = client.post(f"/v1/imports/{run_id}/commit", json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["committed"] == 1


# ---------------------------------------------------------------------------
# Reine Zuordnungslogik
# ---------------------------------------------------------------------------


def test_index_matches_only_the_exact_normal_form() -> None:
    """Schreibweise und Satzzeichen duerfen egal sein, eine Kuerzung nicht.

    Die Kuerzung ist der ganze Punkt: wuerde sie hier auf "Allianz
    Krankenversicherung AG" gezogen, bekaeme die Buchung einen anderen
    Vergleichsschluessel als bisher - und ein Vertrag ohne eigene `payee_id`
    fiele von Identitaet 0,60 auf 0,00 statt auf 0,80 zu steigen.
    """
    household_id = uuid.uuid4()
    versicherer = uuid.uuid4()
    index = payee_link_service.build_payee_index(
        household_id, [(versicherer, "Allianz Krankenversicherung AG")]
    )

    assert payee_link_service.resolve_payee(index, "allianz  krankenversicherung-ag").payee_id == (
        versicherer
    )

    truncated = payee_link_service.resolve_payee(index, TRUNCATED_LABEL)
    assert truncated.payee_id is None
    assert truncated.reason == "unknown_payee"


def test_an_alias_beats_the_name_and_closes_the_truncation_gap() -> None:
    """`payee_aliases` ist der vorgesehene Weg fuer Banktexte, die kein
    Empfaengername abdeckt - eine ausdrueckliche Aussage statt einer Heuristik."""
    household_id = uuid.uuid4()
    versicherer = uuid.uuid4()
    index = payee_link_service.build_payee_index(
        household_id,
        [(versicherer, "Allianz Krankenversicherung AG")],
        [(TRUNCATED_LABEL, versicherer)],
    )

    resolution = payee_link_service.resolve_payee(index, TRUNCATED_LABEL)
    assert resolution.payee_id == versicherer
    assert resolution.reason == "linked_alias"


def test_two_payees_with_the_same_normal_form_are_ambiguous() -> None:
    """`unique (household_id, name)` sichert die Rohschreibweise, nicht die
    Normalform - "A&B GmbH" und "A B GmbH" duerfen beide bestehen."""
    household_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    index = payee_link_service.build_payee_index(
        household_id, [(first, "A&B GmbH"), (second, "A B GmbH")]
    )

    resolution = payee_link_service.resolve_payee(index, "A & B  GmbH")
    assert resolution.payee_id is None
    assert resolution.reason == "ambiguous_name"
    assert set(resolution.candidates) == {first, second}


def test_a_transaction_without_payee_text_resolves_to_nothing() -> None:
    index = payee_link_service.build_payee_index(uuid.uuid4(), [(uuid.uuid4(), PAYEE_NAME)])
    for label in (None, "", "   ", "---"):
        resolution = payee_link_service.resolve_payee(index, label)
        assert resolution.payee_id is None
        assert resolution.reason == "no_label"


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_links_a_committed_transaction_to_the_existing_payee(
    household_headers: Headers, account_id: str
) -> None:
    """Bewusst synchron - wie test_committing_an_import_triggers_the_reconcile_actor:
    der Import-Actor faehrt `asyncio.run` und liefe in einer bereits laufenden
    Ereignisschleife auf einen RuntimeError."""
    household_id = household_headers["X-Household-Id"]
    payee_id = asyncio.run(_create_payee(household_id, PAYEE_NAME))

    _import_and_commit(household_headers, account_id, PAYEE_NAME)

    rows = asyncio.run(
        _rows(
            "select payee_id, payee_label from transactions "
            "where household_id = cast(:household_id as uuid)",
            household_id=household_id,
        )
    )
    assert len(rows) == 1
    assert uuid.UUID(str(rows[0]["payee_id"])) == payee_id


def test_import_never_creates_a_missing_payee(
    household_headers: Headers, account_id: str
) -> None:
    """Festlegung 1: nur verknuepfen, nie anlegen. Ein gekuerzter Banktext darf
    keinen zweiten Empfaenger neben dem echten erzeugen.

    Synchron aus demselben Grund wie der Test darueber."""
    household_id = household_headers["X-Household-Id"]
    asyncio.run(_create_payee(household_id, "Allianz Krankenversicherung AG"))

    _import_and_commit(household_headers, account_id, TRUNCATED_LABEL)

    payees = asyncio.run(
        _rows(
            "select name from payees where household_id = cast(:household_id as uuid)",
            household_id=household_id,
        )
    )
    assert [row["name"] for row in payees] == ["Allianz Krankenversicherung AG"]

    transactions = asyncio.run(
        _rows(
            "select payee_id from transactions where household_id = cast(:household_id as uuid)",
            household_id=household_id,
        )
    )
    assert transactions[0]["payee_id"] is None


def test_a_rule_payee_beats_the_name_resolution(
    household_headers: Headers, account_id: str
) -> None:
    """`action_payee_id` ist eine ausdrueckliche Nutzerkonfiguration und wird
    von der Aufloesung nie ueberschrieben.

    Synchron aus demselben Grund wie die Tests darueber."""
    household_id = household_headers["X-Household-Id"]
    name_payee = asyncio.run(_create_payee(household_id, PAYEE_NAME))
    rule_payee = asyncio.run(_create_payee(household_id, "Sammelkonto Ratenkauf"))
    asyncio.run(
        _execute(
            "insert into classification_rules "
            "(household_id, name, match_payee_raw, action_payee_id) "
            "values (cast(:household_id as uuid), 'Klarna auf Sammelkonto', :match, "
            "cast(:payee_id as uuid))",
            household_id=household_id,
            match=PAYEE_NAME,
            payee_id=str(rule_payee),
        )
    )

    _import_and_commit(household_headers, account_id, PAYEE_NAME)

    rows = asyncio.run(
        _rows(
            "select payee_id from transactions where household_id = cast(:household_id as uuid)",
            household_id=household_id,
        )
    )
    assert uuid.UUID(str(rows[0]["payee_id"])) == rule_payee
    assert uuid.UUID(str(rows[0]["payee_id"])) != name_payee


# ---------------------------------------------------------------------------
# Handanlage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_entry_links_to_the_existing_payee(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, PAYEE_NAME)

    response = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": TODAY.isoformat(),
            "amount_minor": -1234,
            # Andere Schreibweise als der Stammsatz: die Normalisierung soll
            # greifen, nicht ein Zeichenvergleich.
            "payee_label": "klarna  bank ab",
        },
        headers=household_headers,
    )
    assert response.status_code == 201, response.text

    assert await _payee_id_of(uuid.UUID(response.json()["id"])) == payee_id


@pytest.mark.asyncio
async def test_manual_entry_without_a_matching_payee_stays_unlinked(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    await _create_payee(household_id, PAYEE_NAME)

    response = client.post(
        "/v1/transactions",
        json={
            "account_id": account_id,
            "booked_date": TODAY.isoformat(),
            "amount_minor": -1234,
            "payee_label": "Baecker um die Ecke",
        },
        headers=household_headers,
    )
    assert response.status_code == 201, response.text

    assert await _payee_id_of(uuid.UUID(response.json()["id"])) is None
    payees = await _rows(
        "select count(*) as n from payees where household_id = cast(:household_id as uuid)",
        household_id=household_id,
    )
    assert payees[0]["n"] == 1


# ---------------------------------------------------------------------------
# Nachtraegliche Verknuepfung des Bestands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_reports_the_plan_and_writes_nothing(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, PAYEE_NAME)
    linkable = await _create_transaction(household_id, account_id, payee_label=PAYEE_NAME)
    await _create_transaction(household_id, account_id, payee_label=TRUNCATED_LABEL)
    await _create_transaction(household_id, account_id, payee_label=None)

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan = await payee_link_service.analyze_household(session, uuid.UUID(household_id))

    assert plan.transactions_scanned == 3
    assert plan.already_linked == 0
    assert plan.linkable == 1
    assert plan.ambiguous == 0
    assert plan.unchanged == 2
    assert plan.linked == 0
    [group] = plan.link_groups
    assert group.payee_id == payee_id
    assert group.transaction_ids == (linkable,)
    assert {item.reason for item in plan.unchanged_groups} == {"no_label", "unknown_payee"}

    # Dry-Run: nichts geschrieben.
    unchanged = await _rows(
        "select count(*) as n from transactions "
        "where household_id = cast(:household_id as uuid) and payee_id is not null",
        household_id=household_id,
    )
    assert unchanged[0]["n"] == 0


@pytest.mark.asyncio
async def test_apply_links_the_backlog_and_a_second_run_changes_nothing(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, PAYEE_NAME)
    first = await _create_transaction(household_id, account_id, payee_label=PAYEE_NAME)
    second = await _create_transaction(household_id, account_id, payee_label="KLARNA BANK AB")
    unrelated = await _create_transaction(household_id, account_id, payee_label="Baecker")

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan = await payee_link_service.apply_household(session, uuid.UUID(household_id))
        await session.commit()

    assert plan.linkable == 2
    assert plan.linked == 2
    assert await _payee_id_of(first) == payee_id
    assert await _payee_id_of(second) == payee_id
    assert await _payee_id_of(unrelated) is None

    versions_before = await _rows(
        "select id, version from transactions where household_id = cast(:household_id as uuid) "
        "order by id",
        household_id=household_id,
    )

    async with session_factory() as session:
        again = await payee_link_service.apply_household(session, uuid.UUID(household_id))
        await session.commit()

    assert again.already_linked == 2
    assert again.linkable == 0
    assert again.linked == 0

    versions_after = await _rows(
        "select id, version from transactions where household_id = cast(:household_id as uuid) "
        "order by id",
        household_id=household_id,
    )
    # Idempotenz bis in die Versionsspalte: ein zweiter Lauf darf nicht einmal
    # das If-Match offener Clients entwerten.
    assert versions_after == versions_before


@pytest.mark.asyncio
async def test_an_existing_payee_id_is_never_overwritten(
    household_headers: Headers, account_id: str
) -> None:
    """Eine Zuordnung des Nutzers gewinnt immer (Plan 19.6, Leitsatz 5) - auch
    dann, wenn der Empfaengertext eindeutig auf einen anderen zeigt."""
    household_id = household_headers["X-Household-Id"]
    await _create_payee(household_id, PAYEE_NAME)
    chosen_by_user = await _create_payee(household_id, "Von Hand gewaehlt")
    transaction_id = await _create_transaction(
        household_id, account_id, payee_label=PAYEE_NAME, payee_id=str(chosen_by_user)
    )

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan = await payee_link_service.apply_household(session, uuid.UUID(household_id))
        await session.commit()

    assert plan.already_linked == 1
    assert plan.linkable == 0
    assert plan.linked == 0
    assert await _payee_id_of(transaction_id) == chosen_by_user


@pytest.mark.asyncio
async def test_ambiguous_labels_are_reported_with_a_reason_and_link_nothing(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    first = await _create_payee(household_id, "A&B GmbH")
    second = await _create_payee(household_id, "A B GmbH")
    transaction_id = await _create_transaction(household_id, account_id, payee_label="A & B GmbH")

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan = await payee_link_service.apply_household(session, uuid.UUID(household_id))
        await session.commit()

    assert plan.ambiguous == 1
    assert plan.linkable == 0
    assert plan.linked == 0
    [group] = plan.unchanged_groups
    assert group.reason == "ambiguous_name"
    assert group.label == "A & B GmbH"
    assert set(group.candidates) == {first, second}
    assert await _payee_id_of(transaction_id) is None


@pytest.mark.asyncio
async def test_a_payee_of_another_household_is_never_linked(
    household_headers: Headers,
    second_household_headers: Headers,
    account_id: str,
) -> None:
    """Haushaltsisolierung: derselbe Empfaengername in Haushalt A darf niemals
    an einer Buchung aus Haushalt B landen."""
    household_a = household_headers["X-Household-Id"]
    household_b = second_household_headers["X-Household-Id"]
    payee_a = await _create_payee(household_a, PAYEE_NAME)
    account_b = client.post(
        "/v1/accounts",
        json={"name": "Fremdes Girokonto", "account_type": "checking"},
        headers=second_household_headers,
    ).json()["id"]
    transaction_b = await _create_transaction(household_b, str(account_b), payee_label=PAYEE_NAME)

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan_b = await payee_link_service.apply_household(session, uuid.UUID(household_b))
        await session.commit()

    assert plan_b.linkable == 0
    assert plan_b.linked == 0
    assert [item.reason for item in plan_b.unchanged_groups] == ["unknown_payee"]
    assert await _payee_id_of(transaction_b) is None

    # Auch die Handanlage in Haushalt B sieht den fremden Empfaenger nicht.
    created = client.post(
        "/v1/transactions",
        json={
            "account_id": str(account_b),
            "booked_date": TODAY.isoformat(),
            "amount_minor": -1234,
            "payee_label": PAYEE_NAME,
        },
        headers=second_household_headers,
    )
    assert created.status_code == 201, created.text
    assert await _payee_id_of(uuid.UUID(created.json()["id"])) is None

    # Und der Empfaenger in Haushalt A ist unveraendert brauchbar.
    transaction_a = await _create_transaction(household_a, account_id, payee_label=PAYEE_NAME)
    async with session_factory() as session:
        plan_a = await payee_link_service.apply_household(session, uuid.UUID(household_a))
        await session.commit()
    assert plan_a.linked == 1
    assert await _payee_id_of(transaction_a) == payee_a
