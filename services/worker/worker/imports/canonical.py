from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


@dataclass
class CanonicalTransaction:
    """Kanonisches Zwischenformat aller Parser (Architekturplan Abschnitt 7.2)."""

    booked_date: date
    amount_minor: int
    currency: str
    value_date: date | None = None
    external_reference: str | None = None
    payee_raw: str | None = None
    purpose: str | None = None
    bank_transaction_code: str | None = None
    account_signature: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ImportParseError(ValueError):
    """Eine einzelne Zeile/ein Eintrag konnte nicht kanonisch geparst werden."""


def parse_amount_to_minor_units(raw: str) -> int:
    """Wandelt einen Betrags-String in Ganzzahl-Cent um (Architekturplan
    Abschnitt 2: "Geldwerte: Ganzzahlige Minor Units, niemals Float").

    Akzeptiert deutsches Format ("1.234,56", "-25,00", "25,00-") ebenso wie
    Punkt-Dezimalformat aus XML-Quellen ("123.45"). Nutzt Decimal statt
    float, um Rundungsfehler zu vermeiden.
    """
    s = raw.strip()
    if not s:
        raise ImportParseError("leerer Betrag")

    negative = False
    if s.startswith("-"):
        negative = True
        s = s[1:]
    elif s.endswith("-"):
        negative = True
        s = s[:-1]
    elif s.startswith("+"):
        s = s[1:]

    s = s.replace("€", "").replace(" ", "").strip()
    if "," in s:
        # Deutsches Format: Punkt = Tausendertrennzeichen, Komma = Dezimal.
        s = s.replace(".", "").replace(",", ".")

    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise ImportParseError(f"ungueltiger Betrag: {raw!r}") from exc

    minor = int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return -minor if negative else minor
