"""KI in der API: Modellkatalog, Status mit Vorabtest, Einzelklassifikation.

Der Klassifikationsvertrag (Prompt, Schema, Parser, Kandidatenliste) und der
Weg zum Modell (Fenster, relatives Budget, Messung, Protokoll) stehen im
KI-Kern app/llm - fuer API und Worker gleich. Hier steht nur, was die API
zusaetzlich braucht: die Statusauskunft fuer die Oberflaeche und die
Einzelklassifikation mit der Kategorieaufloesung der API.
"""

import math
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import anthropic_api, providers, reasons
from app.llm.answers import log_without_call
from app.llm.calls_log import CallScope
from app.llm.classification import (
    CANARY_CATEGORY_ID,
    DEFAULT_REASON_CODE,
    MAX_SCHEMA_ATTEMPTS,
    NO_MATCH,
    NO_MATCH_REASON_CODE,
    PROMPT_VERSION,
    STALE_CATEGORY_REASON_CODE,
    CanaryResult,
    CategoryCandidates,
    ClassificationSchemaError,
    build_candidate_entries,
    build_classification_json_schema,
    build_system_prompt,
    build_user_prompt,
    classify_one,
    is_no_match,
    load_categories,
    mask_pii_text,
    parse_classification_content,
    prompt_version_for,
    record_model_capability,
    run_canary,
)
from app.llm.discovery import Target, anthropic_listing, locate_target
from app.llm.discovery import discover as _discover
from app.llm.gateway import reasoning_plan
from app.llm.measurements import Measurement, load_measurement, start_measurement
from app.llm.usage import UsageTokens, extract_usage_tokens
from app.schemas.ai import (
    AIClassifyResult,
    AIHealthResponse,
    AIMeasurementInfo,
    AIModelCatalog,
    AIModelInfo,
    AISamplingProfile,
    AIWarning,
    AIWindowInfo,
)
from app.services import ai_model_profiles, ai_runtime, ai_settings_service
from app.services.category_service import resolve_category_id

__all__ = [
    "CANARY_CATEGORY_ID",
    "DEFAULT_REASON_CODE",
    "MAX_SCHEMA_ATTEMPTS",
    "NO_MATCH",
    "NO_MATCH_REASON_CODE",
    "PROMPT_VERSION",
    "STALE_CATEGORY_REASON_CODE",
    "CategoryCandidates",
    "ClassificationSchemaError",
    "UsageTokens",
    "build_candidate_entries",
    "build_classification_json_schema",
    "build_system_prompt",
    "build_user_prompt",
    "check_lm_studio_health",
    "classify_transaction_with_llm",
    "discover",
    "extract_usage_tokens",
    "get_candidate_urls",
    "get_model_catalog",
    "is_no_match",
    "load_categories",
    "mask_pii_text",
    "parse_classification_content",
    "prompt_version_for",
    "record_model_capability",
    "resolve_for_write",
    "run_capability_canary",
]


def get_candidate_urls() -> list[str]:
    settings = get_settings()
    urls = [settings.lm_studio_base_url]
    fallbacks = [
        "http://host.docker.internal:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://localhost:1234/v1",
    ]
    for fb in fallbacks:
        if fb not in urls:
            urls.append(fb)
    return urls


async def discover(
    resolved: ai_settings_service.ResolvedAISettings,
) -> tuple[str | None, list[AIModelInfo], bool]:
    """Erste antwortende Basis-URL samt Modellliste (siehe app/llm/discovery).

    Rueckgabe: (URL oder None, Modelle, ob die detaillierte API verfuegbar
    war)."""
    return await _discover(resolved)


async def resolve_for_write(
    session: AsyncSession, household_id: UUID, category_id: UUID | None
) -> UUID | None:
    """Letzte Pruefung vor dem Schreiben in classification_decisions.

    classification_decisions traegt bewusst KEINEN Archiv-Trigger (die Tabelle
    ist ein unveraenderlicher Entscheidungsnachweis) - hier ist der Resolver
    die einzige Schutzschicht. Ein Merge-Tombstone wird kanonisiert, alles
    Ungueltige wird zu None statt zu einem HTTP-Fehler: ein KI-Vorschlag darf
    einen Lauf nicht abbrechen. Zwilling im Worker:
    worker/ai/classifier.py::resolve_for_write (ohne FastAPI)."""
    if category_id is None:
        return None
    try:
        return await resolve_category_id(session, household_id, category_id, allow_none=True)
    except HTTPException:
        return None


def _sampling_profile_schema(
    profile: ai_model_profiles.SamplingProfile,
) -> AISamplingProfile:
    return AISamplingProfile(
        id=profile.id,
        label=profile.label,
        model_examples=list(profile.model_examples),
        model_markers=list(profile.name_markers),
        reasoning_mode=profile.reasoning_mode,
        temperature=profile.temperature,
        top_p=profile.top_p,
        top_k=profile.top_k,
        repeat_penalty=profile.repeat_penalty,
        min_p=profile.min_p,
        presence_penalty=profile.presence_penalty,
        note=profile.note,
        source_url=profile.source_url,
    )


async def get_model_catalog(
    resolved: ai_settings_service.ResolvedAISettings,
) -> AIModelCatalog:
    """Alle installierten Modelle - Grundlage der Auswahl in den
    Einstellungen. Einbettungsmodelle bleiben in der Liste, sind aber als
    nicht chatfaehig markiert."""
    if resolved.provider == providers.ANTHROPIC:
        return await _cloud_catalog(resolved)
    base_url, models, detailed = await discover(resolved)
    if base_url is None:
        return AIModelCatalog(
            base_url=resolved.candidate_urls()[0],
            is_connected=False,
            supports_detail_api=False,
            models=[],
            sampling_profiles=[
                _sampling_profile_schema(profile) for profile in ai_model_profiles.SAMPLING_PROFILES
            ],
            message="LM Studio ist nicht erreichbar oder kein Dienst laeuft auf Port 1234.",
        )
    chat_models = [model for model in models if model.is_chat_capable]
    if not models:
        message = "LM Studio laeuft, aber es ist kein Modell installiert."
    elif not chat_models:
        message = (
            "LM Studio laeuft, aber keines der installierten Modelle beherrscht "
            "Chat-Completions - Einbettungsmodelle koennen nicht klassifizieren."
        )
    elif detailed:
        loaded = sum(1 for model in chat_models if model.state == "loaded")
        message = f"{len(chat_models)} chatfaehige Modelle, davon {loaded} geladen."
    else:
        message = (
            f"{len(chat_models)} Modelle gefunden. Dieser Server bietet nur die "
            "OpenAI-kompatible Liste - Ladezustand und Kontextfenster sind damit "
            "nicht ermittelbar."
        )
    # Reasoning-Faehigkeit steht nur in der v1-Liste. Sie fehlt bei aelteren
    # LM-Studio-Versionen - dann bleibt reasoning_options leer und die
    # Oberflaeche zeigt den Schalter gar nicht erst an.
    runtime_by_key = {item.key: item for item in await ai_runtime.list_models(base_url)}
    if runtime_by_key:
        models = [
            model.model_copy(
                update={
                    "reasoning_options": runtime_by_key[model.id].reasoning_options,
                    "reasoning_default": runtime_by_key[model.id].reasoning_default,
                    # v1 ist hier die verlaesslichere Quelle des GELADENEN Fensters.
                    "loaded_context_tokens": (
                        runtime_by_key[model.id].loaded_context_length()
                        or model.loaded_context_tokens
                    ),
                }
            )
            if model.id in runtime_by_key
            else model
            for model in models
        ]

    def profile_schema(model_id: str) -> AISamplingProfile | None:
        profile = ai_model_profiles.matching_profile(model_id, resolved.reasoning)
        return _sampling_profile_schema(profile) if profile is not None else None

    models = [
        model.model_copy(update={"sampling_profile": profile_schema(model.id)}) for model in models
    ]
    known_profiles = [
        _sampling_profile_schema(profile) for profile in ai_model_profiles.SAMPLING_PROFILES
    ]

    return AIModelCatalog(
        base_url=base_url,
        is_connected=True,
        supports_detail_api=detailed,
        models=models,
        sampling_profiles=known_profiles,
        message=message,
    )


async def _cloud_catalog(resolved: ai_settings_service.ResolvedAISettings) -> AIModelCatalog:
    """Modellliste bei Anthropic: was der Schluessel erreicht, mit Fenster,
    Antwortgrenze und Denkstufen. Ohne (lesbaren) Schluessel keine Anfrage."""
    listing = await anthropic_listing(resolved)
    name = providers.provider(resolved.provider).name
    if listing.reason_code is not None:
        return AIModelCatalog(
            base_url=listing.base_url,
            is_connected=False,
            provider="anthropic",
            supports_detail_api=False,
            models=[],
            sampling_profiles=[],
            message=listing.message or f"{name} ist nicht erreichbar.",
        )
    models = [model.info for model in listing.models]
    return AIModelCatalog(
        base_url=listing.base_url,
        is_connected=True,
        provider="anthropic",
        supports_detail_api=True,
        models=models,
        sampling_profiles=[],
        message=f"{len(models)} Modelle bei {name} verfügbar.",
    )


async def run_capability_canary(
    target: Target, resolved: ai_settings_service.ResolvedAISettings
) -> CanaryResult:
    """Vorabtest der Statusseite (app/llm/classification.run_canary): eine
    synthetische Beispielbuchung, gleiche Prompt-/Schema-Mechanik wie die
    Klassifikation. Misst und protokolliert nicht."""
    return await run_canary(target, resolved)


def _warning(code: str, message: str | None = None) -> AIWarning:
    return AIWarning(
        code=code,
        message=message or reasons.describe(code),
        level=reasons.level_of(code),
    )


def _measurement_info(measurement: Measurement) -> AIMeasurementInfo:
    return AIMeasurementInfo(
        chars_per_token=measurement.chars_per_token,
        estimate_error=measurement.estimate_error,
        calls=measurement.calls,
        is_start_value=measurement.is_start_value,
        error_is_start_value=measurement.error_is_start_value,
        largest_need_tokens=measurement.largest_need_tokens,
    )


async def _health(
    resolved: ai_settings_service.ResolvedAISettings,
    session: AsyncSession | None,
    household_id: UUID | None,
    *,
    with_canary: bool = True,
) -> tuple[AIHealthResponse, Target | None, CanaryResult | None]:
    """Discovery, Modellwahl, Fenster, Messung und Vorabtest - samt dem Ziel,
    damit die Klassifikation nicht ein zweites Mal sucht.

    Sucht bewusst auch bei ausgeschalteter KI (locate_target): die Statusseite
    zeigt dann, welches Modell bereitstuende; der Vorabtest laeuft erst nach
    dem Einschalten."""
    found = await locate_target(resolved)
    cloud = resolved.provider == providers.ANTHROPIC
    provider_name = providers.provider(resolved.provider).name
    provider_id: Literal["local", "anthropic"] = "anthropic" if cloud else "local"
    if found.base_url is None:
        return (
            AIHealthResponse(
                status="unavailable",
                is_connected=False,
                base_url=resolved.candidate_urls()[0],
                provider=provider_id,
                active_model=None,
                canary_passed=False,
                supports_json_schema=False,
                models_count=0,
                chat_models_count=0,
                message=(
                    found.message or f"{provider_name} ist nicht erreichbar."
                    if cloud
                    else "LM Studio ist nicht erreichbar oder kein Dienst laeuft auf Port 1234."
                ),
                warnings=[_warning(found.reason_code or reasons.MODEL_UNAVAILABLE, found.message)],
            ),
            None,
            None,
        )

    models = found.models
    chat_models = [model for model in models if model.is_chat_capable]
    target = found.target
    selected = target.selected if target is not None else None
    active_model = target.model if target is not None else None
    window = target.window if target is not None else None
    warnings: list[AIWarning] = []

    measurement: Measurement | None = None
    if target is not None:
        if session is not None and household_id is not None:
            measurement = await load_measurement(
                session, household_id, target.base_url, target.model, resolved
            )
        else:
            measurement = start_measurement(resolved)

    if not resolved.is_enabled:
        warnings.append(_warning(reasons.AI_DISABLED))
    if selected is not None and (
        selected.state == "not-loaded" or (window is not None and window.model_loaded is False)
    ):
        warnings.append(
            _warning(
                reasons.MODEL_NOT_LOADED,
                reasons.describe(reasons.MODEL_NOT_LOADED, f"Modell „{selected.id}“."),
            )
        )
    if window is not None and not window.known:
        warnings.append(_warning(reasons.MODEL_CONTEXT_UNKNOWN, found.message))
    if (
        window is not None
        and window.cap is not None
        and window.loaded is not None
        and window.cap > window.loaded
    ):
        warnings.append(_warning(reasons.MODEL_CAP_ABOVE_WINDOW))

    reasoning_sent: str | None = None
    if window is not None and cloud:
        choice = anthropic_api.reasoning_choice(
            resolved, None, selected.capabilities if selected is not None else []
        )
        reasoning_sent = choice.label
        if choice.skipped:
            warnings.append(
                AIWarning(
                    code=reasons.MODEL_REASONING_UNSUPPORTED, message=choice.skipped, level="info"
                )
            )
    elif window is not None:
        reasoning_sent, reasoning_event = reasoning_plan(
            resolved, None, window.reasoning_capability
        )
        if reasoning_event is not None:
            warnings.append(
                AIWarning(
                    code=reasoning_event.code,
                    message=reasoning_event.message,
                    level=reasoning_event.level,
                )
            )

    canary: CanaryResult | None = None
    if (
        with_canary
        and target is not None
        and window is not None
        and window.known
        and resolved.is_enabled
    ):
        canary = await run_capability_canary(target, resolved)
        if not canary.passed and canary.reason_code is not None:
            warnings.append(_warning(canary.reason_code, canary.message))
    canary_passed = bool(canary and canary.passed)
    supports_json_schema = bool(canary and canary.supports_json_schema)

    if cloud and found.reason_code is not None and selected is None:
        # Kein Modell gewaehlt bzw. nicht beim Anbieter vorhanden - der Grund
        # sagt es; bei einem Cloud-Anbieter wird nie automatisch gewaehlt.
        status = "model_missing"
        message = found.message or f"{provider_name}: kein verwendbares Modell."
        warnings.append(_warning(found.reason_code, found.message))
    elif resolved.model_name and selected is None:
        # Ein gewaehltes, aber verschwundenes Modell wird nicht still ersetzt.
        status = "model_missing"
        message = (
            f"Das eingestellte Modell „{resolved.model_name}“ ist in LM Studio nicht "
            "vorhanden. Es wird bewusst kein anderes ersatzweise verwendet - bitte in "
            "den Einstellungen ein vorhandenes Modell waehlen."
        )
    elif not chat_models:
        status = "no_model_loaded"
        message = (
            "LM Studio laeuft, aber es ist kein chatfaehiges Modell installiert "
            "(Einbettungsmodelle koennen nicht klassifizieren)."
        )
    elif active_model and not resolved.is_enabled:
        status = "connected"
        message = (
            f"Modell „{active_model}“ gefunden. Die KI ist für diesen Haushalt "
            "ausgeschaltet - der Vorabtest läuft erst nach dem Einschalten."
        )
    elif active_model and window is not None and not window.known:
        status = "connected"
        message = f"Modell „{active_model}“ gefunden. {found.message}"
    elif active_model and canary_passed:
        status = "connected"
        message = (
            f"Modell „{active_model}“ aktiv, Canary bestanden "
            f"({'strukturierte Ausgabe' if supports_json_schema else 'Prompt-Fallback'})."
        )
    elif active_model:
        status = "connected"
        message = (
            f"Modell „{active_model}“ gefunden, aber der Vorabtest ist gescheitert: "
            f"{canary.message if canary and canary.message else 'keine schemakonforme Antwort.'}"
        )
    else:
        status = "no_model_loaded"
        message = "LM Studio verfuegbar, aber kein Chat-Modell gefunden."

    effective = window.effective if window is not None else None
    reserve = (
        math.floor(effective * resolved.answer_reserve_share)
        if effective and resolved.answer_reserve_share
        else None
    )
    return (
        AIHealthResponse(
            status=status,
            is_connected=True,
            base_url=found.base_url,
            provider=provider_id,
            active_model=active_model,
            canary_passed=canary_passed,
            supports_json_schema=supports_json_schema,
            models_count=len(models),
            chat_models_count=len(chat_models),
            model_source=(
                "household"
                if resolved.model_name and selected
                else "automatic"
                if selected
                else "none"
            ),
            model_state=selected.state if selected else "unknown",
            context_tokens=effective,
            window=(
                AIWindowInfo(
                    loaded=window.loaded,
                    cap=window.cap,
                    effective=window.effective,
                    source=window.source,
                    reason=window.reason,
                    parallel=window.parallel,
                    max_context=window.max_context,
                )
                if window is not None
                else None
            ),
            reserve_tokens=reserve,
            measurement=_measurement_info(measurement) if measurement is not None else None,
            reasoning_capability=(
                window.reasoning_capability.kind if window is not None else "unknown"
            ),
            reasoning_sent=reasoning_sent,
            canary_reason_code=canary.reason_code if canary is not None else None,
            warnings=warnings,
            message=message,
        ),
        target,
        canary,
    )


async def check_lm_studio_health(
    resolved: ai_settings_service.ResolvedAISettings,
    *,
    session: AsyncSession | None = None,
    household_id: UUID | None = None,
) -> AIHealthResponse:
    """Discovery, Modellwahl, Fenster, Messung und Vorabtest.

    Mit Sitzung und Haushalt zeigt die Auskunft die gemessenen Werte des
    Modells; ohne (Aufrufer ohne Haushaltskontext) die Startwerte."""
    health, _target, _canary = await _health(resolved, session, household_id)
    return health


async def classify_transaction_with_llm(
    session: AsyncSession,
    household_id: UUID,
    transaction_id: UUID | None,
    payee_raw: str | None,
    purpose: str | None,
    amount_minor: int,
) -> AIClassifyResult:
    """Klassifiziert eine Buchung strukturiert ueber den KI-Kern (Abschnitt 8.3)."""
    resolved = await ai_settings_service.resolve(session, household_id)
    candidates = await load_categories(session, household_id)
    # Jede llm_calls-Zeile traegt Vertrags- UND Taxonomieversion.
    prompt_version = prompt_version_for(candidates.taxonomy_version)

    def unavailable(code: str) -> AIClassifyResult:
        return AIClassifyResult(
            payee_name=payee_raw or "",
            category_id=None,
            confidence=0.0,
            reason_code=code,
            source="ai",
            model_name=None,
        )

    # Ausgaenge ohne Modellaufruf bekommen trotzdem eine llm_calls-Zeile
    # (Stufe classify, Vertrags- und Taxonomieversion).
    scope = CallScope(
        session=session, household_id=household_id, stage="classify", prompt_version=prompt_version
    )
    if not resolved.is_enabled:
        # Vor der Statusabfrage: _health sucht auch bei ausgeschalteter KI.
        await log_without_call(scope, reasons.AI_DISABLED, reasons.describe(reasons.AI_DISABLED))
        return unavailable(reasons.AI_DISABLED)

    # Bei Claude kein Vorabtest vor jeder Einzelklassifikation: er waere ein
    # zweiter bezahlter Aufruf ohne Protokollzeile, und ob das Modell
    # strukturierte Ausgabe kann, steht schon in der Modellliste. Der Vorabtest
    # bleibt beim ausdruecklichen "Verbindung pruefen" (GET /v1/ai/health).
    health, target, canary = await _health(
        resolved,
        session,
        household_id,
        with_canary=resolved.provider != providers.ANTHROPIC,
    )
    if not health.is_connected or not health.active_model or target is None:
        # Kein Modell: Tokenspalten bleiben NULL - es gab keine Antwort.
        await log_without_call(scope, reasons.MODEL_UNAVAILABLE, health.message)
        return unavailable(reasons.MODEL_UNAVAILABLE)

    if canary is not None:
        # Vorabtest persistieren (auswertbar nach Modell und Promptversion).
        await record_model_capability(
            session,
            household_id,
            target.model,
            supports_json_schema=canary.supports_json_schema,
            canary_passed=canary.passed,
            last_error=None if canary.passed else canary.message,
        )

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
        settings=resolved,
        candidates=candidates,
        supports_json_schema=health.supports_json_schema,
        resolve_category=resolve_category,
    )
    return AIClassifyResult(
        payee_name=outcome.payee_name,
        category_id=outcome.category_id,
        confidence=outcome.confidence,
        reason_code=outcome.reason_code,
        source="ai",
        model_name=outcome.model_name,
    )
