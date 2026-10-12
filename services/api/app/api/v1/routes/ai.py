"""API-Endpunkte fuer Lokale KI, Health, Discovery und Klassifikation (Phase 6)."""

import logging
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    get_current_user,
    get_household_role,
    get_household_session,
    require_household_editor,
    require_household_owner,
)
from app.auth.jwt import AuthenticatedUser
from app.llm import anthropic_api, keys, providers
from app.llm import settings as llm_settings
from app.llm.discovery import locate_target
from app.llm.window import SOURCE_LMSTUDIO_V1, WindowInfo, effective_window
from app.schemas.ai import (
    AIClassifyInput,
    AIClassifyResult,
    AIDecisionLog,
    AIHealthResponse,
    AIKeyInput,
    AIKeyStatus,
    AIModelCatalog,
    AIModelLoadResult,
    AIProposalApplyInput,
    AIProposalBatchResult,
    AIRuntimeGpu,
    AIRuntimeModel,
    AIRuntimeStatus,
    AISettings,
    AISettingsUpdate,
    AIWindowInfo,
    ClassificationRun,
    ClassificationRunCreate,
    ProviderId,
)
from app.services import ai_proposal_service, ai_runtime, ai_settings_service, gpu_probe
from app.services.ai_service import (
    check_lm_studio_health,
    classify_transaction_with_llm,
    discover,
    get_model_catalog,
)

# `worker.actors.process_classification_batch` (dramatiq-Actor) lebt in
# services/worker, nicht in dieser API-Codebase - dieselbe sys.path-Kopplung
# wie in routes/imports.py fuer process_import.
worker_dir = Path(__file__).resolve().parents[5] / "worker"
if str(worker_dir) not in sys.path:
    sys.path.insert(0, str(worker_dir))

from worker.actors import process_classification_batch as classify_batch_actor  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

HouseholdSession = Annotated[tuple[AsyncSession, UUID], Depends(get_household_session)]
# Klassifikation schreibt classification_decisions bzw. classification_runs -
# 403 statt stillem Nichtstun, wenn die Rolle nicht schreiben darf.
HouseholdEditorSession = Annotated[
    tuple[AsyncSession, UUID], Depends(require_household_editor)
]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
# API-Schluessel hinterlegen/entfernen: nur der Owner. Ein Schluessel kostet
# Geld, und mit ihm verlassen Haushaltsdaten das Haus.
HouseholdOwnerSession = Annotated[tuple[AsyncSession, UUID], Depends(require_household_owner)]
HouseholdRole = Annotated[str, Depends(get_household_role)]

# Ab wann ein aktiver Lauf ohne Lebenszeichen als haengend gilt. Der Worker
# schreibt heartbeat_at nach jeder Buchung; eine Buchung kostet bis zu drei
# LM-Studio-Aufrufe, jeder hoechstens so lange wie das Zeitbudget je Anfrage
# der Haushaltseinstellung (request_timeout_seconds, Vorgabe im Schema). 10
# Minuten fangen zugleich den Fall ab, dass gar kein Worker laeuft - sonst
# blockierte ein solcher Lauf wegen classification_runs_one_active_idx jeden
# Neustart dauerhaft.
STALE_RUN_AFTER_MINUTES = 10

_ACTIVE_STATES = "('pending', 'running')"

# is_stale wird in SQL berechnet, damit "haengend" ueberall dieselbe Definition
# hat (Liste, Einzelabruf, Startpruefung) und nicht von der Uhr des Clients
# abhaengt.
_CLASSIFY_RUN_COLUMNS = (
    "id, status, run_type, current_stage, since_date, total_count, processed_count, "
    "success_count, error_count, error_message, created_at, completed_at, heartbeat_at, "
    "cancel_requested, "
    f"(status in {_ACTIVE_STATES} and coalesce(heartbeat_at, created_at) "
    "< now() - make_interval(mins => :stale_minutes)) as is_stale"
)

_UNCLASSIFIED_COUNT_SQL = (
    "select count(*), min(t.booked_date) from transactions t "
    "where t.household_id = :household_id and not t.is_archived and not t.is_transfer "
    "and (not exists (select 1 from transaction_splits s where s.transaction_id = t.id) "
    "or exists (select 1 from transaction_splits s where s.transaction_id = t.id "
    "and s.category_id is null))"
)


async def _select_run(
    db: AsyncSession,
    household_id: UUID,
    condition: str,
    extra_params: dict[str, object] | None = None,
) -> Row[Any] | None:
    """Liest genau einen Lauf des Haushalts (juengster zuerst). `condition` ist
    ausschliesslich projektinterner SQL-Text, nie Nutzereingabe."""
    res = await db.execute(
        text(
            f"select {_CLASSIFY_RUN_COLUMNS} from classification_runs "
            f"where household_id = :household_id and {condition} "
            "order by created_at desc, id desc limit 1"
        ),
        {
            "household_id": str(household_id),
            "stale_minutes": STALE_RUN_AFTER_MINUTES,
            **(extra_params or {}),
        },
    )
    return res.first()


async def _end_run(db: AsyncSession, run_id: UUID, message: str) -> None:
    """Beendet einen Lauf als 'cancelled'. Bereits geschriebene Teilresultate
    (processed_count, classification_decisions) bleiben erhalten."""
    await db.execute(
        text(
            "update classification_runs set status = 'cancelled', cancel_requested = true, "
            "completed_at = now(), error_message = coalesce(error_message, :msg) "
            "where id = :id"
        ),
        {"id": str(run_id), "msg": message},
    )


@router.get("/health", response_model=AIHealthResponse)
async def fetch_ai_health(
    session_ctx: HouseholdSession,
) -> AIHealthResponse:
    """Status mit der Modellwahl DIESES Haushalts: Fenster (geladen, Deckel,
    wirksam), Antwortreserve, Messung des Modells und Vorabtest.

    Frueher parameterlos - dadurch konnte der Status ein anderes Modell melden
    als die Klassifikation spaeter verwendete. Der Vorabtest misst und
    protokolliert nicht; deshalb genuegt auch eine Betrachter-Sitzung."""
    session, household_id = session_ctx
    resolved = await ai_settings_service.resolve(session, household_id)
    return await check_lm_studio_health(resolved, session=session, household_id=household_id)


def _window_info(window: WindowInfo) -> AIWindowInfo:
    return AIWindowInfo(
        loaded=window.loaded,
        cap=window.cap,
        effective=window.effective,
        source=window.source,
        reason=window.reason,
        parallel=window.parallel,
        max_context=window.max_context,
    )


@router.get("/runtime", response_model=AIRuntimeStatus)
async def fetch_ai_runtime(session_ctx: HouseholdSession) -> AIRuntimeStatus:
    """Womit gerade gerechnet wird: Modell, Kontextfenster, Reasoning, GPU.

    Bewusst genau eine Anfrage an LM Studio (`GET /api/v1/models`) plus eine
    lokale GPU-Messung - der Statusstreifen der Warteschlange fragt im
    Sekundenbereich nach, und ein Statusabruf darf die Maschine nicht
    beschaeftigen, die gerade klassifiziert. Der teurere Canary bleibt beim
    ausdruecklichen "Verbindung pruefen" auf der Statusseite.

    Das wirksame Fenster rechnet dieselbe Regel wie der KI-Kern
    (llm.effective_window: min(geladen, "Kontext hoechstens")) aus der Antwort
    derselben einen Anfrage - ohne weitere Leser zu fragen."""
    session, household_id = session_ctx
    resolved = await ai_settings_service.resolve(session, household_id)
    cap = resolved.context_cap_tokens
    if resolved.provider == providers.ANTHROPIC:
        return await _cloud_runtime(resolved)

    base_url: str | None = None
    models: list[ai_runtime.RuntimeModel] = []
    for candidate in resolved.candidate_urls():
        found = await ai_runtime.list_models(candidate)
        if found:
            base_url = candidate
            models = found
            break

    gpu_status = await gpu_probe.probe()
    gpus = [
        AIRuntimeGpu(
            name=reading.name,
            memory_used_mb=reading.memory_used_mb,
            memory_total_mb=reading.memory_total_mb,
            utilization_percent=reading.utilization_percent,
            temperature_celsius=reading.temperature_celsius,
        )
        for reading in gpu_status.gpus
    ]

    if not models:
        # Kein Unterschied erkennbar zwischen "LM Studio aus" und "zu alte
        # Version ohne Verwaltungsschnittstelle" - hier lohnt die zweite,
        # billige Frage an die OpenAI-kompatible Liste.
        reachable_url, listed, _detailed = await discover(resolved)
        unknown = effective_window(
            None,
            cap,
            source=None,
            why_unknown=(
                "LM Studio meldet keine Modellverwaltung (/api/v1/models)"
                if reachable_url is not None
                else "LM Studio ist nicht erreichbar"
            ),
        )
        return AIRuntimeStatus(
            is_connected=reachable_url is not None,
            base_url=reachable_url,
            configured_model=resolved.model_name,
            reasoning=resolved.reasoning,
            reasoning_effort=resolved.reasoning_effort(),
            window=_window_info(unknown),
            chat_models_count=sum(1 for item in listed if item.is_chat_capable),
            gpus=gpus,
            gpu_detail=gpu_status.detail,
            message=(
                "LM Studio antwortet, meldet aber keine Modellverwaltung "
                "(/api/v1/models). Modell und Kontextfenster sind dann nur in "
                "LM Studio selbst sichtbar."
                if reachable_url is not None
                else "LM Studio ist nicht erreichbar."
            ),
        )

    chat_models = [item for item in models if item.kind != "embeddings"]
    loaded = [item for item in models if item.is_loaded]
    # Das eingestellte Modell hat Vorrang: laeuft daneben noch ein zweites,
    # waere "irgendein geladenes" eine irrefuehrende Antwort auf "womit wird
    # gearbeitet".
    active = next((item for item in loaded if item.key == resolved.model_name), None)
    if active is None and resolved.model_name is None:
        active = loaded[0] if loaded else None

    if active is not None:
        window = effective_window(
            active.loaded_window_tokens(),
            cap,
            source=SOURCE_LMSTUDIO_V1,
            why_unknown=f"LM Studio meldet für „{active.key}“ kein geladenes Kontextfenster",
            max_context=active.max_context_length,
            model_loaded=True,
        )
    else:
        window = effective_window(
            None,
            cap,
            source=None,
            why_unknown=(
                f"„{resolved.model_name}“ ist nicht geladen"
                if resolved.model_name
                else "kein Modell geladen"
            ),
            model_loaded=False,
        )

    if active is not None:
        message = f"„{active.key}“ ist geladen und bereit."
    elif loaded and resolved.model_name:
        message = (
            f"Geladen ist „{loaded[0].key}“, eingestellt aber „{resolved.model_name}“. "
            "Der naechste Lauf laedt das eingestellte Modell nach."
        )
    else:
        message = (
            "Kein Modell geladen. Der naechste Lauf laedt das eingestellte Modell - "
            "das dauert einmalig eine halbe Minute und mehr."
        )

    return AIRuntimeStatus(
        is_connected=True,
        base_url=base_url,
        configured_model=resolved.model_name,
        active_model=(
            AIRuntimeModel(
                key=active.key,
                display_name=active.display_name,
                architecture=active.architecture,
                quantization=active.quantization,
                parameters=active.parameters,
                size_bytes=active.size_bytes,
                max_context_tokens=active.max_context_length,
                loaded_context_tokens=active.loaded_context_length(),
            )
            if active is not None
            else None
        ),
        loaded_count=len(loaded),
        chat_models_count=len(chat_models),
        reasoning=resolved.reasoning,
        reasoning_effort=resolved.reasoning_effort(),
        window=_window_info(window),
        gpus=gpus,
        gpu_detail=gpu_status.detail,
        message=message,
    )


async def _cloud_runtime(
    resolved: ai_settings_service.ResolvedAISettings,
) -> AIRuntimeStatus:
    """Laufzeitanzeige bei Anthropic: Modell und Fenster aus der Modellliste.
    Nichts zu laden, keine GPU auf dieser Seite."""
    found = await locate_target(resolved)
    target = found.target
    selected = target.selected if target is not None else None
    window = (
        target.window
        if target is not None
        else effective_window(
            None,
            resolved.context_cap_tokens,
            source=None,
            why_unknown=found.message or "kein Modell gewählt",
        )
    )
    choice = anthropic_api.reasoning_choice(
        resolved, None, selected.capabilities if selected is not None else []
    )
    name = providers.provider(resolved.provider).name
    return AIRuntimeStatus(
        is_connected=found.base_url is not None,
        base_url=found.base_url,
        provider="anthropic",
        configured_model=resolved.model_name,
        active_model=(
            AIRuntimeModel(
                key=selected.id,
                display_name=selected.display_name,
                max_context_tokens=selected.max_context_tokens,
                loaded_context_tokens=selected.loaded_context_tokens,
            )
            if selected is not None
            else None
        ),
        loaded_count=1 if selected is not None else 0,
        chat_models_count=len(found.models),
        reasoning=resolved.reasoning,
        reasoning_effort=choice.label,
        window=_window_info(window),
        message=(
            f"{name}: „{selected.display_name or selected.id}“ ist bereit."
            if selected is not None and found.reason_code is None
            else found.message or f"{name} ist nicht bereit."
        ),
    )


@router.get("/models", response_model=AIModelCatalog)
async def list_ai_models(
    session_ctx: HouseholdSession,
    provider: Annotated[ProviderId | None, Query()] = None,
) -> AIModelCatalog:
    """Alle Modelle des Anbieters - Grundlage der Auswahl.

    Einbettungsmodelle sind enthalten, aber als nicht chatfaehig markiert: sie
    wegzulassen wuerde die Frage aufwerfen, warum LM Studio mehr Modelle zeigt
    als der Finanzplaner.

    `provider` fragt die Liste eines anderen als des gespeicherten Anbieters
    ab - damit die Einstellungen nach einem Wechsel sofort dessen Modelle
    zeigen, ohne vorher zu speichern. Gesendet wird dabei nichts ausser der
    Modellliste (mit dem hinterlegten Schluessel dieses Anbieters)."""
    session, household_id = session_ctx
    resolved = await ai_settings_service.resolve(session, household_id)
    if provider is not None and provider != resolved.provider:
        resolved = await llm_settings.with_api_key(
            session, household_id, replace(resolved, provider=provider)
        )
    return await get_model_catalog(resolved)


@router.post("/models/load", response_model=AIModelLoadResult)
async def load_ai_model(session_ctx: HouseholdEditorSession) -> AIModelLoadResult:
    """Laedt das eingestellte Modell mit dem eingestellten Kontextfenster in
    LM Studio - damit der Nutzer dafuer nicht in eine fremde Oberflaeche
    wechseln muss.

    Bewusst ohne Anfragekoerper: geladen wird, was in den KI-Einstellungen
    dieses Haushalts steht. Eine zweite Stelle, an der Modell und Fenster
    stehen koennten, waere eine Quelle fuer Widersprueche - die Einstellung ist
    der Wunsch, LM Studio der Zustand, und dieser Endpunkt bringt beides
    zusammen.

    Hinter dem Editor-Gate, weil er einen echten Seiteneffekt auf einem
    gemeinsam genutzten Dienst ausloest: das Laden belegt Speicher und
    verdraengt die vorherige Instanz fuer alle Haushalte auf diesem Server."""
    session, household_id = session_ctx
    resolved = await ai_settings_service.resolve(session, household_id)
    if resolved.provider != providers.LOCAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Bei einem Cloud-Anbieter gibt es nichts zu laden – die Modelle stehen dort "
                "immer bereit."
            ),
        )
    if not resolved.model_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Es ist kein Modell ausgewaehlt. Ohne ausdrueckliche Wahl bestimmt "
                "die Discovery das Modell - dann gibt es nichts zu laden."
            ),
        )

    base_url, _models, _detailed = await discover(resolved)
    if base_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LM Studio ist nicht erreichbar.",
        )

    try:
        result = await ai_runtime.ensure_loaded(
            base_url, resolved.model_name, resolved.context_length
        )
    except ai_runtime.LMStudioRuntimeError as exc:
        # 409: die Anfrage war in Ordnung, der Zustand von LM Studio laesst sie
        # nur nicht zu (Speicher, Fenster zu gross, Modell fehlt). Die Meldung
        # kommt aus dem Dienst und ist bereits fuer Menschen formuliert.
        logger.warning("Modell laden fehlgeschlagen: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    return AIModelLoadResult(
        model_name=resolved.model_name,
        changed=result.changed,
        instance_id=result.instance_id,
        context_length=result.context_length,
        load_time_seconds=result.load_time_seconds,
        message=result.message,
    )


@router.get("/settings", response_model=AISettings)
async def get_ai_settings(session_ctx: HouseholdSession) -> AISettings:
    session, household_id = session_ctx
    return await ai_settings_service.get_settings_view(session, household_id)


@router.put("/settings", response_model=AISettings)
async def put_ai_settings(
    payload: AISettingsUpdate, session_ctx: HouseholdEditorSession, role: HouseholdRole
) -> AISettings:
    """Teilaenderung. `clear_fields` setzt einzelne Werte ausdruecklich auf die
    Vorgabe der Umgebung zurueck - `null` allein hiesse "nicht angefasst".

    Den Anbieter wechseln darf nur der Owner: mit einem Cloud-Anbieter
    verlassen Haushaltsdaten das Haus. Wer den gewaehlten Anbieter
    unveraendert mitschickt, aendert nichts und braucht das Recht nicht."""
    session, household_id = session_ctx
    if payload.provider is not None and role != "owner":
        row = await llm_settings.load_settings_row(session, household_id)
        if payload.provider != (row.get("provider") or providers.LOCAL):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="household_owner_required"
            )
    return await ai_settings_service.update(session, household_id, payload)


@router.put("/provider-keys/{provider_id}", response_model=AIKeyStatus)
async def put_provider_key(
    provider_id: ProviderId, payload: AIKeyInput, session_ctx: HouseholdOwnerSession
) -> AIKeyStatus:
    """Hinterlegt den API-Schluessel eines Anbieters - verschluesselt, an das
    Ziel des Anbieters gebunden (app/llm/keys.py). Die Antwort nennt nur
    "hinterlegt (...abcd)", nie den Wert."""
    session, household_id = session_ctx
    try:
        return await ai_settings_service.store_key(
            session, household_id, provider_id, payload.api_key
        )
    except (keys.ProviderKeyError, providers.ProviderError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/provider-keys/{provider_id}", response_model=AIKeyStatus)
async def delete_provider_key(
    provider_id: ProviderId, session_ctx: HouseholdOwnerSession
) -> AIKeyStatus:
    session, household_id = session_ctx
    try:
        return await ai_settings_service.remove_key(session, household_id, provider_id)
    except (keys.ProviderKeyError, providers.ProviderError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/classify", response_model=AIClassifyResult)
async def trigger_ai_classification(
    payload: AIClassifyInput,
    session_ctx: HouseholdEditorSession,
) -> AIClassifyResult:
    db, household_id = session_ctx
    return await classify_transaction_with_llm(
        db,
        household_id,
        payload.transaction_id,
        payload.payee_raw,
        payload.purpose,
        payload.amount_minor,
    )


@router.get("/decisions", response_model=list[AIDecisionLog])
async def list_ai_decisions(
    session_ctx: HouseholdSession,
    limit: int = Query(default=500, le=2000),
    classification_run_id: UUID | None = Query(default=None),
) -> list[AIDecisionLog]:
    """Juengste Entscheidung je Buchung - Grundlage der Konfidenz-Chips.

    `distinct on` ist hier kein Feinschliff, sondern der Kern: die Liste
    beantwortet "was schlaegt die KI zu DIESER Buchung vor?", und dafuer zaehlt
    ausschliesslich der letzte Stand. Ohne die Einschraenkung zaehlte das Limit
    Entscheidungen statt Buchungen, und weil jeder erneute Lauf eine weitere
    Zeile je Buchung anlegt (bei einem Haushalt hier: 919 Entscheidungen zu 620
    Buchungen), deckten 50 Zeilen am Ende nur noch 50 Buchungen ab. Sichtbar
    wurde das als Gespenst: waehrend eines Laufs erschienen Chips an den gerade
    verarbeiteten Buchungen und verschwanden wieder, sobald 50 neuere
    Entscheidungen nachrueckten - der Vorschlag stand unveraendert in der
    Datenbank, nur nicht mehr in dieser Antwort.

    Das Limit begrenzt jetzt Buchungen und liegt hoch genug fuer einen
    vollstaendigen Posteingang; die Sortierung nach `created_at desc` bleibt,
    damit bei sehr grossen Bestaenden die zuletzt geprueften Buchungen
    zuerst abgedeckt sind."""
    db, household_id = session_ctx
    res = await db.execute(
        text(
            "select id, transaction_id, payee_name, category_id, confidence, reason_code, "
            "source, model_name, created_at from ("
            "  select distinct on (transaction_id) "
            "    id, transaction_id, payee_name, category_id, confidence, reason_code, "
            "    source, model_name, created_at "
            "  from classification_decisions "
            "  where household_id = :household_id "
            "  and (cast(:classification_run_id as uuid) is null or exists ("
            "    select 1 from ai_tasks at where at.id = classification_decisions.ai_task_id "
            "    and at.household_id = classification_decisions.household_id "
            "    and at.classification_run_id = cast(:classification_run_id as uuid)"
            "  )) "
            "  order by transaction_id, created_at desc, id desc"
            ") neueste order by created_at desc limit :limit"
        ),
        {
            "household_id": str(household_id),
            "classification_run_id": (
                str(classification_run_id) if classification_run_id else None
            ),
            "limit": limit,
        },
    )
    return [AIDecisionLog(**row._mapping) for row in res]


@router.post("/proposals/preview", response_model=AIProposalBatchResult)
async def preview_ai_proposals(
    payload: AIProposalApplyInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> AIProposalBatchResult:
    """Zeigt je Buchung, was "Alle sicheren uebernehmen" taete - ohne etwas zu
    schreiben.

    Bewusst ebenfalls hinter dem Editor-Gate: die Vorschau ist der erste
    Schritt eines Schreibvorgangs, und ein 'viewer' soll nicht erst im zweiten
    Schritt erfahren, dass er nicht darf."""
    db, household_id = session_ctx
    return await ai_proposal_service.evaluate_proposals(
        db, household_id, payload, write=False, actor_user_id=user.id
    )


@router.post("/proposals/apply", response_model=AIProposalBatchResult)
async def apply_ai_proposals(
    payload: AIProposalApplyInput,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
) -> AIProposalBatchResult:
    """Uebernimmt alle sicheren KI-Vorschlaege in einem Vorgang.

    "Sicher" heisst: juengster Entscheid, Konfidenz mindestens `min_confidence`,
    Buchung noch unkategorisiert, genau ein Split, Zielkategorie weiterhin
    aktiv. Alles andere wird einzeln als uebersprungen, unveraendert oder
    konfliktbehaftet quittiert - nie stillschweigend ueberschrieben. Die
    Obergrenze von 500 Buchungen prueft bereits AIProposalApplyInput (422)."""
    db, household_id = session_ctx
    return await ai_proposal_service.evaluate_proposals(
        db, household_id, payload, write=True, actor_user_id=user.id
    )


@router.post("/classify-runs", response_model=ClassificationRun, status_code=201)
async def start_classification_run(
    payload: ClassificationRunCreate,
    session_ctx: HouseholdEditorSession,
    user: CurrentUser,
    response: Response,
) -> ClassificationRun:
    """Startet einen Hintergrund-Batch-Lauf fuer alle offenen Buchungen.

    Der Zeitraum ist bewusst kein Eingabekriterium mehr: offen ist offen,
    unabhaengig davon, aus welchem Monat die Buchung stammt. Der Client pollt
    GET /classify-runs
    bzw. GET /classify-runs/{id} fuer den Fortschritt (dasselbe Muster wie
    GET /imports/{run_id}).

    Pro Haushalt existiert hoechstens ein aktiver Lauf
    (classification_runs_one_active_idx). Ein Doppelklick, ein Retry nach
    Netzwerkfehler oder ein zweites Geraet bekommt deshalb den bereits
    laufenden Lauf mit HTTP 200 zurueck, statt einen zweiten Job und damit
    doppelte Modellaufrufe zu erzeugen."""
    db, household_id = session_ctx

    # 1. Idempotenz ueber den Client-Schluessel: derselbe request_id liefert
    #    immer denselben Lauf, auch wenn er inzwischen abgeschlossen ist.
    if payload.request_id:
        known = await _select_run(
            db, household_id, "request_id = :request_id", {"request_id": payload.request_id}
        )
        if known is not None:
            response.status_code = status.HTTP_200_OK
            return ClassificationRun(**known._mapping)

    # 2. Laeuft schon einer? Dann diesen zurueckgeben - ausser er haengt.
    active = await _select_run(db, household_id, f"status in {_ACTIVE_STATES}")
    if active is not None:
        if not active.is_stale:
            response.status_code = status.HTTP_200_OK
            return ClassificationRun(**active._mapping)
        await _end_run(
            db,
            active.id,
            f"Automatisch beendet: seit ueber {STALE_RUN_AFTER_MINUTES} Minuten kein "
            "Lebenszeichen des Verarbeitungsdienstes.",
        )

    count_row = (
        await db.execute(
            text(_UNCLASSIFIED_COUNT_SQL), {"household_id": str(household_id)}
        )
    ).one()
    total_count = count_row[0]
    # Die bestehende DB-Spalte bleibt als Laufmetadatum erhalten. Sie ist der
    # aelteste offene Buchungstag, aber kein Filter mehr.
    since_date = count_row[1] or date.today()

    insert_params = {
        "household_id": str(household_id),
        "since_date": since_date,
        "total_count": total_count,
        "started_by": str(user.id),
        "request_id": payload.request_id,
    }
    try:
        # Savepoint: schlaegt das INSERT wegen eines zeitgleichen zweiten
        # Starts fehl, bleibt die umgebende Request-Transaktion benutzbar und
        # kann den bestehenden Lauf noch lesen.
        async with db.begin_nested():
            run_id = (
                await db.execute(
                    text(
                        "insert into classification_runs "
                        "(household_id, since_date, total_count, started_by, status, request_id) "
                        "values (:household_id, :since_date, :total_count, :started_by, "
                        "'pending', :request_id) returning id"
                    ),
                    insert_params,
                )
            ).scalar_one()
    except IntegrityError:
        # Zwei exakt gleichzeitige Starts: der partielle Unique-Index laesst
        # nur einen durch. Der Verlierer liefert den Gewinner zurueck.
        existing = await _select_run(
            db, household_id, f"status in {_ACTIVE_STATES}"
        ) or await _select_run(
            db, household_id, "request_id = :request_id", {"request_id": payload.request_id}
        )
        if existing is None:
            raise
        response.status_code = status.HTTP_200_OK
        return ClassificationRun(**existing._mapping)

    try:
        classify_batch_actor.send(str(run_id))
    except Exception as exc:
        # Wie beim Import (routes/imports.py): ohne erreichbaren Broker darf
        # kein Lauf zurueckbleiben, der fuer immer auf 'pending' steht und
        # zugleich jeden weiteren Start blockiert. Die Ausnahme rollt die
        # Request-Transaktion zurueck, der Lauf entsteht also gar nicht erst.
        logger.exception("KI-Lauf konnte nicht eingereiht werden (run_id=%s)", run_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Der KI-Lauf konnte nicht gestartet werden: der Verarbeitungsdienst "
                "(Redis/Worker) ist nicht erreichbar. Bitte den Dienst starten und "
                "erneut versuchen."
            ),
        ) from exc

    row = await _select_run(db, household_id, "id = :id", {"id": str(run_id)})
    assert row is not None  # gerade eingefuegt, dieselbe Transaktion
    return ClassificationRun(**row._mapping)


@router.get("/classify-runs", response_model=list[ClassificationRun])
async def list_classification_runs(
    session_ctx: HouseholdSession,
    run_status: Annotated[Literal["active", "all"], Query(alias="status")] = "all",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ClassificationRun]:
    """Laufhistorie, juengster zuerst. `status=active` liefert nur den (hoechstens
    einen) aktiven Lauf.

    Diese Liste ist der Grund, warum ein Lauf einen Seitenwechsel, einen Reload
    und einen App-Neustart ueberlebt: der Client haelt keine Run-ID mehr in
    lokalem State, sondern fragt mit `limit=1` einfach den juengsten Lauf des
    Haushalts ab."""
    db, household_id = session_ctx
    condition = f"status in {_ACTIVE_STATES}" if run_status == "active" else "true"
    res = await db.execute(
        text(
            f"select {_CLASSIFY_RUN_COLUMNS} from classification_runs "
            f"where household_id = :household_id and {condition} "
            "order by created_at desc, id desc limit :limit offset :offset"
        ),
        {
            "household_id": str(household_id),
            "stale_minutes": STALE_RUN_AFTER_MINUTES,
            "limit": limit,
            "offset": offset,
        },
    )
    return [ClassificationRun(**row._mapping) for row in res]


@router.get("/classify-runs/{run_id}", response_model=ClassificationRun)
async def get_classification_run(
    run_id: UUID, session_ctx: HouseholdSession
) -> ClassificationRun:
    db, household_id = session_ctx
    row = await _select_run(db, household_id, "id = :id", {"id": str(run_id)})
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="classification_run_not_found"
        )
    return ClassificationRun(**row._mapping)


@router.post("/classify-runs/{run_id}/cancel", response_model=ClassificationRun)
async def cancel_classification_run(
    run_id: UUID, session_ctx: HouseholdEditorSession
) -> ClassificationRun:
    """Bricht einen Lauf ab. Ohne diesen Endpunkt haette ein haengender Lauf
    den Haushalt bis zum Ablauf von STALE_RUN_AFTER_MINUTES blockiert.

    Ein noch nicht gestarteter oder haengender Lauf endet sofort als
    'cancelled'. Ein aktiv arbeitender Lauf bekommt nur cancel_requested = true
    gesetzt; der Worker beendet ihn zwischen zwei Buchungen sauber, damit keine
    halb geschriebene Klassifikation zurueckbleibt."""
    db, household_id = session_ctx
    row = await _select_run(db, household_id, "id = :id", {"id": str(run_id)})
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="classification_run_not_found"
        )

    if row.status in ("pending", "running"):
        if row.status == "pending" or row.is_stale:
            await _end_run(db, run_id, "Vom Nutzer abgebrochen.")
        else:
            await db.execute(
                text(
                    "update classification_runs set cancel_requested = true where id = :id"
                ),
                {"id": str(run_id)},
            )
        row = await _select_run(db, household_id, "id = :id", {"id": str(run_id)})
        assert row is not None

    # Bereits beendete Laeufe: unveraendert zurueckgeben (idempotent).
    return ClassificationRun(**row._mapping)
