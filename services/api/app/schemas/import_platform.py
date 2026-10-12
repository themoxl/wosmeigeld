from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Eine Rohzeile der Datei (Spaltenname -> Wert). additionalProperties gehoert
# an den EINTRAG, nicht an das Feld: bei einem optionalen Feld (list | None)
# baut Pydantic ein anyOf, und ein `json_schema_extra={"items": ...}` am Feld
# landet als Geschwister neben diesem anyOf, wo openapi-typescript es nicht
# liest. Herausgekommen ist dabei `Record<string, never>[]` - genau der Typ,
# aus dem sich kein Wert lesen laesst und den der Kommentar an
# ImportRow.raw_data verhindern sollte.
RawRow = Annotated[dict[str, Any], Field(json_schema_extra={"additionalProperties": True})]

ImportFormat = Literal["csv_generic", "xlsx_generic", "xls_generic", "mt940", "camt", "paypal_csv"]
ImportStatus = Literal[
    "pending",
    "analysing",
    "ready",
    # Datei gelesen, aber Datum/Betrag keiner Spalte zuzuordnen - im Gegensatz
    # zu 'failed' ein heilbarer Zustand (siehe POST /v1/imports/{id}/reparse).
    "needs_mapping",
    "committing",
    "completed",
    "failed",
    "cancelled",
]
RowStatus = Literal[
    "new",
    "duplicate_exact",
    "duplicate_possible",
    "transfer_candidate",
    "error",
    "committed",
    "skipped",
]


class ImportRun(BaseModel):
    id: UUID
    account_id: UUID | None
    mapping_profile_id: UUID | None
    filename: str
    file_size_bytes: int
    detected_format: str | None
    status: ImportStatus
    total_rows: int
    new_count: int
    duplicate_count: int
    transfer_candidate_count: int
    error_count: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None
    cancel_requested: bool = False
    # Spaltennamen der Datei und ein paar Rohzeilen - vorhanden, sobald der
    # Parser die Kopfzeile lesen konnte, also AUCH im Zustand 'needs_mapping',
    # in dem es noch keine import_rows gibt.
    detected_columns: list[str] | None = None
    # Gleicher Grund wie bei ImportRow.raw_data: ohne additionalProperties
    # waere jede Beispielzeile im generierten Vertrag ein leeres Objekt.
    sample_raw: list[RawRow] | None = None
    # Serverseitig berechnet: in Arbeit, aber ohne Lebenszeichen. Dieselbe
    # Rolle wie ai_tasks.is_stale - die Oberflaeche zeigt dafuer eine Diagnose
    # statt eines Fortschritts, der nie endet.
    is_stale: bool = False
    # Serverseitig berechnet, damit die Oberflaeche VOR dem Klick weiss, welche
    # Aktion moeglich ist: committed_count > 0 sperrt das Entfernen (auch bei
    # Status 'ready', wenn nur ein Teil uebernommen wurde),
    # active_transaction_count > 0 heisst, dass ein Zuruecknehmen der Buchungen
    # noch etwas bewirken wuerde. Beides bleibt 0, wo die Laufzeile ohne diese
    # Spalten gebildet wird (PATCH/Abbruch/erneutes Einlesen) - dort kann per
    # Definition nichts uebernommen sein.
    committed_count: int = 0
    active_transaction_count: int = 0


class ImportRunUpdate(BaseModel):
    """Nachtraegliche Korrektur eines noch nicht uebernommenen Importlaufs.

    Nur die im Body tatsaechlich enthaltenen Felder werden geaendert (der
    Endpunkt wertet model_fields_set aus); ein leerer Body ist erlaubt und
    laesst den Lauf unveraendert. `null` loescht die jeweilige Zuordnung.

    `mapping_profile_id` korrigiert ausschliesslich die Verknuepfung - die
    Datei ist zu diesem Zeitpunkt bereits geparst und wird NICHT erneut
    ausgewertet."""

    account_id: UUID | None = None
    mapping_profile_id: UUID | None = None


class ImportRow(BaseModel):
    id: UUID
    import_run_id: UUID
    row_number: int
    # json_schema_extra ist hier nicht kosmetisch: ohne additionalProperties
    # erzeugt openapi-typescript aus diesem Feld `Record<string, never>` - ein
    # Typ, aus dem sich kein einziger Wert lesen laesst. Die Originalspalten
    # der Datei wurden dadurch zwar geliefert, waren im Web-Client aber
    # unbenutzbar, weshalb die Vorschau nur die kanonischen Felder zeigen
    # konnte (packages/contracts/src/generated/api-types.ts).
    raw_data: dict[str, Any] = Field(json_schema_extra={"additionalProperties": True})
    external_reference: str | None
    booked_date: date | None
    value_date: date | None
    amount_minor: int | None
    currency: str | None
    payee_raw: str | None
    purpose: str | None
    bank_transaction_code: str | None
    fingerprint: str | None
    status: RowStatus
    duplicate_of_transaction_id: UUID | None
    committed_transaction_id: UUID | None
    # Kategorieherkunft im Staging (Kategorisierungsplan, "Review und Import").
    # 'rule' = eine aktive Klassifikationsregel schlaegt diese Kategorie vor,
    # 'none' = ohne Kategorie. 'manual' entsteht erst durch die Auswahl im
    # Client und wird beim Commit als category_by_row mitgeschickt; eine
    # unkategorisierte Zeile blockiert den Import nicht, sondern landet
    # anschliessend in der Review-Inbox.
    suggested_category_id: UUID | None = None
    suggested_category_name: str | None = None
    category_source: Literal["rule", "none"] = "none"


class ImportCommitInput(BaseModel):
    row_ids: list[UUID] | None = None  # None = alle nicht-Duplikate
    # Manuell im Staging gewaehlte Kategorie je Zeile. Schlaegt einen
    # Regelvorschlag; nicht genannte Zeilen behalten die Regellogik.
    category_by_row: dict[UUID, UUID | None] = Field(default_factory=dict)


class ImportCommitResult(BaseModel):
    committed: int


class ImportRevertBlocker(BaseModel):
    """Warum eine Buchung beim Zuruecknehmen stehen bleibt, und wie viele."""

    reason: str
    label: str
    count: int


class ImportRevertPreview(BaseModel):
    """Was ein Zuruecknehmen dieses Laufs anfassen wuerde - ohne es zu tun.

    `revertable` und `blocked` zusammen ergeben `transactions_total`. Die
    Trennung ist der Sinn der Vorschau: eine nackte Gesamtzahl verraet nicht,
    ob unter den Buchungen welche sind, an denen inzwischen gearbeitet wurde.
    """

    import_run_id: UUID
    filename: str | None = None
    transactions_total: int
    revertable: int
    sum_minor: int
    from_date: date | None = None
    to_date: date | None = None
    # Dieselben Kennzahlen fuer den Fall, dass der Nutzer die verknuepften
    # Buchungen ausdruecklich mitnimmt (ImportRevertInput.include_linked). Die
    # Anzahl dafuer ist transactions_total - es sind dann alle. Beide Zahlenwerke
    # kommen aus einem Abruf, damit das Umschalten des Hakens im Dialog nicht
    # nachladen muss.
    sum_minor_with_linked: int = 0
    from_date_with_linked: date | None = None
    to_date_with_linked: date | None = None
    # Ohne Vorgabewert und damit im erzeugten Vertrag ein Pflichtfeld: der
    # Dienst setzt die Liste immer (leer, wenn nichts blockiert). Ein Default
    # haette sie im OpenAPI-Schema optional gemacht, und die Oberflaeche
    # muesste einen Fall behandeln, den es nicht gibt.
    blocked: list[ImportRevertBlocker]


class ImportRevertInput(BaseModel):
    """Nimmt auch die Buchungen mit, an denen inzwischen etwas haengt.

    Bewusst ein ausdruecklicher Schalter und kein Vorgabeverhalten: er loest
    Vertragsfaelligkeiten, Erstattungszuordnungen und Umbuchungspaare auf. Das
    ist beim Aufraeumen eines missratenen Imports genau richtig und als stiller
    Standard genau falsch."""

    include_linked: bool = False


class ImportRevertUntangled(BaseModel):
    """Eine geloeste Verknuepfung, in der Sprache ihrer Folge."""

    kind: str
    label: str
    count: int


class ImportRevertResult(BaseModel):
    import_run_id: UUID
    reverted: int
    kept: int
    blocked: list[ImportRevertBlocker]
    # Leer, solange nur unverknuepfte Buchungen entfernt wurden.
    untangled: list[ImportRevertUntangled] = Field(default_factory=list)


class ImportReparseInput(BaseModel):
    """Liest denselben Lauf mit einer korrigierten Spaltenzuordnung erneut ein.

    Der Unterschied zu ImportRunUpdate ist der entscheidende: PATCH aendert nur
    die Verknuepfung zum Mapping-Profil und parst NICHTS neu - die Oberflaeche
    versprach damit eine Wirkung, die nicht eintrat. Hier werden die bisherigen
    Staging-Zeilen verworfen und die Datei mit `column_mapping` neu gelesen.

    Das erkannte Format bleibt dabei erhalten; ein Mapping korrigiert die
    Spaltenzuordnung, nicht die Formatfamilie. Genau das Gegenteil - ein
    Profil, das `bank_format` erzwingt - hat die Autoerkennung bisher
    stillschweigend ausgeschaltet."""

    column_mapping: dict[str, str] = Field(default_factory=dict)


# Kanonische Zielfelder eines Spalten-Mappings - exakt die Schluessel, die
# import_mapping_profiles.column_mapping und die Parser
# (worker/imports/parsers/*.parse_with_mapping) verwenden. Der KI-Vorschlag
# benutzt bewusst dieselben Namen, damit die Oberflaeche ihn ohne Uebersetzung
# als Vorbelegung in den Mapping-Dialog uebernehmen kann.
MAPPING_FIELDS: tuple[str, ...] = ("booked_date", "amount", "payee", "purpose")

# Warum der Vorschlag (nicht) verfuegbar ist. Maschinenlesbar - die Oberflaeche
# zeigt daneben `message` an. Ein KI-seitiger Fehlschlag traegt den benannten
# Grund des KI-Kerns (app/llm/reasons.py, vollstaendig gespiegelt - geprueft in
# tests/test_imports_phase3.py).
MappingSuggestionReason = Literal[
    "ok",  # Vorschlag verwendbar (mindestens Datum und Betrag zugeordnet)
    "no_sample_rows",  # keine Staging-Zeilen und keine mitgeschickten Beispielzeilen
    "suggestion_incomplete",  # JSON gueltig, aber ohne Datums- oder Betragsspalte
    # Gruende des KI-Kerns:
    "ai_disabled",  # KI fuer den Haushalt ausgeschaltet
    "model_unavailable",  # Modellserver nicht erreichbar oder kein Chatmodell
    "model_missing",  # eingestelltes Modell nicht vorhanden
    "model_not_loaded",
    "model_context_unknown",  # Fenster unbekannt - es wird nicht gesendet
    "model_input_too_large",  # Spalten + Beispielzeilen passen nicht ins Fenster
    "model_context_overflow",
    "model_budget_exhausted",  # Antwort abgeschnitten (freier Platz aufgebraucht)
    "model_output_unusable",  # Antwort war kein verwertbares JSON
    "model_reserve_tight",
    "model_usage_unavailable",
    "model_reasoning_unsupported",
    "model_schema_unsupported",
    "model_timeout",
    "model_http_error",
    "model_cancelled",
    "model_settings_invalid",
    "model_cap_above_window",
    "model_not_chosen",
    "model_key_missing",
    "model_key_rejected",
    "model_credit_exhausted",
    "model_rate_limited",
    "model_refused",
]


class ImportMappingSuggestionRequest(BaseModel):
    """Optionaler Body fuer den KI-Spaltenvorschlag.

    Normalfall: der Server nimmt die bereits gestagten Zeilen des Laufs
    (import_rows.raw_data) - dann bleibt der Body leer. `sample_rows` gibt es
    nur fuer den Fall, dass der Lauf gar nicht geparst werden konnte (Status
    'failed', also keine Staging-Zeilen) - dort ist ein Mappingvorschlag am
    wertvollsten, die Datei selbst wird serverseitig aber nicht aufbewahrt.
    Die Obergrenze ist bewusst klein: mehr Zeilen bringen keine zusaetzliche
    Information ueber die Spaltenstruktur, wohl aber mehr echte Kontodaten."""

    sample_rows: list[dict[str, str]] | None = Field(default=None, max_length=20)


class ImportMappingSuggestion(BaseModel):
    """Ergebnis des KI-Spaltenvorschlags. IMMER nur ein Vorschlag: der Aufrufer
    zeigt ihn im Mapping-Dialog an, wendet ihn nie automatisch an. Ist
    `available` false, bleibt der bisherige Ablauf unveraendert nutzbar."""

    available: bool
    reason: MappingSuggestionReason
    message: str
    # Spaltennamen, die dem Modell gezeigt wurden. Bei gestagten Zeilen in der
    # von jsonb normalisierten Reihenfolge (import_rows.raw_data), nicht in
    # Dateireihenfolge - stabil, aber nicht als Spaltenposition lesbar.
    source_columns: list[str] = Field(default_factory=list)
    sample_row_count: int = 0
    # kanonisches Feld (MAPPING_FIELDS) -> Quellspalte; enthaelt nur Felder,
    # denen das Modell eine real vorhandene Spalte zugeordnet hat
    column_mapping: dict[str, str] = Field(default_factory=dict)
    date_format: str | None = None  # strftime-Muster, z. B. "%d.%m.%Y"
    decimal_separator: str | None = None  # "," oder "."
    currency: str | None = None  # ISO-4217, z. B. "EUR"
    confidence: float = 0.0
    model_name: str | None = None
    prompt_version: str


class MappingProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    bank_format: ImportFormat
    column_mapping: dict[str, str] = Field(default_factory=dict)


class MappingProfile(MappingProfileCreate):
    id: UUID
    household_id: UUID
    created_at: datetime
