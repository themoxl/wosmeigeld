"""Klassifikation im Worker - nur noch Orchestrierung.

Bis Etappe 0 stand hier eine vollstaendige Kopie der LM-Studio-Logik der API
(eigene Einstellungen, eigener Transport, eigenes Token-Budget mit Eskalation,
"Zwilling, wortgleich halten"). Das ist vorbei: Vertrag, Transport, Fenster,
relatives Budget, Messung und Einstellungen kommen aus dem KI-Kern
services/api/app/llm (ueber PYTHONPATH, siehe services/worker/Dockerfile,
pyproject.toml und scripts/dev.py).

Hier bleibt, was der Worker anders macht als die API:
* Discovery, Fenster und Vorabtest laufen EINMAL je Batch-Lauf (siehe
  worker/ai/processor.py) statt einmal je Buchung - sonst wiederholte jede
  Zeile bei ausgeschaltetem LM Studio alle Kandidaten-URLs.
* resolve_for_write prueft die Zielkategorie per SQL: der Worker verbindet
  sich ohne RLS und ohne FastAPI (die API nutzt category_service).
"""

from uuid import UUID

from app.llm.calls_log import CallScope
from app.llm.classification import (
    CANDIDATE_SQL,
    FAILURE_REASON_CODES,
    MAX_SCHEMA_ATTEMPTS,
    NO_MATCH,
    NO_MATCH_REASON_CODE,
    PROMPT_VERSION,
    STALE_CATEGORY_REASON_CODE,
    CanaryResult,
    CategoryCandidates,
    ClassificationSchemaError,
    build_candidate_entries,
    build_candidate_variants,
    build_classification_json_schema,
    build_system_prompt,
    build_user_prompt,
    classify_one,
    load_categories,
    mask_pii_text,
    parse_classification_content,
    prompt_version_for,
    run_canary,
)
from app.llm.discovery import Target, TargetResult, resolve_target
from app.llm.settings import LLMSettings
from app.llm.usage import UsageTokens, extract_usage_tokens
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "CANDIDATE_SQL",
    "FAILURE_REASON_CODES",
    "MAX_SCHEMA_ATTEMPTS",
    "NO_MATCH",
    "NO_MATCH_REASON_CODE",
    "PROMPT_VERSION",
    "STALE_CATEGORY_REASON_CODE",
    "CanaryResult",
    "CategoryCandidates",
    "ClassificationResult",
    "ClassificationSchemaError",
    "UsageTokens",
    "build_candidate_entries",
    "build_candidate_variants",
    "build_classification_json_schema",
    "build_system_prompt",
    "build_user_prompt",
    "classify_transaction",
    "extract_usage_tokens",
    "load_categories",
    "mask_pii_text",
    "parse_classification_content",
    "probe_model_capability",
    "prompt_version_for",
    "resolve_for_write",
    "resolve_lm_studio_endpoint",
]


class ClassificationResult(BaseModel):
    payee_name: str
    category_id: UUID | None
    confidence: float
    reason_code: str
    source: str
    model_name: str | None


# Merge-Ketten werden beim Merge abgeflacht; die Grenze faengt nur eine durch
# Fremdeingriff entstandene zyklische Kette ab (Zwilling:
# app/services/category_service.py::_MAX_MERGE_HOPS).
_MAX_MERGE_HOPS = 16

_RESOLVE_SQL = (
    "select c.id, c.is_archived, c.merged_into_category_id, "
    "cg.is_archived as group_is_archived "
    "from categories c "
    "join category_groups cg on cg.id = c.group_id "
    "and cg.household_id = c.household_id "
    "where c.id = :category_id and c.household_id = :household_id"
)


async def resolve_for_write(
    session: AsyncSession, household_id: UUID, category_id: UUID | None
) -> UUID | None:
    """Kleine SQL-Fassung von app/services/category_service.py::resolve_category_id.

    Notwendig, weil classification_decisions bewusst KEINEN Archiv-Trigger
    traegt - hier ist diese Pruefung die einzige Schutzschicht. Zwischen dem
    einmaligen Kandidatenladen zu Laufbeginn und dem Schreiben liegt bei einem
    grossen Lauf leicht eine Viertelstunde. Ein Merge-Tombstone wird auf sein
    aktives Ziel kanonisiert; archiviert, haushaltsfremd oder unbekannt ergibt
    None statt einer Ausnahme."""
    if category_id is None:
        return None
    current = category_id
    for _ in range(_MAX_MERGE_HOPS):
        row = (
            await session.execute(
                text(_RESOLVE_SQL),
                {"category_id": str(current), "household_id": str(household_id)},
            )
        ).first()
        if row is None:
            return None
        if row.merged_into_category_id is not None:
            current = UUID(str(row.merged_into_category_id))
            continue
        if row.is_archived or row.group_is_archived:
            return None
        return UUID(str(row.id))
    return None


async def resolve_lm_studio_endpoint(settings: LLMSettings) -> TargetResult:
    """Server, Modell und geladenes Fenster - EINMAL je Batch-Lauf.

    TargetResult.reason_code nennt, warum ein Lauf nicht starten kann:
    ai_disabled (ohne Serverkontakt), model_unavailable, model_missing oder
    model_context_unknown."""
    return await resolve_target(settings)


async def probe_model_capability(
    target: Target,
    settings: LLMSettings,
    candidates: CategoryCandidates | None = None,
    *,
    session: AsyncSession | None = None,
    household_id: UUID | None = None,
) -> CanaryResult:
    """Vorabtest EINMAL je Batch-Lauf mit dem ECHTEN Prompt (alle Kategorien,
    eine eindeutige Testbuchung): er prueft zugleich, ob die Liste samt
    Antwortreserve ins geladene Fenster passt. Liest die Messung des Modells,
    schreibt sie aber nicht fort und protokolliert nicht."""
    scope = (
        CallScope(
            session=session,
            household_id=household_id,
            stage="canary",
            prompt_version=PROMPT_VERSION,
            log=False,
            measure=False,
        )
        if session is not None and household_id is not None
        else None
    )
    return await run_canary(target, settings, candidates=candidates, scope=scope)


async def classify_transaction(
    session: AsyncSession,
    household_id: UUID,
    transaction_id: UUID | None,
    payee_raw: str | None,
    purpose: str | None,
    amount_minor: int,
    target: Target,
    candidates: CategoryCandidates,
    supports_json_schema: bool,
    settings: LLMSettings,
    ai_task_id: UUID | None = None,
) -> ClassificationResult:
    """Eine Buchung ueber den KI-Kern klassifizieren (app/llm/classification.py
    ::classify_one) - mit Budget aus dem geladenen Fenster, Protokoll in
    llm_calls und Messung. Fehlercodes siehe FAILURE_REASON_CODES."""

    async def resolve_category(category_id: UUID) -> UUID | None:
        return await resolve_for_write(session, household_id, category_id)

    outcome = await classify_one(
        session,
        household_id,
        transaction_id,
        payee_raw,
        purpose,
        amount_minor,
        target=target,
        settings=settings,
        candidates=candidates,
        supports_json_schema=supports_json_schema,
        resolve_category=resolve_category,
        ai_task_id=ai_task_id,
    )
    return ClassificationResult(
        payee_name=outcome.payee_name,
        category_id=outcome.category_id,
        confidence=outcome.confidence,
        reason_code=outcome.reason_code,
        source="ai",
        model_name=outcome.model_name,
    )
