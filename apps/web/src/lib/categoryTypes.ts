// Handgepflegte TypeScript-Spiegelung von
// services/api/app/schemas/category.py (Kategorienverwaltung,
// docs/KATEGORISIERUNGSPLAN.md).
//
// Bewusst NICHT aus packages/contracts abgeleitet: der Generator wird erst
// nach dem Backend-Merge neu erzeugt, die Oberflaeche braucht die Felder aber
// schon jetzt. Sobald packages/contracts nachgezogen ist, koennen die
// Deklarationen hier eins zu eins gegen die generierten Schemas getauscht
// werden - Feldnamen und Literal-Werte sind absichtlich identisch zum
// Pydantic-Vertrag.

import type { AIWarning } from "./types";

export type CategoryDefaultType = "income" | "expense" | "transfer";
export type CategoryBudgetBucket = "needs" | "wants" | "savings";
export type CategoryOrigin = "standard" | "user" | "legacy";

/** Obergrenze aus dem Plan ("Maximal 500 Buchungen pro Vorgang"). */
export const MAX_BULK_TRANSACTIONS = 500;

// ---------------------------------------------------------------------------
// Picker-Baum (GET /v1/categories) - rueckwaertskompatibel, nur additiv
// erweitert.
// ---------------------------------------------------------------------------

export interface Category {
  id: string;
  group_id: string;
  name: string;
  /** 'income' | 'expense' | 'transfer' - backendseitig plain string. */
  default_type: string | null;
  /** 'needs' | 'wants' | 'savings' - backendseitig plain string. */
  budget_bucket: string | null;
  is_archived: boolean;
  icon: string | null;
  color: string | null;
  is_budgeted: boolean;
  sort_order: number;
  synonyms: string[];
  description: string | null;
  semantic_key: string | null;
  version: number;
  /** Steuerrelevanz als getrennte Dimension (Migration 0010). */
  is_tax_deductible: boolean;
}

export interface CategoryGroup {
  id: string;
  name: string;
  sort_order: number;
  categories: Category[];
  is_archived: boolean;
  icon: string | null;
  color: string | null;
  semantic_key: string | null;
  version: number;
}

// ---------------------------------------------------------------------------
// Kategorie anhand ihrer ID auflösen
// ---------------------------------------------------------------------------
//
// `category_id`-Felder (an Vertraegen, Buchungen, ...) tragen nirgends einen
// mitgelieferten Namen - der wuerde bei einer spaeteren Umbenennung sofort
// veralten. Stattdessen wird gegen den ohnehin geladenen Baum aufgeloest;
// diese Funktion ist die eine Stelle dafuer, damit "Gruppe · Name" und die
// Farbe ueberall gleich zusammengesetzt werden.

export const UNCATEGORIZED_LABEL = "Nicht kategorisiert";

export interface ResolvedCategory {
  id: string;
  name: string;
  color: string | null;
  icon: string | null;
  groupId: string;
  groupName: string;
  groupColor: string | null;
  /** "Gruppe · Kategorie", wie im CategoryPicker. */
  path: string;
}

export function buildCategoryIndex(
  groups: CategoryGroup[] | undefined,
): Map<string, ResolvedCategory> {
  const index = new Map<string, ResolvedCategory>();
  for (const group of groups ?? []) {
    for (const category of group.categories) {
      index.set(category.id, {
        id: category.id,
        name: category.name,
        color: category.color,
        icon: category.icon,
        groupId: group.id,
        groupName: group.name,
        groupColor: group.color,
        path: `${group.name} · ${category.name}`,
      });
    }
  }
  return index;
}

// ---------------------------------------------------------------------------
// Verwaltungsansicht (GET /v1/categories/catalog)
// ---------------------------------------------------------------------------

/**
 * Referenzzahlen je Tabelle. Der Plan trennt historische Referenzen (bleiben
 * erhalten) von zukunftswirksamen (muessen bei Archivierung ersetzt oder
 * geloest werden) - deshalb wird derselbe Typ zweimal verwendet.
 */
export interface CategoryReferenceCounts {
  transaction_splits: number;
  payee_defaults: number;
  classification_rules: number;
  contracts: number;
  budget_allocations: number;
  budget_rollovers: number;
  savings_goals: number;
  classification_decisions: number;
  /** computed_field im Pydantic-Modell - kommt immer mit. */
  total: number;
}

export interface CatalogCategory extends Category {
  origin: CategoryOrigin;
  taxonomy_version: number | null;
  archived_at: string | null;
  merged_into_category_id: string | null;
  merged_into_name: string | null;
  updated_at: string | null;
  usage: CategoryReferenceCounts | null;
}

export interface CatalogGroup {
  id: string;
  name: string;
  sort_order: number;
  is_archived: boolean;
  icon: string | null;
  color: string | null;
  semantic_key: string | null;
  origin: CategoryOrigin;
  version: number;
  updated_at: string | null;
  active_category_count: number;
  archived_category_count: number;
  categories: CatalogCategory[];
}

export interface CategoryCatalog {
  /** Baum-ETag: jede strukturelle Aenderung erhoeht die Revision. */
  taxonomy_revision: number;
  installed_taxonomy_version: number | null;
  available_taxonomy_version: number | null;
  groups: CatalogGroup[];
}

// ---------------------------------------------------------------------------
// Schreibmodelle
// ---------------------------------------------------------------------------

export interface CategoryGroupCreateInput {
  name: string;
  icon?: string | null;
  color?: string | null;
  sort_order?: number | null;
}

export interface CategoryGroupUpdateInput {
  name?: string;
  icon?: string | null;
  color?: string | null;
}

export interface CategoryCreateInput {
  group_id: string;
  name: string;
  default_type?: CategoryDefaultType | null;
  budget_bucket?: CategoryBudgetBucket | null;
  is_budgeted?: boolean;
  icon?: string | null;
  color?: string | null;
  description?: string | null;
  synonyms?: string[];
  is_tax_deductible?: boolean;
}

/**
 * `group_id` fehlt bewusst: ein Gruppenwechsel wirkt rueckwirkend auf
 * Berichte und laeuft ausschliesslich ueber move-preview/move.
 */
export interface CategoryUpdateInput {
  name?: string;
  default_type?: CategoryDefaultType | null;
  budget_bucket?: CategoryBudgetBucket | null;
  is_budgeted?: boolean;
  icon?: string | null;
  color?: string | null;
  description?: string | null;
  synonyms?: string[];
  is_tax_deductible?: boolean;
  /** Explizites Loeschen - `undefined` allein bedeutet "nicht angefasst". */
  clear_fields?: ("budget_bucket" | "icon" | "color" | "description" | "default_type")[];
}

export interface ReorderRequestInput {
  taxonomy_revision: number;
  ordered_ids: string[];
  group_id?: string | null;
}

// ---------------------------------------------------------------------------
// Operationen mit Vorschau
// ---------------------------------------------------------------------------

export interface OperationPreviewBase {
  operation_id: string;
  expires_at: string;
}

export interface MovePreview extends OperationPreviewBase {
  category_id: string;
  category_name: string;
  source_group_id: string;
  source_group_name: string;
  target_group_id: string;
  target_group_name: string;
  affected_splits: number;
  first_booked_date: string | null;
  last_booked_date: string | null;
  affected_budget_allocations: number;
  hint: string;
}

export interface MoveApplyInput {
  operation_id: string;
  target_group_id: string;
}

export interface ArchivePreview extends OperationPreviewBase {
  category_id: string;
  category_name: string;
  /** Bleiben unveraendert erhalten (Historie). */
  historical: CategoryReferenceCounts;
  /** Wirken in die Zukunft und muessen ersetzt oder geloest werden. */
  forward: CategoryReferenceCounts;
  requires_resolution: boolean;
  blocking_reason: string | null;
}

export interface ArchiveApplyInput {
  operation_id: string;
  replacement_category_id?: string | null;
}

export interface GroupArchivePreview extends OperationPreviewBase {
  group_id: string;
  group_name: string;
  active_category_ids: string[];
  active_category_names: string[];
  requires_resolution: boolean;
}

export interface GroupArchiveApplyInput {
  operation_id: string;
  archive_categories?: boolean;
  replacement_category_id?: string | null;
}

export interface RestoreRequestInput {
  target_group_id?: string | null;
  restore_group?: boolean;
  new_name?: string | null;
}

export interface MergeCollision {
  kind: "budget_allocation" | "rollover_rule" | "duplicate_rule" | "ai_proposal";
  detail: string;
  count: number;
}

export interface MergePreview extends OperationPreviewBase {
  source_category_id: string;
  source_category_name: string;
  target_category_id: string;
  target_category_name: string;
  references: CategoryReferenceCounts;
  transactions_to_bump: number;
  collisions: MergeCollision[];
  blocking_reason: string | null;
}

export interface MergeRequestInput {
  source_category_id: string;
  target_category_id: string;
}

export interface MergeApplyInput {
  operation_id: string;
  request_id: string;
}

export interface MergeResult {
  source_category_id: string;
  target_category_id: string;
  moved_references: CategoryReferenceCounts;
  bumped_transactions: number;
  merged_budget_allocations: number;
  deactivated_rules: number;
  idempotent_replay: boolean;
}

// ---------------------------------------------------------------------------
// Standardpaket
// ---------------------------------------------------------------------------

export interface StandardPackageStatus {
  installed_version: number | null;
  available_version: number | null;
  installed_at: string | null;
  taxonomy_revision: number;
  upgrade_available: boolean;
}

export type StandardPackageEntryStatus =
  "new" | "already_present" | "possible_match" | "name_conflict";

export interface StandardPackageEntry {
  semantic_key: string;
  name: string;
  group_semantic_key: string;
  group_name: string;
  default_type: string | null;
  budget_bucket: string | null;
  status: StandardPackageEntryStatus;
  existing_category_id: string | null;
  existing_category_name: string | null;
}

export interface StandardPackagePreview extends OperationPreviewBase {
  taxonomy_version: number;
  taxonomy_revision: number;
  new_groups: string[];
  entries: StandardPackageEntry[];
  new_count: number;
  already_present_count: number;
  possible_match_count: number;
  name_conflict_count: number;
}

export interface StandardPackageApplyInput {
  operation_id: string;
  request_id?: string | null;
  /** semantic_key -> vorhandene Kategorie, die der Nutzer bestaetigt hat. */
  confirmed_matches: Record<string, string>;
}

export interface StandardPackageResult {
  taxonomy_version: number;
  created_groups: number;
  created_categories: number;
  linked_existing: number;
  skipped_name_conflicts: number;
  idempotent_replay: boolean;
}

// ---------------------------------------------------------------------------
// KI-Kategorievorschlaege
// ---------------------------------------------------------------------------
// Spiegelung von schemas/category.py, Abschnitt "KI-Kategorievorschlaege".

export type SuggestionKind = "new_category" | "existing_category";

export interface SuggestionSample {
  transaction_id: string;
  booked_date: string;
  amount_minor: number;
  payee_label: string | null;
  note: string | null;
}

export interface CategorySuggestion {
  cluster_key: string;
  payee_label: string;
  transaction_count: number;
  total_amount_minor: number;
  first_booked_date: string;
  last_booked_date: string;
  samples: SuggestionSample[];

  kind: SuggestionKind;
  confidence: number;
  reason: string;

  existing_category_id: string | null;
  existing_category_name: string | null;

  proposed_name: string | null;
  proposed_group_id: string | null;
  proposed_group_name: string | null;
  proposed_default_type: CategoryDefaultType | null;
  proposed_budget_bucket: CategoryBudgetBucket | null;
  proposed_icon: string | null;
  proposed_synonyms: string[];
  proposed_is_tax_deductible: boolean;
}

export interface CategorySuggestionPreview extends OperationPreviewBase {
  taxonomy_revision: number;
  model_name: string | null;
  uncategorized_transactions: number;
  clustered_transactions: number;
  skipped_singletons: number;
  /** Cluster ohne verwertbare Modellantwort (Teilergebnis); fehlt bei aelteren Laeufen. */
  unassessed_clusters?: number;
  /** Benannte Befunde des KI-Kerns zum Teilergebnis (Grund + Klartext). */
  warnings?: AIWarning[];
  suggestions: CategorySuggestion[];
}

export interface CategorySuggestionAcceptInput {
  cluster_key: string;
  name?: string | null;
  group_id?: string | null;
  budget_bucket?: CategoryBudgetBucket | null;
  is_tax_deductible?: boolean | null;
  use_existing_category_id?: string | null;
}

export interface CategorySuggestionApplyInput {
  operation_id: string;
  request_id?: string | null;
  accepted: CategorySuggestionAcceptInput[];
  assign_transactions: boolean;
}

export interface CategorySuggestionResultItem {
  cluster_key: string;
  category_id: string;
  category_name: string;
  created: boolean;
  assigned_transactions: number;
  skipped_transactions: number;
}

export interface CategorySuggestionResult {
  created_categories: number;
  linked_existing: number;
  assigned_transactions: number;
  skipped_transactions: number;
  items: CategorySuggestionResultItem[];
  idempotent_replay: boolean;
}

// ---------------------------------------------------------------------------
// Zuordnung an Buchungen
// ---------------------------------------------------------------------------

export interface TransactionCategoryUpdateInput {
  category_id: string | null;
  expected_version?: number | null;
}

export type BulkItemStatus = "applied" | "unchanged" | "skipped" | "conflict";

export type BulkSkipReason =
  | "split_transaction"
  | "already_assigned"
  | "version_conflict"
  | "not_found"
  | "archived_transaction"
  | "category_inactive";

export interface BulkCategoryItem {
  transaction_id: string;
  status: BulkItemStatus;
  reason: BulkSkipReason | null;
  current_version: number | null;
}

export interface BulkCategoryInput {
  transaction_ids: string[];
  category_id: string | null;
  /** Zuletzt gesehene Version je Buchung (transaction_id -> version). */
  expected_versions?: Record<string, number>;
}

export interface BulkCategoryResult {
  applied: number;
  unchanged: number;
  skipped: number;
  conflict: number;
  items: BulkCategoryItem[];
}

// ---------------------------------------------------------------------------
// Anzeigetexte (an einer Stelle, damit Tabelle, Vorschau und Ergebnis in
// Web und Tests denselben Wortlaut verwenden)
// ---------------------------------------------------------------------------

export const BULK_STATUS_LABELS: Record<BulkItemStatus, string> = {
  applied: "wird geändert",
  unchanged: "bereits so zugeordnet",
  skipped: "übersprungen",
  conflict: "Versionskonflikt",
};

export const BULK_REASON_LABELS: Record<BulkSkipReason, string> = {
  split_transaction: "Split-Buchung",
  already_assigned: "bereits so zugeordnet",
  version_conflict: "Versionskonflikt",
  not_found: "nicht gefunden",
  archived_transaction: "archiviert",
  category_inactive: "Kategorie archiviert",
};

export function bulkItemLabel(item: BulkCategoryItem): string {
  if (item.reason) {
    return BULK_REASON_LABELS[item.reason];
  }
  return BULK_STATUS_LABELS[item.status];
}

export const DEFAULT_TYPE_LABELS: Record<CategoryDefaultType, string> = {
  income: "Einnahme",
  expense: "Ausgabe",
  transfer: "Transfer",
};

export const BUDGET_BUCKET_LABELS: Record<CategoryBudgetBucket, string> = {
  needs: "Bedarf",
  wants: "Wunsch",
  savings: "Sparen",
};

export function defaultTypeLabel(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  return DEFAULT_TYPE_LABELS[value as CategoryDefaultType] ?? value;
}

export function budgetBucketLabel(value: string | null | undefined): string {
  if (!value) {
    return "kein Budgetbereich";
  }
  return BUDGET_BUCKET_LABELS[value as CategoryBudgetBucket] ?? value;
}

/**
 * Normalisiert Suchtext so, dass Umschriften gleichwertig sind: `ä/ae`,
 * `ö/oe`, `ü/ue` und `ß/ss`. Erst NFC, damit auch kombinierende Zeichen
 * (o + U+0308) getroffen werden, dann die deutschen Ersetzungen, erst danach
 * werden verbleibende Diakritika entfernt - andernfalls würde aus "ö" ein
 * "o" statt "oe".
 */
export function normalizeCategorySearchText(value: string): string {
  return value
    .normalize("NFC")
    .toLowerCase()
    .replace(/ä/g, "ae")
    .replace(/ö/g, "oe")
    .replace(/ü/g, "ue")
    .replace(/ß/g, "ss")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}
