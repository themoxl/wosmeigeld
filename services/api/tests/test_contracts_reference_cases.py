"""Referenzfaelle der Vertragserkennung (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, 14).

Die dreizehn Faelle sind die Abnahmekriterien der Erkennung v2.
Jeder Fall bekommt einen eigenen Haushalt:
`get_contract_candidates` liefert je Aufruf hoechstens zehn Vorschlaege, und
ein Fall darf einem anderen nicht die Liste fuellen.

Buchungen entstehen per direktem SQL statt ueber `POST /v1/transactions`: nur
so lassen sich `purpose_raw`, `bank_transaction_code` und die Herkunft des
Imports setzen. Genau daran haengt die Erkennung - der Vertragsschluessel kommt
aus dem Verwendungszweck, der Zahlungsart-Index aus dem Geschaeftsvorfallcode
und dessen Deutung aus `import_runs.detected_format`.

Alle Datumsangaben sind relativ zu heute gerechnet: die Erkennung liest nur
die letzten 24 Monate, feste Kalenderdaten wuerden mit der Zeit aus dem Fenster
laufen.
"""

import calendar
import uuid
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import contract_matching_service
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

Headers = dict[str, str]

TODAY = date.today()
# Letzter abgeschlossener Monat. Alle Zahltage werden von hier aus rueckwaerts
# gerechnet, damit keine Buchung in der Zukunft liegt.
_PREV_MONTH_END = TODAY.replace(day=1) - timedelta(days=1)

# MT940-Geschaeftsvorfallcode einer Lastschrift bzw. eines Kartenumsatzes
# (services/worker/worker/imports/parsers/mt940.py).
MT940_DIRECT_DEBIT = "N105"
MT940_CARD = "N083"


# ---------------------------------------------------------------------------
# Fixtures und Helfer
# ---------------------------------------------------------------------------


@pytest.fixture()
def household_headers() -> Headers:
    token = _signup(f"cref-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Vertrags-Referenzhaushalt"}, headers=_auth_headers(token)
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


def _payday(day: int, months_ago: int) -> date:
    """Zahltag `day`, gerechnet vom letzten abgeschlossenen Monat rueckwaerts.

    `day` muss <= 28 sein, sonst waere der Zahltag nicht in jedem Monat
    vorhanden und die Reihe haette einen Sprung, den kein Vertrag hat.
    """
    assert day <= 28
    total = _PREV_MONTH_END.month - 1 - months_ago
    year = _PREV_MONTH_END.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


async def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    """Fuehrt eine Anweisung MIT Ergebniszeilen aus und committet.

    Committet auch beim reinen `select`: die Helfer dieser Datei legen
    ueberwiegend Daten an, und ein vergessenes Commit faellt nicht als Fehler
    auf - der naechste HTTP-Aufruf saehe schlicht keine Buchungen und der Test
    scheiterte an einer leeren Vorschlagsliste.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(text(sql), params)
        mapped = [dict(row._mapping) for row in result]
        await session.commit()
        return mapped


async def _execute(sql: str, **params: Any) -> None:
    """Gegenstueck fuer Anweisungen ohne `returning`."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def _category_id(household_id: str, semantic_key: str) -> str:
    """Kategorie des Standardpakets ueber ihren `semantic_key`.

    Bewusst nicht ueber den Anzeigenamen: die Kategorieaffinitaet aus
    Abschnitt 6.4 haengt genau an diesem Schluessel.
    """
    rows = await _rows(
        "select id from categories where household_id = :household_id "
        "and semantic_key = :semantic_key",
        household_id=household_id,
        semantic_key=semantic_key,
    )
    assert rows, f"Kategorie '{semantic_key}' fehlt im Standardpaket"
    return str(rows[0]["id"])


async def _import_run(household_id: str, account_id: str, detected_format: str) -> str:
    """Ein Importlauf als Traeger des Formats.

    `bank_transaction_code` bedeutet je Quelle etwas anderes; ohne das Format
    kann die Erkennung 'N105' nicht als Lastschrift lesen.
    """
    rows = await _rows(
        "insert into import_runs (household_id, account_id, filename, file_hash, "
        "file_size_bytes, detected_format, status) values (:household_id, :account_id, "
        "'referenz.sta', :file_hash, 1024, :detected_format, 'completed') returning id",
        household_id=household_id,
        account_id=account_id,
        file_hash=uuid.uuid4().hex,
        detected_format=detected_format,
    )
    return str(rows[0]["id"])


async def _payee_id(household_id: str, name: str) -> str:
    rows = await _rows(
        "insert into payees (household_id, name) values (:household_id, :name) returning id",
        household_id=household_id,
        name=name,
    )
    return str(rows[0]["id"])


async def _seed(
    household_id: str,
    account_id: str,
    booked_date: date,
    amount_minor: int,
    *,
    payee_label: str,
    purpose_raw: str | None = None,
    bank_transaction_code: str | None = None,
    import_run_id: str | None = None,
    category_id: str | None = None,
) -> str:
    """Eine Buchung samt Split, direkt in der Datenbank.

    `household_id` steht ausdruecklich in jedem Insert: die Testsitzung laeuft
    als postgres ohne RLS, dort liefert der Spalten-Default
    current_household_id() NULL.
    """
    rows = await _rows(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "payee_label, purpose_raw, bank_transaction_code, import_run_id) values "
        "(:household_id, :account_id, :booked_date, :amount_minor, :payee_label, "
        ":purpose_raw, :bank_transaction_code, cast(:import_run_id as uuid)) returning id",
        household_id=household_id,
        account_id=account_id,
        booked_date=booked_date,
        amount_minor=amount_minor,
        payee_label=payee_label,
        purpose_raw=purpose_raw,
        bank_transaction_code=bank_transaction_code,
        import_run_id=import_run_id,
    )
    transaction_id = str(rows[0]["id"])
    if category_id is not None:
        await _execute(
            "insert into transaction_splits (household_id, transaction_id, category_id, "
            "amount_minor, category_source) values (:household_id, :transaction_id, "
            "cast(:category_id as uuid), :amount_minor, 'manual')",
            household_id=household_id,
            transaction_id=transaction_id,
            category_id=category_id,
            amount_minor=amount_minor,
        )
    return transaction_id


def _suggestions(headers: Headers) -> list[dict[str, Any]]:
    response = client.get("/v1/review", headers=headers)
    assert response.status_code == 200, response.text
    return list(response.json()["contract_suggestions"])


def _accept(headers: Headers, suggestion: dict[str, Any], category_id: str, **extra: Any) -> None:
    body: dict[str, Any] = {
        "type": "contract_suggestion",
        "source_transaction_id": suggestion["source_transaction_id"],
        "category_id": category_id,
    }
    body.update(extra)
    response = client.post("/v1/review/accept", json=body, headers=headers)
    assert response.status_code == 200, response.text


async def _history(contract_id: str) -> list[dict[str, Any]]:
    return await _rows(
        "select amount_minor, valid_from, valid_to, delta_minor, change_reason "
        "from contract_amount_history where contract_id = cast(:id as uuid) "
        "order by valid_from asc, created_at asc",
        id=contract_id,
    )


async def _reconcile(household_id: str) -> contract_matching_service.ReconcileResult:
    """Der Lauf so, wie ihn der Worker faehrt: eigene Sitzung, ein Commit."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await contract_matching_service.reconcile_household(
            session, UUID(household_id), today=TODAY
        )
        await session.commit()
    return result


# ---------------------------------------------------------------------------
# 1. Musterfunk parallel - zwei Vertraege
# ---------------------------------------------------------------------------


async def test_musterfunk_parallel_contracts_stay_two_suggestions(
    household_headers: Headers, account_id: str
) -> None:
    """Festnetz und Mobilfunk laufen gleichzeitig beim selben Anbieter.

    Sie unterscheiden sich nur in der Vertragsnummer im Verwendungszweck - und
    genau daran muessen sie getrennt werden, bevor ueber Betraege geredet wird.
    """
    household_id = household_headers["X-Household-Id"]
    for months_ago in range(6):
        await _seed(
            household_id,
            account_id,
            _payday(3, months_ago),
            -3_995,
            payee_label="Musterfunk Deutschland GmbH",
            purpose_raw="MUSTERFUNK VERTRAGSNR 4711234 FESTNETZ",
        )
        await _seed(
            household_id,
            account_id,
            _payday(17, months_ago),
            -9_990,
            payee_label="Musterfunk Deutschland GmbH",
            purpose_raw="MUSTERFUNK VERTRAGSNR 8899001 MOBILFUNK",
        )

    musterfunk = [
        item
        for item in _suggestions(household_headers)
        if item["payee_label"].startswith("Musterfunk")
    ]
    assert len(musterfunk) == 2
    assert {item["amount_minor"] for item in musterfunk} == {3_995, 9_990}
    assert {item["contract_key"] for item in musterfunk} == {"4711234", "8899001"}
    assert {item["contract_key_source"] for item in musterfunk} == {"mandate"}
    assert {item["frequency"] for item in musterfunk} == {"monthly"}
    # Parallele Zeitraeume sind keine Preistreppe: jeder Vorschlag hat genau
    # eine Stufe.
    assert [len(item["amount_steps"]) for item in musterfunk] == [1, 1]


# ---------------------------------------------------------------------------
# 2. Musterfunk Preisaenderung - ein Vertrag mit zwei Stufen
# ---------------------------------------------------------------------------


async def test_musterfunk_price_change_becomes_one_contract_with_two_stages(
    household_headers: Headers, account_id: str
) -> None:
    """Dieselbe Vertragsnummer, alter Preis endet vor dem neuen.

    Frueher entstanden daraus zwei Eintraege und die Summe zaehlte den Vertrag
    doppelt.
    """
    household_id = household_headers["X-Household-Id"]
    for months_ago in range(8, 3, -1):
        await _seed(
            household_id,
            account_id,
            _payday(3, months_ago),
            -4_995,
            payee_label="Musterfunk Deutschland GmbH",
            purpose_raw="MUSTERFUNK VERTRAGSNR 4711234",
        )
    for months_ago in range(3, -1, -1):
        await _seed(
            household_id,
            account_id,
            _payday(3, months_ago),
            -3_995,
            payee_label="Musterfunk Deutschland GmbH",
            purpose_raw="MUSTERFUNK VERTRAGSNR 4711234",
        )

    [suggestion] = _suggestions(household_headers)
    assert suggestion["amount_minor"] == 3_995
    assert suggestion["occurrences_count"] == 9
    assert [step["amount_minor"] for step in suggestion["amount_steps"]] == [4_995, 3_995]
    assert suggestion["amount_steps"][0]["last_date"] == _payday(3, 4).isoformat()
    assert suggestion["amount_steps"][1]["first_date"] == _payday(3, 3).isoformat()

    telecom = await _category_id(household_id, "telecom")
    _accept(household_headers, suggestion, telecom)

    contracts = client.get("/v1/contracts", headers=household_headers).json()
    [contract] = contracts
    assert contract["amount_minor"] == 3_995
    assert contract["direction"] == "expense"
    assert contract["frequency"] == "monthly"
    assert contract["contract_key"] == "4711234"
    assert contract["source"] == "confirmed"

    history = await _history(contract["id"])
    # Genau zwei Zeilen: der Betragsverlauf beginnt nicht mit dem heutigen
    # Preis, und dieselbe Stufe steht nicht zweimal da.
    assert [entry["amount_minor"] for entry in history] == [4_995, 3_995]
    assert [entry["change_reason"] for entry in history] == ["backfill", "initial"]
    # Genau eine offene Stufe; die aeltere ist am Tag vor dem Wechsel geschlossen.
    assert [entry["valid_to"] is None for entry in history] == [False, True]
    assert history[0]["valid_to"] == _payday(3, 3) - timedelta(days=1)
    assert history[1]["delta_minor"] == -1_000

    # Der bestaetigte Vertrag kommt nicht als Vorschlag zurueck.
    assert _suggestions(household_headers) == []

    ended = client.post(
        f"/v1/contracts/{contract['id']}/status",
        json={"status": "ended"},
        headers=household_headers,
    )
    assert ended.status_code == 200, ended.text
    # Beenden ist weiterhin eine Entscheidung ueber genau diesen Vertrag. Die
    # Erkennung darf aus denselben Altbuchungen keinen neuen Vorschlag bauen;
    # Reaktivieren geschieht gezielt in der Vertragsverwaltung.
    assert _suggestions(household_headers) == []


# ---------------------------------------------------------------------------
# 3. MaxEnergy - Preisstufe mit delta_minor = -2400
# ---------------------------------------------------------------------------


async def test_maxenergy_price_drop_is_one_contract_with_a_stage_delta(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    for months_ago in range(8, 2, -1):
        await _seed(
            household_id,
            account_id,
            _payday(15, months_ago),
            -14_700,
            payee_label="MaxEnergy GmbH",
            purpose_raw="MAXENERGY ABSCHLAG KUNDENNR 5566778",
        )
    for months_ago in range(2, -1, -1):
        await _seed(
            household_id,
            account_id,
            _payday(15, months_ago),
            -12_300,
            payee_label="MaxEnergy GmbH",
            purpose_raw="MAXENERGY ABSCHLAG KUNDENNR 5566778",
        )

    [suggestion] = _suggestions(household_headers)
    assert suggestion["amount_minor"] == 12_300
    assert [step["amount_minor"] for step in suggestion["amount_steps"]] == [14_700, 12_300]
    assert [step["occurrences_count"] for step in suggestion["amount_steps"]] == [6, 3]

    energy = await _category_id(household_id, "energy")
    _accept(household_headers, suggestion, energy)

    [contract] = client.get("/v1/contracts", headers=household_headers).json()
    history = await _history(contract["id"])
    assert [entry["amount_minor"] for entry in history] == [14_700, 12_300]
    assert [entry["change_reason"] for entry in history] == ["backfill", "initial"]
    # Die eigentliche Zusicherung des Referenzfalls.
    assert history[1]["delta_minor"] == -2_400
    assert history[1]["valid_from"] == _payday(15, 2)


# ---------------------------------------------------------------------------
# 4. Miete - monatlich trotz eines vorgezogenen Zahltags
# ---------------------------------------------------------------------------


async def test_the_rent_is_monthly_and_not_weekly(
    household_headers: Headers, account_id: str
) -> None:
    """Der Regressionstest fuer den Scoring-Fehler aus Abschnitt 6.3.

    Ein einziger vorgezogener Zahltag hat "monatlich" unter die alte Schwelle
    von 75 % gedrueckt, waehrend "woechentlich mit ausgelassenen Wochen" bei
    100 % blieb - deshalb stand die Miete als "Woechentlich" in der Liste.
    """
    household_id = household_headers["X-Household-Id"]
    rent_category = await _category_id(household_id, "rent")
    run_id = await _import_run(household_id, account_id, "mt940")

    for months_ago in range(6, -1, -1):
        booked = _payday(2, months_ago)
        if months_ago == 3:
            booked -= timedelta(days=6)
        await _seed(
            household_id,
            account_id,
            booked,
            -115_000,
            payee_label="Hausverwaltung Nord GmbH",
            purpose_raw="MIETE WOHNUNG 12 MANDATSREF: HV-2211",
            bank_transaction_code=MT940_DIRECT_DEBIT,
            import_run_id=run_id,
            category_id=rent_category,
        )

    [suggestion] = _suggestions(household_headers)
    assert suggestion["frequency"] == "monthly"
    assert suggestion["amount_minor"] == 115_000
    assert suggestion["occurrences_count"] == 7
    assert suggestion["suggested_category_id"] == rent_category
    # Miete, Lastschrift, stabiler Betrag: der Vorschlag ist nicht "unsicher".
    assert suggestion["confidence"] == "suggested"
    assert suggestion["is_uncertain"] is False
    assert suggestion["score_breakdown"]["category_affinity"] == 1.0
    assert suggestion["score_breakdown"]["payment_method"] == 1.0
    assert suggestion["score_breakdown"]["amount_stability"] == 1.0


# ---------------------------------------------------------------------------
# 5. Mustermakler - kein Vorschlag
# ---------------------------------------------------------------------------


async def test_two_doctor_payments_a_quarter_apart_are_not_a_contract(
    household_headers: Headers, account_id: str
) -> None:
    """Zwei Arztrechnungen im Quartalsabstand sehen aus wie ein Vertrag.

    Ausgeschlossen wird der Fall doppelt: ein Quartalspaar aus genau zwei
    Buchungen braucht einen Vertragsschluessel oder eine ausgewiesene
    Lastschrift, und der Vertrags-Score bleibt unter 0.40 - Kartenzahlung,
    schwankender Betrag und die Affinitaet 0.0 der Kategorie "Arzt & Zahnarzt"
    zusammen reichen nicht einmal fuer "unsicher"
    (siehe test_contract_detection.py).
    """
    household_id = household_headers["X-Household-Id"]
    doctor = await _category_id(household_id, "doctor")
    run_id = await _import_run(household_id, account_id, "mt940")
    first = TODAY - timedelta(days=127)
    for booked, amount in ((first, -8_500), (first + timedelta(days=92), -12_400)):
        await _seed(
            household_id,
            account_id,
            booked,
            amount,
            payee_label="Mustermakler & Kollegen",
            purpose_raw="RECHNUNG BEHANDLUNG",
            bank_transaction_code=MT940_CARD,
            import_run_id=run_id,
            category_id=doctor,
        )

    assert _suggestions(household_headers) == []


# ---------------------------------------------------------------------------
# 6. Netflix beendet - Frage in der Review-Inbox
# ---------------------------------------------------------------------------


async def test_netflix_stops_paying_and_becomes_a_question_in_the_inbox(
    household_headers: Headers, account_id: str
) -> None:
    """Der Abgleich kann nur zaehlen, dass nichts kam.

    Ob gekuendigt, gestundet oder nur verschoben, weiss allein der Nutzer -
    deshalb eine Frage statt einer stillen Statusaenderung.
    """
    household_id = household_headers["X-Household-Id"]
    last_payment = _payday(9, 4)
    contract = client.post(
        "/v1/contracts",
        json={
            "name": "Netflix",
            "amount_minor": 1_799,
            "frequency": "monthly",
            "next_due_date": last_payment.isoformat(),
        },
        headers=household_headers,
    ).json()
    await _seed(
        household_id, account_id, last_payment, -1_799, payee_label="Netflix"
    )

    result = await _reconcile(household_id)
    assert result.matched_count == 1
    assert result.missing_count >= 2

    row = await _rows(
        "select status, missed_periods, last_seen_date from contracts where id = cast(:id as uuid)",
        id=contract["id"],
    )
    assert row[0]["status"] == "active"
    assert row[0]["missed_periods"] >= 2
    assert row[0]["last_seen_date"] == last_payment

    inbox = client.get("/v1/review", headers=household_headers).json()
    checks = {item["contract_id"]: item for item in inbox["contract_ended_checks"]}
    assert contract["id"] in checks
    assert checks[contract["id"]]["type"] == "contract_ended_check"
    assert checks[contract["id"]]["amount_minor"] == 1_799
    assert checks[contract["id"]]["direction"] == "expense"


# ---------------------------------------------------------------------------
# 7. Klarna ohne Vertragsschluessel - kein Vorschlag
# ---------------------------------------------------------------------------


async def test_a_collector_account_without_a_key_produces_no_suggestion(
    household_headers: Headers, account_id: str
) -> None:
    """Klarna ist ein Zahlungsdienstleister, kein Vertragspartner.

    Ohne Vertragsschluessel sagt der Empfaenger nichts darueber aus, wessen
    Vertrag hier bezahlt wurde.
    """
    household_id = household_headers["X-Household-Id"]
    for months_ago, amount in ((2, -2_199), (1, -8_750), (0, -1_399)):
        await _seed(
            household_id,
            account_id,
            _payday(8, months_ago),
            amount,
            payee_label="Klarna Bank AB",
            purpose_raw="KLARNA EINKAUF",
        )

    assert _suggestions(household_headers) == []


# ---------------------------------------------------------------------------
# 8. Klarna Ratenkauf
# ---------------------------------------------------------------------------


async def test_a_klarna_installment_plan_is_recognised_with_its_total(
    household_headers: Headers, account_id: str
) -> None:
    """Ein Ratenkauf beendet sich von selbst - dafuer braucht er die Gesamtzahl.

    Der Verwendungszweck traegt zugleich die Mandatsreferenz; ohne sie waere
    Klarna als Sammelkonto ueberhaupt nicht vorschlagsfaehig.
    """
    household_id = household_headers["X-Household-Id"]
    for step, months_ago in enumerate((2, 1, 0), start=1):
        await _seed(
            household_id,
            account_id,
            _payday(8, months_ago),
            -4_999,
            payee_label="Klarna Bank AB",
            purpose_raw=f"KLARNA MREF+KL-9988776 RATE {step}/12 SOFA",
        )

    [suggestion] = _suggestions(household_headers)
    assert suggestion["contract_type"] == "installment"
    assert suggestion["installments_total"] == 12
    assert suggestion["installments_paid"] == 3
    assert suggestion["contract_key"] == "KL-9988776"
    # Bei einem Sammelkonto benennt erst der Schluessel den Vertrag.
    assert suggestion["display_name"] == "Klarna Bank AB · KL-9988776"

    installments = await _category_id(household_id, "installments")
    _accept(household_headers, suggestion, installments)

    [contract] = client.get("/v1/contracts", headers=household_headers).json()
    assert contract["contract_type"] == "installment"
    assert contract["installments_total"] == 12
    assert contract["installments_paid"] == 3


# ---------------------------------------------------------------------------
# 9. Musterkasse jaehrlich
# ---------------------------------------------------------------------------


async def test_a_yearly_premium_keeps_its_rhythm(
    household_headers: Headers, account_id: str
) -> None:
    """Eine Zahlung pro Jahr am selben Tag.

    Ohne den Vertragsschluessel bliebe zweimal derselbe Betrag im Jahresabstand
    zu wenig; mit ihm ist es ein Vertrag.
    """
    household_id = household_headers["X-Household-Id"]
    run_id = await _import_run(household_id, account_id, "mt940")
    risk = await _category_id(household_id, "risk_insurance")
    for months_ago in (12, 0):
        await _seed(
            household_id,
            account_id,
            _payday(11, months_ago),
            -28_800,
            payee_label="Musterkasse Versicherungen",
            purpose_raw="DEBEKA VERSICHERUNGSSCHEIN: VS-0815-4711 JAHRESBEITRAG",
            bank_transaction_code=MT940_DIRECT_DEBIT,
            import_run_id=run_id,
            category_id=risk,
        )

    [suggestion] = _suggestions(household_headers)
    assert suggestion["frequency"] == "yearly"
    assert suggestion["amount_minor"] == 28_800
    assert suggestion["contract_key"] == "VS-0815-4711"
    assert suggestion["occurrences_count"] == 2

    _accept(household_headers, suggestion, risk)
    [contract] = client.get("/v1/contracts", headers=household_headers).json()
    assert contract["frequency"] == "yearly"
    # Der Jahresbeitrag geht mit einem Zwoelftel in die Monatssumme ein.
    summary = client.get("/v1/contracts/summary", headers=household_headers).json()
    assert summary["monthly_expense_minor"] == 2_400


# ---------------------------------------------------------------------------
# 10. Halbjaehrlich
# ---------------------------------------------------------------------------


async def test_a_semiannual_contract_is_a_rhythm_of_its_own(
    household_headers: Headers, account_id: str
) -> None:
    """Fehlte in der Altfassung ganz - halbjaehrlich wurde als quartalsweise
    mit ausgelassener Periode gelesen und der Sollbetrag verdoppelt."""
    household_id = household_headers["X-Household-Id"]
    for months_ago in (6, 0):
        await _seed(
            household_id,
            account_id,
            _payday(20, months_ago),
            -19_800,
            payee_label="Allianz Sachversicherung",
            purpose_raw="ALLIANZ VERTRAGSNR 7788990 HALBJAHRESBEITRAG",
        )

    [suggestion] = _suggestions(household_headers)
    assert suggestion["frequency"] == "semiannual"
    assert suggestion["contract_key"] == "7788990"

    # Der Altpfad kann `semiannual` nicht darstellen und liefert 404 statt
    # einer Antwort, die den Response-Validator zerlegt.
    legacy = client.get(
        f"/v1/review/recurring-suggestions/{suggestion['source_transaction_id']}",
        headers=household_headers,
    )
    assert legacy.status_code == 404
    detail = client.get(
        f"/v1/review/contract-suggestions/{suggestion['source_transaction_id']}",
        headers=household_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["suggestion"]["frequency"] == "semiannual"


# ---------------------------------------------------------------------------
# 11. Manuell + erkannt - die Duplikatpruefung findet den Bestand
# ---------------------------------------------------------------------------


async def test_the_duplicate_check_finds_the_already_detected_contract(
    household_headers: Headers, account_id: str
) -> None:
    """Wer denselben Vertrag von Hand nachtraegt, soll den Treffer sehen,
    bevor er ihn ein zweites Mal in der Summe hat."""
    household_id = household_headers["X-Household-Id"]
    for months_ago in range(5, -1, -1):
        await _seed(
            household_id,
            account_id,
            _payday(15, months_ago),
            -12_300,
            payee_label="MaxEnergy GmbH",
            purpose_raw="MAXENERGY ABSCHLAG KUNDENNR 5566778",
        )
    [suggestion] = _suggestions(household_headers)
    energy = await _category_id(household_id, "energy")
    _accept(household_headers, suggestion, energy)
    [existing] = client.get("/v1/contracts", headers=household_headers).json()

    by_amount = client.post(
        "/v1/contracts/duplicate-check",
        json={"name": "MaxEnergy Strom", "amount_minor": 12_300, "frequency": "monthly"},
        headers=household_headers,
    )
    assert by_amount.status_code == 200, by_amount.text
    assert existing["id"] in {item["id"] for item in by_amount.json()}

    by_payee = client.post(
        "/v1/contracts/duplicate-check",
        json={"name": "Voellig anderer Name", "payee_id": existing["payee_id"]},
        headers=household_headers,
    ).json()
    [hit] = by_payee
    assert hit["id"] == existing["id"]
    assert hit["reason"] == "same_payee"


# ---------------------------------------------------------------------------
# 12. Vorzeichen
# ---------------------------------------------------------------------------


async def test_an_expense_contract_is_stored_positive_with_a_direction(
    household_headers: Headers, account_id: str
) -> None:
    """Vertragskonvention: `amount_minor` immer positiv, die Richtung steht in
    `direction`. Der Vorschlag traegt bewusst einen anderen Feldnamen als das
    negative `expected_amount_minor` des Altmodells."""
    household_id = household_headers["X-Household-Id"]
    for months_ago in range(4, -1, -1):
        await _seed(
            household_id,
            account_id,
            _payday(5, months_ago),
            -8_990,
            payee_label="Stadtwerke Sued",
            purpose_raw="STADTWERKE MANDATSREF: SW-4711",
        )
    for months_ago in range(4, -1, -1):
        await _seed(
            household_id,
            account_id,
            _payday(28, months_ago),
            350_000,
            payee_label="Musterfirma AG",
            purpose_raw="LOHN GEHALT MANDATSREF: LG-8899",
        )

    by_payee = {item["payee_label"]: item for item in _suggestions(household_headers)}
    expense = by_payee["Stadtwerke Sued"]
    assert expense["direction"] == "expense"
    assert expense["amount_minor"] == 8_990
    income = by_payee["Musterfirma AG"]
    assert income["direction"] == "income"
    assert income["amount_minor"] == 350_000
    assert income["contract_type"] == "income"

    energy = await _category_id(household_id, "energy")
    _accept(household_headers, expense, energy)
    [contract] = client.get("/v1/contracts", headers=household_headers).json()
    assert contract["direction"] == "expense"
    assert contract["amount_minor"] > 0

    summary = client.get("/v1/contracts/summary", headers=household_headers).json()
    assert summary["monthly_expense_minor"] == 8_990


# ---------------------------------------------------------------------------
# 13. Duplikatvorschlag fuer den Altbestand
# ---------------------------------------------------------------------------


def _create_contract(headers: Headers, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Vertrag",
        "amount_minor": 1_000,
        "frequency": "monthly",
        "next_due_date": TODAY.isoformat(),
    }
    body.update(overrides)
    response = client.post("/v1/contracts", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _set_seen_range(contract_id: str, first_seen: date, last_seen: date) -> None:
    """Der Altbestand traegt seine Zeitraeume aus der Uebernahme.

    Die Duplikatsuche entscheidet allein an ihnen, ob zwei Eintraege
    nacheinander oder gleichzeitig laufen.
    """
    await _execute(
        "update contracts set first_seen_date = :first_seen, last_seen_date = :last_seen "
        "where id = cast(:id as uuid)",
        id=contract_id,
        first_seen=first_seen,
        last_seen=last_seen,
    )


async def test_two_legacy_entries_of_one_provider_become_a_duplicate_question(
    household_headers: Headers,
) -> None:
    """Bedingung ist die zeitliche TRENNUNG: der aeltere endet, bevor der
    juengere beginnt.

    Beide Vertraege entstehen am selben Tag - genau so sieht der Altbestand
    aus: die Uebernahme aus `recurring_rules` legt alle Vertraege in einem Lauf
    an und setzt die erste Preisstufe auf `contracts.created_at`
    (0001_schema.sql). Das Zusammenfuehren muss damit umgehen koennen.
    """
    older = _create_contract(household_headers, name="Musterfunk Festnetz", amount_minor=4_995)
    newer = _create_contract(household_headers, name="Musterfunk Festnetz", amount_minor=3_995)
    await _set_seen_range(older["id"], _payday(3, 9), _payday(3, 5))
    await _set_seen_range(newer["id"], _payday(3, 4), _payday(3, 0))

    inbox = client.get("/v1/review", headers=household_headers).json()
    [duplicate] = inbox["contract_duplicate_suggestions"]
    assert duplicate["type"] == "contract_duplicate_suggestion"
    assert duplicate["target_id"] == newer["id"]
    assert duplicate["source_ids"] == [older["id"]]
    assert duplicate["amount_delta_minor"] == -1_000
    assert duplicate["gap_days"] == (_payday(3, 4) - _payday(3, 5)).days

    merged = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_duplicate_suggestion",
            "target_id": newer["id"],
            "source_ids": [older["id"]],
        },
        headers=household_headers,
    )
    assert merged.status_code == 200, merged.text
    remaining = client.get("/v1/contracts", headers=household_headers).json()
    assert [item["id"] for item in remaining] == [newer["id"]]
    assert client.get("/v1/review", headers=household_headers).json()[
        "contract_duplicate_suggestions"
    ] == []
    # Die Treppe bleibt lesbar, obwohl beide Stufen am selben Tag beginnen.
    history = await _history(newer["id"])
    assert [entry["amount_minor"] for entry in history] == [4_995, 3_995]
    assert history[1]["delta_minor"] == -1_000


async def test_overlapping_periods_are_two_real_contracts_and_no_question(
    household_headers: Headers,
) -> None:
    """Musterfunk Festnetz und Mobilfunk laufen gleichzeitig - ein
    Zusammenfuehren waere dort Datenverlust."""
    festnetz = _create_contract(household_headers, name="Musterfunk Festnetz", amount_minor=3_995)
    mobil = _create_contract(household_headers, name="Musterfunk Festnetz", amount_minor=9_990)
    await _set_seen_range(festnetz["id"], _payday(3, 9), _payday(3, 0))
    await _set_seen_range(mobil["id"], _payday(17, 9), _payday(17, 0))

    inbox = client.get("/v1/review", headers=household_headers).json()
    assert inbox["contract_duplicate_suggestions"] == []


async def test_a_dismissed_duplicate_question_does_not_come_back(
    household_headers: Headers,
) -> None:
    """Ohne gemerkte Verneinung stuende dasselbe Paar nach jedem Inbox-Aufruf
    erneut da."""
    older = _create_contract(household_headers, name="Fitness Nord", amount_minor=3_990)
    newer = _create_contract(household_headers, name="Fitness Nord", amount_minor=4_490)
    await _set_seen_range(older["id"], _payday(6, 9), _payday(6, 5))
    await _set_seen_range(newer["id"], _payday(6, 4), _payday(6, 0))

    assert len(
        client.get("/v1/review", headers=household_headers).json()[
            "contract_duplicate_suggestions"
        ]
    ) == 1

    dismissed = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_duplicate_suggestion",
            "target_id": newer["id"],
            "source_ids": [older["id"]],
            "dismiss": True,
        },
        headers=household_headers,
    )
    assert dismissed.status_code == 200, dismissed.text
    inbox = client.get("/v1/review", headers=household_headers).json()
    assert inbox["contract_duplicate_suggestions"] == []
    # Verneint heisst nicht geloescht: beide Vertraege stehen weiter da.
    assert len(client.get("/v1/contracts", headers=household_headers).json()) == 2


# ---------------------------------------------------------------------------
# 13b. Schluesselbewusste Duplikatvorschlaege (Abschnitt 19.8/19.10)
# ---------------------------------------------------------------------------


async def _set_contract_key(contract_id: str, contract_key: str | None) -> None:
    await _execute(
        "update contracts set contract_key = cast(:contract_key as text), "
        "contract_key_source = case when cast(:contract_key as text) is null then null "
        "else 'mandate' end "
        "where id = cast(:id as uuid)",
        id=contract_id,
        contract_key=contract_key,
    )


async def test_same_contract_key_is_strong_evidence(household_headers: Headers) -> None:
    """Der Musterfunk-Festnetz-Fall aus Abschnitt 19.1: gleicher Vertragsschluessel
    (Vertragskonto), zeitlich getrennt - starker Beleg statt Namensraten."""
    older = _create_contract(household_headers, name="Musterfunk Festnetz", amount_minor=4_995)
    newer = _create_contract(household_headers, name="Musterfunk Festnetz", amount_minor=3_995)
    await _set_seen_range(older["id"], _payday(3, 9), _payday(3, 5))
    await _set_seen_range(newer["id"], _payday(3, 4), _payday(3, 0))
    await _set_contract_key(older["id"], "1122334455")
    await _set_contract_key(newer["id"], "1122334455")

    response = client.get("/v1/contracts/duplicate-suggestions", headers=household_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["truncated"] is False
    [suggestion] = body["items"]
    assert suggestion["evidence_strength"] == "strong"
    assert "same_contract_key" in suggestion["reason_codes"]
    assert "non_overlapping_periods" in suggestion["reason_codes"]
    assert "amount_transition" in suggestion["reason_codes"]


async def test_different_contract_keys_are_never_suggested(household_headers: Headers) -> None:
    """Der Musterfunk-Mobilfunk-Fall: gleicher Anbieter, andere Vertragsnummer -
    auch bei knapper zeitlicher Luecke kein Vorschlag (Leitsatz 4)."""
    older = _create_contract(household_headers, name="Musterfunk Mobilfunk", amount_minor=4_995)
    newer = _create_contract(household_headers, name="Musterfunk Mobilfunk", amount_minor=3_995)
    await _set_seen_range(older["id"], _payday(3, 9), _payday(3, 5))
    await _set_seen_range(newer["id"], _payday(3, 4), _payday(3, 0))
    await _set_contract_key(older["id"], "0011223344")
    await _set_contract_key(newer["id"], "0099887766")

    response = client.get("/v1/contracts/duplicate-suggestions", headers=household_headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []

    inbox = client.get("/v1/review", headers=household_headers).json()
    assert inbox["contract_duplicate_suggestions"] == []


async def test_missing_key_is_only_a_review_suggestion(household_headers: Headers) -> None:
    """Fehlt einer der beiden Schluessel, bleibt der Vorschlag stehen, aber
    als schwaecherer Pruefvorschlag statt eines starken Belegs."""
    older = _create_contract(household_headers, name="Fitness Nord", amount_minor=3_990)
    newer = _create_contract(household_headers, name="Fitness Nord", amount_minor=4_490)
    await _set_seen_range(older["id"], _payday(6, 9), _payday(6, 5))
    await _set_seen_range(newer["id"], _payday(6, 4), _payday(6, 0))

    [suggestion] = client.get(
        "/v1/contracts/duplicate-suggestions", headers=household_headers
    ).json()["items"]
    assert suggestion["evidence_strength"] == "review"
    assert "same_contract_key" not in suggestion["reason_codes"]


async def test_duplicate_suggestions_endpoint_reports_truncation(
    household_headers: Headers,
) -> None:
    for index in range(3):
        older = _create_contract(
            household_headers, name=f"Anbieter {index}", amount_minor=1_000 + index
        )
        newer = _create_contract(
            household_headers, name=f"Anbieter {index}", amount_minor=2_000 + index
        )
        await _set_seen_range(older["id"], _payday(3, 9), _payday(3, 5))
        await _set_seen_range(newer["id"], _payday(3, 4), _payday(3, 0))

    response = client.get(
        "/v1/contracts/duplicate-suggestions?limit=2", headers=household_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 2
    assert body["truncated"] is True


# ---------------------------------------------------------------------------
# Randfaelle rund um die Referenzfaelle
# ---------------------------------------------------------------------------


async def test_dismissing_a_suggestion_keeps_it_out_of_the_inbox(
    household_headers: Headers, account_id: str
) -> None:
    """"Kein Vertrag" wird als Zeile mit status='not_a_contract' festgehalten -
    sonst stuende derselbe Vorschlag nach dem naechsten Import wieder da."""
    household_id = household_headers["X-Household-Id"]
    for months_ago in range(4, -1, -1):
        await _seed(
            household_id,
            account_id,
            _payday(6, months_ago),
            -1_299,
            payee_label="Nordlicht Media",
            purpose_raw="NORDLICHT MANDATSREF: NL-3344",
        )
    [suggestion] = _suggestions(household_headers)

    dismissed = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_suggestion",
            "source_transaction_id": suggestion["source_transaction_id"],
            "dismiss": True,
        },
        headers=household_headers,
    )
    assert dismissed.status_code == 200, dismissed.text
    assert _suggestions(household_headers) == []
    # Ein verneinter Vorschlag ist kein Vertrag und taucht in der Liste nicht auf.
    assert client.get("/v1/contracts", headers=household_headers).json() == []


async def test_accepting_a_suggestion_without_a_category_is_rejected(
    household_headers: Headers, account_id: str
) -> None:
    """Ein Vertrag wirkt in die Zukunft; ohne Kategorie faellt der Fehler erst
    beim naechsten Faelligwerden auf. `dismiss` darf dagegen keine
    Kategoriewahl erzwingen."""
    household_id = household_headers["X-Household-Id"]
    for months_ago in range(4, -1, -1):
        await _seed(
            household_id,
            account_id,
            _payday(6, months_ago),
            -1_299,
            payee_label="Nordlicht Media",
            purpose_raw="NORDLICHT MANDATSREF: NL-3344",
        )
    [suggestion] = _suggestions(household_headers)

    response = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_suggestion",
            "source_transaction_id": suggestion["source_transaction_id"],
        },
        headers=household_headers,
    )
    assert response.status_code == 422


async def test_an_unknown_anchor_transaction_is_a_404(household_headers: Headers) -> None:
    response = client.post(
        "/v1/review/accept",
        json={
            "type": "contract_suggestion",
            "source_transaction_id": str(uuid.uuid4()),
            "category_id": await _category_id(household_headers["X-Household-Id"], "energy"),
        },
        headers=household_headers,
    )
    assert response.status_code == 404
