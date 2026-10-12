"""Abgleichsmotor fuer Vertraege (Phase 2) gegen ein laufendes lokales Supabase.

Phase 1 hat `contract_occurrences`, `contract_amount_history` und
`contract_events` angelegt, ohne dass sie je jemand gefuellt haette. Hier wird
geprueft, dass der Motor genau das tut - und vor allem, dass er es zweimal
hintereinander gleich tut: `test_running_twice_changes_nothing` ist der
wichtigste Test dieser Datei, weil der naechtliche Actor, der Post-Import-Hook
und der Rescan-Knopf denselben Lauf ausloesen koennen.

Buchungen entstehen per direktem SQL statt ueber `POST /v1/transactions`: nur
so lassen sich `purpose_raw` und `payee_id` setzen, an denen die Identitaet des
Kandidaten haengt.
"""

import calendar
import os
import uuid
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import contract_matching_service, contract_service, worker_dispatch
from tests.conftest import wait_for_contract_reconcile, wait_for_import_processing
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

TODAY = date.today()

# Eigene, synchrone Verbindung fuer den Test, der die Sicht des Workers
# nachstellt: der verbindet als eigener Prozess und sieht nur Committetes.
_SYNC_DSN = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"
).replace("postgresql+psycopg://", "postgresql://")


# ---------------------------------------------------------------------------
# Fixtures und Helfer
# ---------------------------------------------------------------------------


@pytest.fixture()
def household_headers() -> dict[str, str]:
    token = _signup(f"cr-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Abgleich-Testhaushalt"}, headers=_auth_headers(token)
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


def _create_contract(headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Beispielvertrag",
        "amount_minor": 1999,
        "frequency": "monthly",
        "next_due_date": TODAY.isoformat(),
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _months_back(value: date, months: int) -> date:
    """Dasselbe Monatsraster, auf dem der Motor rueckwaerts rechnet.

    `contracts.due_day` wird beim Anlegen aus `next_due_date` abgeleitet, der
    Zieltag bleibt also ueber alle Perioden derselbe und wird nur am kuerzeren
    Monat gekappt.
    """
    total = value.month - 1 - months
    year = value.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _next_due(due: date) -> date:
    return contract_service.next_due_date(
        due, "monthly", due_rule="day_of_month", due_day=due.day
    )


async def _create_payee(household_id: str, name: str) -> str:
    session_factory = get_session_factory()
    async with session_factory() as session:
        # household_id ausdruecklich: die Testsitzung laeuft als postgres ohne
        # RLS, dort liefert der Spalten-Default current_household_id() NULL.
        result = await session.execute(
            text(
                "insert into payees (household_id, name) values (:household_id, :name) "
                "returning id"
            ),
            {"household_id": household_id, "name": name},
        )
        payee_id = str(result.one().id)
        await session.commit()
    return payee_id


async def _seed_transaction(
    household_id: str,
    account_id: str,
    booked_date: date,
    amount_minor: int,
    *,
    payee_id: str | None = None,
    payee_label: str | None = None,
    purpose_raw: str | None = None,
) -> str:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            text(
                "insert into transactions (household_id, account_id, booked_date, "
                "amount_minor, payee_id, payee_label, purpose_raw) values "
                "(:household_id, :account_id, :booked_date, :amount_minor, "
                "cast(:payee_id as uuid), :payee_label, :purpose_raw) returning id"
            ),
            {
                "household_id": household_id,
                "account_id": account_id,
                "booked_date": booked_date,
                "amount_minor": amount_minor,
                "payee_id": payee_id,
                "payee_label": payee_label,
                "purpose_raw": purpose_raw,
            },
        )
        transaction_id = str(result.one().id)
        await session.commit()
    return transaction_id


async def _reconcile(
    household_id: str, today: date | None = None
) -> contract_matching_service.ReconcileResult:
    """Faehrt den Lauf so, wie ihn der Worker faehrt: eigene Sitzung als
    postgres ohne RLS, ein Commit am Ende."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await contract_matching_service.reconcile_household(
            session, UUID(household_id), today=today or TODAY
        )
        await session.commit()
    return result


async def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(text(sql), params)
        return [dict(row._mapping) for row in result]


async def _execute(sql: str, **params: Any) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def _contract_row(contract_id: str) -> dict[str, Any]:
    [row] = await _rows("select * from contracts where id = :id", id=contract_id)
    return row


async def _occurrences(contract_id: str) -> list[dict[str, Any]]:
    return await _rows(
        "select due_date, status, transaction_id, booked_date, amount_minor, "
        "amount_delta_minor, days_delta, match_score from contract_occurrences "
        "where contract_id = :id order by due_date asc",
        id=contract_id,
    )


async def _events(contract_id: str) -> list[dict[str, Any]]:
    return await _rows(
        "select kind, occurred_on, payload from contract_events "
        "where contract_id = :id order by created_at asc, kind asc",
        id=contract_id,
    )


async def _history(contract_id: str) -> list[dict[str, Any]]:
    return await _rows(
        "select amount_minor, valid_from, valid_to, delta_minor, change_reason "
        "from contract_amount_history where contract_id = :id "
        "order by valid_from asc, created_at asc",
        id=contract_id,
    )


# ---------------------------------------------------------------------------
# 1. Treffer innerhalb der Toleranz
# ---------------------------------------------------------------------------


async def test_a_hit_writes_an_occurrence_and_moves_the_due_date_one_period(
    household_headers: dict[str, str], account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=5)
    contract = _create_contract(
        household_headers, name="Nordlicht Media", amount_minor=1299, next_due_date=due.isoformat()
    )
    transaction_id = await _seed_transaction(
        household_id, account_id, due, -1299, payee_label="Nordlicht Media"
    )

    result = await _reconcile(household_id)
    assert (result.matched_count, result.missing_count, result.price_change_count) == (1, 0, 0)

    [occurrence] = await _occurrences(contract["id"])
    assert occurrence["status"] == "matched"
    assert str(occurrence["transaction_id"]) == transaction_id
    assert occurrence["due_date"] == due
    # Originalvorzeichen der Buchung, damit die Detailanzeige eine Ausgabe nicht
    # als Einnahme rendert; die Abweichung daneben ist vorzeichenlos gerechnet.
    assert occurrence["amount_minor"] == -1299
    assert occurrence["amount_delta_minor"] == 0
    assert occurrence["days_delta"] == 0

    row = await _contract_row(contract["id"])
    assert row["next_due_date"] == _next_due(due)
    assert row["missed_periods"] == 0
    assert row["last_seen_date"] == due
    assert row["first_seen_date"] == due
    assert row["last_matched_at"] is not None
    assert row["status"] == "active"


async def test_a_hit_inside_the_day_tolerance_still_counts(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Lastschriften kommen selten auf den Tag genau - sonst waere jeder
    Feiertag eine ausgebliebene Zahlung."""
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=10)
    contract = _create_contract(
        household_headers, name="Stadtwerke", amount_minor=8_900, next_due_date=due.isoformat()
    )
    await _seed_transaction(
        household_id, account_id, due + timedelta(days=3), -8_900, payee_label="Stadtwerke"
    )

    await _reconcile(household_id)
    [occurrence] = await _occurrences(contract["id"])
    assert occurrence["status"] == "matched"
    assert occurrence["days_delta"] == 3


async def test_the_rescan_endpoint_runs_the_same_engine_and_is_idempotent(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Die Notbremse, wenn Redis oder der Worker steht: derselbe Lauf, nur
    synchron im Request."""
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=6)
    contract = _create_contract(
        household_headers, name="Bahn Abo", amount_minor=4_900, next_due_date=due.isoformat()
    )
    await _seed_transaction(household_id, account_id, due, -4_900, payee_label="Bahn Abo")

    first = client.post("/v1/contracts/rescan", headers=household_headers)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "contracts_examined": 1,
        "matched_count": 1,
        "price_change_count": 0,
        "missing_count": 0,
        "ended_count": 0,
    }

    second = client.post("/v1/contracts/rescan", headers=household_headers)
    assert second.status_code == 200
    assert second.json()["matched_count"] == 0
    assert len(await _occurrences(contract["id"])) == 1


def test_committing_an_import_triggers_the_reconcile_actor(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Frisch uebernommene Buchungen sind ausserhalb des naechtlichen Laufs der
    einzige Anlass, zu dem sich das Ergebnis aendern kann - der Import-Commit
    reiht deshalb `reconcile_contracts` ein.

    Bewusst synchron: der Actor faehrt `asyncio.run` und liefe in einer bereits
    laufenden Ereignisschleife auf einen RuntimeError.
    """
    due = TODAY - timedelta(days=5)
    contract = _create_contract(
        household_headers,
        name="MUSTER KRANKENVERSICHERUNG",
        amount_minor=1_999,
        next_due_date=due.isoformat(),
    )
    csv_content = (
        "Datum;Soll/Haben;Betrag_EUR;Buchungsart;Empfaenger/Zweck\n"
        f"{due.isoformat()};Soll;-19,99;EINZUGSERMAECHTIGUNG;MUSTER KRANKENVERSICHERUNG\n"
    ).encode()

    upload = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("umsaetze.csv", csv_content, "text/csv")},
        headers=household_headers,
    )
    assert upload.status_code == 201, upload.text
    wait_for_import_processing()

    commit = client.post(
        f"/v1/imports/{upload.json()['id']}/commit", json={}, headers=household_headers
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["committed"] == 1

    occurrences_url = f"/v1/contracts/{contract['id']}/occurrences"
    # Vor dem Abarbeiten der Nachricht ist noch nichts abgeglichen - ohne diese
    # Zwischenpruefung koennte der Test auch dann gruen sein, wenn der Hook
    # fehlt und der Abgleich schon beim Anlegen gelaufen waere.
    assert client.get(occurrences_url, headers=household_headers).json() == []

    wait_for_contract_reconcile()

    [occurrence] = client.get(occurrences_url, headers=household_headers).json()
    assert occurrence["status"] == "matched"
    assert occurrence["due_date"] == due.isoformat()


class _SpyActor:
    """Zaehlt mit, was eine FREMDE Sitzung im Moment des Einreihens sieht."""

    def __init__(self, inner: Any, household_id: str) -> None:
        self._inner = inner
        self._household_id = household_id
        self.visible_transactions: list[int] = []

    def send(self, *args: Any, **kwargs: Any) -> Any:
        with psycopg.connect(_SYNC_DSN) as connection, connection.cursor() as cursor:
            cursor.execute(
                "select count(*) from transactions where household_id = %s",
                (self._household_id,),
            )
            row = cursor.fetchone()
            self.visible_transactions.append(int(row[0]) if row is not None else -1)
        return self._inner.send(*args, **kwargs)


def test_the_reconcile_message_is_queued_only_after_the_commit_is_written(
    household_headers: dict[str, str],
    account_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Worker oeffnet eine eigene Sitzung und sieht nur Committetes.

    `commit_import_run` committet selbst nicht - die Transaktion umschliesst den
    gesamten Request (app/auth/dependencies.get_household_session). Stuende das
    `.send()` noch im Handler, koennte der Worker den Abgleich auf einem
    Snapshot ohne die frisch importierten Buchungen fahren, die betroffene
    Periode dauerhaft als 'missing' festschreiben und `next_due_date`
    fortschreiben. Der Fehler faellt nicht auf: der Actor findet einfach nichts
    und meldet Erfolg.
    """
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=5)
    _create_contract(
        household_headers,
        name="MUSTER KRANKENVERSICHERUNG",
        amount_minor=1_999,
        next_due_date=due.isoformat(),
    )
    csv_content = (
        "Datum;Soll/Haben;Betrag_EUR;Buchungsart;Empfaenger/Zweck\n"
        f"{due.isoformat()};Soll;-19,99;EINZUGSERMAECHTIGUNG;MUSTER KRANKENVERSICHERUNG\n"
    ).encode()

    # Der Actor steht seit der Zusammenfuehrung in worker_dispatch - dort
    # haengt jetzt auch das Archivieren einer Buchung dran, nicht mehr nur
    # der Import.
    spy = _SpyActor(worker_dispatch.reconcile_contracts_actor, household_id)
    monkeypatch.setattr(worker_dispatch, "reconcile_contracts_actor", spy)

    upload = client.post(
        "/v1/imports/upload",
        data={"account_id": account_id},
        files={"file": ("umsaetze.csv", csv_content, "text/csv")},
        headers=household_headers,
    )
    assert upload.status_code == 201, upload.text
    wait_for_import_processing()

    commit = client.post(
        f"/v1/imports/{upload.json()['id']}/commit", json={}, headers=household_headers
    )
    assert commit.status_code == 200, commit.text

    # Genau ein Einreihvorgang - und die Buchung war dabei bereits sichtbar.
    assert spy.visible_transactions == [1]


# ---------------------------------------------------------------------------
# 2. Preisaenderung statt zweitem Vertrag
# ---------------------------------------------------------------------------


async def test_maxenergy_becomes_a_price_step_and_not_a_second_contract(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Fall aus der Praxis: MaxEnergy bucht statt 147,00 EUR nur noch
    123,00 EUR ab. Ohne diesen Zweig entstuende ein zweiter Vertrag und die
    Fixkostensumme zaehlte den Strom doppelt."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, "MaxEnergy GmbH")
    due = TODAY - timedelta(days=10)
    contract = _create_contract(
        household_headers,
        name="MaxEnergy Strom",
        provider_label="MaxEnergy GmbH",
        payee_id=payee_id,
        amount_minor=14_700,
        next_due_date=due.isoformat(),
    )
    # Bewusst zwei Tage nach der Faelligkeit: faenden Preisstufe und
    # Anfangsstufe am selben Tag statt, ersetzte die neue die alte und der
    # Verlauf haette nur eine Zeile.
    await _seed_transaction(
        household_id, account_id, due + timedelta(days=2), -12_300, payee_id=payee_id
    )

    result = await _reconcile(household_id)
    assert (result.price_change_count, result.matched_count) == (1, 0)

    # Genau eine Vertragszeile, genau eine Faelligkeit.
    assert len(client.get("/v1/contracts", headers=household_headers).json()) == 1
    [occurrence] = await _occurrences(contract["id"])
    assert occurrence["status"] == "matched"
    assert occurrence["amount_delta_minor"] == -2_400

    row = await _contract_row(contract["id"])
    assert row["amount_minor"] == 12_300
    assert row["tolerance_amount_minor"] == 615  # 5 % des neuen Betrags

    history = await _history(contract["id"])
    assert [entry["amount_minor"] for entry in history] == [14_700, 12_300]
    assert history[0]["valid_to"] == due + timedelta(days=1)
    assert history[1]["valid_to"] is None
    assert history[1]["delta_minor"] == -2_400
    assert history[1]["change_reason"] == "detected_change"

    events = await _events(contract["id"])
    [price_change] = [event for event in events if event["kind"] == "price_change"]
    assert price_change["payload"]["delta_minor"] == -2_400
    assert price_change["payload"]["old_minor"] == 14_700
    assert price_change["payload"]["new_minor"] == 12_300


async def test_a_one_off_payment_to_the_same_payee_leaves_the_amount_alone(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Gegenprobe zum 60-%-Deckel: die Jahresabrechnung darf den Monatsbetrag
    nicht umschreiben - sie ist gar kein Treffer."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, "MaxEnergy GmbH")
    due = TODAY - timedelta(days=10)
    contract = _create_contract(
        household_headers,
        name="MaxEnergy Strom",
        payee_id=payee_id,
        amount_minor=14_700,
        next_due_date=due.isoformat(),
    )
    await _seed_transaction(
        household_id, account_id, due + timedelta(days=2), -98_000, payee_id=payee_id
    )

    result = await _reconcile(household_id)
    assert result.price_change_count == 0
    assert result.missing_count == 1

    row = await _contract_row(contract["id"])
    assert row["amount_minor"] == 14_700
    [occurrence] = await _occurrences(contract["id"])
    assert occurrence["status"] == "missing"
    assert occurrence["transaction_id"] is None


# ---------------------------------------------------------------------------
# 3. Ausgebliebene Zahlungen
# ---------------------------------------------------------------------------


async def test_netflix_without_a_booking_is_reported_missing_exactly_once(
    household_headers: dict[str, str]
) -> None:
    """Zwei Perioden bleiben aus, das Ereignis wird trotzdem nur einmal
    geschrieben - sonst stuende die Inbox nach einem halben Jahr Pause voller
    identischer Meldungen."""
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=50)
    contract = _create_contract(
        household_headers, name="Netflix", amount_minor=1_799, next_due_date=due.isoformat()
    )

    result = await _reconcile(household_id)
    assert result.missing_count == 2

    occurrences = await _occurrences(contract["id"])
    assert [item["status"] for item in occurrences] == ["missing", "missing"]
    assert all(item["transaction_id"] is None for item in occurrences)
    assert occurrences[0]["due_date"] == due
    assert occurrences[1]["due_date"] == _next_due(due)

    row = await _contract_row(contract["id"])
    assert row["missed_periods"] == 2
    assert row["status"] == "active"
    # Trotz Fehltreffer fortgeschrieben - sonst bliebe der Motor stehen.
    assert row["next_due_date"] == _next_due(_next_due(due))

    kinds = [event["kind"] for event in await _events(contract["id"])]
    assert kinds.count("payment_missing") == 1


async def test_a_period_beyond_the_imported_data_is_kept_open_and_old_false_missing_is_rewound(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der August-Auszug fehlt: Kalenderzeit allein darf weder die rote
    Fehlperiode erzeugen noch eine bereits voreilig erzeugte stehen lassen."""
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=10)
    data_through = due - timedelta(days=1)
    await _seed_transaction(
        household_id,
        account_id,
        data_through,
        -7_777,
        payee_label="Anderer Umsatz",
    )
    await _execute(
        "insert into import_runs (household_id, account_id, filename, file_hash, "
        "file_size_bytes, status, completed_at) values (:household_id, :account_id, "
        "'juli.csv', :file_hash, 1, 'completed', now())",
        household_id=household_id,
        account_id=account_id,
        file_hash=uuid.uuid4().hex,
    )
    contract = _create_contract(
        household_headers,
        name="Musterkasse",
        account_id=account_id,
        amount_minor=150_000,
        next_due_date=due.isoformat(),
    )

    first = await _reconcile(household_id, today=TODAY)
    assert first.missing_count == 0
    assert await _occurrences(contract["id"]) == []
    assert (await _contract_row(contract["id"]))["next_due_date"] == due

    # Zustand einer aelteren Motorversion nachstellen. Der naechste Lauf muss
    # ihn entfernen und die offene Faelligkeit wieder auf den Ursprung setzen.
    await _execute(
        "insert into contract_occurrences (household_id, contract_id, due_date, status) "
        "values (:household_id, :contract_id, :due_date, 'missing')",
        household_id=household_id,
        contract_id=contract["id"],
        due_date=due,
    )
    await _execute(
        "update contracts set next_due_date = :next_due_date, missed_periods = 1 "
        "where id = :contract_id",
        contract_id=contract["id"],
        next_due_date=_next_due(due),
    )
    second = await _reconcile(household_id, today=TODAY)
    assert second.missing_count == 0
    assert await _occurrences(contract["id"]) == []
    repaired = await _contract_row(contract["id"])
    assert repaired["next_due_date"] == due
    assert repaired["missed_periods"] == 0


async def test_a_returning_payment_is_reported_as_resumed(
    household_headers: dict[str, str], account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=50)
    contract = _create_contract(
        household_headers, name="Fitness Nord", amount_minor=2_990, next_due_date=due.isoformat()
    )
    await _reconcile(household_id)

    resumed_due = (await _contract_row(contract["id"]))["next_due_date"]
    await _seed_transaction(
        household_id, account_id, resumed_due, -2_990, payee_label="Fitness Nord"
    )
    result = await _reconcile(household_id, today=resumed_due + timedelta(days=1))

    assert result.matched_count == 1
    assert result.resumed_count == 1
    assert (await _contract_row(contract["id"]))["missed_periods"] == 0
    assert "payment_resumed" in [event["kind"] for event in await _events(contract["id"])]


# ---------------------------------------------------------------------------
# 4. und 5. Automatisches Beenden
# ---------------------------------------------------------------------------


async def test_the_automation_ends_a_contract_after_the_configured_gaps(
    household_headers: dict[str, str]
) -> None:
    """Beendet wird fruehestens im Lauf NACH dem Erreichen der Inbox-Schwelle.

    Entscheidung 17.3 des Plans verlangt die Zwischenstufe ausdruecklich: ab
    zwei Fehlperioden erscheint das Review-Item, "auch wenn die Automatik
    spaeter ohnehin greifen wuerde". Ein einziger Nachholauf zaehlt aber
    beliebig viele Perioden hoch - ohne diese Trennung waere der Vertrag
    beendet, bevor die Frage je in der Inbox stand.
    """
    household_id = household_headers["X-Household-Id"]
    assert (
        client.put(
            "/v1/contracts/settings",
            json={"auto_end_after_missed": "2"},
            headers=household_headers,
        ).status_code
        == 200
    )
    due = TODAY - timedelta(days=50)
    contract = _create_contract(
        household_headers, name="Zeitschrift", amount_minor=990, next_due_date=due.isoformat()
    )

    first = await _reconcile(household_id)
    assert first.ended_count == 0
    row_after_first = await _contract_row(contract["id"])
    assert row_after_first["status"] == "active"
    assert row_after_first["missed_periods"] == 2

    third_due = _next_due(_next_due(due))
    result = await _reconcile(household_id, today=third_due + timedelta(days=5))
    assert result.ended_count == 1

    row = await _contract_row(contract["id"])
    assert row["status"] == "ended"
    assert row["missed_periods"] == 3

    [ended] = [event for event in await _events(contract["id"]) if event["kind"] == "ended"]
    assert ended["payload"]["reason"] == "auto_missed"
    assert ended["payload"]["missed_periods"] == 3
    # Der automatisch beendete Vertrag bekommt sein Enddatum. Ohne das liefe er
    # im Fixkostentrend ewig weiter - die offene Preisstufe trifft jeden Monat
    # des Rasters, und `ends_on` ist die einzige Grenze, die die Abfrage kennt.
    assert row["ends_on"] == third_due


async def test_an_automatically_ended_contract_leaves_the_fixed_costs_trend(
    household_headers: dict[str, str]
) -> None:
    """Regressionstest zum Enddatum, an seiner sichtbaren Wirkung gemessen.

    Der Vertrag lief bis vor zwei Monaten und wurde danach wegen ausgebliebener
    Zahlungen automatisch beendet. In den Monaten, in denen er lief, muss er in
    der Zeitreihe stehen - im laufenden Monat nicht mehr. Ohne `ends_on` blieb
    er bis in alle Zukunft in der Kurve.
    """
    household_id = household_headers["X-Household-Id"]
    client.put(
        "/v1/contracts/settings",
        json={"auto_end_after_missed": "2"},
        headers=household_headers,
    )
    start = _months_back(TODAY.replace(day=15), 5)
    contract = _create_contract(
        household_headers,
        name="Altes Abo",
        contract_type="subscription",
        amount_minor=1999,
        next_due_date=start.isoformat(),
    )

    # Erster Lauf: zwei Fehlperioden, der Vertrag laeuft weiter (Inbox-Stufe).
    second_due = _next_due(start)
    await _reconcile(household_id, today=second_due + timedelta(days=10))
    assert (await _contract_row(contract["id"]))["missed_periods"] == 2

    # Zweiter Lauf: jetzt darf beendet werden - mit dem Datum der Periode.
    third_due = _next_due(second_due)
    await _reconcile(household_id, today=third_due + timedelta(days=10))
    row = await _contract_row(contract["id"])
    assert row["status"] == "ended"
    assert row["ends_on"] == third_due

    months = client.get(
        "/v1/reports/fixed-costs-trend", params={"months": 6}, headers=household_headers
    ).json()["months"]
    by_key = {(m["year"], m["month"]): m for m in months}
    assert by_key[(start.year, start.month)]["fixed_costs_minor"] == 1999
    assert by_key[(TODAY.year, TODAY.month)]["fixed_costs_minor"] == 0
    assert by_key[(TODAY.year, TODAY.month)]["contract_count"] == 0


async def test_a_long_catchup_asks_before_it_ends_the_contract(
    household_headers: dict[str, str]
) -> None:
    """Der Regelfall nach der Migration: `next_due_date` wurde in
    `recurring_rules` nie fortgeschrieben und liegt Monate zurueck.

    Ein einziger Lauf holt alle Perioden nach. Wuerde er dabei auch beenden,
    faellt der Vertrag sofort aus `GET /v1/contracts` (blendet 'ended' aus) und
    aus der Review-Inbox (fragt nur 'active'/'cancelled') - der Nutzer haette
    Netflix, Discovery und Crunchyroll nie zu Gesicht bekommen.
    """
    household_id = household_headers["X-Household-Id"]
    due = _months_back(TODAY, 6)
    contract = _create_contract(
        household_headers, name="Crunchyroll", amount_minor=699, next_due_date=due.isoformat()
    )

    result = await _reconcile(household_id)
    assert result.ended_count == 0
    assert result.missing_count >= 3

    row = await _contract_row(contract["id"])
    assert row["status"] == "active"
    assert row["missed_periods"] >= 3

    inbox = client.get("/v1/review", headers=household_headers).json()
    assert contract["id"] in {item["contract_id"] for item in inbox["contract_ended_checks"]}


async def test_switching_the_automation_off_keeps_the_contract_active(
    household_headers: dict[str, str]
) -> None:
    household_id = household_headers["X-Household-Id"]
    client.put(
        "/v1/contracts/settings",
        json={"auto_end_after_missed": "off"},
        headers=household_headers,
    )
    due = TODAY - timedelta(days=50)
    contract = _create_contract(
        household_headers, name="Zeitschrift", amount_minor=990, next_due_date=due.isoformat()
    )

    result = await _reconcile(household_id)
    assert result.ended_count == 0

    row = await _contract_row(contract["id"])
    assert row["status"] == "active"
    assert row["missed_periods"] == 2
    assert [event["kind"] for event in await _events(contract["id"])].count("ended") == 0


async def test_a_users_own_end_date_beats_the_automation(
    household_headers: dict[str, str]
) -> None:
    """Wer ein Ende oder eine Kuendigung eingetragen hat, bekommt keinen
    automatisch beendeten Vertrag untergeschoben."""
    household_id = household_headers["X-Household-Id"]
    client.put(
        "/v1/contracts/settings",
        json={"auto_end_after_missed": "2"},
        headers=household_headers,
    )
    due = TODAY - timedelta(days=50)
    with_end_date = _create_contract(
        household_headers,
        name="Mietvertrag Garage",
        amount_minor=5_000,
        next_due_date=due.isoformat(),
        ends_on=(TODAY + timedelta(days=200)).isoformat(),
    )
    cancelled = _create_contract(
        household_headers,
        name="Handyvertrag",
        amount_minor=2_500,
        next_due_date=due.isoformat(),
        status="cancelled",
        cancelled_on=(TODAY - timedelta(days=60)).isoformat(),
    )

    result = await _reconcile(household_id)
    assert result.ended_count == 0
    assert result.missing_count == 4

    for contract_id, expected_status in (
        (with_end_date["id"], "active"),
        (cancelled["id"], "cancelled"),
    ):
        row = await _contract_row(contract_id)
        assert row["status"] == expected_status
        assert row["missed_periods"] == 2
        assert [event["kind"] for event in await _events(contract_id)].count("ended") == 0


# ---------------------------------------------------------------------------
# 6. Idempotenz
# ---------------------------------------------------------------------------


async def _snapshot(household_id: str) -> dict[str, list[dict[str, Any]]]:
    """Alles, was der Motor schreiben darf - in stabiler Reihenfolge.

    `updated_at`, `matched_at` und `last_matched_at` fehlen bewusst: sie
    duerften sich aendern. `version` steht ausdruecklich drin, denn ein
    Wiederholungslauf, der sie hochzaehlt, entwertet jedes offene If-Match im
    Browser des Nutzers.
    """
    return {
        "contracts": await _rows(
            "select id, version, status, amount_minor, tolerance_amount_minor, "
            "next_due_date, missed_periods, first_seen_date, last_seen_date, "
            "installments_paid from contracts where household_id = :household_id "
            "order by id asc",
            household_id=household_id,
        ),
        "occurrences": await _rows(
            "select contract_id, due_date, status, transaction_id, booked_date, "
            "amount_minor, amount_delta_minor, days_delta, match_score "
            "from contract_occurrences where household_id = :household_id "
            "order by contract_id asc, due_date asc",
            household_id=household_id,
        ),
        "events": await _rows(
            "select contract_id, kind, occurred_on, payload from contract_events "
            "where household_id = :household_id "
            "order by contract_id asc, occurred_on asc, kind asc",
            household_id=household_id,
        ),
        "history": await _rows(
            "select contract_id, amount_minor, valid_from, valid_to, delta_minor, "
            "change_reason from contract_amount_history where household_id = :household_id "
            "order by contract_id asc, valid_from asc",
            household_id=household_id,
        ),
    }


async def test_running_twice_changes_nothing(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der wichtigste Test dieser Phase.

    Naechtlicher Actor, Post-Import-Hook und Rescan-Knopf koennen denselben Lauf
    kurz hintereinander ausloesen. Ein zweiter Durchgang darf weder eine zweite
    Faelligkeit noch ein zweites Ereignis erzeugen - und `contracts.version`
    nicht anfassen, weil der Trigger sonst jedes offene If-Match entwertet.
    """
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, "MaxEnergy GmbH")

    hit_due = TODAY - timedelta(days=5)
    _create_contract(
        household_headers,
        name="Nordlicht Media",
        amount_minor=1_299,
        next_due_date=hit_due.isoformat(),
    )
    price_due = TODAY - timedelta(days=10)
    _create_contract(
        household_headers,
        name="MaxEnergy Strom",
        payee_id=payee_id,
        amount_minor=14_700,
        next_due_date=price_due.isoformat(),
    )
    _create_contract(
        household_headers,
        name="Netflix",
        amount_minor=1_799,
        next_due_date=(TODAY - timedelta(days=50)).isoformat(),
    )

    await _seed_transaction(
        household_id, account_id, hit_due, -1_299, payee_label="Nordlicht Media"
    )
    await _seed_transaction(
        household_id, account_id, price_due + timedelta(days=2), -12_300, payee_id=payee_id
    )

    first = await _reconcile(household_id)
    assert (first.matched_count, first.price_change_count, first.missing_count) == (1, 1, 2)
    after_first = await _snapshot(household_id)

    # Gegenprobe, damit der Vergleich unten nicht auf zwei folgenlosen Laeufen
    # ruht: der erste Durchgang hat den Versionstrigger sehr wohl ausgeloest.
    assert all(row["version"] > 1 for row in after_first["contracts"])

    second = await _reconcile(household_id)
    assert (second.matched_count, second.price_change_count, second.missing_count) == (0, 0, 0)
    after_second = await _snapshot(household_id)

    assert after_second == after_first

    # Die beiden entscheidenden Punkte noch einmal ausdruecklich - im
    # Woerterbuchvergleich waeren sie nur mittelbar erkennbar.
    assert [row["version"] for row in after_second["contracts"]] == [
        row["version"] for row in after_first["contracts"]
    ]
    due_dates = [(row["contract_id"], row["due_date"]) for row in after_second["occurrences"]]
    assert len(due_dates) == len(set(due_dates)) == 4


# ---------------------------------------------------------------------------
# 7. Doppelzuordnung
# ---------------------------------------------------------------------------


async def test_one_booking_is_claimed_by_only_one_contract(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Zwei gleichnamige Vertraege, eine Buchung im Fenster beider. Ohne
    Arbitrierung entschiede die Verarbeitungsreihenfolge - und der partielle
    Unique-Index quittierte das mit einem IntegrityError statt mit dem besseren
    Treffer."""
    household_id = household_headers["X-Household-Id"]
    booked = TODAY - timedelta(days=5)
    exact = _create_contract(
        household_headers,
        name="Musterfunk Festnetz",
        amount_minor=3_995,
        next_due_date=booked.isoformat(),
    )
    off_by_three_days = _create_contract(
        household_headers,
        name="Musterfunk Festnetz",
        amount_minor=3_995,
        next_due_date=(booked - timedelta(days=3)).isoformat(),
    )
    transaction_id = await _seed_transaction(
        household_id, account_id, booked, -3_995, payee_label="Musterfunk Festnetz"
    )

    result = await _reconcile(household_id)
    assert (result.matched_count, result.missing_count) == (1, 1)

    claims = await _rows(
        "select contract_id from contract_occurrences "
        "where household_id = :household_id and transaction_id = :transaction_id",
        household_id=household_id,
        transaction_id=transaction_id,
    )
    assert len(claims) == 1
    assert str(claims[0]["contract_id"]) == exact["id"]

    [loser_occurrence] = await _occurrences(off_by_three_days["id"])
    assert loser_occurrence["status"] == "missing"


async def test_a_contract_past_its_term_does_not_block_its_successor(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Alter Tarif und Nachfolgevertrag beim selben Anbieter.

    Der alte ist ueber seine Laufzeit hinaus und verwirft jeden Kandidaten
    (`match_contract` steigt vor der Bewertung aus). Bote er trotzdem mit,
    gewaenne er die Buchung nach Datumsnaehe, schriebe keine Faelligkeit - und
    naehme sie dem Nachfolger trotzdem weg. Der bekaeme eine erfundene
    Fehlperiode, und die Buchung bliebe dauerhaft unzugeordnet.
    """
    household_id = household_headers["X-Household-Id"]
    booked = TODAY - timedelta(days=10)
    expired = _create_contract(
        household_headers,
        name="Musterfunk Mobil",
        amount_minor=2_999,
        # Genau auf dem Buchungstag: nach Datumsnaehe gewinnt der alte Vertrag.
        next_due_date=booked.isoformat(),
        status="cancelled",
        cancelled_on=(TODAY - timedelta(days=90)).isoformat(),
        ends_on=(booked - timedelta(days=5)).isoformat(),
    )
    successor = _create_contract(
        household_headers,
        name="Musterfunk Mobil",
        amount_minor=2_999,
        next_due_date=(booked + timedelta(days=3)).isoformat(),
    )
    transaction_id = await _seed_transaction(
        household_id, account_id, booked, -2_999, payee_label="Musterfunk Mobil"
    )

    result = await _reconcile(household_id)
    assert (result.matched_count, result.missing_count) == (1, 0)

    claims = await _rows(
        "select contract_id from contract_occurrences "
        "where household_id = :household_id and transaction_id = :transaction_id",
        household_id=household_id,
        transaction_id=transaction_id,
    )
    assert [str(row["contract_id"]) for row in claims] == [successor["id"]]

    successor_row = await _contract_row(successor["id"])
    assert successor_row["missed_periods"] == 0
    assert await _occurrences(expired["id"]) == []
    assert (await _contract_row(expired["id"]))["status"] == "ended"


# ---------------------------------------------------------------------------
# 8. Rueckwirkender Erstlauf
# ---------------------------------------------------------------------------


async def test_creating_a_contract_backfills_history_and_a_price_ladder(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ohne den Backfill zeigt die Detailseite direkt nach dem Anlegen eine
    leere Historie, obwohl die Buchungen seit Jahren im Konto stehen."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, "Stadtwerke Nord")
    dues = [_months_back(TODAY, months) for months in (4, 3, 2, 1)]
    for due, amount in zip(dues, (-1_099, -1_099, -1_299, -1_299), strict=True):
        await _seed_transaction(household_id, account_id, due, amount, payee_id=payee_id)

    contract = _create_contract(
        household_headers,
        name="Stadtwerke Nord",
        payee_id=payee_id,
        amount_minor=1_299,
        next_due_date=TODAY.isoformat(),
    )

    occurrences = await _occurrences(contract["id"])
    assert [item["due_date"] for item in occurrences] == dues
    assert {item["status"] for item in occurrences} == {"matched"}
    assert [item["amount_minor"] for item in occurrences] == [-1_099, -1_099, -1_299, -1_299]

    row = await _contract_row(contract["id"])
    assert row["first_seen_date"] == dues[0]
    assert row["last_seen_date"] == dues[-1]
    # Der Backfill fasst weder den Sollbetrag noch den Fehlzaehler an.
    assert row["amount_minor"] == 1_299
    assert row["missed_periods"] == 0

    history = await _history(contract["id"])
    assert [entry["amount_minor"] for entry in history] == [1_099, 1_299, 1_299]
    assert [entry["change_reason"] for entry in history] == ["backfill", "backfill", "initial"]
    assert history[0]["valid_from"] == dues[0]
    assert history[1]["valid_from"] == dues[2]
    assert history[1]["delta_minor"] == 200
    assert history[2]["valid_to"] is None

    # Ein frisch angelegter Vertrag bringt keinen erfundenen Alarmverlauf mit.
    assert [event["kind"] for event in await _events(contract["id"])] == ["created"]


async def test_backfilling_twice_keeps_one_ladder(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Nutzer bestaetigt einen erkannten Vertrag erneut - die Treppe darf
    dadurch nicht doppelt entstehen."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _create_payee(household_id, "Stadtwerke Nord")
    dues = [_months_back(TODAY, months) for months in (3, 2, 1)]
    for due, amount in zip(dues, (-1_099, -1_299, -1_299), strict=True):
        await _seed_transaction(household_id, account_id, due, amount, payee_id=payee_id)

    contract = _create_contract(
        household_headers,
        name="Stadtwerke Nord",
        payee_id=payee_id,
        amount_minor=1_299,
        next_due_date=TODAY.isoformat(),
    )
    before = await _snapshot(household_id)

    session_factory = get_session_factory()
    async with session_factory() as session:
        await contract_matching_service.backfill_contract(
            session, UUID(household_id), UUID(contract["id"]), today=TODAY
        )
        await session.commit()

    assert await _snapshot(household_id) == before


# ---------------------------------------------------------------------------
# 9. Review-Inbox
# ---------------------------------------------------------------------------


async def test_missed_contracts_become_a_question_in_the_review_inbox(
    household_headers: dict[str, str]
) -> None:
    """Der Abgleich kann nur zaehlen, dass nichts kam. Ob gekuendigt, gestundet
    oder nur verschoben, weiss allein der Nutzer - deshalb eine Frage statt
    einer stillen Statusaenderung."""
    household_id = household_headers["X-Household-Id"]
    due = (TODAY - timedelta(days=50)).isoformat()
    really_over = _create_contract(
        household_headers, name="Zeitschrift Nord", amount_minor=990, next_due_date=due
    )
    still_running = _create_contract(
        household_headers, name="Versicherung Sued", amount_minor=4_500, next_due_date=due
    )
    await _reconcile(household_id)

    inbox = client.get("/v1/review", headers=household_headers).json()
    checks = {item["contract_id"]: item for item in inbox["contract_ended_checks"]}
    assert set(checks) == {really_over["id"], still_running["id"]}
    assert checks[really_over["id"]]["missed_periods"] == 2
    assert checks[really_over["id"]]["amount_minor"] == 990
    assert checks[really_over["id"]]["direction"] == "expense"
    # Das Feld heisst wie die Spalte und wie `Contract.next_due_date`; der Plan
    # nennt es an einer Stelle `expected_due_date`. Hier festgenagelt, damit
    # Web und Mobile eine verbindliche Schreibweise haben.
    open_due = (await _contract_row(really_over["id"]))["next_due_date"]
    assert checks[really_over["id"]]["next_due_date"] == open_due.isoformat()
    assert inbox["total_count"] >= 2

    ended = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_ended_check",
            "contract_id": really_over["id"],
            "action": "ended",
        },
        headers=household_headers,
    )
    assert ended.status_code == 200, ended.text

    resumed = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_ended_check",
            "contract_id": still_running["id"],
            "action": "active",
        },
        headers=household_headers,
    )
    assert resumed.status_code == 200, resumed.text

    ended_row = await _contract_row(really_over["id"])
    assert ended_row["status"] == "ended"
    # Der Fehlzaehler bleibt stehen: er ist die Begruendung fuer das Ende.
    assert ended_row["missed_periods"] == 2

    running_row = await _contract_row(still_running["id"])
    assert running_row["status"] == "active"
    assert running_row["missed_periods"] == 0
    # Ohne Vorziehen zaehlte der naechste Lauf dieselben Perioden erneut als
    # ausgeblieben und stellte die Frage sofort wieder.
    assert running_row["next_due_date"] >= TODAY

    assert client.get("/v1/review", headers=household_headers).json()["contract_ended_checks"] == []


async def test_a_resumed_contract_is_not_asked_about_again(
    household_headers: dict[str, str]
) -> None:
    """Gegenprobe zum Vorziehen: der naechste Abgleichslauf darf den gerade
    bestaetigten Vertrag nicht sofort wieder als ausgeblieben zaehlen."""
    household_id = household_headers["X-Household-Id"]
    contract = _create_contract(
        household_headers,
        name="Versicherung Sued",
        amount_minor=4_500,
        next_due_date=(TODAY - timedelta(days=50)).isoformat(),
    )
    await _reconcile(household_id)
    client.post(
        "/v1/review/accept",
        json={"type": "contract_ended_check", "contract_id": contract["id"], "action": "active"},
        headers=household_headers,
    )

    result = await _reconcile(household_id)
    assert result.missing_count == 0
    assert (await _contract_row(contract["id"]))["missed_periods"] == 0


# ---------------------------------------------------------------------------
# 10. Nachgereichte Zahlungen
# ---------------------------------------------------------------------------


async def test_a_booking_imported_after_the_tolerance_still_lands_in_its_period(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Der Regelfall bei periodischem Kontoauszugsimport.

    Der naechtliche Lauf schreibt die Periode als ausgeblieben ab und schiebt
    `next_due_date` weiter. Weil das Suchfenster ausschliesslich um
    `next_due_date` liegt, kaeme ein rein vorwaertsgerichteter Motor auf diese
    Periode nie zurueck - die spaeter importierte Abbuchung bliebe fuer immer
    unzugeordnet.
    """
    household_id = household_headers["X-Household-Id"]
    due = TODAY - timedelta(days=20)
    contract = _create_contract(
        household_headers, name="Netflix", amount_minor=1_799, next_due_date=due.isoformat()
    )

    first = await _reconcile(household_id)
    assert first.missing_count == 1
    row_after_first = await _contract_row(contract["id"])
    assert row_after_first["missed_periods"] == 1
    assert row_after_first["next_due_date"] > due

    booked = due + timedelta(days=1)
    transaction_id = await _seed_transaction(
        household_id, account_id, booked, -1_799, payee_label="Netflix"
    )

    second = await _reconcile(household_id)
    assert (second.matched_count, second.missing_count, second.resumed_count) == (1, 0, 1)

    [occurrence] = await _occurrences(contract["id"])
    assert occurrence["status"] == "matched"
    assert occurrence["due_date"] == due
    assert str(occurrence["transaction_id"]) == transaction_id

    row = await _contract_row(contract["id"])
    assert row["missed_periods"] == 0
    assert row["last_seen_date"] == booked
    assert row["first_seen_date"] == booked
    # Das Nachtragen darf die Faelligkeit nicht zusaetzlich fortschreiben.
    assert row["next_due_date"] == row_after_first["next_due_date"]

    [resumed] = [
        event for event in await _events(contract["id"]) if event["kind"] == "payment_resumed"
    ]
    assert resumed["payload"]["reason"] == "late_booking"
    assert resumed["payload"]["repaired_periods"] == 1

    # Und ein weiterer Lauf aendert daran nichts mehr.
    third = await _reconcile(household_id)
    assert (third.matched_count, third.missing_count) == (0, 0)
    assert len(await _occurrences(contract["id"])) == 1


async def test_late_imports_keep_a_paying_contract_alive(
    household_headers: dict[str, str], account_id: str
) -> None:
    """Ohne den Rueckblick beendet die Automatik einen Vertrag, dessen
    saemtliche Zahlungen als Buchungen in der Datenbank stehen - nur eben
    jeweils erst nach Ablauf der Toleranz importiert."""
    household_id = household_headers["X-Household-Id"]
    contract = _create_contract(
        household_headers,
        name="Discovery Plus",
        amount_minor=999,
        next_due_date=_months_back(TODAY, 3).isoformat(),
    )

    first = await _reconcile(household_id)
    assert first.missing_count >= 3
    written_off = await _occurrences(contract["id"])
    assert {item["status"] for item in written_off} == {"missing"}

    for item in written_off:
        await _seed_transaction(
            household_id,
            account_id,
            item["due_date"] + timedelta(days=1),
            -999,
            payee_label="Discovery Plus",
        )

    second = await _reconcile(household_id)
    assert second.matched_count == len(written_off)

    row = await _contract_row(contract["id"])
    assert row["status"] == "active"
    assert row["missed_periods"] == 0
    assert {item["status"] for item in await _occurrences(contract["id"])} == {"matched"}
