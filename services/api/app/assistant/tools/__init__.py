"""Die Fachwerkzeuge des KI-Felds (nur lesend - E5).

domain_tools() liefert die Werkzeuge in fester Reihenfolge: zuerst die freie
Auswertung (describe_fields, query_analytics), dann die Fachsichten. Seiten-
und Wiederoeffnungswerkzeuge (result_page, reopen_result) gehoeren der
Schleife, nicht hierher.

start_context() sammelt die Fakten fuer den ersten Prompt; den Feldkatalog
dazu liefert field_catalog_text().
"""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.tools import analytics, finance
from app.assistant.tools.analytics import field_catalog_text
from app.assistant.tools.base import ToolSpec

__all__ = ["domain_tools", "field_catalog_text", "start_context"]


def domain_tools() -> list[ToolSpec]:
    return [*analytics.TOOLS, *finance.TOOLS]


async def start_context(session: AsyncSession, household_id: UUID, today: date) -> dict[str, Any]:
    """Startkontext, nur lesend (in read_only_session aufzurufen): heutiges
    Datum, Zeitraum und Anzahl der Buchungen, Konten mit aktuellem Saldo in
    Euro."""
    return await finance.start_context_facts(session, household_id, today)
