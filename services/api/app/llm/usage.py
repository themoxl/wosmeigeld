"""Vom Server gemeldeter Tokenverbrauch eines Aufrufs (usage-Block).

Frueher wortgleich in app/services/ai_service.py und worker/ai/classifier.py;
jetzt einmal hier.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UsageTokens:
    """Vom Server gemeldeter Tokenverbrauch EINES Aufrufs.

    Jeder Wert ist einzeln optional. Fehlt er in der Antwort, bleibt er None
    und wird als NULL gespeichert - eine 0 waere eine Behauptung, die die
    Antwort nicht deckt, und wuerde jeden spaeteren Mittelwert verfaelschen."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None


def usage_int(value: Any) -> int | None:
    """Nur eine echte, nichtnegative Ganzzahl wird uebernommen.

    bool ist in Python ein int - ein versehentliches true wuerde sonst als
    'ein Token' in die Auswertung wandern. Ganzzahlige Gleitkommawerte (1234.0)
    werden akzeptiert, weil manche Server sie so serialisieren; alles andere
    (fehlend, Text, negativ) gilt als 'nicht gemeldet'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def extract_usage_tokens(payload: Any) -> UsageTokens:
    """Liest usage.prompt_tokens, usage.completion_tokens und
    usage.completion_tokens_details.reasoning_tokens aus einer Antwort.

    Es wird nichts geschaetzt und nichts aus einem anderen Feld hochgerechnet:
    was der Server nicht meldet, bleibt None."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return UsageTokens()
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    return UsageTokens(
        prompt_tokens=usage_int(usage.get("prompt_tokens")),
        completion_tokens=usage_int(usage.get("completion_tokens")),
        reasoning_tokens=usage_int(reasoning),
    )
