"""Erzeugt die XLSX-Fixtures neu (deterministisch, nur mit erfundenen Werten).
Nicht Teil der Test-Suite - einmalig ausfuehren, falls eine Fixture angepasst
werden soll:

    python packages/test-fixtures/imports/generate_xlsx_fixture.py
"""

from pathlib import Path

import openpyxl

HERE = Path(__file__).parent


def _write(sheet_rows: list[list[object]], filename: str) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    for row in sheet_rows:
        sheet.append(row)
    path = HERE / filename
    workbook.save(path)
    print(f"geschrieben: {path}")


# Einfacher Export mit sauberen, eindeutigen Spaltennamen. Ein paar
# Praeambel-/Metadatenzeilen vor der Kopfzeile, wie bei echten Exporten
# ueblich - testet, dass der Parser die Kopfzeile sucht statt eine feste
# Zeilennummer anzunehmen.
_write(
    [
        ["Kontoauszug (Beispiel)"],
        ["Erstellt am:", "31.07.2026"],
        [],
        ["Datum", "Betrag", "Empfaenger", "Verwendungszweck"],
        ["10.01.2026", -55.30, "Beispielhaendler AG", "Kartenzahlung"],
        ["11.01.2026", -8.90, "Cafe Musterstadt", "Kartenzahlung"],
    ],
    "easybank_sample.xlsx",
)

# Kreditkartenexport in der Form, die echte Anbieter liefern:
#   * keine Spalte heisst 'Empfaenger' - der Haendler steht in 'Beschreibung',
#   * 'Buchungsdatum' kommt zweimal vor (Umsatz- und Buchungstag),
#   * 'Referenznummer' traegt eine eindeutige Belegnummer,
#   * 'Status' unterscheidet endgueltige von vorgemerkten Umsaetzen. Eine
#     Vormerkung ist nur eine Autorisierung: Betrag und Datum aendern sich
#     beim Abrechnen noch, sie darf deshalb nicht als Buchung importiert
#     werden. 'noch nicht abgerechnet' ist dagegen endgueltig.
_write(
    [
        ["Transaktionsansicht"],
        ["Kontoname", "Musterbank Kreditkarte"],
        ["Kontotyp", "Kreditkarte"],
        [],
        [
            "Referenznummer",
            "Buchungsdatum",
            "Buchungsdatum",
            "Betrag",
            "Beschreibung",
            "Typ",
            "Status",
            "Originalbetrag",
            "Details",
        ],
        [
            "REF00000000000000001",
            "31.07.2026",
            "",
            "-6,52 €",
            "Musterdienst",
            "Belastung",
            "vorgemerkt",
            "-7,42 USD",
            "MUSTERDIENST INC.      MUSTERSTADT USA",
        ],
        [
            "REF00000000000000002",
            "30.07.2026",
            "31.07.2026",
            "-214,20 €",
            "Musterabo",
            "Belastung",
            "noch nicht abgerechnet",
            "-214,20 €",
            "MUSTERABO              MUSTERSTADT USA",
        ],
        [
            "REF00000000000000003",
            "18.07.2026",
            "18.07.2026",
            "+735,81 €",
            "Per Lastschrift dankend erhalten",
            "Gutschrift",
            "abgerechnet",
            "+735,81 €",
            "Per Lastschrift dankend erhalten",
        ],
    ],
    "kreditkarte_sample.xlsx",
)
