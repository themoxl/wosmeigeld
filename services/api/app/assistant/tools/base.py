"""Grundlage der Werkzeuge des KI-Felds: nur lesend (E5).

Jedes Werkzeug ist ein ToolSpec mit OpenAI-Werkzeugdefinition und einem
Handler. Der Handler bekommt einen ToolContext mit einer Sitzung, die in
read_only_session() entsteht:

  1. SET TRANSACTION READ ONLY        - als ERSTE Anweisung der Transaktion;
                                         jeder Schreibversuch scheitert laut
  2. SET LOCAL ROLE authenticated +    - die RLS-Rechte des Verfassers, wie
     request.jwt.claims, app.household_id  app/auth/dependencies.py sie fuer
                                         jeden API-Request setzt
  3. statement_timeout                 - die verbleibende Laufzeit des Laufs

Der Handler liefert eine ToolReply: Kennzahlen (summary), eine Tabelle
(table) und/oder Text. Beträge kommen in Euro, nie in Minor Units:
records() wandelt `*_minor`-Felder in `*_eur` um und maskiert Text
(IBAN/Kontonummern) mit mask_pii_text. Eine Tabelle wird seitenweise
gezeigt (render_page): eine Seite fuellt hoechstens llm.page_chars(W,
result_page_share, Zeichen/Token); das Modell sieht immer die Gesamtzahl und
blaettert mit result_page weiter - kein Top-N, keine still abgeschnittene
Liste.

Freies SQL des Modells gibt es nicht: Werkzeuge rufen die bestehenden
Dienste der App auf.
"""

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.classification import mask_pii_text

ToolStatus = Literal["ok", "rejected", "error", "cancelled"]
ToolArgs = dict[str, Any]

_UUID_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# IBAN mit Leerzeichen ("DE12 3456 7890 ..."), die mask_pii_text nicht trifft.
_SPACED_IBAN = re.compile(r"\b([A-Z]{2}\d{2})(?:[ ]?[0-9A-Z]{4}){3,7}(?:[ ]?[0-9A-Z]{1,3})?\b")


class ToolRejected(Exception):
    """Ungueltige Argumente - geht als Werkzeugergebnis (status rejected) an
    das Modell zurueck, mit einem Satz, was es aendern muss."""


@dataclass(frozen=True)
class ToolTable:
    """Tabellarisches Ergebnis: Spaltennamen einmal, Zeilen als Listen."""

    columns: list[str]
    rows: list[list[Any]]

    @property
    def total(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class ToolReply:
    """Ergebnis eines Werkzeugs.

    summary  einzelne Kennzahlen (steht auf der ersten Seite)
    table    Zeilen (seitenweise)
    text     freier Text (z. B. Erklaerung, Feldkatalog)
    status   ok | rejected (Argumente ungueltig) | error"""

    status: ToolStatus = "ok"
    summary: dict[str, Any] | None = None
    table: ToolTable | None = None
    text: str | None = None


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    household_id: UUID
    user_id: UUID
    today: date


ToolHandler = Callable[[ToolContext, ToolArgs], Awaitable[ToolReply]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler | None = None
    # False: braucht keine Datenbank (z. B. Feldkatalog)
    needs_db: bool = True

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def object_schema(
    properties: dict[str, Any] | None = None, required: Sequence[str] = ()
) -> dict[str, Any]:
    """JSON-Schema eines Argumentobjekts."""
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = list(required)
    return schema


# ---------------------------------------------------------------- Werte


def mask_text(value: str) -> str:
    """IBAN/Kontonummern maskieren (mask_pii_text plus IBAN mit Leerzeichen)."""
    return mask_pii_text(_SPACED_IBAN.sub(lambda match: f"{match.group(1)} **** [MASKED]", value))


def euro(minor: int | None) -> float | None:
    """Minor Units -> Euro (zwei Nachkommastellen)."""
    if minor is None:
        return None
    return round(int(minor) / 100, 2)


def plain(value: Any) -> Any:
    """Ein Wert so, wie er an das Modell geht: JSON-faehig, Text maskiert,
    `*_minor` als `*_eur` (rekursiv in Objekten und Listen)."""
    if isinstance(value, BaseModel):
        return plain(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name.endswith("_minor") and (item is None or isinstance(item, int | Decimal)):
                out[name.removesuffix("_minor") + "_eur"] = euro(
                    int(item) if item is not None else None
                )
            elif name.endswith("_minor") and isinstance(item, Mapping | list):
                out[name.removesuffix("_minor") + "_eur"] = _euro_nested(item)
            else:
                out[name] = plain(item)
        return out
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return plain(value.value)
    if isinstance(value, str):
        # Eine Kennung (UUID) ist keine Kontonummer - mask_pii_text haelt
        # einen 12-stelligen Ziffernblock sonst fuer eine.
        return value if _UUID_TEXT.fullmatch(value) else mask_text(value)
    return mask_text(str(value))


def _euro_nested(value: Any) -> Any:
    """Werte eines `*_minor`-Felds, das selbst ein Objekt/eine Liste ist
    (z. B. dict[str, int] je Klasse)."""
    if isinstance(value, Mapping):
        return {str(key): _euro_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_euro_nested(item) for item in value]
    if isinstance(value, int | Decimal) and not isinstance(value, bool):
        return euro(int(value))
    return plain(value)


def records(items: Sequence[Any], *, drop: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Objekte/Modelle -> Zeilen-Dicts mit plain(); `drop` nennt Felder, die
    das Modell nicht braucht (z. B. interne Versionen)."""
    out: list[dict[str, Any]] = []
    for item in items:
        record = plain(item)
        if not isinstance(record, dict):
            record = {"wert": record}
        for name in drop:
            record.pop(name, None)
            record.pop(name.removesuffix("_minor") + "_eur", None)
        out.append(record)
    return out


def table_of(rows: Sequence[Mapping[str, Any]], columns: Sequence[str] | None = None) -> ToolTable:
    """Zeilen-Dicts -> ToolTable. Spalten in der Reihenfolge des ersten
    Auftretens (oder wie angegeben); fehlende Werte sind null."""
    names: list[str] = list(columns) if columns is not None else []
    if columns is None:
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    names.append(key)
    return ToolTable(columns=names, rows=[[row.get(name) for name in names] for row in rows])


# ---------------------------------------------------------------- Seiten


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


@dataclass(frozen=True)
class Page:
    text: str
    start: int
    end: int
    total: int


def page_of(table: ToolTable, start: int, page_chars: int) -> tuple[int, int]:
    """(start, ende) der Seite ab `start`: so viele Zeilen, wie in page_chars
    passen - mindestens eine."""
    end = start
    used = 0
    while end < table.total:
        size = len(_json(table.rows[end])) + 1
        if end > start and used + size > page_chars:
            break
        used += size
        end += 1
    return start, end


def render_page(key: str, reply: ToolReply, start: int, page_chars: int) -> Page:
    """Text einer Ergebnisseite, wie das Modell sie bekommt.

    Kopfzeile mit Gesamtzahl und, wenn es weitergeht, dem Aufruf fuer die
    naechste Seite; dann "Spalten: [...]" und die Zeilen als JSON-Listen.
    Kennzahlen und freier Text stehen nur auf der ersten Seite."""
    parts: list[str] = []
    if start == 0:
        if reply.text:
            parts.append(reply.text)
        if reply.summary:
            parts.append("Kennzahlen: " + _json(reply.summary))
    table = reply.table
    if table is None:
        if not parts:
            parts.append(f"Kein Ergebnis (Ergebnis {key}).")
        return Page(text="\n".join(parts), start=0, end=0, total=0)
    total = table.total
    first, end = page_of(table, start, page_chars)
    if not total:
        head = f"Keine Zeilen (Ergebnis {key})."
    elif first >= total:
        head = f"Keine weiteren Zeilen: Ergebnis {key} hat {_count(total)} Zeilen."
    elif first == 0 and end >= total:
        head = (
            f"Eine Zeile (Ergebnis {key}):"
            if total == 1
            else f"Alle {_count(total)} Zeilen (Ergebnis {key}):"
        )
    else:
        head = f"Zeilen {_count(first + 1)}–{_count(end)} von {_count(total)} (Ergebnis {key})."
        if end < total:
            head += f' Weiter mit result_page({{"id":"{key}","from":{end}}}).'
    parts.append(head)
    if end > first:
        parts.append("Spalten: " + _json(table.columns))
        parts.append(_json(table.rows[first:end]))
    return Page(text="\n".join(parts), start=first, end=end, total=total)


# ---------------------------------------------------------------- Sitzung


@asynccontextmanager
async def read_only_session(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    household_id: UUID,
    user_id: UUID,
    timeout_ms: int,
) -> AsyncIterator[AsyncSession]:
    """Eigene Transaktion je Werkzeugausfuehrung: READ ONLY als erste
    Anweisung, danach Rolle und Claims des Verfassers (RLS) und die
    verbleibende Laufzeit als statement_timeout. Am Ende immer Rollback -
    gelesen wird nur."""
    async with session_factory() as session:
        transaction = await session.begin()
        try:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL ROLE authenticated"))
            await session.execute(
                text("select set_config('request.jwt.claims', :claims, true)"),
                {"claims": json.dumps({"sub": str(user_id), "role": "authenticated"})},
            )
            await session.execute(
                text("select set_config('app.household_id', :household_id, true)"),
                {"household_id": str(household_id)},
            )
            await session.execute(
                text("select set_config('statement_timeout', :timeout, true)"),
                {"timeout": str(max(1, timeout_ms))},
            )
            yield session
        finally:
            await transaction.rollback()


@dataclass
class ToolSet:
    """Die Werkzeuge eines Laufs: Definitionen fuer das Modell und Zugriff
    auf die Handler."""

    specs: list[ToolSpec] = field(default_factory=list)

    def get(self, name: str) -> ToolSpec | None:
        return next((spec for spec in self.specs if spec.name == name), None)

    def definitions(self) -> list[dict[str, Any]]:
        return [spec.definition() for spec in self.specs]

    def names(self) -> list[str]:
        return [spec.name for spec in self.specs]
