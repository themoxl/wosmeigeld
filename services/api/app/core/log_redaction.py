import re

# Minimale, bewusst grobe PII-Redaction fuer stdout-Logs (Architekturplan
# Abschnitt 16 "Observability", Sicherheitsmodell Punkt 7: IBAN/Kontonummer/
# Verwendungszweck werden in Logs maskiert). Kein DLP-System - ein paar
# false positives (z. B. eine grosse Centbetrag-Zahl) sind fuer eine
# Logzeile ein akzeptabler Trade-off gegenueber einem geleakten Kontodatum.

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_LONG_DIGIT_RUN_RE = re.compile(r"\b\d{8,20}\b")

# Feldnamen, deren Werte in strukturierten Logfeldern (extra=) unabhaengig
# vom Inhalt komplett ersetzt werden - u. a. die Importplattform-Felder aus
# Phase 3 (payee_raw, purpose, raw_data enthalten Rohdaten aus Bankdateien).
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
