// Typisierte Formen des Backend-Vertrags (services/api). Seit Stage 2f aus
// dem OpenAPI-Schema generiert (packages/contracts/src/generated/api-types.ts,
// siehe packages/contracts/README.md) statt von Hand gepflegt - das war
// wiederholt Ursache von Contract-Bugs zwischen App und Backend. Diese
// Datei bildet nur noch die im App-Code verwendeten Namen auf die
// generierten Schemas ab; die tatsaechliche Feldform kommt immer von dort.
// Ein paar reine Client-Hilfstypen ohne Backend-Gegenstueck (z. B.
// ListTransactionsParams) bleiben unten weiterhin von Hand gepflegt.

import type { components } from "@finanzplaner/contracts";

// Stage 2e: Mitglieder-Einladung per Link/Code. `role` hat serverseitig
// einen Default ("editor"), ist im generierten Create-Schema deshalb
// optional (`role?`) - NonNullable holt hier nur die Literal-Union heraus.
export type HouseholdRole = NonNullable<components["schemas"]["HouseholdInviteCreate"]["role"]>;

export type MeHousehold = components["schemas"]["Membership"];
export type MeResponse = components["schemas"]["Me"];

export type Household = components["schemas"]["Household"];
export type HouseholdMember = components["schemas"]["HouseholdMember"];
export type HouseholdInvite = components["schemas"]["HouseholdInvite"];
export type HouseholdInvitePreview = components["schemas"]["HouseholdInvitePreview"];

// Personen des Haushalts (Liquiditaetsplan Stufe 6). Die App liest sie nur -
// angelegt, umbenannt und geloescht werden Personen im Web. Sie existieren
// bewusst unabhaengig von einem Benutzerzugang: Partner und Kinder haben in
// aller Regel keinen eigenen Login, zaehlen aber als Einnahmequelle.
export type Person = components["schemas"]["Person"];

// Die 8 erlaubten Kontotypen laut Auftrag/Architekturplan Abschnitt 6.2.
export type AccountType = components["schemas"]["AccountCreate"]["account_type"];

export type Account = components["schemas"]["Account"];
export type CreateAccountInput = components["schemas"]["AccountCreate"];
export type PatchAccountInput = components["schemas"]["AccountUpdate"];

// Kategorien: seit der Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md)
// kommt der Vertrag NICHT mehr aus packages/contracts, sondern aus dem von
// Hand gepflegten ./categoryTypes.ts. Grund: die OpenAPI-Regenerierung laeuft
// erst nach dieser Aenderung, das generierte `Category` kennt aber weder
// `synonyms` noch `icon`/`version` - der Picker und die Verwaltung braeuchten
// dann ueberall Casts. Sobald api-types.ts neu erzeugt ist, wird nur
// ./categoryTypes.ts umgestellt; alle Importe hier bleiben gueltig.
export type {
  ArchiveApply,
  ArchivePreview,
  BulkCategoryInput,
  BulkCategoryItem,
  BulkCategoryResult,
  BulkItemStatus,
  BulkSkipReason,
  CatalogCategory,
  CatalogGroup,
  Category,
  CategoryBudgetBucket,
  CategoryCatalog,
  CategoryCreate,
  CategoryGroup,
  CategoryGroupCreate,
  CategoryGroupUpdate,
  CategoryOrigin,
  CategoryReferenceCounts,
  CategoryUpdate,
  GroupArchiveApply,
  GroupArchivePreview,
  MergeApply,
  MergeCollision,
  MergePreview,
  MergeRequest,
  MergeResult,
  MoveApply,
  MovePreview,
  ReorderRequest,
  RestoreRequest,
  StandardPackageApply,
  StandardPackageEntry,
  StandardPackageEntryStatus,
  StandardPackagePreview,
  StandardPackageResult,
  StandardPackageStatus,
  TransactionCategoryUpdate,
} from "./categoryTypes";
export { MAX_BULK_TRANSACTIONS } from "./categoryTypes";

// Backend typisiert default_type/budget_bucket response-seitig bewusst als
// plain string, nicht als Literal-Union - diese beiden Hilfstypen (inkl.
// `null` fuer "nicht gesetzt") bleiben deshalb hier, z. B. fuer
// Dropdown-Optionen.
export type CategoryDefaultType = "income" | "expense" | "transfer" | null;
export type BudgetBucket = "needs" | "wants" | "savings" | null;

// Transaction.status ist im Backend ebenfalls plain string (nicht
// Literal-constrained) - siehe Kommentar bei CategoryDefaultType oben.
export type TransactionStatus = "pending" | "booked" | "reconciled";

// Phase 9 (Offline-Kern): Buchungen tragen serverseitig seit Migration 0004
// ein Optimistic-Concurrency-Token (`version`, vom DB-Trigger hochgezaehlt)
// und ein Soft-Delete-Kennzeichen (`is_archived`). Die generierten
// Vertragstypen in packages/contracts sind dafuer noch nicht neu erzeugt
// worden (das Paket liegt ausserhalb dieser Aenderung, siehe
// Abschlussbericht) - deshalb hier lokal ergaenzt statt von Hand
// nachgepflegt. Sobald die Generierung nachgezogen ist, koennen die beiden
// Schnittmengen-Typen ersatzlos entfallen.
type VersionedTransactionFields = {
  /** Vom Server gepflegte Version; Start 1, +1 je fachlicher Aenderung. */
  version: number;
  /** true, wenn die Buchung anderswo geloescht (soft-deleted) wurde. */
  is_archived: boolean;
};

export type Transaction = components["schemas"]["Transaction"] & VersionedTransactionFields;
export type TransactionSplit = components["schemas"]["TransactionSplit"];
export type TransactionDetail = components["schemas"]["TransactionDetail"] &
  VersionedTransactionFields;
export type CreateTransactionInput = components["schemas"]["TransactionCreate"];

// GET /v1/transactions/{id}/provenance - "warum ist diese Buchung in dieser
// Kategorie" (docs/ANALYSE-EXPLORER-PLAN.md, Baustein A).
export type TransactionProvenance = components["schemas"]["TransactionProvenance"];
export type SplitProvenance = components["schemas"]["SplitProvenance"];
export type CategorySource = SplitProvenance["category_source"];

// Betrag/Kategorie sind bewusst NICHT Teil dieses Typs - dafuer ist
// PUT /v1/transactions/{id}/splits (PutTransactionSplitsInput) zustaendig
// (siehe Phase-2-Auftrag, Backend-Kontrakt).
// expected_version: siehe Transaction.version. Wird der Wert mitgeschickt,
// antwortet der Server mit 409 statt eine fremde Aenderung zu ueberschreiben.
// Fehlt er, schreibt der Server ungeprueft wie vor Phase 9.
export type PatchTransactionInput = components["schemas"]["TransactionUpdate"] & {
  expected_version?: number;
};

export type PutTransactionSplitInput = components["schemas"]["SplitInput"];
export type PutTransactionSplitsInput = components["schemas"]["TransactionSplitsUpdate"] & {
  expected_version?: number;
};

/**
 * Antwortkoerper der API bei 409 auf PATCH /v1/transactions/{id} bzw.
 * PUT /v1/transactions/{id}/splits (services/api TransactionConflict).
 *
 * detail unterscheidet die beiden Faelle:
 *   version_conflict     - die Buchung wurde anderswo geaendert.
 *   transaction_archived - die Buchung wurde anderswo geloescht; ein
 *                          Ueberschreiben wuerde sie unbemerkt wiederbeleben.
 *
 * `current` ist vollstaendig (inkl. Splits), damit die Konfliktansicht beide
 * Staende gegenueberstellen kann, ohne nachzuladen.
 */
export interface TransactionConflict {
  detail: "version_conflict" | "transaction_archived";
  expected_version: number;
  current_version: number;
  current: TransactionDetail;
}

// Rueckwirkende Sparziel-Zuordnung (Architekturplan 6.7, Option (c) neben
// der direkten Zuordnung am Split via SplitInput.goal_id).
export type UnassignedGoalSplitCandidate = components["schemas"]["UnassignedGoalSplitCandidate"];
export type BulkAssignGoalInput = components["schemas"]["BulkAssignGoalInput"];
export type BulkAssignGoalResult = components["schemas"]["BulkAssignGoalResult"];

export type TransferLink = components["schemas"]["TransferLink"];
export type LinkTransferInput = components["schemas"]["TransferLinkCreate"];

export interface ListTransactionsParams {
  accountId?: string;
  fromDate?: string;
  toDate?: string;
  limit?: number;
}

// GET /v1/transactions/page (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5):
// additiver, cursorbasierter Paging-Endpunkt fuer die neue Buchungsliste.
// sort/direction sind im OpenAPI-Dokument nur als Inline-Query-Parameter des
// Endpunkts typisiert (kein eigener Schema-Name) - deshalb wie
// TransactionStatus oben von Hand als Literal-Union gepflegt, deckungsgleich
// mit services/api/app/schemas/transaction.py (TransactionSort,
// TransactionDirection).
export type TransactionSort = "date_desc" | "date_asc" | "amount_desc" | "amount_asc" | "payee_asc";
export type TransactionDirection = "income" | "expense";
export type TransactionPage = components["schemas"]["TransactionPage"];

export interface ListTransactionsPageParams {
  accountId?: string;
  fromDate?: string;
  toDate?: string;
  search?: string;
  categoryId?: string;
  uncategorized?: boolean;
  direction?: TransactionDirection;
  sort?: TransactionSort;
  limit?: number;
  cursor?: string;
}

export type ImportFormat = components["schemas"]["MappingProfile"]["bank_format"];
export type ImportStatus = components["schemas"]["ImportRun"]["status"];
export type RowStatus = components["schemas"]["ImportRow"]["status"];

export type ImportRun = components["schemas"]["ImportRun"];
export type ImportRow = components["schemas"]["ImportRow"];

// Zuruecknehmen eines bereits uebernommenen Laufs. Die Beschriftung eines
// Blockers kommt fertig vom Server - die Gruende haengen an fachlichen Regeln,
// die dort stehen und nicht in zwei Oberflaechen doppelt gepflegt werden.
export type ImportRevertPreview = components["schemas"]["ImportRevertPreview"];
export type ImportRevertResult = components["schemas"]["ImportRevertResult"];

// Phase 4: Review-Inbox
export type ReviewItemUnclassified = components["schemas"]["ReviewItemUnclassified"];
export type ReviewItemDuplicate = components["schemas"]["ReviewItemDuplicate"];
export type BookedDuplicateScanResult = components["schemas"]["BookedDuplicateScanResult"];
export type ReviewItemBookedDuplicate = components["schemas"]["ReviewItemBookedDuplicate"];
export type ResolveBookedDuplicateInput = components["schemas"]["ResolveBookedDuplicateInput"];
export type ReviewItemTransfer = components["schemas"]["ReviewItemTransfer"];
export type ReviewItemContractEndedCheck = components["schemas"]["ReviewItemContractEndedCheck"];
export type ReviewInboxResponse = components["schemas"]["ReviewInboxResponse"];

// VERALTET: der Server fuellt `recurring_suggestions` nicht mehr (Erkennung v2,
// siehe ReviewItemContractSuggestion unten). Der Typ bleibt nur, solange ein
// bereits ausgeliefertes App-Build den alten Zweig noch rendern kann.
export type ReviewItemRecurring = components["schemas"]["ReviewItemRecurring"];

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
// Vorschlaege stehen in derselben Liste, muessen aber als solche erkennbar
// bleiben (zusaetzlich `is_uncertain` am Item).
export type ContractSuggestionConfidence = ReviewItemContractSuggestion["confidence"];

// Altbestand-Dubletten: zwei Eintraege desselben Vertrags, zeitlich getrennt.
// `target_id` ist immer der juengere Vertrag, `source_ids` sind die aelteren.
export type ReviewItemContractDuplicate = components["schemas"]["ReviewItemContractDuplicate"];
export type ReviewContractDuplicatePartner =
  components["schemas"]["ReviewContractDuplicatePartner"];

// Vertrag mit erheblich geaendertem Betrag ("stimmt dieser neue Betrag?").
// Der Abgleich hat die Aenderung bereits still uebernommen; die Rueckfrage
// unterscheidet eine echte Preisaenderung von einer fremden Zahlung desselben
// Empfaengers. Richtungsneutral - eine SENKUNG steht hier wie eine Erhoehung.
export type ReviewItemContractAmountChangeCheck =
  components["schemas"]["ReviewItemContractAmountChangeCheck"];

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
export type AcceptContractSuggestion = components["schemas"]["AcceptContractSuggestion"];
export type AcceptContractDuplicate = components["schemas"]["AcceptContractDuplicate"];

export type ReviewCorrectInput =
  | components["schemas"]["CorrectUnclassified"]
  | components["schemas"]["CorrectDuplicate"]
  | components["schemas"]["CorrectTransfer"];

// Phase 4: Regelengine
export type ClassificationRule = components["schemas"]["ClassificationRule"];
export type ClassificationRuleCreate = components["schemas"]["ClassificationRuleCreate"];

// Phase 4: Wiederkehrende Regelmaessige Zahlungen & Abonnements (Fixkosten)
// Vertraege loesen RecurringRule und Subscription ab (Migration 0014). Die
// Alttypen bleiben nur fuer die Kompatibilitaetsschicht des Backends.
export type RecurringRule = components["schemas"]["RecurringRule"];
export type RecurringRuleCreate = components["schemas"]["RecurringRuleCreate"];

export type Contract = components["schemas"]["Contract"];
export type ContractCreate = components["schemas"]["ContractCreate"];
export type ContractUpdate = components["schemas"]["ContractUpdate"];
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
// Einnahmeart (Liquiditaetsplan Stufe 6). Bewusst getrennt von
// `contract_type`: der Vertragstyp sagt, WAS der Vertrag ist, die Einnahmeart,
// WOHER das Geld kommt - ein Vertrag vom Typ "income" kann Gehalt, Rente oder
// Mieteinnahme sein.
export type ContractIncomeKind = NonNullable<Contract["income_kind"]>;
export type ContractSummary = components["schemas"]["ContractSummary"];
export type ContractTypeSummary = components["schemas"]["ContractTypeSummary"];
export type ContractUpcoming = components["schemas"]["ContractUpcoming"];
export type ContractEnding = components["schemas"]["ContractEnding"];
export type ContractAmountEntry = components["schemas"]["ContractAmountEntry"];
export type ContractStatusChange = components["schemas"]["ContractStatusChange"];
export type ContractSettings = components["schemas"]["ContractSettings"];
export type ContractSettingsUpdate = components["schemas"]["ContractSettingsUpdate"];

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

// GET /v1/contracts/provider-suggestions: globaler Anbieterkatalog fuer das
// Anlegen-Formular. `category_id` ist die im Haushalt aufgeloeste Kategorie zu
// `default_category_key` und bleibt null, wenn der Haushalt die
// Standardkategorie umbenannt, archiviert oder nie installiert hat.
export type ContractProviderSuggestion = components["schemas"]["ContractProviderSuggestion"];

export type Subscription = components["schemas"]["Subscription"];
export type SubscriptionCreate = components["schemas"]["SubscriptionCreate"];

// Phase 5: Budgetkern & 50/30/20
export type BudgetAllocationInput = components["schemas"]["BudgetAllocationInput"];
export type BudgetUpdateInput = components["schemas"]["BudgetUpdateInput"];
export type CategoryBudgetStatus = components["schemas"]["CategoryBudgetStatus"];
export type CategoryGroupBudgetStatus = components["schemas"]["CategoryGroupBudgetStatus"];
export type BudgetSummary = components["schemas"]["BudgetSummary"];
// Bezugsgroesse der Fixkostenquote: "actual" sind die Ist-Einnahmen des Monats,
// "contract" der aus Einnahmevertraegen normierte Ersatzwert, solange noch
// nichts gebucht ist. Die Oberflaeche muss den Unterschied benennen, sonst
// steht eine Quote da, die niemand nachrechnen kann.
export type FixedCostsIncomeBasis = BudgetSummary["fixed_costs_income_basis"];

// Native Rücklagensteuerung. Alle Werte kommen aus derselben serverseitigen
// Ledger-/Planungslogik wie Web; Mobile berechnet weder Soll noch Saldo nach.
export type ReserveOverviewResponse = components["schemas"]["ReserveOverviewResponse"];
export type ReservePotResponse = components["schemas"]["ReservePotResponse"];
export type ReserveMovementResponse = components["schemas"]["ReserveMovementResponse"];
export type ReserveBatchResponse = components["schemas"]["ReserveBatchResponse"];
export type ReserveDepositRequest = components["schemas"]["ReserveDepositRequest"];
export type ReserveWithdrawalRequest = components["schemas"]["ReserveWithdrawalRequest"];
export type ReserveReallocationRequest = components["schemas"]["ReserveReallocationRequest"];
export type ReserveSkipRequest = components["schemas"]["ReserveSkipRequest"];
export type ReserveDistributionPreviewRequest =
  components["schemas"]["ReserveDistributionPreviewRequest"];
export type ReserveDistributionPreviewResponse =
  components["schemas"]["ReserveDistributionPreviewResponse"];

// Phase 5: Reports / Overview
export type CashflowMonthData = components["schemas"]["CashflowMonthData"];
export type CategoryReportData = components["schemas"]["CategoryReportData"];
export type OverviewReportResponse = components["schemas"]["OverviewReportResponse"];

// GET /v1/reports/fixed-costs-trend: vereinbarte Fixkosten je Monat aus
// contract_amount_history - also der zum Monatsletzten GUELTIGE Preis, nicht
// der tatsaechlich gebuchte Betrag. Der Statusfilter ist hier weiter als in
// BudgetSummary.fixed_costs_monthly_minor (beendete Vertraege bleiben in den
// Monaten stehen, in denen sie liefen); beide Zahlen sind deshalb bewusst
// nicht identisch.
export type FixedCostsTypeData = components["schemas"]["FixedCostsTypeData"];
export type FixedCostsMonthData = components["schemas"]["FixedCostsMonthData"];
export type FixedCostsTrendResponse = components["schemas"]["FixedCostsTrendResponse"];

// Berichte (docs/ANALYSE-EXPLORER-PLAN.md, Baustein E). Die Monatsreihe des
// Cashflows kommt fertig aggregiert vom Server - dieselbe Quelle, aus der die
// Weboberflaeche ihr Kontrollbuch baut.
export type CashflowTrendResponse = components["schemas"]["CashflowTrendResponse"];

// Auswertungen (POST /v1/analytics). Die Register "Kategorien", "Empfaenger"
// und "Jahresvergleich" bekommen bewusst KEINE eigenen Endpunkte: sie stellen
// dieselben Abfragen wie das Web und erben damit dessen Kuerungsgrain,
// Transferausschluss und Budgetkonten-Filter. Ein zweiter Serverweg nur fuer
// das Telefon wuerde bei gleicher Frage andere Summen liefern.
export type AnalyticsQuery = components["schemas"]["AnalyticsQuery"];
export type AnalyticsResult = components["schemas"]["AnalyticsResult"];
export type AnalyticsRow = AnalyticsResult["rows"][number];
export type AnalyticsFilterNode = components["schemas"]["FilterNode"];

// Vorhaben-Kennzahlen je Projekt (GET /v1/projects/reports-overview).
// Forecast/Ist/Abweichung kommen unveraendert vom Server, damit diese Zahlen
// nie von der Vorhaben-Detailseite abweichen.
export type ProjectReportRow = components["schemas"]["ProjectReportRow"];

// Liquiditaetsvorschau (GET /v1/forecast, /v1/forecast/upcoming;
// docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 5). Die Vorschau speichert
// nichts, sie ist eine reine Ableitung aus Konten, Vertraegen, Budget und
// Ruecklagen (Leitsatz 1) - es gibt deshalb weder Create- noch Update-Formen.
//
// Bewusst NICHT gespiegelt: ForecastSettings(-Input), ForecastChange,
// ForecastComparison, ForecastContribution und die Simulationsformen. Mobile
// ist lesend und kennt keine Szenariobearbeitung (Plan Abschnitt 9); ein hier
// mitgefuehrter Typ ohne Aufrufer waere nur eine Einladung, das zu aendern.
export type ForecastResponse = components["schemas"]["ForecastResponse"];
export type ForecastDay = components["schemas"]["ForecastDay"];
export type ForecastMonth = components["schemas"]["ForecastMonth"];
export type ForecastFlow = components["schemas"]["ForecastFlow"];
export type ForecastDayGroup = components["schemas"]["ForecastDayGroup"];
export type ForecastCause = components["schemas"]["ForecastCause"];
export type ForecastMetrics = components["schemas"]["ForecastMetrics"];
export type ForecastQuality = components["schemas"]["ForecastQuality"];
export type ForecastWarning = components["schemas"]["ForecastWarning"];

export type ForecastGranularity = ForecastResponse["granularity"];
export type ForecastQualityLevel = ForecastQuality["level"];
export type ForecastVariableBasis = ForecastQuality["variable_basis"];

// Finanz-Empfehlungen (GET /v1/insights). Alle Betraege entstehen serverseitig
// aus SQL; `ai_advice` ist die optionale Formulierung eines lokalen Modells und
// enthaelt vertraglich keine eigenen Zahlen.
export type Insight = components["schemas"]["Insight"];
// Seit den Vertragsauswertungen zusaetzlich "contract_unused" und
// "contract_duplicate"; "price_increase" kommt jetzt mit Vertragsbezug aus
// contract_amount_history statt aus einer Median-Schaetzung je Empfaenger.
export type InsightKind = Insight["kind"];
export type InsightSeverity = Insight["severity"];
export type InsightEvidence = components["schemas"]["InsightEvidence"];
export type InsightReport = components["schemas"]["InsightReport"];
// ACHTUNG: das Sprungziel eines Vertrags steht in `drilldown.contract_id`,
// NICHT am Insight selbst. `field`/`value` bleiben der Buchungsfilter fuer den
// Explorer - der kennt keine Vertragsdimension.
export type InsightDrilldown = components["schemas"]["InsightDrilldown"];

// Phase 6: LM Studio / Lokale KI Integration
export type AIHealthResponse = components["schemas"]["AIHealthResponse"];
export type AIClassifyInput = components["schemas"]["AIClassifyInput"];
export type AIClassifyResult = components["schemas"]["AIClassifyResult"];
export type AIDecisionLog = components["schemas"]["AIDecisionLog"];

// Batch-Klassifikationslauf mit Fortschrittsanzeige (Polling, analog zu ImportRun).
export type ClassificationRun = components["schemas"]["ClassificationRun"];
export type ClassificationRunStatus = ClassificationRun["status"];

// Phase 7: Ziele, Fixkosten und Vermoegen
export type GoalStatus = components["schemas"]["SavingsGoalResponse"]["status"];
export type WishlistStatus = components["schemas"]["WishlistItemResponse"]["status"];
export type AssetType = components["schemas"]["AssetDebtResponse"]["type"];
export type AssetClass = components["schemas"]["AssetDebtResponse"]["asset_class"];
export type TrafficLightStatus = components["schemas"]["WishlistItemResponse"]["buy_traffic_light"];

export type SavingsGoal = components["schemas"]["SavingsGoalResponse"];
export type SavingsGoalCreate = components["schemas"]["SavingsGoalCreate"];
export type SavingsGoalUpdate = components["schemas"]["SavingsGoalUpdate"];

export type WishlistItem = components["schemas"]["WishlistItemResponse"];
export type WishlistItemCreate = components["schemas"]["WishlistItemCreate"];
export type WishlistItemUpdate = components["schemas"]["WishlistItemUpdate"];
export type WishlistSavingsPlanCreate = components["schemas"]["WishlistSavingsPlanCreate"];
export type WishlistSavingsPlanResponse = components["schemas"]["WishlistSavingsPlanResponse"];

export type AssetDebt = components["schemas"]["AssetDebtResponse"];
export type AssetDebtCreate = components["schemas"]["AssetDebtCreate"];

export type NetWorthReportResponse = components["schemas"]["NetWorthReportResponse"];

// Phase 8: Lebensmittelplanung (Essensplan, Einkaufsliste, Wochenbudget)
// app/schemas/grocery.py und app/schemas/groceries.py fuehren beide ein
// `MealWeekResponse`/`MealWeekCreate`/`ShoppingListResponse`/`ShoppingListCreate`.
// FastAPI qualifiziert bei Namensgleichheit mit dem Modulpfad, deshalb stehen
// sie hier ausgeschrieben - dieselbe Lage wie bei `TransactionCandidate`
// weiter unten (siehe auch apps/web/src/lib/types.ts). Die App spricht die
// unpraefixierten Pfade an (/v1/meal-weeks, /v1/shopping-lists), die aus
// routes/grocery.py kommen.
export type MealType = components["schemas"]["MealResponse"]["meal_type"];
export type ShoppingListStatus =
  components["schemas"]["app__schemas__grocery__ShoppingListResponse"]["status"];

export type MealWeek = components["schemas"]["app__schemas__grocery__MealWeekResponse"];
export type MealWeekCreate = components["schemas"]["app__schemas__grocery__MealWeekCreate"];
export type MealWeekSummary = components["schemas"]["MealWeekSummaryResponse"];

export type Meal = components["schemas"]["MealResponse"];
export type MealCreate = components["schemas"]["MealCreate"];
export type MealUpdate = components["schemas"]["MealUpdate"];

export type LinkedTransaction = components["schemas"]["LinkedTransaction"];

export type ShoppingList = components["schemas"]["app__schemas__grocery__ShoppingListResponse"];
export type ShoppingListCreate = components["schemas"]["app__schemas__grocery__ShoppingListCreate"];

export type ShoppingItem = components["schemas"]["ShoppingItemResponse"];
export type ShoppingItemCreate = components["schemas"]["ShoppingItemCreate"];
export type ShoppingItemUpdate = components["schemas"]["ShoppingItemUpdate"];

export type PurchaseLink = components["schemas"]["PurchaseLinkResponse"];
export type PurchaseLinkCreate = components["schemas"]["PurchaseLinkCreate"];

// Phase 9: Push-Benachrichtigungen (Geraeteregistrierung)
export type PushTokenPlatform = components["schemas"]["PushTokenRegister"]["platform"];
export type PushTokenRegisterInput = components["schemas"]["PushTokenRegister"];
export type PushTokenResponse = components["schemas"]["PushTokenResponse"];

// Vorhaben & Grossinvestitionen (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md,
// Abschnitt 9, P5). Mobile ist bewusst read-lastig - nur die fuer Liste,
// Detail (Uebersicht/Kosten/Finanzierung/Foerderung) und die zwei erlaubten
// Schnellaktionen (Kostenposition als bezahlt markieren, Banktransaktion
// zuordnen) benoetigten Teilmengen der Web-Typen (apps/web/src/lib/types.ts).
// Darlehensplaene, Foerderprofil-Formular, Annahmen und Import/Export bleiben
// Web-only und sind hier deshalb bewusst NICHT gespiegelt.
export type MajorProject = components["schemas"]["MajorProject"];
export type MajorProjectCreateInput = components["schemas"]["MajorProjectCreate"];
export type ProjectType = MajorProject["project_type"];
export type ProjectStatus = MajorProject["status"];
export type ProjectListItem = components["schemas"]["ProjectListItem"];

export type ProjectScenario = components["schemas"]["ProjectScenario"];
export type ScenarioCalculation = components["schemas"]["ScenarioCalculation"];

export type CostItem = components["schemas"]["CostItem"];
export type CostItemUpdateInput = components["schemas"]["CostItemUpdate"];
export type CostItemStatus = CostItem["status"];

export type FinancingSource = components["schemas"]["FinancingSource"];
export type FinancingSourceType = FinancingSource["source_type"];
export type FinancingSourceStatus = FinancingSource["status"];

export type FundingMatch = components["schemas"]["FundingMatch"];
export type FundingMatchStatus = FundingMatch["status"];
export type FundingCheckRunResponse = components["schemas"]["FundingCheckRunResponse"];
export type CriterionResult = components["schemas"]["CriterionResultSchema"];
// "Foerderquelle oeffnen und Datenstand sehen" (Plan Abschnitt 9): Versionen,
// Quellen und Frische eines Programms, unabhaengig vom zuletzt gelaufenen
// Fördercheck.
export type FundingProgramDetail = components["schemas"]["FundingProgramDetail"];
export type FundingProgramVersionDetail = components["schemas"]["FundingProgramVersionDetail"];

export type CostActual = components["schemas"]["CostActual"];
export type CostActualCreateInput = components["schemas"]["CostActualCreate"];
// OpenAPI qualifiziert diesen Namen, seit auch der Erstattungsbereich ein
// eigenes TransactionCandidate-Schema besitzt. Hier ist weiterhin explizit
// der Kandidat fuer Vorhaben-Istkosten gemeint (siehe apps/web/src/lib/types.ts).
export type TransactionCandidate =
  components["schemas"]["app__schemas__projects__TransactionCandidate"];

// Versicherungen (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7, Phase P2b).
//
// Mobile ist die LESENDE Fassung des Moduls - Liste, Detail, Spartenkatalog,
// Kennzahlen, plus das Anlegen einer einfachen Police. Ausdruecklich NICHT
// gespiegelt sind deshalb: InsurancePolicyUpdate, PolicyStatusChangeRequest,
// ConfirmMainDue/ConfirmNoticeTerms/ConfirmLine, LinkContract, BackfillPreview/
// BackfillResult, InsuranceSettings, InsuranceTask, InsuranceCoverageKey,
// PolicyPurpose und ContractCandidate. Dieselbe Linie wie bei der
// Liquiditaetsvorschau weiter oben: ein mitgefuehrter Typ ohne Aufrufer ist
// keine Vorbereitung, sondern eine Einladung, die Grenze zu verschieben.
//
// Die Namen folgen apps/web/src/lib/insuranceApi.ts, damit sich beide Fassungen
// nebeneinander lesen lassen - mit einer Ausnahme: `Frequency` heisst hier
// `PremiumFrequency`. Ein blankes `Frequency` neben dem bestehenden
// `ContractFrequency` in derselben Datei waere eine Falle.
export type RiskField = components["schemas"]["InsuranceLine"]["risk_field"];
export type NoticeAnchor = components["schemas"]["PolicyDeadline"]["anchor"];
export type AnchorBasisKind = components["schemas"]["PolicyDeadline"]["anchor_basis_kind"];
export type DeadlineKind = components["schemas"]["PolicyDeadline"]["deadline_kind"];
export type PolicyStatus = components["schemas"]["InsurancePolicy"]["status"];
export type ServiceChannel = components["schemas"]["InsurancePolicy"]["service_channel"];

// Die drei Angaben, die im Versicherungsmodul die gesamte Fachlogik der
// Oberflaeche tragen, und die deshalb nirgends optional werden duerfen:
// `certainty` an der Frist sowie `main_due_source` und `notice_terms_source` an
// der Police. Wer sie beim Rendern uebergeht, zeigt eine Annahme wie eine
// Tatsache - genau das verhindert der Plan in Abschnitt 2.4.
export type Certainty = components["schemas"]["PolicyDeadline"]["certainty"];
export type DateSource = components["schemas"]["InsurancePolicy"]["main_due_source"];
export type NoticeTermsSource = components["schemas"]["InsurancePolicy"]["notice_terms_source"];

// Die drei Geldfaelle einer Police als Wert, nicht als Feldpraesenz.
export type PremiumKind = components["schemas"]["PolicyPremium"]["kind"];
export type PremiumPayer = components["schemas"]["ExternalPremiumInput"]["premium_payer"];
export type PremiumFrequency = components["schemas"]["ExternalPremiumInput"]["external_frequency"];

/**
 * `safe_service_holiday` steht seit dem Fristenkern (P2) im Pydantic-Modell
 * (services/api/app/schemas/insurance.py, PolicyDeadline), das generierte
 * OpenAPI-Schema ist dafuer aber noch nicht neu erzeugt worden. Bis dahin hier
 * lokal ergaenzt statt in den Komponenten weggecastet - gleiche Bauart wie
 * VersionedTransactionFields weiter oben, und ebenso ersatzlos entfernbar,
 * sobald api-types.ts nachgezogen ist.
 *
 * Der Feiertag kommt bewusst vom Server: ein zweiter Feiertagskalender im
 * Client ergaebe frueher oder spaeter einen zweiten Zustelltermin.
 */
type DeadlineHolidayField = {
  safe_service_holiday?: string | null;
};

export type PolicyDeadline = components["schemas"]["PolicyDeadline"] & DeadlineHolidayField;
export type PolicyPremium = components["schemas"]["PolicyPremium"];
export type ExternalPremiumInput = components["schemas"]["ExternalPremiumInput"];

export type InsuranceLine = components["schemas"]["InsuranceLine"];
/** Sparte samt `attr_schema` und `completeness_fields` - kommt am Policendetail mit. */
export type InsuranceLineDetail = components["schemas"]["InsuranceLineDetail"];

export type InsurancePolicy = Omit<components["schemas"]["InsurancePolicy"], "deadline"> & {
  deadline: PolicyDeadline;
};
export type InsurancePolicyDetail = Omit<
  components["schemas"]["InsurancePolicyDetail"],
  "policy"
> & {
  policy: InsurancePolicy;
};

export type InsurancePolicyCreate = components["schemas"]["InsurancePolicyCreate"];
export type InsuranceSummary = components["schemas"]["InsuranceSummary"];
