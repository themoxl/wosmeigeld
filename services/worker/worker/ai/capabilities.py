"""Persistierte Modell-Faehigkeiten (Tabelle ai_model_capabilities).

Die Schreiblogik steht im KI-Kern (app/llm/classification.py) und gilt fuer
API und Worker gleich; hier bleibt nur die Signatur, die der Batch-Lauf seit
jeher benutzt (Promptversion an vierter Stelle)."""

from uuid import UUID

from app.llm.classification import record_model_capability as _record
from sqlalchemy.ext.asyncio import AsyncSession


async def record_model_capability(
    session: AsyncSession,
    household_id: UUID,
    model_name: str,
    prompt_version: str,
    supports_json_schema: bool,
    canary_passed: bool,
    last_error: str | None = None,
) -> None:
    """Haelt je Haushalt/Modell/Promptversion fest, ob strukturierte Ausgabe
    (response_format/json_schema) funktioniert."""
    await _record(
        session,
        household_id,
        model_name,
        supports_json_schema=supports_json_schema,
        canary_passed=canary_passed,
        last_error=last_error,
        prompt_version=prompt_version,
    )
