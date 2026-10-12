import json
import logging
import sys
from datetime import UTC, datetime

from worker.job_context import job_id_var
from worker.log_redaction import redact_extra, redact_text

# Gehalten identisch zu services/api/app/core/logging.py (Struktur), siehe
# dort fuer die ausfuehrliche Begruendung. Vorher hatte der Worker ueberhaupt
# keine strukturierten Logs (Dramatiqs Standardausgabe ist Klartext) - das
# ist der eigentliche Unterschied zur API-Variante, nicht nur ein Duplikat.

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

        job_id = job_id_var.get()
        if job_id:
            payload["job_id"] = job_id

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
