import csv
import io
from collections.abc import Mapping
from datetime import date, datetime

from worker.imports.canonical import (
    CanonicalTransaction,
    ImportParseError,
    parse_amount_to_minor_units,
)
from worker.imports.parsers.base import DetectionResult

DATE_ALIASES = [
    "datum",
    "buchungstag",
    "buchungsdatum",
    "date",
    "booking date",
    "valuta",
    "valutadatum",
    "wertstellung",
    "wertstellungsdatum",
    "umsatzdatum",
    "transaktionsdatum",
    "belegdatum",
]
AMOUNT_ALIASES = [
    "betrag",
    "betrag_eur",
    "amount",
    "umsatz",
    "wert",
    "betrag (eur)",
    "betrag eur",
    "betrag in eur",
    "buchungsbetrag",
    "umsatzbetrag",
    "soll/haben",
    "value",
]
# Reihenfolge ist Rangfolge: _find_column nimmt den ersten Treffer. Die
# Kreditkartenspalten stehen bewusst hinten - sie greifen nur, wenn der Export
# keine echte Empfaengerspalte fuehrt. Ohne sie blieb ein kompletter
# Kreditkartenexport ohne payee_raw, womit Empfaengerverknuepfung, Regeln und
# Vertragsabgleich fuer diese Buchungen wirkungslos waren.
PAYEE_ALIASES = [
    "empfaenger",
    "empfänger",
    "zahlungsempfänger",
    "name zahlungsbeteiligter",
    "zahlungspflichtiger",
    "begünstigter/zahlungspflichtiger",
    "empfänger/auftraggeber",
    "empfaenger/auftraggeber",
    "payee",
    "name",
    "empfaenger/zweck",
    "auftraggeber/empfänger",
    "beguenstigter/zahlungspflichtiger",
    "haendler",
    "händler",
    "merchant",
    "beschreibung",
    "description",
]
PURPOSE_ALIASES = [
    "verwendungszweck",
    "zweck",
    "buchungstext",
    "purpose",
    "notiz",
    "details",
    "beschreibung",
]
REFERENCE_ALIASES = [
    "referenz",
    "referenznummer",
    "transaktionscode",
    "transaktionsnummer",
    "reference",
    "buchungsreferenz",
    "belegnummer",
    "auftragsnummer",
    "id",
]
# Spalte, die den Verarbeitungsstand einer Kartenbuchung fuehrt.
STATUS_ALIASES = ["status", "buchungsstatus", "zustand", "state"]
DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"]

# Nur vorgemerkte Umsaetze werden uebersprungen: eine Autorisierung ist noch
# nicht endgueltig, Betrag und Datum aendern sich beim Abrechnen noch. Beim
# naechsten Import kaeme dieselbe Zahlung mit anderen Werten erneut herein und
# stuende doppelt im Konto.
#
# 'noch nicht abgerechnet' ist ausdruecklich KEIN solcher Fall - dort steht der
# Umsatz fest, er ist nur noch nicht in einer Monatsabrechnung enthalten.
_PENDING_STATUS_TOKENS: frozenset[str] = frozenset(
    {"vorgemerkt", "vormerkung", "reserviert", "pending", "autorisiert", "authorized"}
)


def is_pending_status(value: object) -> bool:
    """Ist dieser Statuswert eine noch nicht endgueltige Vormerkung?"""
    if value is None:
        return False
    lowered = str(value).strip().casefold()
    return any(token in lowered for token in _PENDING_STATUS_TOKENS)


class SimpleDialect(csv.Dialect):
    delimiter = ";"
    quotechar = '"'
    doublequote = True
    skipinitialspace = False
    lineterminator = "\r\n"
    quoting = csv.QUOTE_MINIMAL


def _find_column(headers: list[str], aliases: list[str]) -> str | None:
    normalized = {h.strip().lower(): h for h in headers}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def _parse_date(raw: str) -> date:
    cleaned = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ImportParseError(f"unbekanntes Datumsformat: {raw!r}")


def parse_raw_date(raw: str) -> date:
    """Datum aus einem Rohwert, wie er in import_rows.raw_data steht.

    Zusaetzlich zu den CSV-Mustern wird das ISO-Format akzeptiert: der
    XLSX-Parser legt echte Zellwerte als str(datetime) ab, also
    "2026-08-01 00:00:00". Beim erneuten Einlesen aus raw_data (Reparse) ist
    das die haeufigste Form ueberhaupt - ohne diesen Zweig scheiterte die
    Zuordnungskorrektur bei genau den Dateien, fuer die es sie gibt."""
    cleaned = raw.strip()
    try:
        return datetime.fromisoformat(cleaned).date()
    except ValueError:
        return _parse_date(cleaned)


def decode_text(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


def sniff_dialect(text: str) -> type[csv.Dialect]:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        return SimpleDialect


def raw_text(value: object) -> str:
    """Zellwert so, wie er in import_rows.raw_data landen soll.

    Ein Rohwert bleibt Text - erst die Spaltenzuordnung entscheidet, was davon
    Datum und was Betrag ist. Die Liste faengt den restkey von csv.DictReader:
    hat eine Zeile mehr Felder als die Kopfzeile, stuende dort sonst die
    Python-Repraesentation einer Liste."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    return str(value)


class GenericCsvParser:
    format_name = "csv_generic"

    def detect(self, file_bytes: bytes, filename: str) -> DetectionResult:
        if not filename.lower().endswith((".csv", ".txt")):
            return DetectionResult(False, self.format_name, 0.0, "keine .csv/.txt-Endung")
        text = decode_text(file_bytes)
        dialect = sniff_dialect(text)
        reader = csv.reader(io.StringIO(text), dialect=dialect)
        try:
            headers = next(reader)
        except StopIteration:
            return DetectionResult(False, self.format_name, 0.0, "leere Datei")
        has_date = _find_column(headers, DATE_ALIASES) is not None
        has_amount = _find_column(headers, AMOUNT_ALIASES) is not None
        if has_date and has_amount:
            return DetectionResult(True, self.format_name, 0.6, "Datum-/Betragsspalte erkannt")
        return DetectionResult(
            True, self.format_name, 0.2, "CSV, Spalten aber nicht eindeutig - Mapping noetig"
        )

    def parse(self, file_bytes: bytes) -> list[CanonicalTransaction]:
        text = decode_text(file_bytes)
        dialect = sniff_dialect(text)
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        headers = reader.fieldnames or []
        mapping = {
            "booked_date": _find_column(list(headers), DATE_ALIASES),
            "amount": _find_column(list(headers), AMOUNT_ALIASES),
            "payee": _find_column(list(headers), PAYEE_ALIASES),
            "purpose": _find_column(list(headers), PURPOSE_ALIASES),
            "reference": _find_column(list(headers), REFERENCE_ALIASES),
            "status": _find_column(list(headers), STATUS_ALIASES),
        }
        if not mapping["booked_date"] or not mapping["amount"]:
            raise ImportParseError(
                "Datums- oder Betragsspalte konnte nicht automatisch erkannt werden - "
                "manuelles Mapping erforderlich (Importzentrum: Mapping-Assistent)."
            )
        return self.parse_with_mapping(file_bytes, mapping)

    def parse_with_mapping(
        self, file_bytes: bytes, mapping: Mapping[str, str | None]
    ) -> list[CanonicalTransaction]:
        text = decode_text(file_bytes)
        dialect = sniff_dialect(text)
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        return map_rows([dict(row) for row in reader], mapping)

    def read_raw_rows(
        self, file_bytes: bytes, limit: int | None = None
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Kopfzeile und Rohzeilen, ohne irgendeine Spalte zu deuten.

        Gebraucht wird das genau dann, wenn das Deuten scheitert: der Lauf
        endet dann nicht als Totalausfall, sondern mit den Spaltennamen der
        Datei in der Hand (worker/imports/processor.py, Status
        'needs_mapping'). Bewusst KEIN Teil des ImportParser-Protokolls - fuer
        mt940/camt gibt es keine Spalten, die man zuordnen koennte."""
        text = decode_text(file_bytes)
        dialect = sniff_dialect(text)
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        headers = [name for name in (reader.fieldnames or []) if name]
        rows: list[dict[str, str]] = []
        for row in reader:
            if limit is not None and len(rows) >= limit:
                break
            rows.append({name: raw_text(row.get(name)) for name in headers})
        return headers, rows


def map_rows(
    rows: list[dict[str, object]], mapping: Mapping[str, str | None]
) -> list[CanonicalTransaction]:
    """Zeilen (Spaltenname -> Rohwert) plus Zuordnung -> kanonische Buchungen.

    Bewusst getrennt vom Einlesen der Datei: dieselbe Logik wird zweimal
    gebraucht, einmal frisch beim Upload und einmal beim erneuten Einlesen mit
    korrigierter Zuordnung (POST /v1/imports/{id}/reparse). Fuer den zweiten
    Fall liegen die Rohzeilen laengst in import_rows.raw_data - die
    hochgeladene Datei muesste dafuer nirgends aufbewahrt werden, was einen
    Speicher voller unverschluesselter Kontoauszuege erspart."""
    date_col = mapping.get("booked_date")
    amount_col = mapping.get("amount")
    if not date_col or not amount_col:
        raise ImportParseError("Mapping benoetigt mindestens Datum und Betrag.")

    payee_col = mapping.get("payee")
    purpose_col = mapping.get("purpose")
    reference_col = mapping.get("reference")
    status_col = mapping.get("status")
    # Faellt beides auf dieselbe Spalte (Kartenexporte fuehren oft nur eine
    # Beschreibung), waere der Zweck eine wortgleiche Kopie des
    # Empfaengers - dann bleibt er leer.
    if purpose_col and purpose_col == payee_col:
        purpose_col = None

    results = []
    for row in rows:
        if status_col and is_pending_status(row.get(status_col)):
            continue
        missing = next((c for c in (date_col, amount_col) if c not in row), None)
        if missing is not None:
            raise ImportParseError(f"Die Spalte {missing!r} kommt in der Datei nicht vor.")
        booked_date = parse_raw_date(str(row[date_col]))
        amount_minor = parse_amount_to_minor_units(str(row[amount_col]))
        reference = str(row.get(reference_col) or "").strip() if reference_col else ""
        payee = (str(row.get(payee_col) or "").strip() or None) if payee_col else None
        purpose = (str(row.get(purpose_col) or "").strip() or None) if purpose_col else None
        # Zwei verschiedene Spalten mit demselben Text (der easybank-Export
        # fuehrt "Beschreibung" und "Details" wortgleich) sind derselbe Fall wie
        # eine einzige Beschreibungsspalte - der Gleichheitstest oben greift
        # dort nicht, weil die Spaltennamen sich unterscheiden.
        if purpose is not None and purpose == payee:
            purpose = None
        results.append(
            CanonicalTransaction(
                booked_date=booked_date,
                amount_minor=amount_minor,
                currency="EUR",
                payee_raw=payee,
                purpose=purpose,
                external_reference=reference or None,
                raw=dict(row),
            )
        )
    return results
