"""Klassifikationsvertrag fuer Buchungen - EINE Fassung fuer API und Worker.

Bis Etappe 0 stand dieser Vertrag zweimal im Repo (app/services/ai_service.py
und worker/ai/classifier.py, "Zwillinge, wortgleich halten"). Jetzt steht er
hier; die API (Einzelklassifikation, Kanarienvogel im Status) und der Worker
(Batch-Lauf) rufen ihn nur noch auf.

Inhalt:
* Prompt-/Schema-Vertrag (Systemprompt mit Kandidatenliste, Nutzerprompt aus
  maskierten Feldern, JSON-Schema mit no_match-Sentinel, Parser).
* Kandidatenliste, die sich an das FENSTER anpasst statt an eine feste
  Zeichengrenze: voll -> ohne Beschreibungen -> ohne Synonyme ->
  model_input_too_large. Kandidaten selbst fallen nie weg.
* Kanarienvogel (run_canary) und Einzelklassifikation (classify_one) ueber den
  Gateway - mit Budget aus dem geladenen Fenster, ohne Eskalation.
"""

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import reasons
from app.llm.budget import BudgetInputError
from app.llm.calls_log import CallScope, update_call_outcome
from app.llm.discovery import Target
from app.llm.gateway import ChatRequest, ChatResult, chat, measurement_for, plan_budget
from app.llm.measurements import Measurement
from app.llm.settings import LLMSettings

# Promptversion des Klassifikationsvertrags. 'v2' = strukturierte Ausgabe per
# response_format/json_schema (v1 war reiner Prompttext + Regex-Extraktion).
PROMPT_VERSION: Final = "v2"


def prompt_version_for(taxonomy_version: int | None) -> str:
    """Promptversion fuer llm_calls: Vertragsversion PLUS Taxonomieversion des
    Haushalts, z. B. 'v2-tax1'. tax0 = Haushalt ohne installiertes
    Standardpaket. ai_model_capabilities bleibt bewusst auf PROMPT_VERSION:
    ob ein Modell json_schema beherrscht, haengt am Vertrag, nicht an den
    Kategorien."""
    return f"{PROMPT_VERSION}-tax{taxonomy_version if taxonomy_version is not None else 0}"


# Sentinel statt eines JSON-null-Typs: 'strict' json_schema mit
# ("string" | "null") wird von den Grammar-Backends lokaler Server nur
# unvollstaendig unterstuetzt; ein zusaetzlicher Enum-Wert funktioniert ueberall.
NO_MATCH: Final = "no_match"

# Reason Codes, die NICHT vom Modell stammen, sondern vom Vertrag gesetzt
# werden (stehen so in classification_decisions).
NO_MATCH_REASON_CODE: Final = "no_matching_category"
STALE_CATEGORY_REASON_CODE: Final = "category_no_longer_active"
DEFAULT_REASON_CODE: Final = "merchant_and_context_match"

# Rueckgabecodes eines gescheiterten Modellaufrufs, die es schon vor dem
# relativen Budget gab - unveraendert, damit bestehende Auswertungen und der
# Notaus im Worker sie weiter verstehen.
LLM_TIMEOUT: Final = "llm_timeout"
LLM_SCHEMA_INVALID: Final = "llm_schema_invalid"
LLM_EXECUTION_ERROR: Final = "llm_execution_error"

# Alle Codes, mit denen classify_one() einen KAPUTTEN Modellaufruf meldet. Der
# Notaus des Worker-Laufs (worker/ai/processor.py) zaehlt genau diese - no_match
# und eine inzwischen archivierte Kategorie gehoeren ausdruecklich nicht dazu.
FAILURE_REASON_CODES: Final = frozenset(
    {
        LLM_TIMEOUT,
        LLM_SCHEMA_INVALID,
        LLM_EXECUTION_ERROR,
        reasons.AI_DISABLED,
        reasons.MODEL_BUDGET_EXHAUSTED,
        reasons.MODEL_CONTEXT_OVERFLOW,
        reasons.MODEL_CONTEXT_UNKNOWN,
        reasons.MODEL_INPUT_TOO_LARGE,
        reasons.MODEL_REASONING_UNSUPPORTED,
        reasons.MODEL_SETTINGS_INVALID,
    }
)

# fest: genau ein kontrollierter Korrekturversuch nach einem Schemabruch (Vertrag)
MAX_SCHEMA_ATTEMPTS: Final = 2

SCHEMA_RETRY_HINT: Final = (
    "\n\nHINWEIS: Deine vorherige Antwort war ungueltig (fehlerhaftes JSON oder "
    'eine category_id, die weder in der Kandidatenliste steht noch "no_match" '
    "ist). Antworte erneut EXAKT im geforderten JSON-Format. Wenn keine "
    'Kategorie passt, ist "no_match" die richtige Antwort - erfinde keine ID '
    "und weiche nicht auf eine Sammelkategorie aus."
)

# Synthetische Kategorie fuer den Kanarienvogel: er klassifiziert eine
# Beispielbuchung mit exakt derselben Prompt-/Schema-Mechanik wie der
# Produktivpfad - HTTP 200 allein besteht ihn nicht.
CANARY_CATEGORY_ID: Final = "00000000-0000-4000-8000-000000000001"
CANARY_CATEGORIES: Final[tuple[dict[str, Any], ...]] = (
    {
        "id": CANARY_CATEGORY_ID,
        "path": "Variable Kosten · Lebensmittel",
        "key": "groceries",
        "type": "expense",
        "bucket": "needs",
        "synonyms": ["Supermarkt", "Discounter", "Wocheneinkauf"],
    },
)
CANARY_VALID_IDS: Final = frozenset({CANARY_CATEGORY_ID})
CANARY_TAXONOMY_VERSION: Final = 1


class ClassificationSchemaError(Exception):
    """Modell-Output verletzt den Vertrag (kein JSON, fehlendes Feld,
    unbekannte category_id). Wiederholbar - im Gegensatz zu Transportfehlern."""


def mask_pii_text(text_input: str | None) -> str:
    """Maskiert PII (IBANs, Kontonummern) vor der Uebergabe an das LLM."""
    if not text_input:
        return ""
    # IBANs maskieren (z. B. DE12345678901234567890 -> DE12***7890)
    masked = re.sub(
        r"([A-Z]{2}\d{2})\d{12,26}(\d{4})",
        r"\1************\2",
        text_input,
        flags=re.IGNORECASE,
    )
    # Kontonummern/Karten (10+ Ziffern)
    return re.sub(r"\b\d{10,16}\b", "*******[MASKED]", masked)


# ---------------------------------------------------------------------------
# Kandidatenliste
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryCandidates:
    """Alles, was ein Prompt ueber die erlaubten Kategorien wissen muss.

    entries   die volle Liste (mit Beschreibungen und Synonymen)
    variants  Fassungen vom vollstaendigsten zum knappsten: voll, ohne
              Beschreibungen, ohne Synonyme. Welche gesendet wird,
              entscheidet das Fenster (select_variant).
    valid_ids und taxonomy_version duerfen nie auseinanderlaufen - deshalb
    ein Objekt."""

    entries: list[dict[str, Any]]
    valid_ids: set[str]
    taxonomy_version: int | None
    variants: tuple[list[dict[str, Any]], ...] = ()

    def all_variants(self) -> tuple[list[dict[str, Any]], ...]:
        return self.variants or (self.entries,)


# Archivierte Kategorie UND archivierte Gruppe ausschliessen; die Sortierung
# haelt den Prompt zwischen zwei Laeufen stabil. Der Worker verbindet sich
# ohne RLS - der Haushaltsfilter steht deshalb fuer Kategorie und Gruppe
# ausdruecklich im Statement.
CANDIDATE_SQL: Final = (
    "select c.id, c.name, c.semantic_key, c.description, c.synonyms, "
    "c.default_type, c.budget_bucket, cg.name as group_name, "
    "(select t.taxonomy_version from household_category_taxonomy t "
    "where t.household_id = :household_id) as taxonomy_version "
    "from categories c "
    "join category_groups cg on cg.id = c.group_id "
    "and cg.household_id = c.household_id "
    "where c.household_id = :household_id "
    "and not c.is_archived and not cg.is_archived "
    "order by cg.sort_order, cg.name, c.sort_order, c.name"
)


def candidate_entry(
    row: Any, *, with_description: bool, with_synonyms: bool = True
) -> dict[str, Any]:
    """Ein Kandidat fuer den Prompt. Leere Felder werden weggelassen statt
    null zu senden."""
    entry: dict[str, Any] = {"id": str(row.id), "path": f"{row.group_name} · {row.name}"}
    if row.semantic_key:
        entry["key"] = row.semantic_key
    if row.default_type:
        entry["type"] = row.default_type
    if row.budget_bucket:
        entry["bucket"] = row.budget_bucket
    synonyms = [str(value) for value in (row.synonyms or [])]
    if with_synonyms and synonyms:
        entry["synonyms"] = synonyms
    if with_description and row.description:
        entry["description"] = str(row.description)
    return entry


def build_candidate_variants(rows: Sequence[Any]) -> tuple[list[dict[str, Any]], ...]:
    """Die drei Fassungen der Kandidatenliste. Zuerst faellt die Beschreibung
    weg (Fliesstext, wiederholt meist den Namen), dann die Synonyme; Pfad,
    semantischer Schluessel, Typ und Budgetbereich bleiben immer."""
    return (
        [candidate_entry(row, with_description=True) for row in rows],
        [candidate_entry(row, with_description=False) for row in rows],
        [candidate_entry(row, with_description=False, with_synonyms=False) for row in rows],
    )


def build_candidate_entries(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Die volle Fassung der Kandidatenliste."""
    return build_candidate_variants(rows)[0]


def candidates_from_rows(rows: Sequence[Any]) -> CategoryCandidates:
    variants = build_candidate_variants(rows)
    taxonomy_version = rows[0].taxonomy_version if rows else None
    return CategoryCandidates(
        entries=variants[0],
        valid_ids={entry["id"] for entry in variants[0]},
        taxonomy_version=int(taxonomy_version) if taxonomy_version is not None else None,
        variants=variants,
    )


async def load_categories(session: AsyncSession, household_id: UUID) -> CategoryCandidates:
    """Erlaubte Kategorien samt Taxonomieversion (eine Abfrage)."""
    rows = list(await session.execute(text(CANDIDATE_SQL), {"household_id": str(household_id)}))
    return candidates_from_rows(rows)


# ---------------------------------------------------------------------------
# Prompt-/Schema-Vertrag
# ---------------------------------------------------------------------------


def build_system_prompt(
    categories: Sequence[dict[str, Any]], taxonomy_version: int | None = None
) -> str:
    """Systemprompt mit den erlaubten Kategorien (Leitplanke 8: "KI darf
    vorschlagen, nicht erfinden"). Die JSON-Struktur steht ausdruecklich drin,
    weil sie auf dem Prompt+Parse-Pfad die einzige Vorgabe ist."""
    return (
        "Du bist ein Finanz-Klassifikator fuer eine Haushaltsbuch-App.\n"
        "Waehle fuer die angegebene Buchung GENAU eine Kategorie-ID aus der Liste der "
        f'erlaubten Kategorien - oder "{NO_MATCH}", wenn keine davon wirklich passt.\n\n'
        "Regeln:\n"
        "1. Nur IDs aus der Liste sind erlaubt. Erfinde niemals eine ID und niemals "
        "eine neue Kategorie.\n"
        f'2. "{NO_MATCH}" ist eine richtige und ausdruecklich erwartete Antwort. Wenn '
        "keine Kategorie eindeutig passt, antworte so - raten ist falsch.\n"
        "3. Eine Freizeit-, Sonstiges- oder aehnliche Sammelkategorie ist KEIN "
        "Auffangbecken fuer unsichere Treffer. Waehle sie nur, wenn die Buchung "
        f'fachlich genau dorthin gehoert, sonst "{NO_MATCH}".\n'
        "4. Gleiche mit Pfad (Gruppe · Kategorie), Synonymen, semantischem Schluessel "
        "(key) und Beschreibung ab. type = income/expense/transfer, bucket = "
        "needs/wants/savings dienen als Plausibilitaetspruefung gegen den Betrag.\n"
        "5. confidence ist deine Sicherheit zwischen 0 und 1; eine niedrige "
        f'confidence rechtfertigt keinen erzwungenen Treffer, sondern "{NO_MATCH}".\n'
        "6. reason_code ist ein kurzer Schluessel ohne Gedankengang.\n\n"
        "Antworte AUSSCHLIESSLICH als gueltiges JSON-Objekt ohne weiteren Text mit "
        "folgender Struktur:\n"
        "{\n"
        '  "payee_name": "normalisierter Händlername",\n'
        f'  "category_id": "gueltige-category-uuid" oder "{NO_MATCH}",\n'
        '  "confidence": 0.85,\n'
        '  "reason_code": "merchant_and_context_match"\n'
        "}\n\n"
        f"Taxonomieversion des Haushalts: "
        f"{taxonomy_version if taxonomy_version is not None else 'keine (eigene Kategorien)'}\n"
        "Erlaubte Kategorien (JSON):\n"
        f"{json.dumps(list(categories), ensure_ascii=False)}"
    )


def build_user_prompt(clean_payee: str, clean_purpose: str, amount_minor: int) -> str:
    """Userprompt aus BEREITS maskierten Feldern (mask_pii_text davor)."""
    return (
        f"Buchung: Empfaenger/Haendler: '{clean_payee}', "
        f"Verwendungszweck: '{clean_purpose}', Betrag_Cent: {amount_minor}"
    )


def build_classification_json_schema(valid_cat_ids: set[str] | frozenset[str]) -> dict[str, Any]:
    """JSON-Schema fuer response_format: category_id als enum ueber die
    erlaubten IDs PLUS no_match (deterministisch sortiert, Sentinel am Ende)."""
    return {
        "name": "transaction_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "payee_name": {"type": "string"},
                "category_id": {"type": "string", "enum": [*sorted(valid_cat_ids), NO_MATCH]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason_code": {"type": "string"},
            },
            "required": ["payee_name", "category_id", "confidence", "reason_code"],
            "additionalProperties": False,
        },
    }


def response_format_for(valid_cat_ids: set[str] | frozenset[str]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": build_classification_json_schema(valid_cat_ids)}


def classification_messages(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_classification_content(
    content: str, valid_cat_ids: set[str] | frozenset[str]
) -> dict[str, Any]:
    """Validiert den Modell-Output gegen den Vertrag; no_match (auch als
    JSON-null oder Leerstring) wird auf das Sentinel normalisiert. Eine
    unbekannte category_id bleibt ein Schemafehler - KI darf NIEMALS eine
    unbekannte Kategorie schreiben."""
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if not json_match:
        raise ClassificationSchemaError("Kein gueltiges JSON im Modell-Output gefunden.")
    try:
        parsed = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        raise ClassificationSchemaError(f"JSON-Parsing fehlgeschlagen: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ClassificationSchemaError("Modell-Output ist kein JSON-Objekt.")
    if "category_id" not in parsed:
        raise ClassificationSchemaError("Feld category_id fehlt im Modell-Output.")
    raw_cat_id = parsed["category_id"]
    if raw_cat_id is None or (isinstance(raw_cat_id, str) and raw_cat_id.strip() in ("", NO_MATCH)):
        parsed["category_id"] = NO_MATCH
        return parsed
    if raw_cat_id not in valid_cat_ids:
        raise ClassificationSchemaError(f"Unbekannte category_id: {raw_cat_id!r}")
    return parsed


def is_no_match(parsed: dict[str, Any]) -> bool:
    return parsed.get("category_id") == NO_MATCH


def select_variant(
    candidates: CategoryCandidates,
    user_prompt: str,
    *,
    target: Target,
    settings: LLMSettings,
    measurement: Measurement,
) -> list[dict[str, Any]]:
    """Die vollstaendigste Fassung der Kandidatenliste, die samt
    Antwortreserve ins Fenster passt. Passt keine, kommt die knappste zurueck -
    der Gateway lehnt dann mit model_input_too_large und Aufschluesselung ab.
    Ist das Fenster unbekannt, entscheidet ebenfalls der Gateway."""
    variants = candidates.all_variants()
    if not target.window.known:
        return variants[0]
    for variant in variants:
        messages = classification_messages(
            build_system_prompt(variant, candidates.taxonomy_version), user_prompt
        )
        try:
            budget = plan_budget(
                messages, window=target.window, settings=settings, measurement=measurement
            )
        except BudgetInputError:
            return variant
        if budget.fits:
            return variant
    return variants[-1]


# ---------------------------------------------------------------------------
# Persistierte Modell-Faehigkeit (ai_model_capabilities)
# ---------------------------------------------------------------------------


async def record_model_capability(
    session: AsyncSession,
    household_id: UUID,
    model_name: str,
    supports_json_schema: bool,
    canary_passed: bool,
    last_error: str | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> None:
    """Haelt je Haushalt/Modell/Promptversion fest, ob strukturierte Ausgabe
    funktioniert. Bewusst PROMPT_VERSION ohne Taxonomiesuffix (siehe
    prompt_version_for)."""
    await session.execute(
        text(
            "insert into ai_model_capabilities "
            "(household_id, model_name, prompt_version, supports_json_schema, "
            "canary_passed, last_error, checked_at) "
            "values (:household_id, :model, :pv, :sjs, :cp, :err, now()) "
            "on conflict (household_id, model_name, prompt_version) do update set "
            "supports_json_schema = excluded.supports_json_schema, "
            "canary_passed = excluded.canary_passed, "
            "last_error = excluded.last_error, checked_at = now()"
        ),
        {
            "household_id": str(household_id),
            "model": model_name,
            "pv": prompt_version,
            "sjs": supports_json_schema,
            "cp": canary_passed,
            "err": last_error,
        },
    )


# ---------------------------------------------------------------------------
# Kanarienvogel
# ---------------------------------------------------------------------------


@dataclass
class CanaryResult:
    passed: bool
    supports_json_schema: bool
    reason_code: str | None
    message: str | None
    results: list[ChatResult] = field(default_factory=list)


def _canary_prompts(
    candidates: CategoryCandidates | None,
    *,
    target: Target,
    settings: LLMSettings,
    measurement: Measurement,
) -> tuple[list[dict[str, Any]], set[str] | frozenset[str]]:
    """Synthetischer Fall (REWE, ein Kandidat) oder - im Batch-Lauf - der echte
    Prompt mit allen Kategorien und einer eindeutigen Testbuchung auf die
    erste Kategorie. Dann prueft der Vorabtest auch, ob die echte Liste ins
    Fenster passt."""
    if candidates is None or not candidates.entries:
        system = build_system_prompt(CANARY_CATEGORIES, CANARY_TAXONOMY_VERSION)
        user = build_user_prompt("REWE Markt", "Einkauf Lebensmittel", -1500)
        return classification_messages(system, user), CANARY_VALID_IDS
    anchor = candidates.entries[0]
    anchor_name = anchor.get("path", anchor.get("key", "Kategorie"))
    amount = 1500 if anchor.get("type") == "income" else -1500
    user = build_user_prompt("Testbuchung", f"Eindeutige Testbuchung fuer {anchor_name}", amount)
    variant = select_variant(
        candidates, user, target=target, settings=settings, measurement=measurement
    )
    system = build_system_prompt(variant, candidates.taxonomy_version)
    return classification_messages(system, user), candidates.valid_ids


def _canary_verdict(
    result: ChatResult, valid_ids: set[str] | frozenset[str]
) -> tuple[bool, str | None, str | None]:
    """(bestanden, Grund, Meldung). no_match gilt beim eindeutigen Beispiel
    als nicht bestanden - formal korrekt, aber fuer die Klassifikation
    unbrauchbar."""
    if result.reason_code is not None:
        return False, result.reason_code, result.message
    try:
        parsed = parse_classification_content(result.content, valid_ids)
    except ClassificationSchemaError as exc:
        if result.truncated:
            return (
                False,
                reasons.MODEL_BUDGET_EXHAUSTED,
                reasons.describe(reasons.MODEL_BUDGET_EXHAUSTED, str(exc)),
            )
        return (
            False,
            reasons.MODEL_OUTPUT_UNUSABLE,
            reasons.describe(reasons.MODEL_OUTPUT_UNUSABLE, f"Keine schemakonforme Antwort: {exc}"),
        )
    if is_no_match(parsed):
        return (
            False,
            reasons.MODEL_OUTPUT_UNUSABLE,
            reasons.describe(
                reasons.MODEL_OUTPUT_UNUSABLE,
                "Das Modell antwortete no_match auf einen eindeutigen Beispielfall.",
            ),
        )
    return True, None, None


async def run_canary(
    target: Target,
    settings: LLMSettings,
    *,
    candidates: CategoryCandidates | None = None,
    scope: CallScope | None = None,
    client: httpx.AsyncClient | None = None,
) -> CanaryResult:
    """Vorabtest: klassifiziert eine Beispielbuchung und prueft die Antwort
    gegen den Vertrag. Erst strukturiert (response_format); lehnt der Server
    das Schema ab oder ist die Antwort inhaltlich unbrauchbar, ein zweites Mal
    ohne Schema (Prompt+Parse). Ein aufgebrauchtes Budget, ein unbekanntes
    Fenster oder ein Transportfehler beenden den Test sofort - ein zweiter
    Versuch fiele genauso aus.

    Misst und protokolliert nicht (scope None oder log/measure False): bei
    einer Beispielbuchung ueberwiegt der Rahmen der Chatvorlage."""
    measurement = await measurement_for(target, settings, scope)
    messages, valid_ids = _canary_prompts(
        candidates, target=target, settings=settings, measurement=measurement
    )
    first = await chat(
        ChatRequest(messages=messages, response_format=response_format_for(valid_ids)),
        target=target,
        settings=settings,
        scope=scope,
        client=client,
        measurement=measurement,
    )
    passed, code, message = _canary_verdict(first, valid_ids)
    if passed:
        return CanaryResult(True, True, None, None, [first])
    if code not in (reasons.MODEL_SCHEMA_UNSUPPORTED, reasons.MODEL_OUTPUT_UNUSABLE):
        return CanaryResult(False, False, code, message, [first])
    second = await chat(
        ChatRequest(messages=messages),
        target=target,
        settings=settings,
        scope=scope,
        client=client,
        measurement=measurement,
    )
    passed, code, message = _canary_verdict(second, valid_ids)
    if passed:
        return CanaryResult(True, False, None, None, [first, second])
    return CanaryResult(False, False, code, message, [first, second])


# ---------------------------------------------------------------------------
# Einzelklassifikation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationOutcome:
    payee_name: str
    category_id: UUID | None
    confidence: float
    reason_code: str
    model_name: str | None
    # Klartext bei einem Fehlschlag (aus dem Gateway oder dem Vertrag).
    message: str | None = None


ResolveCategory = Callable[[UUID], Awaitable[UUID | None]]


def _failure(
    payee_raw: str | None, code: str, model: str, message: str | None
) -> ClassificationOutcome:
    return ClassificationOutcome(
        payee_name=payee_raw or "",
        category_id=None,
        confidence=0.0,
        reason_code=code,
        model_name=model,
        message=message,
    )


async def classify_one(
    session: AsyncSession,
    household_id: UUID,
    transaction_id: UUID | None,
    payee_raw: str | None,
    purpose: str | None,
    amount_minor: int,
    *,
    target: Target,
    settings: LLMSettings,
    candidates: CategoryCandidates,
    supports_json_schema: bool,
    resolve_category: ResolveCategory,
    ai_task_id: UUID | None = None,
    client: httpx.AsyncClient | None = None,
) -> ClassificationOutcome:
    """Klassifiziert EINE Buchung und schreibt den Entscheidungsnachweis.

    Ablauf: PII maskieren, die passende Fassung der Kandidatenliste waehlen,
    ueber den Gateway fragen (der protokolliert jede Anfrage in llm_calls und
    misst), Antwort gegen den Vertrag pruefen.
    * Schemabruch: GENAU ein Korrekturversuch mit Hinweis (llm_schema_invalid).
    * Server lehnt das Schema ab (model_schema_unsupported): Faehigkeit
      vermerken, einmal ohne Schema - auch fuer die Korrekturversuche.
    * Abgeschnitten ohne verwertbare Antwort: model_budget_exhausted, keine
      Eskalation, kein "ohne Denken" (E1).
    * Ohne Fenster, zu grosse Eingabe, Ueberlauf, KI aus: der benannte Grund
      des Gateways, ohne Wiederholung.
    no_match ist eine richtige Antwort und wird mit eigenem Reason Code als
    Entscheidung geschrieben."""
    prompt_version = prompt_version_for(candidates.taxonomy_version)
    scope = CallScope(
        session=session,
        household_id=household_id,
        stage="classify",
        prompt_version=prompt_version,
        ai_task_id=ai_task_id,
    )
    clean_payee = mask_pii_text(payee_raw)
    clean_purpose = mask_pii_text(purpose)
    base_user_prompt = build_user_prompt(clean_payee, clean_purpose, amount_minor)
    measurement = await measurement_for(target, settings, scope)
    variant = select_variant(
        candidates, base_user_prompt, target=target, settings=settings, measurement=measurement
    )
    system_prompt = build_system_prompt(variant, candidates.taxonomy_version)
    valid_ids = candidates.valid_ids
    use_schema = supports_json_schema and bool(valid_ids)
    retry_hint = ""
    model = target.model

    attempt = 0
    while attempt < MAX_SCHEMA_ATTEMPTS:
        attempt += 1
        messages = classification_messages(system_prompt, base_user_prompt + retry_hint)
        result = await chat(
            ChatRequest(
                messages=messages,
                response_format=response_format_for(valid_ids) if use_schema else None,
            ),
            target=target,
            settings=settings,
            scope=scope,
            client=client,
            measurement=measurement,
        )
        if result.reason_code == reasons.MODEL_SCHEMA_UNSUPPORTED and use_schema:
            # Server/Modell kann response_format nicht: EINMALIG auf den
            # Prompt+Parse-Pfad wechseln, vermerkt statt still. Der Vermerk
            # steht, bevor die Antwort des zweiten Aufrufs feststeht.
            use_schema = False
            await record_model_capability(
                session,
                household_id,
                model,
                supports_json_schema=False,
                canary_passed=True,
                last_error=(
                    "capability_fallback: response_format/json_schema abgelehnt "
                    f"({result.message}) - Wechsel auf Prompt+Parse-Pfad."
                ),
            )
            result = await chat(
                ChatRequest(messages=messages),
                target=target,
                settings=settings,
                scope=scope,
                client=client,
                measurement=measurement,
            )

        code = result.reason_code
        if code == reasons.MODEL_TIMEOUT:
            return _failure(payee_raw, LLM_TIMEOUT, model, result.message)
        if code in (reasons.MODEL_UNAVAILABLE, reasons.MODEL_HTTP_ERROR):
            return _failure(payee_raw, LLM_EXECUTION_ERROR, model, result.message)
        if code == reasons.MODEL_SCHEMA_UNSUPPORTED:
            # Ohne Schema abgelehnt: kein Faehigkeitsproblem, ein Fehler.
            return _failure(payee_raw, LLM_EXECUTION_ERROR, model, result.message)
        if code is not None and code != reasons.MODEL_OUTPUT_UNUSABLE:
            return _failure(payee_raw, code, model, result.message)

        try:
            parsed = parse_classification_content(result.content, valid_ids)
        except ClassificationSchemaError as exc:
            if result.truncated:
                message = reasons.describe(reasons.MODEL_BUDGET_EXHAUSTED, str(exc))
                await update_call_outcome(
                    session,
                    household_id,
                    result.call_id,
                    status="error",
                    reason_code=reasons.MODEL_BUDGET_EXHAUSTED,
                    error_message=message,
                )
                return _failure(payee_raw, reasons.MODEL_BUDGET_EXHAUSTED, model, message)
            await update_call_outcome(
                session,
                household_id,
                result.call_id,
                status="error",
                reason_code=reasons.MODEL_OUTPUT_UNUSABLE,
                error_message=str(exc),
            )
            if attempt < MAX_SCHEMA_ATTEMPTS:
                retry_hint = SCHEMA_RETRY_HINT
                continue
            return _failure(payee_raw, LLM_SCHEMA_INVALID, model, str(exc))

        confidence = min(max(float(parsed.get("confidence", 0.5)), 0.0), 1.0)
        payee_name = str(parsed.get("payee_name", clean_payee or ""))
        category_id: UUID | None = None
        if is_no_match(parsed):
            # Regelkonform "keine passende Kategorie": kein Fehler, kein Retry.
            reason_code = NO_MATCH_REASON_CODE
        else:
            reason_code = str(parsed.get("reason_code", DEFAULT_REASON_CODE))
            category_id = await resolve_category(UUID(str(parsed["category_id"])))
            if category_id is None:
                # Seit dem Laden der Kandidaten archiviert oder gemergt.
                reason_code = STALE_CATEGORY_REASON_CODE
                confidence = 0.0

        await session.execute(
            text(
                "insert into classification_decisions (household_id, transaction_id, "
                "payee_name, category_id, confidence, reason_code, source, model_name, "
                "ai_task_id) values "
                "(:household_id, :tx_id, :payee_name, :cat_id, :conf, :reason, 'ai', :model, "
                ":ai_task_id)"
            ),
            {
                "household_id": str(household_id),
                "tx_id": str(transaction_id) if transaction_id else None,
                "payee_name": payee_name,
                "cat_id": str(category_id) if category_id else None,
                "conf": confidence,
                "reason": reason_code,
                "model": model,
                "ai_task_id": str(ai_task_id) if ai_task_id else None,
            },
        )
        return ClassificationOutcome(
            payee_name=payee_name,
            category_id=category_id,
            confidence=confidence,
            reason_code=reason_code,
            model_name=model,
        )

    raise AssertionError("classify_one: Versuchsschleife ohne Ergebnis beendet.")
