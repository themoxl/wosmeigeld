// Typen fuer den Backend-Vertrag von services/api. Seit Stage 2f aus dem
// OpenAPI-Schema generiert (packages/contracts/src/generated/api-types.ts,
// siehe packages/contracts/README.md) statt von Hand gepflegt - das war
// wiederholt Ursache von Contract-Bugs (Web-Typen drifteten mehrfach von
// den echten Pydantic-Schemas ab). Diese Datei bildet nur noch die im
// App-Code verwendeten Namen auf die generierten Schemas ab; die
// tatsaechliche Feldform kommt immer von dort. Ein paar reine
// Client-Hilfstypen ohne Backend-Gegenstueck (z. B. ListTransactionsParams)
// bleiben unten weiterhin von Hand gepflegt.

import type { components } from "@finanzplaner/contracts";

export type Membership = components["schemas"]["Membership"];

export type Me = components["schemas"]["Me"];

export type ProfileUpdate = components["schemas"]["ProfileUpdate"];

export type Household = components["schemas"]["Household"];

// Stage 2e: Mitglieder-Einladung per Link/Code. `role` hat serverseitig
// einen Default ("editor"), ist im generierten Create-Schema deshalb
// optional (`role?`) - NonNullable holt hier nur die Literal-Union heraus.
export type HouseholdRole = NonNullable<components["schemas"]["HouseholdInviteCreate"]["role"]>;
export type HouseholdMember = components["schemas"]["HouseholdMember"];
export type HouseholdInvite = components["schemas"]["HouseholdInvite"];
export type HouseholdInvitePreview = components["schemas"]["HouseholdInvitePreview"];

// Abschnitt 6.2 des Architekturplans: die acht erlaubten Kontotypen.
export type AccountType = components["schemas"]["AccountCreate"]["account_type"];

export type Account = components["schemas"]["Account"];
export type AccountCreateInput = components["schemas"]["AccountCreate"];
// Phase 2: Konto bearbeiten/schliessen (PATCH /v1/accounts/{id}) - alle
// Felder optional, nur gesetzte Felder werden serverseitig aktualisiert.
export type AccountUpdateInput = components["schemas"]["AccountUpdate"];

// Phase 2: Kontoabstimmung (Abschnitt 6.2/10.3).
export type ReconcileInput = components["schemas"]["ReconcileInput"];
export type Reconciliation = components["schemas"]["Reconciliation"];

// Backend typisiert diese Felder response-seitig bewusst als plain string
// (Category.default_type/budget_bucket), nicht als Literal-Union - diese
// beiden Hilfstypen bleiben deshalb von Hand gepflegt (z. B. fuer
// Dropdown-Optionen), ohne generierte Quelle.
export type DefaultCategoryType = "income" | "expense" | "transfer";
export type BudgetBucket = "needs" | "wants" | "savings";

// HANDKORREKTUR (siehe Kopfkommentar): Mit der Kategorienverwaltung
// (docs/KATEGORISIERUNGSPLAN.md) liefert GET /v1/categories zusaetzlich
// Symbol, Farbe, Sortierung, Synonyme, Beschreibung, semantic_key und
// version - der durchsuchbare Picker braucht diese Felder. Die generierten
// Schemas kennen sie noch nicht; die Quelle der Wahrheit ist bis zur
// naechsten Contract-Generierung ./categoryTypes.ts (handgepflegte
// Spiegelung von services/api/app/schemas/category.py).
export type {
  Category,
  CategoryGroup,
  CatalogCategory,
  CatalogGroup,
  CategoryCatalog,
  CategoryReferenceCounts,
  CategoryDefaultType,
  CategoryBudgetBucket,
  CategoryOrigin,
  CategoryCreateInput,
  CategoryUpdateInput,
  CategoryGroupCreateInput,
  CategoryGroupUpdateInput,
  ReorderRequestInput,
  MovePreview,
  MoveApplyInput,
  ArchivePreview,
  ArchiveApplyInput,
  GroupArchivePreview,
  GroupArchiveApplyInput,
  RestoreRequestInput,
  MergePreview,
  MergeRequestInput,
  MergeApplyInput,
  MergeResult,
  StandardPackageStatus,
  StandardPackagePreview,
  StandardPackageEntry,
  StandardPackageApplyInput,
  StandardPackageResult,
  TransactionCategoryUpdateInput,
  BulkCategoryInput,
  BulkCategoryItem,
  BulkCategoryResult,
  BulkItemStatus,
  BulkSkipReason,
} from "./categoryTypes";

// Transaction.status ist im Backend ebenfalls plain string (nicht
// Literal-constrained) - siehe Kommentar bei DefaultCategoryType oben.
export type TransactionStatus = "pending" | "booked" | "reconciled";

export type Transaction = components["schemas"]["Transaction"];
export type TransactionSplit = components["schemas"]["TransactionSplit"];
export type TransactionDetail = components["schemas"]["TransactionDetail"];
export type TransactionCreateInput = components["schemas"]["TransactionCreate"];
// PATCH /v1/transactions/{id} - Betrag/Kategorie bewusst NICHT hier (siehe
// PUT .../splits weiter unten), damit die Split-Summen-Invariante an einer
// Stelle geprueft wird.
export type TransactionUpdateInput = components["schemas"]["TransactionUpdate"];

// GET /v1/transactions/{id}/provenance - beantwortet "warum ist diese Buchung
// in dieser Kategorie" aus gespeicherten Feldern (Migration 0008).
export type TransactionProvenance = components["schemas"]["TransactionProvenance"];
export type SplitProvenance = components["schemas"]["SplitProvenance"];
export type ImportOrigin = components["schemas"]["ImportOrigin"];
export type CategorySource = SplitProvenance["category_source"];

// Analyse-Abfragevertrag (docs/ANALYSE-EXPLORER-PLAN.md, Baustein B).
// Explorer, Diagramme, Drilldown und Berichte erzeugen dieselbe Struktur.
export type AnalyticsQuery = components["schemas"]["AnalyticsQuery"];
export type AnalyticsResult = components["schemas"]["AnalyticsResult"];
export type AnalyticsColumn = components["schemas"]["ColumnMeta"];
export type AnalyticsRow = AnalyticsResult["rows"][number];
export type DimensionRef = components["schemas"]["DimensionRef"];
export type MeasureRef = components["schemas"]["MeasureRef"];
export type AnalyticsCondition = components["schemas"]["Condition"];
export type AnalyticsFilterNode = components["schemas"]["FilterNode"];
export type PeriodSpec = components["schemas"]["PeriodSpec"];
export type TopNSpec = components["schemas"]["TopNSpec"];
export type FieldCatalog = components["schemas"]["FieldCatalog"];
export type FieldMeta = components["schemas"]["FieldMeta"];
export type FacetRequest = components["schemas"]["FacetRequest"];
export type FacetResult = components["schemas"]["FacetResult"];
export type ValidationResult = components["schemas"]["ValidationResult"];
export type AnalyticsAggregate = MeasureRef["aggregate"];
export type AnalyticsCompareOp = AnalyticsCondition["op"];

// Finanz-Empfehlungen (GET /v1/insights). Alle Betraege entstehen
// serverseitig aus SQL; `ai_advice` ist die optionale Formulierung eines
// lokalen Modells und enthaelt vertraglich keine eigenen Zahlen.
export type Insight = components["schemas"]["Insight"];
// Seit den Vertragsauswertungen zusaetzlich "contract_unused" und
// "contract_duplicate"; "price_increase" kommt jetzt mit Vertragsbezug aus
// contract_amount_history statt aus einer Median-Schaetzung je Empfaenger.
export type InsightKind = Insight["kind"];
export type InsightSeverity = Insight["severity"];
export type InsightReport = components["schemas"]["InsightReport"];
export type InsightEvidence = components["schemas"]["InsightEvidence"];
// ACHTUNG: das Sprungziel eines Vertrags steht in `drilldown.contract_id`,
// NICHT am Insight selbst. `field`/`value` bleiben der Buchungsfilter fuer den
// Explorer - der kennt keine Vertragsdimension.
export type InsightDrilldown = components["schemas"]["InsightDrilldown"];

export type SplitInput = components["schemas"]["SplitInput"];
// PUT /v1/transactions/{id}/splits - sum(splits.amount_minor) MUSS
// amount_minor ergeben (serverseitig per 422 erzwungen; clientseitig im
// Split-Editor zusaetzlich vorab geprueft).
export type TransactionSplitsUpdateInput = components["schemas"]["TransactionSplitsUpdate"];

export type BulkCategorizeInput = components["schemas"]["BulkCategorizeInput"];
export type BulkCategorizeResult = components["schemas"]["BulkCategorizeResult"];

export type TransferLinkCreateInput = components["schemas"]["TransferLinkCreate"];
export type TransferLink = components["schemas"]["TransferLink"];

// Reiner Query-Param-Typ fuer GET /v1/transactions - kein Body-Schema, kein
// Backend-Gegenstueck in der OpenAPI-Spezifikation.
export interface ListTransactionsParams {
  accountId?: string;
  fromDate?: string;
  toDate?: string;
  /** Volltext ueber Empfaenger, Verwendungszweck und Notiz - serverseitig. */
  search?: string;
  /** Wie `search`, aber als Postgres-Regex (~*, case-insensitive) statt
   *  Substring. Schliesst sich mit `search` aus - der Server lehnt beides
   *  zusammen mit 422 ab. */
  searchRegex?: string;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Phase 3: Importplattform (CSV, XLSX, MT940, CAMT, PayPal)
// ---------------------------------------------------------------------------

export type ImportFormat = components["schemas"]["MappingProfile"]["bank_format"];
export type ImportStatus = components["schemas"]["ImportRun"]["status"];
export type RowStatus = components["schemas"]["ImportRow"]["status"];

export type ImportRun = components["schemas"]["ImportRun"];
export type ImportRow = components["schemas"]["ImportRow"];
export type ImportMappingProfile = components["schemas"]["MappingProfile"];

// Zuruecknehmen eines bereits uebernommenen Laufs. Der Blocker traegt neben
// dem Schluessel eine fertige Beschriftung vom Server - die Gruende haengen
// an fachlichen Regeln, die dort stehen, nicht in der Oberflaeche.
export type ImportRevertPreview = components["schemas"]["ImportRevertPreview"];
export type ImportRevertResult = components["schemas"]["ImportRevertResult"];
export type ImportRevertBlocker = components["schemas"]["ImportRevertBlocker"];

// HANDKORREKTUR (siehe Kopfkommentar): PATCH /v1/imports/{run_id}
// (Pydantic-Schema ImportRunUpdate) ist im generierten Vertrag noch nicht
// enthalten. Beide Felder sind optional; serverseitig wird mit
// model_dump(exclude_unset=True) gearbeitet, es werden also ausschliesslich
// die tatsaechlich mitgeschickten Felder geaendert. Ein leerer Body {} ist
// erlaubt und laesst den Lauf unveraendert.
// Quelle: services/api/app/schemas/import_platform.py.
export interface ImportRunUpdateInput {
  /** Zielkonto des Laufs; null loest die Zuordnung wieder. */
  account_id?: string | null;
  /**
   * Verknuepftes Mapping-Profil. ACHTUNG: aendert NUR die Verknuepfung, es
   * wird nicht neu geparst - Zeilen und Betraege bleiben, wie sie sind.
   */
  mapping_profile_id?: string | null;
}

// POST /v1/imports/{run_id}/mapping-suggestion (KI-Spaltenvorschlag).
// `message` ist deutscher Klartext - auch im Ausfall-Fall gesetzt.
// `reason` fuehrt neben ok/no_sample_rows/suggestion_incomplete ALLE Gruende
// des KI-Kerns (services/api/app/llm/reasons.py); lib/aiReasons.ts leitet
// daraus die Liste der KI-Gruende ab. `column_mapping` traegt die Schluessel
// wie im column_mapping von POST /v1/imports/mapping-profiles.
export type ImportMappingSuggestion = components["schemas"]["ImportMappingSuggestion"];
export type MappingSuggestionReason = ImportMappingSuggestion["reason"];

// ---------------------------------------------------------------------------
// Phase 4: Regelengine, Review-Inbox & Fixkosten
// ---------------------------------------------------------------------------

// HANDKORREKTUR (siehe Kopfkommentar): Das Backend liefert seit der Korrektur
// der Regelengine zusaetzlich `version` (Trigger
// classification_rules_version_trg) und kennt PATCH /v1/rules/{id}. Der
// Generator in packages/contracts ist noch nicht nachgezogen - sobald er das
// ist, ersetzen die generierten Schemas diese beiden Deklarationen wieder.
export type ClassificationRule = components["schemas"]["ClassificationRule"] & {
  version: number;
};
export type ClassificationRuleCreateInput = components["schemas"]["ClassificationRuleCreate"];
// ClassificationRuleUpdate: alle Felder optional, `source` ist nicht
// aenderbar (siehe _RULE_UPDATABLE_COLUMNS in services/api/.../rules.py).
export type ClassificationRuleUpdateInput = Partial<Omit<ClassificationRuleCreateInput, "source">>;

// Rueckwirkende Regelanwendung: Vorschau und Anwendung teilen sich Ein- und
// Ausgabeform, damit die Oberflaeche exakt anzeigen kann, was passieren wird.
export type RuleApplyInput = components["schemas"]["RuleApplyInput"];
export type RuleApplyItem = components["schemas"]["RuleApplyItem"];
export type RuleApplyResult = components["schemas"]["RuleApplyResult"];
export type RuleApplyAllInput = components["schemas"]["RuleApplyAllInput"];
export type RuleApplyAllResult = components["schemas"]["RuleApplyAllResult"];
export type RuleApplyRuleSummary = components["schemas"]["RuleApplyRuleSummary"];

// Vertraege loesen RecurringRule und Subscription ab (Migration 0014). Die
// beiden Alttypen bleiben nur, weil `/v1/recurring` und `/v1/subscriptions` als
// Kompatibilitaetsschicht fuer ausgelieferte Mobile-Versionen weiterlaufen.
export type RecurringRule = components["schemas"]["RecurringRule"];
export type RecurringRuleCreateInput = components["schemas"]["RecurringRuleCreate"];

export type Subscription = components["schemas"]["Subscription"];
export type SubscriptionCreateInput = components["schemas"]["SubscriptionCreate"];

export type Contract = components["schemas"]["Contract"];
export type ContractCreateInput = components["schemas"]["ContractCreate"];
export type ContractUpdateInput = components["schemas"]["ContractUpdate"];
export type ContractStatus = Contract["status"];
export type ContractType = Contract["contract_type"];
export type ContractDirection = Contract["direction"];
export type ContractFrequency = Contract["frequency"];
export type ContractDueRule = Contract["due_rule"];
export type ContractSource = Contract["source"];
// Woher der Vertragsschluessel stammt. `mandate` ist die SEPA-Mandatsreferenz
// und damit die belastbarste Zuordnung; `purpose_pattern` ist geraten, `manual`
// vom Nutzer gesetzt - die Oberflaeche darf die drei nicht gleich darstellen.
export type ContractKeySource = NonNullable<Contract["contract_key_source"]>;
export type ContractSummary = components["schemas"]["ContractSummary"];
export type ContractTypeSummary = components["schemas"]["ContractTypeSummary"];
export type ContractCategorySummary = components["schemas"]["ContractCategorySummary"];
export type ContractUpcoming = components["schemas"]["ContractUpcoming"];
export type ContractEnding = components["schemas"]["ContractEnding"];
export type ContractAmountEntry = components["schemas"]["ContractAmountEntry"];
export type ContractOccurrence = components["schemas"]["ContractOccurrence"];
export type ContractEvent = components["schemas"]["ContractEvent"];
export type ContractMergePreview = components["schemas"]["ContractMergePreview"];
export type ContractMergeResult = components["schemas"]["ContractMergeResult"];
export type ContractMergeUndoPreview = components["schemas"]["ContractMergeUndoPreview"];
export type ContractMergeUndoResult = components["schemas"]["ContractMergeUndoResult"];
export type ContractReassignPreview = components["schemas"]["ContractReassignPreview"];
export type ContractReassignResult = components["schemas"]["ContractReassignResult"];
export type ContractDuplicateSuggestion = components["schemas"]["ReviewItemContractDuplicate"];
export type ContractDuplicateSuggestionsResponse =
  components["schemas"]["ContractDuplicateSuggestionsResponse"];
export type ContractDuplicateCandidate = components["schemas"]["ContractDuplicateCandidate"];

// POST /v1/contracts/ai-crosscheck (Plan 19.11): das lokale Sprachmodell
// beurteilt Vertragspaare, die deterministisch nicht entscheidbar sind. Das
// Antwortschema kennt bewusst KEIN Konfidenz-/Prozentfeld - `gehoert_zusammen`
// plus `begruendung` sind ein Pruefvorschlag, keine Tatsache, und `quelle:"ai"`
// verhindert, dass die Oberflaeche ihn neben einem deterministischen Beleg als
// gleichwertig darstellt (Leitsatz 8).
export type ContractAICrosscheckRequest = components["schemas"]["ContractAICrosscheckRequest"];
export type ContractAICrosscheckResponse = components["schemas"]["ContractAICrosscheckResponse"];
export type ContractAICrosscheckItem = components["schemas"]["ContractAICrosscheckItem"];
export type ContractAICrosscheckPartner = components["schemas"]["ContractAICrosscheckPartner"];
export type ContractAICrosscheckSkip = components["schemas"]["ContractAICrosscheckSkip"];
export type ContractAICrosscheckSkipReason = ContractAICrosscheckSkip["grund"];

// HANDKORREKTUR (siehe Kopfkommentar): Belege je Preisstufe, freie Buchungen,
// Zuordnungsvorschau, Neuberechnung und die Buchungspruefung durch das
// Sprachmodell (Plan 19.7/19.11). Die zwoelf Schemata stehen bereits in
// services/api/app/schemas/contract.py, aber noch nicht in
// packages/contracts/src/generated/api-types.ts - diese Datei wird aus dem
// OpenAPI-Schema erzeugt und gehoert einem anderen Arbeitsstrang.
//
// Deshalb hier ausnahmsweise von Hand gespiegelt, mit exakt den Feldnamen und
// Optionalitaeten der Pydantic-Modelle. Bei der naechsten Generierung ersetzt
// ein `components["schemas"][...]`-Alias je Zeile diesen Block ersatzlos; die
// Namen sind bewusst identisch mit den OpenAPI-Schemanamen, damit das ein
// reines Loeschen wird und keine Umbenennung.

/** Eine Buchung als Beleg. `amount_minor` traegt das ORIGINALVORZEICHEN der
 *  Buchung (Ausgaben negativ) - anders als `Contract.amount_minor`, der immer
 *  positiv ist. Wer beides gleich einfaerbt, macht aus einer Abbuchung eine
 *  Gutschrift. */
export interface ContractEvidenceTransaction {
  transaction_id: string;
  booked_date: string;
  amount_minor: number;
  payee_label: string | null;
  purpose_excerpt: string | null;
  account_id?: string | null;
  /** Nur bei bereits zugeordneten Buchungen gesetzt. */
  due_date?: string | null;
  /** Diese Buchung steht in `source_transaction_id` der Stufe. */
  is_stage_source?: boolean;
}

/** Eine Preisstufe samt der Buchungen in ihrem Zeitraum. `has_evidence=false`
 *  ist die fachlich wichtige Aussage: die Stufe behauptet einen Preis, den
 *  keine einzige Buchung stuetzt. */
export interface ContractAmountStageEvidence {
  stage: ContractAmountEntry;
  transactions: ContractEvidenceTransaction[];
  transaction_count: number;
  has_source_transaction: boolean;
  has_evidence: boolean;
}

export interface ContractAmountEvidenceResponse {
  contract_id: string;
  stages: ContractAmountStageEvidence[];
  /** Zugeordnete Buchungen, die in KEINE Preisstufe fallen - im
   *  Uebernahmefall betrifft das haeufig alle Buchungen eines Vertrags. */
  transactions_outside_stages: ContractEvidenceTransaction[];
  stages_without_evidence: number;
  assigned_transaction_count: number;
}

/** Die tatsaechlich angewendete Suche - kommt immer mit der Antwort zurueck,
 *  damit sichtbar ist, wonach ueberhaupt gesucht wurde. */
export interface ContractFreeTransactionFilter {
  date_from: string;
  date_to: string;
  amount_min_minor: number | null;
  amount_max_minor: number | null;
  search: string | null;
  from_contract_defaults: boolean;
}

export interface ContractFreeTransactionsResponse {
  items: ContractEvidenceTransaction[];
  /** Echte Trefferzahl der Suche, nicht die Seitenlaenge. */
  total: number;
  truncated: boolean;
  limit: number;
  offset: number;
  applied_filter: ContractFreeTransactionFilter;
}

/** Eine geplante Preisstufe der Neuberechnung - eine Stufe, die es noch nicht
 *  gibt. Bewusst getrennt von `ContractAmountEntry` (keine id, dafuer die Zahl
 *  der tragenden Buchungen). */
export interface ContractAmountStagePlan {
  amount_minor: number;
  valid_from: string;
  valid_to: string | null;
  delta_minor: number | null;
  transaction_count: number;
  source_transaction_id: string;
}

export interface ContractAssignmentPreviewRequest {
  transaction_ids: string[];
}

/** Woher die Buchungen kommen. `contract_id === null` heisst: bisher frei. */
export interface ContractAssignmentSource {
  contract_id: string | null;
  contract_name: string | null;
  transaction_count: number;
}

export interface ContractAssignmentPreview {
  contract_id: string;
  contract_name: string;
  transactions: ContractEvidenceTransaction[];
  sources: ContractAssignmentSource[];
  unknown_transaction_ids: string[];
  first_seen_date_before: string | null;
  first_seen_date_after: string | null;
  last_seen_date_before: string | null;
  last_seen_date_after: string | null;
  amount_stages_before: ContractAmountEntry[];
  amount_stages_after: ContractAmountStagePlan[];
  stages_without_evidence_before: number;
}

export interface ContractAmountRecalculationResult {
  contract_id: string;
  changed: boolean;
  stages_written: number;
  stages_removed: number;
  manual_stages_kept: number;
  amount_stages: ContractAmountEntry[];
  first_seen_date: string | null;
  last_seen_date: string | null;
  /** Betrag der juengsten HERGELEITETEN Stufe. Kann von
   *  `contract_amount_minor` abweichen: die Neuberechnung fasst den Sollbetrag
   *  ausdruecklich nicht an, damit sich die Fixkostensumme nicht unbemerkt
   *  verschiebt. Die Oberflaeche muss die Abweichung zeigen. */
  resulting_amount_minor: number | null;
  contract_amount_minor: number;
}

export interface ContractAITransactionCheckRequest {
  transaction_ids?: string[] | null;
}

/** Eine beurteilte Buchung. Vorschlag, kein Ergebnis - kein Konfidenz- oder
 *  Prozentfeld, und `quelle` bleibt sichtbar (Leitsatz 8). */
export interface ContractAITransactionItem {
  transaction_id: string;
  booked_date: string;
  amount_minor: number;
  payee_label: string | null;
  gehoert_zum_vertrag: boolean;
  begruendung: string;
  quelle: "ai";
}

export type ContractAITransactionSkipReason = "not_free" | "no_model_answer";

export interface ContractAITransactionSkip {
  transaction_id: string;
  booked_date: string | null;
  amount_minor: number | null;
  grund: ContractAITransactionSkipReason;
}

export interface ContractAITransactionCheckResponse {
  contract_id: string;
  items: ContractAITransactionItem[];
  geprueft: number;
  uebersprungen: number;
  uebersprungen_gruende: ContractAITransactionSkip[];
  truncated: boolean;
  kandidaten_gesamt: number;
  vorauswahl: ContractFreeTransactionFilter;
  modell_name: string | null;
  meldung: string | null;
}

export type ContractSettings = components["schemas"]["ContractSettings"];
export type ContractSettingsUpdateInput = components["schemas"]["ContractSettingsUpdate"];
export type ContractStatusChangeInput = components["schemas"]["ContractStatusChange"];
export type ContractRescanResult = components["schemas"]["ContractRescanResult"];

// Kuendigungsfrist (Migration 0015). Die Kante ist `deadline_on`, nicht
// `term_ends_on` - wer bis zum Laufzeitende wartet, hat die Frist verpasst.
// `kind` trennt zwei Faelle mit derselben Darstellung: bei `fixed_term` laeuft
// eine Frist ab und `days_left` ist ein Countdown, bei `open_ended` gibt es
// keine Frist und `days_left` ist ohne Aussage.
export type ContractTermDeadline = components["schemas"]["ContractTermDeadline"];
export type ContractTermDeadlineKind = ContractTermDeadline["kind"];
// Projektion derselben Frist in der Uebersicht (nur 0-120 Tage Restlaufzeit).
export type ContractTermSoon = components["schemas"]["ContractTermSoon"];
// Rangliste der teuersten Vertraege. `amount_minor` je Periode steht bewusst
// neben der Jahressumme - sonst waere eine Jahrespraemie nicht von zwoelf
// Monatsbetraegen zu unterscheiden.
export type ContractYearlyCost = components["schemas"]["ContractYearlyCost"];

// GET /v1/contracts/cashflow: Vertrags-Cashflow je KALENDERZEITRAUM. Loest die
// rollierende 30/90-Tage-Prognose ab - dort fiel ein monatlicher Vertrag je
// nach Faelligkeitstag ein- oder zweimal ins Fenster, und die Summe stimmte
// weder mit der Fixkostensumme noch mit der Fixkostenquote im Budget ueberein.
export type ContractCashflowResponse = components["schemas"]["ContractCashflowResponse"];
export type ContractCashflowPeriod = components["schemas"]["ContractCashflowPeriod"];
export type CashflowGranularity = ContractCashflowResponse["granularity"];

// Fixkostenziel: Quote fuehrt, absoluter Betrag ist optional. `is_configured`
// trennt "noch kein Ziel gesetzt" vom Vorgabewert - sonst meldete die
// Oberflaeche eine Zielverfehlung, die niemand gesetzt hat.
export type ContractCostTarget = components["schemas"]["ContractCostTargetView"];
export type ContractCostTargetInput = components["schemas"]["ContractCostTargetInput"];

// Senkungsszenario. Aendert keinen Vertrag: ein geplanter Kuendigungswunsch ist
// keine Kuendigung, und die Fixkostensumme darf sich davon nicht bewegen.
export type ContractScenario = components["schemas"]["ContractScenario"];
export type ContractScenarioItem = components["schemas"]["ContractScenarioItem"];
export type ContractScenarioItemInput = components["schemas"]["ContractScenarioItemInput"];
export type ContractScenarioAction = ContractScenarioItem["action"];
export type ContractScenarioCreateInput = components["schemas"]["ContractScenarioCreate"];
export type ContractScenarioUpdateInput = components["schemas"]["ContractScenarioUpdate"];

// GET /v1/contracts/provider-suggestions: globaler Anbieterkatalog fuer das
// Anlegen-Formular. `category_id` ist die im Haushalt aufgeloeste Kategorie zu
// `default_category_key` und bleibt null, wenn der Haushalt die
// Standardkategorie umbenannt, archiviert oder nie installiert hat.
export type ContractProviderSuggestion = components["schemas"]["ContractProviderSuggestion"];

export type ReviewItemUnclassified = components["schemas"]["ReviewItemUnclassified"];
export type ReviewItemDuplicate = components["schemas"]["ReviewItemDuplicate"];
export type BookedDuplicateScanResult = components["schemas"]["BookedDuplicateScanResult"];
export type BookedDuplicatePairInput = components["schemas"]["BookedDuplicatePairInput"];
export type BookedDuplicateAssessmentInput =
  components["schemas"]["BookedDuplicateAssessmentInput"];
export type BookedDuplicateAssessmentResult =
  components["schemas"]["BookedDuplicateAssessmentResult"];
export type BookedDuplicateAssessment = components["schemas"]["BookedDuplicateAssessment"];
export type ReviewItemBookedDuplicate = components["schemas"]["ReviewItemBookedDuplicate"];
export type ResolveBookedDuplicateInput = components["schemas"]["ResolveBookedDuplicateInput"];
export type BulkResolveBookedDuplicatesInput =
  components["schemas"]["BulkResolveBookedDuplicatesInput"];
export type BulkResolveBookedDuplicatesResult =
  components["schemas"]["BulkResolveBookedDuplicatesResult"];
export type ReviewItemTransfer = components["schemas"]["ReviewItemTransfer"];
export type ReviewItemContractEndedCheck = components["schemas"]["ReviewItemContractEndedCheck"];
// Rueckfrage bei erheblich geaendertem Betrag (Plan 19.11). Anker ist das
// EREIGNIS, nicht der Vertrag: ein Vertrag kann mehrere unbestaetigte
// Preisstufen tragen. `delta_minor`/`delta_pct` sind vorzeichenbehaftet -
// negativ heisst guenstiger geworden, und genau dieser Fall (ein Festnetz-
// vertrag, dessen Preis von 49,95 auf 39,95 EUR faellt) ist der Anlass.
export type ReviewItemContractAmountChangeCheck =
  components["schemas"]["ReviewItemContractAmountChangeCheck"];
export type ReviewInboxResponse = components["schemas"]["ReviewInboxResponse"];

// VERALTET: der Server fuellt `recurring_suggestions` nicht mehr (Erkennung v2,
// siehe ReviewItemContractSuggestion unten). Die Typen bleiben nur, solange der
// alte Detail-Endpunkt /v1/review/recurring-suggestions/{id} antwortet.
export type ReviewItemRecurring = components["schemas"]["ReviewItemRecurring"];
export type ReviewRecurringTransaction = components["schemas"]["ReviewRecurringTransaction"];
export type ReviewRecurringDetail = components["schemas"]["ReviewRecurringDetail"];

// Erkennung v2 (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, Abschnitt 6). Zwei
// Unterschiede zum abgeloesten ReviewItemRecurring sind bedeutungstragend:
// `amount_minor` ist wie in `contracts` IMMER positiv (Richtung in
// `direction`), waehrend das alte `expected_amount_minor` negativ war - und
// `amount_steps` traegt die erkannte Betragstreppe, aus der beim Bestaetigen
// der Betragsverlauf des Vertrags entsteht.
export type ReviewItemContractSuggestion = components["schemas"]["ReviewItemContractSuggestion"];
export type ReviewContractAmountStep = components["schemas"]["ReviewContractAmountStep"];
export type ReviewContractScore = components["schemas"]["ReviewContractScore"];
export type ReviewContractTransaction = components["schemas"]["ReviewContractTransaction"];
export type ReviewContractSuggestionDetail =
  components["schemas"]["ReviewContractSuggestionDetail"];
// "suggested" ab Score 0.55, "uncertain" im Band 0.40-0.55. Unsichere
// Vorschlaege stehen bewusst in derselben Liste, muessen aber als solche
// erkennbar bleiben (zusaetzlich `is_uncertain` am Item).
export type ContractSuggestionConfidence = ReviewItemContractSuggestion["confidence"];

// Altbestand-Dubletten: zwei Eintraege desselben Vertrags, zeitlich getrennt.
// `target_id` ist immer der juengere Vertrag, `source_ids` sind die aelteren.
export type ReviewItemContractDuplicate = components["schemas"]["ReviewItemContractDuplicate"];
export type ReviewContractDuplicatePartner =
  components["schemas"]["ReviewContractDuplicatePartner"];

// Das Backend exponiert fuer diese beiden Endpunkt-Bodies keinen einzelnen
// Schema-Namen, sondern je einen `oneOf` aus acht bzw. drei separaten
// Pydantic-Klassen (discriminated union ueber `type`) - deshalb hier aus den
// generierten Bausteinen zusammengesetzt statt eines direkten Alias.
export type ReviewAcceptInput =
  | components["schemas"]["AcceptUnclassified"]
  | components["schemas"]["AcceptDuplicate"]
  | components["schemas"]["AcceptTransfer"]
  | components["schemas"]["AcceptRecurring"]
  | components["schemas"]["AcceptContractEnded"]
  | components["schemas"]["AcceptContractSuggestion"]
  | components["schemas"]["AcceptContractDuplicate"]
  | components["schemas"]["AcceptContractAmountChange"];

// Einzeln exportiert, weil die Inbox beide Bodies mit `dismiss: true` auch zum
// VERNEINEN verwendet - der Aufrufer braucht den Typ, nicht nur die Union.
export type AcceptContractSuggestionInput = components["schemas"]["AcceptContractSuggestion"];
export type AcceptContractDuplicateInput = components["schemas"]["AcceptContractDuplicate"];

export type CorrectUnclassifiedInput = components["schemas"]["CorrectUnclassified"] & {
  rule_match_payee?: boolean;
  rule_match_purpose?: boolean;
  rule_match_exact_amount?: boolean;
  rule_match_account?: boolean;
  apply_rule_to_existing?: boolean;
};

export type ReviewRulePreviewInput = {
  transaction_id: string;
  category_id?: string | null;
  payee_pattern?: string | null;
  match_payee?: boolean;
  match_purpose?: boolean;
  match_exact_amount?: boolean;
  match_account?: boolean;
};

export type ReviewHistoryMatch = {
  matched: boolean;
  category_id: string | null;
  category_label: string | null;
  evidence_count: number;
  ambiguous: boolean;
  source: "rule" | "history" | null;
  rule_id: string | null;
  rule_name: string | null;
};

export type ReviewHistoryMatchItem = ReviewHistoryMatch & {
  transaction_id: string;
};

export type ReviewHistoryMatchesResponse = {
  checked_count: number;
  matched_count: number;
  items: ReviewHistoryMatchItem[];
};

export type ReviewCorrectInput =
  | CorrectUnclassifiedInput
  | components["schemas"]["CorrectDuplicate"]
  | components["schemas"]["CorrectTransfer"];

// ---------------------------------------------------------------------------
// Phase 5: Budgetkern & Dashboard
// ---------------------------------------------------------------------------

export type RolloverRule = components["schemas"]["CategoryBudgetStatus"]["rollover_rule"];

// HANDKORREKTUR (siehe Kopfkommentar): Der Budgetkern trennt Einnahmen jetzt
// strikt von den Ausgaben (`income_actual_minor` statt eines negativen
// `spent_minor`) und fuehrt den Uebertrag auf Gruppen- und Gesamtebene mit
// (`total_rollover_minor`). Die generierten Schemas kennen diese Felder noch
// nicht; die Schnittmengen unten bilden den echten Pydantic-Vertrag ab
// (services/api/app/schemas/budgets.py).
export type CategoryAllocation = components["schemas"]["CategoryBudgetStatus"] & {
  // 'income' | 'expense' | 'transfer' | null - backendseitig plain string.
  default_type: string | null;
  // Ist-Einnahmen als positive Zahl; bei Ausgabenkategorien immer 0.
  income_actual_minor: number;
};
export type GroupAllocation = Omit<
  components["schemas"]["CategoryGroupBudgetStatus"],
  "categories"
> & {
  total_rollover_minor: number;
  total_income_actual_minor: number;
  categories: CategoryAllocation[];
};
export type BudgetSummary = Omit<components["schemas"]["BudgetSummary"], "groups"> & {
  total_rollover_minor: number;
  groups: GroupAllocation[];
};
// Bezugsgroesse der Fixkostenquote: "actual" sind die Ist-Einnahmen des Monats,
// "contract" der aus Einnahmevertraegen normierte Ersatzwert, solange noch
// nichts gebucht ist. Die Oberflaeche muss den Unterschied benennen, sonst
// steht eine Quote da, die niemand nachrechnen kann.
export type FixedCostsIncomeBasis = BudgetSummary["fixed_costs_income_basis"];
export type CloseMonthResponse = components["schemas"]["CloseMonthResponse"];
export type BudgetAllocationInput = components["schemas"]["BudgetAllocationInput"];

export type AnnualMatrixMonthData = components["schemas"]["AnnualMatrixMonthData"] & {
  income_actual_minor: number;
};
export type AnnualMatrixRow = Omit<components["schemas"]["AnnualMatrixRow"], "months"> & {
  default_type: string | null;
  months: AnnualMatrixMonthData[];
  total_income_actual_minor: number;
};
export type AnnualMatrixResponse = Omit<components["schemas"]["AnnualMatrixResponse"], "rows"> & {
  rows: AnnualMatrixRow[];
};

export type ReserveOverview = components["schemas"]["ReserveOverviewResponse"];
export type ReservePot = components["schemas"]["ReservePotResponse"];
export type ReserveBatch = components["schemas"]["ReserveBatchResponse"];
export type ReserveDepositInput = components["schemas"]["ReserveDepositRequest"];
export type ReserveWithdrawalInput = components["schemas"]["ReserveWithdrawalRequest"];
export type ReserveReallocationInput = components["schemas"]["ReserveReallocationRequest"];
export type ReserveDistributionPreview =
  components["schemas"]["ReserveDistributionPreviewResponse"];

export type TopExpenseCategory = components["schemas"]["CategoryReportData"];
export type CashflowMonthData = components["schemas"]["CashflowMonthData"];
export type OverviewReportResponse = components["schemas"]["OverviewReportResponse"];
export type CashflowTrendResponse = components["schemas"]["CashflowTrendResponse"];

// GET /v1/reports/fixed-costs-trend: vereinbarte Fixkosten je Monat aus
// contract_amount_history - also der zum Monatsletzten GUELTIGE Preis, nicht
// der tatsaechlich gebuchte Betrag. Der Statusfilter ist hier weiter als in
// BudgetSummary.fixed_costs_monthly_minor (beendete Vertraege bleiben in den
// Monaten stehen, in denen sie liefen); beide Zahlen sind deshalb bewusst
// nicht identisch.
export type FixedCostsTypeData = components["schemas"]["FixedCostsTypeData"];
export type FixedCostsMonthData = components["schemas"]["FixedCostsMonthData"];
export type FixedCostsTrendResponse = components["schemas"]["FixedCostsTrendResponse"];

// ---------------------------------------------------------------------------
// Phase 6: Lokale KI / LM Studio Integration
// ---------------------------------------------------------------------------

export type AIHealthResponse = components["schemas"]["AIHealthResponse"];
// Modellwahl und Token-Budget je Haushalt (Migration 0011).
export type AIModelInfo = components["schemas"]["AIModelInfo"];
export type AIModelCatalog = components["schemas"]["AIModelCatalog"];
export type AISamplingProfile = components["schemas"]["AISamplingProfile"];
// Kontextfenster und Reasoning je Haushalt, in LM Studio durchgestellt
// (Migration 0012).
export type AIModelLoadResult = components["schemas"]["AIModelLoadResult"];
export type AISettings = components["schemas"]["AISettings"];
export type AISettingsUpdate = components["schemas"]["AISettingsUpdate"];
export type AISettingsField = NonNullable<AISettingsUpdate["clear_fields"]>[number];
// Waehlbare KI-Anbieter (services/api/app/llm/providers.py): lokal oder
// Anthropic (Claude). Ein API-Schluessel geht nur hinein - zurueck kommt nur,
// ob einer hinterlegt ist ("…abcd"), nie der Wert.
export type AIProviderInfo = components["schemas"]["AIProviderInfo"];
export type AIKeyStatus = components["schemas"]["AIKeyStatus"];
export type AIKeyInput = components["schemas"]["AIKeyInput"];
export type ProviderId = AIProviderInfo["id"];
// Relatives KI-Budget (docs/KI-BUDGET-UND-KI-FELD-PLAN.md, Etappe 1): wirksames
// Fenster, Messung Zeichen/Token und benannte Befunde aus GET /v1/ai/health.
export type AIWarning = components["schemas"]["AIWarning"];
export type AIWindowInfo = components["schemas"]["AIWindowInfo"];
export type AIMeasurementInfo = components["schemas"]["AIMeasurementInfo"];
// Laufzeitanzeige der Warteschlange: geladenes Modell, Kontextfenster, GPU.
export type AIRuntimeStatus = components["schemas"]["AIRuntimeStatus"];
export type AIRuntimeModel = components["schemas"]["AIRuntimeModel"];
export type AIRuntimeGpu = components["schemas"]["AIRuntimeGpu"];
export type AIClassifyInput = components["schemas"]["AIClassifyInput"];
export type AIClassifyResult = components["schemas"]["AIClassifyResult"];
export type AIDecisionLog = components["schemas"]["AIDecisionLog"];

// Sammelübernahme sicherer KI-Vorschläge (Review-Inbox). Der Server entscheidet
// je Buchung; die Oberfläche zeigt das Ergebnis pro Buchung an, statt eine
// Gesamtzahl zu behaupten.
export type AIProposalApplyInput = components["schemas"]["AIProposalApplyInput"] & {
  classification_run_id?: string | null;
};
export type AIProposalBatchResult = components["schemas"]["AIProposalBatchResult"];
export type AIProposalItem = components["schemas"]["AIProposalItem"] & {
  source?: "ai" | "history" | null;
};

// Batch-Klassifikationslauf mit Fortschrittsanzeige (Polling, analog zu ImportRun).
export type ClassificationRun = components["schemas"]["ClassificationRun"];
export type ClassificationRunStatus = ClassificationRun["status"];

// ---------------------------------------------------------------------------
// Phase 7: Sparziele, Wunschliste, Kaufampel, Vermögen, Schulden & Nettovermögen
// ---------------------------------------------------------------------------

export type GoalStatus = components["schemas"]["SavingsGoalResponse"]["status"];
export type WishlistStatus = components["schemas"]["WishlistItemResponse"]["status"];
export type AssetType = components["schemas"]["AssetDebtResponse"]["type"];
export type AssetClass = components["schemas"]["AssetDebtResponse"]["asset_class"];
export type TrafficLightStatus = components["schemas"]["WishlistItemResponse"]["buy_traffic_light"];

export type SavingsGoal = components["schemas"]["SavingsGoalResponse"];
export type SavingsGoalCreateInput = components["schemas"]["SavingsGoalCreate"];
export type SavingsGoalUpdateInput = components["schemas"]["SavingsGoalUpdate"];
export type SavingsPlanningMode = SavingsGoal["planning_mode"];
export type SavingsPlanPreviewInput = components["schemas"]["SavingsPlanPreviewRequest"];
export type SavingsPlanPreview = components["schemas"]["SavingsPlanPreviewResponse"];

export type WishlistItem = components["schemas"]["WishlistItemResponse"];
export type WishlistItemCreateInput = components["schemas"]["WishlistItemCreate"];
export type WishlistItemUpdateInput = components["schemas"]["WishlistItemUpdate"];
export type WishlistSavingsPlanCreateInput = components["schemas"]["WishlistSavingsPlanCreate"];
export type WishlistSavingsPlanResult = components["schemas"]["WishlistSavingsPlanResponse"];

export type AssetDebt = components["schemas"]["AssetDebtResponse"];
export type AssetDebtCreateInput = components["schemas"]["AssetDebtCreate"];
export type AssetDebtUpdateInput = components["schemas"]["AssetDebtUpdate"];

// HANDKORREKTUR (siehe Kopfkommentar): Bewertungshistorie einer Vermoegens-
// bzw. Schuldenposition (GET /v1/assets/{id}/valuations, Pydantic-Schema
// AssetValuationResponse). Im generierten Vertrag noch nicht enthalten.
export interface AssetValuation {
  id: string;
  asset_id: string;
  /** ISO-Datum (YYYY-MM-DD) des Bewertungsstichtags. */
  valued_on: string;
  value_minor: number;
  created_at: string;
}

export type NetWorthReportResponse = components["schemas"]["NetWorthReportResponse"];

// Rueckwirkende Sparziel-Zuordnung (Architekturplan 6.7, Option (c) neben
// der direkten Zuordnung am Split via TransactionSplit.goal_id).
export type UnassignedGoalSplitCandidate = components["schemas"]["UnassignedGoalSplitCandidate"];
export type BulkAssignGoalInput = components["schemas"]["BulkAssignGoalInput"];
export type BulkAssignGoalResult = components["schemas"]["BulkAssignGoalResult"];

// ---------------------------------------------------------------------------
// Phase 8: Lebensmittelplanung (Essensplan, Einkaufsliste, Wochenbudget)
// ---------------------------------------------------------------------------

// app/schemas/grocery.py und app/schemas/groceries.py fuehren beide ein
// `DishResponse`, `MealWeekResponse` und `ShoppingListResponse`. FastAPI
// qualifiziert bei Namensgleichheit mit dem Modulpfad, deshalb steht er hier
// ausgeschrieben - dieselbe Lage wie bei `TransactionCandidate` weiter unten.
// Die Oberflaeche spricht die unpraefixierten Pfade an (/v1/dishes,
// /v1/meal-weeks, /v1/shopping-lists), die aus routes/grocery.py kommen.
export type MealType = components["schemas"]["MealResponse"]["meal_type"];
export type ShoppingListStatus =
  components["schemas"]["app__schemas__grocery__ShoppingListResponse"]["status"];

export type MealWeek = components["schemas"]["app__schemas__grocery__MealWeekResponse"];
export type MealWeekCreateInput = components["schemas"]["app__schemas__grocery__MealWeekCreate"];
export type MealWeekUpdateInput = components["schemas"]["app__schemas__grocery__MealWeekUpdate"];
export type MealWeekSummary = components["schemas"]["MealWeekSummaryResponse"];

export type Dish = components["schemas"]["app__schemas__grocery__DishResponse"];
export type DishCreateInput = components["schemas"]["app__schemas__grocery__DishCreate"];
export type DishUpdateInput = components["schemas"]["app__schemas__grocery__DishUpdate"];

export type DishIngredient = components["schemas"]["DishIngredientResponse"];
export type DishIngredientCreateInput = components["schemas"]["DishIngredientCreate"];
export type DishIngredientUpdateInput = components["schemas"]["DishIngredientUpdate"];
export type DishIngredientBulkInput = components["schemas"]["DishIngredientBulkCreate"];

// Eigene Preisliste des Haushalts. Der Preis haengt an der ZUTAT - daraus
// rechnet sich die Kostenschaetzung jedes Gerichts, das sie verwendet.
export type IngredientPrice = components["schemas"]["IngredientPriceResponse"];
export type IngredientPriceCreateInput = components["schemas"]["IngredientPriceCreate"];
export type IngredientPriceUpdateInput = components["schemas"]["IngredientPriceUpdate"];
export type ShoppingListFillResult = components["schemas"]["ShoppingListFillResult"];

// Zutaten mit Menge, aber ohne verwertbaren Preis - nach ZUTAT gruppiert
// statt nach Gericht: eine Zutat einmal bepreisen repariert jedes Gericht,
// das sie verwendet.
export type PriceGap = components["schemas"]["PriceGapResponse"];
export type PriceGapDish = components["schemas"]["PriceGapDish"];

// Bestaetigte Schreibweise: Bruecke zwischen einem Rezeptnamen und einer
// Preiszeile, die anders heisst.
export type IngredientAlias = components["schemas"]["IngredientAliasResponse"];
export type IngredientAliasCreateInput = components["schemas"]["IngredientAliasCreate"];

// "1 Zehe Knoblauch = 5 g": die Bruecke zwischen der Einheit im Rezept und
// der Packungseinheit der Preisliste. Eine Dichte ist derselbe Satz mit ml
// als Einheit ("1 ml Sesamoel = 0,93 g").
export type UnitConversion = components["schemas"]["UnitConversionResponse"];
export type UnitConversionCreateInput = components["schemas"]["UnitConversionCreate"];
export type SuggestedConversion = components["schemas"]["SuggestedConversionResponse"];

// Preisherkunft. Die Preisliste oben traegt EINE Zahl je Zutat — den
// Bewertungspreis, aus dem gerechnet wird. Die Vielfalt (welcher Laden, welche
// Marke, Bio, Angebot, wann gesehen) liegt eine Ebene tiefer in den Sichtungen
// und wird nur zu dieser einen Zahl verdichtet.
export type GroceryStore = components["schemas"]["StoreResponse"];
export type GroceryStoreCreateInput = components["schemas"]["StoreCreate"];
export type GroceryStoreUpdateInput = components["schemas"]["StoreUpdate"];
export type PriceObservation = components["schemas"]["ObservationResponse"];
export type PriceObservationInput = components["schemas"]["ObservationCreate"];
export type PriceObservationBulkResult = components["schemas"]["ObservationBulkResult"];
export type IngredientPriceDetail = components["schemas"]["IngredientPriceDetail"];
export type PriceMatrixCell = components["schemas"]["PriceMatrixCell"];
export type ValuationState = components["schemas"]["ValuationState"];
export type ValuationUpdateInput = components["schemas"]["ValuationUpdate"];
export type GroceryPriceSettings = components["schemas"]["PriceSettingsPayload"];

// Excel-Vorschau: ausdruecklich nichts Gespeichertes, wie bei den
// KI-Vorschlaegen. Geschrieben wird erst ueber den Bulk-Endpunkt, nachdem der
// Nutzer die Zeilen bestaetigt hat.
export type PriceSheetPreview = components["schemas"]["SheetPreviewResult"];
export type PriceSheetPreviewRow = components["schemas"]["SheetPreviewRow"];
export type PriceSheetProblem = components["schemas"]["SheetProblem"];

// Datenbank-Explorer. Zeilen kommen absichtlich untypisiert (Record) statt als
// Modell je Tabelle: es sind 79, sie folgen dem Schema, und der Explorer soll
// zeigen was DA IST. Die Typinformation reist in `columns` mit.
export type ExplorerTableList = components["schemas"]["TableListResponse"];
export type ExplorerTableSummary = components["schemas"]["TableSummary"];
export type ExplorerRowPage = components["schemas"]["RowPageResponse"];
export type ExplorerColumn = components["schemas"]["ColumnInfo"];
export type ExplorerDeletePreview = components["schemas"]["DeletePreviewResponse"];
export type ExplorerCascadeEffect = components["schemas"]["CascadeEffect"];
export type ExplorerDeleteResult = components["schemas"]["DeleteResponse"];
export type ExplorerPurgePreview = components["schemas"]["PurgePreviewResponse"];
export type ExplorerPurgeResult = components["schemas"]["PurgeResponse"];

// KI-Vorschlaege: ausdruecklich nichts Gespeichertes. Geschrieben wird erst
// ueber die Bulk-Endpunkte, nachdem der Nutzer ausgewaehlt hat.
export type IngredientSuggestion = components["schemas"]["IngredientSuggestion"];
export type IngredientSuggestionResult = components["schemas"]["IngredientSuggestionResult"];
export type MealSuggestion = components["schemas"]["MealSuggestion"];
export type WeekPlanSuggestionInput = components["schemas"]["WeekPlanSuggestionInput"];
export type WeekPlanSuggestionResult = components["schemas"]["WeekPlanSuggestionResult"];
export type MealBulkInput = components["schemas"]["MealBulkCreate"];

export type Meal = components["schemas"]["MealResponse"];
export type MealCreateInput = components["schemas"]["MealCreate"];
export type MealUpdateInput = components["schemas"]["MealUpdate"];

export type LinkedTransaction = components["schemas"]["LinkedTransaction"];

export type ShoppingList = components["schemas"]["app__schemas__grocery__ShoppingListResponse"];
export type ShoppingListCreateInput =
  components["schemas"]["app__schemas__grocery__ShoppingListCreate"];
export type ShoppingListUpdateInput =
  components["schemas"]["app__schemas__grocery__ShoppingListUpdate"];

export type ShoppingItem = components["schemas"]["ShoppingItemResponse"];
export type ShoppingItemCreateInput = components["schemas"]["ShoppingItemCreate"];
export type ShoppingItemUpdateInput = components["schemas"]["ShoppingItemUpdate"];

export type PurchaseLink = components["schemas"]["PurchaseLinkResponse"];
export type PurchaseLinkCreateInput = components["schemas"]["PurchaseLinkCreate"];

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P1: Projekte, Szenarien, Kostenplan)
// ---------------------------------------------------------------------------

export type MajorProject = components["schemas"]["MajorProject"];
export type MajorProjectCreateInput = components["schemas"]["MajorProjectCreate"];
export type MajorProjectUpdateInput = components["schemas"]["MajorProjectUpdate"];
export type ProjectType = MajorProject["project_type"];
export type ProjectStatus = MajorProject["status"];
export type ProjectListItem = components["schemas"]["ProjectListItem"];
export type ProjectDeleteImpact = components["schemas"]["ProjectDeleteImpact"];
export type ProjectTransitionRequest = components["schemas"]["ProjectTransitionRequest"];

export type ProjectScenario = components["schemas"]["ProjectScenario"];
export type ProjectScenarioCreateInput = components["schemas"]["ProjectScenarioCreate"];
export type ProjectScenarioUpdateInput = components["schemas"]["ProjectScenarioUpdate"];
export type ScenarioKind = ProjectScenario["scenario_kind"];
export type ScenarioStatus = ProjectScenario["status"];
export type ScenarioCloneRequest = components["schemas"]["ScenarioCloneRequest"];
export type ScenarioComparison = components["schemas"]["ScenarioComparison"];
export type ScenarioComparisonRow = components["schemas"]["ScenarioComparisonRow"];
export type ScenarioCalculation = components["schemas"]["ScenarioCalculation"];

export type CostSection = components["schemas"]["CostSection"];
export type CostSectionCreateInput = components["schemas"]["CostSectionCreate"];
export type CostSectionUpdateInput = components["schemas"]["CostSectionUpdate"];

export type CostItem = components["schemas"]["CostItem"];
export type CostItemCreateInput = components["schemas"]["CostItemCreate"];
export type CostItemUpdateInput = components["schemas"]["CostItemUpdate"];
export type CalculationMode = CostItem["calculation_mode"];
export type CostItemStatus = CostItem["status"];

export type CostScheduleEntry = components["schemas"]["CostScheduleEntry"];
export type CostScheduleReplaceInput = components["schemas"]["CostScheduleReplace"];

export type WishlistConvertResponse = components["schemas"]["WishlistConvertResponse"];

// ---------------------------------------------------------------------------
// Vorhaben: KI-Kostenschaetzung (Nachtrag zum Plan nach P0-P5)
// ---------------------------------------------------------------------------

export type CostAiSuggestionRequest = components["schemas"]["CostAiSuggestionRequest"];
export type CostAiItemSuggestion = components["schemas"]["CostAiItemSuggestion"];
export type CostAiSearchSnippet = components["schemas"]["CostAiSearchSnippet"];
export type CostAiSuggestionPreview = components["schemas"]["CostAiSuggestionPreview"];
export type CostAiSuggestionAcceptItem = components["schemas"]["CostAiSuggestionAcceptItem"];
export type CostAiSuggestionApplyInput = components["schemas"]["CostAiSuggestionApply"];
export type CostAiSuggestionResult = components["schemas"]["CostAiSuggestionResult"];

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P2: Finanzierung, Darlehen, Annahmen, Abschluss)
// ---------------------------------------------------------------------------

export type FinancingSource = components["schemas"]["FinancingSource"];
export type FinancingSourceCreateInput = components["schemas"]["FinancingSourceCreate"];
export type FinancingSourceUpdateInput = components["schemas"]["FinancingSourceUpdate"];
export type FinancingConfigurationCreateInput =
  components["schemas"]["FinancingConfigurationCreate"];
export type FinancingConfigurationUpdateInput =
  components["schemas"]["FinancingConfigurationUpdate"];
export type FinancingConfigurationResponse =
  components["schemas"]["FinancingConfigurationResponse"];
export type CostDrawPreview = components["schemas"]["CostDrawPreview"];
export type CostDrawPreviewInput = components["schemas"]["CostDrawPreviewInput"];
export type FinancingSourceType = FinancingSource["source_type"];
export type FinancingSourceStatus = FinancingSource["status"];
export type EquityAvailability = components["schemas"]["EquityAvailability"];
export type EquityAvailabilityAccount = components["schemas"]["EquityAvailabilityAccount"];

export type DebtPlan = components["schemas"]["DebtPlan"];
export type DebtPlanDetail = components["schemas"]["DebtPlanDetail"];
export type DebtPlanConfigurationInput = components["schemas"]["DebtPlanConfigurationInput"];
export type DebtPlanCreateInput = components["schemas"]["DebtPlanCreate"];
export type DebtPlanUpdateInput = components["schemas"]["DebtPlanUpdate"];
export type AmortizationMode = DebtPlan["amortization_mode"];
export type RatePeriod = components["schemas"]["RatePeriod"];
export type RatePeriodInput = components["schemas"]["RatePeriodInput"];
export type Draw = components["schemas"]["Draw"];
export type DrawInput = components["schemas"]["DrawInput"];
export type Fee = components["schemas"]["Fee"];
export type FeeInput = components["schemas"]["FeeInput"];
export type SpecialRepayment = components["schemas"]["SpecialRepayment"];
export type SpecialRepaymentInput = components["schemas"]["SpecialRepaymentInput"];
export type DebtSchedule = components["schemas"]["DebtSchedule"];
export type DebtScheduleRow = components["schemas"]["DebtScheduleRow"];

export type CashflowAssumptions = components["schemas"]["CashflowAssumptions"];
export type CashflowAssumptionsInput = components["schemas"]["CashflowAssumptionsInput"];
export type CashflowSuggestion = components["schemas"]["CashflowSuggestion"];

export type RecurringImpact = components["schemas"]["RecurringImpact"];
export type RecurringImpactCreateInput = components["schemas"]["RecurringImpactCreate"];
export type RecurringImpactUpdateInput = components["schemas"]["RecurringImpactUpdate"];
export type RecurringImpactType = RecurringImpact["impact_type"];

export type GoalAssumption = components["schemas"]["GoalAssumption"];
export type GoalAssumptionInput = components["schemas"]["GoalAssumptionInput"];
export type GoalAssumptionImpact = components["schemas"]["GoalAssumptionImpact"];

export type FundingGap = components["schemas"]["FundingGap"];
export type AffordabilityMetrics = components["schemas"]["AffordabilityMetrics"];
export type ScenarioFinancialSummary = components["schemas"]["ScenarioFinancialSummary"];
export type CashflowMonthRow = components["schemas"]["CashflowMonthRow"];
export type ScenarioTimeline = components["schemas"]["ScenarioTimeline"];

export type CompletionPreview = components["schemas"]["CompletionPreview"];
export type CompletionApplyResponse = components["schemas"]["CompletionApplyResponse"];

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P3: Dynamischer Foerderkatalog)
// ---------------------------------------------------------------------------

export type EligibilityProfile = components["schemas"]["EligibilityProfile"];
export type EligibilityProfileInput = components["schemas"]["EligibilityProfileInput"];
export type FundingProgramSummary = components["schemas"]["FundingProgramSummary"];
export type FundingProgramDetail = components["schemas"]["FundingProgramDetail"];
// `dict[str, Any]` wird von OpenAPI absichtlich offen beschrieben;
// openapi-typescript bildet es jedoch als `Record<string, never>` ab. Der
// Konditionseditor arbeitet mit den bekannten Bausteinfeldern und braucht
// deshalb hier den tatsaechlich passenden JSON-Objekttyp.
export interface FundingProgramTermsUpdate {
  source_version: string;
  review_due_on: string;
  source_url: string;
  benefit_json: Record<string, unknown>;
  loan_terms_json?: Record<string, unknown>;
}
export type CatalogStatus = components["schemas"]["CatalogStatus"];
export type ProviderCatalogStatus = components["schemas"]["ProviderCatalogStatus"];
export type FundingMatch = components["schemas"]["FundingMatch"];
export type FundingMatchStatus = FundingMatch["status"];
export type FundingCheckRunResponse = components["schemas"]["FundingCheckRunResponse"];
export type CombinationEvaluateRequest = components["schemas"]["CombinationEvaluateRequest"];
export type CombinationEvaluateResponse = components["schemas"]["CombinationEvaluateResponse"];
export type CombinationResultSchema = components["schemas"]["CombinationResultSchema"];
export type AdoptMatchRequest = components["schemas"]["AdoptMatchRequest"];
export type AdoptMatchResponse = components["schemas"]["AdoptMatchResponse"];

// Foerderkatalog-Import/-Export (docs/FOERDERKATALOG-IMPORT-PLAN.md, S3/S4)
export type FundingPackageMeta = components["schemas"]["FundingPackageMeta"];
export type FundingPackagePayload = components["schemas"]["FundingPackagePayload"];
export type PortableFundingProgram = components["schemas"]["PortableFundingProgram"];
export type FundingImportRequest = components["schemas"]["FundingImportRequest"];
export type FundingPreviewResponse = components["schemas"]["FundingPreviewResponse"];
export type FundingPreviewItem = components["schemas"]["FundingPreviewItem"];
export type FundingPreviewStatus = FundingPreviewItem["status"];
export type FundingImportResult = components["schemas"]["FundingImportResult"];
export type FundingExportRequest = components["schemas"]["FundingExportRequest"];

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P4: Istkosten-Zuordnung, Import/Export)
// ---------------------------------------------------------------------------

export type CostActual = components["schemas"]["CostActual"];
export type CostActualCreateInput = components["schemas"]["CostActualCreate"];
export type ActualSource = CostActual["source"];
// OpenAPI qualifiziert diesen Namen, seit auch der Erstattungsbereich ein
// eigenes TransactionCandidate-Schema besitzt. Hier ist weiterhin explizit
// der Kandidat für Vorhaben-Istkosten gemeint.
export type TransactionCandidate =
  components["schemas"]["app__schemas__projects__TransactionCandidate"];
export type BulkAssignActualsItem = components["schemas"]["BulkAssignActualsItem"];
export type BulkAssignActualsRequest = components["schemas"]["BulkAssignActualsRequest"];
export type BulkAssignActualsResult = components["schemas"]["BulkAssignActualsResult"];

export type CostImportMappingProfile = components["schemas"]["CostImportMappingProfile"];
export type CostImportMappingProfileInput = components["schemas"]["CostImportMappingProfileInput"];
export type CostImportPreviewResponse = components["schemas"]["CostImportPreviewResponse"];
export type CostImportPreviewRow = components["schemas"]["CostImportPreviewRow"];
// Kein generierter Typ: die Apply-Route nimmt die Zeilenzuordnung als
// JSON-codierten Multipart-Formfeld-String entgegen (Datei-Upload + Body
// lassen sich in OpenAPI nicht als ein Feld sauber typisieren), das Backend-
// Pydantic-Modell taucht deshalb nicht im OpenAPI-Schema auf. Von Hand
// gepflegtes Spiegelbild von app/schemas/project_import.py:
// CostImportRowResolution.
export interface CostImportRowResolution {
  row_number: number;
  action: "create" | "update" | "skip";
  target_item_id?: string | null;
}
export type CostImportApplyResult = components["schemas"]["CostImportApplyResult"];

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P5: Uebersicht und Berichte, Plan 11.7)
// ---------------------------------------------------------------------------

export type ProjectDashboardSummary = components["schemas"]["ProjectDashboardSummary"];
export type ProjectDashboardNextPayment = components["schemas"]["ProjectDashboardNextPayment"];
export type ProjectDashboardWarning = components["schemas"]["ProjectDashboardWarning"];
export type ProjectReportRow = components["schemas"]["ProjectReportRow"];

// --- Regel-Import/Export ---------------------------------------------------

export interface RulePackageMeta {
  id: string;
  name: string;
  description: string;
  icon: string;
  rule_count: number;
  /** Regeln dieses Pakets, die es im Haushalt schon gibt. */
  installed_count: number;
}

export interface PortableRule {
  name: string;
  match_payee_raw: string | null;
  match_payee_exact_word: boolean;
  match_purpose: string | null;
  match_purpose_exact_word: boolean;
  exclude_payee_raw: string | null;
  exclude_purpose: string | null;
  match_min_amount_minor: number | null;
  match_max_amount_minor: number | null;
  match_amount_mode: "inside" | "outside";
  action_category_key: string | null;
  action_set_transfer: boolean;
  action_note: string | null;
  priority: number;
  is_active: boolean;
}

export interface RulePackage {
  format_version: string;
  name: string;
  description: string;
  author: string;
  rules: PortableRule[];
}

/**
 * Ergebnis der Duplikatpruefung fuer genau eine einzuspielende Regel. Siehe
 * services/api/app/services/rule_import_export_service.py fuer die Herleitung:
 * `duplicate` = gleiche Bedingung und gleiche Aktion, `conflict` = gleiche
 * Bedingung mit anderer Zielkategorie, `overlap` = gemeinsame Suchbegriffe.
 */
export type RuleImportItemStatus = "new" | "duplicate" | "conflict" | "overlap";

export interface RuleImportPreviewItem {
  /** Position in `RulePackage.rules` - Schluessel fuer `skip_indices`. */
  index: number;
  rule_name: string;
  match_payee_raw: string | null;
  category_key: string | null;
  resolved_category_id: string | null;
  resolved_category_name: string | null;
  status: RuleImportItemStatus;
  is_duplicate: boolean;
  duplicate_rule_id: string | null;
  existing_rule_name: string | null;
  existing_category_name: string | null;
  shared_terms: string[];
  in_package_duplicate: boolean;
  /** Vorbelegung der Auswahl: nur unbekannte Regeln sind vorausgewaehlt. */
  recommended: boolean;
}

export interface RuleImportPreview {
  package_name: string;
  total_rules: number;
  mappable: number;
  unmapped: number;
  duplicates: number;
  conflicts: number;
  overlaps: number;
  recommended: number;
  items: RuleImportPreviewItem[];
}

export interface RuleImportInput {
  package: RulePackage;
  category_overrides: Record<string, string>;
  skip_duplicates: boolean;
  /** Positionen, die nicht importiert werden sollen. */
  skip_indices?: number[];
}

export interface RuleImportResult {
  created: number;
  skipped: number;
  skipped_duplicates: number;
  skipped_deselected: number;
  errors: string[];
}

export interface RuleExportInput {
  rule_ids: string[];
  package_name: string;
  description: string;
}

// ---------------------------------------------------------------------------
// Liquiditaetsvorschau (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md)
// ---------------------------------------------------------------------------

export type ForecastResponse = components["schemas"]["ForecastResponse"];
export type ForecastDay = components["schemas"]["ForecastDay"];
export type ForecastMonth = components["schemas"]["ForecastMonth"];
export type ForecastFlow = components["schemas"]["ForecastFlow"];
export type ForecastDayGroup = components["schemas"]["ForecastDayGroup"];
export type ForecastCause = components["schemas"]["ForecastCause"];
export type ForecastWarning = components["schemas"]["ForecastWarning"];
export type ForecastQuality = components["schemas"]["ForecastQuality"];
export type ForecastOpeningAccount = components["schemas"]["ForecastOpeningAccount"];
export type ForecastMetrics = components["schemas"]["ForecastMetrics"];
export type ForecastSettings = components["schemas"]["ForecastSettings"];
export type ForecastSettingsInput = components["schemas"]["ForecastSettingsInput"];
export type ForecastChange = components["schemas"]["ForecastChange"];
export type ForecastComparison = components["schemas"]["ForecastComparison"];
export type ForecastContribution = components["schemas"]["ForecastContribution"];
export type ForecastSimulationInput = components["schemas"]["ForecastSimulationRequest"];
export type ForecastSimulationResponse = components["schemas"]["ForecastSimulationResponse"];

export type ForecastGranularity = ForecastResponse["granularity"];
export type ForecastQualityLevel = ForecastQuality["level"];
export type ForecastWarningLevel = ForecastWarning["level"];
export type ForecastVariableBasis = ForecastSettings["variable_basis"];
export type ForecastChangeType = ForecastChange["change_type"];

// ---------------------------------------------------------------------------
// Personen und Einnahmenauswertung (Plan Stufe 6)
// ---------------------------------------------------------------------------

export type Person = components["schemas"]["Person"];
export type PersonCreateInput = components["schemas"]["PersonCreate"];
export type PersonUpdateInput = components["schemas"]["PersonUpdate"];
export type ContractIncomeSummary = components["schemas"]["ContractIncomeSummary"];
export type ContractIncomePersonBucket = components["schemas"]["ContractIncomePersonBucket"];
export type ContractIncomeKindBucket = components["schemas"]["ContractIncomeKindBucket"];
export type ContractIncomeTrendPoint = components["schemas"]["ContractIncomeTrendPoint"];
export type ContractIncomeKind = NonNullable<ContractIncomeKindBucket["income_kind"]>;

// ---------------------------------------------------------------------------
// Gespeicherte Haushaltsszenarien (Plan Stufe 7)
// ---------------------------------------------------------------------------

export type HouseholdScenario = components["schemas"]["HouseholdScenario"];
export type HouseholdScenarioDetail = components["schemas"]["HouseholdScenarioDetail"];
export type HouseholdScenarioChange = components["schemas"]["HouseholdScenarioChange"];
export type HouseholdScenarioChangeInput = components["schemas"]["HouseholdScenarioChangeInput"];
export type HouseholdScenarioCreateInput = components["schemas"]["HouseholdScenarioCreate"];
export type HouseholdScenarioUpdateInput = components["schemas"]["HouseholdScenarioUpdate"];
export type ScenarioComparisonResponse = components["schemas"]["ScenarioComparisonResponse"];
export type ScenarioComparisonEntry = components["schemas"]["ScenarioComparisonEntry"];
export type HouseholdScenarioStatus = HouseholdScenario["status"];
