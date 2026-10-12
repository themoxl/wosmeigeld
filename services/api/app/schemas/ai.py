from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AIWarning(BaseModel):
    """Benannter Befund (Reason-Code des KI-Kerns, app/llm/reasons.py) samt
    deutschem Klartext mit Hinweis auf die zustaendige Einstellung."""

    code: str
    message: str
    level: Literal["info", "warnung", "fehler"] = "warnung"


class AIWindowInfo(BaseModel):
    """Kontextfenster des aktiven Modells: geladen, Deckel, wirksam.

    `effective` (W) ist die Groesse, aus der jedes Antwortbudget gerechnet
    wird. `max_context` steht nur zur Information - sie ist nie das Fenster."""

    loaded: int | None = None
    cap: int | None = None
    effective: int | None = None
    source: str | None = None
    reason: str | None = None
    parallel: int | None = None
    max_context: int | None = None


class AIMeasurementInfo(BaseModel):
    """Gemessenes Verhaeltnis Zeichen/Token und Schaetzabstand des Modells -
    oder die Startwerte der Einstellung, solange nichts gemessen ist."""

    chars_per_token: float | None = None
    estimate_error: float | None = None
    calls: int = 0
    is_start_value: bool = True
    error_is_start_value: bool = True
    largest_need_tokens: int | None = None


# Waehlbare KI-Anbieter (app/llm/providers.py).
ProviderId = Literal["local", "anthropic"]


class AIHealthResponse(BaseModel):
    status: str
    is_connected: bool
    base_url: str
    # Gewaehlter Anbieter; bei "anthropic" verlassen die Daten den Haushalt.
    provider: ProviderId = "local"
    active_model: str | None
    canary_passed: bool
    # True nur, wenn der Canary eine schemakonforme Antwort UEBER
    # response_format/json_schema erhalten hat. False bedeutet: bewusster
    # Fallback auf den Prompt+Parse-Pfad (siehe app/llm/classification.run_canary).
    supports_json_schema: bool = False
    models_count: int
    message: str

    # --- Modellwahl ---------------------------------------------------------
    # Woher das aktive Modell kommt: ausdruecklich gewaehlt oder automatisch
    # bestimmt. Ohne diese Angabe waere nicht erkennbar, ob eine Aenderung in
    # LM Studio die Auswahl still verschoben hat.
    model_source: Literal["household", "automatic", "none"] = "none"
    model_state: Literal["loaded", "not-loaded", "unknown"] = "unknown"
    # Anzahl der Modelle, die ueberhaupt Chat-Completions beherrschen -
    # `models_count` zaehlt auch Einbettungsmodelle mit.
    chat_models_count: int = 0

    # --- Relatives Budget ---------------------------------------------------
    # Wirksames Fenster W (= window.effective); bleibt als eigenes Feld fuer
    # bestehende Aufrufer. None = unbekannt - dann wird nicht gesendet.
    context_tokens: int | None = None
    window: AIWindowInfo | None = None
    # Antwortreserve R = W * answer_reserve_share.
    reserve_tokens: int | None = None
    measurement: AIMeasurementInfo | None = None
    # Was das Modell zum Denken meldet (switchable/always/none/unknown) und
    # was bei der aktuellen Einstellung als reasoning_effort gesendet wird.
    reasoning_capability: Literal["switchable", "always", "none", "unknown"] = "unknown"
    reasoning_sent: str | None = None
    # Benannter Grund, wenn der Vorabtest nicht bestanden ist.
    canary_reason_code: str | None = None
    # Konkrete, benannte Befunde statt eines Fliesstexts.
    warnings: list[AIWarning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Laufzeitanzeige: womit gerade gerechnet wird
# ---------------------------------------------------------------------------


class AIRuntimeGpu(BaseModel):
    """Ein Grafikprozessor des Rechners, auf dem die API laeuft."""

    name: str
    memory_used_mb: int
    memory_total_mb: int
    utilization_percent: int
    temperature_celsius: int | None = None


class AIRuntimeModel(BaseModel):
    """Das geladene Modell so, wie LM Studio es beschreibt."""

    key: str
    display_name: str | None = None
    architecture: str | None = None
    quantization: str | None = None
    parameters: str | None = None
    size_bytes: int | None = None
    max_context_tokens: int | None = None
    loaded_context_tokens: int | None = None


class AIRuntimeStatus(BaseModel):
    """Antwort von GET /v1/ai/runtime.

    Beantwortet drei Fragen in einer Anfrage: laeuft die lokale KI, mit welchem
    Modell und welchem Kontextfenster arbeitet sie, und was macht die GPU. Die
    Warteschlange zeigt das waehrend eines Laufs an - bis dahin war nur
    nachtraeglich (ueber `ai_tasks.model_name`) erkennbar, womit gerechnet
    wurde."""

    is_connected: bool
    base_url: str | None = None
    provider: ProviderId = "local"
    # Was in den KI-Einstellungen dieses Haushalts steht. Weicht es vom
    # geladenen Modell ab, arbeitet die KI nicht mit dem gewuenschten Modell.
    configured_model: str | None = None
    active_model: AIRuntimeModel | None = None
    loaded_count: int = 0
    chat_models_count: int = 0
    # Eingestellte Reasoning-Stufe und der daraus tatsaechlich gesendete Wert.
    reasoning: str | None = None
    reasoning_effort: str | None = None
    # Wirksames Fenster W des aktiven Modells samt Quelle - dieselbe Regel wie
    # im KI-Kern: min(geladenes Fenster, "Kontext hoechstens"); ohne geladenes
    # Fenster gilt der Deckel, ohne beides ist effective None und reason sagt
    # warum.
    window: AIWindowInfo | None = None
    gpus: list[AIRuntimeGpu] = Field(default_factory=list)
    # Warum keine GPU-Werte vorliegen (kein nvidia-smi, Container, Fehler).
    gpu_detail: str | None = None
    message: str


# ---------------------------------------------------------------------------
# Modellkatalog und Einstellungen
# ---------------------------------------------------------------------------


class AIModelInfo(BaseModel):
    """Ein von LM Studio gemeldetes Modell.

    `kind`/`state`/Kontextlaengen stammen aus `GET /api/v0/models`. Bietet der
    Server nur die OpenAI-kompatible Liste, bleiben sie `unknown` bzw. None -
    geraten wird nichts."""

    id: str
    kind: Literal["llm", "vlm", "embeddings", "unknown"] = "unknown"
    state: Literal["loaded", "not-loaded", "unknown"] = "unknown"
    publisher: str | None = None
    arch: str | None = None
    quantization: str | None = None
    max_context_tokens: int | None = None
    loaded_context_tokens: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    # Einbettungsmodelle beherrschen keine Chat-Completion und sind als
    # Klassifikationsmodell schlicht unbrauchbar.
    is_chat_capable: bool = True
    # Aus GET /api/v1/models: capabilities.reasoning.allowed_options. Leer,
    # wenn das Modell kein Reasoning kennt oder der Server die v1-Liste nicht
    # anbietet - dann blendet die Oberflaeche den Schalter aus, statt eine
    # Einstellung anzubieten, die nichts bewirkt.
    reasoning_options: list[str] = Field(default_factory=list)
    # Von derselben LM-Studio-Capability. So kann die Oberflaeche statt des
    # mehrdeutigen "Modellstandard" konkret "An" oder "Aus" anzeigen.
    reasoning_default: str | None = None
    # Herstellerprofil, das bei "Sampling automatisch" fuer dieses Modell
    # und die aktuelle Reasoning-Wahl wirksam wird. None bedeutet: keine
    # belastbare Vorgabe bekannt, daher Modell-/Serverstandard.
    sampling_profile: "AISamplingProfile | None" = None
    # Anzeigename des Anbieters (Anthropic: "Claude Opus 5"); None = die ID.
    display_name: str | None = None
    # Hoechste Antwortlaenge laut Anbieter (Anthropic max_tokens); None =
    # nicht gemeldet. Das Antwortbudget wird darauf gedeckelt.
    output_limit_tokens: int | None = None


class AISamplingProfile(BaseModel):
    """Transparente, verlinkte Empfehlung fuer eine Modellfamilie."""

    id: str
    label: str
    model_examples: list[str] = Field(default_factory=list)
    model_markers: list[str] = Field(default_factory=list)
    reasoning_mode: Literal["thinking", "non-thinking", "any"] = "any"
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    repeat_penalty: float | None = None
    min_p: float | None = None
    presence_penalty: float | None = None
    note: str
    source_url: str


class AIModelCatalog(BaseModel):
    base_url: str
    is_connected: bool
    provider: ProviderId = "local"
    # False, wenn nur die OpenAI-kompatible Liste verfuegbar war. Dann fehlen
    # Zustand und Kontextlaenge - die Oberflaeche muss das benennen, statt
    # leere Felder als "0" zu zeigen.
    supports_detail_api: bool = False
    models: list[AIModelInfo] = Field(default_factory=list)
    sampling_profiles: list[AISamplingProfile] = Field(default_factory=list)
    message: str = ""


# default = die Spaltenvorgabe aus dem Datenbankschema (Wert entspricht ihr).
SettingSource = Literal["household", "environment", "automatic", "default"]

# Die Oberflaeche bietet nur Optionen an, die LM Studio fuer das aktive Modell
# meldet. Boolesche Reasoner verwenden on/off, Modelle mit regelbarer Tiefe die
# Effort-Stufen. Standard wird als None dargestellt und sendet keinen Override.
ReasoningLevel = Literal["on", "off", "minimal", "low", "medium", "high", "xhigh", "max"]
SamplingMode = Literal["model", "manual"]


class AIProviderInfo(BaseModel):
    """Ein waehlbarer KI-Anbieter, wie app/llm/providers.py ihn beschreibt."""

    id: ProviderId
    name: str
    # Feste Adresse; None = die Adresse der Einstellung (lokaler Server).
    url: str | None = None
    key_required: bool
    leaves_house: bool
    model_required: bool
    hint: str


class AIKeyStatus(BaseModel):
    """Ob fuer den Anbieter ein API-Schluessel hinterlegt ist - nie der Wert.

    `ending` sind die letzten Zeichen ("...abcd"), nur bei langen Schluesseln.
    `problem` nennt, warum ein hinterlegter Schluessel nicht lesbar ist."""

    provider: ProviderId
    target: str
    stored: bool
    ending: str | None = None
    since: datetime | None = None
    problem: str | None = None


class AIKeyInput(BaseModel):
    """Body von PUT /v1/ai/provider-keys/{provider}. Der Wert geht nur hinein,
    nie wieder hinaus."""

    api_key: Annotated[str, Field(min_length=1, max_length=1000)]


class AISettings(BaseModel):
    """Wirksame Einstellungen samt Herkunft jedes Werts.

    Die Herkunft ist Teil des Vertrags: ein Wert beantwortet nicht, ob er eine
    bewusste Entscheidung dieses Haushalts oder eine Vorgabe ist - und nur im
    ersten Fall darf ein Zuruecksetzen etwas aendern. Die Vorgaben selbst
    stehen im Datenbankschema; `schema_defaults` liefert sie je Feld mit."""

    is_enabled: bool
    auto_categorize_min_confidence: float

    # --- Anbieter (Etappe 3) ----------------------------------------------
    provider: ProviderId = "local"
    providers: list[AIProviderInfo] = Field(default_factory=list)
    # Schluesselstand jedes Anbieters, der einen verlangt - auch des gerade
    # nicht gewaehlten, damit der Owner ihn vor dem Wechsel hinterlegen kann.
    provider_keys: list[AIKeyStatus] = Field(default_factory=list)

    # Adresse des lokalen Servers (gilt nur fuer provider = "local").
    base_url: str
    base_url_source: SettingSource
    model_name: str | None
    model_source: SettingSource
    temperature: float
    temperature_source: SettingSource
    sampling_mode: SamplingMode = "model"
    sampling_mode_source: SettingSource = "automatic"
    top_p: float = 0.95
    top_p_source: SettingSource = "automatic"
    top_k: int = 64
    top_k_source: SettingSource = "automatic"
    repeat_penalty: float = 1.0
    repeat_penalty_source: SettingSource = "automatic"
    min_p: float = 0.0
    min_p_source: SettingSource = "automatic"
    presence_penalty: float = 0.0
    presence_penalty_source: SettingSource = "automatic"
    request_timeout_seconds: int
    request_timeout_seconds_source: SettingSource

    # --- Laufzeitsteuerung von LM Studio (Migration 0012) -----------------
    # Gewuenschtes Kontextfenster. None heisst "so lassen, wie geladen" - das
    # ist etwas anderes als ein Umgebungsvorgabewert, deshalb kein
    # Ersatzwert aus den Settings.
    context_length: int | None = None
    context_length_source: SettingSource = "environment"
    # None = Modellstandard.
    reasoning: ReasoningLevel | None = None
    reasoning_source: SettingSource = "environment"

    # --- SearXNG-Websuche fuer die KI-Kostenschaetzung (Vorhaben-Modul) ----
    # None = keine Suchinstanz hinterlegt - die Kostenschaetzung laeuft dann
    # ohne Websuche, nicht mit einer erfundenen URL.
    searxng_base_url: str | None = None
    searxng_base_url_source: SettingSource = "environment"

    # --- Kontext und Budget (relatives KI-Budget) ---------------------------
    # "Kontext hoechstens": None = das geladene Fenster gilt allein.
    context_cap_tokens: int | None = None
    # Antwortreserve als Anteil des wirksamen Fensters (R = W * Anteil).
    answer_reserve_share: float
    # Groesse einer Ergebnisseite im KI-Feld als Anteil des Fensters.
    result_page_share: float
    run_time_limit_minutes: int
    # Startwerte, bis fuer ein Modell eine Messung vorliegt (E3).
    start_chars_per_token: float
    start_estimate_error: float
    context_line_enabled: bool
    # Aufbewahrung der Chats des KI-Felds in Tagen seit der letzten Aenderung
    # (E6); aeltere loescht der Wiederherstellungsjob der KI-Warteschlange.
    assistant_retention_days: int
    # Frueheres festes Ausgabebudget. Wird nicht mehr verwendet; die
    # Oberflaeche weist EINMAL darauf hin und setzt es mit
    # clear_fields=["legacy_max_output_tokens"] zurueck.
    legacy_max_output_tokens: int | None = None
    # Spaltenvorgaben aus dem Schema je Feld ("Vorgabe: 25 %"), Grundlage fuer
    # Anzeige und Zuruecksetzen. Felder ohne Vorgabe stehen mit None darin.
    schema_defaults: dict[str, bool | int | float | str | None] = Field(default_factory=dict)


AISettingsField = Literal[
    "custom_base_url",
    "model_name",
    "temperature",
    "sampling_mode",
    "top_p",
    "top_k",
    "repeat_penalty",
    "min_p",
    "presence_penalty",
    "request_timeout_seconds",
    "context_length",
    "reasoning",
    "searxng_base_url",
    "context_cap_tokens",
    "answer_reserve_share",
    "result_page_share",
    "run_time_limit_minutes",
    "start_chars_per_token",
    "start_estimate_error",
    "context_line_enabled",
    "assistant_retention_days",
    "legacy_max_output_tokens",
]


class AISettingsUpdate(BaseModel):
    """Teilaenderung. Ein Feld auf seine Vorgabe zurueckzusetzen verlangt
    `clear_fields` - `null` allein hiesse "nicht angefasst" und liesse sich
    von "bitte zuruecksetzen" nicht unterscheiden. Zuruecksetzen heisst: die
    Spaltenvorgabe aus dem Schema (bzw. NULL, wo es keine gibt).

    Die Bereiche spiegeln die CHECKs in 0001_schema.sql, damit ein
    Tippfehler als 422 statt als Datenbankfehler zurueckkommt."""

    is_enabled: bool | None = None
    auto_categorize_min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    # Anbieterwechsel darf nur der Owner (routes/ai.py): mit Claude verlassen
    # Haushaltsdaten das Haus.
    provider: ProviderId | None = None
    base_url: str | None = None
    model_name: str | None = None
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None
    sampling_mode: SamplingMode | None = None
    top_p: Annotated[float, Field(gt=0.0, le=1.0)] | None = None
    top_k: Annotated[int, Field(ge=0, le=1000)] | None = None
    repeat_penalty: Annotated[float, Field(ge=0.0, le=2.0)] | None = None
    min_p: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    presence_penalty: Annotated[float, Field(ge=-2.0, le=2.0)] | None = None
    request_timeout_seconds: Annotated[int, Field(ge=5, le=900)] | None = None
    # Grenzen wie in Migration 0012. Die obere ist bewusst grosszuegig: was ein
    # Modell wirklich kann, sagt max_context_length, und was in den Speicher
    # passt, entscheidet LM Studio beim Laden - mit einer klaren Fehlermeldung.
    context_length: Annotated[int, Field(ge=2048, le=1048576)] | None = None
    reasoning: ReasoningLevel | None = None
    searxng_base_url: str | None = None
    context_cap_tokens: Annotated[int, Field(ge=2048, le=1048576)] | None = None
    answer_reserve_share: Annotated[float, Field(ge=0.05, le=0.9)] | None = None
    result_page_share: Annotated[float, Field(ge=0.01, le=0.5)] | None = None
    run_time_limit_minutes: Annotated[int, Field(ge=1, le=240)] | None = None
    start_chars_per_token: Annotated[float, Field(ge=1.0, le=8.0)] | None = None
    start_estimate_error: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    context_line_enabled: bool | None = None
    assistant_retention_days: Annotated[int, Field(ge=1, le=3650)] | None = None
    clear_fields: list[AISettingsField] = Field(default_factory=list)

    @field_validator("base_url", "model_name", "searxng_base_url")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class AIModelLoadResult(BaseModel):
    """Ergebnis von POST /v1/ai/models/load.

    `changed` unterscheidet "war schon so geladen" von "wurde neu geladen" -
    ohne diese Angabe koennte die Oberflaeche nicht erklaeren, warum ein Klick
    einmal 40 Sekunden dauert und einmal gar nichts tut."""

    model_name: str
    changed: bool
    instance_id: str | None
    context_length: int | None
    load_time_seconds: float
    message: str


class AIClassifyInput(BaseModel):
    transaction_id: UUID | None = None
    payee_raw: str | None = None
    purpose: str | None = None
    amount_minor: int = 0


class AIClassifyResult(BaseModel):
    payee_name: str
    category_id: UUID | None
    confidence: float
    reason_code: str
    source: str
    model_name: str | None


class AIDecisionLog(BaseModel):
    id: UUID
    transaction_id: UUID | None
    payee_name: str | None
    category_id: UUID | None
    confidence: float
    reason_code: str
    source: str
    model_name: str | None
    created_at: datetime


ClassificationRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
# Stufen des Laufs. Heute nur Vorbereitung und Klassifikation; der spaetere
# KI-Finanz-Check haengt weitere Stufen an denselben Lauf.
ClassificationRunStage = Literal["preparing", "classifying", "completed"]


class ClassificationRunCreate(BaseModel):
    # Idempotenzschluessel des Clients: derselbe Wert liefert denselben Lauf
    # zurueck, statt einen zweiten Job einzureihen (Doppelklick, Retry nach
    # Netzwerkfehler, App-Neustart mitten im Start).
    request_id: str | None = Field(default=None, max_length=100)


class ClassificationRun(BaseModel):
    id: UUID
    status: ClassificationRunStatus
    run_type: str
    current_stage: ClassificationRunStage | None
    since_date: date
    total_count: int
    processed_count: int
    success_count: int
    error_count: int
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    heartbeat_at: datetime | None
    cancel_requested: bool
    # Serverseitig berechnet: aktiver Lauf ohne Lebenszeichen (siehe
    # STALE_RUN_AFTER in routes/ai.py). Die Oberflaeche zeigt dafuer einen
    # Hinweis statt eines endlos laufenden Fortschritts an.
    is_stale: bool


# ---------------------------------------------------------------------------
# Sammeluebernahme sicherer KI-Vorschlaege
# ---------------------------------------------------------------------------
# docs/KATEGORISIERUNGSPLAN.md verschiebt "Alle sicheren uebernehmen" fuer
# heterogene KI-Vorschlaege ausdruecklich in den KI-Plan und liefert dafuer die
# aktiven Zielkategorien, die Versionspruefung und die sichere Schreiblogik.
# Die Modelle hier spiegeln deshalb bewusst BulkCategoryInput/-Result aus
# schemas/category.py: dieselben Statuswerte, dieselbe Obergrenze, dieselbe
# Rueckmeldung je Buchung - nur dass die Zielkategorie nicht vom Client kommt,
# sondern je Buchung aus dem juengsten KI-Entscheid stammt.

# Obergrenze wie im manuellen Bulk-Pfad (schemas/category.MAX_BULK_TRANSACTIONS).
# Bewusst dieselbe Zahl: eine Sammelaktion darf nicht davon abhaengen, ueber
# welchen Knopf sie ausgeloest wurde.
MAX_AI_PROPOSAL_TRANSACTIONS = 500

# Rueckfallschwelle, wenn weder der Request noch
# ai_household_settings.auto_categorize_min_confidence etwas vorgeben -
# identisch zum Spaltendefault in supabase/migrations/0001_schema.sql.
DEFAULT_MIN_CONFIDENCE = 0.85

AIProposalStatus = Literal["applied", "unchanged", "skipped", "conflict"]

# Maschinenlesbare Begruendung. Bewusst ein geschlossenes Literal: die
# Oberflaeche gruppiert danach ("12 zu unsicher", "3 ohne passende Kategorie")
# und darf sich nicht auf freien Text stuetzen.
AIProposalReason = Literal[
    "below_confidence",
    "no_match",
    "split_transaction",
    "already_categorized",
    "category_inactive",
    "transaction_not_found",
    "archived_transaction",
    "version_conflict",
]


class AIProposalItem(BaseModel):
    """Ergebnis genau einer Buchung des Vorgangs."""

    transaction_id: UUID
    # Die aufgeloeste Zielkategorie des Vorschlags (ein Merge-Tombstone ist hier
    # bereits auf sein aktives Ziel kanonisiert). None bei no_match und bei
    # Buchungen ohne verwertbaren Vorschlag.
    category_id: UUID | None = None
    category_name: str | None = None
    confidence: float = 0.0
    source: Literal["ai", "history"] | None = None
    # reason_code des juengsten Entscheids (z. B. 'merchant_and_context_match',
    # 'no_matching_category', 'category_no_longer_active'). Existiert gar kein
    # Entscheid, steht hier NO_DECISION_REASON_CODE.
    reason_code: str
    status: AIProposalStatus
    reason: AIProposalReason | None = None
    # Versionsstand nach dem Vorgang - der Client kann seinen lokalen Stand
    # damit fortschreiben, ohne jede Buchung neu zu lesen (wie BulkCategoryItem).
    current_version: int | None = None


class AIProposalApplyInput(BaseModel):
    """Gemeinsamer Request von Vorschau und Anwendung."""

    # None = alle offenen Vorschlaege des Haushalts (hoechstens
    # MAX_AI_PROPOSAL_TRANSACTIONS, juengster Vorschlag zuerst).
    transaction_ids: (
        Annotated[list[UUID], Field(max_length=MAX_AI_PROPOSAL_TRANSACTIONS)] | None
    ) = None
    # None = Haushaltseinstellung auto_categorize_min_confidence.
    min_confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    # Wenn gesetzt, stammen alle Vorschlaege exakt aus diesem Lauf. Ohne
    # diese Grenze vermischte die Review-Inbox alte Modelle mit dem gerade
    # abgeschlossenen Lauf und zeigte z. B. 160 Ergebnisse nach nur 4 Calls.
    classification_run_id: UUID | None = None
    # Nur fuer apply: derselbe Wert liefert das Ergebnis des ersten Laufs
    # zurueck, statt ein zweites Mal zu schreiben.
    request_id: str | None = Field(default=None, max_length=100)

    @field_validator("transaction_ids")
    @classmethod
    def _unique_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        """Doppelte IDs wuerden sonst zwei Ergebniszeilen fuer dieselbe Buchung
        erzeugen - dieselbe Normalisierung wie in BulkCategoryInput."""
        if value is None:
            return None
        seen: set[UUID] = set()
        unique: list[UUID] = []
        for item in value:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique


class AIProposalBatchResult(BaseModel):
    # Tatsaechlich verwendete Schwelle - die Oberflaeche zeigt sie an, damit
    # "sicher" nicht unerklaert bleibt.
    min_confidence: float
    applied: int
    unchanged: int
    skipped: int
    conflict: int
    items: list[AIProposalItem]
    # True, wenn dieselbe request_id schon einmal angewendet wurde und hier das
    # gespeicherte Ergebnis des ersten Laufs zurueckkommt (wie MergeResult).
    idempotent_replay: bool = False
