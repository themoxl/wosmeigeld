// Vertrag der Kategorienverwaltung, von Hand gepflegtes Spiegelbild von
// services/api/app/schemas/category.py (siehe docs/KATEGORISIERUNGSPLAN.md).
//
// Warum nicht aus packages/contracts generiert wie der Rest von ./types.ts:
// die OpenAPI-Regenerierung laeuft erst nach dieser Aenderung. Damit App und
// Backend trotzdem gegen dieselben Feldnamen gebaut werden, stehen die
// Modelle hier explizit - ein Tippfehler faellt beim ersten Aufruf auf und
// nicht erst in der Laufzeit. Sobald api-types.ts neu erzeugt ist, koennen
// diese Deklarationen durch Aliase auf components["schemas"][...] ersetzt
// werden, ohne dass ein einziger Aufrufer angefasst werden muss.

/** Obergrenze aus dem Plan: "Maximal 500 Buchungen pro Vorgang". */
export const MAX_BULK_TRANSACTIONS = 500;

export type CategoryDefaultType = "income" | "expense" | "transfer";
export type CategoryBudgetBucket = "needs" | "wants" | "savings";
export type CategoryOrigin = "standard" | "user" | "legacy";

/**
 * Eine Kategorie im rueckwaertskompatiblen Picker-Baum (GET /v1/categories).
 *
 * Die Felder ab `icon` sind additiv hinzugekommen. Aeltere Server (und der
 * persistierte Lesecache eines aelteren App-Starts) liefern sie nicht -
 * deshalb sind sie optional deklariert und JEDE Leseseite muss defensiv
 * lesen (`category.synonyms ?? []`).
 */
export interface Category {
  id: string;
  group_id: string;
  name: string;
  default_type: string | null;
  budget_bucket: string | null;
  is_archived: boolean;
  icon?: string | null;
  color?: string | null;
  is_budgeted?: boolean;
  sort_order?: number;
  synonyms?: string[] | null;
  description?: string | null;
  semantic_key?: string | null;
  version?: number;
}

export interface CategoryGroup {
  id: string;
  name: string;
  sort_order: number;
  categories: Category[];
  is_archived?: boolean;
  icon?: string | null;
  color?: string | null;
  semantic_key?: string | null;
  version?: number;
}

// ---------------------------------------------------------------------------
// Verwaltungsansicht (GET /v1/categories/catalog)
// ---------------------------------------------------------------------------

/**
 * Referenzzahlen je Tabelle. Der Plan unterscheidet historische Referenzen
 * (bleiben erhalten) von zukunftswirksamen (muessen beim Archivieren ersetzt
 * oder geloest werden) - deshalb wird derselbe Typ zweimal verwendet.
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
  /** Vom Server berechnetes Feld (computed_field). */
  total: number;
}

export interface CatalogCategory extends Category {
  origin?: CategoryOrigin;
  taxonomy_version?: number | null;
  archived_at?: string | null;
  merged_into_category_id?: string | null;
  merged_into_name?: string | null;
  updated_at?: string | null;
  usage?: CategoryReferenceCounts | null;
}

export interface CatalogGroup {
  id: string;
  name: string;
  sort_order: number;
  is_archived: boolean;
  icon?: string | null;
  color?: string | null;
  semantic_key?: string | null;
  origin?: CategoryOrigin;
  version: number;
  updated_at?: string | null;
  active_category_count: number;
  archived_category_count: number;
  categories: CatalogCategory[];
}

export interface CategoryCatalog {
  /** Baum-ETag: schuetzt Reorder und Paket-Upgrade gegen fremde Umbauten. */
  taxonomy_revision: number;
  installed_taxonomy_version: number | null;
  available_taxonomy_version: number | null;
  groups: CatalogGroup[];
}

// ---------------------------------------------------------------------------
// Schreibmodelle
// ---------------------------------------------------------------------------

export interface CategoryGroupCreate {
  name: string;
  icon?: string | null;
  color?: string | null;
  sort_order?: number | null;
}

export interface CategoryGroupUpdate {
  name?: string;
  icon?: string | null;
  color?: string | null;
}

export interface CategoryCreate {
  group_id: string;
  name: string;
  default_type?: CategoryDefaultType | null;
  budget_bucket?: CategoryBudgetBucket | null;
  is_budgeted?: boolean;
  icon?: string | null;
  color?: string | null;
  description?: string | null;
  synonyms?: string[];
}

/**
 * `group_id` fehlt bewusst: ein Gruppenwechsel wirkt rueckwirkend auf
 * Berichte und laeuft ausschliesslich ueber move-preview/move.
 */
export interface CategoryUpdate {
  name?: string;
  default_type?: CategoryDefaultType | null;
  budget_bucket?: CategoryBudgetBucket | null;
  is_budgeted?: boolean;
  icon?: string | null;
  color?: string | null;
  description?: string | null;
  synonyms?: string[];
  /** Explizites Loeschen - `undefined` allein bedeutet "nicht angefasst". */
  clear_fields?: ("budget_bucket" | "icon" | "color" | "description" | "default_type")[];
}

export interface ReorderRequest {
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
  first_booked_date?: string | null;
  last_booked_date?: string | null;
  affected_budget_allocations: number;
  hint: string;
}

export interface MoveApply {
  operation_id: string;
  target_group_id: string;
}

export interface ArchivePreview extends OperationPreviewBase {
  category_id: string;
  category_name: string;
  /** Bleibt unveraendert erhalten (Historie). */
  historical: CategoryReferenceCounts;
  /** Wirkt in die Zukunft und muss ersetzt oder geloest werden. */
  forward: CategoryReferenceCounts;
  requires_resolution: boolean;
  blocking_reason?: string | null;
}

export interface ArchiveApply {
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

export interface GroupArchiveApply {
  operation_id: string;
  archive_categories?: boolean;
  replacement_category_id?: string | null;
}

export interface RestoreRequest {
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
  blocking_reason?: string | null;
}

export interface MergeRequest {
  source_category_id: string;
  target_category_id: string;
}

export interface MergeApply {
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
  idempotent_replay?: boolean;
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
  existing_category_id?: string | null;
  existing_category_name?: string | null;
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

export interface StandardPackageApply {
  operation_id: string;
  request_id?: string | null;
  /** semantic_key -> vorhandene Kategorie, die der Nutzer ausdruecklich bestaetigt. */
  confirmed_matches?: Record<string, string>;
}

export interface StandardPackageResult {
  taxonomy_version: number;
  created_groups: number;
  created_categories: number;
  linked_existing: number;
  skipped_name_conflicts: number;
  idempotent_replay?: boolean;
}

// ---------------------------------------------------------------------------
// Zuordnung an Buchungen
// ---------------------------------------------------------------------------

/**
 * Kategorie genau eines Splits aendern (PUT /v1/transactions/{id}/category).
 * Buchungen mit mehreren Splits lehnt der Server mit
 * `409 transaction_has_multiple_splits` ab, damit eine Gesamtkategorie keine
 * vorhandene Aufteilung still ueberschreibt.
 */
export interface TransactionCategoryUpdate {
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
  reason?: BulkSkipReason | null;
  current_version?: number | null;
}

export interface BulkCategoryInput {
  transaction_ids: string[];
  category_id: string | null;
  /** Zuletzt gesehene Version je Buchung; fehlt ein Eintrag, wird ungeprueft geschrieben. */
  expected_versions?: Record<string, number>;
}

export interface BulkCategoryResult {
  applied: number;
  unchanged: number;
  skipped: number;
  conflict: number;
  items: BulkCategoryItem[];
}
