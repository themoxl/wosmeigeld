import { getApiBaseUrl } from "./config";
import type {
  ArchiveApply,
  ArchivePreview,
  BulkCategoryInput,
  BulkCategoryResult,
  CatalogCategory,
  CategoryCatalog,
  CategoryCreate,
  CategoryUpdate,
  CatalogGroup,
  CategoryGroupCreate,
  CategoryGroupUpdate,
  GroupArchiveApply,
  GroupArchivePreview,
  MergeApply,
  MergePreview,
  MergeRequest,
  MergeResult,
  MoveApply,
  MovePreview,
  ReorderRequest,
  RestoreRequest,
  StandardPackageApply,
  StandardPackagePreview,
  StandardPackageResult,
  StandardPackageStatus,
  TransactionCategoryUpdate,
} from "./categoryTypes";
import type {
  Account,
  AIClassifyInput,
  AIClassifyResult,
  AIDecisionLog,
  AIHealthResponse,
  AnalyticsQuery,
  AnalyticsResult,
  AssetDebt,
  AssetDebtCreate,
  BudgetAllocationInput,
  BudgetSummary,
  BulkAssignGoalInput,
  BulkAssignGoalResult,
  CashflowTrendResponse,
  CategoryGroup,
  ClassificationRule,
  ClassificationRuleCreate,
  ClassificationRun,
  Contract,
  ContractAmountEntry,
  ContractCreate,
  ContractProviderSuggestion,
  ContractStatusChange,
  ContractSummary,
  ContractUpcoming,
  ContractUpdate,
  // Vorhaben & Grossinvestitionen (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md,
  // Abschnitt 9, P5).
  CostActual,
  CostActualCreateInput,
  CostItem,
  CostItemUpdateInput,
  CreateAccountInput,
  CreateTransactionInput,
  FinancingSource,
  FixedCostsTrendResponse,
  ForecastDayGroup,
  ForecastGranularity,
  ForecastResponse,
  FundingCheckRunResponse,
  FundingProgramDetail,
  Household,
  HouseholdInvite,
  HouseholdInvitePreview,
  HouseholdMember,
  HouseholdRole,
  ImportRevertPreview,
  ImportRevertResult,
  ImportRow,
  ImportRun,
  LinkTransferInput,
  ListTransactionsPageParams,
  ListTransactionsParams,
  MajorProject,
  MajorProjectCreateInput,
  Meal,
  MealCreate,
  MealUpdate,
  MealWeek,
  MealWeekCreate,
  MealWeekSummary,
  MeResponse,
  NetWorthReportResponse,
  OverviewReportResponse,
  PatchAccountInput,
  PatchTransactionInput,
  Person,
  ProjectListItem,
  ProjectReportRow,
  ProjectScenario,
  PurchaseLink,
  PurchaseLinkCreate,
  PushTokenPlatform,
  PushTokenResponse,
  PutTransactionSplitsInput,
  ReviewAcceptInput,
  ReviewContractSuggestionDetail,
  ReviewCorrectInput,
  ReviewInboxResponse,
  ReserveBatchResponse,
  ReserveDepositRequest,
  ReserveDistributionPreviewRequest,
  ReserveDistributionPreviewResponse,
  ReserveOverviewResponse,
  ReserveReallocationRequest,
  ReserveSkipRequest,
  ReserveWithdrawalRequest,
  SavingsGoal,
  SavingsGoalCreate,
  SavingsGoalUpdate,
  ScenarioCalculation,
  ShoppingItem,
  ShoppingItemCreate,
  ShoppingItemUpdate,
  ShoppingList,
  ShoppingListCreate,
  Transaction,
  TransactionCandidate,
  TransactionConflict,
  TransactionDetail,
  TransactionPage,
  TransactionProvenance,
  TransferLink,
  UnassignedGoalSplitCandidate,
  WishlistItem,
  WishlistItemCreate,
  WishlistItemUpdate,
  WishlistSavingsPlanCreate,
  WishlistSavingsPlanResponse,
} from "./types";

// Authentifizierter Fetch-Helfer, analog zu fetchSystemHealth() in
// health.ts, aber mit Authorization- und optionalem X-Household-Id-Header
// (siehe Auftrag: alle /v1/*-Endpunkte ausser /health und
// /v1/system/health verlangen Authorization: Bearer <token>;
// /v1/accounts, /v1/categories, /v1/transactions zusaetzlich
// X-Household-Id).
export class ApiError extends Error {
  readonly status: number;
  /**
   * Rohes `detail`-Feld der Fehlerantwort, sofern vorhanden. Der Server
   * verwendet dafuer stabile Kennungen (z. B. "household_role_readonly",
   * "version_conflict") - der Client soll darauf pruefen statt auf den
   * uebersetzten Meldungstext.
   */
  readonly detail: string | null;
  /**
   * Phase 9 (Offline-Write-Queue): bei 409 liefert die API den vollstaendigen
   * Serverstand der Buchung mit (siehe TransactionConflict in ./types). Der
   * Queue-Processor reicht ihn in die Konfliktansicht durch, statt den
   * Konflikt stillschweigend zu verwerfen.
   */
  readonly conflict: TransactionConflict | null;

  constructor(
    message: string,
    status: number,
    options?: { detail?: string | null; conflict?: TransactionConflict | null },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = options?.detail ?? null;
    this.conflict = options?.conflict ?? null;
  }
}

/**
 * Kennung aus einer Fehlerantwort, sofern es eine ApiError mit `detail` ist
 * (z. B. "actuals_overbooking"). Analog zu apiErrorDetail() in
 * apps/web/src/lib/api.ts - Aufrufer sollen auf die stabile Kennung pruefen
 * statt auf den (uebersetzten) Meldungstext.
 */
export function apiErrorDetail(error: unknown): string | null {
  return error instanceof ApiError ? error.detail : null;
}

/** true, wenn der Antwortkoerper die Form von TransactionConflict hat. */
function parseTransactionConflict(payload: unknown): TransactionConflict | null {
  if (!payload || typeof payload !== "object") return null;
  const candidate = payload as Partial<TransactionConflict>;
  if (candidate.detail !== "version_conflict" && candidate.detail !== "transaction_archived") {
    return null;
  }
  if (typeof candidate.current_version !== "number") return null;
  if (!candidate.current || typeof candidate.current !== "object") return null;
  return candidate as TransactionConflict;
}

// Stabile 403-Kennungen der API (Feld `detail`) auf verstaendliche Texte
// abgebildet. "insufficient_privilege" ist die Ablehnung durch die
// Datenbank-RLS und sollte im Normalbetrieb nie sichtbar werden, weil das
// Rollen-Gate der API vorher greift - deshalb bewusst ohne eigenen Text.
const FORBIDDEN_MESSAGES: Record<string, string> = {
  household_role_readonly: "Diese Rolle darf im Haushalt nur lesen, nicht ändern.",
  household_owner_required: "Das darf nur die Besitzerin/der Besitzer des Haushalts.",
};

/**
 * Stabile Fehlerkennungen der Kategorienverwaltung
 * (docs/KATEGORISIERUNGSPLAN.md, "API-Vertrag") auf verstaendliche Texte
 * abgebildet. Wie bei FORBIDDEN_MESSAGES gilt: geprueft wird auf die
 * Kennung, angezeigt wird der Text.
 */
const DETAIL_MESSAGES: Record<string, string> = {
  if_match_required:
    "Diese Änderung braucht den zuletzt gelesenen Stand. Bitte die Ansicht neu laden.",
  version_conflict:
    "Der Eintrag wurde zwischenzeitlich an anderer Stelle geändert. Bitte neu laden und erneut versuchen.",
  category_tree_revision_conflict:
    "Die Kategorienstruktur wurde zwischenzeitlich an anderer Stelle geändert. Bitte neu laden.",
  category_preview_stale:
    "Die Vorschau ist nicht mehr aktuell. Bitte die Auswirkungen erneut berechnen lassen.",
  category_duplicate_name: "In dieser Gruppe gibt es bereits eine Kategorie mit diesem Namen.",
  category_inactive: "Diese Kategorie ist archiviert und kann nicht neu zugeordnet werden.",
  transaction_has_multiple_splits:
    "Diese Buchung ist aufgeteilt. Die Kategorie lässt sich nur je Split ändern.",
};

type AuthorizedRequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  accessToken: string;
  householdId?: string;
  body?: unknown;
  signal?: AbortSignal;
  /**
   * Optimistic-Concurrency-Token der Entitaet (Gruppe/Kategorie). Beim
   * Aendern und Archivieren verpflichtend - fehlt der Header, antwortet der
   * Server mit 428 `if_match_required`.
   */
  ifMatch?: number | string;
};

async function authorizedFetch<T>(path: string, options: AuthorizedRequestOptions): Promise<T> {
  const { method = "GET", accessToken, householdId, body, signal, ifMatch } = options;

  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };
  if (householdId) {
    headers["X-Household-Id"] = householdId;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (ifMatch !== undefined) {
    headers["If-Match"] = `"${ifMatch}"`;
  }

  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (cause) {
    throw new Error(
      `API unter ${getApiBaseUrl()} nicht erreichbar. Läuft die lokale API (docker compose up)?`,
      { cause },
    );
  }

  if (!response.ok) {
    let detail = "";
    let conflict: TransactionConflict | null = null;
    try {
      const payload: unknown = await response.json();
      if (
        payload &&
        typeof payload === "object" &&
        typeof (payload as { detail?: unknown }).detail === "string"
      ) {
        detail = (payload as { detail: string }).detail;
      }
      if (response.status === 409) {
        conflict = parseTransactionConflict(payload);
      }
    } catch {
      // Antwortkoerper war kein (oder kein lesbares) JSON - ignorieren und
      // stattdessen die generische Meldung unten verwenden.
    }

    const options = { detail: detail || null, conflict };

    if (response.status === 401) {
      throw new ApiError(detail || "Anmeldung abgelaufen. Bitte erneut anmelden.", 401, options);
    }
    if (response.status === 400 && !householdId) {
      throw new ApiError(detail || "Kein Haushalt ausgewählt.", 400, options);
    }
    if (response.status === 403) {
      // Der Server unterscheidet seit Phase 9 drei Ablehnungsgruende - die
      // beiden fachlichen bekommen einen sprechenden Text, alles andere
      // bleibt der generische Berechtigungshinweis.
      throw new ApiError(
        FORBIDDEN_MESSAGES[detail] ?? "Kein Zugriff auf diesen Haushalt.",
        403,
        options,
      );
    }
    if (response.status === 409 && conflict) {
      throw new ApiError(
        conflict.detail === "transaction_archived"
          ? "Die Buchung wurde zwischenzeitlich an anderer Stelle gelöscht."
          : "Die Buchung wurde zwischenzeitlich an anderer Stelle geändert.",
        409,
        options,
      );
    }
    throw new ApiError(
      DETAIL_MESSAGES[detail] || detail || `Anfrage fehlgeschlagen (HTTP ${response.status}).`,
      response.status,
      options,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function getMe(accessToken: string, signal?: AbortSignal): Promise<MeResponse> {
  return authorizedFetch<MeResponse>("/v1/me", { accessToken, signal });
}

export function listHouseholds(accessToken: string, signal?: AbortSignal): Promise<Household[]> {
  return authorizedFetch<Household[]>("/v1/households", { accessToken, signal });
}

export function createHousehold(accessToken: string, name: string): Promise<Household> {
  return authorizedFetch<Household>("/v1/households", {
    accessToken,
    method: "POST",
    body: { name },
  });
}

export function renameHousehold(
  accessToken: string,
  householdId: string,
  name: string,
): Promise<Household> {
  return authorizedFetch<Household>("/v1/households", {
    accessToken,
    householdId,
    method: "PATCH",
    body: { name },
  });
}

export function listHouseholdMembers(
  accessToken: string,
  householdId: string,
): Promise<HouseholdMember[]> {
  return authorizedFetch<HouseholdMember[]>("/v1/households/members", {
    accessToken,
    householdId,
  });
}

export function listHouseholdInvites(
  accessToken: string,
  householdId: string,
): Promise<HouseholdInvite[]> {
  return authorizedFetch<HouseholdInvite[]>("/v1/households/invites", {
    accessToken,
    householdId,
  });
}

export function createHouseholdInvite(
  accessToken: string,
  householdId: string,
  role: HouseholdRole,
): Promise<HouseholdInvite> {
  return authorizedFetch<HouseholdInvite>("/v1/households/invites", {
    accessToken,
    householdId,
    method: "POST",
    body: { role },
  });
}

export function revokeHouseholdInvite(
  accessToken: string,
  householdId: string,
  inviteId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/households/invites/${inviteId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

export function previewHouseholdInvite(
  accessToken: string,
  code: string,
): Promise<HouseholdInvitePreview> {
  return authorizedFetch<HouseholdInvitePreview>(
    `/v1/households/invites/redeem/${encodeURIComponent(code)}`,
    { accessToken },
  );
}

export function redeemHouseholdInvite(accessToken: string, code: string): Promise<Household> {
  return authorizedFetch<Household>("/v1/households/invites/redeem", {
    accessToken,
    method: "POST",
    body: { code },
  });
}

export function listAccounts(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<Account[]> {
  return authorizedFetch<Account[]>("/v1/accounts", { accessToken, householdId, signal });
}

export function createAccount(
  accessToken: string,
  householdId: string,
  input: CreateAccountInput,
): Promise<Account> {
  return authorizedFetch<Account>("/v1/accounts", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function listCategories(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<CategoryGroup[]> {
  return authorizedFetch<CategoryGroup[]>("/v1/categories", { accessToken, householdId, signal });
}

// --- Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md) --------------------
//
// Zwei Leseendpunkte mit unterschiedlichem Zweck:
//   GET /v1/categories          - aktiver Picker-Baum (listCategories oben)
//   GET /v1/categories/catalog  - Verwaltungsansicht inkl. archivierter
//                                 Eintraege, Versionen und Nutzungszahlen
// Alle Schreibpfade auf Gruppe/Kategorie verlangen `If-Match` mit der
// zuletzt gelesenen `version`; baumweite Sortierungen stattdessen die
// `taxonomy_revision` im Koerper.

export function getCategoryCatalog(
  accessToken: string,
  householdId: string,
  options?: { includeArchived?: boolean; includeUsage?: boolean },
  signal?: AbortSignal,
): Promise<CategoryCatalog> {
  const query = new URLSearchParams();
  if (options?.includeArchived) query.set("include_archived", "true");
  if (options?.includeUsage) query.set("include_usage", "true");
  const queryString = query.toString();
  return authorizedFetch<CategoryCatalog>(
    `/v1/categories/catalog${queryString ? `?${queryString}` : ""}`,
    { accessToken, householdId, signal },
  );
}

export function createCategory(
  accessToken: string,
  householdId: string,
  input: CategoryCreate,
): Promise<CatalogCategory> {
  return authorizedFetch<CatalogCategory>("/v1/categories", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function updateCategory(
  accessToken: string,
  householdId: string,
  categoryId: string,
  input: CategoryUpdate,
  version: number,
): Promise<CatalogCategory> {
  return authorizedFetch<CatalogCategory>(`/v1/categories/${categoryId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
    ifMatch: version,
  });
}

export function createCategoryGroup(
  accessToken: string,
  householdId: string,
  input: CategoryGroupCreate,
): Promise<CatalogGroup> {
  return authorizedFetch<CatalogGroup>("/v1/categories/groups", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function updateCategoryGroup(
  accessToken: string,
  householdId: string,
  groupId: string,
  input: CategoryGroupUpdate,
  version: number,
): Promise<CatalogGroup> {
  return authorizedFetch<CatalogGroup>(`/v1/categories/groups/${groupId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
    ifMatch: version,
  });
}

export function reorderCategories(
  accessToken: string,
  householdId: string,
  input: ReorderRequest,
): Promise<CategoryCatalog> {
  return authorizedFetch<CategoryCatalog>("/v1/categories/reorder", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function reorderCategoryGroups(
  accessToken: string,
  householdId: string,
  input: ReorderRequest,
): Promise<CategoryCatalog> {
  return authorizedFetch<CategoryCatalog>("/v1/categories/groups/reorder", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function previewCategoryMove(
  accessToken: string,
  householdId: string,
  categoryId: string,
  targetGroupId: string,
): Promise<MovePreview> {
  return authorizedFetch<MovePreview>(`/v1/categories/${categoryId}/move-preview`, {
    accessToken,
    householdId,
    method: "POST",
    body: { target_group_id: targetGroupId },
  });
}

export function applyCategoryMove(
  accessToken: string,
  householdId: string,
  categoryId: string,
  input: MoveApply,
): Promise<CatalogCategory> {
  return authorizedFetch<CatalogCategory>(`/v1/categories/${categoryId}/move`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function previewCategoryArchive(
  accessToken: string,
  householdId: string,
  categoryId: string,
): Promise<ArchivePreview> {
  return authorizedFetch<ArchivePreview>(`/v1/categories/${categoryId}/archive-preview`, {
    accessToken,
    householdId,
    method: "POST",
  });
}

export function applyCategoryArchive(
  accessToken: string,
  householdId: string,
  categoryId: string,
  input: ArchiveApply,
  version: number,
): Promise<CatalogCategory> {
  return authorizedFetch<CatalogCategory>(`/v1/categories/${categoryId}/archive`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
    ifMatch: version,
  });
}

export function restoreCategory(
  accessToken: string,
  householdId: string,
  categoryId: string,
  input: RestoreRequest,
): Promise<CatalogCategory> {
  return authorizedFetch<CatalogCategory>(`/v1/categories/${categoryId}/restore`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function previewCategoryGroupArchive(
  accessToken: string,
  householdId: string,
  groupId: string,
): Promise<GroupArchivePreview> {
  return authorizedFetch<GroupArchivePreview>(`/v1/categories/groups/${groupId}/archive-preview`, {
    accessToken,
    householdId,
    method: "POST",
  });
}

export function applyCategoryGroupArchive(
  accessToken: string,
  householdId: string,
  groupId: string,
  input: GroupArchiveApply,
  version: number,
): Promise<CatalogGroup> {
  return authorizedFetch<CatalogGroup>(`/v1/categories/groups/${groupId}/archive`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
    ifMatch: version,
  });
}

export function restoreCategoryGroup(
  accessToken: string,
  householdId: string,
  groupId: string,
): Promise<CatalogGroup> {
  return authorizedFetch<CatalogGroup>(`/v1/categories/groups/${groupId}/restore`, {
    accessToken,
    householdId,
    method: "POST",
  });
}

/** Merge ist irreversibel und deshalb serverseitig owner-only (403 household_owner_required). */
export function previewCategoryMerge(
  accessToken: string,
  householdId: string,
  input: MergeRequest,
): Promise<MergePreview> {
  return authorizedFetch<MergePreview>("/v1/categories/merge-preview", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function applyCategoryMerge(
  accessToken: string,
  householdId: string,
  input: MergeApply,
): Promise<MergeResult> {
  return authorizedFetch<MergeResult>("/v1/categories/merge", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function getStandardPackageStatus(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<StandardPackageStatus> {
  return authorizedFetch<StandardPackageStatus>("/v1/categories/standard-package", {
    accessToken,
    householdId,
    signal,
  });
}

export function previewStandardPackage(
  accessToken: string,
  householdId: string,
): Promise<StandardPackagePreview> {
  return authorizedFetch<StandardPackagePreview>("/v1/categories/standard-package/preview", {
    accessToken,
    householdId,
    method: "POST",
  });
}

/** Das Anwenden eines haushaltsweiten Paket-Upgrades ist owner-only. */
export function applyStandardPackage(
  accessToken: string,
  householdId: string,
  input: StandardPackageApply,
): Promise<StandardPackageResult> {
  return authorizedFetch<StandardPackageResult>("/v1/categories/standard-package/apply", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

/**
 * Kategorie genau eines Splits aendern. Wird auch vom Offline-Queue-Processor
 * verwendet (siehe ../offline/queueProcessor.ts, Eintragsart "category") -
 * es gibt bewusst keinen zweiten, direkten Schreibpfad.
 */
export function updateTransactionCategory(
  accessToken: string,
  householdId: string,
  transactionId: string,
  input: TransactionCategoryUpdate,
): Promise<TransactionDetail> {
  return authorizedFetch<TransactionDetail>(`/v1/transactions/${transactionId}/category`, {
    accessToken,
    householdId,
    method: "PUT",
    body: input,
  });
}

export function previewBulkCategory(
  accessToken: string,
  householdId: string,
  input: BulkCategoryInput,
): Promise<BulkCategoryResult> {
  return authorizedFetch<BulkCategoryResult>("/v1/transactions/bulk-category/preview", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function applyBulkCategory(
  accessToken: string,
  householdId: string,
  input: BulkCategoryInput,
): Promise<BulkCategoryResult> {
  return authorizedFetch<BulkCategoryResult>("/v1/transactions/bulk-category/apply", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function listTransactions(
  accessToken: string,
  householdId: string,
  params?: ListTransactionsParams,
  signal?: AbortSignal,
): Promise<Transaction[]> {
  const query = new URLSearchParams();
  if (params?.accountId) {
    query.set("account_id", params.accountId);
  }
  if (params?.fromDate) {
    query.set("from_date", params.fromDate);
  }
  if (params?.toDate) {
    query.set("to_date", params.toDate);
  }
  if (params?.limit) {
    query.set("limit", String(params.limit));
  }
  const queryString = query.toString();
  return authorizedFetch<Transaction[]>(`/v1/transactions${queryString ? `?${queryString}` : ""}`, {
    accessToken,
    householdId,
    signal,
  });
}

// Additiver, cursorbasierter Paging-Endpunkt fuer die neue Buchungsliste
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5). listTransactions() oben
// bleibt fuer Bestandsclients (z. B. Detailseite, Einkaufsliste) unveraendert
// bestehen - dieser Endpunkt darf sein Filterverhalten eigenstaendig
// weiterentwickeln.
export function listTransactionsPage(
  accessToken: string,
  householdId: string,
  params?: ListTransactionsPageParams,
  signal?: AbortSignal,
): Promise<TransactionPage> {
  const query = new URLSearchParams();
  if (params?.accountId) {
    query.set("account_id", params.accountId);
  }
  if (params?.fromDate) {
    query.set("from_date", params.fromDate);
  }
  if (params?.toDate) {
    query.set("to_date", params.toDate);
  }
  if (params?.search) {
    query.set("search", params.search);
  }
  if (params?.categoryId) {
    query.set("category_id", params.categoryId);
  }
  if (params?.uncategorized) {
    query.set("uncategorized", "true");
  }
  if (params?.direction) {
    query.set("direction", params.direction);
  }
  if (params?.sort) {
    query.set("sort", params.sort);
  }
  if (params?.limit) {
    query.set("limit", String(params.limit));
  }
  if (params?.cursor) {
    query.set("cursor", params.cursor);
  }
  const queryString = query.toString();
  return authorizedFetch<TransactionPage>(
    `/v1/transactions/page${queryString ? `?${queryString}` : ""}`,
    { accessToken, householdId, signal },
  );
}

export function createTransaction(
  accessToken: string,
  householdId: string,
  input: CreateTransactionInput,
): Promise<Transaction> {
  return authorizedFetch<Transaction>("/v1/transactions", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function getTransaction(
  accessToken: string,
  householdId: string,
  transactionId: string,
  signal?: AbortSignal,
): Promise<TransactionDetail> {
  return authorizedFetch<TransactionDetail>(`/v1/transactions/${transactionId}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function getTransactionProvenance(
  accessToken: string,
  householdId: string,
  transactionId: string,
  signal?: AbortSignal,
): Promise<TransactionProvenance> {
  return authorizedFetch<TransactionProvenance>(`/v1/transactions/${transactionId}/provenance`, {
    accessToken,
    householdId,
    signal,
  });
}

export function patchTransaction(
  accessToken: string,
  householdId: string,
  transactionId: string,
  input: PatchTransactionInput,
): Promise<TransactionDetail> {
  return authorizedFetch<TransactionDetail>(`/v1/transactions/${transactionId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
  });
}

export function deleteTransaction(
  accessToken: string,
  householdId: string,
  transactionId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/transactions/${transactionId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

// Zentrale Buchungsinvariante (Architekturplan Abschnitt 9): der Server
// lehnt mit 422 ab, wenn sum(splits.amount_minor) !== amount_minor ist.
// Wird sowohl fuer Betrags- als auch fuer Kategorieaenderungen verwendet
// (siehe Backend-Kontrakt im Auftrag).
export function putTransactionSplits(
  accessToken: string,
  householdId: string,
  transactionId: string,
  input: PutTransactionSplitsInput,
): Promise<TransactionDetail> {
  return authorizedFetch<TransactionDetail>(`/v1/transactions/${transactionId}/splits`, {
    accessToken,
    householdId,
    method: "PUT",
    body: input,
  });
}

export function listUnassignedGoalSplits(
  accessToken: string,
  householdId: string,
  accountId?: string,
  signal?: AbortSignal,
): Promise<UnassignedGoalSplitCandidate[]> {
  const query = new URLSearchParams();
  if (accountId) {
    query.set("account_id", accountId);
  }
  const queryString = query.toString();
  return authorizedFetch<UnassignedGoalSplitCandidate[]>(
    `/v1/transactions/splits/unassigned-goal${queryString ? `?${queryString}` : ""}`,
    { accessToken, householdId, signal },
  );
}

export function assignGoalToSplits(
  accessToken: string,
  householdId: string,
  input: BulkAssignGoalInput,
): Promise<BulkAssignGoalResult> {
  return authorizedFetch<BulkAssignGoalResult>("/v1/transactions/splits/assign-goal", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function linkTransfer(
  accessToken: string,
  householdId: string,
  input: LinkTransferInput,
): Promise<TransferLink> {
  return authorizedFetch<TransferLink>("/v1/transfers/link", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function unlinkTransfer(
  accessToken: string,
  householdId: string,
  transferLinkId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/transfers/link/${transferLinkId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

export function patchAccount(
  accessToken: string,
  householdId: string,
  accountId: string,
  input: PatchAccountInput,
): Promise<Account> {
  return authorizedFetch<Account>(`/v1/accounts/${accountId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
  });
}

export async function uploadImport(
  accessToken: string,
  householdId: string,
  file: { uri: string; name: string; type: string },
  accountId?: string,
): Promise<ImportRun> {
  const formData = new FormData();
  formData.append("file", {
    uri: file.uri,
    name: file.name,
    type: file.type || "application/octet-stream",
  } as unknown as Blob);

  if (accountId) {
    formData.append("account_id", accountId);
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
    "X-Household-Id": householdId,
  };

  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}/v1/imports/upload`, {
      method: "POST",
      headers,
      body: formData,
    });
  } catch (cause) {
    throw new Error(
      `API unter ${getApiBaseUrl()} nicht erreichbar. Läuft die lokale API (docker compose up)?`,
      { cause },
    );
  }

  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload?.detail === "string") {
        detail = payload.detail;
      }
    } catch {
      // Ignorieren
    }

    if (response.status === 401) {
      throw new ApiError(detail || "Anmeldung abgelaufen. Bitte erneut anmelden.", 401);
    }
    if (response.status === 403) {
      throw new ApiError(detail || "Kein Zugriff auf diesen Haushalt.", 403);
    }
    throw new ApiError(
      detail || `Import-Upload fehlgeschlagen (HTTP ${response.status}).`,
      response.status,
    );
  }

  return (await response.json()) as ImportRun;
}

export function getImportRun(
  accessToken: string,
  householdId: string,
  id: string,
  signal?: AbortSignal,
): Promise<ImportRun> {
  return authorizedFetch<ImportRun>(`/v1/imports/${id}`, {
    accessToken,
    householdId,
    signal,
  });
}

/** Historie der Importlaeufe des Haushalts (GET /v1/imports). */
export function listImportRuns(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<ImportRun[]> {
  return authorizedFetch<ImportRun[]>("/v1/imports", {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Was wuerde ein Zuruecknehmen dieses Laufs anfassen?
 * (GET /v1/imports/{id}/revert-preview, aendert nichts).
 */
export function previewImportRevert(
  accessToken: string,
  householdId: string,
  id: string,
  signal?: AbortSignal,
): Promise<ImportRevertPreview> {
  return authorizedFetch<ImportRevertPreview>(`/v1/imports/${id}/revert-preview`, {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Nimmt die Buchungen eines uebernommenen Laufs aus dem Bestand
 * (POST /v1/imports/{id}/revert).
 *
 * Sie werden archiviert, nicht geloescht; der Lauf bleibt als
 * Herkunftsnachweis stehen. Danach laesst sich dieselbe Datei erneut
 * einlesen - die Dublettenerkennung sieht archivierte Buchungen nicht.
 */
export function revertImportRun(
  accessToken: string,
  householdId: string,
  id: string,
): Promise<ImportRevertResult> {
  return authorizedFetch<ImportRevertResult>(`/v1/imports/${id}/revert`, {
    accessToken,
    householdId,
    method: "POST",
  });
}

export function getImportRows(
  accessToken: string,
  householdId: string,
  id: string,
  signal?: AbortSignal,
): Promise<ImportRow[]> {
  return authorizedFetch<ImportRow[]>(`/v1/imports/${id}/rows`, {
    accessToken,
    householdId,
    signal,
  });
}

export function commitImportRun(
  accessToken: string,
  householdId: string,
  id: string,
  rowIds?: string[],
): Promise<{ committed: number }> {
  return authorizedFetch<{ committed: number }>(`/v1/imports/${id}/commit`, {
    accessToken,
    householdId,
    method: "POST",
    body: { row_ids: rowIds ?? null },
  });
}

// Phase 4: Review-Inbox API
export function getReviewInbox(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<ReviewInboxResponse> {
  return authorizedFetch<ReviewInboxResponse>("/v1/review", {
    accessToken,
    householdId,
    signal,
  });
}

export function scanBookedDuplicates(accessToken: string, householdId: string) {
  return authorizedFetch<import("./types").BookedDuplicateScanResult>(
    "/v1/review/duplicates/scan", { accessToken, householdId },
  );
}

export function resolveBookedDuplicate(
  accessToken: string, householdId: string, input: import("./types").ResolveBookedDuplicateInput,
) {
  return authorizedFetch<void>("/v1/review/duplicates/resolve", {
    accessToken, householdId, method: "POST", body: input,
  });
}

/**
 * Buchungen, Preisstufen und Score-Bestandteile eines Vertragsvorschlags.
 *
 * Der Vorschlag wird bei jedem Aufruf neu erkannt - er ist nirgends
 * gespeichert. Ist die Ankerbuchung inzwischen kategorisiert oder der Vertrag
 * bereits angelegt, antwortet der Server mit 404 statt einem leeren Detail.
 */
export function getContractSuggestionDetail(
  accessToken: string,
  householdId: string,
  sourceTransactionId: string,
  signal?: AbortSignal,
): Promise<ReviewContractSuggestionDetail> {
  return authorizedFetch<ReviewContractSuggestionDetail>(
    `/v1/review/contract-suggestions/${sourceTransactionId}`,
    { accessToken, householdId, signal },
  );
}

export function acceptReviewItem(
  accessToken: string,
  householdId: string,
  input: ReviewAcceptInput,
): Promise<{ status: string }> {
  return authorizedFetch<{ status: string }>("/v1/review/accept", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

/**
 * Bestaetigt mehrere moegliche Dubletten in einem Zug
 * (POST /v1/review/accept/duplicates).
 *
 * Ein eigener Endpunkt statt einer Schleife: fuenfzig Einzelrequests waeren
 * fuenfzig Transaktionen, von denen die Haelfte durchgehen kann und die andere
 * nicht - auf einer wackligen Mobilverbindung der Regelfall. `skipped` sind
 * Zeilen, die inzwischen nicht mehr offen waren.
 */
export function acceptDuplicatesBulk(
  accessToken: string,
  householdId: string,
  importRowIds: string[],
): Promise<{ confirmed: number; skipped: number }> {
  return authorizedFetch<{ confirmed: number; skipped: number }>("/v1/review/accept/duplicates", {
    accessToken,
    householdId,
    method: "POST",
    body: { import_row_ids: importRowIds },
  });
}

export function correctReviewItem(
  accessToken: string,
  householdId: string,
  input: ReviewCorrectInput,
): Promise<{ status: string }> {
  return authorizedFetch<{ status: string }>("/v1/review/correct", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

// Phase 4: Regelengine API
export function listRules(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<ClassificationRule[]> {
  return authorizedFetch<ClassificationRule[]>("/v1/rules", {
    accessToken,
    householdId,
    signal,
  });
}

export function createRule(
  accessToken: string,
  householdId: string,
  input: ClassificationRuleCreate,
): Promise<ClassificationRule> {
  return authorizedFetch<ClassificationRule>("/v1/rules", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

/**
 * Personen des Haushalts (Liquiditaetsplan Stufe 6). Nur lesend - die
 * Verwaltung gehoert ins Web.
 *
 * `include_inactive` ist standardmaessig an: ein Vertrag behaelt beim
 * Loeschen einer Person deren `person_id` (Soft-Delete). Ohne den Namen der
 * geloeschten Person landete seine Einnahme in der Gruppierung unter "Ohne
 * Person" - eine Aussage, die nicht stimmt.
 */
export function listPeople(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
  options: { includeInactive?: boolean } = {},
): Promise<Person[]> {
  const includeInactive = options.includeInactive ?? true;
  const query = includeInactive ? "?include_inactive=true" : "";
  return authorizedFetch<Person[]>(`/v1/people${query}`, {
    accessToken,
    householdId,
    signal,
  });
}

// Vertraege (loesen /v1/recurring und /v1/subscriptions ab, Migration 0014)
export function listContracts(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
  options: { includeEnded?: boolean } = {},
): Promise<Contract[]> {
  const query = options.includeEnded ? "?include_ended=true" : "";
  return authorizedFetch<Contract[]>(`/v1/contracts${query}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function getContractSummary(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<ContractSummary> {
  return authorizedFetch<ContractSummary>("/v1/contracts/summary", {
    accessToken,
    householdId,
    signal,
  });
}

export function getContractForecast(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
  days = 90,
): Promise<ContractUpcoming[]> {
  return authorizedFetch<ContractUpcoming[]>(`/v1/contracts/forecast?days=${days}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function listContractHistory(
  accessToken: string,
  householdId: string,
  contractId: string,
  signal?: AbortSignal,
): Promise<ContractAmountEntry[]> {
  return authorizedFetch<ContractAmountEntry[]>(`/v1/contracts/${contractId}/history`, {
    accessToken,
    householdId,
    signal,
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
  accessToken: string,
  householdId: string,
  query: string,
  signal?: AbortSignal,
): Promise<ContractProviderSuggestion[]> {
  return authorizedFetch<ContractProviderSuggestion[]>(
    `/v1/contracts/provider-suggestions?q=${encodeURIComponent(query)}`,
    { accessToken, householdId, signal },
  );
}

export function createContract(
  accessToken: string,
  householdId: string,
  input: ContractCreate,
): Promise<Contract> {
  return authorizedFetch<Contract>("/v1/contracts", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function updateContract(
  accessToken: string,
  householdId: string,
  contractId: string,
  input: ContractUpdate,
  version: number,
): Promise<Contract> {
  return authorizedFetch<Contract>(`/v1/contracts/${contractId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
    ifMatch: version,
  });
}

export function setContractStatus(
  accessToken: string,
  householdId: string,
  contractId: string,
  input: ContractStatusChange,
): Promise<Contract> {
  return authorizedFetch<Contract>(`/v1/contracts/${contractId}/status`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function deleteContract(
  accessToken: string,
  householdId: string,
  contractId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/contracts/${contractId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

// Phase 5: Budgetkern API
export function getBudgetSummary(
  accessToken: string,
  householdId: string,
  year: number,
  month: number,
  signal?: AbortSignal,
): Promise<BudgetSummary> {
  return authorizedFetch<BudgetSummary>(`/v1/budgets/${year}/${month}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function updateBudgetAllocations(
  accessToken: string,
  householdId: string,
  year: number,
  month: number,
  allocations: BudgetAllocationInput[],
): Promise<BudgetSummary> {
  return authorizedFetch<BudgetSummary>(`/v1/budgets/${year}/${month}/allocations`, {
    accessToken,
    householdId,
    method: "PUT",
    body: { allocations },
  });
}

// Native Rücklagen-Ledger. Schreibvorgänge werden in useReserves explizit
// online-only ausgeführt und landen nie in der Offline-Buchungsqueue.
export function getReserveOverview(
  accessToken: string,
  householdId: string,
  year: number,
  month: number,
  signal?: AbortSignal,
): Promise<ReserveOverviewResponse> {
  return authorizedFetch<ReserveOverviewResponse>(
    `/v1/reserves/overview?year=${year}&month=${month}`,
    { accessToken, householdId, signal },
  );
}

export function createReserveDeposit(
  accessToken: string,
  householdId: string,
  input: ReserveDepositRequest,
): Promise<ReserveBatchResponse> {
  return authorizedFetch<ReserveBatchResponse>("/v1/reserves/deposits", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function createReserveWithdrawal(
  accessToken: string,
  householdId: string,
  input: ReserveWithdrawalRequest,
): Promise<ReserveBatchResponse> {
  return authorizedFetch<ReserveBatchResponse>("/v1/reserves/withdrawals", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function createReserveReallocation(
  accessToken: string,
  householdId: string,
  input: ReserveReallocationRequest,
): Promise<ReserveBatchResponse> {
  return authorizedFetch<ReserveBatchResponse>("/v1/reserves/reallocations", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function skipReserveMonth(
  accessToken: string,
  householdId: string,
  input: ReserveSkipRequest,
): Promise<void> {
  return authorizedFetch<void>("/v1/reserves/month-decisions/skip", {
    accessToken,
    householdId,
    method: "PUT",
    body: input,
  });
}

export function undoSkipReserveMonth(
  accessToken: string,
  householdId: string,
  input: ReserveSkipRequest,
): Promise<void> {
  return authorizedFetch<void>("/v1/reserves/month-decisions/skip", {
    accessToken,
    householdId,
    method: "DELETE",
    body: input,
  });
}

export function previewReserveDistribution(
  accessToken: string,
  householdId: string,
  input: ReserveDistributionPreviewRequest,
): Promise<ReserveDistributionPreviewResponse> {
  return authorizedFetch<ReserveDistributionPreviewResponse>("/v1/reserves/distribution-preview", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

// Phase 5: Overview Report API
export function getOverviewReport(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<OverviewReportResponse> {
  return authorizedFetch<OverviewReportResponse>("/v1/reports/overview", {
    accessToken,
    householdId,
    signal,
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
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
  months = 24,
): Promise<FixedCostsTrendResponse> {
  return authorizedFetch<FixedCostsTrendResponse>(
    `/v1/reports/fixed-costs-trend?months=${months}`,
    {
      accessToken,
      householdId,
      signal,
    },
  );
}

/**
 * Einnahmen, Ausgaben und Saldo je Monat (1-36 Monate, Standard 24).
 *
 * Gebuchte Betraege, Transfers ausgeschlossen, nur Budgetkonten - das ist die
 * Ist-Reihe. Sie ist deshalb NICHT mit getFixedCostsTrend() vergleichbar, das
 * vereinbarte Vertragsbetraege liefert.
 */
export function getCashflowTrend(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
  months = 12,
): Promise<CashflowTrendResponse> {
  return authorizedFetch<CashflowTrendResponse>(`/v1/reports/cashflow-trend?months=${months}`, {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Freie Auswertung (POST /v1/analytics) - derselbe Endpunkt, den der Explorer
 * der Weboberflaeche benutzt.
 *
 * Die App stellt damit nur die drei fest verdrahteten Berichtsabfragen aus
 * ../utils/reportQueries.ts; einen Abfragebaukasten gibt es auf dem Telefon
 * bewusst nicht (siehe Kopf von app/mehr/berichte.tsx).
 */
export function runAnalyticsQuery(
  accessToken: string,
  householdId: string,
  query: AnalyticsQuery,
  signal?: AbortSignal,
): Promise<AnalyticsResult> {
  return authorizedFetch<AnalyticsResult>("/v1/analytics", {
    accessToken,
    householdId,
    method: "POST",
    body: query,
    signal,
  });
}

/**
 * Forecast/Ist/Abweichung je aktivem Vorhaben mit ausgewaehltem Szenario.
 *
 * Dieselbe Aggregation wie hinter der Vorhaben-Detailseite; die App rechnet
 * nichts nach, damit beide Ansichten fuer dasselbe Projekt nie auseinander
 * laufen.
 */
export function getProjectReportsOverview(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<ProjectReportRow[]> {
  return authorizedFetch<ProjectReportRow[]>("/v1/projects/reports-overview", {
    accessToken,
    householdId,
    signal,
  });
}

// Liquiditaetsvorschau (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 5).
// Mobile ist ausdruecklich NUR LESEND (Plan Abschnitt 9): /forecast/settings
// und /forecast/simulate bleiben der Web-App vorbehalten und werden hier
// bewusst nicht angebunden.

/**
 * Basisvorschau. `horizonMonths` steuert zugleich, wie weit die Monatsreihe
 * reicht; `granularity: "day"` fuellt zusaetzlich `daily` (serverseitig auf
 * hoechstens 12 Monate begrenzt).
 *
 * `includeSavings` entspricht dem Schnellschalter "Tagesgeld einbeziehen" der
 * Weboberflaeche - in der App bislang ungenutzt, aber Teil des Endpunkts und
 * deshalb hier durchgereicht statt spaeter nachgeruestet.
 */
export function getForecast(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
  options: {
    horizonMonths?: number;
    granularity?: ForecastGranularity;
    includeSavings?: boolean;
  } = {},
): Promise<ForecastResponse> {
  const query = new URLSearchParams();
  if (options.horizonMonths !== undefined) {
    query.set("horizon_months", String(options.horizonMonths));
  }
  if (options.granularity) query.set("granularity", options.granularity);
  if (options.includeSavings) query.set("include_savings", "true");
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return authorizedFetch<ForecastResponse>(`/v1/forecast${suffix}`, {
    accessToken,
    householdId,
    signal,
  });
}

/** Die naechsten Zahlungen, nach Tagen gruppiert (Tagessumme + Stand danach). */
export function getForecastUpcoming(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
  days = 90,
): Promise<ForecastDayGroup[]> {
  return authorizedFetch<ForecastDayGroup[]>(`/v1/forecast/upcoming?days=${days}`, {
    accessToken,
    householdId,
    signal,
  });
}

// Phase 6: LM Studio / Lokale KI API
export function getAIHealth(
  accessToken: string,
  householdId?: string,
  signal?: AbortSignal,
): Promise<AIHealthResponse> {
  return authorizedFetch<AIHealthResponse>("/v1/ai/health", {
    accessToken,
    householdId,
    signal,
  });
}

export function classifyWithAI(
  accessToken: string,
  householdId: string,
  input: AIClassifyInput,
): Promise<AIClassifyResult> {
  return authorizedFetch<AIClassifyResult>("/v1/ai/classify", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function getAIDecisions(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<AIDecisionLog[]> {
  return authorizedFetch<AIDecisionLog[]>("/v1/ai/decisions", {
    accessToken,
    householdId,
    signal,
  });
}

export function createClassificationRun(
  accessToken: string,
  householdId: string,
  requestId?: string,
): Promise<ClassificationRun> {
  return authorizedFetch<ClassificationRun>("/v1/ai/classify-runs", {
    accessToken,
    householdId,
    method: "POST",
    body: {
      ...(requestId ? { request_id: requestId } : {}),
    },
  });
}

export function getClassificationRun(
  accessToken: string,
  householdId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<ClassificationRun> {
  return authorizedFetch<ClassificationRun>(`/v1/ai/classify-runs/${runId}`, {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Juengster KI-Lauf des Haushalts. Der Zustand des Laufs liegt bewusst
 * vollstaendig auf dem Server (siehe docs/KI-FINANZASSISTENT-PLAN.md,
 * Ausbaustufe 0) - nur so findet die App ihn nach einem Neustart oder auf
 * einem zweiten Geraet wieder.
 */
export async function getLatestClassificationRun(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<ClassificationRun | null> {
  const runs = await authorizedFetch<ClassificationRun[]>("/v1/ai/classify-runs?limit=1", {
    accessToken,
    householdId,
    signal,
  });
  return runs[0] ?? null;
}

export function cancelClassificationRun(
  accessToken: string,
  householdId: string,
  runId: string,
): Promise<ClassificationRun> {
  return authorizedFetch<ClassificationRun>(`/v1/ai/classify-runs/${runId}/cancel`, {
    accessToken,
    householdId,
    method: "POST",
  });
}

// Phase 7: Sparziele, Wunschliste, Vermoegen/Schulden, Nettovermoegen
export function listSavingsGoals(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<SavingsGoal[]> {
  return authorizedFetch<SavingsGoal[]>("/v1/goals", { accessToken, householdId, signal });
}

export function createSavingsGoal(
  accessToken: string,
  householdId: string,
  input: SavingsGoalCreate,
): Promise<SavingsGoal> {
  return authorizedFetch<SavingsGoal>("/v1/goals", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function updateSavingsGoal(
  accessToken: string,
  householdId: string,
  goalId: string,
  input: SavingsGoalUpdate,
): Promise<SavingsGoal> {
  return authorizedFetch<SavingsGoal>(`/v1/goals/${goalId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
  });
}

export function deleteSavingsGoal(
  accessToken: string,
  householdId: string,
  goalId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/goals/${goalId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

export function listWishlistItems(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<WishlistItem[]> {
  return authorizedFetch<WishlistItem[]>("/v1/wishlist", { accessToken, householdId, signal });
}

export function createWishlistItem(
  accessToken: string,
  householdId: string,
  input: WishlistItemCreate,
): Promise<WishlistItem> {
  return authorizedFetch<WishlistItem>("/v1/wishlist", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function updateWishlistItem(
  accessToken: string,
  householdId: string,
  itemId: string,
  input: WishlistItemUpdate,
): Promise<WishlistItem> {
  return authorizedFetch<WishlistItem>(`/v1/wishlist/${itemId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
  });
}

export function deleteWishlistItem(
  accessToken: string,
  householdId: string,
  itemId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/wishlist/${itemId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

export function startWishlistSavingsPlan(
  accessToken: string,
  householdId: string,
  itemId: string,
  input: WishlistSavingsPlanCreate,
): Promise<WishlistSavingsPlanResponse> {
  return authorizedFetch<WishlistSavingsPlanResponse>(`/v1/wishlist/${itemId}/start-savings-plan`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function listAssetsAndDebts(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<AssetDebt[]> {
  return authorizedFetch<AssetDebt[]>("/v1/assets", { accessToken, householdId, signal });
}

export function createAssetOrDebt(
  accessToken: string,
  householdId: string,
  input: AssetDebtCreate,
): Promise<AssetDebt> {
  return authorizedFetch<AssetDebt>("/v1/assets", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function deleteAssetOrDebt(
  accessToken: string,
  householdId: string,
  itemId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/assets/${itemId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

export function getNetWorthReport(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<NetWorthReportResponse> {
  return authorizedFetch<NetWorthReportResponse>("/v1/reports/net-worth", {
    accessToken,
    householdId,
    signal,
  });
}

// Phase 8: Lebensmittelplanung (Essensplan, Einkaufsliste, Wochenbudget)
export function listMealWeeks(
  accessToken: string,
  householdId: string,
  signal?: AbortSignal,
): Promise<MealWeek[]> {
  return authorizedFetch<MealWeek[]>("/v1/meal-weeks", { accessToken, householdId, signal });
}

export function createMealWeek(
  accessToken: string,
  householdId: string,
  input: MealWeekCreate,
): Promise<MealWeek> {
  return authorizedFetch<MealWeek>("/v1/meal-weeks", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function getMealWeekSummary(
  accessToken: string,
  householdId: string,
  mealWeekId: string,
  signal?: AbortSignal,
): Promise<MealWeekSummary> {
  return authorizedFetch<MealWeekSummary>(`/v1/meal-weeks/${mealWeekId}/summary`, {
    accessToken,
    householdId,
    signal,
  });
}

export function listMeals(
  accessToken: string,
  householdId: string,
  mealWeekId: string,
  signal?: AbortSignal,
): Promise<Meal[]> {
  return authorizedFetch<Meal[]>(`/v1/meals?meal_week_id=${mealWeekId}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function createMeal(
  accessToken: string,
  householdId: string,
  input: MealCreate,
): Promise<Meal> {
  return authorizedFetch<Meal>("/v1/meals", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function updateMeal(
  accessToken: string,
  householdId: string,
  mealId: string,
  input: MealUpdate,
): Promise<Meal> {
  return authorizedFetch<Meal>(`/v1/meals/${mealId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
  });
}

export function deleteMeal(
  accessToken: string,
  householdId: string,
  mealId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/meals/${mealId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

export function listShoppingLists(
  accessToken: string,
  householdId: string,
  mealWeekId: string,
  signal?: AbortSignal,
): Promise<ShoppingList[]> {
  return authorizedFetch<ShoppingList[]>(`/v1/shopping-lists?meal_week_id=${mealWeekId}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function createShoppingList(
  accessToken: string,
  householdId: string,
  input: ShoppingListCreate,
): Promise<ShoppingList> {
  return authorizedFetch<ShoppingList>("/v1/shopping-lists", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function listShoppingItems(
  accessToken: string,
  householdId: string,
  shoppingListId: string,
  signal?: AbortSignal,
): Promise<ShoppingItem[]> {
  return authorizedFetch<ShoppingItem[]>(`/v1/shopping-items?shopping_list_id=${shoppingListId}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function createShoppingItem(
  accessToken: string,
  householdId: string,
  input: ShoppingItemCreate,
): Promise<ShoppingItem> {
  return authorizedFetch<ShoppingItem>("/v1/shopping-items", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function updateShoppingItem(
  accessToken: string,
  householdId: string,
  itemId: string,
  input: ShoppingItemUpdate,
): Promise<ShoppingItem> {
  return authorizedFetch<ShoppingItem>(`/v1/shopping-items/${itemId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
  });
}

export function deleteShoppingItem(
  accessToken: string,
  householdId: string,
  itemId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/shopping-items/${itemId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

export function linkPurchase(
  accessToken: string,
  householdId: string,
  shoppingListId: string,
  input: PurchaseLinkCreate,
): Promise<PurchaseLink> {
  return authorizedFetch<PurchaseLink>(`/v1/shopping-lists/${shoppingListId}/purchase-links`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function unlinkPurchase(
  accessToken: string,
  householdId: string,
  purchaseLinkId: string,
): Promise<void> {
  return authorizedFetch<void>(`/v1/purchase-links/${purchaseLinkId}`, {
    accessToken,
    householdId,
    method: "DELETE",
  });
}

// Phase 9: Push-Benachrichtigungen (Geraeteregistrierung). Nutzer-skopiert,
// deshalb ohne householdId - siehe services/api/app/api/v1/routes/devices.py.
export function registerPushToken(
  accessToken: string,
  expoToken: string,
  platform: PushTokenPlatform,
  deviceName?: string | null,
): Promise<PushTokenResponse> {
  return authorizedFetch<PushTokenResponse>("/v1/devices/push-token", {
    accessToken,
    method: "POST",
    body: { expo_token: expoToken, platform, device_name: deviceName ?? null },
  });
}

export function unregisterPushToken(accessToken: string, expoToken: string): Promise<void> {
  return authorizedFetch<void>("/v1/devices/push-token", {
    accessToken,
    method: "DELETE",
    body: { expo_token: expoToken },
  });
}

// ---------------------------------------------------------------------------
// Vorhaben & Grossinvestitionen (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md,
// Abschnitt 9, P5). Bewusst nur die Endpunkte fuer Liste, Detail
// (Uebersicht/Kosten/Finanzierung/Foerderung) und die zwei erlaubten
// Schnellaktionen - dieselben Server-Antworten wie in
// apps/web/src/lib/api.ts, keine lokal nachgerechneten Kennzahlen (P5-Abnahme:
// "Web und Mobile zeigen identische Serverkennzahlen"). Finanzierungsquellen-
// Bearbeitung, Darlehensplaene, Foerderprofil und Szenario-Klon/-Auswahl
// bleiben Web-only.
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
  accessToken: string,
  householdId: string,
  params: ProjectListParams = {},
  signal?: AbortSignal,
): Promise<ProjectListItem[]> {
  return authorizedFetch<ProjectListItem[]>(`/v1/projects${projectListQuery(params)}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function getProject(
  accessToken: string,
  householdId: string,
  projectId: string,
  signal?: AbortSignal,
): Promise<MajorProject> {
  return authorizedFetch<MajorProject>(`/v1/projects/${projectId}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function createProject(
  accessToken: string,
  householdId: string,
  input: MajorProjectCreateInput,
): Promise<MajorProject> {
  return authorizedFetch<MajorProject>("/v1/projects", {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export function listProjectScenarios(
  accessToken: string,
  householdId: string,
  projectId: string,
  signal?: AbortSignal,
): Promise<ProjectScenario[]> {
  return authorizedFetch<ProjectScenario[]>(`/v1/projects/${projectId}/scenarios`, {
    accessToken,
    householdId,
    signal,
  });
}

/**
 * Liveberechnung (POST, aber ohne DB-Aenderung - siehe Plan 7.4). Liefert
 * dieselben Kennzahlen wie web's useScenarioCalculation(), niemals lokal
 * nachgerechnet.
 */
export function calculateProjectScenario(
  accessToken: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
): Promise<ScenarioCalculation> {
  return authorizedFetch<ScenarioCalculation>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/calculate`,
    { accessToken, householdId, method: "POST" },
  );
}

export function listCostItems(
  accessToken: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  signal?: AbortSignal,
): Promise<CostItem[]> {
  return authorizedFetch<CostItem[]>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/cost-items`,
    { accessToken, householdId, signal },
  );
}

/** "Als bezahlt markieren" ist ein minimaler Status-Patch (nur `status`). */
export function updateCostItem(
  accessToken: string,
  householdId: string,
  projectId: string,
  itemId: string,
  input: CostItemUpdateInput,
  version: number,
): Promise<CostItem> {
  return authorizedFetch<CostItem>(`/v1/projects/${projectId}/cost-items/${itemId}`, {
    accessToken,
    householdId,
    method: "PATCH",
    body: input,
    ifMatch: version,
  });
}

export function listFinancingSources(
  accessToken: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  signal?: AbortSignal,
): Promise<FinancingSource[]> {
  return authorizedFetch<FinancingSource[]>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/financing-sources`,
    { accessToken, householdId, signal },
  );
}

/** Letzter Fördercheck-Lauf mit Begründungen; null, wenn noch keiner lief. */
export function getFundingMatches(
  accessToken: string,
  householdId: string,
  projectId: string,
  scenarioId: string,
  signal?: AbortSignal,
): Promise<FundingCheckRunResponse | null> {
  return authorizedFetch<FundingCheckRunResponse | null>(
    `/v1/projects/${projectId}/scenarios/${scenarioId}/funding-matches`,
    { accessToken, householdId, signal },
  );
}

/**
 * Programmdetail mit Versionen/Quellen/Frische (Plan 7.5) - "Foerderquelle
 * oeffnen und Datenstand sehen". Unabhaengig vom zuletzt gelaufenen
 * Fördercheck, deshalb ein eigener Aufruf statt eine Ableitung aus FundingMatch.
 */
export function getFundingProgram(
  accessToken: string,
  householdId: string,
  programId: string,
  signal?: AbortSignal,
): Promise<FundingProgramDetail> {
  return authorizedFetch<FundingProgramDetail>(`/v1/funding/programs/${programId}`, {
    accessToken,
    householdId,
    signal,
  });
}

export function listActuals(
  accessToken: string,
  householdId: string,
  projectId: string,
  itemId: string,
  signal?: AbortSignal,
): Promise<CostActual[]> {
  return authorizedFetch<CostActual[]>(`/v1/projects/${projectId}/cost-items/${itemId}/actuals`, {
    accessToken,
    householdId,
    signal,
  });
}

export function createActual(
  accessToken: string,
  householdId: string,
  projectId: string,
  itemId: string,
  input: CostActualCreateInput,
): Promise<CostActual> {
  return authorizedFetch<CostActual>(`/v1/projects/${projectId}/cost-items/${itemId}/actuals`, {
    accessToken,
    householdId,
    method: "POST",
    body: input,
  });
}

export interface TransactionCandidateParams {
  search?: string;
  accountId?: string;
  dateFrom?: string;
  dateTo?: string;
}

export function listTransactionCandidates(
  accessToken: string,
  householdId: string,
  projectId: string,
  params: TransactionCandidateParams = {},
  signal?: AbortSignal,
): Promise<TransactionCandidate[]> {
  const query = new URLSearchParams();
  if (params.search) query.set("search", params.search);
  if (params.accountId) query.set("account_id", params.accountId);
  if (params.dateFrom) query.set("date_from", params.dateFrom);
  if (params.dateTo) query.set("date_to", params.dateTo);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return authorizedFetch<TransactionCandidate[]>(
    `/v1/projects/${projectId}/transaction-candidates${suffix}`,
    { accessToken, householdId, signal },
  );
}
