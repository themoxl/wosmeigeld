import re

# Gehalten identisch zu services/api/app/core/log_redaction.py, siehe dort
# fuer die Begruendung (Architekturplan Abschnitt 16, docs/SICHERHEIT.md
# Punkt 7). Bewusst dupliziert statt als gemeinsames Package - kleiner,
# stabiler Code, der sich selten aendert.

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_LONG_DIGIT_RUN_RE = re.compile(r"\b\d{8,20}\b")

_SENSITIVE_KEYS = {
    "iban",
    "account_number",
    "kontonummer",
    "purpose",
    "verwendungszweck",
    "payee_raw",
    "raw_data",
}


def redact_text(text: str) -> str:
    text = _IBAN_RE.sub("***IBAN***", text)
    text = _LONG_DIGIT_RUN_RE.sub("***NUMMER***", text)
    return text


def redact_extra(extra: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in extra.items():
        if key.lower() in _SENSITIVE_KEYS:
            redacted[key] = "***REDACTED***"
        elif isinstance(value, str):
            redacted[key] = redact_text(value)
        else:
            redacted[key] = value
    return redacted
