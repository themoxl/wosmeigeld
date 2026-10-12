"""Benannte Gruende des KI-Kerns (Reason-Codes) mit deutschem Klartext.

Jeder Fehlschlag und jede Auffaelligkeit eines Modellaufrufs bekommt einen
festen Schluessel, einen Satz fuer Menschen und - wo es eine gibt - die
zustaendige Einstellung. Die Oberflaeche gruppiert nach dem Schluessel und
zeigt den Satz; sie muss sich nie auf freien Text stuetzen.

Leitlinie (Daueregel des Besitzers): ein Fehlschlag wird benannt, nicht still
uebersteuert. Es gibt deshalb keinen Code fuer "automatisch anders
wiederholt" - der Kern wiederholt nur, wenn ein Aufruf mit korrigierter
Messung wirklich anders ausfallen kann (Fensterueberlauf, einmal).
"""

from dataclasses import dataclass
from typing import Final, Literal

Level = Literal["info", "warnung", "fehler"]

# Pfad der Einstellungen in der Oberflaeche - an einer Stelle, damit jeder
# Hinweis dieselbe Sprache spricht.
_SETTINGS: Final = "Einstellungen › KI"


@dataclass(frozen=True)
class Reason:
    code: str
    level: Level
    text: str
    # Zustaendige Einstellung oder Handlung; None, wenn es keine gibt.
    hint: str | None = None


AI_DISABLED: Final = "ai_disabled"
MODEL_UNAVAILABLE: Final = "model_unavailable"
MODEL_MISSING: Final = "model_missing"
MODEL_NOT_LOADED: Final = "model_not_loaded"
MODEL_CONTEXT_UNKNOWN: Final = "model_context_unknown"
MODEL_INPUT_TOO_LARGE: Final = "model_input_too_large"
MODEL_CONTEXT_OVERFLOW: Final = "model_context_overflow"
MODEL_BUDGET_EXHAUSTED: Final = "model_budget_exhausted"
MODEL_OUTPUT_UNUSABLE: Final = "model_output_unusable"
MODEL_RESERVE_TIGHT: Final = "model_reserve_tight"
MODEL_USAGE_UNAVAILABLE: Final = "model_usage_unavailable"
MODEL_REASONING_UNSUPPORTED: Final = "model_reasoning_unsupported"
MODEL_SCHEMA_UNSUPPORTED: Final = "model_schema_unsupported"
MODEL_TIMEOUT: Final = "model_timeout"
MODEL_HTTP_ERROR: Final = "model_http_error"
MODEL_CANCELLED: Final = "model_cancelled"
MODEL_SETTINGS_INVALID: Final = "model_settings_invalid"
MODEL_CAP_ABOVE_WINDOW: Final = "model_cap_above_window"
# Anbieter mit API-Schluessel (Etappe 3, app/llm/providers.py)
MODEL_NOT_CHOSEN: Final = "model_not_chosen"
MODEL_KEY_MISSING: Final = "model_key_missing"
MODEL_KEY_REJECTED: Final = "model_key_rejected"
MODEL_CREDIT_EXHAUSTED: Final = "model_credit_exhausted"
MODEL_RATE_LIMITED: Final = "model_rate_limited"
MODEL_REFUSED: Final = "model_refused"

REASONS: Final[dict[str, Reason]] = {
    reason.code: reason
    for reason in (
        Reason(
            AI_DISABLED,
            "fehler",
            "Die KI ist für diesen Haushalt ausgeschaltet.",
            f"{_SETTINGS} › KI-Unterstützung einschalten.",
        ),
        Reason(
            MODEL_UNAVAILABLE,
            "fehler",
            "Der Modellserver ist nicht erreichbar oder bietet kein chatfähiges Modell an.",
            f"LM Studio starten bzw. die Adresse unter {_SETTINGS} › Server prüfen; bei "
            "Claude die Internetverbindung des Servers prüfen.",
        ),
        Reason(
            MODEL_MISSING,
            "fehler",
            "Das eingestellte Modell ist auf dem Modellserver nicht vorhanden. Es wird "
            "bewusst kein anderes ersatzweise verwendet.",
            f"{_SETTINGS} › Modell: ein vorhandenes Modell wählen.",
        ),
        Reason(
            MODEL_NOT_LOADED,
            "warnung",
            "Das Modell ist auf dem Server nicht geladen; der erste Aufruf lädt es nach "
            "und kann dabei ins Zeitbudget laufen.",
            f"{_SETTINGS} › Modell laden.",
        ),
        Reason(
            MODEL_CONTEXT_UNKNOWN,
            "fehler",
            "Das Kontextfenster des Modells ist unbekannt - ohne Fenster lässt sich kein "
            "Antwortbudget berechnen, deshalb wird nicht gesendet.",
            f"Modell in LM Studio laden oder unter {_SETTINGS} › Kontext höchstens "
            "einen Wert eintragen.",
        ),
        Reason(
            MODEL_INPUT_TOO_LARGE,
            "fehler",
            "Die Eingabe passt samt Antwortreserve nicht in das Kontextfenster.",
            f"Modell mit größerem Fenster laden oder unter {_SETTINGS} › Antwortreserve "
            "verkleinern.",
        ),
        Reason(
            MODEL_CONTEXT_OVERFLOW,
            "fehler",
            "Der Modellserver meldet, dass das Kontextfenster überläuft.",
            f"Modell mit größerem Fenster laden oder unter {_SETTINGS} › Kontext höchstens "
            "den tatsächlich geladenen Wert eintragen.",
        ),
        Reason(
            MODEL_BUDGET_EXHAUSTED,
            "fehler",
            "Das Antwortbudget war aufgebraucht, bevor eine Antwort stand - meist hat "
            "das Denken (Reasoning) den freien Platz verbraucht.",
            f"Reasoning unter {_SETTINGS} ausschalten oder das Modell mit größerem "
            "Fenster laden.",
        ),
        Reason(
            MODEL_OUTPUT_UNUSABLE,
            "fehler",
            "Die Antwort des Modells war nicht verwertbar.",
            "Ein anderes Modell wählen, wenn das wiederholt auftritt.",
        ),
        Reason(
            MODEL_RESERVE_TIGHT,
            "info",
            "Die Antwort brauchte mehr als die Antwortreserve.",
            f"Bei vollerem Kontext wird früher verdichtet, wenn die Reserve unter "
            f"{_SETTINGS} › Antwortreserve größer ist.",
        ),
        Reason(
            MODEL_USAGE_UNAVAILABLE,
            "info",
            "Der Modellserver meldet keinen Tokenverbrauch - die Messung von Zeichen je "
            "Token bleibt für diesen Aufruf aus.",
            None,
        ),
        Reason(
            MODEL_REASONING_UNSUPPORTED,
            "warnung",
            "Das Modell bzw. der Server unterstützt die gewählte Reasoning-Einstellung nicht.",
            f"{_SETTINGS} › Reasoning auf „Modellstandard“ stellen.",
        ),
        Reason(
            MODEL_SCHEMA_UNSUPPORTED,
            "warnung",
            "Der Server lehnt die strukturierte Ausgabe (response_format/Werkzeuge) ab.",
            None,
        ),
        Reason(
            MODEL_TIMEOUT,
            "fehler",
            "Der Modellaufruf hat das Zeitbudget überschritten.",
            f"{_SETTINGS} › Zeitbudget je Anfrage erhöhen oder ein schnelleres Modell wählen.",
        ),
        Reason(
            MODEL_HTTP_ERROR,
            "fehler",
            "Der Modellserver hat den Aufruf mit einem Fehler beantwortet.",
            None,
        ),
        Reason(MODEL_CANCELLED, "info", "Der Modellaufruf wurde abgebrochen.", None),
        Reason(
            MODEL_SETTINGS_INVALID,
            "fehler",
            "Eine für das Antwortbudget nötige KI-Einstellung fehlt oder ist ungültig.",
            f"{_SETTINGS} › Kontext und Budget prüfen.",
        ),
        Reason(
            MODEL_CAP_ABOVE_WINDOW,
            "info",
            "„Kontext höchstens“ liegt über dem geladenen Fenster und wirkt deshalb nicht.",
            f"{_SETTINGS} › Kontext höchstens.",
        ),
        Reason(
            MODEL_NOT_CHOSEN,
            "fehler",
            "Für den gewählten Anbieter ist kein Modell ausgewählt. Bei einem Cloud-Anbieter "
            "wählt der Finanzplaner bewusst keines selbst.",
            f"{_SETTINGS} › Modell wählen.",
        ),
        Reason(
            MODEL_KEY_MISSING,
            "fehler",
            "Für den gewählten Anbieter ist kein lesbarer API-Schlüssel hinterlegt - es geht "
            "kein Aufruf hinaus.",
            f"{_SETTINGS} › API-Schlüssel hinterlegen (nur der Owner).",
        ),
        Reason(
            MODEL_KEY_REJECTED,
            "fehler",
            "Der Anbieter lehnt den API-Schlüssel ab (ungültig, widerrufen, ohne Berechtigung "
            "oder für diese Region gesperrt).",
            f"Neuen Schlüssel in der Anthropic-Konsole erzeugen und unter {_SETTINGS} › "
            "API-Schlüssel hinterlegen.",
        ),
        Reason(
            MODEL_CREDIT_EXHAUSTED,
            "fehler",
            "Das Guthaben beim Anbieter ist aufgebraucht.",
            "Guthaben in der Anthropic-Konsole prüfen oder unter "
            f"{_SETTINGS} auf den lokalen Anbieter wechseln.",
        ),
        Reason(
            MODEL_RATE_LIMITED,
            "warnung",
            "Der Anbieter hat die Anfrage wegen zu vieler Aufrufe (Ratenlimit) abgewiesen.",
            "Später erneut versuchen.",
        ),
        Reason(
            MODEL_REFUSED,
            "fehler",
            "Das Modell hat die Antwort verweigert (Sicherheitsfilter des Anbieters).",
            "Frage umformulieren oder ein anderes Modell wählen.",
        ),
    )
}


def describe(code: str, detail: str | None = None) -> str:
    """Klartext fuer die Oberflaeche: Satz, optional ein konkreter Befund,
    dann der Hinweis auf die zustaendige Einstellung.

    Unbekannte Codes (z. B. aeltere Bestandswerte) kommen unveraendert
    zurueck - lieber der Schluessel als ein erfundener Satz."""
    reason = REASONS.get(code)
    if reason is None:
        return f"{code}: {detail}" if detail else code
    parts = [reason.text]
    if detail:
        parts.append(detail)
    if reason.hint:
        parts.append(f"Abhilfe: {reason.hint}")
    return " ".join(parts)


def level_of(code: str) -> Level:
    reason = REASONS.get(code)
    return reason.level if reason is not None else "fehler"
