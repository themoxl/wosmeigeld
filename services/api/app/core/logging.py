import json
import logging
import sys
from datetime import UTC, datetime

from app.core.log_redaction import redact_extra, redact_text
from app.core.request_context import request_id_var

# Strukturierte JSON-Logs mit Request-ID und PII-Redaction, siehe
# Architekturplan Abschnitt 16 (Observability) und docs/SICHERHEIT.md
# Punkt 7 (IBAN/Kontonummer/Verwendungszweck werden in Logs maskiert - seit
# Phase 3 existieren diese Felder tatsaechlich).

# Standard-Attribute eines logging.LogRecord (siehe Python-Doku) - alles
# darueber hinaus in record.__dict__ stammt aus logger.info(..., extra={...})
# und wird als strukturiertes Feld uebernommen (und redaktiert).
_STANDARD_LOG_RECORD_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_ATTRS
        }
        if extra:
            payload.update(redact_extra(extra))

        if record.exc_info:
            payload["exc_info"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
