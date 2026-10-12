"""Steht jede Zahl einer Antwort in dem, was das Modell gesehen hat?

Port von sia_public/src/core/ki_zahlenpruefung.js.

Quellen sind die Werkzeugergebnisse des Laufs (im Original, auch wenn sie fuer
das Modell verdichtet wurden) und seine Eingaben (Auftrag, Frage, Verlauf).
Eine belegte Zahl nennt ihre Quellen (etwa "W3"), eine unbelegte wird markiert
- nicht entfernt: in freier Erklaerung wuerde Entfernen den Zusammenhang
zerreissen.

* Ein Punkt ist nicht pauschal Tausendertrenner: "412.330" (deutsch) und
  "1234.56" (JSON) werden beide gelesen; bei echter Mehrdeutigkeit zaehlen
  beide Lesarten.
* Keine Toleranz: belegt ist eine Zahl, wenn eine Quelle - auf die Stellen
  gerundet, die die Antwort zeigt - genau diesen Wert hat ("2,1" deckt 2,13,
  aber nicht 2,2).
* Prozent und Groessenwoerter ("Mio.", "Tsd.") werden umgerechnet, das
  Vorzeichen darf fehlen.
* Nicht geprueft: ganze Zahlen bis 10 (Aufzaehlungen, "zwei Konten"),
  Datums- und Uhrzeitangaben sowie Listennummern am Zeilenanfang.

Positionen sind Python-Zeichenindizes (Unicode-Codepunkte); die Oberflaeche
rechnet sie auf UTF-16 um. Reine Funktionen ohne Netz und DB.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# fest: ganze Zahlen bis hierher sind Aufzaehlungen, keine Betraege (Vertrag der Zahlenpruefung)
SMALL = 10

_DATE = re.compile(
    r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b"
    r"|\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)
# Eine Zahl der Antwort: nicht mitten in einem Wort, einer Kennung (W3), einer
# Spanne (2024-2025) oder einem Pfad.
_NUMBER = re.compile(r"(?<![\w\-/:.,])[-−]?\d(?:[\d.,]*\d)?(?!\w)")
_SOURCE_NUMBER = re.compile(r"[-−]?\d+(?:[.,]\d+)*")
_DIGITS = re.compile(r"\d+")
_GROUPED_COMMA = re.compile(r"^\d{1,3}(,\d{3})+$")
_GROUPED_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")
_PERCENT = re.compile(r"^\s*(%|Prozent)", re.IGNORECASE)
_MAGNITUDES: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"^\s*(Mrd\.?|Milliarden?)", re.IGNORECASE), 1e9),
    (re.compile(r"^\s*(Mio\.?|Millionen?)", re.IGNORECASE), 1e6),
    (re.compile(r"^\s*(Tsd\.?|Tausend|T€|k\b)", re.IGNORECASE), 1e3),
)
_LIST_START = re.compile(r"(^|\n)\s*$")
_LIST_END = re.compile(r"^[.)]")
# fest: so weit hinter einer Zahl wird nach "%"/"Mio." gesucht (Lesart, kein Promptmaterial)
_LOOKAHEAD = 14
# fest: laengere Ziffernfolgen sind Kennungen, keine Werte (JS-Zahlgenauigkeit des Originals)
_LONGEST_DIGITS = 15


@dataclass(frozen=True)
class Reading:
    value: float
    places: int


def readings(raw: str) -> list[Reading]:
    """Alle Lesarten einer Zahl: deutsch (1.234,56), englisch/JSON
    (1,234.56 bzw. 1234.56), bei Mehrdeutigkeit beide."""
    text = raw.replace("−", "-")
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    out: list[Reading] = []

    def places(value: str, separator: str) -> int:
        index = value.rfind(separator)
        return len(value) - index - 1 if index >= 0 else 0

    has_dot = "." in text
    has_comma = "," in text
    try:
        if has_dot and has_comma:
            if text.rfind(",") > text.rfind("."):
                out.append(
                    Reading(float(text.replace(".", "").replace(",", ".")), places(text, ","))
                )
            else:
                out.append(Reading(float(text.replace(",", "")), places(text, ".")))
        elif has_comma:
            if text.count(",") == 1:
                out.append(Reading(float(text.replace(",", ".")), places(text, ",")))
            if _GROUPED_COMMA.match(text):
                out.append(Reading(float(text.replace(",", "")), 0))
        elif has_dot:
            if _GROUPED_DOT.match(text):
                out.append(Reading(float(text.replace(".", "")), 0))
            if text.count(".") == 1:
                out.append(Reading(float(text), places(text, ".")))
        else:
            out.append(Reading(float(text), 0))
    except ValueError:
        return []
    return [
        Reading(-reading.value if negative else reading.value, reading.places)
        for reading in out
        if math.isfinite(reading.value)
    ]


def source_values(text: str | None) -> list[float]:
    """Alle Zahlen einer Quelle: jede Lesart jedes Zahlworts, dazu jede
    Ziffernfolge fuer sich (Jahre in Datumsangaben, Belegnummern). In JSON
    steht vor jeder Zahl ein Doppelpunkt - hier gibt es deshalb keine
    Ausschluesse: was das Modell gesehen hat, zaehlt vollstaendig."""
    values: list[float] = []
    raw = text or ""
    for match in _SOURCE_NUMBER.findall(raw):
        values.extend(reading.value for reading in readings(match))
    for digits in _DIGITS.findall(raw):
        if len(digits) <= _LONGEST_DIGITS:
            values.append(float(digits))
    return values


def _round(value: float, places: int) -> float:
    """Kaufmaennisch wie Math.round im Original (nicht Bankers' Rounding)."""
    factor = 10.0**places
    return math.floor(value * factor + 0.5) / factor


def _equal(left: float, right: float) -> bool:
    return abs(left - right) < 1e-9 * max(1.0, abs(left))


def covers(answer: float, places: int, source: float, scales: Sequence[float]) -> bool:
    for scale in scales:
        quotient = source / scale
        if _equal(_round(quotient, places), answer) or _equal(
            _round(abs(quotient), places), abs(answer)
        ):
            return True
    return False


@dataclass(frozen=True)
class Source:
    """Eine Quelle: id erscheint als Beleg ("W3", "Eingabe")."""

    id: str
    text: str
    kind: Literal["werkzeug", "eingabe"] = "werkzeug"


@dataclass(frozen=True)
class NumberItem:
    start: int
    end: int
    text: str
    status: Literal["belegt", "unbelegt"]
    sources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "status": self.status,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class NumberCheck:
    items: list[NumberItem] = field(default_factory=list)
    # ganze Zahlen bis SMALL: gesehen, bewusst nicht geprueft
    small: int = 0

    @property
    def unverified(self) -> int:
        return sum(1 for item in self.items if item.status == "unbelegt")

    @property
    def verified(self) -> int:
        return sum(1 for item in self.items if item.status == "belegt")

    def as_dict(self) -> dict[str, Any]:
        """Vertragsform NumberCheck."""
        return {"items": [item.as_dict() for item in self.items], "unverified": self.unverified}


def check(text: str | None, sources: Sequence[Source]) -> NumberCheck:
    """Prueft jede Zahl von `text` gegen die Quellen."""
    answer = text or ""
    mask = _DATE.sub(lambda match: " " * len(match.group(0)), answer)
    prepared = [(source.id, source_values(source.text)) for source in sources]
    # Je Quelle, Stellenzahl und Skala die gerundeten Werte als Menge: eine
    # Antwort mit 50 Zahlen gegen Ergebnisse mit zehntausenden Werten bleibt
    # so eine Mengenabfrage statt eines Kreuzprodukts. Gleiche Rundung wie
    # covers().
    rounded: dict[tuple[int, int, float], tuple[set[float], set[float]]] = {}

    def index(position: int, places: int, scale: float) -> tuple[set[float], set[float]]:
        key = (position, places, scale)
        if key not in rounded:
            values = prepared[position][1]
            rounded[key] = (
                {_round(value / scale, places) for value in values},
                {_round(abs(value / scale), places) for value in values},
            )
        return rounded[key]

    items: list[NumberItem] = []
    small = 0
    for match in _NUMBER.finditer(mask):
        raw = match.group(0)
        start, end = match.start(), match.end()
        if _LIST_START.search(mask[:start]) and _LIST_END.match(mask[end:]):
            continue  # "1." / "2)" einer Aufzaehlung
        options = readings(raw)
        if not options:
            continue
        if all(option.places == 0 and abs(option.value) <= SMALL for option in options):
            small += 1
            continue
        after = mask[end : end + _LOOKAHEAD]
        scales: list[float] = [1.0]
        if _PERCENT.match(after):
            scales = [1.0, 0.01]  # 2,1 % <- 2.1 oder 0.021
        for pattern, factor in _MAGNITUDES:
            if pattern.match(after):
                scales = [factor]
        found: list[str] = []
        for position, (source_id, _values) in enumerate(prepared):
            if source_id in found:
                continue
            for option in options:
                hit = False
                for scale in scales:
                    signed, unsigned = index(position, option.places, scale)
                    if option.value in signed or abs(option.value) in unsigned:
                        hit = True
                        break
                if hit:
                    found.append(source_id)
                    break
        items.append(
            NumberItem(
                start=start,
                end=end,
                text=answer[start:end],
                status="belegt" if found else "unbelegt",
                sources=tuple(found),
            )
        )
    return NumberCheck(items=items, small=small)
