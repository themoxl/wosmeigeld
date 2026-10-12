"""KI-Kostenschaetzung fuer Vorhaben: Freitext -> Schaetzwertvorschlaege.

Nachtrag zum Vorhaben-Plan nach Abschluss von P0-P5 (siehe
VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt "Nachtrag"). Der Nutzer
beschreibt sein Vorhaben in eigenen Worten ("Grundstueck geerbt, kein
Kaufpreis", "normaler Hauskauf, Altbau Baujahr 1985"); das lokale Modell
schlaegt daraus Schaetzwerte fuer die VORHANDENEN Kostenpositionen des
Szenarios vor (aus Vorlage oder manuell angelegt) - bewusst keine neuen
Positionen/Abschnitte, das waere ein zweiter, riskanterer Funktionsumfang als
"Werte fuer bekannte Kategorien schaetzen".

Optional wird die Beschreibung zusaetzlich als EINE Suchanfrage gegen die
selbstgehostete SearXNG-Instanz des Haushalts geschickt (siehe
`searxng_service.py`), um die Schaetzung mit realen Preisangaben zu erden -
keine mehrstufige "Modell erzeugt Suchanfragen"-Orchestrierung, das waere
fuer den Nutzen hier nicht robust genug zu rechtfertigen.

Dieselbe Vorschau/Anwenden-Mechanik wie Kategorienvorschlaege
(category_suggestion_service.py), aber eine eigene Tabelle
(project_cost_ai_operations): die Vorgaenge sind projekt-/szenariogebunden
statt baumweit, und `source_id`/`target_id`/`taxonomy_revision` aus
category_operations passen fachlich nicht. "KI schlaegt vor, Mensch
entscheidet" gilt genauso: nichts wird ohne ausdrueckliche Bestaetigung pro
Position uebernommen (Plan 13.8 "kein unbelegter KI-Text").

Modellaufruf (relatives KI-Budget, docs/KI-BUDGET-UND-KI-FELD-PLAN.md)
---------------------------------------------------------------------
EIN Aufruf ueber den Gateway des KI-Kerns (app/llm) mit dem ganzen freien
Platz des geladenen Fensters und der Reasoning-Einstellung des Haushalts.
Frueher war das zweigeteilt: ein Analyseaufruf mit gedeckeltem Memo
(hoechstens 9000 Token) und ein zweiter "Formatierer" mit erzwungenem
Reasoning aus (hoechstens 6000 Token) - Code-Vorgaben, die der Daueregel
"keine KI-Parameter im Code" widersprechen (E2). Steht das JSON nur im
Denktext derselben Antwort, wird es von dort genommen; einen zweiten Aufruf
gibt es nicht. Liefert das Modell nichts Verwertbares, steht der Grund des
Kerns in `error_code` (model_budget_exhausted, model_output_unusable,
model_timeout, model_input_too_large, ...) und sein Klartext in
`error_message`.

Was ins Fenster muss, wird geprueft statt gedeckelt: ALLE festen
Kostenpositionen des Szenarios (frueher hoechstens 60, der Rest fiel still
weg). Die Websuchtreffer sind Zusatzmaterial: passen Positionen und Treffer
nicht zusammen ins Fenster, gehen Treffer von hinten nach vorn heraus, und
`search_unavailable_reason` sagt das. Passen die Positionen allein nicht,
scheitert der Aufruf mit model_input_too_large samt Aufschluesselung - eine
Schaetzung in Stapeln waere fachlich keine (die Gesamtgroessenordnung ist
Teil der Frage).
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.llm import answers, reasons
from app.schemas.projects import (
    CostAiItemSuggestion,
    CostAiSearchSnippet,
    CostAiSuggestionApply,
    CostAiSuggestionPreview,
    CostAiSuggestionRequest,
    CostAiSuggestionResult,
    CostAiSuggestionResultItem,
)
from app.services import ai_settings_service, searxng_service
from app.services.audit_service import record_audit_event
from app.services.project_service import (
    PROJECT_COST_ITEM_ENTITY_TYPE,
    require_project,
    require_scenario,
)

COST_AI_OPERATION_NOT_FOUND: Final = "cost_ai_operation_not_found"
COST_AI_PREVIEW_STALE: Final = "cost_ai_preview_stale"
COST_AI_MODEL_UNAVAILABLE: Final = "ai_model_unavailable"
# v3: ein Aufruf statt Analyse + Formatierer; alle Positionen statt hoechstens 60.
COST_AI_PROMPT_VERSION: Final = "project-cost-v3"
COST_AI_STAGE: Final = "project_cost_estimate"

# Sanity-Obergrenze je Position (50 Mio. EUR) - schuetzt vor einer
# durchrutschenden Zahl mit falscher Groessenordnung, ohne echte Vorhaben
# (auch grosse Neubauten) einzuschraenken.
# fest: Plausibilitaetsgrenze eines Antwortwerts, kein Budget
_MAX_ESTIMATE_MINOR: Final = 50_000_000_00

# Begruendung je Position, wie sie gespeichert (ai_rationale) und angezeigt
# wird - Modellausgabe, kein Promptmaterial.
# fest: Anzeige- und Speichergrenze der Begruendung je Position
_MAX_RATIONALE_CHARS: Final = 400

CostAIStageCallback = Callable[[str, str], Awaitable[None]]
CostAIGenerationStatus = Literal[
    "success", "no_suggestions", "timeout", "invalid_response", "model_error"
]
Message = dict[str, Any]



class CostAIGenerationError(RuntimeError):
    """Kein verwertbarer Schaetzvorschlag. `code` ist ein Grund des KI-Kerns
    (app/llm/reasons.py), die Meldung sein Klartext samt Befund."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Alle festen Positionen des Szenarios. Kein LIMIT mehr: ob sie ins Fenster
# passen, prueft der Gateway (model_input_too_large statt still fehlender
# Positionen).
_CANDIDATE_ITEMS_SQL: Final = (
    "select ci.id, ci.name as item_name, ci.semantic_key, ci.estimate_manual_minor, "
    "cs.name as section_name "
    "from project_cost_items ci "
    "join project_cost_sections cs on cs.id = ci.section_id "
    "where ci.scenario_id = :scenario_id and ci.household_id = :household_id "
    "and ci.calculation_mode = 'fixed' "
    "order by cs.sort_order, ci.sort_order"
)


class _CandidateItem:
    def __init__(self, row: Any) -> None:
        self.id = UUID(str(row.id))
        self.item_name = str(row.item_name)
        self.section_name = str(row.section_name)
        self.semantic_key = row.semantic_key
        self.current_estimate_minor = int(row.estimate_manual_minor or 0)


async def _load_candidate_items(
    session: AsyncSession, household_id: UUID, scenario_id: UUID
) -> list[_CandidateItem]:
    result = await session.execute(
        text(_CANDIDATE_ITEMS_SQL),
        {"scenario_id": str(scenario_id), "household_id": str(household_id)},
    )
    return [_CandidateItem(row) for row in result]


# ---------------------------------------------------------------------------
# Modellaufruf
# ---------------------------------------------------------------------------


def _amount_eur(amount_minor: int) -> str:
    return f"{amount_minor / 100:.2f} EUR"


def build_cost_ai_system_prompt(
    project_type: str,
    country_code: str,
    region_code: str | None,
    municipality: str | None,
    description: str,
    search_snippets: list[CostAiSearchSnippet],
    reasoning_enabled: bool,
) -> str:
    """`reasoning_enabled` steuert zwei Anweisungen, die sich NICHT kombinieren
    lassen (direkt getestet, siehe Nachtrag im Plan, Abschnitt 23): der
    Gesamtsummen-Anker hilft ohne Reasoning (verankert die Groessenordnung vor
    der Verteilung), reisst das Modell mit Reasoning aber in eine echte
    Wiederholungsschleife ("Final check X again" - dieselbe Sequenz identisch
    wiederholt, ignoriert jede Stop-Anweisung). Die Anti-Nochmal-pruefen-
    Anweisung wiederum ist ohne Reasoning wirkungslos (es gibt kein
    `reasoning_content`, das sie stoppen koennte), aber mit Reasoning noetig,
    damit das Modell ueberhaupt zum JSON kommt statt das Ausgabebudget im
    Durchdenken zu verbrennen. Deshalb bekommt nur EINE der beiden Anweisungen
    den Zuschlag, je nachdem, ob Reasoning fuer diesen Aufruf aktiv ist."""
    location = ", ".join(
        part for part in (municipality, region_code, country_code) if part
    )
    lines = [
        "Du bist ein deutschsprachiger Finanzassistent fuer private "
        "Haushalte und hilfst bei der Kostenschaetzung eines Vorhabens.",
        f"Vorhabenart: {project_type}",
        f"Ort: {location}" if location else "Ort: nicht angegeben",
        "Beschreibung des Nutzers:",
        description.strip(),
    ]
    if search_snippets:
        lines.append("Ergaenzende Websuchergebnisse (koennen veraltet oder ungenau sein):")
        for snippet in search_snippets:
            lines.append(f"- {snippet.title}: {snippet.snippet} ({snippet.url})")
    lines.append(
        "Aufgabe: Schaetze fuer jede genannte Kostenposition einen realistischen "
        "Betrag in Cent (estimate_minor) fuer ein privates Vorhaben in "
        "Deutschland, gestuetzt auf die Beschreibung und - falls vorhanden - die "
        "Websuchergebnisse. Wenn die Beschreibung eine Position ausdruecklich "
        "ausschliesst oder als kostenlos benennt (z. B. ein geerbtes, bereits "
        "bezahltes Grundstueck), schaetze 0 mit entsprechender Begruendung. "
        "Wenn du fuer eine Position keine begruendbare Schaetzung abgeben "
        "kannst, lass sie in deiner Antwort einfach weg statt zu raten."
    )
    if reasoning_enabled:
        lines.append(
            "Ueberlege jede Position genau EINMAL und entscheide dich sofort - "
            "pruefe eine bereits entschiedene Position NICHT ein zweites Mal "
            "nach (kein 'Moment, ich pruefe X nochmal' oder 'Final check X "
            "again'). Sobald du fuer jede Position einen Wert hast, hoere auf "
            "zu ueberlegen und gib sofort ausschliesslich das geforderte "
            "JSON-Objekt aus."
        )
    else:
        lines.append(
            "Schaetze zuerst die realistische GESAMTSUMME aller Positionen "
            "dieses Vorhabens (total_estimate_minor) in Cent, BEVOR du die "
            "einzelnen Positionen festlegst. Die Einzelwerte muessen sich in "
            "der Summe sinnvoll zu dieser Gesamtsumme addieren - keine "
            "einzelne Position darf allein schon groesser als die Gesamtsumme "
            "sein. Antworte ausschliesslich mit dem geforderten JSON-Objekt."
        )
    return "\n".join(lines)


def build_cost_ai_user_prompt(items: list[_CandidateItem]) -> str:
    lines = ["Zu schaetzende Kostenpositionen (ID | Abschnitt | Position | aktueller Wert):"]
    for item in items:
        lines.append(
            f"- {item.id} | {item.section_name} | {item.item_name} | "
            f"{_amount_eur(item.current_estimate_minor)}"
        )
    return "\n".join(lines)


def build_cost_ai_json_schema(valid_item_ids: set[str], reasoning_enabled: bool) -> dict[str, Any]:
    """Ohne Reasoning bekommt das Schema `total_estimate_minor` als erste,
    verpflichtende Eigenschaft vor `items`: bei schema-gefuehrter Generierung
    zwingt das das Modell, sich zuerst auf eine grobe Gesamtgroessenordnung
    festzulegen, bevor es einzelne Positionen verteilt. Ohne diesen Anker sind
    Einzelwerte eines lokalen Modells ueber mehrere Laeufe hinweg um bis zu das
    20-fache verrutscht (z. B. Rohbau abwechselnd bei 160.000 EUR und
    3.500.000 EUR fuer dasselbe Haus); mit dem Anker blieben dieselben
    Positionen ueber drei Wiederholungslaeufe im selben plausiblen
    Groessenbereich.

    MIT Reasoning entfaellt das Feld bewusst: derselbe Anker gibt dem Modell
    beim lauten Denken einen zusaetzlichen Punkt zum "nochmal pruefen" und
    kippt es direkt getestet in eine echte Wiederholungsschleife (identische
    "Final check X again"-Sequenz endlos wiederholt, ignoriert jede
    Stop-Anweisung) - siehe Nachtrag im Plan, Abschnitt 23."""
    properties: dict[str, Any] = {}
    required = []
    if not reasoning_enabled:
        properties["total_estimate_minor"] = {"type": "integer"}
        required.append("total_estimate_minor")
    properties["items"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "enum": sorted(valid_item_ids)},
                "estimate_minor": {"type": "integer"},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
            },
            "required": ["item_id", "estimate_minor", "confidence", "rationale"],
            "additionalProperties": False,
        },
    }
    required.append("items")
    return {
        "name": "project_cost_estimate",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def _answer_object(parsed: dict[str, Any]) -> dict[str, Any] | None:
    """Das Antwortobjekt nach Vertrag: ein JSON-Objekt mit einer Liste `items`
    (auch leer - "keine begruendbare Schaetzung" ist eine gueltige Antwort)."""
    return parsed if isinstance(parsed.get("items"), list) else None


def build_cost_ai_messages(system_prompt: str, user_prompt: str) -> list[Message]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _fit_search_snippets(
    messages_with: Callable[[list[CostAiSearchSnippet]], list[Message]],
    snippets: list[CostAiSearchSnippet],
    *,
    target: llm.Target,
    settings: llm.LLMSettings,
    measurement: llm.Measurement | None,
) -> tuple[list[Message], list[CostAiSearchSnippet]]:
    """Websuchtreffer nach Passung statt nach fester Anzahl.

    Die Kostenpositionen sind die Frage selbst und gehen immer mit; die Treffer
    sind Zusatzmaterial. Passt beides zusammen nicht samt Antwortreserve ins
    geladene Fenster, fallen Treffer von hinten nach vorn weg (SearXNG liefert
    die relevantesten zuerst). Ohne bekanntes Fenster oder bei ungueltigen
    Einstellungen wird nichts weggelassen - der Gateway nennt dann den Grund
    (model_context_unknown, model_settings_invalid)."""
    used = list(snippets)
    if measurement is None or not target.window.known:
        return messages_with(used), used
    while used:
        messages = messages_with(used)
        try:
            budget = llm.plan_budget(
                messages, window=target.window, settings=settings, measurement=measurement
            )
        except llm.BudgetInputError:
            return messages, used
        if budget.fits:
            return messages, used
        used.pop()
    return messages_with([]), []


async def _ask_model(
    messages: list[Message],
    json_schema: dict[str, Any],
    *,
    target: llm.Target,
    settings: llm.LLMSettings,
    scope: llm.CallScope,
    measurement: llm.Measurement | None = None,
    compact: llm.CompactHook | None = None,
) -> dict[str, Any]:
    """EIN Modellaufruf ueber den Gateway - Antwortbudget aus dem geladenen
    Fenster, Reasoning und Sampling aus der Haushaltseinstellung.

    Liefert das Antwortobjekt oder wirft CostAIGenerationError mit dem Grund
    des Kerns. Die Antwort prueft llm.answers.ask (abgeschnitten, JSON aus
    content oder dem Denktext derselben Antwort, Protokollzeile markieren);
    einen zweiten ("Formatierer"-)Aufruf gibt es nicht (E2)."""
    answer = await answers.ask(
        answers.Ready(settings, target, scope),
        llm.ChatRequest(
            messages=messages,
            response_format={"type": "json_schema", "json_schema": json_schema},
        ),
        accept=_answer_object,
        breach="Die Antwort enthielt kein JSON-Objekt mit einer Liste „items“.",
        hooks=llm.ChatHooks(compact=compact) if compact is not None else None,
        measurement=measurement,
    )
    if answer.value is not None:
        return answer.value
    code = answer.reason_code or reasons.MODEL_OUTPUT_UNUSABLE
    raise CostAIGenerationError(code, answer.message or reasons.describe(code))


def _suggestions_from_answer(
    items: list[_CandidateItem], answer: dict[str, Any] | None
) -> list[CostAiItemSuggestion]:
    by_id: dict[str, dict[str, Any]] = {}
    if answer is not None:
        for entry in answer.get("items") or []:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("item_id") or "")
            if item_id and item_id not in by_id:
                by_id[item_id] = entry

    suggestions: list[CostAiItemSuggestion] = []
    for item in items:
        entry = by_id.get(str(item.id))
        proposed: int | None = None
        confidence = 0.0
        rationale = ""
        if entry is not None:
            try:
                proposed = max(0, min(_MAX_ESTIMATE_MINOR, int(entry.get("estimate_minor", 0))))
            except (TypeError, ValueError):
                proposed = None
            try:
                confidence = min(1.0, max(0.0, float(entry.get("confidence", 0.0))))
            except (TypeError, ValueError):
                confidence = 0.0
            rationale = str(entry.get("rationale") or "").strip()[:_MAX_RATIONALE_CHARS]
        suggestions.append(
            CostAiItemSuggestion(
                cost_item_id=item.id,
                section_name=item.section_name,
                item_name=item.item_name,
                current_estimate_minor=item.current_estimate_minor,
                proposed_estimate_minor=proposed,
                confidence=confidence,
                rationale=rationale,
            )
        )
    return suggestions


def _generation_status(code: str) -> CostAIGenerationStatus:
    """Grund des Kerns -> technischer Ausgang der Vorschau (Vertrag der
    Oberflaeche: timeout/invalid_response/model_error sind Fehler)."""
    if code == reasons.MODEL_TIMEOUT:
        return "timeout"
    if code in (reasons.MODEL_OUTPUT_UNUSABLE, reasons.MODEL_BUDGET_EXHAUSTED):
        return "invalid_response"
    return "model_error"


def _fingerprint(impact: dict[str, Any]) -> str:
    canonical = json.dumps(impact, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Vorschau
# ---------------------------------------------------------------------------


async def build_preview(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    scenario_id: UUID,
    actor_user_id: UUID | None,
    payload: CostAiSuggestionRequest,
    *,
    ai_task_id: UUID | None = None,
    stage_callback: CostAIStageCallback | None = None,
) -> CostAiSuggestionPreview:
    project = await require_project(session, household_id, project_id)
    await require_scenario(session, household_id, project_id, scenario_id)

    items = await _load_candidate_items(session, household_id, scenario_id)

    model_name: str | None = None
    search_performed = False
    search_unavailable_reason: str | None = None
    search_snippets: list[CostAiSearchSnippet] = []
    suggestions: list[CostAiItemSuggestion] = []
    generation_status: CostAIGenerationStatus = "no_suggestions"
    error_code: str | None = None
    error_message: str | None = None
    answer: dict[str, Any] | None = None

    settings = await ai_settings_service.resolve(session, household_id) if items else None
    found = await llm.resolve_target(settings) if settings is not None else None
    if found is not None and found.reason_code == reasons.AI_DISABLED:
        # KI im Haushalt aus: weder suchen noch fragen - benannt statt einer
        # leeren Vorschlagsliste, die wie "nichts zu schaetzen" aussaehe.
        generation_status = "model_error"
        error_code = reasons.AI_DISABLED
        error_message = found.message or reasons.describe(reasons.AI_DISABLED)
    elif settings is not None and found is not None:
        if found.target is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=COST_AI_MODEL_UNAVAILABLE,
            )
        # Ein unbekanntes Fenster (model_context_unknown) laesst der Gateway
        # mit benanntem Grund und llm_calls-Zeile scheitern.
        target = found.target
        model_name = target.model

        if payload.use_web_search:
            searxng_url = settings.searxng_url()
            if searxng_url is None:
                search_unavailable_reason = (
                    "Keine SearXNG-Adresse hinterlegt (siehe KI-Einstellungen)."
                )
            else:
                results = await searxng_service.search(searxng_url, payload.description)
                search_performed = True
                search_snippets = [
                    CostAiSearchSnippet(title=r.title, url=r.url, snippet=r.snippet)
                    for r in results
                ]

        # Die Einstellung bestimmt nur, welche der beiden Prompt-Fassungen
        # gilt (siehe build_cost_ai_system_prompt) - gesendet wird sie
        # unveraendert vom Gateway.
        reasoning_enabled = bool(settings.reasoning) and settings.reasoning != "off"
        scope = llm.CallScope(
            session=session,
            household_id=household_id,
            stage=COST_AI_STAGE,
            prompt_version=(
                f"{COST_AI_PROMPT_VERSION}-reasoning-{settings.reasoning or 'model-default'}"
            ),
            ai_task_id=ai_task_id,
        )
        user_prompt = build_cost_ai_user_prompt(items)

        def messages_with(snippets: list[CostAiSearchSnippet]) -> list[Message]:
            return build_cost_ai_messages(
                build_cost_ai_system_prompt(
                    project.project_type,
                    project.country_code,
                    project.region_code,
                    project.municipality,
                    payload.description,
                    snippets,
                    reasoning_enabled,
                ),
                user_prompt,
            )

        measurement = (
            await llm.measurement_for(target, settings, scope) if target.window.known else None
        )
        messages, sent = _fit_search_snippets(
            messages_with,
            search_snippets,
            target=target,
            settings=settings,
            measurement=measurement,
        )

        async def without_snippets(
            current: list[Message], budget: llm.Budget
        ) -> list[Message] | None:
            # Meldet der Server trotz Rechnung einen Ueberlauf, gehen auch die
            # restlichen Treffer heraus - einmal, die Positionen bleiben.
            del current, budget
            if not sent:
                return None
            sent.clear()
            return messages_with([])

        if stage_callback is not None:
            await stage_callback(
                "cost_estimate",
                f"{len(items)} Kostenposition(en) werden in einem Modellaufruf geschätzt "
                f"(Reasoning: {settings.reasoning or 'Modellstandard'})",
            )
        try:
            answer = await _ask_model(
                messages,
                build_cost_ai_json_schema({str(item.id) for item in items}, reasoning_enabled),
                target=target,
                settings=settings,
                scope=scope,
                measurement=measurement,
                compact=without_snippets,
            )
        except CostAIGenerationError as exc:
            generation_status = _generation_status(exc.code)
            error_code = exc.code
            error_message = str(exc)

        if len(sent) < len(search_snippets):
            search_unavailable_reason = (
                f"{len(search_snippets) - len(sent)} von {len(search_snippets)} "
                "Websuchtreffern passten nicht mehr samt Antwortreserve ins Kontextfenster "
                "des Modells und wurden nicht mitgesendet."
            )
            if stage_callback is not None:
                await stage_callback("cost_search_fit", search_unavailable_reason)
        # Angezeigt wird, was die Schaetzung tatsaechlich gesehen hat.
        search_snippets = list(sent)

    # Positionen ohne verwertbaren Vorschlag bleiben mit
    # proposed_estimate_minor=None sichtbar - auch bei einem Fehlschlag.
    suggestions = _suggestions_from_answer(items, answer)
    if error_code is None and items:
        generation_status = (
            "success"
            if any(item.proposed_estimate_minor is not None for item in suggestions)
            else "no_suggestions"
        )

    impact: dict[str, Any] = {
        "description": payload.description,
        "use_web_search": payload.use_web_search,
        "search_performed": search_performed,
        "model_name": model_name,
        "generation_status": generation_status,
        "error_code": error_code,
        "error_message": error_message,
        "suggestions": [item.model_dump(mode="json") for item in suggestions],
    }

    result = await session.execute(
        text(
            "insert into project_cost_ai_operations "
            "(household_id, project_id, scenario_id, status, created_by, fingerprint, "
            "model_name, generation_status, error_code, error_message, impact, ai_task_id) "
            "values (:household_id, :project_id, :scenario_id, :status, :created_by, "
            ":fingerprint, :model_name, :generation_status, :error_code, :error_message, "
            "cast(:impact as jsonb), :ai_task_id) returning id, expires_at"
        ),
        {
            "household_id": str(household_id),
            "project_id": str(project_id),
            "scenario_id": str(scenario_id),
            "status": "failed" if error_code else "preview",
            "created_by": str(actor_user_id) if actor_user_id else None,
            "fingerprint": _fingerprint(impact),
            "model_name": model_name,
            "generation_status": generation_status,
            "error_code": error_code,
            "error_message": error_message,
            "impact": json.dumps(impact, ensure_ascii=False),
            "ai_task_id": str(ai_task_id) if ai_task_id else None,
        },
    )
    row = result.one()

    return CostAiSuggestionPreview(
        operation_id=UUID(str(row.id)),
        expires_at=row.expires_at,
        scenario_id=scenario_id,
        model_name=model_name,
        search_requested=payload.use_web_search,
        search_performed=search_performed,
        search_unavailable_reason=search_unavailable_reason,
        search_snippets=search_snippets,
        generation_status=generation_status,
        error_code=error_code,
        error_message=error_message,
        suggestions=suggestions,
    )


# ---------------------------------------------------------------------------
# Anwenden
# ---------------------------------------------------------------------------


async def _find_applied_result(
    session: AsyncSession, household_id: UUID, scenario_id: UUID, request_id: str
) -> CostAiSuggestionResult | None:
    result = await session.execute(
        text(
            "select result from project_cost_ai_operations "
            "where household_id = :household_id and scenario_id = :scenario_id "
            "and request_id = :request_id and status = 'applied'"
        ),
        {
            "household_id": str(household_id),
            "scenario_id": str(scenario_id),
            "request_id": request_id,
        },
    )
    row = result.first()
    if row is None or row.result is None:
        return None
    stored = dict(row.result)
    stored["idempotent_replay"] = True
    return CostAiSuggestionResult(**stored)


async def apply(
    session: AsyncSession,
    household_id: UUID,
    project_id: UUID,
    scenario_id: UUID,
    actor_user_id: UUID | None,
    payload: CostAiSuggestionApply,
) -> CostAiSuggestionResult:
    await require_project(session, household_id, project_id)
    await require_scenario(session, household_id, project_id, scenario_id)

    if payload.request_id is not None:
        replay = await _find_applied_result(session, household_id, scenario_id, payload.request_id)
        if replay is not None:
            return replay

    operation = await session.execute(
        text(
            "select id, status, expires_at, impact from project_cost_ai_operations "
            "where id = :id and household_id = :household_id and project_id = :project_id "
            "and scenario_id = :scenario_id for update"
        ),
        {
            "id": str(payload.operation_id),
            "household_id": str(household_id),
            "project_id": str(project_id),
            "scenario_id": str(scenario_id),
        },
    )
    row = operation.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=COST_AI_OPERATION_NOT_FOUND
        )
    if row.status != "preview" or row.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=COST_AI_PREVIEW_STALE)

    impact = dict(row.impact)
    stored: dict[str, dict[str, Any]] = {
        str(item["cost_item_id"]): item for item in impact.get("suggestions", [])
    }

    items: list[CostAiSuggestionResultItem] = []
    updated_count = 0

    for accept in payload.accepted:
        suggestion = stored.get(str(accept.cost_item_id))
        if suggestion is None or suggestion.get("proposed_estimate_minor") is None:
            items.append(
                CostAiSuggestionResultItem(
                    cost_item_id=accept.cost_item_id,
                    applied=False,
                    skipped_reason="not_in_preview",
                )
            )
            continue

        current = (
            await session.execute(
                text(
                    "select id, calculation_mode, scenario_id, estimate_manual_minor, "
                    "estimate_source, ai_operation_id, ai_confidence, ai_rationale "
                    "from project_cost_items where id = :id and household_id = :household_id"
                ),
                {"id": str(accept.cost_item_id), "household_id": str(household_id)},
            )
        ).first()
        if current is None or str(current.scenario_id) != str(scenario_id):
            items.append(
                CostAiSuggestionResultItem(
                    cost_item_id=accept.cost_item_id, applied=False, skipped_reason="item_not_found"
                )
            )
            continue
        if current.calculation_mode != "fixed":
            items.append(
                CostAiSuggestionResultItem(
                    cost_item_id=accept.cost_item_id,
                    applied=False,
                    skipped_reason="calculation_mode_changed",
                )
            )
            continue

        confidence = suggestion.get("confidence")
        rationale = suggestion.get("rationale")
        await session.execute(
            text(
                "update project_cost_items set estimate_manual_minor = :estimate_minor, "
                "estimate_source = 'ai', ai_operation_id = :operation_id, "
                "ai_confidence = :confidence, ai_rationale = :rationale "
                "where id = :id and household_id = :household_id"
            ),
            {
                "estimate_minor": accept.estimate_minor,
                "operation_id": str(payload.operation_id),
                "confidence": confidence,
                "rationale": rationale,
                "id": str(accept.cost_item_id),
                "household_id": str(household_id),
            },
        )
        await record_audit_event(
            session,
            household_id,
            actor_user_id,
            PROJECT_COST_ITEM_ENTITY_TYPE,
            accept.cost_item_id,
            "update",
            before={
                "estimate_manual_minor": current.estimate_manual_minor,
                "estimate_source": current.estimate_source,
            },
            after={
                "estimate_manual_minor": accept.estimate_minor,
                "estimate_source": "ai",
                "ai_operation_id": str(payload.operation_id),
            },
        )
        updated_count += 1
        items.append(
            CostAiSuggestionResultItem(cost_item_id=accept.cost_item_id, applied=True)
        )

    result = CostAiSuggestionResult(updated_count=updated_count, items=items)

    await session.execute(
        text(
            "update project_cost_ai_operations set status = 'applied', applied_at = now(), "
            "request_id = coalesce(:request_id, request_id), result = cast(:result as jsonb) "
            "where id = :id and household_id = :household_id"
        ),
        {
            "request_id": payload.request_id,
            "result": json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
            "id": str(payload.operation_id),
            "household_id": str(household_id),
        },
    )
    return result
