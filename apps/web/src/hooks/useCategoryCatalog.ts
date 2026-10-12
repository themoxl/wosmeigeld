import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  applyCategorySuggestions,
  applyStandardPackage,
  archiveCategory,
  archiveCategoryGroup,
  createCategory,
  createCategoryGroup,
  getCategoryCatalog,
  getStandardPackageStatus,
  listCategories,
  mergeCategories,
  moveCategory,
  previewArchiveCategory,
  previewArchiveCategoryGroup,
  previewCategorySuggestions,
  previewMergeCategories,
  previewMoveCategory,
  previewStandardPackage,
  reorderCategories,
  reorderCategoryGroups,
  restoreCategory,
  restoreCategoryGroup,
  updateCategory,
  updateCategoryGroup,
} from "../lib/api";
import type {
  ArchiveApplyInput,
  ArchivePreview,
  CatalogCategory,
  CatalogGroup,
  CategoryCatalog,
  CategoryCreateInput,
  CategoryGroup,
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
  MoveApplyInput,
  MovePreview,
  MergeResult,
  RestoreRequestInput,
  StandardPackageApplyInput,
  StandardPackagePreview,
  StandardPackageResult,
  StandardPackageStatus,
} from "../lib/categoryTypes";

/**
 * Datenzugriff der Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md).
 *
 * Zwei Caches, bewusst getrennt:
 * - `["categories", householdId]` ist der aktive Picker-Baum. Der Schluessel
 *   wird an mehreren Stellen (Buchungen, Regeln, Fixkosten, Review) direkt so
 *   verwendet und bleibt deshalb unveraendert.
 * - `["category-catalog", householdId]` ist die vollstaendige
 *   Verwaltungsansicht inklusive archivierter Eintraege, Versionen und
 *   Nutzungszahlen.
 *
 * JEDE Mutation invalidiert beide - eine Archivierung oder ein Merge aendert
 * den Picker genauso wie die Verwaltung.
 */

export const categoriesQueryKey = (householdId: string) => ["categories", householdId] as const;

export const categoryCatalogQueryKey = (householdId: string) =>
  ["category-catalog", householdId] as const;

export const standardPackageQueryKey = (householdId: string) =>
  ["category-standard-package", householdId] as const;

interface CategoryContext {
  token: string;
  householdId: string;
  enabled: boolean;
  /** owner | editor | viewer | child - Quelle ist GET /v1/me. */
  role: string | null;
  canEdit: boolean;
  isOwner: boolean;
}

export function useCategoryContext(): CategoryContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const role = selectedHousehold?.role ?? null;
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
    role,
    // Viewer und Child duerfen ausschliesslich lesen (Plan: "Viewer und Child
    // sehen die Struktur schreibgeschuetzt und erhalten keine funktionslosen
    // Bearbeitungsschaltflaechen").
    canEdit: role === "owner" || role === "editor",
    isOwner: role === "owner",
  };
}

/** Aktiver Picker-Baum (GET /v1/categories). */
export function useCategoryTree() {
  const { token, householdId, enabled } = useCategoryContext();
  return useQuery<CategoryGroup[]>({
    queryKey: categoriesQueryKey(householdId),
    queryFn: () => listCategories(token, householdId),
    enabled,
  });
}

/** Vollstaendige Verwaltungsansicht (GET /v1/categories/catalog). */
export function useCategoryCatalog(options: { includeArchived?: boolean } = {}) {
  const { token, householdId, enabled } = useCategoryContext();
  const includeArchived = options.includeArchived ?? true;
  return useQuery<CategoryCatalog>({
    queryKey: [...categoryCatalogQueryKey(householdId), { includeArchived }],
    queryFn: () => getCategoryCatalog(token, householdId, { includeArchived, includeUsage: true }),
    enabled,
  });
}

export function useStandardPackageStatus() {
  const { token, householdId, enabled } = useCategoryContext();
  return useQuery<StandardPackageStatus>({
    queryKey: standardPackageQueryKey(householdId),
    queryFn: () => getStandardPackageStatus(token, householdId),
    enabled,
  });
}

/**
 * Laedt Picker-Baum und Verwaltungsansicht neu. Zusaetzlich die Ansichten,
 * die Kategorienamen aufloesen (Buchungen, Budget, Berichte, Regeln,
 * Fixkosten) - ein Merge oder eine Umbenennung wirkt dort sofort.
 */
export function useInvalidateCategories() {
  const queryClient = useQueryClient();
  const { householdId } = useCategoryContext();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: categoriesQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: categoryCatalogQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: standardPackageQueryKey(householdId) });
    void queryClient.invalidateQueries({ queryKey: ["transactions", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["rules", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["budget", householdId] });
  }, [queryClient, householdId]);
}

// ---------------------------------------------------------------------------
// Gruppen
// ---------------------------------------------------------------------------

export function useCreateCategoryGroup() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CatalogGroup, Error, CategoryGroupCreateInput>({
    mutationFn: (input) => createCategoryGroup(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useUpdateCategoryGroup() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<
    CatalogGroup,
    Error,
    { groupId: string; version: number; input: CategoryGroupUpdateInput }
  >({
    mutationFn: ({ groupId, version, input }) =>
      updateCategoryGroup(token, householdId, groupId, input, version),
    onSuccess: invalidate,
  });
}

export function useReorderCategoryGroups() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CategoryCatalog, Error, { taxonomy_revision: number; ordered_ids: string[] }>({
    mutationFn: (input) => reorderCategoryGroups(token, householdId, input),
    onSuccess: invalidate,
  });
}

/**
 * Vorschauen sind bewusst Queries, keine Mutationen: Sie laufen beim Öffnen
 * des Dialogs von selbst, lassen sich über `refetch()` gezielt erneuern
 * (z. B. nach 409 `category_preview_stale`) und aktualisieren den Zustand
 * niemals während des Renderns. `gcTime`/`staleTime` sind 0, damit nie eine
 * bereits abgelaufene `operation_id` aus dem Cache wiederverwendet wird.
 */
const PREVIEW_QUERY_OPTIONS = { gcTime: 0, staleTime: 0, retry: false } as const;

export function useArchiveCategoryGroupPreview(groupId: string | null) {
  const { token, householdId, enabled } = useCategoryContext();
  return useQuery<GroupArchivePreview>({
    queryKey: ["category-group-archive-preview", householdId, groupId],
    queryFn: () => previewArchiveCategoryGroup(token, householdId, groupId as string),
    enabled: enabled && Boolean(groupId),
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useArchiveCategoryGroup() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CatalogGroup, Error, { groupId: string; input: GroupArchiveApplyInput }>({
    mutationFn: ({ groupId, input }) => archiveCategoryGroup(token, householdId, groupId, input),
    onSuccess: invalidate,
  });
}

export function useRestoreCategoryGroup() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CatalogGroup, Error, string>({
    mutationFn: (groupId) => restoreCategoryGroup(token, householdId, groupId),
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------------
// Kategorien
// ---------------------------------------------------------------------------

export function useCreateCategory() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CatalogCategory, Error, CategoryCreateInput>({
    mutationFn: (input) => createCategory(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useUpdateCategory() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<
    CatalogCategory,
    Error,
    { categoryId: string; version: number; input: CategoryUpdateInput }
  >({
    mutationFn: ({ categoryId, version, input }) =>
      updateCategory(token, householdId, categoryId, input, version),
    onSuccess: invalidate,
  });
}

export function useReorderCategories() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<
    CategoryCatalog,
    Error,
    { taxonomy_revision: number; ordered_ids: string[]; group_id: string }
  >({
    mutationFn: (input) => reorderCategories(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useMoveCategoryPreview(categoryId: string, targetGroupId: string) {
  const { token, householdId, enabled } = useCategoryContext();
  return useQuery<MovePreview>({
    queryKey: ["category-move-preview", householdId, categoryId, targetGroupId],
    queryFn: () => previewMoveCategory(token, householdId, categoryId, targetGroupId),
    enabled: enabled && Boolean(categoryId && targetGroupId),
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useMoveCategory() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CatalogCategory, Error, { categoryId: string; input: MoveApplyInput }>({
    mutationFn: ({ categoryId, input }) => moveCategory(token, householdId, categoryId, input),
    onSuccess: invalidate,
  });
}

export function useArchiveCategoryPreview(categoryId: string | null) {
  const { token, householdId, enabled } = useCategoryContext();
  return useQuery<ArchivePreview>({
    queryKey: ["category-archive-preview", householdId, categoryId],
    queryFn: () => previewArchiveCategory(token, householdId, categoryId as string),
    enabled: enabled && Boolean(categoryId),
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useArchiveCategory() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CatalogCategory, Error, { categoryId: string; input: ArchiveApplyInput }>({
    mutationFn: ({ categoryId, input }) => archiveCategory(token, householdId, categoryId, input),
    onSuccess: invalidate,
  });
}

export function useRestoreCategory() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CatalogCategory, Error, { categoryId: string; input?: RestoreRequestInput }>({
    mutationFn: ({ categoryId, input }) =>
      restoreCategory(token, householdId, categoryId, input ?? {}),
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------------
// Zusammenfuehren (owner-only) und Standardpaket
// ---------------------------------------------------------------------------

export function useMergeCategoriesPreview(sourceCategoryId: string, targetCategoryId: string) {
  const { token, householdId, enabled } = useCategoryContext();
  return useQuery<MergePreview>({
    queryKey: ["category-merge-preview", householdId, sourceCategoryId, targetCategoryId],
    queryFn: () =>
      previewMergeCategories(token, householdId, {
        source_category_id: sourceCategoryId,
        target_category_id: targetCategoryId,
      }),
    enabled: enabled && Boolean(sourceCategoryId && targetCategoryId),
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useMergeCategories() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<MergeResult, Error, MergeApplyInput>({
    mutationFn: (input) => mergeCategories(token, householdId, input),
    onSuccess: invalidate,
  });
}

export function useStandardPackagePreview(active: boolean) {
  const { token, householdId, enabled } = useCategoryContext();
  return useQuery<StandardPackagePreview>({
    queryKey: ["category-standard-package-preview", householdId],
    queryFn: () => previewStandardPackage(token, householdId),
    enabled: enabled && active,
    ...PREVIEW_QUERY_OPTIONS,
  });
}

export function useApplyStandardPackage() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<StandardPackageResult, Error, StandardPackageApplyInput>({
    mutationFn: (input) => applyStandardPackage(token, householdId, input),
    onSuccess: invalidate,
  });
}

// ---------------------------------------------------------------------------
// KI-Kategorievorschlaege
// ---------------------------------------------------------------------------

/**
 * Vorschau der KI-Vorschlaege. Bewusst KEINE Query mit automatischem Start:
 * der Aufruf laesst ein lokales Modell mehrere Cluster beurteilen und darf
 * nicht beim blossen Oeffnen der Seite oder bei einem Refetch loslaufen. Als
 * Mutation startet er genau dann, wenn der Nutzer ihn ausloest.
 */
export function usePreviewCategorySuggestions() {
  const { token, householdId } = useCategoryContext();
  return useMutation<CategorySuggestionPreview, Error, void>({
    mutationFn: () => previewCategorySuggestions(token, householdId),
  });
}

export function useApplyCategorySuggestions() {
  const { token, householdId } = useCategoryContext();
  const invalidate = useInvalidateCategories();
  return useMutation<CategorySuggestionResult, Error, CategorySuggestionApplyInput>({
    mutationFn: (input) => applyCategorySuggestions(token, householdId, input),
    onSuccess: invalidate,
  });
}

/**
 * Idempotenzschluessel fuer Merge und Paket-Apply. `crypto.randomUUID` fehlt
 * in aelteren Testumgebungen, deshalb mit Rueckfallebene (analog zu
 * hooks/useClassificationRun.ts).
 */
export function newRequestId(): string {
  const webCrypto = globalThis.crypto;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
