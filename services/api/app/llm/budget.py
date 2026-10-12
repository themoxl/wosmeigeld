"""Die Rechnung vor jedem Modellaufruf - reine Funktionen, ohne Netz und DB.

Port von sia_public/src/core/ki_budget.js. Begriffe:

  W  wirksames Fenster: das geladene, hoechstens "Kontext hoechstens"
  E  Eingabe in Token: EXAKT fuer den Teil, den das Modell im selben Lauf
     schon gesehen hat (usage.prompt_tokens des Vorgaengers, wenn dessen
     Nachrichtenliste ein Anfangsstueck der jetzigen ist), geschaetzt nur fuer
     das Neue - Zeichen / gemessene Zeichen je Token
  A  Schaetzabstand: gemessener Schaetzfehler des Modells, angewandt auf den
     geschaetzten Teil
  R  Antwortreserve: Anteil des Fensters (Einstellung answer_reserve_share)
  max_tokens = W_frei - E - A   (der ganze freie Platz), hoechstens die
               Antwortgrenze des Modells, wenn der Server eine nennt
  passt, wenn E + A <= W_frei - R; sonst muss verdichtet werden oder der
  Aufruf scheitert laut (model_input_too_large).

Hier steht keine Zahl, die eine Grenze setzt: Fenster, Verhaeltnis,
Schaetzfehler und Anteile kommen herein. Fehlt eines davon, wirft compute()
BudgetInputError - es gibt keinen Rueckfall.
"""

import base64
import binascii
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

Message = dict[str, Any]


class BudgetInputError(ValueError):
    """Eine Eingabe der Budgetrechnung fehlt oder ist ungueltig.

    `field` nennt die Einstellung bzw. Groesse, damit der Aufrufer sie im
    Klartext benennen kann (model_settings_invalid / model_context_unknown)."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field = field_name


@dataclass(frozen=True)
class BudgetParts:
    """Aufschluesselung von E in Token (Vertrag RunBudget.parts)."""

    system: int = 0
    history: int = 0
    tool_results: int = 0
    tool_defs: int = 0
    current: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "system": self.system,
            "history": self.history,
            "tool_results": self.tool_results,
            "tool_defs": self.tool_defs,
            "current": self.current,
        }


@dataclass(frozen=True)
class Basis:
    """Exakte Zaehlung des vorigen Aufrufs im selben Lauf.

    `messages` ist die Liste, wie sie gesendet wurde, `prompt_tokens` die vom
    Server gemeldete Eingabe. Ist sie ein Anfangsstueck der naechsten Liste
    und sind die Werkzeuge gleich, ist dieser Teil von E exakt bekannt."""

    messages: tuple[Message, ...]
    tools_signature: str
    prompt_tokens: int


@dataclass(frozen=True)
class Budget:
    """Ergebnis der Rechnung. Feldnamen wie im Vertrag RunBudget
    (docs/KI-FELD-SCHNITTSTELLE.md); as_run_budget() liefert genau diese Form."""

    W: int
    W_free: int
    E: int
    # Schaetzung der GESAMTEN Eingabe (auch wenn E exakt ist) - zum Vergleich.
    E_estimated: int
    # Der Teil von E, der geschaetzt ist; auf ihn wirkt A.
    estimated_part: int
    exact: bool
    A: int
    R: int
    max_tokens: int
    occupied_by_others: int
    fits: bool
    parts: BudgetParts = field(default_factory=BudgetParts)
    # Antwortgrenze des Modells, falls der Server eine nennt (Anthropic); dann
    # ist max_tokens hoechstens diese Zahl.
    output_limit: int | None = None
    chars: int = 0
    chars_per_token: float = 0.0
    estimate_error: float = 0.0

    def as_run_budget(self) -> dict[str, Any]:
        return {
            "W": self.W,
            "W_free": self.W_free,
            "E": self.E,
            "E_estimated": self.E_estimated,
            "exact": self.exact,
            "A": self.A,
            "R": self.R,
            "max_tokens": self.max_tokens,
            "parts": self.parts.as_dict(),
        }

    def describe(self) -> str:
        """Ein Satz fuer Meldungen: wie gross sind die Teile, was fehlt."""
        parts = self.parts
        return (
            f"Fenster {number_de(self.W)} Token, Eingabe {number_de(self.E)} "
            f"({'exakt' if self.exact else 'geschätzt'}: System {number_de(parts.system)}, "
            f"Verlauf {number_de(parts.history)}, "
            f"Werkzeugergebnisse {number_de(parts.tool_results)}, "
            f"Werkzeuge {number_de(parts.tool_defs)}, "
            f"aktuelle Frage {number_de(parts.current)}), "
            f"Schätzabstand {number_de(self.A)}, Antwortreserve {number_de(self.R)}."
        )


def number_de(value: int) -> str:
    """Ganzzahl mit deutschem Tausenderpunkt (32.768)."""
    return f"{value:,}".replace(",", ".")


def _content_chars(content: Any) -> int:
    """Zeichen eines content-Felds: Text direkt, Teile-Listen (OpenAI-Format
    mit {"type": "text", "text": ...}) als Summe ihrer Texte."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                total += len(str(part.get("text") or ""))
            else:
                total += len(str(part))
        return total
    return len(str(content))


def _image_tokens(messages: Sequence[Message]) -> int:
    """Bildflaeche zaehlt zum Fenster, niemals die Laenge der Base64-Zeichen.

    Konservative Patch-Schaetzung fuer lokale Bildmodelle. usage korrigiert
    den Abstand nach dem Aufruf; unbekannte Bildformate kosten ebenfalls Platz.
    """
    total = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_url = part.get("image_url") or {}
            url = image_url.get("url", "") if isinstance(image_url, dict) else ""
            width = height = 0
            if isinstance(url, str) and url.startswith("data:image/png;base64,"):
                try:
                    header = base64.b64decode(url.split(",", 1)[1][:44], validate=True)
                    if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
                        width = int.from_bytes(header[16:20], "big")
                        height = int.from_bytes(header[20:24], "big")
                except (ValueError, binascii.Error):
                    pass
            total += max(256, math.ceil(width / 28) * math.ceil(height / 28)) \
                if width and height else 2048
    return total


def message_chars(message: Message | None) -> int:
    """Zeichen, die eine Nachricht im Auftrag belegt. Rolle und Rahmen der
    Chatvorlage zaehlen grob mit; der gemessene Schaetzfehler faengt ab, wie
    weit das danebenliegt."""
    if not message:
        return 0
    count = _content_chars(message.get("content")) + len(str(message.get("role") or ""))
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        count += len(str(function.get("arguments") or ""))
        count += len(str(function.get("name") or ""))
        count += len(str(call.get("id") or ""))
    if message.get("tool_call_id"):
        count += len(str(message["tool_call_id"]))
    return count


def tools_json(tools: Sequence[dict[str, Any]] | None) -> str:
    """Werkzeugdefinitionen so kompakt serialisiert, wie sie gezaehlt werden."""
    if not tools:
        return ""
    return json.dumps(list(tools), ensure_ascii=False, separators=(",", ":"))


def tools_signature(tools: Sequence[dict[str, Any]] | None) -> str:
    """Kennung der angebotenen Werkzeuge: aendern sie sich, gilt die exakte
    Zaehlung des Vorgaengers nicht mehr."""
    return tools_json(tools)


def chars_of(messages: Sequence[Message], tools: Sequence[dict[str, Any]] | None = None) -> int:
    """Zeichen des ganzen Auftrags (Nachrichten plus Werkzeugdefinitionen)."""
    total = sum(message_chars(message) for message in messages)
    return total + len(tools_json(tools))


def tokens(chars: int, chars_per_token: float) -> int:
    if not chars_per_token > 0:
        raise BudgetInputError("chars_per_token", "Zeichen je Token fehlt.")
    return math.ceil(chars / chars_per_token)


def is_prefix(basis: Sequence[Message], messages: Sequence[Message]) -> bool:
    """Ist `basis` (die Liste des letzten Aufrufs) ein Anfangsstueck von
    `messages` - Nachricht fuer Nachricht gleich?"""
    if len(basis) > len(messages):
        return False
    return all(left == right for left, right in zip(basis, messages, strict=False))


def parts_of(
    messages: Sequence[Message],
    tools: Sequence[dict[str, Any]] | None,
    chars_per_token: float,
) -> BudgetParts:
    """Aufschluesselung in Token: System, Verlauf, Werkzeugergebnisse,
    Werkzeugdefinitionen, aktuelle Frage (die letzte Nutzernachricht)."""
    sums = {"system": 0, "history": 0, "tool_results": 0, "current": 0}
    last_user = -1
    for index, message in enumerate(messages):
        if message and message.get("role") == "user":
            last_user = index
    for index, message in enumerate(messages):
        if not message:
            continue
        size = message_chars(message) + round(_image_tokens([message]) * chars_per_token)
        if message.get("role") == "system":
            sums["system"] += size
        elif index == last_user:
            sums["current"] += size
        elif message.get("role") == "tool" or message.get("tool_calls"):
            sums["tool_results"] += size
        else:
            sums["history"] += size
    tool_defs = len(tools_json(tools))

    def as_tokens(value: int) -> int:
        return tokens(value, chars_per_token) if value else 0

    return BudgetParts(
        system=as_tokens(sums["system"]),
        history=as_tokens(sums["history"]),
        tool_results=as_tokens(sums["tool_results"]),
        tool_defs=as_tokens(tool_defs),
        current=as_tokens(sums["current"]),
    )


def compute(
    *,
    window: int | None,
    messages: Sequence[Message],
    chars_per_token: float | None,
    estimate_error: float | None,
    reserve_share: float | None,
    tools: Sequence[dict[str, Any]] | None = None,
    basis: Basis | None = None,
    occupied_by_others: int = 0,
    output_limit: int | None = None,
) -> Budget:
    """Das Budget eines Aufrufs. Wirft BudgetInputError, wenn W, das
    Verhaeltnis, der Schaetzfehler oder der Reserveanteil fehlen.

    `output_limit` ist die vom Server gemeldete Antwortgrenze des Modells -
    keine Zahl aus dem Code. Sie deckelt nur max_tokens; ob die Eingabe passt,
    entscheidet weiter die Reserve."""
    if window is None or window <= 0:
        raise BudgetInputError("window", "Das wirksame Kontextfenster fehlt.")
    if chars_per_token is None or not chars_per_token > 0:
        raise BudgetInputError("chars_per_token", "Zeichen je Token fehlt.")
    if estimate_error is None or estimate_error < 0:
        raise BudgetInputError("estimate_error", "Der Schätzabstand fehlt.")
    if reserve_share is None or not 0 < reserve_share < 1:
        raise BudgetInputError(
            "answer_reserve_share",
            "Die Antwortreserve muss ein Anteil zwischen 0 und 1 sein.",
        )
    chars = chars_of(messages, tools)
    image_estimate = _image_tokens(messages)
    full_estimate = tokens(chars, chars_per_token) + image_estimate
    chars += math.ceil(image_estimate * chars_per_token)
    used = full_estimate
    estimated_part = full_estimate
    exact = False
    if (
        basis is not None
        and basis.prompt_tokens > 0
        and basis.tools_signature == tools_signature(tools)
        and is_prefix(basis.messages, messages)
    ):
        new = list(messages[len(basis.messages) :])
        estimated_part = tokens(chars_of(new), chars_per_token) + _image_tokens(new) if new else 0
        used = basis.prompt_tokens + estimated_part
        exact = True
    allowance = math.ceil(estimated_part * estimate_error)
    others = max(0, occupied_by_others)
    free = window - others
    reserve = math.floor(window * reserve_share)
    max_tokens = math.floor(free - used - allowance)
    if output_limit is not None and output_limit > 0:
        max_tokens = min(max_tokens, output_limit)
    return Budget(
        W=window,
        W_free=free,
        E=used,
        E_estimated=full_estimate,
        estimated_part=estimated_part,
        exact=exact,
        A=allowance,
        R=reserve,
        max_tokens=max_tokens,
        occupied_by_others=others,
        fits=used + allowance <= free - reserve,
        parts=parts_of(messages, tools, chars_per_token),
        output_limit=output_limit if output_limit is not None and output_limit > 0 else None,
        chars=chars,
        chars_per_token=chars_per_token,
        estimate_error=estimate_error,
    )


def page_chars(window: int | None, page_share: float | None, chars_per_token: float | None) -> int:
    """Wie viele Zeichen eine Ergebnisseite haben darf: Anteil des Fensters,
    in Zeichen dieses Modells."""
    if window is None or window <= 0:
        raise BudgetInputError("window", "Das wirksame Kontextfenster fehlt.")
    if page_share is None or not 0 < page_share < 1:
        raise BudgetInputError(
            "result_page_share",
            "Der Anteil einer Ergebnisseite muss zwischen 0 und 1 liegen.",
        )
    if chars_per_token is None or not chars_per_token > 0:
        raise BudgetInputError("chars_per_token", "Zeichen je Token fehlt.")
    return math.floor(window * page_share * chars_per_token)


@dataclass(frozen=True)
class MeasurementSums:
    """Laufende Summen der Messung eines Modells (Spalten von
    ai_model_measurements). Verhaeltnis und Schaetzfehler sind daraus
    abgeleitet, nach Groesse gewichtet."""

    sum_chars: int = 0
    sum_prompt_tokens: int = 0
    sum_estimate_error: float = 0.0
    sum_checked_tokens: int = 0
    calls: int = 0
    largest_need_tokens: int | None = None

    @property
    def chars_per_token(self) -> float | None:
        return self.sum_chars / self.sum_prompt_tokens if self.sum_prompt_tokens > 0 else None

    @property
    def estimate_error(self) -> float | None:
        if self.sum_checked_tokens <= 0:
            return None
        return self.sum_estimate_error / self.sum_checked_tokens


def update_measurement(
    previous: MeasurementSums | None, chars: int, prompt_tokens: int, need_tokens: int = 0
) -> MeasurementSums:
    """Messung nach einem Aufruf fortschreiben (Port von messe()).

    `chars` sind die Zeichen des ganzen Auftrags, `prompt_tokens` die
    gemessene Eingabe, `need_tokens` die gemeldete Antwortlaenge. Der
    Schaetzfehler ist die Summe der Abweichungen der jeweils VORHERIGEN
    Schaetzung, geteilt durch die Summe der geprueften Token. Dieselbe Rechnung
    steht als Upsert in app/llm/measurements.py (in SQL, damit gleichzeitige
    Aufrufe keine Messung verlieren)."""
    sums = previous or MeasurementSums()
    sum_chars = sums.sum_chars
    sum_prompt = sums.sum_prompt_tokens
    sum_error = sums.sum_estimate_error
    sum_checked = sums.sum_checked_tokens
    calls = sums.calls
    if chars > 0 and prompt_tokens > 0:
        if sum_prompt > 0:
            estimate = chars / (sum_chars / sum_prompt)
            sum_error += abs(estimate - prompt_tokens)
            sum_checked += prompt_tokens
        sum_chars += chars
        sum_prompt += prompt_tokens
        calls += 1
    largest = sums.largest_need_tokens
    if need_tokens > 0:
        largest = max(largest or 0, need_tokens)
    return MeasurementSums(
        sum_chars=sum_chars,
        sum_prompt_tokens=sum_prompt,
        sum_estimate_error=sum_error,
        sum_checked_tokens=sum_checked,
        calls=calls,
        largest_need_tokens=largest,
    )
