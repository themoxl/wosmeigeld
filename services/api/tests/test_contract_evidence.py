"""Belege je Preisstufe, freie Buchungen, Nachtragen und Neuberechnung.

docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 19.7 ("Fehlzuordnung ohne Merge")
und 19.11 (Sprachmodell).

Der Referenzfall ist der Festnetzvertrag aus 19.1:

* vier zugeordnete Buchungen in vier aufeinanderfolgenden Monaten, die erste
  davon 39,25 EUR (anteiliger erster Monat), die drei folgenden 39,95 EUR,
* drei aeltere Buchungen derselben Mandatsreferenz (die drei Monate davor, je
  49,95 EUR) voellig unzugeordnet daneben,
* ein Betragsverlauf aus zwei Zeilen, die BEIDE am Tag der Datenuebernahme
  beginnen, keine Quellbuchung nennen und deshalb nichts belegen - die
  49,95-EUR-Zeile sogar mit einem Zeitraum der Laenge null.

Abnahme: nach dem Zuordnen der drei freien Buchungen und einer Neuberechnung
steht dort 49,95 EUR ab dem ersten und 39,95 EUR ab dem vierten Monat.

Gearbeitet wird in einem Testhaushalt.
"""

import calendar
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.schemas.contract import ContractEvidenceTransaction
from app.services import contract_ai_crosscheck_service as crosscheck
from app.services import contract_service
from tests import ki_attrappe
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

Headers = dict[str, str]

TODAY = date.today()

PROVIDER = "Musterfunk Deutschland GmbH"
FESTNETZ_PURPOSE = "MUSTERFUNK FESTNETZ RECHNUNG MREF+DE000200000100000000000000001234567"
MOBILFUNK_PURPOSE = "MUSTERFUNK MOBILFUNK RECHNUNG MREF+DE000200000100000000000000007654321"


# ---------------------------------------------------------------------------
# Kalenderhilfen: der Referenzfall haengt an Monatsabstaenden, nicht an festen
# Kalenderdaten - sonst laeuft die Testsuite nur in einem bestimmten Monat.
# ---------------------------------------------------------------------------


def _month_day(months_back: int, day_of_month: int) -> date:
    total = TODAY.year * 12 + (TODAY.month - 1) - months_back
    year, month = divmod(total, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day_of_month, last))


# Die sieben Buchungen des Referenzfalls, aeltester zuerst.
JAN = _month_day(7, 15)
FEB = _month_day(6, 15)
MAR = _month_day(5, 15)
APR = _month_day(4, 15)
MAY = _month_day(3, 15)
JUN = _month_day(2, 15)
JUL = _month_day(1, 15)


# ---------------------------------------------------------------------------
# Fixtures und Helfer
# ---------------------------------------------------------------------------


def _household(name: str) -> Headers:
    token = _signup(f"evidence-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-4")
    household = client.post(
        "/v1/households", json={"name": name}, headers=_auth_headers(token)
    ).json()
    return {**_auth_headers(token), "X-Household-Id": household["id"]}


def household_headers() -> Headers:
    return _household("Beleg-Testhaushalt")


def _account(headers: Headers) -> str:
    account = client.post(
        "/v1/accounts",
        json={"name": "Girokonto", "account_type": "checking"},
        headers=headers,
    ).json()
    return str(account["id"])


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


async def _insert_transaction(
    household_id: str,
    account_id: str,
    booked_date: date,
    amount_minor: int,
    *,
    payee_label: str = PROVIDER,
    purpose_raw: str = FESTNETZ_PURPOSE,
) -> str:
    """Buchung per SQL: nur so laesst sich `purpose_raw` setzen, und daran
    haengt die Mandatsreferenz. `payee_id` bleibt leer - genau wie bei
    Buchungen aus einer Datenuebernahme."""
    rows = await _rows(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "payee_label, purpose_raw) values (cast(:household_id as uuid), "
        "cast(:account_id as uuid), :booked_date, :amount_minor, :payee_label, :purpose_raw) "
        "returning id",
        household_id=household_id,
        account_id=account_id,
        booked_date=booked_date,
        amount_minor=amount_minor,
        payee_label=payee_label,
        purpose_raw=purpose_raw,
    )
    return str(rows[0]["id"])


def _create_contract(headers: Headers, name: str, amount_minor: int, due_day: int) -> str:
    response = client.post(
        "/v1/contracts",
        json={
            "name": name,
            "provider_label": name,
            "amount_minor": amount_minor,
            "frequency": "monthly",
            "due_day": due_day,
            "next_due_date": _month_day(0, due_day).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _assign(household_id: str, contract_id: str, transaction_id: str, booked: date) -> None:
    await _execute(
        "insert into contract_occurrences (household_id, contract_id, due_date, status, "
        "transaction_id, booked_date, amount_minor, matched_at) values "
        "(cast(:household_id as uuid), cast(:contract_id as uuid), :due_date, 'matched', "
        "cast(:transaction_id as uuid), :booked_date, "
        "(select amount_minor from transactions where id = cast(:transaction_id as uuid)), "
        "now())",
        household_id=household_id,
        contract_id=contract_id,
        due_date=booked,
        transaction_id=transaction_id,
        booked_date=booked,
    )


async def _replace_history_with_migration_state(household_id: str, contract_id: str) -> None:
    """Baut den Verlauf nach, den die Datenuebernahme hinterlassen hat.

    Zwei 'initial'-Zeilen, beide ab heute, ohne Quellbuchung, die teurere mit
    einem Zeitraum der Laenge null.
    """
    await _execute(
        "delete from contract_amount_history where contract_id = cast(:contract_id as uuid)",
        contract_id=contract_id,
    )
    await _execute(
        "insert into contract_amount_history (household_id, contract_id, amount_minor, "
        "valid_from, valid_to, change_reason) values "
        "(cast(:household_id as uuid), cast(:contract_id as uuid), 4995, :today, :today, "
        "'initial')",
        household_id=household_id,
        contract_id=contract_id,
        today=TODAY,
    )
    await _execute(
        "insert into contract_amount_history (household_id, contract_id, amount_minor, "
        "valid_from, delta_minor, change_reason) values "
        "(cast(:household_id as uuid), cast(:contract_id as uuid), 3995, :today, -1000, "
        "'initial')",
        household_id=household_id,
        contract_id=contract_id,
        today=TODAY,
    )


class Musterfunk:
    """Der geseedete Referenzfall."""

    def __init__(
        self,
        headers: Headers,
        account_id: str,
        contract_id: str,
        free: list[str],
        assigned: list[str],
    ):
        self.headers = headers
        self.household_id = headers["X-Household-Id"]
        self.account_id = account_id
        self.contract_id = contract_id
        self.free = free
        self.assigned = assigned


async def _seed_musterfunk(*, with_mobilfunk: bool = False) -> Musterfunk:
    headers = household_headers()
    household_id = headers["X-Household-Id"]
    account_id = _account(headers)

    # Erst der Vertrag, dann die Buchungen: sonst zieht der rueckwirkende
    # Erstlauf beim Anlegen die Buchungen selbst ein und der zu reparierende
    # Ausgangszustand entstuende gar nicht erst.
    contract_id = _create_contract(headers, PROVIDER, 3995, 15)

    free: list[str] = []
    for booked in (JAN, FEB, MAR):
        free.append(await _insert_transaction(household_id, account_id, booked, -4995))

    assigned: list[str] = []
    for booked, amount in ((APR, -3925), (MAY, -3995), (JUN, -3995), (JUL, -3995)):
        transaction_id = await _insert_transaction(household_id, account_id, booked, amount)
        await _assign(household_id, contract_id, transaction_id, booked)
        assigned.append(transaction_id)

    if with_mobilfunk:
        for booked in (JAN, FEB, MAR, APR, MAY, JUN, JUL):
            await _insert_transaction(
                household_id,
                account_id,
                booked + timedelta(days=1),
                -9990,
                purpose_raw=MOBILFUNK_PURPOSE,
            )

    await _replace_history_with_migration_state(household_id, contract_id)
    # Zeitraum wie nach der Datenuebernahme: er kennt nur die vier zugeordneten
    # Buchungen, die drei aelteren fehlen ihm.
    await _execute(
        "update contracts set first_seen_date = :first_seen, last_seen_date = :last_seen "
        "where id = cast(:contract_id as uuid)",
        contract_id=contract_id,
        first_seen=APR,
        last_seen=JUL,
    )
    return Musterfunk(headers, account_id, contract_id, free, assigned)


def _evidence(musterfunk: Musterfunk) -> dict[str, Any]:
    response = client.get(
        f"/v1/contracts/{musterfunk.contract_id}/amount-history/evidence",
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _free(musterfunk: Musterfunk, **params: Any) -> dict[str, Any]:
    response = client.get(
        f"/v1/contracts/{musterfunk.contract_id}/free-transactions",
        params=params,
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _recalculate(musterfunk: Musterfunk) -> dict[str, Any]:
    response = client.post(
        f"/v1/contracts/{musterfunk.contract_id}/amount-history/recalculate",
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _reassign(musterfunk: Musterfunk, transaction_id: str) -> None:
    response = client.post(
        f"/v1/contracts/transactions/{transaction_id}/reassign",
        json={"target_contract_id": musterfunk.contract_id},
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text


def _ladder(stages: list[dict[str, Any]]) -> list[tuple[int, str, str | None]]:
    return [(item["amount_minor"], item["valid_from"], item["valid_to"]) for item in stages]


# ---------------------------------------------------------------------------
# Herleitung der Preistreppe (ohne Datenbank)
# ---------------------------------------------------------------------------


def _tx(booked_date: date, amount_minor: int) -> ContractEvidenceTransaction:
    return ContractEvidenceTransaction(
        transaction_id=uuid.uuid4(),
        booked_date=booked_date,
        amount_minor=amount_minor,
        payee_label=PROVIDER,
        purpose_excerpt=None,
    )


def test_the_ladder_follows_the_actual_bookings() -> None:
    """Der Referenzfall als reine Rechnung: zwei Stufen, nicht drei.

    Die 39,25 EUR des ersten Monats nach der Senkung liegen innerhalb der
    Toleranz von 39,95 EUR und eroeffnen deshalb keine eigene Stufe. Als Betrag
    der Stufe gewinnt der haeufigere Wert - der Vertrag kostet 39,95 EUR, die
    anteilige erste Zahlung ist der Ausreisser.
    """
    plans = contract_service.plan_amount_stages(
        [
            _tx(JAN, -4995),
            _tx(FEB, -4995),
            _tx(MAR, -4995),
            _tx(APR, -3925),
            _tx(MAY, -3995),
            _tx(JUN, -3995),
            _tx(JUL, -3995),
        ]
    )

    assert [(item.amount_minor, item.valid_from, item.valid_to) for item in plans] == [
        (4995, JAN, APR - timedelta(days=1)),
        (3995, APR, None),
    ]
    assert [item.transaction_count for item in plans] == [3, 4]
    assert [item.delta_minor for item in plans] == [None, -1000]


def test_a_manual_stage_is_a_floor_for_the_derivation() -> None:
    """Ab der manuellen Zeile schreibt die Ableitung nichts mehr; die letzte
    hergeleitete Stufe endet am Vortag (Leitsatz 5)."""
    plans = contract_service.plan_amount_stages(
        [_tx(JAN, -4995), _tx(FEB, -4995), _tx(APR, -3925), _tx(MAY, -3995)],
        manual_from=APR,
    )

    assert [(item.amount_minor, item.valid_from, item.valid_to) for item in plans] == [
        (4995, JAN, APR - timedelta(days=1))
    ]


def test_without_bookings_there_is_no_ladder() -> None:
    assert contract_service.plan_amount_stages([]) == []


def test_the_default_search_word_is_the_brand_not_the_legal_form() -> None:
    """Ein Abo kann ueber einen Zahlungsdienstleister laufen: der Empfaenger
    heisst dann "PayPal Europe S.a.r.l. et C", der Anbieter steht nur im
    Verwendungszweck. Ein ILIKE auf den vollstaendigen Namen findet ihn dort
    nicht."""
    assert contract_service.default_search_text("Crunchyroll, LLC") == "Crunchyroll"
    assert contract_service.default_search_text("Musterfunk Deutschland GmbH") == "Musterfunk"
    assert contract_service.default_search_text("Musterpost Verlag GmbH") == "Musterpost"
    assert contract_service.default_search_text("MaxEnergy GmbH") == "MaxEnergy"
    assert contract_service.default_search_text(None) is None


# ---------------------------------------------------------------------------
# Belege je Preisstufe
# ---------------------------------------------------------------------------


async def test_migrated_stages_are_marked_as_unbacked() -> None:
    """Der heutige Zustand, sichtbar gemacht statt geraten.

    Beide Stufen beginnen am Uebernahmetag und nennen keine Quellbuchung; alle
    vier zugeordneten Buchungen liegen vor ihnen. Der Nutzer muss beides sehen.
    """
    musterfunk = await _seed_musterfunk()
    body = _evidence(musterfunk)

    assert len(body["stages"]) == 2
    assert body["stages_without_evidence"] == 2
    assert body["assigned_transaction_count"] == 4
    for stage in body["stages"]:
        assert stage["has_evidence"] is False
        assert stage["has_source_transaction"] is False
        assert stage["transactions"] == []
        assert stage["transaction_count"] == 0

    outside = body["transactions_outside_stages"]
    assert [item["booked_date"] for item in outside] == [
        APR.isoformat(),
        MAY.isoformat(),
        JUN.isoformat(),
        JUL.isoformat(),
    ]
    # Vorzeichenrichtig: eine Ausgabe bleibt eine Ausgabe.
    assert all(item["amount_minor"] < 0 for item in outside)


async def test_a_recalculated_stage_names_its_bookings() -> None:
    """Nach der Neuberechnung traegt jede Stufe ihre Belege - und die Buchung,
    die sie eroeffnet hat, ist als solche markiert."""
    musterfunk = await _seed_musterfunk()
    for transaction_id in musterfunk.free:
        _reassign(musterfunk, transaction_id)
    _recalculate(musterfunk)

    body = _evidence(musterfunk)
    assert body["stages_without_evidence"] == 0
    assert body["transactions_outside_stages"] == []

    first, second = body["stages"]
    assert first["stage"]["amount_minor"] == 4995
    assert first["transaction_count"] == 3
    assert second["stage"]["amount_minor"] == 3995
    assert second["transaction_count"] == 4
    for stage in (first, second):
        assert stage["has_evidence"] is True
        assert [item["is_stage_source"] for item in stage["transactions"]].count(True) == 1


async def test_evidence_of_a_foreign_household_is_not_readable() -> None:
    musterfunk = await _seed_musterfunk()
    stranger = household_headers()

    response = client.get(
        f"/v1/contracts/{musterfunk.contract_id}/amount-history/evidence", headers=stranger
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "contract_not_found"


# ---------------------------------------------------------------------------
# Freie Buchungen
# ---------------------------------------------------------------------------


async def test_the_default_search_finds_exactly_the_missing_bookings() -> None:
    """Die Vorbelegung findet die drei 49,95-EUR-Buchungen, obwohl der Vertrag
    39,95 EUR kostet: das Band umschliesst die bekannten Preisstufen weit genug,
    sonst blieben genau die gesuchten Buchungen unsichtbar."""
    musterfunk = await _seed_musterfunk()
    body = _free(musterfunk)

    assert body["total"] == 3
    assert body["truncated"] is False
    assert {item["transaction_id"] for item in body["items"]} == set(musterfunk.free)
    assert body["applied_filter"]["search"] == "Musterfunk"
    assert body["applied_filter"]["from_contract_defaults"] is True
    assert body["applied_filter"]["amount_min_minor"] <= 4995
    assert body["applied_filter"]["amount_max_minor"] >= 4995


async def test_assigned_bookings_never_show_up_as_free() -> None:
    """Weder die vier bereits zugeordneten noch - nach dem Nachtragen - die drei
    nachgetragenen Buchungen duerfen erneut als frei erscheinen."""
    musterfunk = await _seed_musterfunk()
    before = _free(musterfunk)
    assert set(musterfunk.assigned).isdisjoint(
        {item["transaction_id"] for item in before["items"]}
    )

    for transaction_id in musterfunk.free:
        _reassign(musterfunk, transaction_id)

    after = _free(musterfunk)
    assert after["items"] == []
    assert after["total"] == 0


async def test_a_detached_booking_stays_out_of_the_free_list() -> None:
    """Ausdruecklich geloest ist nicht dasselbe wie frei: der Override ist
    bindend, bis der Nutzer ihn selbst aendert (Leitsatz 5)."""
    musterfunk = await _seed_musterfunk()
    detached = musterfunk.free[0]
    response = client.post(
        f"/v1/contracts/transactions/{detached}/reassign",
        json={"target_contract_id": None},
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text

    body = _free(musterfunk)
    assert detached not in {item["transaction_id"] for item in body["items"]}
    assert body["total"] == 2


async def test_the_filters_narrow_period_amount_and_text() -> None:
    musterfunk = await _seed_musterfunk()
    # Eine fremde freie Buchung im selben Betragsband und Zeitraum.
    await _insert_transaction(
        musterfunk.household_id,
        musterfunk.account_id,
        FEB,
        -4995,
        payee_label="Stadtwerke Hamburg",
        purpose_raw="ABSCHLAG STROM",
    )

    # Zeitraum
    period = _free(musterfunk, date_from=MAR.isoformat())
    assert {item["booked_date"] for item in period["items"]} == {MAR.isoformat()}

    # Betrag
    amount = _free(musterfunk, amount_max_minor=4000)
    assert amount["items"] == []
    assert amount["total"] == 0

    # Text: die Vorbelegung "Musterfunk" schliesst die Stadtwerke bereits aus ...
    assert _free(musterfunk)["total"] == 3
    # ... und ohne Vorbelegung taucht sie auf.
    everything = _free(musterfunk, use_contract_defaults=False)
    assert everything["total"] == 4
    assert everything["applied_filter"]["search"] is None
    # ... ein eigener Suchtext gewinnt gegen die Vorbelegung.
    stadtwerke = _free(musterfunk, search="Stadtwerke")
    assert stadtwerke["total"] == 1
    assert stadtwerke["items"][0]["payee_label"] == "Stadtwerke Hamburg"


async def test_the_free_list_pages_and_reports_the_full_count() -> None:
    musterfunk = await _seed_musterfunk()
    first = _free(musterfunk, limit=2)
    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert first["truncated"] is True

    second = _free(musterfunk, limit=2, offset=2)
    assert second["total"] == 3
    assert len(second["items"]) == 1
    assert second["truncated"] is False


async def test_free_bookings_stay_inside_the_household() -> None:
    """Zwei Haushalte mit gleichem Anbieter und gleichem Betrag: keiner sieht
    die Buchungen des anderen."""
    musterfunk = await _seed_musterfunk()
    other = await _seed_musterfunk()

    mine = _free(musterfunk)
    theirs = _free(other)
    assert {item["transaction_id"] for item in mine["items"]} == set(musterfunk.free)
    assert {item["transaction_id"] for item in theirs["items"]} == set(other.free)

    response = client.get(
        f"/v1/contracts/{musterfunk.contract_id}/free-transactions", headers=other.headers
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


async def test_the_preview_shows_source_target_period_and_ladder() -> None:
    """Die woertliche Forderung aus 19.7 - und zwar fuer alle drei Buchungen
    zusammen, weil erst die Gesamtmenge die spaetere Treppe ergibt."""
    musterfunk = await _seed_musterfunk()
    response = client.post(
        f"/v1/contracts/{musterfunk.contract_id}/assignment-preview",
        json={"transaction_ids": musterfunk.free},
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["contract_name"] == PROVIDER
    assert len(body["transactions"]) == 3
    assert body["sources"] == [{"contract_id": None, "contract_name": None, "transaction_count": 3}]
    assert body["unknown_transaction_ids"] == []

    assert body["first_seen_date_before"] == APR.isoformat()
    assert body["first_seen_date_after"] == JAN.isoformat()
    assert body["last_seen_date_after"] == JUL.isoformat()

    assert body["stages_without_evidence_before"] == 2
    # Als Menge verglichen: beide Zeilen der Datenuebernahme beginnen am selben
    # Tag, ihre Reihenfolge ist damit auch in der Datenbank beliebig.
    assert set(_ladder(body["amount_stages_before"])) == {
        (4995, TODAY.isoformat(), TODAY.isoformat()),
        (3995, TODAY.isoformat(), None),
    }
    assert _ladder(body["amount_stages_after"]) == [
        (4995, JAN.isoformat(), (APR - timedelta(days=1)).isoformat()),
        (3995, APR.isoformat(), None),
    ]

    # Vorschau heisst Vorschau: nichts geschrieben.
    assert set(_ladder([item["stage"] for item in _evidence(musterfunk)["stages"]])) == set(
        _ladder(body["amount_stages_before"])
    )
    assert _free(musterfunk)["total"] == 3


async def test_the_preview_names_the_current_owner_and_unknown_ids() -> None:
    musterfunk = await _seed_musterfunk()
    stranger_id = str(uuid.uuid4())
    response = client.post(
        f"/v1/contracts/{musterfunk.contract_id}/assignment-preview",
        json={"transaction_ids": [musterfunk.assigned[0], stranger_id]},
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["unknown_transaction_ids"] == [stranger_id]
    assert body["sources"] == [
        {
            "contract_id": musterfunk.contract_id,
            "contract_name": PROVIDER,
            "transaction_count": 1,
        }
    ]
    # Eine Buchung, die bereits an diesem Vertrag haengt, aendert nichts.
    assert _ladder(body["amount_stages_after"]) == [(3995, APR.isoformat(), None)]


# ---------------------------------------------------------------------------
# Abnahme: Nachtragen und Neuberechnen
# ---------------------------------------------------------------------------


async def test_the_musterfunk_case_produces_the_correct_price_ladder() -> None:
    """Die Abnahme aus Abschnitt 19.1.

    `valid_to` der ersten Stufe ist der Vortag der zweiten und nicht der Tag der
    letzten 49,95-EUR-Buchung: der Verlauf bleibt lueckenlos, welche Buchungen
    eine Stufe tragen, beantwortet der Belegendpunkt.
    """
    musterfunk = await _seed_musterfunk()
    for transaction_id in musterfunk.free:
        _reassign(musterfunk, transaction_id)

    result = _recalculate(musterfunk)
    assert result["changed"] is True
    assert result["stages_written"] == 2
    assert result["stages_removed"] == 2
    assert result["manual_stages_kept"] == 0
    assert result["resulting_amount_minor"] == 3995
    assert result["contract_amount_minor"] == 3995
    assert result["first_seen_date"] == JAN.isoformat()
    assert result["last_seen_date"] == JUL.isoformat()

    assert _ladder(result["amount_stages"]) == [
        (4995, JAN.isoformat(), (APR - timedelta(days=1)).isoformat()),
        (3995, APR.isoformat(), None),
    ]
    history = client.get(
        f"/v1/contracts/{musterfunk.contract_id}/history", headers=musterfunk.headers
    ).json()
    assert _ladder(history) == _ladder(result["amount_stages"])
    assert [entry["change_reason"] for entry in history] == ["backfill", "backfill"]
    # Jede Stufe nennt jetzt ihre Quellbuchung - vorher war beides leer.
    assert history[0]["source_transaction_id"] == musterfunk.free[0]
    assert history[1]["source_transaction_id"] == musterfunk.assigned[0]
    assert history[1]["delta_minor"] == -1000


async def test_a_second_recalculation_changes_nothing() -> None:
    musterfunk = await _seed_musterfunk()
    for transaction_id in musterfunk.free:
        _reassign(musterfunk, transaction_id)
    first = _recalculate(musterfunk)

    second = _recalculate(musterfunk)
    assert second["changed"] is False
    assert second["stages_written"] == 0
    assert second["stages_removed"] == 0
    assert _ladder(second["amount_stages"]) == _ladder(first["amount_stages"])

    ids_first = [entry["id"] for entry in first["amount_stages"]]
    ids_second = [entry["id"] for entry in second["amount_stages"]]
    assert ids_first == ids_second


async def test_a_recalculation_without_bookings_keeps_the_history() -> None:
    """Kein Beleg heisst nicht "Verlauf loeschen": ein Vertrag ohne zugeordnete
    Buchungen behaelt, was er hat."""
    headers = household_headers()
    contract_id = _create_contract(headers, "Stadtwerke Hamburg", 12300, 15)

    response = client.post(
        f"/v1/contracts/{contract_id}/amount-history/recalculate", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is False
    assert body["resulting_amount_minor"] is None
    assert [entry["amount_minor"] for entry in body["amount_stages"]] == [12300]


async def test_a_manual_stage_survives_the_recalculation() -> None:
    """Der Nutzer hat den Preis ab Juni von Hand korrigiert.

    Danach traegt er eine weitere alte Buchung nach, die Treppe muss also
    tatsaechlich neu geschrieben werden. Die Neuberechnung ergaenzt die
    Vergangenheit unterhalb der manuellen Zeile und laesst diese unangetastet
    (Leitsatz 5).
    """
    musterfunk = await _seed_musterfunk()
    for transaction_id in musterfunk.free[1:]:
        _reassign(musterfunk, transaction_id)
    first = _recalculate(musterfunk)
    assert _ladder(first["amount_stages"])[0][1] == FEB.isoformat()

    manual = client.post(
        f"/v1/contracts/{musterfunk.contract_id}/history",
        json={"amount_minor": 4999, "valid_from": JUN.isoformat()},
        headers=musterfunk.headers,
    )
    assert manual.status_code == 201, manual.text

    _reassign(musterfunk, musterfunk.free[0])
    result = _recalculate(musterfunk)

    assert result["changed"] is True
    assert result["manual_stages_kept"] == 1
    assert _ladder(result["amount_stages"]) == [
        (4995, JAN.isoformat(), (APR - timedelta(days=1)).isoformat()),
        (3995, APR.isoformat(), (JUN - timedelta(days=1)).isoformat()),
        (4999, JUN.isoformat(), None),
    ]
    reasons = [entry["change_reason"] for entry in result["amount_stages"]]
    assert reasons == ["backfill", "backfill", "manual"]


async def test_a_foreign_household_cannot_recalculate() -> None:
    musterfunk = await _seed_musterfunk()
    stranger = household_headers()
    response = client.post(
        f"/v1/contracts/{musterfunk.contract_id}/amount-history/recalculate", headers=stranger
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# KI-Modus: freie Buchungen zu EINEM Vertrag
# ---------------------------------------------------------------------------


def _stub_model(
    monkeypatch: pytest.MonkeyPatch,
    answer: dict[str, Any] | Callable[[str], dict[str, Any]] | None,
    *,
    connected: bool = True,
) -> list[dict[str, Any]]:
    """Ersetzt nur Discovery und Modellserver (tests/ki_attrappe.py). Vorfilterung,
    Belegaufbereitung, Maskierung, Antwortpruefung und der Gateway des KI-Kerns
    laufen echt - dort sitzen die Zusicherungen. Zurueckgegeben werden die
    tatsaechlich gesendeten Anfragen."""
    server = ki_attrappe.install(
        monkeypatch,
        ki_attrappe.json_server(answer),
        ki_attrappe.target_result() if connected else ki_attrappe.offline(),
    )
    return server.bodies


def _check(musterfunk: Musterfunk, **body: Any) -> dict[str, Any]:
    response = client.post(
        f"/v1/contracts/{musterfunk.contract_id}/ai-transaction-check",
        json=body,
        headers=musterfunk.headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _answer_by_reference(prompt: str) -> dict[str, Any]:
    """Stellvertreter fuer das Modell: vergleicht die Referenzzeilen im Prompt.

    Faellt die Mandatsreferenz der Maskierung zum Opfer oder landet sie nicht im
    Prompt, sind Festnetz und Mobilfunk ununterscheidbar und dieser Test
    schlaegt fehl - er prueft also die Prompt-Aufbereitung mit.
    """
    references = [
        line.split(":", 1)[1].strip()
        for line in prompt.splitlines()
        if "Referenz im Verwendungszweck:" in line
    ]
    same = len(references) == 2 and references[0] == references[1]
    return {
        "gehoert_zum_vertrag": same,
        "begruendung": (
            "Dieselbe Mandatsreferenz wie die zugeordneten Buchungen, nur ein anderer Preis."
            if same
            else "Der Verwendungszweck nennt eine andere Mandatsreferenz."
        ),
    }


async def test_the_model_sorts_the_missing_bookings_from_the_other_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Referenzfall mit Mobilfunkbuchungen daneben: die drei
    49,95-EUR-Buchungen gehoeren zum Vertrag, die 99,90-EUR-Buchungen nicht."""
    musterfunk = await _seed_musterfunk(with_mobilfunk=True)
    asked = _stub_model(monkeypatch, _answer_by_reference)
    body = _check(musterfunk)

    assert body["kandidaten_gesamt"] == 10
    assert body["truncated"] is False
    assert len(asked) == 10
    assert body["geprueft"] == 10
    assert body["uebersprungen"] == 0

    verdicts = {item["transaction_id"]: item["gehoert_zum_vertrag"] for item in body["items"]}
    assert all(verdicts[transaction_id] is True for transaction_id in musterfunk.free)
    assert sum(1 for value in verdicts.values() if value is False) == 7
    assert all(item["quelle"] == "ai" for item in body["items"])
    assert all("confidence" not in item for item in body["items"])
    assert body["vorauswahl"]["search"] == "Musterfunk"

    # Folgenlos: nichts zugeordnet, nichts am Verlauf geaendert.
    assert _free(musterfunk)["total"] == 10
    assert _evidence(musterfunk)["stages_without_evidence"] == 2


async def test_an_unusable_model_answer_is_not_a_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eine erfundene Prozentzahl macht die Antwort unbrauchbar - und
    unbrauchbar heisst "nicht beurteilt", nicht "gehoert dazu"."""
    musterfunk = await _seed_musterfunk()
    asked = _stub_model(
        monkeypatch,
        {"gehoert_zum_vertrag": True, "begruendung": "Zu 95 % dieselbe Referenz."},
    )
    body = _check(musterfunk)

    assert len(asked) == 3
    assert body["items"] == []
    assert body["geprueft"] == 0
    assert body["uebersprungen"] == 3
    assert {entry["grund"] for entry in body["uebersprungen_gruende"]} == {"no_model_answer"}


async def test_without_a_model_the_answer_stays_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kein 500er: ein optionaler Zusatz, der gerade nicht laeuft, ist ein
    Hinweis."""
    musterfunk = await _seed_musterfunk()
    asked = _stub_model(monkeypatch, None, connected=False)
    body = _check(musterfunk)

    assert asked == []
    assert body["items"] == []
    assert body["modell_name"] is None
    assert body["meldung"].startswith(crosscheck.TRANSACTION_SUBJECT)
    assert "LM Studio" in body["meldung"]
    assert crosscheck.TRANSACTION_OUTCOME in body["meldung"]
    # Die deterministische Vorauswahl steht trotzdem in der Antwort.
    assert body["kandidaten_gesamt"] == 3


async def test_switched_off_ai_says_so_instead_of_searching_for_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    musterfunk = await _seed_musterfunk()
    settings = client.put(
        "/v1/ai/settings", json={"is_enabled": False}, headers=musterfunk.headers
    )
    assert settings.status_code == 200, settings.text

    asked = _stub_model(monkeypatch, None)
    body = _check(musterfunk)

    assert asked == []
    assert body["meldung"] == crosscheck.TRANSACTION_AI_DISABLED_MESSAGE


async def test_nothing_free_means_no_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne Kandidaten wird das Modell nicht einmal gesucht - eine
    Erreichbarkeitsmeldung waere hier irrefuehrend."""
    musterfunk = await _seed_musterfunk()
    for transaction_id in musterfunk.free:
        _reassign(musterfunk, transaction_id)

    asked = _stub_model(monkeypatch, None, connected=False)
    body = _check(musterfunk)

    assert asked == []
    assert body["meldung"] is None
    assert body["kandidaten_gesamt"] == 0
    assert body["uebersprungen"] == 0


async def test_a_named_booking_outside_the_preselection_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`transaction_ids` schraenkt die Vorauswahl ein, umgeht sie aber nicht:
    eine bereits zugeordnete Buchung wird nicht zur Diskussion gestellt."""
    musterfunk = await _seed_musterfunk()
    asked = _stub_model(monkeypatch, _answer_by_reference)
    body = _check(musterfunk, transaction_ids=[musterfunk.free[0], musterfunk.assigned[0]])

    assert len(asked) == 1
    assert [item["transaction_id"] for item in body["items"]] == [musterfunk.free[0]]
    assert body["uebersprungen_gruende"] == [
        {
            "transaction_id": musterfunk.assigned[0],
            "booked_date": None,
            "amount_minor": None,
            "grund": "not_free",
        }
    ]


async def test_the_model_never_sees_a_foreign_household(monkeypatch: pytest.MonkeyPatch) -> None:
    musterfunk = await _seed_musterfunk()
    stranger = household_headers()
    _stub_model(monkeypatch, _answer_by_reference)

    response = client.post(
        f"/v1/contracts/{musterfunk.contract_id}/ai-transaction-check", json={}, headers=stranger
    )
    assert response.status_code == 404
