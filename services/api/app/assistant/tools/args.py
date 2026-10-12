"""Argumentpruefung der Werkzeuge des KI-Felds.

Das Modell liefert die Argumente als JSON. Was nicht passt, wird weder
geraten noch still ersetzt: ToolRejected traegt einen Satz, was das Modell
aendern muss, und geht als Werkzeugergebnis (status rejected) zurueck.
Unbekannte Argumentnamen werden ebenfalls abgelehnt - ein vertippter
Monatsname haette sonst still den Standardwert ergeben und eine falsche
Antwort mit richtig aussehenden Zahlen.
"""

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from app.assistant.tools.base import ToolArgs, ToolRejected

_INTEGER_TEXT = re.compile(r"[+-]?\d+")


def shown(value: Any) -> str:
    """Ein Argumentwert so, wie er in der Ablehnung zitiert wird."""
    return json.dumps(value, ensure_ascii=False, default=str)


def check_keys(args: ToolArgs, allowed: Iterable[str]) -> None:
    """Lehnt Argumente ab, die das Werkzeug nicht kennt."""
    known = list(allowed)
    unknown = sorted(key for key in args if key not in known)
    if unknown:
        listed = ", ".join(known) if known else "keine"
        raise ToolRejected(
            f"Unbekannte Argumente: {', '.join(unknown)}. Erlaubt sind: {listed}."
        )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()):
        return int(value.strip())
    return None


def _bounds(minimum: int | None, maximum: int | None) -> str:
    if minimum is not None and maximum is not None:
        return f" zwischen {minimum} und {maximum}"
    if minimum is not None:
        return f" ab {minimum}"
    if maximum is not None:
        return f" bis {maximum}"
    return ""


def optional_int_arg(
    args: ToolArgs,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Ganze Zahl oder None (nicht angegeben bzw. null)."""
    value = args.get(name)
    if value is None:
        return None
    number = _as_int(value)
    if (
        number is None
        or (minimum is not None and number < minimum)
        or (maximum is not None and number > maximum)
    ):
        raise ToolRejected(
            f"'{name}' muss eine ganze Zahl{_bounds(minimum, maximum)} sein, "
            f"nicht {shown(value)}."
        )
    return number


def int_arg(
    args: ToolArgs,
    name: str,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    number = optional_int_arg(args, name, minimum=minimum, maximum=maximum)
    return default if number is None else number


def bool_arg(args: ToolArgs, name: str, *, default: bool) -> bool:
    value = args.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ToolRejected(f"'{name}' muss true oder false sein, nicht {shown(value)}.")


def text_arg(args: ToolArgs, name: str) -> str | None:
    """Text oder None; leerer Text gilt als nicht angegeben."""
    value = args.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolRejected(f"'{name}' muss ein Text sein, nicht {shown(value)}.")
    return value.strip() or None


def choice_arg(args: ToolArgs, name: str, choices: Sequence[str]) -> str | None:
    """Einer der erlaubten Werte oder None."""
    value = text_arg(args, name)
    if value is None:
        return None
    if value not in choices:
        raise ToolRejected(
            f"'{name}' muss einer dieser Werte sein: {', '.join(choices)} - "
            f"nicht {shown(value)}."
        )
    return value


def uuid_arg(args: ToolArgs, name: str, *, source: str) -> UUID:
    """Pflicht-ID. `source` nennt das Werkzeug, aus dem gueltige IDs stammen."""
    value = args.get(name)
    if value is None:
        raise ToolRejected(f"'{name}' fehlt. Gültige IDs liefert {source}.")
    try:
        return UUID(str(value).strip())
    except ValueError:
        raise ToolRejected(
            f"'{name}' ist keine gültige ID (UUID): {shown(value)}. "
            f"Gültige IDs liefert {source}."
        ) from None
