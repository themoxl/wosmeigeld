"""Haushaltsweiter Legacy-Reparaturlauf (docs/VERTRAEGE-FIXKOSTEN-PLAN.md,
Abschnitt 19.9/19.12, P5.1b).

Der Referenzfall ist aus dem Plan uebernommen: drei Legacy-Zeilen
"Musterfunk Deutschland GmbH" (39,95 EUR, 49,95 EUR, 99,90 EUR), obwohl es
fachlich zwei Vertraege sind - ein Festnetzvertrag mit einer Preissenkung
(49,95 -> 39,95, gleiches Vertragskonto) und ein davon unabhaengiger
Mobilfunkvertrag (99,90, eigenes Kundenkonto).

Alle Legacy-Zeilen entstehen hier ueber `_create_legacy_contract` und damit so,
wie sie die SQL-Uebernahme in 0001_schema.sql hinterlaesst - einschliesslich
`payee_id`. Die Erstfassung dieser Tests legte sie OHNE `payee_id` an und
markierte sie erst danach per UPDATE als Legacy. Dadurch landeten Vertrag und
Kandidat beide im Namensbuendel, die Suite war gruen, und der einzige Pfad, der
im Betrieb wirklich vorkommt - Vertrag MIT `payee_id`, Buchungen OHNE - blieb
ungeprueft und fiel durch.
"""

import calendar
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.services import contract_legacy_repair_service as repair
from tests.test_household_isolation import _auth_headers, _signup

client = TestClient(app)

Headers = dict[str, str]

TODAY = date.today()
_PREV_MONTH_END = TODAY.replace(day=1) - timedelta(days=1)

MT940_DIRECT_DEBIT = "N105"

PROVIDER = "Musterfunk Deutschland GmbH"

# Zahltage der beiden geseedeten Reihen. Sie stehen als Konstanten da, weil sie
# doppelt gebraucht werden: als Buchungstag UND als `due_day` des Vertrags -
# genau dieses Paar prueft der Faelligkeitsabgleich (Abschnitt 19.9 Punkt 3).
FESTNETZ_DUE_DAY = 3
MOBILFUNK_DUE_DAY = 17


@pytest.fixture()
def household_headers() -> Headers:
    token = _signup(f"legrep-{uuid.uuid4().hex}@finanzplaner.test", "correct-horse-battery-2")
    household = client.post(
        "/v1/households", json={"name": "Reparatur-Testhaushalt"}, headers=_auth_headers(token)
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
    total = _PREV_MONTH_END.month - 1 - months_ago
    year = _PREV_MONTH_END.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_due_date(day: int) -> date:
    """Ein Faelligkeitsdatum im laufenden Monat mit genau diesem Tag - die
    Uebernahme leitet `due_day` aus `recurring_rules.next_due_date` ab, beide
    muessen deshalb zusammenpassen."""
    return date(TODAY.year, TODAY.month, min(day, calendar.monthrange(TODAY.year, TODAY.month)[1]))


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


async def _payee_id(household_id: str, name: str) -> str:
    rows = await _rows(
        "insert into payees (household_id, name) values (:household_id, :name) returning id",
        household_id=household_id,
        name=name,
    )
    return str(rows[0]["id"])


async def _import_run(household_id: str, account_id: str) -> str:
    rows = await _rows(
        "insert into import_runs (household_id, account_id, filename, file_hash, "
        "file_size_bytes, detected_format, status) values (:household_id, :account_id, "
        "'legacy.sta', :file_hash, 1024, 'mt940', 'completed') returning id",
        household_id=household_id,
        account_id=account_id,
        file_hash=uuid.uuid4().hex,
    )
    return str(rows[0]["id"])


async def _seed(
    household_id: str,
    account_id: str,
    booked_date: date,
    amount_minor: int,
    *,
    purpose_raw: str,
    import_run_id: str,
    payee_id: str | None = None,
    payee_label: str = PROVIDER,
) -> None:
    """Eine Buchung. `payee_id` ist standardmaessig NULL - das ist der Zustand
    nach einer Datenuebernahme und der Grund, warum die Buendelung nicht daran
    haengen darf."""
    await _execute(
        "insert into transactions (household_id, account_id, booked_date, amount_minor, "
        "payee_id, payee_label, purpose_raw, bank_transaction_code, import_run_id) values "
        "(:household_id, :account_id, :booked_date, :amount_minor, cast(:payee_id as uuid), "
        ":payee_label, :purpose_raw, :code, cast(:import_run_id as uuid))",
        household_id=household_id,
        account_id=account_id,
        booked_date=booked_date,
        amount_minor=amount_minor,
        payee_id=payee_id,
        payee_label=payee_label,
        purpose_raw=purpose_raw,
        code=MT940_DIRECT_DEBIT,
        import_run_id=import_run_id,
    )


def _create_legacy_contract(
    headers: Headers,
    name: str,
    amount_minor: int,
    *,
    payee_id: str,
    account_id: str,
    due_day: int,
    provider_label: str | None = PROVIDER,
    frequency: str = "monthly",
) -> dict[str, Any]:
    """Eine Zeile, wie sie die Uebernahme aus `recurring_rules` hinterlaesst.

    Nachgebildet ist der Insert aus 0001_schema.sql, Abschnitt "8. Uebernahme
    aus recurring_rules und subscriptions": `payee_id` und `account_id` werden
    aus der Altregel MITKOPIERT, `provider_label` ist der Name des verknuepften
    Empfaengers, `due_rule`/`due_day` stammen aus `next_due_date`.

    Die `payee_id` ist der Punkt, an dem sich Test und Wirklichkeit vorher
    unterschieden - und genau daran ist der Reparaturlauf im Betrieb
    gescheitert, ohne dass ein Test es gemerkt haette.
    """
    response = client.post(
        "/v1/contracts",
        json={
            "name": name,
            "provider_label": provider_label,
            "payee_id": payee_id,
            "account_id": account_id,
            "amount_minor": amount_minor,
            "frequency": frequency,
            "due_rule": "month_end" if due_day > 28 else "day_of_month",
            "due_day": due_day,
            "next_due_date": _next_due_date(due_day).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    contract = dict(response.json())
    return contract


async def _mark_as_legacy(contract_id: str, *, manual_key: str | None = None) -> None:
    """Setzt genau den Marker, an dem der Reparaturlauf Legacy-Zeilen erkennt:
    `legacy_recurring_rule_id` gesetzt, `first_seen_date`/`last_seen_date` leer.
    `legacy_recurring_rule_id` traegt keinen Fremdschluessel (0001_schema.sql) -
    ein beliebiger UUID-Wert genuegt als Marker."""
    if manual_key is not None:
        await _execute(
            "update contracts set legacy_recurring_rule_id = gen_random_uuid(), "
            "contract_key = :key, contract_key_source = 'manual' where id = cast(:id as uuid)",
            id=contract_id,
            key=manual_key,
        )
    else:
        await _execute(
            "update contracts set legacy_recurring_rule_id = gen_random_uuid() "
            "where id = cast(:id as uuid)",
            id=contract_id,
        )


async def _seed_musterfunk_household(
    household_id: str, account_id: str, *, payee_id: str | None = None
) -> None:
    """Sechs Monate Vertragskonto (drei zu 49,95, drei zu 39,95) und sechs
    Monate Kundenkonto (99,90) - genug Vorkommen fuer einen Vertrags-Score
    ueber der Anzeigeschwelle."""
    import_run_id = await _import_run(household_id, account_id)
    for months_ago in range(6, 3, -1):
        await _seed(
            household_id,
            account_id,
            _payday(FESTNETZ_DUE_DAY, months_ago),
            -4995,
            purpose_raw="MUSTERFUNK FESTNETZ VERTRAGSKONTO 1122334455",
            import_run_id=import_run_id,
            payee_id=payee_id,
        )
    for months_ago in range(3, 0, -1):
        await _seed(
            household_id,
            account_id,
            _payday(FESTNETZ_DUE_DAY, months_ago),
            -3995,
            purpose_raw="MUSTERFUNK FESTNETZ VERTRAGSKONTO 1122334455",
            import_run_id=import_run_id,
            payee_id=payee_id,
        )
    for months_ago in range(6, 0, -1):
        await _seed(
            household_id,
            account_id,
            _payday(MOBILFUNK_DUE_DAY, months_ago),
            -9990,
            purpose_raw="MUSTERFUNK MOBILFUNK KUNDENKONTO 0011223344",
            import_run_id=import_run_id,
            payee_id=payee_id,
        )


async def _musterfunk_legacy_rows(
    household_headers: Headers, account_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Die drei Legacy-Zeilen des Referenzfalls, verknuepft mit EINEM Empfaenger
    - so wie die Uebernahme sie aus drei Altregeln desselben Anbieters erzeugt."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _payee_id(household_id, PROVIDER)
    alt = _create_legacy_contract(
        household_headers,
        PROVIDER,
        4995,
        payee_id=payee_id,
        account_id=account_id,
        due_day=FESTNETZ_DUE_DAY,
    )
    neu = _create_legacy_contract(
        household_headers,
        PROVIDER,
        3995,
        payee_id=payee_id,
        account_id=account_id,
        due_day=FESTNETZ_DUE_DAY,
    )
    mobil = _create_legacy_contract(
        household_headers,
        PROVIDER,
        9990,
        payee_id=payee_id,
        account_id=account_id,
        due_day=MOBILFUNK_DUE_DAY,
    )
    for contract in (alt, neu, mobil):
        await _mark_as_legacy(contract["id"])
    return alt, neu, mobil


async def _analyze(household_id: str) -> repair.LegacyRepairPlan:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await repair.analyze_household(session, uuid.UUID(household_id))


@pytest.mark.asyncio
async def test_dry_run_reports_one_merge_suggestion_and_leaves_mobilfunk_alone(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    alt, neu, mobil = await _musterfunk_legacy_rows(household_headers, account_id)
    await _seed_musterfunk_household(household_id, account_id)

    plan = await _analyze(household_id)

    assert plan.contracts_scanned == 3
    assert plan.skipped_manual == 0
    assert plan.contracts_enriched == 3
    assert plan.ambiguous == 0
    assert len(plan.merge_suggestions) == 1
    [suggestion] = plan.merge_suggestions
    assert str(suggestion.target_id) == neu["id"]
    assert [str(item) for item in suggestion.source_ids] == [alt["id"]]

    # Der Mobilfunkvertrag ist an KEINEM Merge-Vorschlag beteiligt.
    involved = {str(suggestion.target_id), *[str(item) for item in suggestion.source_ids]}
    assert mobil["id"] not in involved

    # Dry-Run: nichts geschrieben.
    unchanged = await _rows(
        "select first_seen_date, contract_key from contracts where id = cast(:id as uuid)",
        id=alt["id"],
    )
    assert unchanged[0]["first_seen_date"] is None
    assert unchanged[0]["contract_key"] is None


@pytest.mark.asyncio
async def test_a_legacy_contract_with_payee_id_finds_transactions_without_one(
    household_headers: Headers, account_id: str
) -> None:
    """Der asymmetrische Fall, an dem der Lauf im Betrieb gescheitert ist.

    Die Uebernahme kopiert `rr.payee_id` in den Vertrag, die Buchungen des
    Altbestands tragen aber durchgehend `payee_id is null`. Buendelte man die
    eine Seite ueber die Empfaenger-ID und die andere ueber den Namen, koennten
    sich die Buendel nie treffen - der Lauf meldete 26-mal
    "no_matching_transactions", obwohl die Clusterung 25 Zahlungsreihen fand.
    """
    household_id = household_headers["X-Household-Id"]
    alt, neu, mobil = await _musterfunk_legacy_rows(household_headers, account_id)
    await _seed_musterfunk_household(household_id, account_id)

    # Die Voraussetzung des Falls steht ausdruecklich im Test: Vertrag MIT,
    # Buchung OHNE Empfaengerverknuepfung. Ohne diese Zusicherung koennte ein
    # spaeterer Umbau die Asymmetrie unbemerkt aufloesen und der Test bewiese
    # wieder nichts.
    contracts = await _rows(
        "select count(*) filter (where payee_id is not null) as linked, count(*) as total "
        "from contracts where household_id = cast(:h as uuid)",
        h=household_id,
    )
    assert contracts[0]["linked"] == contracts[0]["total"] == 3
    transactions = await _rows(
        "select count(*) filter (where payee_id is null) as unlinked, count(*) as total "
        "from transactions where household_id = cast(:h as uuid)",
        h=household_id,
    )
    assert transactions[0]["unlinked"] == transactions[0]["total"] == 12

    plan = await _analyze(household_id)

    assert plan.contracts_enriched == 3
    assert plan.ambiguous == 0
    keys = {str(item.contract_id): item.contract_key for item in plan.enriched_contracts}
    assert keys[alt["id"]] == keys[neu["id"]] == "1122334455"
    assert keys[mobil["id"]] == "0011223344"


@pytest.mark.asyncio
async def test_the_payee_name_bridges_a_stale_provider_label(
    household_headers: Headers, account_id: str
) -> None:
    """Umbenannter Empfaenger: `provider_label` ist die Schreibweise vom Tag der
    Migration, `payees.name` die heutige - und nur letztere steht auch an den
    Buchungen. Die Zuordnung muss ueber den Empfaengernamen laufen, nicht ueber
    das eingefrorene Etikett am Vertrag."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _payee_id(household_id, PROVIDER)
    contract = _create_legacy_contract(
        household_headers,
        "Festnetz",
        9990,
        payee_id=payee_id,
        account_id=account_id,
        due_day=MOBILFUNK_DUE_DAY,
        provider_label="Deutsche Musterfunk AG (alte Schreibweise)",
    )
    await _mark_as_legacy(contract["id"])
    await _seed_musterfunk_household(household_id, account_id)

    plan = await _analyze(household_id)

    assert plan.contracts_enriched == 1
    assert str(plan.enriched_contracts[0].contract_id) == contract["id"]
    assert plan.enriched_contracts[0].contract_key == "0011223344"


@pytest.mark.asyncio
async def test_a_different_payee_link_beats_the_matching_name(
    household_headers: Headers, account_id: str
) -> None:
    """Die Empfaenger-ID bleibt der staerkere Beleg.

    Beide Seiten tragen hier eine `payee_id`, aber verschiedene. Dass die Namen
    auf denselben Schluessel normalisieren (der Punkt am Ende faellt weg), darf
    daran nichts aendern - sonst waere die gelockerte Buendelung ein Rueckschritt
    gegenueber der Empfaengerpruefung.
    """
    household_id = household_headers["X-Household-Id"]
    contract_payee = await _payee_id(household_id, PROVIDER)
    transaction_payee = await _payee_id(household_id, f"{PROVIDER}.")
    contract = _create_legacy_contract(
        household_headers,
        PROVIDER,
        9990,
        payee_id=contract_payee,
        account_id=account_id,
        due_day=MOBILFUNK_DUE_DAY,
    )
    await _mark_as_legacy(contract["id"])
    await _seed_musterfunk_household(household_id, account_id, payee_id=transaction_payee)

    plan = await _analyze(household_id)

    assert plan.contracts_enriched == 0
    assert [item.reason for item in plan.ambiguous_contracts] == ["different_payee_link"]


@pytest.mark.asyncio
async def test_a_far_off_due_day_blocks_the_match(
    household_headers: Headers, account_id: str
) -> None:
    """Faelligkeitstag als Trennmerkmal (Abschnitt 19.9 Punkt 3).

    Der Betrag passt, der Anbieter passt, aber gebucht wird am 17. und faellig
    ist der 2. - das ist keine Verschiebung auf den naechsten Bankarbeitstag
    mehr, sondern eine andere Zahlungsreihe.
    """
    household_id = household_headers["X-Household-Id"]
    payee_id = await _payee_id(household_id, PROVIDER)
    contract = _create_legacy_contract(
        household_headers,
        PROVIDER,
        9990,
        payee_id=payee_id,
        account_id=account_id,
        due_day=2,
    )
    await _mark_as_legacy(contract["id"])
    await _seed_musterfunk_household(household_id, account_id)

    plan = await _analyze(household_id)

    assert plan.contracts_enriched == 0
    assert [item.reason for item in plan.ambiguous_contracts] == ["due_day_mismatch"]


@pytest.mark.asyncio
async def test_a_different_frequency_blocks_the_match(
    household_headers: Headers, account_id: str
) -> None:
    """Frequenz als Trennmerkmal: ein vierteljaehrlicher Vertrag erklaert keine
    monatliche Zahlungsreihe, auch wenn Anbieter und Betrag passen."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _payee_id(household_id, PROVIDER)
    contract = _create_legacy_contract(
        household_headers,
        PROVIDER,
        9990,
        payee_id=payee_id,
        account_id=account_id,
        due_day=MOBILFUNK_DUE_DAY,
        frequency="quarterly",
    )
    await _mark_as_legacy(contract["id"])
    await _seed_musterfunk_household(household_id, account_id)

    plan = await _analyze(household_id)

    assert plan.contracts_enriched == 0
    assert [item.reason for item in plan.ambiguous_contracts] == ["frequency_mismatch"]


@pytest.mark.asyncio
async def test_a_foreign_account_blocks_the_match(
    household_headers: Headers, account_id: str
) -> None:
    """Konto als Trennmerkmal: derselbe Anbieter kann ueber zwei Konten
    abbuchen, und der Vertrag gehoert nur zu einem davon."""
    household_id = household_headers["X-Household-Id"]
    other_account = client.post(
        "/v1/accounts",
        json={"name": "Zweitkonto", "account_type": "checking"},
        headers=household_headers,
    ).json()
    payee_id = await _payee_id(household_id, PROVIDER)
    contract = _create_legacy_contract(
        household_headers,
        PROVIDER,
        9990,
        payee_id=payee_id,
        account_id=str(other_account["id"]),
        due_day=MOBILFUNK_DUE_DAY,
    )
    await _mark_as_legacy(contract["id"])
    await _seed_musterfunk_household(household_id, account_id)

    plan = await _analyze(household_id)

    assert plan.contracts_enriched == 0
    assert [item.reason for item in plan.ambiguous_contracts] == ["account_mismatch"]


@pytest.mark.asyncio
async def test_apply_enriches_all_three_and_is_idempotent(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    alt, neu, mobil = await _musterfunk_legacy_rows(household_headers, account_id)
    await _seed_musterfunk_household(household_id, account_id)

    session_factory = get_session_factory()
    async with session_factory() as session:
        first = await repair.apply_household(session, uuid.UUID(household_id))
        await session.commit()

    assert first.contracts_enriched == 3
    assert first.keys_enriched == 3

    rows = await _rows(
        "select id, contract_key, contract_key_source, first_seen_date, last_seen_date "
        "from contracts where household_id = cast(:household_id as uuid) order by amount_minor",
        household_id=household_id,
    )
    by_id = {str(row["id"]): row for row in rows}
    # 49,95 und 39,95 teilen denselben Schluessel - genau das macht sie danach
    # ueber die schluesselbewusste Duplikaterkennung (P5.1a) auffindbar.
    assert by_id[alt["id"]]["contract_key"] == by_id[neu["id"]]["contract_key"] == "1122334455"
    assert by_id[mobil["id"]]["contract_key"] == "0011223344"
    for contract_id in (alt["id"], neu["id"], mobil["id"]):
        assert by_id[contract_id]["first_seen_date"] is not None
        assert by_id[contract_id]["last_seen_date"] is not None
        # "Vertragskonto"/"Kundenkonto" sind bekannte Schluesselmuster, also
        # eine Mandatsreferenz - und nicht die generische Ableitung.
        assert by_id[contract_id]["contract_key_source"] == "mandate"

    occurrences = await _rows(
        "select count(*) as n from contract_occurrences where household_id = cast(:h as uuid)",
        h=household_id,
    )
    assert occurrences[0]["n"] == 12  # 6 Vertragskonto + 6 Kundenkonto

    # Jetzt zeigt die schluesselbewusste Duplikaterkennung den Merge-Vorschlag.
    suggestions = client.get(
        "/v1/contracts/duplicate-suggestions", headers=household_headers
    ).json()
    assert suggestions["total"] == 1
    assert suggestions["items"][0]["evidence_strength"] == "strong"

    # Zweiter Lauf: idempotent, keine der drei Zeilen ist noch "eligible"
    # (first_seen_date ist jetzt gesetzt), also nichts zu tun.
    session_factory = get_session_factory()
    async with session_factory() as session:
        second = await repair.apply_household(session, uuid.UUID(household_id))
        await session.commit()
    assert second.contracts_scanned == 0
    assert second.contracts_enriched == 0

    occurrences_after = await _rows(
        "select count(*) as n from contract_occurrences where household_id = cast(:h as uuid)",
        h=household_id,
    )
    assert occurrences_after[0]["n"] == 12


@pytest.mark.asyncio
async def test_a_series_without_contract_key_gets_the_period_but_no_empty_key(
    household_headers: Headers, account_id: str
) -> None:
    """Ohne Mandatsreferenz und ohne verwertbares Muster im Zweck bleibt
    `contract_key` NULL. Ein leerer Schluessel mit `contract_key_source =
    'mandate'` waere eine Behauptung ohne Beleg - der Zeitraum ist trotzdem eine
    Verbesserung, und `keys_enriched` zaehlt getrennt."""
    household_id = household_headers["X-Household-Id"]
    payee_id = await _payee_id(household_id, "Nachbarschaftshilfe")
    contract = _create_legacy_contract(
        household_headers,
        "Nachbarschaftshilfe",
        2500,
        payee_id=payee_id,
        account_id=account_id,
        due_day=FESTNETZ_DUE_DAY,
        provider_label="Nachbarschaftshilfe",
    )
    await _mark_as_legacy(contract["id"])
    import_run_id = await _import_run(household_id, account_id)
    for months_ago in range(6, 0, -1):
        await _seed(
            household_id,
            account_id,
            _payday(FESTNETZ_DUE_DAY, months_ago),
            -2500,
            purpose_raw="DAUERAUFTRAG",
            import_run_id=import_run_id,
            payee_label="Nachbarschaftshilfe",
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan = await repair.apply_household(session, uuid.UUID(household_id))
        await session.commit()

    assert plan.contracts_enriched == 1
    assert plan.keys_enriched == 0

    written = await _rows(
        "select contract_key, contract_key_source, first_seen_date from contracts "
        "where id = cast(:id as uuid)",
        id=contract["id"],
    )
    assert written[0]["contract_key"] is None
    assert written[0]["contract_key_source"] is None
    assert written[0]["first_seen_date"] is not None


@pytest.mark.asyncio
async def test_a_manually_set_key_is_never_touched(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    payee_id = await _payee_id(household_id, PROVIDER)
    manual = _create_legacy_contract(
        household_headers,
        PROVIDER,
        9990,
        payee_id=payee_id,
        account_id=account_id,
        due_day=MOBILFUNK_DUE_DAY,
    )
    await _mark_as_legacy(manual["id"], manual_key="HANDGESETZT-123")
    await _seed_musterfunk_household(household_id, account_id)

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan = await repair.apply_household(session, uuid.UUID(household_id))
        await session.commit()

    assert plan.contracts_scanned == 1
    assert plan.skipped_manual == 1
    assert plan.contracts_enriched == 0

    # Abschnitt 19.9: das Ergebnis muss nennen, WELCHE Zeile uebersprungen wurde.
    # Eine blosse Zahl laesst nicht nachpruefen, ob der Lauf das Richtige
    # ausgelassen hat.
    assert [
        (str(item.contract_id), item.name, item.reason)
        for item in plan.skipped_manual_contracts
    ] == [(manual["id"], PROVIDER, "manual_contract_key")]

    unchanged = await _rows(
        "select contract_key, first_seen_date from contracts where id = cast(:id as uuid)",
        id=manual["id"],
    )
    assert unchanged[0]["contract_key"] == "HANDGESETZT-123"
    assert unchanged[0]["first_seen_date"] is None


@pytest.mark.asyncio
async def test_a_legacy_contract_without_matching_transactions_is_ambiguous(
    household_headers: Headers, account_id: str
) -> None:
    household_id = household_headers["X-Household-Id"]
    payee_id = await _payee_id(household_id, "Nie gebuchter Anbieter")
    orphan = _create_legacy_contract(
        household_headers,
        "Nie gebuchter Vertrag",
        999,
        payee_id=payee_id,
        account_id=account_id,
        due_day=FESTNETZ_DUE_DAY,
        provider_label="Nie gebuchter Anbieter",
    )
    await _mark_as_legacy(orphan["id"])

    plan = await _analyze(household_id)

    assert plan.contracts_scanned == 1
    assert plan.contracts_enriched == 0
    assert plan.ambiguous == 1
    assert plan.ambiguous_contracts[0].reason == "no_matching_transactions"
