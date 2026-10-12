"""PayPal-CSV-Export (deutsche Kontoauszuege). Komma-getrennt, BOM,
deutsches Datumsformat, "Brutto" als Buchungsbetrag, "Transaktionscode" als
externe Referenz. Format anhand einer echten Referenz-Exportdatei ausserhalb
dieses Repositorys untersucht (nur Spaltenstruktur, keine echten Werte)."""

import csv
import io
from datetime import date, datetime

from worker.imports.canonical import (
    CanonicalTransaction,
    ImportParseError,
    parse_amount_to_minor_units,
)
from worker.imports.parsers.base import DetectionResult

_REQUIRED_COLUMNS = {"Datum", "Brutto", "Transaktionscode", "Währung"}


def _decode(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


def _parse_date(raw: str) -> date:
    try:
        return datetime.strptime(raw.strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise ImportParseError(f"unbekanntes PayPal-Datumsformat: {raw!r}") from exc


class PayPalCsvParser:
    format_name = "paypal_csv"

    def detect(self, file_bytes: bytes, filename: str) -> DetectionResult:
        if not filename.lower().endswith(".csv"):
            return DetectionResult(False, self.format_name, 0.0, "keine .csv-Endung")
        text = _decode(file_bytes)
        reader = csv.reader(io.StringIO(text))
        try:
            headers = set(next(reader))
        except StopIteration:
            return DetectionResult(False, self.format_name, 0.0, "leere Datei")
        if _REQUIRED_COLUMNS.issubset(headers):
            return DetectionResult(True, self.format_name, 0.95, "PayPal-Spaltenset erkannt")
        return DetectionResult(False, self.format_name, 0.0, "PayPal-Spalten fehlen")

    def parse(self, file_bytes: bytes) -> list[CanonicalTransaction]:
        text = _decode(file_bytes)
        reader = csv.DictReader(io.StringIO(text))
        results = []
        for row in reader:
            booked_date = _parse_date(row["Datum"])
            amount_minor = parse_amount_to_minor_units(row["Brutto"])
            currency = (row.get("Währung") or "EUR").strip() or "EUR"
            payee = (row.get("Name") or "").strip() or None
            purpose = (row.get("Betreff") or row.get("Artikelbezeichnung") or "").strip() or None
            reference = (row.get("Transaktionscode") or "").strip() or None
            results.append(
                CanonicalTransaction(
                    booked_date=booked_date,
                    amount_minor=amount_minor,
                    currency=currency,
                    payee_raw=payee,
                    purpose=purpose,
                    external_reference=reference,
                    bank_transaction_code=(row.get("Typ") or "").strip() or None,
                    raw=dict(row),
                )
            )
        return results
