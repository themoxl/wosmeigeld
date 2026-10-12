"""Rueckfrage bei geaendertem Betrag (Plan Abschnitt 19.11, P5.3).

Eigene Datei statt eines Anbaus an `test_rules_phase4.py`: dort laeuft alles
ueber oeffentliche Endpunkte, hier muessen `contract_events`-Zeilen mit einer
gezielt gebauten `price_change`-Nutzlast direkt in die Datenbank geschrieben
werden - es gibt keinen Endpunkt, der ein solches Ereignis erzeugt, und ein
vollstaendiger Abgleichslauf nur zum Erzeugen der Nutzlast waere ein Test des
Abgleichs statt der Rueckfrage. Dieselbe Bauweise verwendet
`test_contracts_reversibility.py`. Ausserdem bleiben die Abnahmekriterien zu
P5.3 so an einer Stelle auffindbar.

Der fachliche Anlassfall ist ein Festnetzvertrag mit
49,95 EUR -> 39,95 EUR. Eine SENKUNG - genau der Fall, den ein
Meldungstext "kostet ab sofort mehr" und eine stille Uebernahme beide falsch
behandeln.
"""

import json
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

Headers = dict[str, str]

# Fester Bezugstag, damit die Reihenfolge der Eintraege nicht vom Testlauf
# abhaengt. Der Abgleich schreibt `occurred_on` als Buchungstag.
BOOKED = date.today() - timedelta(days=3)


@pytest.fixture()
def household_headers() -> Headers:
    token = _signup(f"cac-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households",
        json={"name": "Betragsaenderung-Testhaushalt"},
        headers=_auth_headers(token),
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def _create_contract(headers: Headers, name: str, amount_minor: int) -> str:
    response = client.post(
        "/v1/contracts",
        json={
            "name": name,
            "amount_minor": amount_minor,
            "frequency": "monthly",
            "next_due_date": (date.today() + timedelta(days=10)).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(text(sql), params)
        mapped = [dict(row._mapping) for row in result]
        await session.commit()
        return mapped


async def _insert_price_change(
    household_id: str,
    contract_id: str,
    old_minor: int,
    new_minor: int,
    *,
    occurred_on: date = BOOKED,
) -> str:
    """Schreibt genau die Nutzlast, die `contract_matching_service._hit_outcome`
    erzeugt - inklusive der vorzeichenbehafteten Felder."""
    payload = json.dumps(
        {
            "old_minor": old_minor,
            "new_minor": new_minor,
            "delta_minor": new_minor - old_minor,
            "pct": round((new_minor - old_minor) * 100.0 / old_minor, 2),
        }
    )
    rows = await _rows(
        "insert into contract_events (household_id, contract_id, kind, occurred_on, payload) "
        "values (:household_id, :contract_id, 'price_change', :occurred_on, "
        "cast(:payload as jsonb)) returning id",
        household_id=household_id,
        contract_id=contract_id,
        occurred_on=occurred_on,
        payload=payload,
    )
    return str(rows[0]["id"])


def _checks(headers: Headers) -> list[dict[str, Any]]:
    response = client.get("/v1/review", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["contract_amount_change_checks"])


def _confirm(headers: Headers, event_id: str) -> Any:
    return client.post(
        "/v1/review/accept",
        json={
            "type": "contract_amount_change_check",
            "event_id": event_id,
            "action": "confirmed",
        },
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Sichtbarkeit
# ---------------------------------------------------------------------------


async def test_a_price_cut_becomes_a_question_instead_of_a_silent_takeover(
    household_headers: Headers,
) -> None:
    """Der Musterfunk-Fall: 49,95 EUR -> 39,95 EUR.

    Eine Senkung muss in der Inbox stehen. Genau sie zieht den Sollbetrag nach
    unten, und genau sie faellt sonst niemandem auf - eine Erhoehung merkt der
    Nutzer am Kontostand, eine Senkung nicht.
    """
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Musterfunk Festnetz", 3995)
    event_id = await _insert_price_change(household_id, contract_id, 4995, 3995)

    [item] = _checks(household_headers)

    assert item["type"] == "contract_amount_change_check"
    assert item["event_id"] == event_id
    assert item["contract_id"] == contract_id
    assert item["name"] == "Musterfunk Festnetz"
    assert item["old_amount_minor"] == 4995
    assert item["new_amount_minor"] == 3995
    # Vorzeichenbehaftet: negativ heisst guenstiger geworden.
    assert item["delta_minor"] == -1000
    assert item["delta_pct"] == pytest.approx(-20.02, abs=0.01)
    assert item["direction"] == "expense"
    assert item["frequency"] == "monthly"
    assert item["occurred_on"] == BOOKED.isoformat()


async def test_a_price_rise_is_asked_the_same_way(household_headers: Headers) -> None:
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Stadtwerke Strom", 5300)
    await _insert_price_change(household_id, contract_id, 5000, 5300)

    [item] = _checks(household_headers)

    assert item["delta_minor"] == 300
    assert item["delta_pct"] == pytest.approx(6.0, abs=0.01)


async def test_the_check_counts_towards_the_inbox_total(household_headers: Headers) -> None:
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Allianz Hausrat", 3995)
    await _insert_price_change(household_id, contract_id, 4995, 3995)

    inbox = client.get("/v1/review", headers=household_headers).json()
    assert inbox["total_count"] == 1


# ---------------------------------------------------------------------------
# Erheblichkeit
# ---------------------------------------------------------------------------


async def test_only_a_change_that_is_worth_asking_about_reaches_the_inbox(
    household_headers: Headers,
) -> None:
    """Beide Bedingungen muessen zutreffen, nicht eine von beiden.

    2 EUR auf 500 EUR ist keine Frage, und 5 % von 3 EUR auch nicht. Wer
    solche Zeilen zu sehen bekommt, hoert auf, die Liste zu lesen - und dann
    bleibt auch die eine Aenderung liegen, auf die es ankommt.
    """
    household_id = household_headers["X-Household-Id"]

    # 1,50 EUR: prozentual deutlich (7,5 %), absolut unter 2 EUR.
    small_absolute = _create_contract(household_headers, "Zeitschrift", 1850)
    await _insert_price_change(household_id, small_absolute, 2000, 1850)

    # 3,00 EUR auf 500 EUR: absolut ueber der Grenze, prozentual 0,6 %.
    small_relative = _create_contract(household_headers, "Miete Stellplatz", 49700)
    await _insert_price_change(household_id, small_relative, 50000, 49700)

    # Genau an beiden Grenzen: 2,00 EUR und exakt 5 %. Das Gleichheitszeichen
    # gehoert zur Schwelle - mit einem Gleitkommafaktor 0.05 fiele dieser Fall
    # heraus (4000 * 0.05 = 200.00000000000003).
    exactly_at_limit = _create_contract(household_headers, "Fitnessstudio", 4200)
    limit_event = await _insert_price_change(household_id, exactly_at_limit, 4000, 4200)

    items = _checks(household_headers)

    assert [item["event_id"] for item in items] == [limit_event]


# ---------------------------------------------------------------------------
# Beantworten
# ---------------------------------------------------------------------------


async def test_a_confirmed_change_disappears_and_silences_the_push(
    household_headers: Headers,
) -> None:
    """Die Bestaetigung setzt `contract_events.acknowledged_at`.

    Dieselbe Spalte liest der naechtliche Push-Filter
    (worker/notifications/contract_alerts.py). Wer hier antwortet, bekommt den
    Hinweis abends bewusst nicht noch einmal auf den Sperrbildschirm - deshalb
    prueft dieser Test die Spalte und nicht nur das Verschwinden aus der Liste.
    """
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Musterfunk Festnetz", 3995)
    event_id = await _insert_price_change(household_id, contract_id, 4995, 3995)

    accepted = _confirm(household_headers, event_id)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    assert _checks(household_headers) == []

    rows = await _rows(
        "select acknowledged_at from contract_events where id = :id", id=event_id
    )
    assert rows[0]["acknowledged_at"] is not None

    # Ein zweiter Klick ist eine langsame Verbindung, keine Fehlbedienung.
    again = _confirm(household_headers, event_id)
    assert again.status_code == 200, again.text
    rows_after = await _rows(
        "select acknowledged_at from contract_events where id = :id", id=event_id
    )
    assert rows_after[0]["acknowledged_at"] == rows[0]["acknowledged_at"]


async def test_taking_the_amount_back_is_not_offered_yet(
    household_headers: Headers,
) -> None:
    """'wrong_amount' fehlt absichtlich (siehe `AcceptContractAmountChange`).

    Ein unbekannter Wert muss abgelehnt werden statt still nichts zu tun -
    sonst glaubte die Oberflaeche, die Preisstufe sei zurueckgenommen worden.
    """
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Musterfunk Festnetz", 3995)
    event_id = await _insert_price_change(household_id, contract_id, 4995, 3995)

    response = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_amount_change_check",
            "event_id": event_id,
            "action": "wrong_amount",
        },
        headers=household_headers,
    )
    assert response.status_code == 422

    # Der Eintrag bleibt offen - eine abgelehnte Eingabe darf nichts geaendert
    # haben.
    assert [item["event_id"] for item in _checks(household_headers)] == [event_id]


async def test_an_unknown_event_cannot_be_confirmed(household_headers: Headers) -> None:
    response = _confirm(household_headers, str(uuid.uuid4()))
    assert response.status_code == 404


async def test_a_foreign_household_neither_sees_nor_confirms_the_change(
    household_headers: Headers,
) -> None:
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Musterfunk Festnetz", 3995)
    event_id = await _insert_price_change(household_id, contract_id, 4995, 3995)

    token = _signup(f"cac-other-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    other_household = client.post(
        "/v1/households", json={"name": "Fremder Haushalt"}, headers=_auth_headers(token)
    ).json()
    other_headers = {**_auth_headers(token), "X-Household-Id": other_household["id"]}

    assert _checks(other_headers) == []
    assert _confirm(other_headers, event_id).status_code == 404
    # Und die Zeile des Eigentuemers ist unberuehrt geblieben.
    assert [item["event_id"] for item in _checks(household_headers)] == [event_id]


# ---------------------------------------------------------------------------
# Ausschluesse
# ---------------------------------------------------------------------------


async def test_a_merged_contract_asks_nothing(household_headers: Headers) -> None:
    """Ein zusammengefuehrter Vertrag ist ueberall sonst ausgeblendet
    (Soft-Merge, Abschnitt 19.7). Eine Rueckfrage zu ihm liefe ins Leere: der
    Nutzer findet den Vertrag gar nicht mehr, den er bestaetigen soll."""
    household_id = household_headers["X-Household-Id"]
    target_id = _create_contract(household_headers, "Musterfunk Festnetz", 3995)
    merged_id = _create_contract(household_headers, "Musterfunk Festnetz alt", 4995)
    event_id = await _insert_price_change(household_id, merged_id, 4995, 3995)

    assert [item["event_id"] for item in _checks(household_headers)] == [event_id]

    await _rows(
        "update contracts set merged_into_id = :target, merged_at = now() "
        "where id = :id and household_id = :household_id returning id",
        target=target_id,
        id=merged_id,
        household_id=household_id,
    )

    assert _checks(household_headers) == []
    # Auch die Bestaetigung greift nicht mehr ins Leere, sondern meldet 404.
    assert _confirm(household_headers, event_id).status_code == 404


async def test_an_ended_contract_is_no_longer_asked(household_headers: Headers) -> None:
    """Beendet heisst: der Betrag ist Geschichte, keine offene Frage. Dieselbe
    Statusmenge wie der erzeugende Abgleichslauf
    (`contract_matching_service.RECONCILE_STATUSES`) und der Push-Filter."""
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Zeitung Abo", 3995)
    await _insert_price_change(household_id, contract_id, 4995, 3995)

    assert len(_checks(household_headers)) == 1

    await _rows(
        "update contracts set status = 'ended' where id = :id and household_id = :hid "
        "returning id",
        id=contract_id,
        hid=household_id,
    )

    assert _checks(household_headers) == []


async def test_a_broken_payload_does_not_take_the_whole_inbox_down(
    household_headers: Headers,
) -> None:
    """Ein ungeschuetzter Cast auf eine unerwartete Nutzlast wuerde die Abfrage
    mit 22P02 abbrechen - und damit die GESAMTE Review-Inbox, nicht nur diese
    eine Zeile."""
    household_id = household_headers["X-Household-Id"]
    contract_id = _create_contract(household_headers, "Kaputte Nutzlast", 3995)
    await _rows(
        "insert into contract_events (household_id, contract_id, kind, occurred_on, payload) "
        "values (:household_id, :contract_id, 'price_change', :occurred_on, "
        "cast(:payload as jsonb)) returning id",
        household_id=household_id,
        contract_id=contract_id,
        occurred_on=BOOKED,
        payload=json.dumps({"old_minor": "unbekannt", "new_minor": None}),
    )

    good_contract = _create_contract(household_headers, "Musterfunk Festnetz", 3995)
    good_event = await _insert_price_change(household_id, good_contract, 4995, 3995)

    assert [item["event_id"] for item in _checks(household_headers)] == [good_event]
