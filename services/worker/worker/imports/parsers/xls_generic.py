"""Generischer XLS (altes binaeres Excel-Format, OLE2/BIFF) Kontoauszug-Parser.

Getrennt von xlsx_generic.py, da xlrd>=2.0 ausschliesslich .xls liest (die
.xlsx-Unterstuetzung wurde in xlrd 2.0 entfernt) - openpyxl deckt weiterhin
.xlsx ab."""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import xlrd

from worker.imports.canonical import (
    CanonicalTransaction,
    ImportParseError,
    parse_amount_to_minor_units,
)
from worker.imports.parsers.base import DetectionResult
from worker.imports.parsers.csv_generic import (
    AMOUNT_ALIASES,
    DATE_ALIASES,
    DATE_FORMATS,
    PAYEE_ALIASES,
    PURPOSE_ALIASES,
    REFERENCE_ALIASES,
    STATUS_ALIASES,
    _find_column,
    is_pending_status,
)
from worker.imports.parsers.xlsx_generic import (
    HEADER_SCAN_ROWS,
    _trim_trailing_empty,
    _unique_headers,
)

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _raw_cell(value: object, ctype: int, datemode: int) -> str:
    """Zellwert als Text fuer import_rows.raw_data.

    Datumszellen werden ausdruecklich in ISO-Form gebracht: xlrd liefert sie
    als Seriennummer, str() daraus waere "45123.0". Beim erneuten Einlesen mit
    korrigierter Zuordnung liest der Parser nur noch raw_data - eine
    Seriennummer waere dort kein Datum mehr, das er zurueckuebersetzen kann."""
    if value is None or value == "":
        return ""
    if ctype == xlrd.XL_CELL_DATE:
        try:
            as_datetime: datetime = xlrd.xldate_as_datetime(
                float(value),  # type: ignore[arg-type]
                datemode,
            )
        except (ValueError, TypeError, OverflowError):
            return str(value)
        return as_datetime.isoformat()
    return str(value)


def _parse_cell_date(value: object, ctype: int, datemode: int) -> date:
    if ctype == xlrd.XL_CELL_DATE:
        as_datetime: datetime = xlrd.xldate_as_datetime(float(value), datemode)  # type: ignore[arg-type]
        return as_datetime.date()
    if isinstance(value, str):
        cleaned = value.strip()
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
    raise ImportParseError(f"unbekanntes Datumsformat in XLS: {value!r}")


def _extract_rows(
    sheet: Any,
    datemode: int,
    headers: list[str],
    start_row: int,
    date_idx: int,
    amount_idx: int,
    payee_idx: int | None,
    purpose_idx: int | None,
    ref_idx: int | None,
    status_idx: int | None = None,
) -> list[CanonicalTransaction]:
    # Siehe xlsx_generic: eine gemeinsame Beschreibungsspalte wird nicht
    # doppelt als Empfaenger UND Zweck uebernommen.
    if purpose_idx is not None and purpose_idx == payee_idx:
        purpose_idx = None

    unique_headers = _unique_headers(headers)
    results: list[CanonicalTransaction] = []
    for r in range(start_row, sheet.nrows):
        row_values: list[object] = sheet.row_values(r)
        row_types: list[int] = sheet.row_types(r)
        if not row_values or all(v == "" for v in row_values):
            continue
        if (
            status_idx is not None
            and status_idx < len(row_values)
            and is_pending_status(row_values[status_idx])
        ):
            continue
        if date_idx >= len(row_values) or amount_idx >= len(row_values):
            continue

        date_val = row_values[date_idx]
        amount_val = row_values[amount_idx]
        if date_val in (None, "") or amount_val in (None, ""):
            continue

        try:
            booked_date = _parse_cell_date(date_val, row_types[date_idx], datemode)
            amount_minor = parse_amount_to_minor_units(str(amount_val))
        except ImportParseError:
            continue

        payee = (
            str(row_values[payee_idx]).strip()
            if payee_idx is not None and payee_idx < len(row_values) and row_values[payee_idx]
            else None
        )
        purpose = (
            str(row_values[purpose_idx]).strip()
            if purpose_idx is not None
            and purpose_idx < len(row_values)
            and row_values[purpose_idx]
            else None
        )
        ref = (
            str(row_values[ref_idx]).strip()
            if ref_idx is not None and ref_idx < len(row_values) and row_values[ref_idx]
            else None
        )

        results.append(
            CanonicalTransaction(
                booked_date=booked_date,
                amount_minor=amount_minor,
                currency="EUR",
                payee_raw=payee,
                purpose=purpose,
                external_reference=ref,
                raw={
                    unique_headers[i]: str(row_values[i])
                    for i in range(min(len(unique_headers), len(row_values)))
                },
            )
        )

    return results


def _find_header_row(sheet: Any, max_rows: int | None = None) -> list[list[str]]:
    """Liefert alle Kandidaten-Kopfzeilen (als Liste von Strings je Zeile) bis
    `max_rows` bzw. bis zum Ende des Blatts."""
    limit = sheet.nrows if max_rows is None else min(max_rows, sheet.nrows)
    rows = []
    for r in range(limit):
        rows.append([str(c).strip() if c not in (None, "") else "" for c in sheet.row_values(r)])
    return rows


class GenericXlsParser:
    format_name = "xls_generic"

    def detect(self, file_bytes: bytes, filename: str) -> DetectionResult:
        if not filename.lower().endswith(".xls"):
            return DetectionResult(False, self.format_name, 0.0, "keine .xls-Endung")
        if not file_bytes.startswith(_OLE2_MAGIC):
            return DetectionResult(False, self.format_name, 0.0, "kein OLE2/XLS-Dateikopf")
        try:
            book = xlrd.open_workbook(file_contents=file_bytes)
            sheet = book.sheet_by_index(0)
            for candidate_headers in _find_header_row(sheet, max_rows=20):
                has_date = _find_column(candidate_headers, DATE_ALIASES) is not None
                has_amount = _find_column(candidate_headers, AMOUNT_ALIASES) is not None
                if has_date and has_amount:
                    return DetectionResult(True, self.format_name, 0.7, "XLS Datum/Betrag erkannt")
            return DetectionResult(
                True, self.format_name, 0.3, "XLS erkannt, Spalten-Mapping benoetigt"
            )
        except Exception as exc:
            return DetectionResult(False, self.format_name, 0.0, f"XLS-Ladefehler: {exc}")

    def parse(self, file_bytes: bytes) -> list[CanonicalTransaction]:
        book = xlrd.open_workbook(file_contents=file_bytes)
        sheet = book.sheet_by_index(0)

        headers: list[str] = []
        date_col: str | None = None
        amount_col: str | None = None
        start_row = 0

        for r, candidate_headers in enumerate(_find_header_row(sheet)):
            date_candidate = _find_column(candidate_headers, DATE_ALIASES)
            amount_candidate = _find_column(candidate_headers, AMOUNT_ALIASES)
            if date_candidate and amount_candidate:
                headers = candidate_headers
                date_col = date_candidate
                amount_col = amount_candidate
                start_row = r + 1
                break

        if not date_col or not amount_col:
            raise ImportParseError("Datums- oder Betragsspalte in XLS nicht erkannt.")

        date_idx = headers.index(date_col)
        amount_idx = headers.index(amount_col)

        payee_col = _find_column(headers, PAYEE_ALIASES)
        payee_idx = headers.index(payee_col) if payee_col else None

        purpose_col = _find_column(headers, PURPOSE_ALIASES)
        purpose_idx = headers.index(purpose_col) if purpose_col else None

        ref_col = _find_column(headers, REFERENCE_ALIASES)
        ref_idx = headers.index(ref_col) if ref_col else None

        status_col = _find_column(headers, STATUS_ALIASES)
        status_idx = headers.index(status_col) if status_col else None

        return _extract_rows(
            sheet, book.datemode, headers, start_row, date_idx, amount_idx, payee_idx,
            purpose_idx, ref_idx, status_idx,
        )

    def read_raw_rows(
        self, file_bytes: bytes, limit: int | None = None
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Kopfzeile und Rohzeilen, ohne irgendeine Spalte zu deuten (siehe
        GenericCsvParser.read_raw_rows). Kopfzeilensuche wie in
        GenericXlsxParser.read_raw_rows: erst die Zeile mit Datum UND Betrag,
        sonst die erste nicht leere Zeile."""
        book = xlrd.open_workbook(file_contents=file_bytes)
        sheet = book.sheet_by_index(0)

        candidates = _find_header_row(sheet, max_rows=HEADER_SCAN_ROWS)
        header_row = next(
            (
                r
                for r, candidate in enumerate(candidates)
                if _find_column(candidate, DATE_ALIASES)
                and _find_column(candidate, AMOUNT_ALIASES)
            ),
            None,
        )
        if header_row is None:
            header_row = next(
                (r for r, candidate in enumerate(candidates) if any(candidate)), None
            )
        if header_row is None:
            return [], []

        headers = _unique_headers(_trim_trailing_empty(candidates[header_row]))
        rows: list[dict[str, str]] = []
        for r in range(header_row + 1, sheet.nrows):
            if limit is not None and len(rows) >= limit:
                break
            values: list[object] = sheet.row_values(r)
            types: list[int] = sheet.row_types(r)
            if not values or all(v == "" for v in values):
                continue
            rows.append(
                {
                    headers[i]: _raw_cell(values[i], types[i], book.datemode)
                    for i in range(min(len(headers), len(values)))
                }
            )
        return headers, rows

    def parse_with_mapping(
        self, file_bytes: bytes, mapping: Mapping[str, str | None]
    ) -> list[CanonicalTransaction]:
        date_col = mapping.get("booked_date")
        amount_col = mapping.get("amount")
        if not date_col or not amount_col:
            raise ImportParseError("Mapping benoetigt mindestens Datum und Betrag.")

        book = xlrd.open_workbook(file_contents=file_bytes)
        sheet = book.sheet_by_index(0)

        headers: list[str] = []
        start_row = 0
        for r, candidate_headers in enumerate(_find_header_row(sheet)):
            if date_col in candidate_headers and amount_col in candidate_headers:
                headers = candidate_headers
                start_row = r + 1
                break

        if not headers:
            raise ImportParseError(
                f"Mapping-Spalten {date_col!r}/{amount_col!r} nicht in XLS gefunden."
            )

        date_idx = headers.index(date_col)
        amount_idx = headers.index(amount_col)

        payee_col = mapping.get("payee")
        payee_idx = headers.index(payee_col) if payee_col and payee_col in headers else None

        purpose_col = mapping.get("purpose")
        purpose_idx = (
            headers.index(purpose_col) if purpose_col and purpose_col in headers else None
        )

        ref_col = mapping.get("reference")
        ref_idx = headers.index(ref_col) if ref_col and ref_col in headers else None

        status_col = mapping.get("status") or _find_column(headers, STATUS_ALIASES)
        status_idx = headers.index(status_col) if status_col and status_col in headers else None

        return _extract_rows(
            sheet, book.datemode, headers, start_row, date_idx, amount_idx, payee_idx,
            purpose_idx, ref_idx, status_idx,
        )
