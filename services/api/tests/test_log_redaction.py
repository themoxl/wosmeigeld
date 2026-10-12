"""PII-Redaction in Logs (Architekturplan Abschnitt 16, docs/SICHERHEIT.md
Punkt 7). Reine Unit-Tests, keine echte DB/Supabase noetig."""

import json
import logging

from app.core.log_redaction import redact_extra, redact_text
from app.core.logging import JsonFormatter


def test_redact_text_masks_iban():
    text = "Ueberweisung von DE89370400440532013000 eingegangen"

    result = redact_text(text)

    assert "DE89370400440532013000" not in result
    assert "***IBAN***" in result


def test_redact_text_masks_long_digit_runs():
    result = redact_text("Kontonummer 12345678901234 gefunden")

    assert "12345678901234" not in result
    assert "***NUMMER***" in result


def test_redact_extra_replaces_sensitive_keys_wholesale():
    result = redact_extra({"iban": "DE89370400440532013000", "amount_minor": 1234})

    assert result["iban"] == "***REDACTED***"
    assert result["amount_minor"] == 1234


def test_json_formatter_never_leaks_iban_in_message_or_extra():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Fehler bei Zeile mit IBAN DE89370400440532013000",
        args=(),
        exc_info=None,
    )
    record.purpose = "Miete DE89370400440532013000"

    payload = json.loads(JsonFormatter().format(record))

    assert "DE89370400440532013000" not in json.dumps(payload)
    assert payload["purpose"] == "***REDACTED***"
