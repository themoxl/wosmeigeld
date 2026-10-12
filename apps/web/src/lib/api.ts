import type { SystemHealth } from "@finanzplaner/contracts";
import type {
  Account,
  AccountCreateInput,
  AccountUpdateInput,
  AIClassifyInput,
  AIClassifyResult,
  AIDecisionLog,
  AIProposalApplyInput,
  AIProposalBatchResult,
  AIHealthResponse,
  AIKeyInput,
  AIKeyStatus,
  AIModelCatalog,
  AIModelLoadResult,
  AIRuntimeStatus,
  AISettings,
  AISettingsUpdate,
  AdoptMatchRequest,
  AdoptMatchResponse,
  AnalyticsQuery,
  AnalyticsResult,
  AnnualMatrixResponse,
  AssetDebt,
  AssetDebtCreateInput,
  AssetDebtUpdateInput,
  AssetValuation,
  BudgetAllocationInput,
  BudgetSummary,
  BulkAssignActualsRequest,
  BulkAssignActualsResult,
  BulkAssignGoalInput,
  BulkAssignGoalResult,
  BulkCategorizeInput,
  BulkCategorizeResult,
  CatalogStatus,
  CashflowAssumptions,
  CashflowAssumptionsInput,
  CashflowSuggestion,
  CashflowTrendResponse,
  CategoryGroup,
  ClassificationRule,
  ClassificationRuleCreateInput,
  ClassificationRuleUpdateInput,
  ContractIncomeSummary,
  ForecastDayGroup,
  ForecastGranularity,
  ForecastResponse,
  ForecastSettings,
  ForecastSettingsInput,
  ForecastSimulationInput,
  ForecastSimulationResponse,
  HouseholdScenario,
  HouseholdScenarioChangeInput,
  HouseholdScenarioCreateInput,
  HouseholdScenarioDetail,
  HouseholdScenarioUpdateInput,
  Person,
  PersonCreateInput,
  PersonUpdateInput,
  RulePackageMeta,
  RulePackage,
  RuleImportInput,
  RuleImportPreview,
  RuleImportResult,
  RuleExportInput,
  ClassificationRun,
  CloseMonthResponse,
  CombinationEvaluateRequest,
  CombinationEvaluateResponse,
  CompletionApplyResponse,
  CompletionPreview,
  CashflowGranularity,
  CostActual,
  CostActualCreateInput,
  CostImportApplyResult,
  CostImportMappingProfile,
  CostImportMappingProfileInput,
  CostImportPreviewResponse,
  CostImportRowResolution,
  Contract,
  ContractAICrosscheckResponse,
  ContractAITransactionCheckResponse,
  ContractAmountEntry,
  ContractAmountEvidenceResponse,
  ContractAmountRecalculationResult,
  ContractAssignmentPreview,
  ContractCashflowResponse,
  ContractCostTarget,
  ContractCostTargetInput,
  ContractCreateInput,
  ContractDirection,
  ContractDuplicateCandidate,
  ContractDuplicateSuggestionsResponse,
  ContractEvent,
  ContractFreeTransactionsResponse,
  ContractFrequency,
  ContractMergePreview,
  ContractMergeResult,
  ContractMergeUndoPreview,
  ContractMergeUndoResult,
  ContractOccurrence,
  ContractProviderSuggestion,
  ContractReassignPreview,
  ContractReassignResult,
  ContractRescanResult,
  ContractScenario,
  ContractScenarioCreateInput,
  ContractScenarioUpdateInput,
  ContractSettings,
  ContractStatus,
  ContractStatusChangeInput,
  ContractSummary,
  ContractType,
  ContractUpcoming,
  ContractUpdateInput,
  CostAiSuggestionApplyInput,
  CostAiSuggestionPreview,
  CostAiSuggestionRequest,
  CostAiSuggestionResult,
  CostDrawPreview,
  CostDrawPreviewInput,
  CostItem,
  CostItemCreateInput,
  CostItemUpdateInput,
  CostScheduleEntry,
  CostScheduleReplaceInput,
  CostSection,
  CostSectionCreateInput,
  CostSectionUpdateInput,
  DebtPlan,
  DebtPlanCreateInput,
  DebtPlanUpdateInput,
  DebtSchedule,
  Draw,
  DrawInput,
  EligibilityProfile,
  EligibilityProfileInput,
  EquityAvailability,
  FacetRequest,
  FacetResult,
  Fee,
  FeeInput,
  FieldCatalog,
  FinancingConfigurationCreateInput,
  FinancingConfigurationResponse,
  FinancingConfigurationUpdateInput,
  FinancingSource,
  FinancingSourceCreateInput,
  FinancingSourceUpdateInput,
  FixedCostsTrendResponse,
  FundingCheckRunResponse,
  FundingExportRequest,
  FundingImportRequest,
  FundingImportResult,
  FundingPackageMeta,
  FundingPackagePayload,
  FundingPreviewResponse,
  FundingProgramDetail,
  FundingProgramSummary,
  FundingProgramTermsUpdate,
  GoalAssumption,
  GoalAssumptionInput,
  Household,
  HouseholdInvite,
  HouseholdInvitePreview,
  HouseholdMember,
  HouseholdRole,
  ImportFormat,
  ImportMappingProfile,
  ImportMappingSuggestion,
  ImportRevertPreview,
  ImportRevertResult,
  ImportRow,
  ImportRun,
  ImportRunUpdateInput,
  InsightReport,
  ListTransactionsParams,
  MajorProject,
  MajorProjectCreateInput,
  MajorProjectUpdateInput,
  Dish,
  DishCreateInput,
  DishIngredient,
  DishIngredientBulkInput,
  DishIngredientCreateInput,
  DishIngredientUpdateInput,
  DishUpdateInput,
  ExplorerDeletePreview,
  ExplorerDeleteResult,
  ExplorerPurgePreview,
  ExplorerPurgeResult,
  ExplorerRowPage,
  ExplorerTableList,
  GroceryPriceSettings,
  GroceryStore,
  GroceryStoreCreateInput,
  GroceryStoreUpdateInput,
  IngredientAlias,
  IngredientAliasCreateInput,
  UnitConversion,
  UnitConversionCreateInput,
  IngredientPrice,
  IngredientPriceCreateInput,
  IngredientPriceDetail,
  IngredientPriceUpdateInput,
  IngredientSuggestionResult,
  PriceGap,
  PriceObservationBulkResult,
  PriceObservationInput,
  PriceSheetPreview,
  ScenarioComparisonResponse,
  ValuationState,
  ValuationUpdateInput,
  Meal,
  MealBulkInput,
  MealCreateInput,
  MealUpdateInput,
  MealWeek,
  MealWeekCreateInput,
  MealWeekSummary,
  MealWeekUpdateInput,
  Me,
  NetWorthReportResponse,
  OverviewReportResponse,
  ProfileUpdate,
  ProjectDashboardSummary,
  ProjectDeleteImpact,
  ProjectListItem,
  ProjectReportRow,
  ProjectScenario,
  ProjectScenarioCreateInput,
  ProjectScenarioUpdateInput,
  ProjectTransitionRequest,
  ProviderId,
  PurchaseLink,
  PurchaseLinkCreateInput,
  RatePeriod,
  RatePeriodInput,
  ReconcileInput,
  Reconciliation,
  RecurringImpact,
  RecurringImpactCreateInput,
  RecurringImpactUpdateInput,
  ReviewAcceptInput,
  ReviewContractSuggestionDetail,
  ReviewCorrectInput,
  ReviewHistoryMatch,
  ReviewHistoryMatchesResponse,
  ReviewRulePreviewInput,
  ReviewInboxResponse,
  ReviewRecurringDetail,
  ReserveBatch,
  ReserveDepositInput,
  ReserveDistributionPreview,
  ReserveOverview,
  ReserveReallocationInput,
  ReserveWithdrawalInput,
  RuleApplyAllInput,
  RuleApplyAllResult,
  RuleApplyInput,
  RuleApplyResult,
  SavingsGoal,
  SavingsGoalCreateInput,
  SavingsGoalUpdateInput,
  SavingsPlanPreview,
  SavingsPlanPreviewInput,
  ScenarioCalculation,
  ScenarioCloneRequest,
  ScenarioComparison,
  ScenarioFinancialSummary,
  ScenarioTimeline,
  ShoppingItem,
  ShoppingItemCreateInput,
  ShoppingItemUpdateInput,
  ShoppingList,
  ShoppingListCreateInput,
  ShoppingListFillResult,
  WeekPlanSuggestionInput,
  WeekPlanSuggestionResult,
  ShoppingListUpdateInput,
  SpecialRepayment,
  SpecialRepaymentInput,
  Transaction,
  TransactionCandidate,
  TransactionCreateInput,
  TransactionDetail,
  TransactionProvenance,
  TransactionSplitsUpdateInput,
  TransactionUpdateInput,
  TransferLink,
  TransferLinkCreateInput,
  UnassignedGoalSplitCandidate,
  WishlistConvertResponse,
  WishlistItem,
  WishlistItemCreateInput,
  WishlistItemUpdateInput,
  WishlistSavingsPlanCreateInput,
  WishlistSavingsPlanResult,
} from "./types";
import type {
  ArchiveApplyInput,
  ArchivePreview,
  BulkCategoryInput,
  BulkCategoryResult,
  CatalogCategory,
  CatalogGroup,
  CategoryCatalog,
  CategoryCreateInput,
  CategoryGroupCreateInput,
  CategoryGroupUpdateInput,
  CategorySuggestionApplyInput,
  CategorySuggestionPreview,
  CategorySuggestionResult,
  CategoryUpdateInput,
  GroupArchiveApplyInput,
  GroupArchivePreview,
  MergeApplyInput,
  MergePreview,
  MergeRequestInput,
  MergeResult,
  MoveApplyInput,
  MovePreview,
  ReorderRequestInput,
  RestoreRequestInput,
  StandardPackageApplyInput,
  StandardPackagePreview,
  StandardPackageResult,
  StandardPackageStatus,
  TransactionCategoryUpdateInput,
} from "./categoryTypes";
import type { AITask, AITaskCreateInput, AITaskDetail } from "./aiTaskTypes";

// Abschnitt 10.4 / 16 des Architekturplans: relativer Pfad als Fallback,
// damit die App hinter Caddy im gleichen Origin funktioniert, ohne dass
// VITE_API_BASE_URL zwingend gesetzt sein muss.
//
// Abschliessende Schraegstriche muessen weg: alle Pfade hier beginnen selbst
// mit "/", und aus dem NAS-Wert "/" wuerde sonst "//v1/..." - der Browser
// liest das als Protokoll-relative URL und haelt "v1" fuer einen Hostnamen.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status?: number;
  /**
   * true NUR, wenn fetch selbst geworfen hat (DNS/Verbindung/CORS/Abbruch) -
   * dann gab es gar keine HTTP-Antwort. Jede echte HTTP-Antwort (auch 4xx/5xx)
   * ist ein Serverfehler und KEIN Erreichbarkeitsproblem; die beiden Faelle
   * wurden frueher zusammengeworfen, weshalb z. B. ein fehlendes Zielkonto
   * (HTTP 400) als "Die API ist nicht erreichbar" erschien.
   */
  readonly isNetworkError: boolean;
  /**
   * Der rohe `detail`-Wert der Serverantwort, sofern es ein einzelner String
   * war. Die Kategorienverwaltung braucht die maschinenlesbare Kennung
   * (z. B. `category_preview_stale`), um eine veraltete Vorschau gezielt neu
   * zu laden, statt den Fehler nur als Text anzuzeigen.
   */
  readonly detail: string | null;
  /**
   * Benannter Grund aus der Form {"detail": {"code": ..., "message": ...}}
   * (KI-Gruende wie `model_context_unknown`, `ai_disabled`). `detail` traegt
   * dann den Klartext, `code` die Kennung fuer die passende Aktion.
   */
  readonly code: string | null;

  constructor(
    message: string,
    status?: number,
    isNetworkError = false,
    detail: string | null = null,
    code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.isNetworkError = isNetworkError;
    this.detail = detail;
    this.code = code;
  }
}

/** Kennung aus einer Fehlerantwort, sofern es eine ApiError mit `detail` ist. */
export function apiErrorDetail(error: unknown): string | null {
  return error instanceof ApiError ? error.detail : null;
}

const NETWORK_ERROR_MESSAGE = "Die API ist nicht erreichbar. Laeuft der Backend-Dienst?";

/**
 * Kurztexte fuer die maschinenlesbaren Kennungen, die das Backend im Feld
 * `detail` liefert (Zugriffspruefung: services/api/app/api/deps.py,
 * Kategorienverwaltung: docs/KATEGORISIERUNGSPLAN.md). Die Kennung bleibt im
 * Text sichtbar, damit Support-Rueckfragen eindeutig bleiben.
 */
const KNOWN_ERROR_DETAIL_TEXTS: Record<string, string> = {
  household_role_readonly:
    "Dein Zugriff auf diesen Haushalt ist auf Lesen beschraenkt - fuer diese Aktion braucht es die Rolle Editor oder Besitzer.",
  household_owner_required:
    "Diese Aktion ist der Besitzerin bzw. dem Besitzer des Haushalts vorbehalten.",
  household_role_unknown:
    "Deine Rolle in diesem Haushalt ist unbekannt. Bitte die Haushaltsverwaltung pruefen.",
  if_match_required:
    "Diese Aenderung verlangt den zuletzt gelesenen Stand. Bitte die Ansicht neu laden und erneut versuchen.",
  version_conflict:
    "Der Datensatz wurde zwischenzeitlich woanders geaendert. Bitte neu laden und die Aenderung erneut vornehmen.",
  category_tree_revision_conflict:
    "Die Kategorienstruktur wurde zwischenzeitlich geaendert. Die Sortierung wurde deshalb nicht uebernommen - bitte neu laden.",
  category_preview_stale:
    "Die Vorschau ist nicht mehr aktuell, weil sich seither etwas geaendert hat. Bitte die Vorschau neu berechnen.",
  category_duplicate_name:
    "In dieser Gruppe gibt es bereits eine Kategorie mit diesem Namen. Bitte einen anderen Namen waehlen oder die vorhandene Kategorie verwenden.",
  category_inactive:
    "Diese Kategorie ist archiviert und kann nicht neu zugeordnet werden. Bitte eine aktive Kategorie waehlen.",
  category_type_locked:
    "Die Art dieser Kategorie ist nach der ersten Verwendung nicht mehr aenderbar. Dafuer braucht es eine neue Kategorie.",
  transaction_has_multiple_splits:
    "Diese Buchung hat mehrere Splits - eine Gesamtkategorie wuerde die Aufteilung ueberschreiben. Bitte die Splits bearbeiten.",
};

/** Statusabhaengiger Ersatztext, wenn der Server keinen `detail`-Text liefert. */
function fallbackMessageForStatus(status: number): string {
  if (status === 401) {
    return `Nicht angemeldet oder Sitzung abgelaufen (HTTP ${status}). Bitte neu anmelden.`;
  }
  if (status === 403) {
    return `Zugriff verweigert (HTTP ${status}).`;
  }
  if (status === 404) {
    return `Nicht gefunden (HTTP ${status}).`;
  }
  if (status === 409) {
    return `Die Aktion widerspricht dem aktuellen Zustand (HTTP ${status}).`;
  }
  if (status >= 500) {
    return `Serverfehler (HTTP ${status}). Die Anfrage ist beim Backend angekommen, dort aber fehlgeschlagen.`;
  }
  return `Anfrage fehlgeschlagen (HTTP ${status}).`;
}

/**
 * Liest den Fehlerkoerper als Text. Bewusst zuerst text() statt json(): so
 * gehen auch nicht-JSON-Antworten (HTML-Fehlerseite eines Proxys, leerer
 * Koerper) nicht verloren. Testdoubles, die nur json() anbieten, werden
 * weiterhin unterstuetzt.
 */
async function readErrorBodyText(response: Response): Promise<string | null> {
  try {
    if (typeof response.text === "function") {
      return await response.text();
    }
    if (typeof response.json === "function") {
      return JSON.stringify(await response.json());
    }
  } catch {
    // Koerper nicht lesbar - der Statuscode bleibt als Information uebrig.
  }
  return null;
}

/**
 * Holt die Servermeldung aus dem Fehlerkoerper. Unterstuetzt den
 * FastAPI-Normalfall {"detail": "<Satz>"}, die 422-Form
 * {"detail": [{"msg": ...}]} sowie {"detail": {"message": ...}}.
 */
function extractDetailMessage(body: unknown): string | null {
  if (typeof body === "string") {
    return body.trim() || null;
  }
  if (!body || typeof body !== "object") {
    return null;
  }
  const detail: unknown = (body as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry): string | null => {
        if (
          entry &&
          typeof entry === "object" &&
          typeof (entry as { msg?: unknown }).msg === "string"
        ) {
          return (entry as { msg: string }).msg;
        }
        return null;
      })
      .filter((msg): msg is string => Boolean(msg));
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }
  if (detail && typeof detail === "object") {
    const nested: unknown = (detail as { message?: unknown }).message;
    if (typeof nested === "string" && nested.trim()) {
      return nested.trim();
    }
  }
  const message: unknown = (body as { message?: unknown }).message;
  if (typeof message === "string" && message.trim()) {
    return message.trim();
  }
  return null;
}

/**
 * Baut aus einer HTTP-Fehlerantwort die Anzeigemeldung. Regel: liegt eine
 * Antwort des Servers vor, wird dessen Meldung gezeigt - niemals ein
 * Erreichbarkeitstext.
 */
/** Kennung aus {"detail": {"code": "..."}}, sonst null. */
function extractDetailCode(body: unknown): string | null {
  if (!body || typeof body !== "object") {
    return null;
  }
  const detail: unknown = (body as { detail?: unknown }).detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const code: unknown = (detail as { code?: unknown }).code;
    if (typeof code === "string" && code.trim()) {
      return code.trim();
    }
  }
  return null;
}

async function buildHttpError(
  response: Response,
): Promise<{ message: string; detail: string | null; code: string | null }> {
  const rawText = await readErrorBodyText(response);
  let parsed: unknown = null;
  if (rawText) {
    try {
      parsed = JSON.parse(rawText);
    } catch {
      parsed = null;
    }
  }

  const code = extractDetailCode(parsed);
  const detail = extractDetailMessage(parsed);
  if (detail) {
    const known = KNOWN_ERROR_DETAIL_TEXTS[detail];
    if (known) {
      return { message: `${known} (Kennung: ${detail})`, detail, code };
    }
    return { message: detail, detail, code };
  }

  const fallback = fallbackMessageForStatus(response.status);
  // Kein JSON-detail: kurzen Klartext (z. B. von einem Reverse Proxy) anhaengen,
  // statt ihn stillschweigend zu verwerfen. HTML-Seiten bleiben aussen vor.
  const snippet = rawText?.trim() ?? "";
  if (snippet && snippet.length <= 200 && !snippet.startsWith("<")) {
    return { message: `${fallback} Serverantwort: ${snippet}`, detail: null, code };
  }
  return { message: fallback, detail: null, code };
}

async function buildHttpErrorMessage(response: Response): Promise<string> {
  return (await buildHttpError(response)).message;
}

/**
 * Ruft GET {API_BASE}/v1/system/health ab und liefert den typisierten
 * SystemHealth-Vertrag aus @finanzplaner/contracts. Wirft eine ApiError mit
 * einer verstaendlichen deutschen Meldung, wenn das Backend nicht erreichbar
 * ist oder einen Fehlerstatus liefert - die App soll dabei nicht abstuerzen.
 */
export async function fetchSystemHealth(): Promise<SystemHealth> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE}/v1/system/health`, {
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiError(NETWORK_ERROR_MESSAGE, undefined, true);
  }

  if (!response.ok) {
    // Auch hier gilt: es gab eine Antwort, also die Meldung des Servers zeigen
    // und nur ersatzweise auf den Statuscode zurueckfallen.
    const message = await buildHttpErrorMessage(response);
    throw new ApiError(`Systemstatus konnte nicht geladen werden: ${message}`, response.status);
  }

  return (await response.json()) as SystemHealth;
}

// ---------------------------------------------------------------------------
// Authentifizierter API-Client (Phase 1: Auth, Haushalt und Datenbank)
// ---------------------------------------------------------------------------
//
// Alle /v1/*-Endpunkte AUSSER /health und /v1/system/health verlangen den
// Header `Authorization: Bearer <supabase-access-token>`. /v1/accounts,
// /v1/categories und /v1/transactions verlangen zusaetzlich
// `X-Household-Id: <uuid>` (siehe Aufgabenstellung). apiFetch kapselt beides
// plus einheitliches Fehlerhandling nach dem Muster von fetchSystemHealth.

interface ApiFetchOptions extends Omit<RequestInit, "headers"> {
  token?: string | null;
  householdId?: string | null;
  headers?: HeadersInit;
}

/**
 * Wie apiFetch (Kopfzeilen, Fehlerbehandlung), liefert aber die rohe Antwort
 * statt JSON - fuer Stroeme (KI-Feld, NDJSON). Ein mitgegebener
 * Accept-Header bleibt stehen; Vorgabe ist JSON.
 */
export async function apiFetchResponse(
  path: string,
  { token, householdId, headers, ...init }: ApiFetchOptions = {},
): Promise<Response> {
  const finalHeaders = new Headers(headers);
  if (!finalHeaders.has("Accept")) {
    finalHeaders.set("Accept", "application/json");
  }
  if (
    init.body !== undefined &&
    !(init.body instanceof FormData) &&
    !finalHeaders.has("Content-Type")
  ) {
    finalHeaders.set("Content-Type", "application/json");
  }
  if (token) {
    finalHeaders.set("Authorization", `Bearer ${token}`);
  }
  if (householdId) {
    finalHeaders.set("X-Household-Id", householdId);
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers: finalHeaders });
  } catch {
    // NUR hier ist "nicht erreichbar" richtig: es kam keine HTTP-Antwort an.
    throw new ApiError(NETWORK_ERROR_MESSAGE, undefined, true);
  }

  if (!response.ok) {
    const { message, detail, code } = await buildHttpError(response);
    throw new ApiError(message, response.status, false, detail, code);
  }
  return response;
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  // JSON-Endpunkte: Accept ist immer JSON, auch wenn ein Aufrufer etwas
  // anderes mitgibt (Ausnahmen gehen ueber apiFetchResponse).
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  const response = await apiFetchResponse(path, { ...options, headers });

  if (response.status === 204) {
    return undefined as T;
  }

  // Erfolgsantworten ebenfalls ueber text() lesen: ein leerer Koerper (manche
  // DELETE-Endpunkte antworten mit 200 statt 204) ist gueltig, echter Muell im
  // Koerper soll dagegen als klare Meldung auffallen und nicht als kryptische
  // JSON-Ausnahme aus dem Aufrufer herausfallen.
  if (typeof response.text === "function") {
    const rawText = await response.text();
    if (!rawText.trim()) {
      return undefined as T;
    }
    try {
      return JSON.parse(rawText) as T;
    } catch {
      throw new ApiError(
        `Die Antwort des Servers war kein gueltiges JSON (HTTP ${response.status}).`,
        response.status,
      );
    }
  }

  return (await response.json()) as T;
}

export function getMe(token: string): Promise<Me> {
  return apiFetch<Me>("/v1/me", { token });
}

export function updateMe(token: string, patch: ProfileUpdate): Promise<Me> {
  return apiFetch<Me>("/v1/me", {
    token,
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function createHousehold(token: string, name: string): Promise<Household> {
  return apiFetch<Household>("/v1/households", {
    token,
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function listHouseholds(token: string): Promise<Household[]> {
  return apiFetch<Household[]>("/v1/households", { token });
}

export function renameHousehold(
  token: string,
  householdId: string,
  name: string,
): Promise<Household> {
  return apiFetch<Household>("/v1/households", {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export function listHouseholdMembers(
  token: string,
  householdId: string,
): Promise<HouseholdMember[]> {
  return apiFetch<HouseholdMember[]>("/v1/households/members", { token, householdId });
}

export function listHouseholdInvites(
  token: string,
  householdId: string,
): Promise<HouseholdInvite[]> {
  return apiFetch<HouseholdInvite[]>("/v1/households/invites", { token, householdId });
}

export function createHouseholdInvite(
  token: string,
  householdId: string,
  role: HouseholdRole,
): Promise<HouseholdInvite> {
  return apiFetch<HouseholdInvite>("/v1/households/invites", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

export function revokeHouseholdInvite(
  token: string,
  householdId: string,
  inviteId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/households/invites/${inviteId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function previewHouseholdInvite(
  token: string,
  code: string,
): Promise<HouseholdInvitePreview> {
  return apiFetch<HouseholdInvitePreview>(
    `/v1/households/invites/redeem/${encodeURIComponent(code)}`,
    { token },
  );
}

export function redeemHouseholdInvite(token: string, code: string): Promise<Household> {
  return apiFetch<Household>("/v1/households/invites/redeem", {
    token,
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function listAccounts(token: string, householdId: string): Promise<Account[]> {
  return apiFetch<Account[]>("/v1/accounts", { token, householdId });
}

export function createAccount(
  token: string,
  householdId: string,
  input: AccountCreateInput,
): Promise<Account> {
  return apiFetch<Account>("/v1/accounts", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listCategories(token: string, householdId: string): Promise<CategoryGroup[]> {
  return apiFetch<CategoryGroup[]>("/v1/categories", { token, householdId });
}

export function listTransactions(
  token: string,
  householdId: string,
  params: ListTransactionsParams = {},
): Promise<Transaction[]> {
  const search = new URLSearchParams();
  if (params.accountId) {
    search.set("account_id", params.accountId);
  }
  if (params.fromDate) {
    search.set("from_date", params.fromDate);
  }
  if (params.toDate) {
    search.set("to_date", params.toDate);
  }
  if (params.search) {
    search.set("search", params.search);
  }
  if (params.searchRegex) {
    search.set("search_regex", params.searchRegex);
  }
  if (params.limit) {
    search.set("limit", String(params.limit));
  }
  const query = search.toString();
  return apiFetch<Transaction[]>(`/v1/transactions${query ? `?${query}` : ""}`, {
    token,
    householdId,
  });
}

export function createTransaction(
  token: string,
  householdId: string,
  input: TransactionCreateInput,
): Promise<Transaction> {
  return apiFetch<Transaction>("/v1/transactions", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getTransaction(
  token: string,
  householdId: string,
  transactionId: string,
): Promise<TransactionDetail> {
  return apiFetch<TransactionDetail>(`/v1/transactions/${transactionId}`, { token, householdId });
}

// --- Analyse-Abfragevertrag ------------------------------------------------
// docs/ANALYSE-EXPLORER-PLAN.md, Baustein B. Explorer, Diagramme, Drilldown
// und Berichte lesen ausschliesslich ueber diese vier Funktionen.

export function getAnalyticsFields(token: string, householdId: string): Promise<FieldCatalog> {
  return apiFetch<FieldCatalog>("/v1/analytics/fields", { token, householdId });
}

export function runAnalyticsQuery(
  token: string,
  householdId: string,
  query: AnalyticsQuery,
  signal?: AbortSignal,
): Promise<AnalyticsResult> {
  return apiFetch<AnalyticsResult>("/v1/analytics", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(query),
    headers: { "Content-Type": "application/json" },
    signal,
  });
}

/**
 * Finanz-Empfehlungen. `withAi` laesst ein lokales Modell zu jeder bereits
 * berechneten Empfehlung einen Handlungssatz formulieren - das dauert
 * spuerbar laenger und aendert keine einzige Zahl.
 */
export function getInsights(
  token: string,
  householdId: string,
  options: {
    months?: number;
    withAi?: boolean;
    anchorYear?: number;
    anchorMonth?: number;
  } = {},
): Promise<InsightReport> {
  const params = new URLSearchParams();
  if (options.months !== undefined) params.set("months", String(options.months));
  if (options.withAi) params.set("with_ai", "true");
  if (options.anchorYear !== undefined) params.set("anchor_year", String(options.anchorYear));
  if (options.anchorMonth !== undefined) params.set("anchor_month", String(options.anchorMonth));
  const query = params.toString();
  return apiFetch<InsightReport>(`/v1/insights${query ? `?${query}` : ""}`, {
    token,
    householdId,
  });
}

export function getAnalyticsFacets(
  token: string,
  householdId: string,
  request: FacetRequest,
  signal?: AbortSignal,
): Promise<FacetResult> {
  return apiFetch<FacetResult>("/v1/analytics/facets", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(request),
    headers: { "Content-Type": "application/json" },
    signal,
  });
}

/**
 * Laedt dieselbe Abfrage als CSV herunter. Bewusst ein eigener fetch statt
 * apiFetch: die Antwort ist ein Datenstrom, kein JSON, und darf nicht durch
 * die JSON-Fehlerbehandlung laufen.
 */
export async function downloadAnalyticsCsv(
  token: string,
  householdId: string,
  query: AnalyticsQuery,
): Promise<Blob> {
  const response = await fetch(`${API_BASE}/v1/analytics/export`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Household-Id": householdId,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(query),
  });
  if (!response.ok) {
    throw new ApiError(`Export fehlgeschlagen (HTTP ${response.status}).`, response.status);
  }
  return response.blob();
}

export function getTransactionProvenance(
  token: string,
  householdId: string,
  transactionId: string,
): Promise<TransactionProvenance> {
  return apiFetch<TransactionProvenance>(`/v1/transactions/${transactionId}/provenance`, {
    token,
    householdId,
  });
}

export function updateTransaction(
  token: string,
  householdId: string,
  transactionId: string,
  input: TransactionUpdateInput,
): Promise<TransactionDetail> {
  return apiFetch<TransactionDetail>(`/v1/transactions/${transactionId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function scanBookedDuplicates(token: string, householdId: string) {
  return apiFetch<import("./types").BookedDuplicateScanResult>("/v1/review/duplicates/scan", {
    token,
    householdId,
  });
}

export function resolveBookedDuplicate(
  token: string,
  householdId: string,
  input: import("./types").ResolveBookedDuplicateInput,
) {
  return apiFetch<void>("/v1/review/duplicates/resolve", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function assessBookedDuplicates(
  token: string,
  householdId: string,
  input: import("./types").BookedDuplicateAssessmentInput,
) {
  return apiFetch<import("./types").BookedDuplicateAssessmentResult>(
    "/v1/review/duplicates/assess-booked",
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function resolveBookedDuplicatesBulk(
  token: string,
  householdId: string,
  input: import("./types").BulkResolveBookedDuplicatesInput,
) {
  return apiFetch<import("./types").BulkResolveBookedDuplicatesResult>(
    "/v1/review/duplicates/resolve-bulk",
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function deleteTransaction(
  token: string,
  householdId: string,
  transactionId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/transactions/${transactionId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function replaceTransactionSplits(
  token: string,
  householdId: string,
  transactionId: string,
  input: TransactionSplitsUpdateInput,
): Promise<TransactionDetail> {
  return apiFetch<TransactionDetail>(`/v1/transactions/${transactionId}/splits`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function bulkCategorizeTransactions(
  token: string,
  householdId: string,
  input: BulkCategorizeInput,
): Promise<BulkCategorizeResult> {
  return apiFetch<BulkCategorizeResult>("/v1/transactions/bulk-categorize", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listUnassignedGoalSplits(
  token: string,
  householdId: string,
  accountId?: string,
): Promise<UnassignedGoalSplitCandidate[]> {
  const search = new URLSearchParams();
  if (accountId) {
    search.set("account_id", accountId);
  }
  const query = search.toString();
  return apiFetch<UnassignedGoalSplitCandidate[]>(
    `/v1/transactions/splits/unassigned-goal${query ? `?${query}` : ""}`,
    { token, householdId },
  );
}

export function assignGoalToSplits(
  token: string,
  householdId: string,
  input: BulkAssignGoalInput,
): Promise<BulkAssignGoalResult> {
  return apiFetch<BulkAssignGoalResult>("/v1/transactions/splits/assign-goal", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function linkTransfer(
  token: string,
  householdId: string,
  input: TransferLinkCreateInput,
): Promise<TransferLink> {
  return apiFetch<TransferLink>("/v1/transfers/link", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function unlinkTransfer(
  token: string,
  householdId: string,
  transferLinkId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/transfers/link/${transferLinkId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function updateAccount(
  token: string,
  householdId: string,
  accountId: string,
  input: AccountUpdateInput,
): Promise<Account> {
  return apiFetch<Account>(`/v1/accounts/${accountId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function reconcileAccount(
  token: string,
  householdId: string,
  accountId: string,
  input: ReconcileInput,
): Promise<Reconciliation> {
  return apiFetch<Reconciliation>(`/v1/accounts/${accountId}/reconcile`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listReconciliations(
  token: string,
  householdId: string,
  accountId: string,
): Promise<Reconciliation[]> {
  return apiFetch<Reconciliation[]>(`/v1/accounts/${accountId}/reconciliations`, {
    token,
    householdId,
  });
}

// ---------------------------------------------------------------------------
// Phase 3: Import-API (Upload, ImportLaeufe, Staging-Zeilen, Commit, Mapping)
// ---------------------------------------------------------------------------

export function uploadImportFile(
  token: string,
  householdId: string,
  file: File,
  accountId?: string,
  mappingProfileId?: string,
): Promise<ImportRun> {
  const formData = new FormData();
  formData.append("file", file);
  if (accountId) {
    formData.append("account_id", accountId);
  }
  if (mappingProfileId) {
    formData.append("mapping_profile_id", mappingProfileId);
  }
  return apiFetch<ImportRun>("/v1/imports/upload", {
    token,
    householdId,
    method: "POST",
    body: formData,
  });
}

export function listImportRuns(token: string, householdId: string): Promise<ImportRun[]> {
  return apiFetch<ImportRun[]>("/v1/imports", { token, householdId });
}

export function getImportRun(
  token: string,
  householdId: string,
  importRunId: string,
): Promise<ImportRun> {
  return apiFetch<ImportRun>(`/v1/imports/${importRunId}`, { token, householdId });
}

/**
 * Aendert einen noch nicht uebernommenen Importlauf (PATCH /v1/imports/{id}).
 *
 * Hauptzweck: einem Lauf nachtraeglich das Zielkonto zuweisen, das beim Upload
 * gefehlt hat - ohne die Datei erneut hochladen zu muessen. Es werden nur die
 * im Objekt enthaltenen Felder geaendert.
 *
 * Fehlerantworten (der `detail`-Text ist deutscher Klartext und direkt
 * anzeigbar): 404 (Lauf, Konto oder Mapping-Profil gehoert nicht zum
 * Haushalt), 409 (Lauf ist nicht mehr im Status 'ready'), 403 (nur Leserecht).
 */
export function updateImportRun(
  token: string,
  householdId: string,
  importRunId: string,
  input: ImportRunUpdateInput,
): Promise<ImportRun> {
  return apiFetch<ImportRun>(`/v1/imports/${importRunId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

/**
 * Entfernt einen nie uebernommenen Importlauf samt Staging-Zeilen
 * (DELETE /v1/imports/{id}, HTTP 204 ohne Koerper).
 *
 * Der Server lehnt mit 409 ab, solange der Lauf verarbeitet wird, und
 * ebenso, wenn aus ihm bereits Buchungen entstanden sind - die wuerden sonst
 * still ihre Herkunft verlieren. Die Serverantwort muss deshalb immer
 * anzeigbar bleiben; eine reine Client-Heuristik reicht nicht (ein teilweise
 * uebernommener Lauf steht weiterhin auf 'ready').
 */
export function deleteImportRun(
  token: string,
  householdId: string,
  importRunId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/imports/${importRunId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

/**
 * Was wuerde ein Zuruecknehmen dieses Laufs anfassen?
 * (GET /v1/imports/{id}/revert-preview, aendert nichts).
 *
 * Gegenstueck zu deleteImportRun fuer den umgekehrten Zeitpunkt: Loeschen
 * geht nur VOR dem Uebernehmen, Zuruecknehmen nur danach. Die Vorschau
 * trennt, was archiviert werden kann, von dem, woran inzwischen etwas haengt
 * - eine nackte Gesamtzahl liesse den Nutzer nicht erkennen, ob er dabei
 * eigene Arbeit verliert.
 */
export function previewImportRevert(
  token: string,
  householdId: string,
  importRunId: string,
): Promise<ImportRevertPreview> {
  return apiFetch<ImportRevertPreview>(`/v1/imports/${importRunId}/revert-preview`, {
    token,
    householdId,
  });
}

/**
 * Nimmt die Buchungen eines uebernommenen Laufs aus dem Bestand
 * (POST /v1/imports/{id}/revert).
 *
 * Die Buchungen werden archiviert, nicht geloescht - und der Importlauf
 * bleibt als Herkunftsnachweis stehen. Danach laesst sich dieselbe Datei
 * erneut einlesen: die Dublettenerkennung sieht archivierte Buchungen nicht.
 *
 * includeLinked nimmt auch die Buchungen mit, an denen inzwischen etwas haengt.
 * Der Server loest die Verknuepfungen dabei sauber auf, statt sie auf eine
 * verschwundene Buchung zeigen zu lassen; die Antwort nennt in `untangled`, was
 * das war.
 */
export function revertImportRun(
  token: string,
  householdId: string,
  importRunId: string,
  includeLinked = false,
): Promise<ImportRevertResult> {
  return apiFetch<ImportRevertResult>(`/v1/imports/${importRunId}/revert`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ include_linked: includeLinked }),
  });
}

/**
 * Bricht einen noch nicht uebernommenen Importlauf ab
 * (POST /v1/imports/{id}/cancel).
 *
 * Der Unterschied zu deleteImportRun ist der Zeitpunkt: Loeschen verlangt
 * einen Lauf, der NICHT in Arbeit ist - genau der Zustand, aus dem ein
 * haengender Lauf nie herauskam, wenn kein Worker lief. Abbrechen greift
 * ausschliesslich in Arbeit: 'pending' wird sofort beendet, bei 'analysing'
 * wird der Abbruch vorgemerkt und der Worker steigt am naechsten Pruefpunkt
 * aus. Alles andere lehnt der Server mit 409 ab.
 */
export function cancelImportRun(
  token: string,
  householdId: string,
  importRunId: string,
): Promise<ImportRun> {
  return apiFetch<ImportRun>(`/v1/imports/${importRunId}/cancel`, {
    token,
    householdId,
    method: "POST",
  });
}

/**
 * Liest denselben Lauf mit korrigierter Spaltenzuordnung erneut ein
 * (POST /v1/imports/{id}/reparse).
 *
 * Bewusst getrennt von updateImportRun: PATCH aendert nur die Verknuepfung zum
 * Mapping-Profil und parst nichts neu - die Oberflaeche versprach damit eine
 * Wirkung, die nicht eintrat. Hier werden die Staging-Zeilen verworfen und aus
 * den bereits gespeicherten Rohzeilen neu aufgebaut; die Datei muss dafuer
 * nicht erneut hochgeladen werden.
 */
export function reparseImportRun(
  token: string,
  householdId: string,
  importRunId: string,
  columnMapping: Record<string, string>,
): Promise<ImportRun> {
  return apiFetch<ImportRun>(`/v1/imports/${importRunId}/reparse`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ column_mapping: columnMapping }),
  });
}

export function getImportRows(
  token: string,
  householdId: string,
  importRunId: string,
): Promise<ImportRow[]> {
  return apiFetch<ImportRow[]>(`/v1/imports/${importRunId}/rows`, { token, householdId });
}

export function commitImportRun(
  token: string,
  householdId: string,
  importRunId: string,
  rowIds?: string[],
): Promise<{ committed: number }> {
  return apiFetch<{ committed: number }>(`/v1/imports/${importRunId}/commit`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(rowIds && rowIds.length > 0 ? { row_ids: rowIds } : {}),
  });
}

/**
 * Laesst die lokale KI ein Spalten-Mapping VORSCHLAGEN
 * (POST /v1/imports/{run_id}/mapping-suggestion).
 *
 * Der Endpunkt antwortet auch bei KI-Ausfall mit HTTP 200 und
 * `available: false` samt Begruendung - ein fehlender Vorschlag ist also kein
 * Fehlerfall. Einzige echte Fehlerantworten: 404 (Lauf unbekannt) und 403
 * (nur Leserecht). Verlangt Editor-Rechte, weil serverseitig ein Modellaufruf
 * ausgeloest und eine Audit-Zeile geschrieben wird.
 *
 * `sampleRows` ist nur fuer Laeufe ohne Staging-Zeilen gedacht (Parse
 * fehlgeschlagen); existieren Staging-Zeilen, gewinnen serverseitig immer die.
 */
export function suggestImportMapping(
  token: string,
  householdId: string,
  importRunId: string,
  sampleRows?: Record<string, string>[],
): Promise<ImportMappingSuggestion> {
  const body = sampleRows && sampleRows.length > 0 ? { sample_rows: sampleRows.slice(0, 20) } : {};
  return apiFetch<ImportMappingSuggestion>(`/v1/imports/${importRunId}/mapping-suggestion`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listMappingProfiles(
  token: string,
  householdId: string,
): Promise<ImportMappingProfile[]> {
  return apiFetch<ImportMappingProfile[]>("/v1/imports/mapping-profiles", { token, householdId });
}

export function createMappingProfile(
  token: string,
  householdId: string,
  input: { name: string; bank_format: ImportFormat; column_mapping: Record<string, string> },
): Promise<ImportMappingProfile> {
  return apiFetch<ImportMappingProfile>("/v1/imports/mapping-profiles", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteMappingProfile(
  token: string,
  householdId: string,
  profileId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/imports/mapping-profiles/${profileId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function updateMappingProfile(
  token: string,
  householdId: string,
  profileId: string,
  input: { name: string; bank_format: ImportFormat; column_mapping: Record<string, string> },
): Promise<ImportMappingProfile> {
  return apiFetch<ImportMappingProfile>(`/v1/imports/mapping-profiles/${profileId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------
// Phase 4: Regelengine, Review-Inbox & Fixkosten
// ---------------------------------------------------------------------------

export function listClassificationRules(
  token: string,
  householdId: string,
): Promise<ClassificationRule[]> {
  return apiFetch<ClassificationRule[]>("/v1/rules", { token, householdId });
}

export function createClassificationRule(
  token: string,
  householdId: string,
  input: ClassificationRuleCreateInput,
): Promise<ClassificationRule> {
  return apiFetch<ClassificationRule>("/v1/rules", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateClassificationRule(
  token: string,
  householdId: string,
  ruleId: string,
  input: ClassificationRuleUpdateInput,
): Promise<ClassificationRule> {
  return apiFetch<ClassificationRule>(`/v1/rules/${ruleId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteClassificationRule(
  token: string,
  householdId: string,
  ruleId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/rules/${ruleId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

/**
 * Wendet eine Regel auf bereits gebuchte Transaktionen an. Regeln greifen
 * sonst nur beim Import - ohne diesen Aufruf bleibt der Bestand unberuehrt.
 * `dryRun` liefert dieselbe Auswertung, schreibt aber nichts.
 */
export function applyRuleToExisting(
  token: string,
  householdId: string,
  ruleId: string,
  input: RuleApplyInput,
  { dryRun }: { dryRun: boolean },
): Promise<RuleApplyResult> {
  const path = dryRun ? `/v1/rules/${ruleId}/apply/preview` : `/v1/rules/${ruleId}/apply`;
  return apiFetch<RuleApplyResult>(path, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Wendet alle laufbereiten Regeln in einem Durchgang auf den Bestand an. Je
 * Buchung gewinnt die erste passende Regel in Prioritaetsreihenfolge - genau
 * wie beim Import. `dryRun` liefert dieselbe Auswertung, schreibt aber nichts.
 */
export function applyAllRulesToExisting(
  token: string,
  householdId: string,
  input: RuleApplyAllInput,
  { dryRun }: { dryRun: boolean },
): Promise<RuleApplyAllResult> {
  const path = dryRun ? "/v1/rules/apply-all/preview" : "/v1/rules/apply-all";
  return apiFetch<RuleApplyAllResult>(path, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

// --- Regel-Import/Export ---------------------------------------------------

export function listRulePackages(token: string, householdId: string): Promise<RulePackageMeta[]> {
  return apiFetch<RulePackageMeta[]>("/v1/rules/packages", { token, householdId });
}

export function getRulePackage(
  token: string,
  householdId: string,
  packageId: string,
): Promise<RulePackage> {
  return apiFetch<RulePackage>(`/v1/rules/packages/${packageId}`, { token, householdId });
}

export function previewRuleImport(
  token: string,
  householdId: string,
  input: RuleImportInput,
): Promise<RuleImportPreview> {
  return apiFetch<RuleImportPreview>("/v1/rules/import/preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function importRules(
  token: string,
  householdId: string,
  input: RuleImportInput,
): Promise<RuleImportResult> {
  return apiFetch<RuleImportResult>("/v1/rules/import", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function exportRules(
  token: string,
  householdId: string,
  input: RuleExportInput,
): Promise<RulePackage> {
  return apiFetch<RulePackage>("/v1/rules/export", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------
// Vertraege (loesen /v1/recurring und /v1/subscriptions ab)
// ---------------------------------------------------------------------------

export function listContracts(
  token: string,
  householdId: string,
  params: {
    status?: ContractStatus;
    contractType?: ContractType;
    direction?: ContractDirection;
    q?: string;
    includeEnded?: boolean;
  } = {},
): Promise<Contract[]> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.contractType) query.set("contract_type", params.contractType);
  if (params.direction) query.set("direction", params.direction);
  if (params.q) query.set("q", params.q);
  if (params.includeEnded) query.set("include_ended", "true");
  const suffix = query.toString();
  return apiFetch<Contract[]>(`/v1/contracts${suffix ? `?${suffix}` : ""}`, {
    token,
    householdId,
  });
}

export function getContract(
  token: string,
  householdId: string,
  contractId: string,
): Promise<Contract> {
  return apiFetch<Contract>(`/v1/contracts/${contractId}`, { token, householdId });
}

export function createContract(
  token: string,
  householdId: string,
  input: ContractCreateInput,
): Promise<Contract> {
  return apiFetch<Contract>("/v1/contracts", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateContract(
  token: string,
  householdId: string,
  contractId: string,
  input: ContractUpdateInput,
  version: number,
): Promise<Contract> {
  return apiFetch<Contract>(`/v1/contracts/${contractId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function deleteContract(
  token: string,
  householdId: string,
  contractId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/contracts/${contractId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function setContractStatus(
  token: string,
  householdId: string,
  contractId: string,
  input: ContractStatusChangeInput,
): Promise<Contract> {
  return apiFetch<Contract>(`/v1/contracts/${contractId}/status`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getContractSummary(token: string, householdId: string): Promise<ContractSummary> {
  return apiFetch<ContractSummary>("/v1/contracts/summary", { token, householdId });
}

/**
 * Stoesst den Abgleich zwischen Vertraegen und Buchungen sofort an.
 *
 * Der Endpunkt laeuft synchron: der Nutzer soll das Ergebnis unmittelbar sehen,
 * statt einen Hintergrundlauf abwarten und die Seite neu laden zu muessen.
 * Idempotent - ein zweiter Klick liefert dieselben Zahlen, ohne doppelt zu
 * schreiben.
 */
export function rescanContracts(token: string, householdId: string): Promise<ContractRescanResult> {
  return apiFetch<ContractRescanResult>("/v1/contracts/rescan", {
    token,
    householdId,
    method: "POST",
  });
}

export function getContractForecast(
  token: string,
  householdId: string,
  days = 90,
): Promise<ContractUpcoming[]> {
  return apiFetch<ContractUpcoming[]>(`/v1/contracts/forecast?days=${days}`, {
    token,
    householdId,
  });
}

/**
 * Vertrags-Cashflow je Kalendermonat beziehungsweise -quartal.
 *
 * Loest die rollierende Tagesprognose als Grundlage der Prognosekarte ab: in
 * "die naechsten 30 Tage" fiel ein monatlicher Vertrag je nach Faelligkeitstag
 * ein- oder zweimal, in "die naechsten 90 Tage" drei- oder viermal. Im
 * Kalenderraster deckt sich der erste Eimer mit der Fixkostensumme daneben.
 */
export function getContractCashflow(
  token: string,
  householdId: string,
  granularity: CashflowGranularity = "month",
  periods = 6,
): Promise<ContractCashflowResponse> {
  return apiFetch<ContractCashflowResponse>(
    `/v1/contracts/cashflow?granularity=${granularity}&periods=${periods}`,
    { token, householdId },
  );
}

export function getContractCostTarget(
  token: string,
  householdId: string,
): Promise<ContractCostTarget> {
  return apiFetch<ContractCostTarget>("/v1/contracts/cost-target", { token, householdId });
}

export function updateContractCostTarget(
  token: string,
  householdId: string,
  input: ContractCostTargetInput,
): Promise<ContractCostTarget> {
  return apiFetch<ContractCostTarget>("/v1/contracts/cost-target", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function listContractScenarios(
  token: string,
  householdId: string,
): Promise<ContractScenario[]> {
  return apiFetch<ContractScenario[]>("/v1/contracts/scenarios", { token, householdId });
}

export function createContractScenario(
  token: string,
  householdId: string,
  input: ContractScenarioCreateInput,
): Promise<ContractScenario> {
  return apiFetch<ContractScenario>("/v1/contracts/scenarios", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateContractScenario(
  token: string,
  householdId: string,
  scenarioId: string,
  input: ContractScenarioUpdateInput,
): Promise<ContractScenario> {
  return apiFetch<ContractScenario>(`/v1/contracts/scenarios/${scenarioId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteContractScenario(
  token: string,
  householdId: string,
  scenarioId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/contracts/scenarios/${scenarioId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function listContractHistory(
  token: string,
  householdId: string,
  contractId: string,
): Promise<ContractAmountEntry[]> {
  return apiFetch<ContractAmountEntry[]>(`/v1/contracts/${contractId}/history`, {
    token,
    householdId,
  });
}

export function addContractAmount(
  token: string,
  householdId: string,
  contractId: string,
  input: { amount_minor: number; valid_from: string },
): Promise<Contract> {
  return apiFetch<Contract>(`/v1/contracts/${contractId}/history`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listContractOccurrences(
  token: string,
  householdId: string,
  contractId: string,
): Promise<ContractOccurrence[]> {
  return apiFetch<ContractOccurrence[]>(`/v1/contracts/${contractId}/occurrences`, {
    token,
    householdId,
  });
}

export function listContractEvents(
  token: string,
  householdId: string,
  contractId: string,
): Promise<ContractEvent[]> {
  return apiFetch<ContractEvent[]>(`/v1/contracts/${contractId}/events`, {
    token,
    householdId,
  });
}

export function acknowledgeContractEvent(
  token: string,
  householdId: string,
  eventId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/contracts/events/${eventId}/ack`, {
    token,
    householdId,
    method: "POST",
  });
}

export function previewContractMerge(
  token: string,
  householdId: string,
  input: { target_id: string; source_ids: string[] },
): Promise<ContractMergePreview> {
  return apiFetch<ContractMergePreview>("/v1/contracts/merge/preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function applyContractMerge(
  token: string,
  householdId: string,
  input: { target_id: string; source_ids: string[]; request_id: string },
): Promise<ContractMergeResult> {
  return apiFetch<ContractMergeResult>("/v1/contracts/merge", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getContractMergeUndoPreview(
  token: string,
  householdId: string,
  mergeId: string,
): Promise<ContractMergeUndoPreview> {
  return apiFetch<ContractMergeUndoPreview>(`/v1/contracts/merges/${mergeId}/undo-preview`, {
    token,
    householdId,
  });
}

export function undoContractMerge(
  token: string,
  householdId: string,
  mergeId: string,
): Promise<ContractMergeUndoResult> {
  return apiFetch<ContractMergeUndoResult>(`/v1/contracts/merges/${mergeId}/undo`, {
    token,
    householdId,
    method: "POST",
  });
}

export function previewContractReassign(
  token: string,
  householdId: string,
  transactionId: string,
  targetContractId: string | null,
): Promise<ContractReassignPreview> {
  return apiFetch<ContractReassignPreview>(
    `/v1/contracts/transactions/${transactionId}/reassign/preview`,
    {
      token,
      householdId,
      method: "POST",
      body: JSON.stringify({ target_contract_id: targetContractId }),
    },
  );
}

export function applyContractReassign(
  token: string,
  householdId: string,
  transactionId: string,
  targetContractId: string | null,
): Promise<ContractReassignResult> {
  return apiFetch<ContractReassignResult>(`/v1/contracts/transactions/${transactionId}/reassign`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ target_contract_id: targetContractId }),
  });
}

/**
 * Schluesselbewusste Duplikatvorschlaege fuer die Vertraege-Seite
 * (Abschnitt 19.10) - dieselbe Erkennung wie in der Review-Inbox, aber als
 * eigene Liste mit `total`/`truncated` statt als Inbox-Eintrag.
 */
export function getContractDuplicateSuggestions(
  token: string,
  householdId: string,
): Promise<ContractDuplicateSuggestionsResponse> {
  return apiFetch<ContractDuplicateSuggestionsResponse>("/v1/contracts/duplicate-suggestions", {
    token,
    householdId,
  });
}

/**
 * Gegenpruefung durch das lokale Sprachmodell (Plan 19.11).
 *
 * Bewusst OHNE Sonderbehandlung eines Fehlerfalls: der Endpunkt antwortet auch
 * dann mit 200, wenn kein Modell erreichbar ist, und legt den Klartext in
 * `meldung`. Ein 503 waere fuer einen abschaltbaren Zusatz die falsche Antwort,
 * und die Oberflaeche haette sonst zwei Wege fuer denselben Sachverhalt.
 *
 * `contractIds` grenzt den Lauf auf Paare ein, bei denen BEIDE Vertraege in der
 * Liste stehen; ohne die Angabe betrachtet der Server alle Paare des Haushalts.
 */
export function crosscheckContracts(
  token: string,
  householdId: string,
  contractIds?: string[],
): Promise<ContractAICrosscheckResponse> {
  return apiFetch<ContractAICrosscheckResponse>("/v1/contracts/ai-crosscheck", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ contract_ids: contractIds ?? null }),
  });
}

// ---------------------------------------------------------------------------
// Belege je Preisstufe, freie Buchungen, Nachtragen (Plan 19.7/19.11)
// ---------------------------------------------------------------------------
//
// Der Ablauf ist bewusst dreiteilig: sehen (evidence), suchen
// (free-transactions), pruefen (assignment-preview). Geschrieben wird danach
// ueber den VORHANDENEN `reassign`-Endpunkt je Buchung, gefolgt von genau einem
// `recalculate`. Es entsteht hier kein zweiter Schreibweg - `assignmentPreview`
// und `checkContractTransactionsWithAI` aendern nichts.

/**
 * Welche Buchungen stehen hinter welcher Preisstufe?
 *
 * `has_evidence=false` an einer Stufe ist der Normalfall bei aus der
 * Datenuebernahme stammenden Verlaeufen und muss in der Oberflaeche benannt
 * werden - eine leere Liste allein liesse offen, ob die Stufe unbelegt oder die
 * Anzeige kaputt ist.
 */
export function getContractAmountEvidence(
  token: string,
  householdId: string,
  contractId: string,
): Promise<ContractAmountEvidenceResponse> {
  return apiFetch<ContractAmountEvidenceResponse>(
    `/v1/contracts/${contractId}/amount-history/evidence`,
    { token, householdId },
  );
}

/**
 * Buchungen des Haushalts, die noch keinem Vertrag zugeordnet sind.
 *
 * Ohne Parameter gilt die Vorbelegung aus dem Vertrag (Anbieterwort,
 * Betragsband, Rueckblick). Jeder Parameter ueberschreibt sie einzeln,
 * `use_contract_defaults=false` schaltet sie ganz ab - der Ausweg fuer Faelle,
 * in denen die Vorbelegung nichts findet. Die Richtung folgt immer dem Vertrag
 * und ist nicht ueberschreibbar.
 */
export function getContractFreeTransactions(
  token: string,
  householdId: string,
  contractId: string,
  params: {
    dateFrom?: string | null;
    dateTo?: string | null;
    amountMinMinor?: number | null;
    amountMaxMinor?: number | null;
    search?: string | null;
    useContractDefaults?: boolean;
    limit?: number;
    offset?: number;
  } = {},
): Promise<ContractFreeTransactionsResponse> {
  const query = new URLSearchParams();
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);
  if (params.amountMinMinor != null) query.set("amount_min_minor", String(params.amountMinMinor));
  if (params.amountMaxMinor != null) query.set("amount_max_minor", String(params.amountMaxMinor));
  if (params.search?.trim()) query.set("search", params.search.trim());
  // Nur senden, wenn ausdruecklich abgeschaltet: der Serverdefault ist true,
  // und ein mitgeschicktes `true` erzeugte nur Rauschen im Query-Key.
  if (params.useContractDefaults === false) query.set("use_contract_defaults", "false");
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  const suffix = query.toString();
  return apiFetch<ContractFreeTransactionsResponse>(
    `/v1/contracts/${contractId}/free-transactions${suffix ? `?${suffix}` : ""}`,
    { token, householdId },
  );
}

/**
 * Vorschau des Zuordnens mehrerer Buchungen. Schreibt nichts.
 *
 * Bewusst fuer die GESAMTE Auswahl und nicht je Buchung: erst die Gesamtmenge
 * ergibt die Preistreppe, die am Ende entsteht - drei nachgetragene Buchungen
 * sind ein Vorgang, nicht drei.
 */
export function previewContractAssignment(
  token: string,
  householdId: string,
  contractId: string,
  transactionIds: string[],
): Promise<ContractAssignmentPreview> {
  return apiFetch<ContractAssignmentPreview>(`/v1/contracts/${contractId}/assignment-preview`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ transaction_ids: transactionIds }),
  });
}

/**
 * Rechnet den Betragsverlauf aus den zugeordneten Buchungen neu.
 *
 * Der Schritt NACH dem Zuordnen - ohne ihn haengt die neue Buchung am Vertrag,
 * der Verlauf bliebe aber der alte. `contracts.amount_minor` bleibt dabei
 * unangetastet; weicht `resulting_amount_minor` davon ab, ist das ein
 * Anzeigefall fuer den Nutzer und kein Fehler.
 */
export function recalculateContractAmountHistory(
  token: string,
  householdId: string,
  contractId: string,
): Promise<ContractAmountRecalculationResult> {
  return apiFetch<ContractAmountRecalculationResult>(
    `/v1/contracts/${contractId}/amount-history/recalculate`,
    { token, householdId, method: "POST" },
  );
}

/**
 * Laesst das lokale Sprachmodell die restlichen freien Buchungen beurteilen.
 *
 * Wie `crosscheckContracts` OHNE Sonderbehandlung eines Fehlerfalls: der
 * Endpunkt antwortet auch ohne erreichbares Modell mit 200 und legt den
 * Klartext in `meldung`.
 *
 * `transactionIds` schneidet die deterministische Vorauswahl ein, umgeht sie
 * nicht - eine bereits zugeordnete Buchung kommt auch benannt nicht durch.
 */
export function checkContractTransactionsWithAI(
  token: string,
  householdId: string,
  contractId: string,
  transactionIds?: string[],
): Promise<ContractAITransactionCheckResponse> {
  return apiFetch<ContractAITransactionCheckResponse>(
    `/v1/contracts/${contractId}/ai-transaction-check`,
    {
      token,
      householdId,
      method: "POST",
      body: JSON.stringify({ transaction_ids: transactionIds ?? null }),
    },
  );
}

export function checkContractDuplicates(
  token: string,
  householdId: string,
  input: {
    name: string;
    payee_id?: string;
    amount_minor?: number;
    frequency?: ContractFrequency;
    exclude_id?: string;
  },
): Promise<ContractDuplicateCandidate[]> {
  return apiFetch<ContractDuplicateCandidate[]>("/v1/contracts/duplicate-check", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Anbietervorschlaege aus dem globalen Katalog fuer das Anlegen-Formular.
 *
 * `query` wird gegen Anbietername und Suchmuster geprueft; der Endpunkt
 * verlangt den Parameter, ein leerer String liefert 422. Aufrufer entscheiden
 * deshalb selbst, ab welcher Eingabelaenge sie fragen.
 */
export function getContractProviderSuggestions(
  token: string,
  householdId: string,
  query: string,
): Promise<ContractProviderSuggestion[]> {
  return apiFetch<ContractProviderSuggestion[]>(
    `/v1/contracts/provider-suggestions?q=${encodeURIComponent(query)}`,
    { token, householdId },
  );
}

export function getContractSettings(token: string, householdId: string): Promise<ContractSettings> {
  return apiFetch<ContractSettings>("/v1/contracts/settings", { token, householdId });
}

export function updateContractSettings(
  token: string,
  householdId: string,
  input: ContractSettings,
): Promise<ContractSettings> {
  return apiFetch<ContractSettings>("/v1/contracts/settings", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function getReviewInbox(
  token: string,
  householdId: string,
  params: { limit?: number; offset?: number; search?: string } = {},
): Promise<ReviewInboxResponse> {
  const query = new URLSearchParams();
  if (params.limit != null) query.set("unclassified_limit", String(params.limit));
  if (params.offset != null) query.set("unclassified_offset", String(params.offset));
  if (params.search?.trim()) query.set("unclassified_search", params.search.trim());
  const suffix = query.toString();
  const inbox = await apiFetch<ReviewInboxResponse>(`/v1/review${suffix ? `?${suffix}` : ""}`, {
    token,
    householdId,
  });

  // Die Felder sind Teil des aktuellen Vertrags. Eine noch laufende alte API
  // liefert sie zur Laufzeit trotz TypeScript nicht. In diesem Fall darf die
  // UI nicht aus den maximal 100 sichtbaren Zeilen eine erfundene Gesamtzahl
  // ableiten ("100 Treffer von 100").
  const paginationMetadata = [
    inbox.unclassified_open_count,
    inbox.unclassified_total_count,
    inbox.unclassified_offset,
    inbox.unclassified_limit,
  ];
  if (!paginationMetadata.every((value) => Number.isInteger(value) && value >= 0)) {
    throw new Error(
      "Die Review-API liefert noch keine vollständigen Filter- und Seiteninformationen. Bitte das Backend neu starten.",
    );
  }

  return inbox;
}

/**
 * Buchungen, Preisstufen und Score-Bestandteile eines Vertragsvorschlags.
 *
 * Der Vorschlag wird bei jedem Aufruf neu erkannt - er ist nirgends
 * gespeichert. Ist die Ankerbuchung inzwischen kategorisiert oder der Vertrag
 * bereits angelegt, antwortet der Server mit 404 statt einem leeren Detail.
 */
export function getContractSuggestionDetail(
  token: string,
  householdId: string,
  sourceTransactionId: string,
): Promise<ReviewContractSuggestionDetail> {
  return apiFetch<ReviewContractSuggestionDetail>(
    `/v1/review/contract-suggestions/${sourceTransactionId}`,
    { token, householdId },
  );
}

/**
 * VERALTET, abgeloest durch getContractSuggestionDetail.
 *
 * Der Altpfad kann halbjaehrliche und unsichere Kandidaten nicht darstellen
 * und antwortet dort mit 404.
 */
export function getRecurringSuggestionDetail(
  token: string,
  householdId: string,
  sourceTransactionId: string,
): Promise<ReviewRecurringDetail> {
  return apiFetch<ReviewRecurringDetail>(
    `/v1/review/recurring-suggestions/${sourceTransactionId}`,
    { token, householdId },
  );
}

export function acceptReviewProposal(
  token: string,
  householdId: string,
  input: ReviewAcceptInput,
): Promise<void> {
  return apiFetch<void>("/v1/review/accept", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Bestaetigt mehrere moegliche Dubletten in einem Zug
 * (POST /v1/review/accept/duplicates).
 *
 * Ein eigener Endpunkt statt einer Schleife im Client: fuenfzig einzelne
 * Requests waeren fuenfzig Transaktionen, von denen die Haelfte durchgehen kann
 * und die andere nicht. Die Antwort nennt, was tatsaechlich entschieden wurde -
 * `skipped` sind Zeilen, die inzwischen nicht mehr offen waren.
 */
export function acceptDuplicatesBulk(
  token: string,
  householdId: string,
  importRowIds: string[],
): Promise<{ confirmed: number; skipped: number }> {
  return apiFetch<{ confirmed: number; skipped: number }>("/v1/review/accept/duplicates", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ import_row_ids: importRowIds }),
  });
}

/** Ein Urteil der KI zu genau einem Dublettenkandidaten. */
export interface DuplicateAssessment {
  import_row_id: string;
  verdict: "same" | "different" | "unsure";
  reason: string;
  confidence: number;
}

export interface DuplicateAssessmentResult {
  available: boolean;
  reason: string;
  message: string;
  model_name: string | null;
  prompt_version: string;
  assessments: DuplicateAssessment[];
}

/**
 * Laesst das lokale Modell gemeldete Dublettenkandidaten beurteilen
 * (POST /v1/review/duplicates/assess).
 *
 * Antwortet IMMER mit 200: ein Fehlschlag kommt als available=false mit
 * Begruendung. Die Einschaetzung wird nie angewendet - sie steht neben der
 * Zeile, entschieden wird weiter per Knopf.
 */
export function assessDuplicates(
  token: string,
  householdId: string,
  importRowIds: string[],
): Promise<DuplicateAssessmentResult> {
  return apiFetch<DuplicateAssessmentResult>("/v1/review/duplicates/assess", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ import_row_ids: importRowIds }),
  });
}

export function correctReviewProposal(
  token: string,
  householdId: string,
  input: ReviewCorrectInput,
): Promise<void> {
  return apiFetch<void>("/v1/review/correct", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function previewReviewRule(
  token: string,
  householdId: string,
  input: ReviewRulePreviewInput,
): Promise<RuleApplyResult> {
  return apiFetch<RuleApplyResult>("/v1/review/rule-preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getReviewHistoryMatch(
  token: string,
  householdId: string,
  transactionId: string,
): Promise<ReviewHistoryMatch> {
  return apiFetch<ReviewHistoryMatch>(`/v1/review/history-match/${transactionId}`, {
    token,
    householdId,
  });
}

export function getReviewHistoryMatches(
  token: string,
  householdId: string,
): Promise<ReviewHistoryMatchesResponse> {
  return apiFetch<ReviewHistoryMatchesResponse>("/v1/review/history-matches", {
    token,
    householdId,
  });
}

// ---------------------------------------------------------------------------
// Phase 5: Budgetkern, Jahresmatrix & Dashboard
// ---------------------------------------------------------------------------

export function getBudgetSummary(
  token: string,
  householdId: string,
  year: number,
  month: number,
): Promise<BudgetSummary> {
  return apiFetch<BudgetSummary>(`/v1/budgets/${year}/${month}`, { token, householdId });
}

export function updateBudgetAllocations(
  token: string,
  householdId: string,
  year: number,
  month: number,
  allocations: BudgetAllocationInput[],
): Promise<BudgetSummary> {
  return apiFetch<BudgetSummary>(`/v1/budgets/${year}/${month}/allocations`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify({ allocations }),
  });
}

export function closeBudgetMonth(
  token: string,
  householdId: string,
  year: number,
  month: number,
): Promise<CloseMonthResponse> {
  return apiFetch<CloseMonthResponse>(`/v1/budgets/${year}/${month}/close`, {
    token,
    householdId,
    method: "POST",
  });
}

export function getAnnualMatrix(
  token: string,
  householdId: string,
  year: number,
): Promise<AnnualMatrixResponse> {
  return apiFetch<AnnualMatrixResponse>(`/v1/budgets/${year}/matrix`, { token, householdId });
}

export function getReserveOverview(
  token: string,
  householdId: string,
  year: number,
  month: number,
): Promise<ReserveOverview> {
  return apiFetch<ReserveOverview>(`/v1/reserves/overview?year=${year}&month=${month}`, {
    token,
    householdId,
  });
}

export function createReserveDeposit(
  token: string,
  householdId: string,
  input: ReserveDepositInput,
): Promise<ReserveBatch> {
  return apiFetch<ReserveBatch>("/v1/reserves/deposits", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createReserveWithdrawal(
  token: string,
  householdId: string,
  input: ReserveWithdrawalInput,
): Promise<ReserveBatch> {
  return apiFetch<ReserveBatch>("/v1/reserves/withdrawals", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createReserveReallocation(
  token: string,
  householdId: string,
  input: ReserveReallocationInput,
): Promise<ReserveBatch> {
  return apiFetch<ReserveBatch>("/v1/reserves/reallocations", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function skipReserveMonth(
  token: string,
  householdId: string,
  input: { pot_id: string; year: number; month: number },
): Promise<void> {
  return apiFetch<void>("/v1/reserves/month-decisions/skip", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function undoSkipReserveMonth(
  token: string,
  householdId: string,
  input: { pot_id: string; year: number; month: number },
): Promise<void> {
  return apiFetch<void>("/v1/reserves/month-decisions/skip", {
    token,
    householdId,
    method: "DELETE",
    body: JSON.stringify(input),
  });
}

export function previewReserveDistribution(
  token: string,
  householdId: string,
  input: { year: number; month: number; total_minor: number },
): Promise<ReserveDistributionPreview> {
  return apiFetch<ReserveDistributionPreview>("/v1/reserves/distribution-preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getOverviewReport(
  token: string,
  householdId: string,
): Promise<OverviewReportResponse> {
  return apiFetch<OverviewReportResponse>("/v1/reports/overview", { token, householdId });
}

export function getCashflowTrend(
  token: string,
  householdId: string,
  months: number,
): Promise<CashflowTrendResponse> {
  return apiFetch<CashflowTrendResponse>(`/v1/reports/cashflow-trend?months=${months}`, {
    token,
    householdId,
  });
}

/**
 * Verlauf der vereinbarten Fixkosten (1-36 Monate, Standard 24).
 *
 * Gezeigt wird der zum Monatsletzten gueltige Preis aus dem Betragsverlauf -
 * nicht der gebuchte Betrag. Genau das macht eine Preiserhoehung sichtbar, auch
 * wenn die erste hoehere Abbuchung noch aussteht.
 */
export function getFixedCostsTrend(
  token: string,
  householdId: string,
  months = 24,
): Promise<FixedCostsTrendResponse> {
  return apiFetch<FixedCostsTrendResponse>(`/v1/reports/fixed-costs-trend?months=${months}`, {
    token,
    householdId,
  });
}

// ---------------------------------------------------------------------------
// Phase 6: Lokale KI / LM Studio Integration API
// ---------------------------------------------------------------------------

export function getAIHealth(token: string, householdId: string): Promise<AIHealthResponse> {
  return apiFetch<AIHealthResponse>("/v1/ai/health", { token, householdId });
}

/**
 * Alle in LM Studio installierten Modelle - Grundlage der Auswahl.
 * Einbettungsmodelle sind enthalten, aber als nicht chatfaehig markiert.
 */
/**
 * Modellliste. Mit `provider` die eines anderen als des gespeicherten
 * Anbieters - die Einstellungen zeigen nach einem Wechsel sofort dessen
 * Modelle, ohne vorher zu speichern.
 */
export function getAIModels(
  token: string,
  householdId: string,
  provider?: ProviderId,
): Promise<AIModelCatalog> {
  const query = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  return apiFetch<AIModelCatalog>(`/v1/ai/models${query}`, { token, householdId });
}

export function getAISettings(token: string, householdId: string): Promise<AISettings> {
  return apiFetch<AISettings>("/v1/ai/settings", { token, householdId });
}

export function updateAISettings(
  token: string,
  householdId: string,
  input: AISettingsUpdate,
): Promise<AISettings> {
  return apiFetch<AISettings>("/v1/ai/settings", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

/**
 * Hinterlegt den API-Schluessel eines KI-Anbieters (nur der Owner, sonst 403
 * `household_owner_required`). Der Server legt ihn verschluesselt ab und
 * antwortet nur mit dem Stand ("…abcd") - der Wert kommt nie zurueck. Ein
 * unbrauchbarer Schluessel kommt als 400 mit deutschem Satz in `detail`.
 *
 * Der Schluessel wird hier bewusst nirgends protokolliert, auch nicht im
 * Fehlerfall.
 */
export function storeAIProviderKey(
  token: string,
  householdId: string,
  provider: ProviderId,
  apiKey: string,
): Promise<AIKeyStatus> {
  const body: AIKeyInput = { api_key: apiKey };
  return apiFetch<AIKeyStatus>(`/v1/ai/provider-keys/${encodeURIComponent(provider)}`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Entfernt den API-Schluessel eines KI-Anbieters (nur der Owner). */
export function removeAIProviderKey(
  token: string,
  householdId: string,
  provider: ProviderId,
): Promise<AIKeyStatus> {
  return apiFetch<AIKeyStatus>(`/v1/ai/provider-keys/${encodeURIComponent(provider)}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

/**
 * Laedt das eingestellte Modell mit dem eingestellten Kontextfenster in LM
 * Studio. Kein Anfragekoerper: geladen wird, was in den Einstellungen steht.
 *
 * Bewusst ohne clientseitiges Zeitlimit: ein 15-GB-Modell braucht gemessen
 * rund 40 Sekunden, ein grosses mit weitem Fenster deutlich laenger. Ein
 * Abbruch hier wuerde den Ladevorgang in LM Studio nicht stoppen, sondern nur
 * die Rueckmeldung darueber verlieren. Die Grenze zieht die API
 * (ai_runtime.LOAD_TIMEOUT_SECONDS) und meldet sie im Klartext.
 */
export function loadAIModel(token: string, householdId: string): Promise<AIModelLoadResult> {
  return apiFetch<AIModelLoadResult>("/v1/ai/models/load", {
    token,
    householdId,
    method: "POST",
  });
}

export function classifyWithAI(
  token: string,
  householdId: string,
  input: AIClassifyInput,
): Promise<AIClassifyResult> {
  return apiFetch<AIClassifyResult>("/v1/ai/classify", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getAIDecisions(
  token: string,
  householdId: string,
  limit?: number,
  classificationRunId?: string | null,
): Promise<AIDecisionLog[]> {
  const search = new URLSearchParams();
  if (limit) search.set("limit", String(limit));
  if (classificationRunId) search.set("classification_run_id", classificationRunId);
  const query = search.toString();
  return apiFetch<AIDecisionLog[]>(`/v1/ai/decisions${query ? `?${query}` : ""}`, {
    token,
    householdId,
  });
}

/** Vorschau der Sammelübernahme: schreibt nichts, liefert je Buchung, was
 *  passieren würde. Wird vor jedem Anwenden aufgerufen. */
export function previewAIProposals(
  token: string,
  householdId: string,
  input: AIProposalApplyInput,
): Promise<AIProposalBatchResult> {
  return apiFetch<AIProposalBatchResult>("/v1/ai/proposals/preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Wendet die sicheren Vorschläge an. `request_id` macht einen Wiederholversuch
 *  idempotent (der Server liefert dann das gespeicherte Ergebnis zurück). */
export function applyAIProposals(
  token: string,
  householdId: string,
  input: AIProposalApplyInput,
): Promise<AIProposalBatchResult> {
  return apiFetch<AIProposalBatchResult>("/v1/ai/proposals/apply", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createClassificationRun(
  token: string,
  householdId: string,
  requestId?: string,
): Promise<ClassificationRun> {
  return apiFetch<ClassificationRun>("/v1/ai/classify-runs", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({
      ...(requestId ? { request_id: requestId } : {}),
    }),
  });
}

export function getClassificationRun(
  token: string,
  householdId: string,
  runId: string,
): Promise<ClassificationRun> {
  return apiFetch<ClassificationRun>(`/v1/ai/classify-runs/${runId}`, { token, householdId });
}

export function listClassificationRuns(
  token: string,
  householdId: string,
  params: { status?: "active" | "all"; limit?: number; offset?: number } = {},
): Promise<ClassificationRun[]> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  const query = search.toString();
  return apiFetch<ClassificationRun[]>(`/v1/ai/classify-runs${query ? `?${query}` : ""}`, {
    token,
    householdId,
  });
}

/**
 * Juengster KI-Lauf des Haushalts - unabhaengig davon, ob dieser Browser ihn
 * gestartet hat. Nur dadurch ueberlebt der Laufstatus Seitenwechsel, Reload
 * und einen Wechsel des Geraets (siehe docs/KI-FINANZASSISTENT-PLAN.md,
 * Ausbaustufe 0); vorher lag die Run-ID ausschliesslich im lokalen State der
 * Review-Seite.
 */
export async function getLatestClassificationRun(
  token: string,
  householdId: string,
): Promise<ClassificationRun | null> {
  const runs = await listClassificationRuns(token, householdId, { limit: 1 });
  return runs[0] ?? null;
}

export function cancelClassificationRun(
  token: string,
  householdId: string,
  runId: string,
): Promise<ClassificationRun> {
  return apiFetch<ClassificationRun>(`/v1/ai/classify-runs/${runId}/cancel`, {
    token,
    householdId,
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Persistente KI-Warteschlange
// ---------------------------------------------------------------------------

export function createAiTask(
  token: string,
  householdId: string,
  input: AITaskCreateInput,
): Promise<AITask> {
  return apiFetch<AITask>("/v1/ai/tasks", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ ...input, input: input.input ?? {} }),
  });
}

export function listAiTasks(
  token: string,
  householdId: string,
  params: { status?: "active" | "all"; limit?: number; offset?: number } = {},
): Promise<AITask[]> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  const query = search.toString();
  return apiFetch<AITask[]>(`/v1/ai/tasks${query ? `?${query}` : ""}`, {
    token,
    householdId,
  });
}

export function getAiTask(
  token: string,
  householdId: string,
  taskId: string,
): Promise<AITaskDetail> {
  return apiFetch<AITaskDetail>(`/v1/ai/tasks/${taskId}`, { token, householdId });
}

/**
 * `force` ist die Notbremse: Sie beendet eine laufende Aufgabe sofort, statt
 * auf das Ende des aktuellen Modellaufrufs zu warten. Der Server verlangt
 * dafür eine bereits gestellte Abbruchbitte — die schonende Variante bleibt
 * der Normalfall.
 */
export function cancelAiTask(
  token: string,
  householdId: string,
  taskId: string,
  options: { force?: boolean } = {},
): Promise<AITask> {
  return apiFetch<AITask>(`/v1/ai/tasks/${taskId}/cancel${options.force ? "?force=true" : ""}`, {
    token,
    householdId,
    method: "POST",
  });
}

export function getAiRuntime(token: string, householdId: string): Promise<AIRuntimeStatus> {
  return apiFetch<AIRuntimeStatus>("/v1/ai/runtime", { token, householdId });
}

export function completeAiTask(
  token: string,
  householdId: string,
  taskId: string,
): Promise<AITask> {
  return apiFetch<AITask>(`/v1/ai/tasks/${taskId}/complete`, {
    token,
    householdId,
    method: "POST",
  });
}

export function retryAiTask(token: string, householdId: string, taskId: string): Promise<AITask> {
  return apiFetch<AITask>(`/v1/ai/tasks/${taskId}/retry`, {
    token,
    householdId,
    method: "POST",
  });
}

/** Entfernt einen abgeschlossenen Verlaufseintrag; aktive Aufgaben lehnt der Server ab. */
export function deleteAiTask(token: string, householdId: string, taskId: string): Promise<void> {
  return apiFetch<void>(`/v1/ai/tasks/${taskId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Phase 7: Sparziele, Wunschliste, Vermögen, Schulden & Nettovermögen
// ---------------------------------------------------------------------------

export function listGoals(token: string, householdId: string): Promise<SavingsGoal[]> {
  return apiFetch<SavingsGoal[]>("/v1/goals", { token, householdId });
}

export function createGoal(
  token: string,
  householdId: string,
  input: SavingsGoalCreateInput,
): Promise<SavingsGoal> {
  return apiFetch<SavingsGoal>("/v1/goals", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateGoal(
  token: string,
  householdId: string,
  goalId: string,
  input: SavingsGoalUpdateInput,
): Promise<SavingsGoal> {
  return apiFetch<SavingsGoal>(`/v1/goals/${goalId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteGoal(token: string, householdId: string, goalId: string): Promise<void> {
  return apiFetch<void>(`/v1/goals/${goalId}`, { token, householdId, method: "DELETE" });
}

export function previewSavingsPlan(
  token: string,
  householdId: string,
  input: SavingsPlanPreviewInput,
): Promise<SavingsPlanPreview> {
  return apiFetch<SavingsPlanPreview>("/v1/goals/planning-preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listWishlistItems(token: string, householdId: string): Promise<WishlistItem[]> {
  return apiFetch<WishlistItem[]>("/v1/wishlist", { token, householdId });
}

export function createWishlistItem(
  token: string,
  householdId: string,
  input: WishlistItemCreateInput,
): Promise<WishlistItem> {
  return apiFetch<WishlistItem>("/v1/wishlist", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateWishlistItem(
  token: string,
  householdId: string,
  itemId: string,
  input: WishlistItemUpdateInput,
): Promise<WishlistItem> {
  return apiFetch<WishlistItem>(`/v1/wishlist/${itemId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteWishlistItem(
  token: string,
  householdId: string,
  itemId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/wishlist/${itemId}`, { token, householdId, method: "DELETE" });
}

export function startWishlistSavingsPlan(
  token: string,
  householdId: string,
  itemId: string,
  input: WishlistSavingsPlanCreateInput,
): Promise<WishlistSavingsPlanResult> {
  return apiFetch<WishlistSavingsPlanResult>(`/v1/wishlist/${itemId}/start-savings-plan`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listAssetsAndDebts(token: string, householdId: string): Promise<AssetDebt[]> {
  return apiFetch<AssetDebt[]>("/v1/assets", { token, householdId });
}

export function createAssetOrDebt(
  token: string,
  householdId: string,
  input: AssetDebtCreateInput,
): Promise<AssetDebt> {
  return apiFetch<AssetDebt>("/v1/assets", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateAssetOrDebt(
  token: string,
  householdId: string,
  itemId: string,
  input: AssetDebtUpdateInput,
): Promise<AssetDebt> {
  return apiFetch<AssetDebt>(`/v1/assets/${itemId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

/**
 * Bewertungshistorie einer Vermoegens-/Schuldenposition, absteigend nach
 * Stichtag. Das Backend schreibt bei jedem Anlegen und bei jeder Wertaenderung
 * einen Punkt fort (siehe _record_asset_valuation in routes/goals.py).
 */
export function listAssetValuations(
  token: string,
  householdId: string,
  itemId: string,
): Promise<AssetValuation[]> {
  return apiFetch<AssetValuation[]>(`/v1/assets/${itemId}/valuations`, { token, householdId });
}

export function deleteAssetOrDebt(
  token: string,
  householdId: string,
  itemId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/assets/${itemId}`, { token, householdId, method: "DELETE" });
}

export function getNetWorthReport(
  token: string,
  householdId: string,
): Promise<NetWorthReportResponse> {
  return apiFetch<NetWorthReportResponse>("/v1/reports/net-worth", { token, householdId });
}

// ---------------------------------------------------------------------------
// Phase 8: Lebensmittelplanung (Essensplan, Einkaufsliste, Wochenbudget)
// ---------------------------------------------------------------------------

export function listMealWeeks(token: string, householdId: string): Promise<MealWeek[]> {
  return apiFetch<MealWeek[]>("/v1/meal-weeks", { token, householdId });
}

export function createMealWeek(
  token: string,
  householdId: string,
  input: MealWeekCreateInput,
): Promise<MealWeek> {
  return apiFetch<MealWeek>("/v1/meal-weeks", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateMealWeek(
  token: string,
  householdId: string,
  mealWeekId: string,
  input: MealWeekUpdateInput,
): Promise<MealWeek> {
  return apiFetch<MealWeek>(`/v1/meal-weeks/${mealWeekId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteMealWeek(
  token: string,
  householdId: string,
  mealWeekId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/meal-weeks/${mealWeekId}`, { token, householdId, method: "DELETE" });
}

export function getMealWeekSummary(
  token: string,
  householdId: string,
  mealWeekId: string,
): Promise<MealWeekSummary> {
  return apiFetch<MealWeekSummary>(`/v1/meal-weeks/${mealWeekId}/summary`, {
    token,
    householdId,
  });
}

export function listDishes(token: string, householdId: string): Promise<Dish[]> {
  return apiFetch<Dish[]>("/v1/dishes", { token, householdId });
}

export function seedDefaultDishes(token: string, householdId: string): Promise<{ seeded: number }> {
  return apiFetch<{ seeded: number }>("/v1/dishes/seed-defaults", {
    token,
    householdId,
    method: "POST",
  });
}

export function createDish(
  token: string,
  householdId: string,
  input: DishCreateInput,
): Promise<Dish> {
  return apiFetch<Dish>("/v1/dishes", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateDish(
  token: string,
  householdId: string,
  dishId: string,
  input: DishUpdateInput,
): Promise<Dish> {
  return apiFetch<Dish>(`/v1/dishes/${dishId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteDish(token: string, householdId: string, dishId: string): Promise<void> {
  return apiFetch<void>(`/v1/dishes/${dishId}`, { token, householdId, method: "DELETE" });
}

export function listDishIngredients(
  token: string,
  householdId: string,
  dishId: string,
): Promise<DishIngredient[]> {
  return apiFetch<DishIngredient[]>(`/v1/dishes/${dishId}/ingredients`, { token, householdId });
}

export function createDishIngredient(
  token: string,
  householdId: string,
  input: DishIngredientCreateInput,
): Promise<DishIngredient> {
  return apiFetch<DishIngredient>("/v1/dish-ingredients", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateDishIngredient(
  token: string,
  householdId: string,
  ingredientId: string,
  input: DishIngredientUpdateInput,
): Promise<DishIngredient> {
  return apiFetch<DishIngredient>(`/v1/dish-ingredients/${ingredientId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteDishIngredient(
  token: string,
  householdId: string,
  ingredientId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/dish-ingredients/${ingredientId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function listIngredientPrices(
  token: string,
  householdId: string,
): Promise<IngredientPrice[]> {
  return apiFetch<IngredientPrice[]>("/v1/ingredient-prices", { token, householdId });
}

export function createIngredientPrice(
  token: string,
  householdId: string,
  input: IngredientPriceCreateInput,
): Promise<IngredientPrice> {
  return apiFetch<IngredientPrice>("/v1/ingredient-prices", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateIngredientPrice(
  token: string,
  householdId: string,
  priceId: string,
  input: IngredientPriceUpdateInput,
): Promise<IngredientPrice> {
  return apiFetch<IngredientPrice>(`/v1/ingredient-prices/${priceId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteIngredientPrice(
  token: string,
  householdId: string,
  priceId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/ingredient-prices/${priceId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// --- Preislücken und zugeordnete Schreibweisen -----------------------------

/**
 * Zutaten mit Menge, aber ohne verwertbaren Preis — nach Zutat gruppiert und
 * nach Hebel sortiert. Der Arbeitsvorrat zur unvollständigen Berechnung.
 */
export function listPriceGaps(token: string, householdId: string): Promise<PriceGap[]> {
  return apiFetch<PriceGap[]>("/v1/dishes/price-gaps", { token, householdId });
}

export function listIngredientAliases(
  token: string,
  householdId: string,
): Promise<IngredientAlias[]> {
  return apiFetch<IngredientAlias[]>("/v1/ingredient-aliases", { token, householdId });
}

export function createIngredientAlias(
  token: string,
  householdId: string,
  input: IngredientAliasCreateInput,
): Promise<IngredientAlias> {
  return apiFetch<IngredientAlias>("/v1/ingredient-aliases", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteIngredientAlias(
  token: string,
  householdId: string,
  aliasId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/ingredient-aliases/${aliasId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// --- Umrechnung von Haushaltseinheiten -------------------------------------
// Der Server schlaegt die Zahl aus einer nachgeschlagenen Werteliste vor;
// gespeichert wird nur, was hier ankommt - also was jemand bestaetigt hat.

export function listUnitConversions(token: string, householdId: string): Promise<UnitConversion[]> {
  return apiFetch<UnitConversion[]>("/v1/ingredient-unit-conversions", { token, householdId });
}

export function createUnitConversion(
  token: string,
  householdId: string,
  input: UnitConversionCreateInput,
): Promise<UnitConversion> {
  return apiFetch<UnitConversion>("/v1/ingredient-unit-conversions", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createUnitConversionsBulk(
  token: string,
  householdId: string,
  items: UnitConversionCreateInput[],
): Promise<UnitConversion[]> {
  return apiFetch<UnitConversion[]>("/v1/ingredient-unit-conversions/bulk", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

export function deleteUnitConversion(
  token: string,
  householdId: string,
  conversionId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/ingredient-unit-conversions/${conversionId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// --- Preisherkunft: Läden, Sichtungen, Excel-Austausch ---------------------

export function listGroceryStores(
  token: string,
  householdId: string,
  includeArchived = false,
): Promise<GroceryStore[]> {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiFetch<GroceryStore[]>(`/v1/grocery-stores${query}`, { token, householdId });
}

export function createGroceryStore(
  token: string,
  householdId: string,
  input: GroceryStoreCreateInput,
): Promise<GroceryStore> {
  return apiFetch<GroceryStore>("/v1/grocery-stores", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateGroceryStore(
  token: string,
  householdId: string,
  storeId: string,
  input: GroceryStoreUpdateInput,
): Promise<GroceryStore> {
  return apiFetch<GroceryStore>(`/v1/grocery-stores/${storeId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteGroceryStore(
  token: string,
  householdId: string,
  storeId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/grocery-stores/${storeId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

/**
 * Alles zu einer Zutat: woher die geltende Zahl kommt, was die Läden aktuell
 * verlangen, und der vollständige Verlauf. Der Schlüssel ist der
 * kleingeschriebene Zutatenname — so ordnet auch der Server zu.
 */
export function getIngredientPriceDetail(
  token: string,
  householdId: string,
  ingredientKey: string,
): Promise<IngredientPriceDetail> {
  return apiFetch<IngredientPriceDetail>(
    `/v1/ingredient-prices/${encodeURIComponent(ingredientKey)}/detail`,
    { token, householdId },
  );
}

export function createPriceObservation(
  token: string,
  householdId: string,
  input: PriceObservationInput,
): Promise<PriceObservationBulkResult> {
  return apiFetch<PriceObservationBulkResult>("/v1/price-observations", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

// Der Endpunkt nimmt 2.000 Zeilen je Anfrage (ObservationBulkCreate). Ein
// Prospektauszug über mehrere Händler ist schnell größer, deshalb wird hier
// gestückelt statt den Nutzer die Datei teilen zu lassen.
//
// Bewusst NACHEINANDER, nicht parallel: jede Übernahme berechnet am Ende die
// betroffenen Bewertungspreise neu. Gleichzeitige Läufe würden auf denselben
// Zeilen von ingredient_prices arbeiten.
const OBSERVATION_CHUNK_SIZE = 1000;

/** Übernimmt die in der Vorschau angehakten Zeilen. */
export async function createPriceObservationsBulk(
  token: string,
  householdId: string,
  observations: PriceObservationInput[],
  sourceRef?: string,
): Promise<PriceObservationBulkResult> {
  const total: PriceObservationBulkResult = {
    inserted: 0,
    skipped_duplicates: 0,
    rejected: [],
    created_price_rows: 0,
    updated_valuations: 0,
    blocked_by_manual: 0,
  };

  for (let offset = 0; offset < observations.length; offset += OBSERVATION_CHUNK_SIZE) {
    const chunk = observations.slice(offset, offset + OBSERVATION_CHUNK_SIZE);
    const result = await apiFetch<PriceObservationBulkResult>("/v1/price-observations/bulk", {
      token,
      householdId,
      method: "POST",
      body: JSON.stringify({ observations: chunk, source: "excel", source_ref: sourceRef ?? null }),
    });
    total.inserted += result.inserted;
    total.skipped_duplicates += result.skipped_duplicates;
    total.created_price_rows += result.created_price_rows;
    total.updated_valuations += result.updated_valuations;
    // Nicht addieren: der Server zählt haushaltsweit, nicht je Stück. Der
    // letzte Lauf kennt den vollständigen Stand.
    total.blocked_by_manual = result.blocked_by_manual;
    total.rejected.push(...result.rejected);
  }

  return total;
}

export function deletePriceObservation(
  token: string,
  householdId: string,
  observationId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/price-observations/${observationId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

/** Steuert, wie der Bewertungspreis einer Zutat zustande kommt. */
export function updateIngredientValuation(
  token: string,
  householdId: string,
  ingredientKey: string,
  input: ValuationUpdateInput,
): Promise<ValuationState> {
  return apiFetch<ValuationState>(
    `/v1/ingredient-prices/${encodeURIComponent(ingredientKey)}/valuation`,
    { token, householdId, method: "PATCH", body: JSON.stringify(input) },
  );
}

export function getGroceryPriceSettings(
  token: string,
  householdId: string,
): Promise<GroceryPriceSettings> {
  return apiFetch<GroceryPriceSettings>("/v1/grocery-price-settings", { token, householdId });
}

export function putGroceryPriceSettings(
  token: string,
  householdId: string,
  input: GroceryPriceSettings,
): Promise<GroceryPriceSettings> {
  return apiFetch<GroceryPriceSettings>("/v1/grocery-price-settings", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

/**
 * Lädt die Vorlage herunter — mit den bekannten Zutaten bereits als Zeilen.
 *
 * Bewusst nicht über apiFetch: das erwartet JSON, hier kommt eine Binärdatei.
 * Der Download wird direkt ausgelöst, statt das Blob zurückzugeben — sonst
 * müsste jeder Aufrufer dasselbe Objekt-URL-Aufräumen wiederholen.
 */
export async function downloadPriceSheetTemplate(
  token: string,
  householdId: string,
): Promise<void> {
  const response = await fetch(`${API_BASE}/v1/price-sheet/template`, {
    headers: { Authorization: `Bearer ${token}`, "X-Household-Id": householdId },
  });
  if (!response.ok) {
    const { message, detail } = await buildHttpError(response);
    throw new ApiError(message, response.status, false, detail);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `Preisvorlage-${new Date().toISOString().slice(0, 10)}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Liest eine ausgefüllte Vorlage und legt das Ergebnis vor. Speichert nichts. */
export function previewPriceSheet(
  token: string,
  householdId: string,
  file: File,
): Promise<PriceSheetPreview> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<PriceSheetPreview>("/v1/price-sheet/preview", {
    token,
    householdId,
    method: "POST",
    body: form,
  });
}

/**
 * Laesst das lokale Modell Zutaten vorschlagen. Schreibt NICHTS - erst
 * createDishIngredientsBulk uebernimmt die Auswahl des Nutzers.
 */
export function suggestDishIngredients(
  token: string,
  householdId: string,
  dishId: string,
): Promise<IngredientSuggestionResult> {
  return apiFetch<IngredientSuggestionResult>(`/v1/dishes/${dishId}/ingredient-suggestions`, {
    token,
    householdId,
    method: "POST",
  });
}

export function createDishIngredientsBulk(
  token: string,
  householdId: string,
  input: DishIngredientBulkInput,
): Promise<DishIngredient[]> {
  return apiFetch<DishIngredient[]>("/v1/dish-ingredients/bulk", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Wochenplan-Vorschlag aus dem Gerichte-Katalog. Schreibt ebenfalls nichts. */
export function suggestWeekPlan(
  token: string,
  householdId: string,
  mealWeekId: string,
  input: WeekPlanSuggestionInput,
): Promise<WeekPlanSuggestionResult> {
  return apiFetch<WeekPlanSuggestionResult>(`/v1/meal-weeks/${mealWeekId}/plan-suggestions`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createMealsBulk(
  token: string,
  householdId: string,
  input: MealBulkInput,
): Promise<Meal[]> {
  return apiFetch<Meal[]>("/v1/meals/bulk", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Uebertraegt die Zutaten aller Mahlzeiten der Woche in die Einkaufsliste. */
export function fillShoppingListFromMeals(
  token: string,
  householdId: string,
  shoppingListId: string,
): Promise<ShoppingListFillResult> {
  return apiFetch<ShoppingListFillResult>(`/v1/shopping-lists/${shoppingListId}/fill-from-meals`, {
    token,
    householdId,
    method: "POST",
  });
}

export function listMeals(token: string, householdId: string, mealWeekId: string): Promise<Meal[]> {
  return apiFetch<Meal[]>(`/v1/meals?meal_week_id=${mealWeekId}`, { token, householdId });
}

export function createMeal(
  token: string,
  householdId: string,
  input: MealCreateInput,
): Promise<Meal> {
  return apiFetch<Meal>("/v1/meals", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateMeal(
  token: string,
  householdId: string,
  mealId: string,
  input: MealUpdateInput,
): Promise<Meal> {
  return apiFetch<Meal>(`/v1/meals/${mealId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteMeal(token: string, householdId: string, mealId: string): Promise<void> {
  return apiFetch<void>(`/v1/meals/${mealId}`, { token, householdId, method: "DELETE" });
}

export function listShoppingLists(
  token: string,
  householdId: string,
  mealWeekId: string,
): Promise<ShoppingList[]> {
  return apiFetch<ShoppingList[]>(`/v1/shopping-lists?meal_week_id=${mealWeekId}`, {
    token,
    householdId,
  });
}

export function createShoppingList(
  token: string,
  householdId: string,
  input: ShoppingListCreateInput,
): Promise<ShoppingList> {
  return apiFetch<ShoppingList>("/v1/shopping-lists", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateShoppingList(
  token: string,
  householdId: string,
  shoppingListId: string,
  input: ShoppingListUpdateInput,
): Promise<ShoppingList> {
  return apiFetch<ShoppingList>(`/v1/shopping-lists/${shoppingListId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteShoppingList(
  token: string,
  householdId: string,
  shoppingListId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/shopping-lists/${shoppingListId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function listShoppingItems(
  token: string,
  householdId: string,
  shoppingListId: string,
): Promise<ShoppingItem[]> {
  return apiFetch<ShoppingItem[]>(`/v1/shopping-items?shopping_list_id=${shoppingListId}`, {
    token,
    householdId,
  });
}

export function createShoppingItem(
  token: string,
  householdId: string,
  input: ShoppingItemCreateInput,
): Promise<ShoppingItem> {
  return apiFetch<ShoppingItem>("/v1/shopping-items", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateShoppingItem(
  token: string,
  householdId: string,
  itemId: string,
  input: ShoppingItemUpdateInput,
): Promise<ShoppingItem> {
  return apiFetch<ShoppingItem>(`/v1/shopping-items/${itemId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deleteShoppingItem(
  token: string,
  householdId: string,
  itemId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/shopping-items/${itemId}`, { token, householdId, method: "DELETE" });
}

export function linkPurchase(
  token: string,
  householdId: string,
  shoppingListId: string,
  input: PurchaseLinkCreateInput,
): Promise<PurchaseLink> {
  return apiFetch<PurchaseLink>(`/v1/shopping-lists/${shoppingListId}/purchase-links`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function unlinkPurchase(
  token: string,
  householdId: string,
  purchaseLinkId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/purchase-links/${purchaseLinkId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md)
//
// Alle Endpunkte verlangen X-Household-Id (setzt apiFetch). Einzelaenderungen
// an Gruppe/Kategorie sind optimistisch versioniert: `If-Match` traegt die
// zuletzt gelesene `version` der Entitaet - fehlt der Header, antwortet der
// Server mit 428, ist er veraltet mit 409. Baumweite Operationen (Sortierung,
// Standardpaket) verwenden stattdessen die `taxonomy_revision` des Katalogs.
// ---------------------------------------------------------------------------

/** ETag-Form des Versionszaehlers: der Server erwartet einen quoted-string. */
function ifMatch(version: number): Record<string, string> {
  return { "If-Match": `"${version}"` };
}

export function getCategoryCatalog(
  token: string,
  householdId: string,
  options: { includeArchived?: boolean; includeUsage?: boolean } = {},
): Promise<CategoryCatalog> {
  const search = new URLSearchParams();
  if (options.includeArchived) {
    search.set("include_archived", "true");
  }
  if (options.includeUsage) {
    search.set("include_usage", "true");
  }
  const query = search.toString();
  return apiFetch<CategoryCatalog>(`/v1/categories/catalog${query ? `?${query}` : ""}`, {
    token,
    householdId,
  });
}

export function createCategoryGroup(
  token: string,
  householdId: string,
  input: CategoryGroupCreateInput,
): Promise<CatalogGroup> {
  return apiFetch<CatalogGroup>("/v1/categories/groups", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateCategoryGroup(
  token: string,
  householdId: string,
  groupId: string,
  input: CategoryGroupUpdateInput,
  version: number,
): Promise<CatalogGroup> {
  return apiFetch<CatalogGroup>(`/v1/categories/groups/${groupId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function reorderCategoryGroups(
  token: string,
  householdId: string,
  input: Omit<ReorderRequestInput, "group_id">,
): Promise<CategoryCatalog> {
  return apiFetch<CategoryCatalog>("/v1/categories/groups/reorder", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function previewArchiveCategoryGroup(
  token: string,
  householdId: string,
  groupId: string,
): Promise<GroupArchivePreview> {
  return apiFetch<GroupArchivePreview>(`/v1/categories/groups/${groupId}/archive-preview`, {
    token,
    householdId,
    method: "POST",
  });
}

export function archiveCategoryGroup(
  token: string,
  householdId: string,
  groupId: string,
  input: GroupArchiveApplyInput,
): Promise<CatalogGroup> {
  return apiFetch<CatalogGroup>(`/v1/categories/groups/${groupId}/archive`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function restoreCategoryGroup(
  token: string,
  householdId: string,
  groupId: string,
): Promise<CatalogGroup> {
  return apiFetch<CatalogGroup>(`/v1/categories/groups/${groupId}/restore`, {
    token,
    householdId,
    method: "POST",
  });
}

export function createCategory(
  token: string,
  householdId: string,
  input: CategoryCreateInput,
): Promise<CatalogCategory> {
  return apiFetch<CatalogCategory>("/v1/categories", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateCategory(
  token: string,
  householdId: string,
  categoryId: string,
  input: CategoryUpdateInput,
  version: number,
): Promise<CatalogCategory> {
  return apiFetch<CatalogCategory>(`/v1/categories/${categoryId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function reorderCategories(
  token: string,
  householdId: string,
  input: ReorderRequestInput,
): Promise<CategoryCatalog> {
  return apiFetch<CategoryCatalog>("/v1/categories/reorder", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function previewMoveCategory(
  token: string,
  householdId: string,
  categoryId: string,
  targetGroupId: string,
): Promise<MovePreview> {
  return apiFetch<MovePreview>(`/v1/categories/${categoryId}/move-preview`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ target_group_id: targetGroupId }),
  });
}

export function moveCategory(
  token: string,
  householdId: string,
  categoryId: string,
  input: MoveApplyInput,
): Promise<CatalogCategory> {
  return apiFetch<CatalogCategory>(`/v1/categories/${categoryId}/move`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function previewArchiveCategory(
  token: string,
  householdId: string,
  categoryId: string,
): Promise<ArchivePreview> {
  return apiFetch<ArchivePreview>(`/v1/categories/${categoryId}/archive-preview`, {
    token,
    householdId,
    method: "POST",
  });
}

export function archiveCategory(
  token: string,
  householdId: string,
  categoryId: string,
  input: ArchiveApplyInput,
): Promise<CatalogCategory> {
  return apiFetch<CatalogCategory>(`/v1/categories/${categoryId}/archive`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function restoreCategory(
  token: string,
  householdId: string,
  categoryId: string,
  input: RestoreRequestInput = {},
): Promise<CatalogCategory> {
  return apiFetch<CatalogCategory>(`/v1/categories/${categoryId}/restore`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function previewMergeCategories(
  token: string,
  householdId: string,
  input: MergeRequestInput,
): Promise<MergePreview> {
  return apiFetch<MergePreview>("/v1/categories/merge-preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Nur fuer Besitzerin bzw. Besitzer - der Merge ist irreversibel. */
export function mergeCategories(
  token: string,
  householdId: string,
  input: MergeApplyInput,
): Promise<MergeResult> {
  return apiFetch<MergeResult>("/v1/categories/merge", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getStandardPackageStatus(
  token: string,
  householdId: string,
): Promise<StandardPackageStatus> {
  return apiFetch<StandardPackageStatus>("/v1/categories/standard-package", {
    token,
    householdId,
  });
}

export function previewStandardPackage(
  token: string,
  householdId: string,
): Promise<StandardPackagePreview> {
  return apiFetch<StandardPackagePreview>("/v1/categories/standard-package/preview", {
    token,
    householdId,
    method: "POST",
  });
}

export function applyStandardPackage(
  token: string,
  householdId: string,
  input: StandardPackageApplyInput,
): Promise<StandardPackageResult> {
  return apiFetch<StandardPackageResult>("/v1/categories/standard-package/apply", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * KI-Kategorievorschlaege aus dem Buchungsbestand. Der Aufruf laesst ein
 * lokales Modell mehrere Buchungscluster beurteilen und dauert entsprechend
 * laenger als ein normaler Read - der aufrufende Dialog muss das als eigenen
 * Ladezustand zeigen.
 */
export function previewCategorySuggestions(
  token: string,
  householdId: string,
): Promise<CategorySuggestionPreview> {
  return apiFetch<CategorySuggestionPreview>("/v1/categories/suggestions/preview", {
    token,
    householdId,
    method: "POST",
  });
}

export function applyCategorySuggestions(
  token: string,
  householdId: string,
  input: CategorySuggestionApplyInput,
): Promise<CategorySuggestionResult> {
  return apiFetch<CategorySuggestionResult>("/v1/categories/suggestions/apply", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Kategorie genau EINES Splits aendern. Buchungen mit mehreren Splits lehnt
 * der Server mit 409 `transaction_has_multiple_splits` ab, damit eine
 * Gesamtkategorie keine vorhandene Aufteilung still ueberschreibt.
 */
export function setTransactionCategory(
  token: string,
  householdId: string,
  transactionId: string,
  input: TransactionCategoryUpdateInput,
): Promise<TransactionDetail> {
  return apiFetch<TransactionDetail>(`/v1/transactions/${transactionId}/category`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function previewBulkCategory(
  token: string,
  householdId: string,
  input: BulkCategoryInput,
): Promise<BulkCategoryResult> {
  return apiFetch<BulkCategoryResult>("/v1/transactions/bulk-category/preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function applyBulkCategory(
  token: string,
  householdId: string,
  input: BulkCategoryInput,
): Promise<BulkCategoryResult> {
  return apiFetch<BulkCategoryResult>("/v1/transactions/bulk-category/apply", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P1)
// ---------------------------------------------------------------------------

export interface ProjectListParams {
  status?: string;
  projectType?: string;
  search?: string;
  includeArchived?: boolean;
}

function projectListQuery(params: ProjectListParams): string {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.projectType) query.set("project_type", params.projectType);
  if (params.search) query.set("search", params.search);
  if (params.includeArchived) query.set("include_archived", "true");
  const suffix = query.toString();
  return suffix ? `?${suffix}` : "";
}

export function listProjects(
  token: string,
  householdId: string,
  params: ProjectListParams = {},
): Promise<ProjectListItem[]> {
  return apiFetch<ProjectListItem[]>(`/v1/projects${projectListQuery(params)}`, {
    token,
    householdId,
  });
}

export function getProject(
  token: string,
  householdId: string,
  projectId: string,
): Promise<MajorProject> {
  return apiFetch<MajorProject>(`/v1/projects/${projectId}`, { token, householdId });
}

export function createProject(
  token: string,
  householdId: string,
  input: MajorProjectCreateInput,
): Promise<MajorProject> {
  return apiFetch<MajorProject>("/v1/projects", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateProject(
  token: string,
  householdId: string,
  projectId: string,
  input: MajorProjectUpdateInput,
  version: number,
): Promise<MajorProject> {
  return apiFetch<MajorProject>(`/v1/projects/${projectId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function transitionProject(
  token: string,
  householdId: string,
  projectId: string,
  input: ProjectTransitionRequest,
  version: number,
): Promise<MajorProject> {
  return apiFetch<MajorProject>(`/v1/projects/${projectId}/transition`, {
    token,
    householdId,
    method: "POST",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function getProjectDeleteImpact(
  token: string,
  householdId: string,
  projectId: string,
): Promise<ProjectDeleteImpact> {
  return apiFetch<ProjectDeleteImpact>(`/v1/projects/${projectId}/delete-impact`, {
    token,
    householdId,
  });
}

export function deleteProject(
  token: string,
  householdId: string,
  projectId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}`, { token, householdId, method: "DELETE" });
}

export function archiveProject(
  token: string,
  householdId: string,
  projectId: string,
): Promise<MajorProject> {
  return apiFetch<MajorProject>(`/v1/projects/${projectId}/archive`, {
    token,
    householdId,
    method: "POST",
  });
}

export function listProjectScenarios(
  token: string,
  householdId: string,
  projectId: string,
): Promise<ProjectScenario[]> {
  return apiFetch<ProjectScenario[]>(`/v1/projects/${projectId}/scenarios`, {
    token,
    householdId,
  });
}

export function createProjectScenario(
  token: string,
  householdId: string,
  projectId: string,
  input: ProjectScenarioCreateInput,
): Promise<ProjectScenario> {
  return apiFetch<ProjectScenario>(`/v1/projects/${projectId}/scenarios`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateProjectScenario(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: ProjectScenarioUpdateInput,
  version: number,
): Promise<ProjectScenario> {
  return apiFetch<ProjectScenario>(`/v1/projects/${projectId}/scenarios/${scenarioId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function deleteProjectScenario(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}/scenarios/${scenarioId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function cloneProjectScenario(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: ScenarioCloneRequest,
): Promise<ProjectScenario> {
  return apiFetch<ProjectScenario>(`/v1/projects/${projectId}/scenarios/${scenarioId}/clone`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function selectProjectScenario(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<ProjectScenario> {
  return apiFetch<ProjectScenario>(`/v1/projects/${projectId}/scenarios/${scenarioId}/select`, {
    token,
    householdId,
    method: "POST",
  });
}

export function compareProjectScenarios(
  token: string,
  householdId: string,
  projectId: string,
  scenarioIds: string[],
): Promise<ScenarioComparison> {
  const query = scenarioIds.map((id) => `scenario_id=${encodeURIComponent(id)}`).join("&");
  return apiFetch<ScenarioComparison>(`/v1/projects/${projectId}/comparison?${query}`, {
    token,
    householdId,
  });
}

export function calculateProjectScenario(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<ScenarioCalculation> {
  return apiFetch<ScenarioCalculation>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/calculate`,
    { token, householdId, method: "POST" },
  );
}

export function listCostSections(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<CostSection[]> {
  return apiFetch<CostSection[]>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/cost-sections`,
    { token, householdId },
  );
}

export function createCostSection(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: CostSectionCreateInput,
): Promise<CostSection> {
  return apiFetch<CostSection>(`/v1/projects/${projectId}/scenarios/${scenarioId}/cost-sections`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateCostSection(
  token: string,
  householdId: string,
  projectId: string,
  sectionId: string,
  input: CostSectionUpdateInput,
  version: number,
): Promise<CostSection> {
  return apiFetch<CostSection>(`/v1/projects/${projectId}/cost-sections/${sectionId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function deleteCostSection(
  token: string,
  householdId: string,
  projectId: string,
  sectionId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}/cost-sections/${sectionId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function listCostItems(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<CostItem[]> {
  return apiFetch<CostItem[]>(`/v1/projects/${projectId}/scenarios/${scenarioId}/cost-items`, {
    token,
    householdId,
  });
}

export function createCostItem(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: CostItemCreateInput,
): Promise<CostItem> {
  return apiFetch<CostItem>(`/v1/projects/${projectId}/scenarios/${scenarioId}/cost-items`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateCostItem(
  token: string,
  householdId: string,
  projectId: string,
  itemId: string,
  input: CostItemUpdateInput,
  version: number,
): Promise<CostItem> {
  return apiFetch<CostItem>(`/v1/projects/${projectId}/cost-items/${itemId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function deleteCostItem(
  token: string,
  householdId: string,
  projectId: string,
  itemId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}/cost-items/${itemId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function replaceCostSchedule(
  token: string,
  householdId: string,
  projectId: string,
  itemId: string,
  input: CostScheduleReplaceInput,
): Promise<CostScheduleEntry[]> {
  return apiFetch<CostScheduleEntry[]>(`/v1/projects/${projectId}/cost-items/${itemId}/schedule`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function previewCostAiSuggestions(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: CostAiSuggestionRequest,
): Promise<CostAiSuggestionPreview> {
  return apiFetch<CostAiSuggestionPreview>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/cost-ai-suggestions/preview`,
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function applyCostAiSuggestions(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: CostAiSuggestionApplyInput,
): Promise<CostAiSuggestionResult> {
  return apiFetch<CostAiSuggestionResult>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/cost-ai-suggestions/apply`,
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function convertWishlistItemToProject(
  token: string,
  householdId: string,
  wishlistItemId: string,
): Promise<WishlistConvertResponse> {
  return apiFetch<WishlistConvertResponse>(`/v1/wishlist/${wishlistItemId}/convert-to-project`, {
    token,
    householdId,
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P2: Finanzierung, Darlehen, Annahmen, Abschluss)
// ---------------------------------------------------------------------------

export function listFinancingSources(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<FinancingSource[]> {
  return apiFetch<FinancingSource[]>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/financing-sources`,
    { token, householdId },
  );
}

export function createFinancingSource(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: FinancingSourceCreateInput,
): Promise<FinancingSource> {
  return apiFetch<FinancingSource>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/financing-sources`,
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function createFinancingConfiguration(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: FinancingConfigurationCreateInput,
): Promise<FinancingConfigurationResponse> {
  return apiFetch<FinancingConfigurationResponse>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/financing-configurations`,
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function previewFinancingDraws(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: CostDrawPreviewInput,
): Promise<CostDrawPreview> {
  return apiFetch<CostDrawPreview>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/financing-draw-preview`,
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function updateFinancingConfiguration(
  token: string,
  householdId: string,
  projectId: string,
  sourceId: string,
  input: FinancingConfigurationUpdateInput,
  version: number,
): Promise<FinancingConfigurationResponse> {
  return apiFetch<FinancingConfigurationResponse>(
    `/v1/projects/${projectId}/financing-sources/${sourceId}/configuration`,
    {
      token,
      householdId,
      method: "PUT",
      headers: ifMatch(version),
      body: JSON.stringify(input),
    },
  );
}

export function updateFinancingSource(
  token: string,
  householdId: string,
  projectId: string,
  sourceId: string,
  input: FinancingSourceUpdateInput,
  version: number,
): Promise<FinancingSource> {
  return apiFetch<FinancingSource>(`/v1/projects/${projectId}/financing-sources/${sourceId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function deleteFinancingSource(
  token: string,
  householdId: string,
  projectId: string,
  sourceId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}/financing-sources/${sourceId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function getEquityAvailability(
  token: string,
  householdId: string,
  projectId: string,
): Promise<EquityAvailability> {
  return apiFetch<EquityAvailability>(`/v1/projects/${projectId}/equity-availability`, {
    token,
    householdId,
  });
}

export function listDebtPlans(
  token: string,
  householdId: string,
  projectFinancingSourceId?: string,
): Promise<DebtPlan[]> {
  const suffix = projectFinancingSourceId
    ? `?project_financing_source_id=${encodeURIComponent(projectFinancingSourceId)}`
    : "";
  return apiFetch<DebtPlan[]>(`/v1/debt-plans${suffix}`, { token, householdId });
}

export function createDebtPlan(
  token: string,
  householdId: string,
  input: DebtPlanCreateInput,
): Promise<DebtPlan> {
  return apiFetch<DebtPlan>("/v1/debt-plans", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getDebtPlan(
  token: string,
  householdId: string,
  debtPlanId: string,
): Promise<DebtPlan> {
  return apiFetch<DebtPlan>(`/v1/debt-plans/${debtPlanId}`, { token, householdId });
}

export function updateDebtPlan(
  token: string,
  householdId: string,
  debtPlanId: string,
  input: DebtPlanUpdateInput,
  version: number,
): Promise<DebtPlan> {
  return apiFetch<DebtPlan>(`/v1/debt-plans/${debtPlanId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function deleteDebtPlan(
  token: string,
  householdId: string,
  debtPlanId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/debt-plans/${debtPlanId}`, { token, householdId, method: "DELETE" });
}

export function replaceDebtRatePeriods(
  token: string,
  householdId: string,
  debtPlanId: string,
  periods: RatePeriodInput[],
): Promise<RatePeriod[]> {
  return apiFetch<RatePeriod[]>(`/v1/debt-plans/${debtPlanId}/rate-periods`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify({ periods }),
  });
}

export function replaceDebtDraws(
  token: string,
  householdId: string,
  debtPlanId: string,
  draws: DrawInput[],
): Promise<Draw[]> {
  return apiFetch<Draw[]>(`/v1/debt-plans/${debtPlanId}/draws`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify({ draws }),
  });
}

export function replaceDebtFees(
  token: string,
  householdId: string,
  debtPlanId: string,
  fees: FeeInput[],
): Promise<Fee[]> {
  return apiFetch<Fee[]>(`/v1/debt-plans/${debtPlanId}/fees`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify({ fees }),
  });
}

export function replaceDebtSpecialRepayments(
  token: string,
  householdId: string,
  debtPlanId: string,
  specialRepayments: SpecialRepaymentInput[],
): Promise<SpecialRepayment[]> {
  return apiFetch<SpecialRepayment[]>(`/v1/debt-plans/${debtPlanId}/special-repayments`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify({ special_repayments: specialRepayments }),
  });
}

export function getDebtSchedule(
  token: string,
  householdId: string,
  debtPlanId: string,
  horizonMonths?: number,
): Promise<DebtSchedule> {
  const suffix = horizonMonths ? `?horizon_months=${horizonMonths}` : "";
  return apiFetch<DebtSchedule>(`/v1/debt-plans/${debtPlanId}/schedule${suffix}`, {
    token,
    householdId,
  });
}

export function getCashflowAssumptions(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<CashflowAssumptions | null> {
  return apiFetch<CashflowAssumptions | null>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/assumptions`,
    { token, householdId },
  );
}

export function suggestCashflowAssumptions(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<CashflowSuggestion> {
  return apiFetch<CashflowSuggestion>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/assumptions/suggest`,
    { token, householdId, method: "POST" },
  );
}

export function saveCashflowAssumptions(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: CashflowAssumptionsInput,
): Promise<CashflowAssumptions> {
  return apiFetch<CashflowAssumptions>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/assumptions`,
    { token, householdId, method: "PUT", body: JSON.stringify(input) },
  );
}

export function listRecurringImpacts(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<RecurringImpact[]> {
  return apiFetch<RecurringImpact[]>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/recurring-impacts`,
    { token, householdId },
  );
}

export function createRecurringImpact(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: RecurringImpactCreateInput,
): Promise<RecurringImpact> {
  return apiFetch<RecurringImpact>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/recurring-impacts`,
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function updateRecurringImpact(
  token: string,
  householdId: string,
  projectId: string,
  impactId: string,
  input: RecurringImpactUpdateInput,
  version: number,
): Promise<RecurringImpact> {
  return apiFetch<RecurringImpact>(`/v1/projects/${projectId}/recurring-impacts/${impactId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: ifMatch(version),
    body: JSON.stringify(input),
  });
}

export function deleteRecurringImpact(
  token: string,
  householdId: string,
  projectId: string,
  impactId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}/recurring-impacts/${impactId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function listGoalAssumptions(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<GoalAssumption[]> {
  return apiFetch<GoalAssumption[]>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/goal-assumptions`,
    { token, householdId },
  );
}

export function saveGoalAssumption(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: GoalAssumptionInput,
): Promise<GoalAssumption> {
  return apiFetch<GoalAssumption>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/goal-assumptions`,
    { token, householdId, method: "PUT", body: JSON.stringify(input) },
  );
}

export function deleteGoalAssumption(
  token: string,
  householdId: string,
  projectId: string,
  goalAssumptionId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}/goal-assumptions/${goalAssumptionId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function getFinancialSummary(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<ScenarioFinancialSummary> {
  return apiFetch<ScenarioFinancialSummary>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/financial-summary`,
    { token, householdId },
  );
}

export function getProjectTimeline(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<ScenarioTimeline> {
  return apiFetch<ScenarioTimeline>(`/v1/projects/${projectId}/scenarios/${scenarioId}/timeline`, {
    token,
    householdId,
  });
}

export function previewProjectCompletion(
  token: string,
  householdId: string,
  projectId: string,
): Promise<CompletionPreview> {
  return apiFetch<CompletionPreview>(`/v1/projects/${projectId}/complete/preview`, {
    token,
    householdId,
    method: "POST",
  });
}

export function applyProjectCompletion(
  token: string,
  householdId: string,
  projectId: string,
): Promise<CompletionApplyResponse> {
  return apiFetch<CompletionApplyResponse>(`/v1/projects/${projectId}/complete/apply`, {
    token,
    householdId,
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P3: Dynamischer Foerderkatalog)
// ---------------------------------------------------------------------------

export function listFundingPrograms(
  token: string,
  householdId: string,
  params: {
    countryCode?: string;
    regionCode?: string;
    projectType?: string;
    includeInactive?: boolean;
  } = {},
): Promise<FundingProgramSummary[]> {
  const query = new URLSearchParams();
  query.set("country_code", params.countryCode ?? "DE");
  if (params.regionCode) query.set("region_code", params.regionCode);
  if (params.projectType) query.set("project_type", params.projectType);
  if (params.includeInactive) query.set("include_inactive", "true");
  return apiFetch<FundingProgramSummary[]>(`/v1/funding/programs?${query.toString()}`, {
    token,
    householdId,
  });
}

export function listFundingPackages(
  token: string,
  householdId: string,
): Promise<FundingPackageMeta[]> {
  return apiFetch<FundingPackageMeta[]>("/v1/funding/packages", { token, householdId });
}

export function getFundingPackage(
  token: string,
  householdId: string,
  packageId: string,
): Promise<FundingPackagePayload> {
  return apiFetch<FundingPackagePayload>(`/v1/funding/packages/${packageId}`, {
    token,
    householdId,
  });
}

export function previewFundingImport(
  token: string,
  householdId: string,
  input: FundingImportRequest,
): Promise<FundingPreviewResponse> {
  return apiFetch<FundingPreviewResponse>("/v1/funding/import/preview", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function runFundingImport(
  token: string,
  householdId: string,
  input: FundingImportRequest,
): Promise<FundingImportResult> {
  return apiFetch<FundingImportResult>("/v1/funding/import", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function exportFundingPrograms(
  token: string,
  householdId: string,
  input: FundingExportRequest,
): Promise<FundingPackagePayload> {
  return apiFetch<FundingPackagePayload>("/v1/funding/export", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deactivateFundingProgram(
  token: string,
  householdId: string,
  programId: string,
): Promise<FundingProgramDetail> {
  return apiFetch<FundingProgramDetail>(`/v1/funding/programs/${programId}/deactivate`, {
    token,
    householdId,
    method: "POST",
  });
}

export function getFundingProgram(
  token: string,
  householdId: string,
  programId: string,
): Promise<FundingProgramDetail> {
  return apiFetch<FundingProgramDetail>(`/v1/funding/programs/${programId}`, {
    token,
    householdId,
  });
}

export function updateFundingProgramTerms(
  token: string,
  householdId: string,
  programId: string,
  input: FundingProgramTermsUpdate,
): Promise<FundingProgramDetail> {
  return apiFetch<FundingProgramDetail>(`/v1/funding/programs/${programId}/terms`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function getFundingCatalogStatus(
  token: string,
  householdId: string,
): Promise<CatalogStatus> {
  return apiFetch<CatalogStatus>("/v1/funding/catalog-status", { token, householdId });
}

export function getEligibilityProfile(
  token: string,
  householdId: string,
  projectId: string,
): Promise<EligibilityProfile | null> {
  return apiFetch<EligibilityProfile | null>(`/v1/projects/${projectId}/eligibility-profile`, {
    token,
    householdId,
  });
}

export function saveEligibilityProfile(
  token: string,
  householdId: string,
  projectId: string,
  input: EligibilityProfileInput,
): Promise<EligibilityProfile> {
  return apiFetch<EligibilityProfile>(`/v1/projects/${projectId}/eligibility-profile`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function runFundingCheck(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<FundingCheckRunResponse> {
  return apiFetch<FundingCheckRunResponse>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/funding-check`,
    { token, householdId, method: "POST" },
  );
}

export function getFundingMatches(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<FundingCheckRunResponse | null> {
  return apiFetch<FundingCheckRunResponse | null>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/funding-matches`,
    { token, householdId },
  );
}

export function evaluateFundingCombinations(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  input: CombinationEvaluateRequest,
): Promise<CombinationEvaluateResponse> {
  return apiFetch<CombinationEvaluateResponse>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/funding-combinations/evaluate`,
    { token, householdId, method: "POST", body: JSON.stringify(input) },
  );
}

export function adoptFundingMatch(
  token: string,
  householdId: string,
  projectId: string,
  matchId: string,
  input: AdoptMatchRequest,
): Promise<AdoptMatchResponse> {
  return apiFetch<AdoptMatchResponse>(
    `/v1/projects/${projectId}/funding-matches/${matchId}/adopt`,
    {
      token,
      householdId,
      method: "POST",
      body: JSON.stringify(input),
    },
  );
}

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P4: Istkosten-Zuordnung, Import/Export)
// ---------------------------------------------------------------------------

export function listActuals(
  token: string,
  householdId: string,
  projectId: string,
  itemId: string,
): Promise<CostActual[]> {
  return apiFetch<CostActual[]>(`/v1/projects/${projectId}/cost-items/${itemId}/actuals`, {
    token,
    householdId,
  });
}

export function createActual(
  token: string,
  householdId: string,
  projectId: string,
  itemId: string,
  input: CostActualCreateInput,
): Promise<CostActual> {
  return apiFetch<CostActual>(`/v1/projects/${projectId}/cost-items/${itemId}/actuals`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteActual(
  token: string,
  householdId: string,
  projectId: string,
  actualId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/projects/${projectId}/cost-actuals/${actualId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

export function listTransactionCandidates(
  token: string,
  householdId: string,
  projectId: string,
  params: { search?: string; accountId?: string; dateFrom?: string; dateTo?: string } = {},
): Promise<TransactionCandidate[]> {
  const query = new URLSearchParams();
  if (params.search) query.set("search", params.search);
  if (params.accountId) query.set("account_id", params.accountId);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<TransactionCandidate[]>(
    `/v1/projects/${projectId}/transaction-candidates${suffix}`,
    { token, householdId },
  );
}

export function bulkAssignActuals(
  token: string,
  householdId: string,
  projectId: string,
  input: BulkAssignActualsRequest,
): Promise<BulkAssignActualsResult> {
  return apiFetch<BulkAssignActualsResult>(`/v1/projects/${projectId}/actuals/bulk-assign`, {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function listCostImportProfiles(
  token: string,
  householdId: string,
): Promise<CostImportMappingProfile[]> {
  return apiFetch<CostImportMappingProfile[]>("/v1/cost-import-profiles", { token, householdId });
}

export function createCostImportProfile(
  token: string,
  householdId: string,
  input: CostImportMappingProfileInput,
): Promise<CostImportMappingProfile> {
  return apiFetch<CostImportMappingProfile>("/v1/cost-import-profiles", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteCostImportProfile(
  token: string,
  householdId: string,
  profileId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/cost-import-profiles/${profileId}`, {
    token,
    householdId,
    method: "DELETE",
  });
}

interface CostImportFileParams {
  file: File;
  mapping: Record<string, string>;
  sheetName?: string | null;
  headerRow?: number;
}

function costImportFormData(params: CostImportFileParams): FormData {
  const form = new FormData();
  form.set("file", params.file);
  form.set("mapping_json", JSON.stringify(params.mapping));
  if (params.sheetName) form.set("sheet_name", params.sheetName);
  form.set("header_row", String(params.headerRow ?? 1));
  return form;
}

export function previewCostImport(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  params: CostImportFileParams,
): Promise<CostImportPreviewResponse> {
  return apiFetch<CostImportPreviewResponse>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/cost-import/preview`,
    { token, householdId, method: "POST", body: costImportFormData(params) },
  );
}

export function applyCostImport(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  params: CostImportFileParams & {
    resolutions: CostImportRowResolution[];
    requestId: string;
    previewFingerprint: string;
  },
): Promise<CostImportApplyResult> {
  const form = costImportFormData(params);
  form.set("resolutions_json", JSON.stringify(params.resolutions));
  form.set("request_id", params.requestId);
  form.set("preview_fingerprint", params.previewFingerprint);
  return apiFetch<CostImportApplyResult>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/cost-import/apply`,
    { token, householdId, method: "POST", body: form },
  );
}

/**
 * XLSX-/CSV-Export eines Szenarios. Eigener fetch statt apiFetch wie bei
 * downloadAnalyticsCsv: die Antwort ist ein Binaerstrom, kein JSON.
 */
export async function downloadScenarioXlsx(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/v1/projects/${projectId}/scenarios/${scenarioId}/export.xlsx`,
    { headers: { Authorization: `Bearer ${token}`, "X-Household-Id": householdId } },
  );
  if (!response.ok) {
    throw new ApiError(`Export fehlgeschlagen (HTTP ${response.status}).`, response.status);
  }
  return response.blob();
}

export async function downloadCostItemsCsv(
  token: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/v1/projects/${projectId}/scenarios/${scenarioId}/export/cost-items.csv`,
    { headers: { Authorization: `Bearer ${token}`, "X-Household-Id": householdId } },
  );
  if (!response.ok) {
    throw new ApiError(`Export fehlgeschlagen (HTTP ${response.status}).`, response.status);
  }
  return response.blob();
}

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (P5: Uebersicht und Berichte, Plan 11.7)
// ---------------------------------------------------------------------------

/**
 * Uebersichtskarte fuer das Dashboard: aktive Vorhaben, naechste grosse
 * Zahlung, gesicherte Luecke, wichtigste Warnung - serverseitig aus denselben
 * Funktionen aggregiert, die auch die Vorhaben-Detailseite fuer das jeweilige
 * Projekt aufruft.
 */
export function getProjectDashboardSummary(
  token: string,
  householdId: string,
): Promise<ProjectDashboardSummary> {
  return apiFetch<ProjectDashboardSummary>("/v1/projects/dashboard-summary", {
    token,
    householdId,
  });
}

/**
 * Vorhaben-Berichtsregister: Forecast/Ist/Abweichung und Finanzierungskosten
 * je aktivem Vorhaben mit ausgewaehltem Szenario - eigener Datenpfad, nicht
 * ueber den Analyse-Explorer (siehe ReportsPage.tsx).
 */
export function getProjectReportsOverview(
  token: string,
  householdId: string,
): Promise<ProjectReportRow[]> {
  return apiFetch<ProjectReportRow[]>("/v1/projects/reports-overview", { token, householdId });
}

// --- Datenbank-Explorer ----------------------------------------------------
// Generischer Blick auf alle Haushaltstabellen. Tabellen- und Spaltennamen
// prüft der Server gegen den Katalog der Datenbank; hier werden sie nur
// URL-kodiert weitergereicht.

export function listExplorerTables(token: string, householdId: string): Promise<ExplorerTableList> {
  return apiFetch<ExplorerTableList>("/v1/data-explorer/tables", { token, householdId });
}

export function readExplorerRows(
  token: string,
  householdId: string,
  table: string,
  options: {
    limit?: number;
    offset?: number;
    orderBy?: string;
    descending?: boolean;
    search?: string;
  } = {},
): Promise<ExplorerRowPage> {
  const query = new URLSearchParams();
  if (options.limit != null) query.set("limit", String(options.limit));
  if (options.offset != null) query.set("offset", String(options.offset));
  if (options.orderBy) query.set("order_by", options.orderBy);
  if (options.descending != null) query.set("descending", String(options.descending));
  if (options.search?.trim()) query.set("search", options.search.trim());
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<ExplorerRowPage>(
    `/v1/data-explorer/tables/${encodeURIComponent(table)}/rows${suffix}`,
    { token, householdId },
  );
}

/** Was ein Löschen außer den gewählten Zeilen noch mitnimmt. Ändert nichts. */
export function previewExplorerDelete(
  token: string,
  householdId: string,
  table: string,
  ids: string[],
): Promise<ExplorerDeletePreview> {
  return apiFetch<ExplorerDeletePreview>(
    `/v1/data-explorer/tables/${encodeURIComponent(table)}/delete-preview`,
    { token, householdId, method: "POST", body: JSON.stringify({ ids }) },
  );
}

export function deleteExplorerRows(
  token: string,
  householdId: string,
  table: string,
  ids: string[],
): Promise<ExplorerDeleteResult> {
  return apiFetch<ExplorerDeleteResult>(
    `/v1/data-explorer/tables/${encodeURIComponent(table)}/delete`,
    { token, householdId, method: "POST", body: JSON.stringify({ ids }) },
  );
}

/** Was ein Leeren des Haushalts wegnehmen würde. Ändert nichts. */
export function previewHouseholdPurge(
  token: string,
  householdId: string,
): Promise<ExplorerPurgePreview> {
  return apiFetch<ExplorerPurgePreview>("/v1/data-explorer/purge-preview", {
    token,
    householdId,
  });
}

/**
 * Leert alle Bewegungsdaten des Haushalts.
 *
 * `confirmName` muss dem Haushaltsnamen entsprechen — geprüft wird das auf dem
 * Server. Bei hunderten Testhaushalten neben einem echten wäre eine reine
 * Oberflächenprüfung keine Sicherung.
 */
export function purgeHousehold(
  token: string,
  householdId: string,
  confirmName: string,
): Promise<ExplorerPurgeResult> {
  return apiFetch<ExplorerPurgeResult>("/v1/data-explorer/purge", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify({ confirm_name: confirmName }),
  });
}

// ---------------------------------------------------------------------------
// Haushaltssicherung (Einstellungen → Sicherung)
//
// Derselbe Zuschnitt wie der Datenbank-Explorer oben — alle Haushaltstabellen
// auf einmal —, hier aber als eine Datei zum Herunterladen und Zurückspielen.
// ---------------------------------------------------------------------------

export interface BackupTableStat {
  table: string;
  rows: number;
}

export interface BackupInfo {
  household_name: string;
  table_count: number;
  total_rows: number;
  tables: BackupTableStat[];
  excluded: string[];
}

export interface BackupTablePreview {
  table: string;
  rows_in_file: number;
  rows_in_household: number;
  handling: "ersetzen" | "ueberschreiben" | "ergaenzen" | "uebersprungen";
  note: string | null;
}

export interface BackupImportPreview {
  household_name: string;
  source_household_name: string;
  source_household_id: string;
  created_at: string;
  schema_matches: boolean;
  rows_in_file: number;
  rows_in_household: number;
  tables: BackupTablePreview[];
  unknown_tables: string[];
}

export interface BackupImportResult {
  household_name: string;
  deleted_rows: number;
  imported_rows: number;
  imported_per_table: Record<string, number>;
  skipped_tables: string[];
  cleared_user_references: number;
  schema_matches: boolean;
}

export function getBackupInfo(token: string, householdId: string): Promise<BackupInfo> {
  return apiFetch<BackupInfo>("/v1/backup/info", { token, householdId });
}

/**
 * Lädt die Sicherung herunter und gibt Datei samt Namen zurück.
 *
 * Eigener fetch statt apiFetch wie bei downloadScenarioXlsx: die Antwort ist
 * ein ZIP, kein JSON. Der Dateiname kommt aus dem Content-Disposition-Kopf —
 * dort steht der Haushaltsname samt Zeitstempel, und den nachzubauen hieße,
 * ihn an zwei Stellen zu pflegen.
 */
export async function downloadBackup(
  token: string,
  householdId: string,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`${API_BASE}/v1/backup/export`, {
    headers: { Authorization: `Bearer ${token}`, "X-Household-Id": householdId },
  });
  if (!response.ok) {
    const { message, detail } = await buildHttpError(response);
    throw new ApiError(message, response.status, false, detail);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const treffer = /filename="([^"]+)"/.exec(disposition);
  return {
    blob: await response.blob(),
    filename: treffer?.[1] ?? "finanzplaner-sicherung.zip",
  };
}

/** Was ein Einspielen bewirken würde. Ändert nichts. */
export function previewBackupImport(
  token: string,
  householdId: string,
  file: File,
): Promise<BackupImportPreview> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<BackupImportPreview>("/v1/backup/preview", {
    token,
    householdId,
    method: "POST",
    body: formData,
  });
}

/**
 * Spielt die Sicherung ein und ersetzt damit die Daten des Haushalts.
 *
 * `confirmName` muss dem Haushaltsnamen entsprechen — geprüft wird das auf dem
 * Server, aus demselben Grund wie bei purgeHousehold.
 */
export function importBackup(
  token: string,
  householdId: string,
  file: File,
  confirmName: string,
): Promise<BackupImportResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("confirm_name", confirmName);
  return apiFetch<BackupImportResult>("/v1/backup/import", {
    token,
    householdId,
    method: "POST",
    body: formData,
  });
}

// ---------------------------------------------------------------------------
// Liquiditaetsvorschau
// ---------------------------------------------------------------------------

export function getForecast(
  token: string,
  householdId: string,
  params: {
    horizonMonths?: number;
    granularity?: ForecastGranularity;
    includeSavings?: boolean;
  } = {},
): Promise<ForecastResponse> {
  const query = new URLSearchParams();
  if (params.horizonMonths !== undefined) query.set("horizon_months", String(params.horizonMonths));
  if (params.granularity) query.set("granularity", params.granularity);
  if (params.includeSavings) query.set("include_savings", "true");
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<ForecastResponse>(`/v1/forecast${suffix}`, { token, householdId });
}

export function getForecastUpcoming(
  token: string,
  householdId: string,
  days = 90,
): Promise<ForecastDayGroup[]> {
  return apiFetch<ForecastDayGroup[]>(`/v1/forecast/upcoming?days=${days}`, { token, householdId });
}

export function getForecastSettings(token: string, householdId: string): Promise<ForecastSettings> {
  return apiFetch<ForecastSettings>("/v1/forecast/settings", { token, householdId });
}

export function updateForecastSettings(
  token: string,
  householdId: string,
  input: ForecastSettingsInput,
): Promise<ForecastSettings> {
  return apiFetch<ForecastSettings>("/v1/forecast/settings", {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function simulateForecast(
  token: string,
  householdId: string,
  input: ForecastSimulationInput,
): Promise<ForecastSimulationResponse> {
  return apiFetch<ForecastSimulationResponse>("/v1/forecast/simulate", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

// ---------------------------------------------------------------------------
// Personen und Einnahmenauswertung
// ---------------------------------------------------------------------------

export function listPeople(
  token: string,
  householdId: string,
  includeInactive = false,
): Promise<Person[]> {
  const suffix = includeInactive ? "?include_inactive=true" : "";
  return apiFetch<Person[]>(`/v1/people${suffix}`, { token, householdId });
}

export function createPerson(
  token: string,
  householdId: string,
  input: PersonCreateInput,
): Promise<Person> {
  return apiFetch<Person>("/v1/people", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updatePerson(
  token: string,
  householdId: string,
  personId: string,
  input: PersonUpdateInput,
): Promise<Person> {
  return apiFetch<Person>(`/v1/people/${personId}`, {
    token,
    householdId,
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

export function deactivatePerson(
  token: string,
  householdId: string,
  personId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/people/${personId}`, { token, householdId, method: "DELETE" });
}

export function getContractIncomeSummary(
  token: string,
  householdId: string,
  months = 24,
): Promise<ContractIncomeSummary> {
  return apiFetch<ContractIncomeSummary>(`/v1/contracts/income-summary?months=${months}`, {
    token,
    householdId,
  });
}

// ---------------------------------------------------------------------------
// Gespeicherte Haushaltsszenarien
// ---------------------------------------------------------------------------

export function listScenarios(token: string, householdId: string): Promise<HouseholdScenario[]> {
  return apiFetch<HouseholdScenario[]>("/v1/scenarios", { token, householdId });
}

export function getScenario(
  token: string,
  householdId: string,
  scenarioId: string,
): Promise<HouseholdScenarioDetail> {
  return apiFetch<HouseholdScenarioDetail>(`/v1/scenarios/${scenarioId}`, { token, householdId });
}

export function createScenario(
  token: string,
  householdId: string,
  input: HouseholdScenarioCreateInput,
): Promise<HouseholdScenarioDetail> {
  return apiFetch<HouseholdScenarioDetail>("/v1/scenarios", {
    token,
    householdId,
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateScenario(
  token: string,
  householdId: string,
  scenarioId: string,
  version: number,
  input: HouseholdScenarioUpdateInput,
): Promise<HouseholdScenarioDetail> {
  return apiFetch<HouseholdScenarioDetail>(`/v1/scenarios/${scenarioId}`, {
    token,
    householdId,
    method: "PATCH",
    headers: { "If-Match": String(version) },
    body: JSON.stringify(input),
  });
}

export function deleteScenario(
  token: string,
  householdId: string,
  scenarioId: string,
): Promise<void> {
  return apiFetch<void>(`/v1/scenarios/${scenarioId}`, { token, householdId, method: "DELETE" });
}

export function duplicateScenario(
  token: string,
  householdId: string,
  scenarioId: string,
): Promise<HouseholdScenarioDetail> {
  return apiFetch<HouseholdScenarioDetail>(`/v1/scenarios/${scenarioId}/duplicate`, {
    token,
    householdId,
    method: "POST",
  });
}

export function putScenarioChanges(
  token: string,
  householdId: string,
  scenarioId: string,
  changes: HouseholdScenarioChangeInput[],
): Promise<HouseholdScenarioDetail> {
  return apiFetch<HouseholdScenarioDetail>(`/v1/scenarios/${scenarioId}/changes`, {
    token,
    householdId,
    method: "PUT",
    body: JSON.stringify({ changes }),
  });
}

/** Liefert dieselbe Form wie `POST /v1/forecast/simulate` - es gibt genau einen Rechenkern. */
export function getScenarioForecast(
  token: string,
  householdId: string,
  scenarioId: string,
  includeSavings = false,
): Promise<ForecastSimulationResponse> {
  const suffix = includeSavings ? "?include_savings=true" : "";
  return apiFetch<ForecastSimulationResponse>(`/v1/scenarios/${scenarioId}/forecast${suffix}`, {
    token,
    householdId,
  });
}

export function compareScenarios(
  token: string,
  householdId: string,
  scenarioIds: string[],
  includeSavings = false,
): Promise<ScenarioComparisonResponse> {
  const query = new URLSearchParams({ ids: scenarioIds.join(",") });
  if (includeSavings) query.set("include_savings", "true");
  return apiFetch<ScenarioComparisonResponse>(`/v1/scenarios/compare?${query.toString()}`, {
    token,
    householdId,
  });
}
