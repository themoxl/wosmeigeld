"""Parser-Tests gegen anonymisierte, handgeschriebene Golden Fixtures
(packages/test-fixtures/imports/) - keine echten Bankdaten (Architekturplan
Abschnitt 5, Punkt 10 und Abschnitt 15)."""

from datetime import date
from pathlib import Path

import pytest

from worker.imports.canonical import ImportParseError
from worker.imports.parsers.camt import CamtParser
from worker.imports.parsers.csv_generic import GenericCsvParser
from worker.imports.parsers.mt940 import Mt940Parser
from worker.imports.parsers.paypal_csv import PayPalCsvParser
from worker.imports.parsers.registry import PARSERS, detect_format, parse_file
from worker.imports.parsers.xls_generic import GenericXlsParser
from worker.imports.parsers.xlsx_generic import GenericXlsxParser

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "packages" / "test-fixtures" / "imports"


def test_mlp_columns_keep_recipient_and_purpose_distinct() -> None:
    content = (
        b"Bezeichnung Auftragskonto;Buchungstag;Valutadatum;Name Zahlungsbeteiligter;"
        b"Buchungstext;Verwendungszweck;Betrag;Waehrung\n"
        b"Kreditkarte;04.09.2026;05.09.2026;Muster Markt;EINZUGSERMAECHTIGUNG;"
        b"Kartenzahlung vom 02.09.2026;-75,00;EUR\n"
    )
    row = GenericCsvParser().parse(content)[0]
    assert row.booked_date == date(2026, 9, 4)
    assert row.amount_minor == -7500
    assert row.payee_raw == "Muster Markt"
    assert row.purpose == "Kartenzahlung vom 02.09.2026"


def _read(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_generic_csv_parser_auto_detects_columns() -> None:
    file_bytes = _read("csv_generic_sample.csv")
    parser = GenericCsvParser()

    detection = parser.detect(file_bytes, "csv_generic_sample.csv")
    assert detection.matched

    rows = parser.parse(file_bytes)
    assert len(rows) == 3
    assert rows[0].booked_date == date(2026, 1, 5)
    assert rows[0].amount_minor == -4250
    assert rows[0].payee_raw == "Supermarkt Muster GmbH"
    assert rows[1].amount_minor == 250000


def test_generic_csv_parser_requires_mapping_when_columns_unrecognized() -> None:
    parser = GenericCsvParser()
    file_bytes = b"Spalte A;Spalte B\nx;y\n"
    with pytest.raises(ImportParseError):
        parser.parse(file_bytes)


def test_csv_parser_with_explicit_mapping_profile() -> None:
    # Spaltennamen, die keiner der DATE_ALIASES/AMOUNT_ALIASES/PAYEE_ALIASES
    # entsprechen - automatische Erkennung muss fehlschlagen, ein explizites
    # Mapping-Profil (z. B. aus import_mapping_profiles) muss trotzdem
    # funktionieren.
    file_bytes = b"Wertstellung;Zahlungsbetrag;Auftraggeber\n05.01.2026;-42,50;Muster GmbH\n"

    with pytest.raises(ImportParseError):
        GenericCsvParser().parse(file_bytes)

    mapping = {"booked_date": "Wertstellung", "amount": "Zahlungsbetrag", "payee": "Auftraggeber"}
    detected_format, rows = parse_file(
        file_bytes, "custom.csv", forced_format="csv_generic", column_mapping=mapping
    )
    assert detected_format == "csv_generic"
    assert len(rows) == 1
    assert rows[0].booked_date == date(2026, 1, 5)
    assert rows[0].amount_minor == -4250
    assert rows[0].payee_raw == "Muster GmbH"


def test_paypal_parser() -> None:
    file_bytes = _read("paypal_sample.csv")
    parser = PayPalCsvParser()

    detection = parser.detect(file_bytes, "paypal_sample.csv")
    assert detection.matched
    assert detection.confidence > 0.9

    rows = parser.parse(file_bytes)
    assert len(rows) == 2
    assert rows[0].amount_minor == -1999
    assert rows[0].external_reference == "SYNTHPP0001TESTCODE"
    assert rows[0].booked_date == date(2026, 1, 8)
    assert rows[1].amount_minor == 1999


def test_mt940_parser() -> None:
    file_bytes = _read("mt940_sample.mta")
    parser = Mt940Parser()

    detection = parser.detect(file_bytes, "mt940_sample.mta")
    assert detection.matched

    rows = parser.parse(file_bytes)
    assert len(rows) == 2
    assert rows[0].booked_date == date(2026, 1, 5)
    assert rows[0].amount_minor == -4250
    assert rows[0].external_reference == "SYNTH0001"
    assert rows[0].account_signature == "DE00000000000000000000"
    assert rows[1].amount_minor == 250000
    assert rows[1].external_reference == "SYNTH0002"


# ---------------------------------------------------------------------------
# MT940: das strukturierte Mehrzweckfeld :86:
#
# Die Fixture mt940_sepa_multiblock.sta bildet die Eigenheiten echter
# deutscher Auszuege nach: mehrere Auszugsblocke mit '-' als Abschluss, der auf
# 27-Zeichen-Stuecke verteilte Verwendungszweck (Woerter und IBANs brechen
# mitten durch), ein zweiteiliger Empfaengername (?32/?33), der leere
# Platzhalter 'KREF+' im Referenzfeld von :61: und ein Buchungsdatum, das im
# Folgejahr des Valutadatums liegt.
# ---------------------------------------------------------------------------


def test_mt940_reassembles_chunked_purpose_and_payee() -> None:
    rows = Mt940Parser().parse(_read("mt940_sepa_multiblock.sta"))
    assert len(rows) == 4
    row = rows[0]

    # ?32 und ?33 gehoeren zusammen - vorher endete der Name nach 27 Zeichen.
    assert row.payee_raw == "Musterversicherung Lebensversicherung auf Gegenseitigkeit"

    # Die Stuecke des Verwendungszwecks werden OHNE Trennzeichen gefuegt:
    # '...-Ih' + 'r Beitrag...' ergibt ein Wort, 'IBAN: DE' + '1234...' eine
    # vollstaendige IBAN.
    assert row.purpose == (
        "Vers-Nr:00001234567-Ihr Beitrag:01.26 IBAN: DE12345678901234567890"
    )
    # Keine rohen Subfeldmarker mehr im Zweck.
    assert "?2" not in (row.purpose or "")


def test_mt940_uses_booking_date_and_keeps_value_date() -> None:
    rows = Mt940Parser().parse(_read("mt940_sepa_multiblock.sta"))

    # :61:2512310102... - sechsstellig die Valuta (31.12.2025), vierstellig das
    # Buchungsdatum (02.01.), dessen Jahr aus der Valuta abgeleitet wird und
    # hier ueber den Jahreswechsel springt.
    assert rows[0].value_date == date(2025, 12, 31)
    assert rows[0].booked_date == date(2026, 1, 2)


def test_mt940_takes_transaction_code_from_tag86() -> None:
    """Der Geschaeftsvorfallcode steht in :86:, nicht in :61:.

    `contract_detection_service._mt940_score` wertet 'N' + drei Ziffern aus
    (105 = Lastschrift, 117 = Dauerauftrag). Frueher landete hier der
    SWIFT-Buchungsschluessel NDDT/NTRF, aus dem die Auswertung keine Ziffern
    ziehen konnte - die Zahlungsart blieb bei jedem MT940-Import neutral."""
    rows = Mt940Parser().parse(_read("mt940_sepa_multiblock.sta"))
    assert [row.bank_transaction_code for row in rows] == ["N105", "N166", "N835", "N117"]


def test_mt940_rejects_placeholder_references() -> None:
    """'KREF+' und 'NONREF' sind Platzhalter, keine Referenzen.

    Landen sie als external_reference in der Datenbank, haelt
    dedup.classify_row jede spaetere Buchung mit demselben Platzhalter fuer ein
    exaktes Duplikat und uebernimmt sie nie."""
    rows = Mt940Parser().parse(_read("mt940_sepa_multiblock.sta"))

    # Punkt mitten in der Referenz: fruehere Zeichenklasse brach hier ab und
    # lieferte das nutzlose 'Beleg-Nr'.
    assert rows[0].external_reference == "Beleg-Nr.100000000001"
    # Kein EREF, dafuer eine echte Kundenreferenz aus :86:.
    assert rows[1].external_reference == "2026010588795000903800000000001584"
    # ':61:...NONREF' und ':61:...KREF+' tragen keine Referenz.
    assert rows[2].external_reference is None
    assert rows[3].external_reference is None


def test_mt940_card_statement_without_tag32_still_has_payee() -> None:
    """Kartenabrechnungen (GVC 8xx) fuehren kein ?32.

    Der Haendlername steht dort im ersten Stueck des Verwendungszwecks. Ohne
    diesen Rueckfall blieb eine komplette Kreditkartendatei ohne Empfaenger."""
    rows = Mt940Parser().parse(_read("mt940_sepa_multiblock.sta"))
    assert rows[2].payee_raw == "Musterhaendler AG"
    assert rows[2].purpose == (
        "Musterhaendler AG DEU MUSTERSTADT EUR 40,30 Umsatz vom 05.01.2026 MC MAX MUSTERMANN"
    )


def test_mt940_block_separator_is_not_part_of_a_booking() -> None:
    """Der Strich am Blockende gehoert zu keiner Buchung.

    Frueher hing er an der jeweils letzten Buchung jedes Auszugs - im
    Verwendungszweck und sogar im Empfaengernamen."""
    rows = Mt940Parser().parse(_read("mt940_sepa_multiblock.sta"))
    for row in rows:
        assert not (row.purpose or "").endswith("-")
        assert not (row.payee_raw or "").endswith("-")
    assert rows[3].payee_raw == "Mustervermietung eG"
    assert rows[3].purpose == "Miete Stellplatz"


def test_mt940_statement_summary_reconciles() -> None:
    """Saldenprobe: Anfangssaldo + Buchungen = Schlusssaldo, Block fuer Block.

    Das ist der Nachweis, dass keine Buchung verloren ging - genau die Frage,
    die man einer eingelesenen Bankdatei sonst nicht ansieht."""
    rows, summary = Mt940Parser().parse_statement(_read("mt940_sepa_multiblock.sta"))

    assert summary.reconciles
    assert summary.unbalanced_blocks == ()
    assert summary.opening is not None
    assert summary.opening.balance_minor == 100000
    assert summary.opening.balance_date == date(2025, 12, 31)
    assert summary.closing is not None
    assert summary.closing.balance_minor == 336200
    assert summary.account_signature == "10010010/1234567890"
    # Und die Probe stimmt auch gegen die Buchungen selbst.
    assert summary.opening.balance_minor + sum(r.amount_minor for r in rows) == (
        summary.closing.balance_minor
    )


def test_mt940_statement_summary_flags_missing_bookings() -> None:
    """Fehlt eine Buchung, muss die Saldenprobe das melden."""
    statement = (
        ":20:STARTUMS\n"
        ":25:10010010/1234567890\n"
        ":60F:C260101EUR1000,00\n"
        ":61:2601020102DR100,00NDDTNONREF\n"
        ":86:105?00LASTSCHRIFT?20SVWZ+Beitrag?32Muster AG\n"
        # Die Bank meldet 800,00 - es fehlen also 100,00 an Buchungen.
        ":62F:C260102EUR800,00\n"
        "-\n"
    ).encode("latin-1")

    _, summary = Mt940Parser().parse_statement(statement)
    assert not summary.reconciles
    assert len(summary.unbalanced_blocks) == 1


def test_mt940_reversals_move_money_the_other_way() -> None:
    """RC/RD sind Storni: 'Reversal of credit' bucht aus, nicht ein."""
    statement = (
        ":20:STARTUMS\n"
        ":25:10010010/1234567890\n"
        ":60F:C260101EUR1000,00\n"
        ":61:2601020102RC50,00NTRFNONREF\n"
        ":86:105?00STORNO GUTSCHRIFT?20SVWZ+Storno?32Muster AG\n"
        ":61:2601020102RD30,00NTRFNONREF\n"
        ":86:105?00STORNO LASTSCHRIFT?20SVWZ+Storno?32Muster AG\n"
        ":62F:C260102EUR980,00\n"
        "-\n"
    ).encode("latin-1")

    rows, summary = Mt940Parser().parse_statement(statement)
    assert rows[0].amount_minor == -5000
    assert rows[1].amount_minor == 3000
    assert summary.reconciles


def test_camt_parser() -> None:
    file_bytes = _read("camt_sample.xml")
    parser = CamtParser()

    detection = parser.detect(file_bytes, "camt_sample.xml")
    assert detection.matched

    rows = parser.parse(file_bytes)
    assert len(rows) == 2
    assert rows[0].booked_date == date(2026, 1, 5)
    assert rows[0].amount_minor == -4250
    assert rows[0].currency == "EUR"
    assert rows[0].payee_raw == "Supermarkt Muster GmbH"
    assert rows[1].amount_minor == 250000
    assert rows[1].payee_raw == "Arbeitgeber Muster AG"


def test_camt_054_parser() -> None:
    file_bytes = _read("camt_054_sample.xml")
    parser = CamtParser()

    detection = parser.detect(file_bytes, "camt_054_sample.xml")
    assert detection.matched

    rows = parser.parse(file_bytes)
    assert len(rows) == 2
    assert rows[0].booked_date == date(2026, 1, 5)
    assert rows[0].amount_minor == -4250
    assert rows[0].payee_raw == "Supermarkt Muster GmbH"
    assert rows[1].amount_minor == 250000


# Realistischer camt.053-Ausschnitt: eine ISO-Domn-Lastschrift, ein
# DK-Prtry-Dauerauftrag (Sparkassen/Volksbanken liefern nur diese Variante),
# eine Buchung ganz ohne BkTxCd, eine Kartenzahlung mit BkTxCd erst in den
# Transaktionsdetails und eine Gutschrift ohne SubFmlyCd.
CAMT_BK_TX_CD_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt>
    <Stmt>
      <Ntry>
        <Amt Ccy="EUR">29.99</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-01-05</Dt></BookgDt>
        <ValDt><Dt>2026-01-05</Dt></ValDt>
        <BkTxCd>
          <Domn>
            <Cd>PMNT</Cd>
            <Fmly><Cd>RDDT</Cd><SubFmlyCd>ESDD</SubFmlyCd></Fmly>
          </Domn>
          <Prtry><Cd>NDDT+105</Cd><Issr>DK</Issr></Prtry>
        </BkTxCd>
        <NtryDtls>
          <TxDtls>
            <RltdPties><Cdtr><Pty><Nm>Streamdienst Muster GmbH</Nm></Pty></Cdtr></RltdPties>
            <RmtInf><Ustrd>Abo Januar 2026</Ustrd></RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">850.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-01-02</Dt></BookgDt>
        <BkTxCd>
          <Prtry><Cd>NSTO+152</Cd><Issr>DK</Issr></Prtry>
        </BkTxCd>
        <NtryDtls>
          <TxDtls>
            <RltdPties><Cdtr><Pty><Nm>Vermieter Muster</Nm></Pty></Cdtr></RltdPties>
            <RmtInf><Ustrd>Miete Januar 2026</Ustrd></RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">12.30</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-01-07</Dt></BookgDt>
        <NtryDtls>
          <TxDtls>
            <RmtInf><Ustrd>Barabhebung</Ustrd></RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">42.50</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-01-08</Dt></BookgDt>
        <NtryDtls>
          <TxDtls>
            <BkTxCd>
              <Domn>
                <Cd>PMNT</Cd>
                <Fmly><Cd>CCRD</Cd><SubFmlyCd>POSD</SubFmlyCd></Fmly>
              </Domn>
            </BkTxCd>
            <RltdPties><Cdtr><Pty><Nm>Supermarkt Muster GmbH</Nm></Pty></Cdtr></RltdPties>
            <RmtInf><Ustrd>Kartenzahlung</Ustrd></RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">2500.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <BookgDt><Dt>2026-01-30</Dt></BookgDt>
        <BkTxCd>
          <Domn>
            <Cd>PMNT</Cd>
            <Fmly><Cd>RCDT</Cd></Fmly>
          </Domn>
        </BkTxCd>
        <NtryDtls>
          <TxDtls>
            <RltdPties><Dbtr><Pty><Nm>Arbeitgeber Muster AG</Nm></Pty></Dbtr></RltdPties>
            <RmtInf><Ustrd>Gehalt Januar 2026</Ustrd></RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""


def test_camt_parser_extracts_bank_transaction_code() -> None:
    rows = CamtParser().parse(CAMT_BK_TX_CD_SAMPLE)

    assert len(rows) == 5
    # Domn schlaegt Prtry: die ISO-Ebenen sind institutsuebergreifend gleich.
    assert rows[0].bank_transaction_code == "PMNT/RDDT/ESDD"
    assert rows[1].bank_transaction_code == "NSTO+152"
    assert rows[2].bank_transaction_code is None
    # BkTxCd nur in den Transaktionsdetails - der Eintrag selbst hat keinen.
    assert rows[3].bank_transaction_code == "PMNT/CCRD/POSD"
    # Fehlende Ebene wird weggelassen, nicht mit einem Platzhalter aufgefuellt.
    assert rows[4].bank_transaction_code == "PMNT/RCDT"

    # Der Code darf die uebrigen Felder nicht verschieben.
    assert rows[0].amount_minor == -2999
    assert rows[0].payee_raw == "Streamdienst Muster GmbH"
    assert rows[4].amount_minor == 250000


def test_camt_parser_bank_transaction_code_is_none_without_bktxcd() -> None:
    # Die Golden Fixtures tragen kein BkTxCd - dort bleibt das Feld leer,
    # statt auf einen Ersatzwert auszuweichen.
    rows = CamtParser().parse(_read("camt_sample.xml"))
    assert [row.bank_transaction_code for row in rows] == [None, None]


def test_xlsx_generic_parser_with_explicit_mapping_profile() -> None:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    sheet = wb.active
    assert sheet is not None
    sheet.append(["Wertstellung", "Zahlungsbetrag", "Auftraggeber"])
    sheet.append(["2026-01-10", "-55.30", "Beispielhaendler AG"])
    buf = io.BytesIO()
    wb.save(buf)
    file_bytes = buf.getvalue()

    mapping = {"booked_date": "Wertstellung", "amount": "Zahlungsbetrag", "payee": "Auftraggeber"}
    rows = GenericXlsxParser().parse_with_mapping(file_bytes, mapping)
    assert len(rows) == 1
    assert rows[0].booked_date == date(2026, 1, 10)
    assert rows[0].amount_minor == -5530
    assert rows[0].payee_raw == "Beispielhaendler AG"


def _build_xls_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    import io

    import xlwt

    wb = xlwt.Workbook()
    sheet = wb.add_sheet("Sheet1")
    for col, header in enumerate(headers):
        sheet.write(0, col, header)
    for row_idx, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            sheet.write(row_idx, col, value)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xls_generic_parser_auto_detects_columns() -> None:
    file_bytes = _build_xls_bytes(
        ["Datum", "Betrag", "Empfaenger"],
        [["05.01.2026", -42.50, "Supermarkt Muster GmbH"]],
    )
    parser = GenericXlsParser()

    detection = parser.detect(file_bytes, "sample.xls")
    assert detection.matched

    rows = parser.parse(file_bytes)
    assert len(rows) == 1
    assert rows[0].booked_date == date(2026, 1, 5)
    assert rows[0].amount_minor == -4250
    assert rows[0].payee_raw == "Supermarkt Muster GmbH"


def test_xls_generic_parser_with_explicit_mapping_profile() -> None:
    file_bytes = _build_xls_bytes(
        ["Wertstellung", "Zahlungsbetrag", "Auftraggeber"],
        [["10.01.2026", -55.30, "Beispielhaendler AG"]],
    )
    mapping = {"booked_date": "Wertstellung", "amount": "Zahlungsbetrag", "payee": "Auftraggeber"}
    rows = GenericXlsParser().parse_with_mapping(file_bytes, mapping)
    assert len(rows) == 1
    assert rows[0].booked_date == date(2026, 1, 10)
    assert rows[0].amount_minor == -5530
    assert rows[0].payee_raw == "Beispielhaendler AG"


def test_xlsx_generic_parser_finds_header_row_after_preamble() -> None:
    file_bytes = _read("easybank_sample.xlsx")
    parser = GenericXlsxParser()

    detection = parser.detect(file_bytes, "easybank_sample.xlsx")
    assert detection.matched

    rows = parser.parse(file_bytes)
    assert len(rows) == 2
    assert rows[0].booked_date == date(2026, 1, 10)
    assert rows[0].amount_minor == -5530
    assert rows[0].payee_raw == "Beispielhaendler AG"


def test_xlsx_credit_card_export_maps_merchant_reference_and_status() -> None:
    """Kreditkartenexporte fuehren weder 'Empfaenger' noch 'Verwendungszweck'.

    Der Haendler steht in 'Beschreibung', die Belegnummer in
    'Referenznummer'. Ohne diese Aliase blieb eine komplette Kartendatei ohne
    Empfaenger und ohne Referenz - Empfaengerverknuepfung, Regeln und
    Vertragsabgleich liefen fuer diese Buchungen ins Leere."""
    rows = GenericXlsxParser().parse(_read("kreditkarte_sample.xlsx"))

    # Die vorgemerkte Zeile fehlt: eine Autorisierung ist nicht endgueltig,
    # beim Abrechnen aendern sich Betrag und Datum noch. 'noch nicht
    # abgerechnet' bleibt dagegen erhalten.
    assert len(rows) == 2
    assert rows[0].payee_raw == "Musterabo"
    assert rows[0].booked_date == date(2026, 7, 30)
    assert rows[0].amount_minor == -21420
    assert rows[0].external_reference == "REF00000000000000002"
    assert rows[1].amount_minor == 73581

    # Die doppelte Ueberschrift 'Buchungsdatum' verschluckt in raw_data nicht
    # mehr die erste Spalte.
    assert rows[0].raw["Buchungsdatum"] == "30.07.2026"
    assert rows[0].raw["Buchungsdatum (2)"] == "31.07.2026"

    # Der Haendler steht nicht zusaetzlich wortgleich im Zweck.
    assert rows[0].purpose != rows[0].payee_raw


@pytest.mark.parametrize(
    ("filename", "expected_format"),
    [
        ("csv_generic_sample.csv", "csv_generic"),
        ("paypal_sample.csv", "paypal_csv"),
        ("mt940_sample.mta", "mt940"),
        ("camt_sample.xml", "camt"),
        ("easybank_sample.xlsx", "xlsx_generic"),
        ("kreditkarte_sample.xlsx", "xlsx_generic"),
    ],
)
def test_registry_auto_detects_correct_format(filename: str, expected_format: str) -> None:
    file_bytes = _read(filename)
    result = detect_format(file_bytes, filename)
    assert result.matched
    assert result.format == expected_format


def test_registry_contains_all_expected_parsers() -> None:
    formats = {p.format_name for p in PARSERS}
    assert formats == {
        "csv_generic",
        "xlsx_generic",
        "xls_generic",
        "mt940",
        "camt",
        "paypal_csv",
    }
