"""Zentrale Registry aller Import-Parser (Architekturplan Abschnitt 7.1)."""

from collections.abc import Mapping

from worker.imports.canonical import CanonicalTransaction, ImportParseError
from worker.imports.parsers.base import DetectionResult, ImportParser
from worker.imports.parsers.camt import CamtParser
from worker.imports.parsers.csv_generic import GenericCsvParser
from worker.imports.parsers.mt940 import Mt940Parser, StatementSummary
from worker.imports.parsers.paypal_csv import PayPalCsvParser
from worker.imports.parsers.xls_generic import GenericXlsParser
from worker.imports.parsers.xlsx_generic import GenericXlsxParser

PARSERS: list[ImportParser] = [
    PayPalCsvParser(),
    Mt940Parser(),
    CamtParser(),
    GenericXlsxParser(),
    GenericXlsParser(),
    GenericCsvParser(),  # Fallback zuletzt
]


def detect_format(file_bytes: bytes, filename: str) -> DetectionResult:
    """Ermittelt das am besten passende Format anhand von Dateiendung, Content-Type
    und Inhaltsanalyse."""
    best_parser: ImportParser = PARSERS[-1]
    best_result = DetectionResult(False, "unknown", 0.0)

    for parser in PARSERS:
        res = parser.detect(file_bytes, filename)
        if res.matched and res.confidence > best_result.confidence:
            best_parser = parser
            best_result = res
            if res.confidence >= 0.9:
                break

    return DetectionResult(
        matched=best_result.matched,
        format=best_parser.format_name,
        confidence=best_result.confidence,
        reason=best_result.reason,
    )


def parse_file(
    file_bytes: bytes,
    filename: str,
    forced_format: str | None = None,
    column_mapping: Mapping[str, str | None] | None = None,
) -> tuple[str, list[CanonicalTransaction]]:
    """Parst eine Datei mit automatischem oder erzwungenem Format. `column_mapping`
    (z. B. aus einem gespeicherten Mapping-Profil) wird nur beachtet, wenn
    `forced_format` gesetzt ist und der gewaehlte Parser eine `parse_with_mapping`-
    Methode anbietet (aktuell csv_generic und xlsx_generic)."""
    detected_format, transactions, _ = parse_file_with_summary(
        file_bytes, filename, forced_format, column_mapping
    )
    return detected_format, transactions


def parse_file_with_summary(
    file_bytes: bytes,
    filename: str,
    forced_format: str | None = None,
    column_mapping: Mapping[str, str | None] | None = None,
) -> tuple[str, list[CanonicalTransaction], StatementSummary | None]:
    """Wie `parse_file`, liefert zusaetzlich den Saldenrahmen des Auszugs.

    Den kennt bisher nur MT940 (Tags :60F:/:62F:). Fuer alle anderen Formate
    ist er None - CSV-, XLSX- und CAMT-Exporte fuehren keine Salden, die sich
    ohne Zusatzannahmen gegenrechnen liessen."""
    if forced_format:
        parser = next((p for p in PARSERS if p.format_name == forced_format), None)
        if not parser:
            raise ImportParseError(f"unbekanntes erzwungenes Format: {forced_format!r}")
        parse_with_mapping = getattr(parser, "parse_with_mapping", None)
        if column_mapping and parse_with_mapping:
            return parser.format_name, parse_with_mapping(file_bytes, column_mapping), None
        return (parser.format_name, *_parse_with_summary(parser, file_bytes))

    res = detect_format(file_bytes, filename)
    parser = next((p for p in PARSERS if p.format_name == res.format), PARSERS[-1])
    if not res.matched and res.confidence == 0.0:
        raise ImportParseError(
            f"Dateiformat von {filename!r} konnte nicht automatisch erkannt werden."
        )
    return (parser.format_name, *_parse_with_summary(parser, file_bytes))


def _parse_with_summary(
    parser: ImportParser, file_bytes: bytes
) -> tuple[list[CanonicalTransaction], StatementSummary | None]:
    parse_statement = getattr(parser, "parse_statement", None)
    if parse_statement is not None:
        transactions, summary = parse_statement(file_bytes)
        return transactions, summary
    return parser.parse(file_bytes), None
