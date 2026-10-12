"""Siehe services/api/tests/test_log_redaction.py fuer die Begruendung."""

import json
import logging

from worker.log_redaction import redact_extra, redact_text
from worker.logging_config import JsonFormatter


def test_redact_text_masks_iban():
    result = redact_text("Ueberweisung von DE89370400440532013000 eingegangen")

    assert "DE89370400440532013000" not in result
    assert "***IBAN***" in result


def test_redact_extra_replaces_sensitive_keys_wholesale():
    result = redact_extra({"raw_data": '{"iban": "DE89370400440532013000"}', "row_number": 3})

    assert result["raw_data"] == "***REDACTED***"
    assert result["row_number"] == 3


def test_json_formatter_never_leaks_iban_and_includes_job_id():
    from worker.job_context import job_id_var

    token = job_id_var.set("11111111-1111-1111-1111-111111111111")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Import fehlgeschlagen: IBAN DE89370400440532013000 ungueltig",
            args=(),
            exc_info=None,
        )

        payload = json.loads(JsonFormatter().format(record))
    finally:
        job_id_var.reset(token)

    assert "DE89370400440532013000" not in json.dumps(payload)
    assert payload["job_id"] == "11111111-1111-1111-1111-111111111111"
