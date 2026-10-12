import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import {
  applyBulkCategory,
  applyCategoryArchive,
  applyCategoryGroupArchive,
  applyCategoryMerge,
  applyCategoryMove,
  applyStandardPackage,
  createCategory,
  createCategoryGroup,
  getCategoryCatalog,
  getStandardPackageStatus,
  previewBulkCategory,
  previewCategoryArchive,
  previewCategoryGroupArchive,
  previewCategoryMerge,
  previewCategoryMove,
  previewStandardPackage,
  reorderCategories,
  reorderCategoryGroups,
  restoreCategory,
  restoreCategoryGroup,
  updateCategory,
  updateCategoryGroup,
} from "../api/client";
import type {
  ArchiveApply,
  BulkCategoryInput,
  BulkCategoryResult,
  CatalogCategory,
  CatalogGroup,
  CategoryCatalog,
  CategoryCreate,
  CategoryGroupCreate,
  CategoryGroupUpdate,
  CategoryUpdate,
  GroupArchiveApply,
  MergeApply,
  MergeRequest,
  MoveApply,
  RestoreRequest,
  StandardPackageApply,
} from "../api/categoryTypes";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { flush } from "../offline/queueProcessor";
import { enqueueEntries, enqueueEntry, generateId } from "../offline/queueStore";
import type { QueueEntry } from "../offline/queueTypes";
import { recentCategoryIdsFromTransactions } from "../utils/categorySearch";
import { useTransactions } from "./useTransactions";

// Queries und Mutationen der Kategorienverwaltung
// (docs/KATEGORISIERUNGSPLAN.md). Aufbau wie useGoals.ts/useAI.ts: je ein
// schmaler Hook pro Endpunkt, Token und Haushalt aus den Providern.

export const CATEGORY_CATALOG_QUERY_KEY = "category-catalog" as const;
export const STANDARD_PACKAGE_QUERY_KEY = "category-standard-package" as const;

function useApiContext() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return {
    accessToken,
    householdId: selectedHouseholdId,
    requireContext: () => {
      if (!accessToken) throw new Error("Nicht angemeldet.");
      if (!selectedHouseholdId) throw new Error("Kein Haushalt ausgewählt.");
      return { accessToken, householdId: selectedHouseholdId };
    },
  };
}

/**
 * Rollen des Haushalts auf die im Plan geforderten Rechte abgebildet:
 * Owner und Editor duerfen verwalten, Viewer und Child sehen die Struktur
 * schreibgeschuetzt und bekommen keine funktionslosen Schaltflaechen. Der
 * irreversible Merge und das Paket-Upgrade sind zusaetzlich owner-only.
 */
export function useCategoryPermissions(): {
  role: string | null;
  canManage: boolean;
  isOwner: boolean;
} {
  const { selectedHousehold } = useHousehold();
  const role = selectedHousehold?.role ?? null;
  return {
    role,
    canManage: role === "owner" || role === "editor",
    isOwner: role === "owner",
  };
}

/** Vollstaendige Verwaltungsansicht (GET /v1/categories/catalog). */
export function useCategoryCatalog(options?: {
  includeArchived?: boolean;
  includeUsage?: boolean;
}) {
  const { accessToken, householdId } = useApiContext();
  const includeArchived = options?.includeArchived ?? true;
  const includeUsage = options?.includeUsage ?? true;

  return useQuery({
    queryKey: [CATEGORY_CATALOG_QUERY_KEY, householdId, includeArchived, includeUsage],
    queryFn: ({ signal }) =>
      getCategoryCatalog(
        accessToken as string,
        householdId as string,
        { includeArchived, includeUsage },
        signal,
      ),
    enabled: !!accessToken && !!householdId,
  });
}

/** Installierte und verfuegbare Taxonomieversion (GET /v1/categories/standard-package). */
export function useStandardPackageStatus() {
  const { accessToken, householdId } = useApiContext();

  return useQuery({
    queryKey: [STANDARD_PACKAGE_QUERY_KEY, householdId],
    queryFn: ({ signal }) =>
      getStandardPackageStatus(accessToken as string, householdId as string, signal),
    enabled: !!accessToken && !!householdId,
  });
}

/**
 * Nach jeder Strukturaenderung muessen BEIDE Baeume neu geladen werden: die
 * Verwaltungsansicht und der Picker-Baum aus GET /v1/categories. Sonst
 * bietet ein Picker eine gerade archivierte Kategorie weiter an.
 */
function useInvalidateCategories(): () => void {
  const queryClient = useQueryClient();
  const { householdId } = useApiContext();
  return () => {
    void queryClient.invalidateQueries({ queryKey: [CATEGORY_CATALOG_QUERY_KEY, householdId] });
    void queryClient.invalidateQueries({ queryKey: ["categories", householdId] });
    void queryClient.invalidateQueries({ queryKey: [STANDARD_PACKAGE_QUERY_KEY, householdId] });
  };
}

export function useCreateCategory() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CatalogCategory, Error, CategoryCreate>({
    mutationFn: (input) => {
      const { accessToken, householdId } = requireContext();
      return createCategory(accessToken, householdId, input);
    },
    onSuccess: invalidate,
  });
}

export interface UpdateCategoryArgs {
  categoryId: string;
  /** Zuletzt gelesene `version` - wird als `If-Match` gesendet (428 ohne, 409 veraltet). */
  version: number;
  patch: CategoryUpdate;
}

export function useUpdateCategory() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CatalogCategory, Error, UpdateCategoryArgs>({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return updateCategory(accessToken, householdId, args.categoryId, args.patch, args.version);
    },
    onSuccess: invalidate,
  });
}

export function useCreateCategoryGroup() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CatalogGroup, Error, CategoryGroupCreate>({
    mutationFn: (input) => {
      const { accessToken, householdId } = requireContext();
      return createCategoryGroup(accessToken, householdId, input);
    },
    onSuccess: invalidate,
  });
}

export interface UpdateCategoryGroupArgs {
  groupId: string;
  version: number;
  patch: CategoryGroupUpdate;
}

export function useUpdateCategoryGroup() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CatalogGroup, Error, UpdateCategoryGroupArgs>({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return updateCategoryGroup(accessToken, householdId, args.groupId, args.patch, args.version);
    },
    onSuccess: invalidate,
  });
}

export interface ReorderArgs {
  taxonomyRevision: number;
  orderedIds: string[];
  /** Nur beim Sortieren INNERHALB einer Gruppe gesetzt. */
  groupId?: string;
}

export function useReorderCategories() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CategoryCatalog, Error, ReorderArgs>({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return reorderCategories(accessToken, householdId, {
        taxonomy_revision: args.taxonomyRevision,
        ordered_ids: args.orderedIds,
        group_id: args.groupId ?? null,
      });
    },
    onSuccess: invalidate,
  });
}

export function useReorderCategoryGroups() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CategoryCatalog, Error, ReorderArgs>({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return reorderCategoryGroups(accessToken, householdId, {
        taxonomy_revision: args.taxonomyRevision,
        ordered_ids: args.orderedIds,
      });
    },
    onSuccess: invalidate,
  });
}

export function usePreviewCategoryMove() {
  const { requireContext } = useApiContext();

  return useMutation({
    mutationFn: (args: { categoryId: string; targetGroupId: string }) => {
      const { accessToken, householdId } = requireContext();
      return previewCategoryMove(accessToken, householdId, args.categoryId, args.targetGroupId);
    },
  });
}

export function useApplyCategoryMove() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CatalogCategory, Error, { categoryId: string; apply: MoveApply }>({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return applyCategoryMove(accessToken, householdId, args.categoryId, args.apply);
    },
    onSuccess: invalidate,
  });
}

export function usePreviewCategoryArchive() {
  const { requireContext } = useApiContext();

  return useMutation({
    mutationFn: (categoryId: string) => {
      const { accessToken, householdId } = requireContext();
      return previewCategoryArchive(accessToken, householdId, categoryId);
    },
  });
}

export function useApplyCategoryArchive() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<
    CatalogCategory,
    Error,
    { categoryId: string; version: number; apply: ArchiveApply }
  >({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return applyCategoryArchive(
        accessToken,
        householdId,
        args.categoryId,
        args.apply,
        args.version,
      );
    },
    onSuccess: invalidate,
  });
}

export function useRestoreCategory() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CatalogCategory, Error, { categoryId: string; input?: RestoreRequest }>({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return restoreCategory(accessToken, householdId, args.categoryId, args.input ?? {});
    },
    onSuccess: invalidate,
  });
}

export function usePreviewCategoryGroupArchive() {
  const { requireContext } = useApiContext();

  return useMutation({
    mutationFn: (groupId: string) => {
      const { accessToken, householdId } = requireContext();
      return previewCategoryGroupArchive(accessToken, householdId, groupId);
    },
  });
}

export function useApplyCategoryGroupArchive() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<
    CatalogGroup,
    Error,
    { groupId: string; version: number; apply: GroupArchiveApply }
  >({
    mutationFn: (args) => {
      const { accessToken, householdId } = requireContext();
      return applyCategoryGroupArchive(
        accessToken,
        householdId,
        args.groupId,
        args.apply,
        args.version,
      );
    },
    onSuccess: invalidate,
  });
}

export function useRestoreCategoryGroup() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation<CatalogGroup, Error, string>({
    mutationFn: (groupId) => {
      const { accessToken, householdId } = requireContext();
      return restoreCategoryGroup(accessToken, householdId, groupId);
    },
    onSuccess: invalidate,
  });
}

export function usePreviewCategoryMerge() {
  const { requireContext } = useApiContext();

  return useMutation({
    mutationFn: (input: MergeRequest) => {
      const { accessToken, householdId } = requireContext();
      return previewCategoryMerge(accessToken, householdId, input);
    },
  });
}

export function useApplyCategoryMerge() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation({
    mutationFn: (input: MergeApply) => {
      const { accessToken, householdId } = requireContext();
      return applyCategoryMerge(accessToken, householdId, input);
    },
    onSuccess: invalidate,
  });
}

export function usePreviewStandardPackage() {
  const { requireContext } = useApiContext();

  return useMutation({
    mutationFn: () => {
      const { accessToken, householdId } = requireContext();
      return previewStandardPackage(accessToken, householdId);
    },
  });
}

export function useApplyStandardPackage() {
  const { requireContext } = useApiContext();
  const invalidate = useInvalidateCategories();

  return useMutation({
    mutationFn: (input: StandardPackageApply) => {
      const { accessToken, householdId } = requireContext();
      return applyStandardPackage(accessToken, householdId, input);
    },
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------------
// Zuordnung an Buchungen
// ---------------------------------------------------------------------------

export interface AssignCategoryArgs {
  transactionId: string;
  /** Zuletzt gesehene Serverversion der Buchung (Transaction.version). */
  expectedVersion: number;
  categoryId: string | null;
}

/**
 * Kategorie genau einer Buchung setzen - immer ueber die Offline-Write-Queue
 * (wie alle Buchungsschreibvorgaenge, siehe useTransactionMutations.ts). Es
 * gibt bewusst keinen zweiten, direkten Online-Pfad: der wuerde nur unter
 * guten Bedingungen getestet und im schlechten Fall die Reihenfolge
 * gegenueber der Queue vertauschen.
 */
export function useAssignTransactionCategory() {
  const { selectedHouseholdId } = useHousehold();

  return useMutation({
    mutationFn: async (args: AssignCategoryArgs): Promise<void> => {
      if (!selectedHouseholdId) throw new Error("Kein Haushalt ausgewählt.");
      await enqueueEntry(buildCategoryQueueEntry(selectedHouseholdId, args));
      void flush();
    },
  });
}

/**
 * Mehrere Buchungen offline zuordnen: pro Buchung EIN Queue-Eintrag, damit
 * ein einzelner Konflikt die uebrigen nicht blockiert und in der vorhandenen
 * Konfliktansicht (app/mehr/sync-konflikte.tsx) einzeln entschieden werden
 * kann. Es gibt bewusst keinen Offline-Bulk-Eintrag: der waere serverseitig
 * ein einziger Request, dessen Teilergebnisse die Queue nicht abbilden kann.
 */
export function useQueueBulkCategory() {
  const { selectedHouseholdId } = useHousehold();

  return useMutation({
    mutationFn: async (args: {
      categoryId: string | null;
      transactions: { id: string; version: number }[];
    }): Promise<number> => {
      if (!selectedHouseholdId) throw new Error("Kein Haushalt ausgewählt.");
      // Ein einziger Schreibvorgang fuer bis zu 500 Eintraege - siehe
      // enqueueEntries() in ../offline/queueStore.ts.
      await enqueueEntries(
        args.transactions.map((transaction) =>
          buildCategoryQueueEntry(selectedHouseholdId, {
            transactionId: transaction.id,
            expectedVersion: transaction.version,
            categoryId: args.categoryId,
          }),
        ),
      );
      void flush();
      return args.transactions.length;
    },
  });
}

function buildCategoryQueueEntry(householdId: string, args: AssignCategoryArgs): QueueEntry {
  return {
    entryId: generateId(),
    localId: args.transactionId,
    householdId,
    status: "pending",
    attempts: 0,
    createdAt: new Date().toISOString(),
    nextAttemptAt: 0,
    kind: "category",
    payload: { category_id: args.categoryId },
    expectedVersion: args.expectedVersion,
  };
}

/** Servervorschau der Mehrfachzuordnung - laeuft nur online. */
export function useBulkCategoryPreview() {
  const { requireContext } = useApiContext();

  return useMutation<BulkCategoryResult, Error, BulkCategoryInput>({
    mutationFn: (input) => {
      const { accessToken, householdId } = requireContext();
      return previewBulkCategory(accessToken, householdId, input);
    },
  });
}

/** Mehrfachzuordnung anwenden - liefert das Ergebnis pro Buchung zurueck. */
export function useBulkCategoryApply() {
  const { requireContext } = useApiContext();
  const queryClient = useQueryClient();

  return useMutation<BulkCategoryResult, Error, BulkCategoryInput>({
    mutationFn: (input) => {
      const { accessToken, householdId } = requireContext();
      return applyBulkCategory(accessToken, householdId, input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
  });
}

/**
 * Zuletzt verwendete Kategorien des Haushalts fuer den Picker. Bewusst aus
 * der ohnehin geladenen Buchungsliste abgeleitet statt ueber einen eigenen
 * Endpunkt - die Liste liegt auf allen relevanten Screens bereits im Cache.
 */
export function useRecentCategoryIds(limit = 5): string[] {
  const { data } = useTransactions({ limit: 100 });
  return useMemo(() => recentCategoryIdsFromTransactions(data ?? [], limit), [data, limit]);
}
