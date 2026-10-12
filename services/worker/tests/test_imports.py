"""Tests fuer Import-Parser, Sicherheit und Dublettenerkennung (Phase 3)."""

from datetime import date

import pytest

from worker.imports.canonical import CanonicalTransaction, parse_amount_to_minor_units
from worker.imports.dedup import compute_fingerprint, evaluate_row_status
from worker.imports.parsers.paypal_csv import PayPalCsvParser
from worker.imports.registry import parse_file
from worker.imports.security import ImportSecurityError, check_file_size, check_row_count


def test_parse_amount_to_minor_units_german_and_decimal() -> None:
    assert parse_amount_to_minor_units("1.234,56") == 123456
    assert parse_amount_to_minor_units("-25,00 €") == -2500
    assert parse_amount_to_minor_units("25,00-") == -2500
    assert parse_amount_to_minor_units("123.45") == 12345
    assert parse_amount_to_minor_units("0,00") == 0


def test_security_checks() -> None:
    check_file_size(b"123")
    with pytest.raises(ImportSecurityError, match="leer"):
        check_file_size(b"")
    with pytest.raises(ImportSecurityError, match="gross"):
        check_file_size(b"x" * (20 * 1024 * 1024 + 1))
    with pytest.raises(ImportSecurityError, match="viele Zeilen"):
        check_row_count(50001)


def test_paypal_csv_parser() -> None:
    paypal_bytes = (
        '"Datum","Uhrzeit","Zeitzone","Name","Typ","Status","Währung","Brutto",'
        '"Transaktionscode"\n'
        '"01.12.2025","12:00:00","CET","Supermarkt GmbH","Einkauf","Abgeschlossen",'
        '"EUR","-99,00","SYNTHPP0001TESTCODE"\n'
    ).encode()

    parser = PayPalCsvParser()
    res = parser.detect(paypal_bytes, "PayPal.csv")
    assert res.matched is True
    assert res.confidence >= 0.9

    txs = parser.parse(paypal_bytes)
    assert len(txs) == 1
    assert txs[0].booked_date == date(2025, 12, 1)
    assert txs[0].amount_minor == -9900
    assert txs[0].payee_raw == "Supermarkt GmbH"
    assert txs[0].external_reference == "SYNTHPP0001TESTCODE"


def test_mt940_parser() -> None:
    mt940_bytes = (
        ":20:STARTSEPA\n"
        ":25:DE89370400440532013000\n"
        ":28C:00001/001\n"
        ":61:2512011201D25,00N056NONREF\n"
        ":86:166?00SVWE?20Aral Tankstelle?21MUSTERSTADT\n"
    ).encode("latin-1")

    fmt, txs = parse_file(mt940_bytes, "auszug.sta")
    assert fmt == "mt940"
    assert len(txs) == 1
    assert txs[0].booked_date == date(2025, 12, 1)
    assert txs[0].amount_minor == -2500
    assert "Aral Tankstelle" in (txs[0].purpose or "")


def test_camt_xml_parser() -> None:
    camt_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">\n'
        b'  <BkToCstmrStmt>\n'
        b'    <Stmt>\n'
        b'      <Ntry>\n'
        b'        <BookgDt><Dt>2026-01-02</Dt></BookgDt>\n'
        b'        <Amt Ccy="EUR">25.00</Amt>\n'
        b'        <CdtDbtInd>DBIT</CdtDbtInd>\n'
        b'        <NtryDtls><TxDtls>\n'
        b'          <RltdPties><Cdtr><Nm>Musterladen</Nm></Cdtr></RltdPties>\n'
        b'          <RmtInf><Ustrd>Einkauf MUSTERSTADT</Ustrd></RmtInf>\n'
        b'        </TxDtls></NtryDtls>\n'
        b'      </Ntry>\n'
        b'    </Stmt>\n'
        b'  </BkToCstmrStmt>\n'
        b'</Document>\n'
    )

    fmt, txs = parse_file(camt_xml, "auszug.xml")
    assert fmt == "camt"
    assert len(txs) == 1
    assert txs[0].booked_date == date(2026, 1, 2)
    assert txs[0].amount_minor == -2500
    assert txs[0].payee_raw == "Musterladen"


def test_generic_csv_parser() -> None:
    csv_bytes = (
        b"Datum;Soll/Haben;Betrag_EUR;Buchungsart;Empfaenger/Zweck\n"
        b"2026-01-02;Soll;-19,99;EINZUGSERMAECHTIGUNG;MUSTER KRANKENVERSICHERUNG\n"
    )

    fmt, txs = parse_file(csv_bytes, "umsaetze.csv")
    assert fmt == "csv_generic"
    assert len(txs) == 1
    assert txs[0].booked_date == date(2026, 1, 2)
    assert txs[0].amount_minor == -1999
    assert txs[0].payee_raw == "MUSTER KRANKENVERSICHERUNG"


def test_dedup_statuses() -> None:
    tx = CanonicalTransaction(
        booked_date=date(2026, 1, 2),
        amount_minor=-1999,
        currency="EUR",
        payee_raw="MUSTER KRANKENVERSICHERUNG",
        purpose="Beitrag",
        external_reference="REF123",
    )
    fp = compute_fingerprint(tx.booked_date, tx.amount_minor, tx.payee_raw, tx.purpose)

    # 1. External ref match -> duplicate_exact. Betrag und Datum gehoeren
    # dazu: die Referenz allein traegt diese Entscheidung nicht (siehe
    # test_external_reference_alone_is_not_a_duplicate).
    ex1 = [
        {
            "id": "uuid-1",
            "external_reference": "REF123",
            "booked_date": date(2026, 1, 2),
            "amount_minor": -1999,
        }
    ]
    status, ref_id = evaluate_row_status(tx, ex1, [])
    assert status == "duplicate_exact"
    assert ref_id == "uuid-1"

    # 2. Fingerprint match -> duplicate_exact
    ex2 = [{"id": "uuid-2", "fingerprint": fp}]
    status, ref_id = evaluate_row_status(tx, ex2, [])
    assert status == "duplicate_exact"
    assert ref_id == "uuid-2"

    # 3. Same date, amount AND payee -> duplicate_possible. Der Empfaenger
    # gehoert seit der Verschaerfung dazu; ohne ihn traf die Regel jede
    # beliebige Buchung ueber denselben Betrag (siehe dedup.classify_row).
    ex3 = [
        {
            "id": "uuid-3",
            "booked_date": date(2026, 1, 2),
            "amount_minor": -1999,
            "payee": "MUSTER KRANKENVERSICHERUNG",
        }
    ]
    status, ref_id = evaluate_row_status(tx, ex3, [])
    assert status == "duplicate_possible"
    assert ref_id == "uuid-3"

    # 3b. Gleicher Betrag und Tag, anderer Empfaenger -> keine Dublette.
    ex3b = [
        {
            "id": "uuid-3b",
            "booked_date": date(2026, 1, 2),
            "amount_minor": -1999,
            "payee": "Stadtwerke",
        }
    ]
    status, ref_id = evaluate_row_status(tx, ex3b, [])
    assert status == "new"

    # 4. Opposite amount on another account within 3 days -> transfer_candidate
    oth = [{"id": "uuid-4", "booked_date": date(2026, 1, 3), "amount_minor": 1999}]
    status, ref_id = evaluate_row_status(tx, [], oth)
    assert status == "transfer_candidate"
    assert ref_id == "uuid-4"

    # 5. Clean new transaction
    status, ref_id = evaluate_row_status(tx, [], [])
    assert status == "new"
    assert ref_id is None


def test_external_reference_alone_is_not_a_duplicate() -> None:
    """Eine wiederverwendete Bankreferenz darf keine Buchung verschlucken.

    MT940-Auszuege vergeben denselben EREF-Wert mehrfach (eine Kartenreferenz
    kann in einem Halbjahresauszug mehrfach vorkommen), und ein
    Dauerauftrag behaelt seine Referenz Monat fuer Monat. 'duplicate_exact'
    bedeutet, dass die Zeile nie uebernommen wird - deshalb muessen Betrag und
    Buchungsdatum mitstimmen."""
    tx = CanonicalTransaction(
        booked_date=date(2026, 3, 2),
        amount_minor=-1999,
        currency="EUR",
        payee_raw="MUSTER KRANKENVERSICHERUNG",
        purpose="Beitrag Maerz",
        external_reference="DAUERAUFTRAG-1",
    )

    # Gleiche Referenz, gleicher Betrag - aber die Buchung des Vormonats.
    vormonat = [
        {
            "id": "uuid-alt",
            "external_reference": "DAUERAUFTRAG-1",
            "booked_date": date(2026, 2, 2),
            "amount_minor": -1999,
        }
    ]
    status, _ = evaluate_row_status(tx, vormonat, [])
    assert status == "new"

    # Gleiche Referenz am selben Tag, aber ein anderer Betrag: ebenfalls eine
    # eigenstaendige Buchung.
    anderer_betrag = [
        {
            "id": "uuid-anders",
            "external_reference": "DAUERAUFTRAG-1",
            "booked_date": date(2026, 3, 2),
            "amount_minor": -4200,
        }
    ]
    status, _ = evaluate_row_status(tx, anderer_betrag, [])
    assert status == "new"
