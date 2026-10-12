import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Modal } from "../components/Modal";
import { AiErrorNotice, AiReasonNotice } from "../components/AiReasonNotice";
import { CategoryPicker } from "../components/CategoryPicker";
import { RowMenu } from "../components/RowMenu";
import { apiErrorDetail } from "../lib/api";
import { aiReasonCodeIn } from "../lib/aiReasons";
import {
  budgetBucketLabel,
  defaultTypeLabel,
  normalizeCategorySearchText,
  type ArchivePreview,
  type CatalogCategory,
  type CatalogGroup,
  type CategoryBudgetBucket,
  type CategoryDefaultType,
  type CategoryGroup,
  type CategorySuggestionPreview,
  type CategorySuggestionResult,
  type GroupArchivePreview,
  type MergePreview,
  type MovePreview,
  type StandardPackagePreview,
} from "../lib/categoryTypes";
import { formatMinorUnitsAsEuro } from "../lib/money";
import {
  newRequestId,
  useApplyCategorySuggestions,
  useApplyStandardPackage,
  useArchiveCategory,
  useArchiveCategoryGroup,
  useArchiveCategoryGroupPreview,
  useArchiveCategoryPreview,
  useCategoryCatalog,
  useCategoryContext,
  useCreateCategory,
  useCreateCategoryGroup,
  useMergeCategories,
  useMergeCategoriesPreview,
  useMoveCategory,
  useMoveCategoryPreview,
  useReorderCategories,
  useReorderCategoryGroups,
  useRestoreCategory,
  useRestoreCategoryGroup,
  useStandardPackagePreview,
  useStandardPackageStatus,
  useUpdateCategory,
  useUpdateCategoryGroup,
} from "../hooks/useCategoryCatalog";
import { isAiTaskWorking, useAiTask, useEnqueueAiTask } from "../hooks/useAiTaskQueue";

// Kategorienverwaltung (docs/KATEGORISIERUNGSPLAN.md, "Zieloberflaeche:
// Kategorien verwalten / Web"). Route /kategorien, Sidebar-Eintrag unter
// "System", zusaetzlich verlinkt aus den Einstellungen.
//
// Leitplanken, die sich direkt in der Oberflaeche abbilden:
// - Es gibt kein hartes Loeschen. Archivieren ist der einzige Weg, und es
//   zeigt vorher die Servervorschau der betroffenen Referenzen.
// - Ein Gruppenwechsel laeuft ausschliesslich ueber "In andere Gruppe
//   verschieben" mit Vorschau; Drag-and-drop sortiert nur INNERHALB einer
//   Gruppe bzw. Gruppen untereinander.
// - Fuer Tastaturbedienung existieren immer "Nach oben"/"Nach unten".
// - Zusammenfuehren ist owner-only und vereinheitlicht die gesamte Historie.
// - Viewer/Child sehen alles schreibgeschuetzt und bekommen keine
//   funktionslosen Bearbeitungsschaltflaechen.

const DEFAULT_TYPE_OPTIONS: { value: CategoryDefaultType; label: string }[] = [
  { value: "expense", label: "Ausgabe" },
  { value: "income", label: "Einnahme" },
  { value: "transfer", label: "Transfer" },
];

const BUDGET_BUCKET_OPTIONS: { value: CategoryBudgetBucket | ""; label: string }[] = [
  { value: "", label: "keiner" },
  { value: "needs", label: "Bedarf" },
  { value: "wants", label: "Wunsch" },
  { value: "savings", label: "Sparen" },
];

type DialogState =
  | { kind: "category-create"; groupId: string }
  | { kind: "category-edit"; category: CatalogCategory }
  | { kind: "group-create" }
  | { kind: "group-rename"; group: CatalogGroup }
  | { kind: "category-move"; category: CatalogCategory }
  | { kind: "category-archive"; category: CatalogCategory }
  | { kind: "category-restore"; category: CatalogCategory }
  | { kind: "category-merge"; category: CatalogCategory }
  | { kind: "group-archive"; group: CatalogGroup }
  | { kind: "standard-package" }
  | { kind: "ai-suggestions"; taskId?: string };

function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) {
    return items;
  }
  const next = [...items];
  const [moved] = next.splice(from, 1);
  if (moved === undefined) {
    return items;
  }
  next.splice(to, 0, moved);
  return next;
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/**
 * Eine Zelle statt zwei Spalten fuer Budgetbereich und Budgetstatus.
 *
 * "Budgetbereich: kein Budgetbereich" und "Budgetstatus: nicht im Budget"
 * standen frueher nebeneinander und sagten dasselbe zweimal. Der Normalfall
 * (budgetiert, mit Bereich) ist jetzt ein einzelnes Wort; nur die Abweichung
 * bekommt einen Zusatz.
 */
function budgetCellLabel(category: CatalogCategory): string {
  const bucket = category.budget_bucket ? budgetBucketLabel(category.budget_bucket) : null;
  if (!category.is_budgeted) {
    return bucket ? `${bucket} (nicht budgetiert)` : "nicht budgetiert";
  }
  return bucket ?? "—";
}

function referenceLines(counts: {
  transaction_splits: number;
  payee_defaults: number;
  classification_rules: number;
  contracts: number;
  budget_allocations: number;
  budget_rollovers: number;
  savings_goals: number;
  classification_decisions: number;
}): string[] {
  const entries: [string, number][] = [
    ["Buchungssplits", counts.transaction_splits],
    ["Zahlungsempfänger-Vorgaben", counts.payee_defaults],
    ["Klassifikationsregeln", counts.classification_rules],
    ["Verträge", counts.contracts],
    ["Budgetzeilen", counts.budget_allocations],
    ["Überträge", counts.budget_rollovers],
    ["Sparziele", counts.savings_goals],
    ["KI-Entscheidungen", counts.classification_decisions],
  ];
  return entries.filter(([, value]) => value > 0).map(([label, value]) => `${label}: ${value}`);
}

// ---------------------------------------------------------------------------
// Formulare
// ---------------------------------------------------------------------------

interface CategoryFormModalProps {
  groups: CatalogGroup[];
  category: CatalogCategory | null;
  initialGroupId: string;
  onClose: () => void;
}

function CategoryFormModal({ groups, category, initialGroupId, onClose }: CategoryFormModalProps) {
  const createMutation = useCreateCategory();
  const updateMutation = useUpdateCategory();

  const [name, setName] = useState(category?.name ?? "");
  const [groupId, setGroupId] = useState(category?.group_id ?? initialGroupId);
  const [defaultType, setDefaultType] = useState<CategoryDefaultType>(
    (category?.default_type as CategoryDefaultType | null) ?? "expense",
  );
  const [budgetBucket, setBudgetBucket] = useState<CategoryBudgetBucket | "">(
    (category?.budget_bucket as CategoryBudgetBucket | null) ?? "",
  );
  const [isBudgeted, setIsBudgeted] = useState(category?.is_budgeted ?? true);
  const [isTaxDeductible, setIsTaxDeductible] = useState(category?.is_tax_deductible ?? false);
  const [icon, setIcon] = useState(category?.icon ?? "");
  const [color, setColor] = useState(category?.color ?? "");
  const [description, setDescription] = useState(category?.description ?? "");
  const [synonyms, setSynonyms] = useState((category?.synonyms ?? []).join(", "));
  const [formError, setFormError] = useState<string | null>(null);

  const isEdit = category !== null;
  const isPending = createMutation.isPending || updateMutation.isPending;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    const trimmed = name.trim().replace(/\s+/g, " ");
    if (!trimmed) {
      setFormError("Bitte einen Namen angeben (1–120 Zeichen).");
      return;
    }
    const synonymList = synonyms
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);

    if (isEdit) {
      // Leere Felder werden ausdruecklich geloescht - `undefined` allein
      // hiesse "nicht angefasst".
      const clearFields: ("budget_bucket" | "icon" | "color" | "description")[] = [];
      if (!budgetBucket) clearFields.push("budget_bucket");
      if (!icon.trim()) clearFields.push("icon");
      if (!color.trim()) clearFields.push("color");
      if (!description.trim()) clearFields.push("description");

      updateMutation.mutate(
        {
          categoryId: category.id,
          version: category.version,
          input: {
            name: trimmed,
            default_type: defaultType,
            ...(budgetBucket ? { budget_bucket: budgetBucket } : {}),
            is_budgeted: isBudgeted,
            is_tax_deductible: isTaxDeductible,
            ...(icon.trim() ? { icon: icon.trim() } : {}),
            ...(color.trim() ? { color: color.trim() } : {}),
            ...(description.trim() ? { description: description.trim() } : {}),
            synonyms: synonymList,
            clear_fields: clearFields,
          },
        },
        {
          onSuccess: onClose,
          onError: (error) =>
            setFormError(errorText(error, "Kategorie konnte nicht gespeichert werden.")),
        },
      );
      return;
    }

    createMutation.mutate(
      {
        group_id: groupId,
        name: trimmed,
        default_type: defaultType,
        budget_bucket: budgetBucket || null,
        is_budgeted: isBudgeted,
        is_tax_deductible: isTaxDeductible,
        icon: icon.trim() || null,
        color: color.trim() || null,
        description: description.trim() || null,
        synonyms: synonymList,
      },
      {
        onSuccess: onClose,
        onError: (error) =>
          setFormError(errorText(error, "Kategorie konnte nicht angelegt werden.")),
      },
    );
  };

  return (
    <Modal title={isEdit ? "Kategorie bearbeiten" : "Kategorie hinzufügen"} onClose={onClose}>
      <form className="edit-transaction-form" onSubmit={handleSubmit}>
        <div className="form-field">
          <label className="form-label" htmlFor="category-form-name">
            Name *
          </label>
          <input
            id="category-form-name"
            className="form-input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            required
          />
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="category-form-group">
            Gruppe
          </label>
          <select
            id="category-form-group"
            className="form-input"
            value={groupId}
            disabled={isEdit}
            onChange={(event) => setGroupId(event.target.value)}
          >
            {groups
              .filter((group) => !group.is_archived || group.id === groupId)
              .map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
          </select>
          {isEdit && (
            <span className="placeholder-note">
              Ein Gruppenwechsel wirkt rückwirkend auf Berichte und läuft deshalb über „In andere
              Gruppe verschieben“ mit Vorschau.
            </span>
          )}
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="category-form-type">
            Art
          </label>
          <select
            id="category-form-type"
            className="form-input"
            value={defaultType}
            onChange={(event) => setDefaultType(event.target.value as CategoryDefaultType)}
          >
            {DEFAULT_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="category-form-bucket">
            Budgetbereich
          </label>
          <select
            id="category-form-bucket"
            className="form-input"
            value={budgetBucket}
            onChange={(event) => setBudgetBucket(event.target.value as CategoryBudgetBucket | "")}
          >
            {BUDGET_BUCKET_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <input
            type="checkbox"
            id="category-form-budgeted"
            checked={isBudgeted}
            onChange={(event) => setIsBudgeted(event.target.checked)}
          />
          <label htmlFor="category-form-budgeted" style={{ fontSize: "13px", cursor: "pointer" }}>
            Im Budget anzeigen
          </label>
        </div>

        {/* Steuerrelevanz ist eine eigene Dimension und ausdruecklich KEIN
            Budgetbereich: dieselbe Kategorie kann budgetiert und absetzbar
            sein (Migration 0010). */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
          <input
            type="checkbox"
            id="category-form-tax"
            checked={isTaxDeductible}
            onChange={(event) => setIsTaxDeductible(event.target.checked)}
          />
          <label htmlFor="category-form-tax" style={{ fontSize: "13px", cursor: "pointer" }}>
            Steuerlich absetzbar
          </label>
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="category-form-icon">
            Symbol (optional)
          </label>
          <input
            id="category-form-icon"
            className="form-input"
            value={icon}
            onChange={(event) => setIcon(event.target.value)}
            placeholder="z. B. 🚆"
          />
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="category-form-color">
            Farbe (optional)
          </label>
          <input
            id="category-form-color"
            className="form-input"
            value={color}
            onChange={(event) => setColor(event.target.value)}
            placeholder="z. B. #4c7a5d"
          />
          <span className="placeholder-note">
            Die Farbe ist nur eine Ergänzung – Name und Gruppe bleiben immer sichtbar.
          </span>
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="category-form-description">
            Beschreibung (optional)
          </label>
          <input
            id="category-form-description"
            className="form-input"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="category-form-synonyms">
            Synonyme (kommagetrennt)
          </label>
          <input
            id="category-form-synonyms"
            className="form-input"
            value={synonyms}
            onChange={(event) => setSynonyms(event.target.value)}
            placeholder="z. B. Bahn, Nahverkehr, Ticket"
          />
          <span className="placeholder-note">
            Synonyme werden in der Kategoriesuche mitdurchsucht.
          </span>
        </div>

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button type="submit" className="btn-primary" disabled={isPending}>
            {isPending ? "Wird gespeichert…" : "Speichern"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

interface GroupFormModalProps {
  group: CatalogGroup | null;
  onClose: () => void;
}

function GroupFormModal({ group, onClose }: GroupFormModalProps) {
  const createMutation = useCreateCategoryGroup();
  const updateMutation = useUpdateCategoryGroup();
  const [name, setName] = useState(group?.name ?? "");
  const [icon, setIcon] = useState(group?.icon ?? "");
  const [color, setColor] = useState(group?.color ?? "");
  const [formError, setFormError] = useState<string | null>(null);

  const isEdit = group !== null;
  const isPending = createMutation.isPending || updateMutation.isPending;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    const trimmed = name.trim().replace(/\s+/g, " ");
    if (!trimmed) {
      setFormError("Bitte einen Namen angeben (1–120 Zeichen).");
      return;
    }
    const onError = (error: unknown) =>
      setFormError(errorText(error, "Gruppe konnte nicht gespeichert werden."));

    if (isEdit) {
      updateMutation.mutate(
        {
          groupId: group.id,
          version: group.version,
          input: { name: trimmed, icon: icon.trim() || null, color: color.trim() || null },
        },
        { onSuccess: onClose, onError },
      );
      return;
    }
    createMutation.mutate(
      { name: trimmed, icon: icon.trim() || null, color: color.trim() || null },
      { onSuccess: onClose, onError },
    );
  };

  return (
    <Modal title={isEdit ? "Gruppe umbenennen" : "Gruppe hinzufügen"} onClose={onClose}>
      <form className="edit-transaction-form" onSubmit={handleSubmit}>
        <div className="form-field">
          <label className="form-label" htmlFor="group-form-name">
            Name *
          </label>
          <input
            id="group-form-name"
            className="form-input"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            required
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="group-form-icon">
            Symbol (optional)
          </label>
          <input
            id="group-form-icon"
            className="form-input"
            value={icon}
            onChange={(event) => setIcon(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="group-form-color">
            Farbe (optional)
          </label>
          <input
            id="group-form-color"
            className="form-input"
            value={color}
            onChange={(event) => setColor(event.target.value)}
          />
        </div>

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button type="submit" className="btn-primary" disabled={isPending}>
            {isPending ? "Wird gespeichert…" : "Speichern"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Operationen mit Servervorschau
// ---------------------------------------------------------------------------

interface MoveModalProps {
  category: CatalogCategory;
  groups: CatalogGroup[];
  onClose: () => void;
}

function MoveCategoryModal({ category, groups, onClose }: MoveModalProps) {
  const moveMutation = useMoveCategory();
  const [targetGroupId, setTargetGroupId] = useState("");
  const previewQuery = useMoveCategoryPreview(category.id, targetGroupId);
  const [formError, setFormError] = useState<string | null>(null);

  const preview: MovePreview | null = previewQuery.data ?? null;
  const targets = groups.filter((group) => !group.is_archived && group.id !== category.group_id);

  const handleApply = () => {
    if (!preview) {
      return;
    }
    moveMutation.mutate(
      {
        categoryId: category.id,
        input: { operation_id: preview.operation_id, target_group_id: preview.target_group_id },
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          setFormError(errorText(error, "Verschieben ist fehlgeschlagen."));
          // Eine veraltete Vorschau wird nicht verschluckt: sie wird gemeldet
          // und sofort neu berechnet.
          if (apiErrorDetail(error) === "category_preview_stale") {
            void previewQuery.refetch();
          }
        },
      },
    );
  };

  return (
    <Modal title="In andere Gruppe verschieben" onClose={onClose}>
      <div className="edit-transaction-form">
        <p className="card-body">
          „{category.name}“ behält ihre ID und alle Buchungen. Berichte verwenden immer die aktuelle
          Gruppe – der Wechsel wirkt deshalb rückwirkend.
        </p>

        <div className="form-field">
          <label className="form-label" htmlFor="move-target-group">
            Zielgruppe
          </label>
          <select
            id="move-target-group"
            className="form-input"
            value={targetGroupId}
            onChange={(event) => {
              setFormError(null);
              setTargetGroupId(event.target.value);
            }}
          >
            <option value="">— Gruppe wählen —</option>
            {targets.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
        </div>

        {previewQuery.isFetching && <p role="status">Auswirkung wird berechnet…</p>}
        {previewQuery.isError && (
          <p role="alert" className="status-error">
            {errorText(previewQuery.error, "Vorschau konnte nicht berechnet werden.")}
          </p>
        )}

        {preview && (
          <div className="operation-preview" role="status">
            <h3 className="card-heading">Auswirkung</h3>
            <ul>
              <li>
                {preview.source_group_name} → {preview.target_group_name}
              </li>
              <li>Betroffene Buchungssplits: {preview.affected_splits}</li>
              <li>
                Zeitraum:{" "}
                {preview.first_booked_date && preview.last_booked_date
                  ? `${preview.first_booked_date} bis ${preview.last_booked_date}`
                  : "keine Buchungen"}
              </li>
              <li>Betroffene Budgetzeilen: {preview.affected_budget_allocations}</li>
            </ul>
            <p className="placeholder-note">{preview.hint}</p>
          </div>
        )}

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!preview || moveMutation.isPending}
            onClick={handleApply}
          >
            {moveMutation.isPending ? "Wird verschoben…" : "Verschieben"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

interface ArchiveModalProps {
  category: CatalogCategory;
  pickerGroups: CategoryGroup[];
  onClose: () => void;
}

function ArchiveCategoryModal({ category, pickerGroups, onClose }: ArchiveModalProps) {
  const archiveMutation = useArchiveCategory();
  const previewQuery = useArchiveCategoryPreview(category.id);
  const [replacementId, setReplacementId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const preview: ArchivePreview | null = previewQuery.data ?? null;

  const handleApply = () => {
    if (!preview) {
      return;
    }
    archiveMutation.mutate(
      {
        categoryId: category.id,
        input: {
          operation_id: preview.operation_id,
          replacement_category_id: replacementId || null,
        },
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          setFormError(errorText(error, "Archivieren ist fehlgeschlagen."));
          if (apiErrorDetail(error) === "category_preview_stale") {
            void previewQuery.refetch();
          }
        },
      },
    );
  };

  return (
    <Modal title={`„${category.name}“ archivieren`} onClose={onClose}>
      <div className="edit-transaction-form">
        <p className="card-body">
          Archivieren löscht nichts: Historische Buchungen, Budgetzeilen und Berichte behalten Name,
          Gruppe und Betrag. Die Kategorie verschwindet nur aus Pickern, neuen Regeln und neuen
          Budgets.
        </p>

        {previewQuery.isFetching && <p role="status">Referenzen werden gezählt…</p>}
        {previewQuery.isError && (
          <p role="alert" className="status-error">
            {errorText(previewQuery.error, "Vorschau konnte nicht geladen werden.")}
          </p>
        )}

        {preview && (
          <div className="operation-preview" role="status">
            <h3 className="card-heading">Historisch (bleibt erhalten)</h3>
            <ul>
              {referenceLines(preview.historical).map((line) => (
                <li key={line}>{line}</li>
              ))}
              {referenceLines(preview.historical).length === 0 && <li>keine Referenzen</li>}
            </ul>
            <h3 className="card-heading">Zukunftswirksam (muss aufgelöst werden)</h3>
            <ul>
              {referenceLines(preview.forward).map((line) => (
                <li key={line}>{line}</li>
              ))}
              {referenceLines(preview.forward).length === 0 && <li>keine Referenzen</li>}
            </ul>
            {preview.blocking_reason && (
              <p role="alert" className="status-error">
                {preview.blocking_reason}
              </p>
            )}
          </div>
        )}

        {preview?.requires_resolution && (
          <div className="form-field">
            <label className="form-label" htmlFor="archive-replacement">
              Ersatzkategorie für zukunftswirksame Referenzen
            </label>
            <CategoryPicker
              id="archive-replacement"
              categoryGroups={pickerGroups}
              value={replacementId}
              onChange={setReplacementId}
              noneLabel="Ohne Ersatz (Regeln werden deaktiviert)"
              allowCreate={false}
            />
          </div>
        )}

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!preview || Boolean(preview.blocking_reason) || archiveMutation.isPending}
            onClick={handleApply}
          >
            {archiveMutation.isPending ? "Wird archiviert…" : "Archivieren"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

interface GroupArchiveModalProps {
  group: CatalogGroup;
  pickerGroups: CategoryGroup[];
  onClose: () => void;
}

function ArchiveGroupModal({ group, pickerGroups, onClose }: GroupArchiveModalProps) {
  const archiveMutation = useArchiveCategoryGroup();
  const previewQuery = useArchiveCategoryGroupPreview(group.id);
  const [archiveCategories, setArchiveCategories] = useState(false);
  const [replacementId, setReplacementId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const preview: GroupArchivePreview | null = previewQuery.data ?? null;

  const handleApply = () => {
    if (!preview) {
      return;
    }
    archiveMutation.mutate(
      {
        groupId: group.id,
        input: {
          operation_id: preview.operation_id,
          archive_categories: archiveCategories,
          replacement_category_id: replacementId || null,
        },
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          setFormError(errorText(error, "Gruppe konnte nicht archiviert werden."));
          if (apiErrorDetail(error) === "category_preview_stale") {
            void previewQuery.refetch();
          }
        },
      },
    );
  };

  return (
    <Modal title={`Gruppe „${group.name}“ archivieren`} onClose={onClose}>
      <div className="edit-transaction-form">
        <p className="card-body">
          Eine Gruppe kann nur archiviert werden, wenn alle enthaltenen Kategorien bereits
          archiviert sind oder in derselben bestätigten Operation aufgelöst werden. Ein
          Cascade-Archiv ohne Vorschau gibt es nicht.
        </p>

        {previewQuery.isFetching && <p role="status">Auswirkung wird berechnet…</p>}
        {previewQuery.isError && (
          <p role="alert" className="status-error">
            {errorText(previewQuery.error, "Vorschau konnte nicht geladen werden.")}
          </p>
        )}

        {preview && (
          <div className="operation-preview" role="status">
            <p>
              Noch aktive Kategorien in dieser Gruppe: {preview.active_category_names.length || 0}
            </p>
            {preview.active_category_names.length > 0 && (
              <ul>
                {preview.active_category_names.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {preview?.requires_resolution && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
              <input
                type="checkbox"
                id="group-archive-categories"
                checked={archiveCategories}
                onChange={(event) => setArchiveCategories(event.target.checked)}
              />
              <label
                htmlFor="group-archive-categories"
                style={{ fontSize: "13px", cursor: "pointer" }}
              >
                Enthaltene Kategorien in derselben Operation mit archivieren
              </label>
            </div>

            <div className="form-field">
              <label className="form-label" htmlFor="group-archive-replacement">
                Ersatzkategorie für zukunftswirksame Referenzen
              </label>
              <CategoryPicker
                id="group-archive-replacement"
                categoryGroups={pickerGroups}
                value={replacementId}
                onChange={setReplacementId}
                noneLabel="Ohne Ersatz (Regeln werden deaktiviert)"
                allowCreate={false}
              />
            </div>
          </>
        )}

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={
              !preview ||
              archiveMutation.isPending ||
              (preview.requires_resolution && !archiveCategories)
            }
            onClick={handleApply}
          >
            {archiveMutation.isPending ? "Wird archiviert…" : "Gruppe archivieren"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

interface RestoreModalProps {
  category: CatalogCategory;
  groups: CatalogGroup[];
  onClose: () => void;
}

function RestoreCategoryModal({ category, groups, onClose }: RestoreModalProps) {
  const restoreMutation = useRestoreCategory();
  const ownGroup = groups.find((group) => group.id === category.group_id) ?? null;
  const groupIsArchived = ownGroup?.is_archived ?? false;

  const [restoreGroup, setRestoreGroup] = useState(groupIsArchived);
  const [targetGroupId, setTargetGroupId] = useState("");
  const [newName, setNewName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const handleApply = () => {
    setFormError(null);
    restoreMutation.mutate(
      {
        categoryId: category.id,
        input: {
          restore_group: groupIsArchived ? restoreGroup : false,
          target_group_id: !restoreGroup && targetGroupId ? targetGroupId : null,
          new_name: newName.trim() || null,
        },
      },
      {
        onSuccess: onClose,
        onError: (error) => setFormError(errorText(error, "Wiederherstellen ist fehlgeschlagen.")),
      },
    );
  };

  return (
    <Modal title={`„${category.name}“ wiederherstellen`} onClose={onClose}>
      <div className="edit-transaction-form">
        {category.merged_into_category_id ? (
          <p role="alert" className="status-error">
            Diese Kategorie wurde mit „{category.merged_into_name ?? "einer anderen Kategorie"}“
            zusammengeführt und ist deshalb nicht wiederherstellbar.
          </p>
        ) : (
          <>
            {groupIsArchived && (
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                <input
                  type="checkbox"
                  id="restore-group-checkbox"
                  checked={restoreGroup}
                  onChange={(event) => setRestoreGroup(event.target.checked)}
                />
                <label
                  htmlFor="restore-group-checkbox"
                  style={{ fontSize: "13px", cursor: "pointer" }}
                >
                  Archivierte Gruppe „{ownGroup?.name}“ mit wiederherstellen
                </label>
              </div>
            )}

            {groupIsArchived && !restoreGroup && (
              <div className="form-field">
                <label className="form-label" htmlFor="restore-target-group">
                  Aktive Zielgruppe
                </label>
                <select
                  id="restore-target-group"
                  className="form-input"
                  value={targetGroupId}
                  onChange={(event) => setTargetGroupId(event.target.value)}
                >
                  <option value="">— Gruppe wählen —</option>
                  {groups
                    .filter((group) => !group.is_archived)
                    .map((group) => (
                      <option key={group.id} value={group.id}>
                        {group.name}
                      </option>
                    ))}
                </select>
              </div>
            )}

            <div className="form-field">
              <label className="form-label" htmlFor="restore-new-name">
                Neuer Name (nur bei Namenskonflikt nötig)
              </label>
              <input
                id="restore-new-name"
                className="form-input"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder={category.name}
              />
            </div>
          </>
        )}

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={Boolean(category.merged_into_category_id) || restoreMutation.isPending}
            onClick={handleApply}
          >
            {restoreMutation.isPending ? "Wird wiederhergestellt…" : "Wiederherstellen"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

interface MergeModalProps {
  category: CatalogCategory;
  pickerGroups: CategoryGroup[];
  onClose: () => void;
}

function MergeCategoryModal({ category, pickerGroups, onClose }: MergeModalProps) {
  const mergeMutation = useMergeCategories();
  const [targetId, setTargetId] = useState("");
  const previewQuery = useMergeCategoriesPreview(category.id, targetId);
  const [formError, setFormError] = useState<string | null>(null);
  // Idempotenzschlüssel bleibt über Wiederholungen desselben Dialogs gleich.
  const requestId = useRef(newRequestId());

  const preview: MergePreview | null = previewQuery.data ?? null;

  const handleApply = () => {
    if (!preview) {
      return;
    }
    mergeMutation.mutate(
      { operation_id: preview.operation_id, request_id: requestId.current },
      {
        onSuccess: onClose,
        onError: (error) => {
          setFormError(errorText(error, "Zusammenführen ist fehlgeschlagen."));
          if (apiErrorDetail(error) === "category_preview_stale") {
            void previewQuery.refetch();
          }
        },
      },
    );
  };

  return (
    <Modal title={`„${category.name}“ zusammenführen`} onClose={onClose}>
      <div className="edit-transaction-form">
        <p className="card-body">
          <strong>Zusammenführen vereinheitlicht die gesamte Historie.</strong> Alle Buchungen,
          Regeln, Budgets und Ziele der Quelle zeigen danach auf die Zielkategorie; die Quelle
          bleibt als nicht wiederherstellbarer Hinweis „Zusammengeführt mit …“ zurück. Wer nur
          künftige Zuordnungen ändern will, archiviert stattdessen mit Ersatz.
        </p>

        <div className="form-field">
          <label className="form-label" htmlFor="merge-target">
            Zielkategorie
          </label>
          <CategoryPicker
            id="merge-target"
            categoryGroups={pickerGroups}
            value={targetId}
            onChange={(value) => {
              setFormError(null);
              setTargetId(value);
            }}
            noneLabel="— Zielkategorie wählen —"
            allowCreate={false}
          />
        </div>

        {previewQuery.isFetching && <p role="status">Auswirkung wird berechnet…</p>}
        {previewQuery.isError && (
          <p role="alert" className="status-error">
            {errorText(previewQuery.error, "Vorschau konnte nicht berechnet werden.")}
          </p>
        )}

        {preview && (
          <div className="operation-preview" role="status">
            <p>
              {preview.source_category_name} → {preview.target_category_name}
            </p>
            <ul>
              {referenceLines(preview.references).map((line) => (
                <li key={line}>{line}</li>
              ))}
              <li>Buchungen mit Versionssprung: {preview.transactions_to_bump}</li>
            </ul>
            {preview.collisions.length > 0 && (
              <>
                <h3 className="card-heading">Kollisionen</h3>
                <ul>
                  {preview.collisions.map((collision) => (
                    <li key={`${collision.kind}-${collision.detail}`}>
                      {collision.detail} ({collision.count})
                    </li>
                  ))}
                </ul>
              </>
            )}
            {preview.blocking_reason && (
              <p role="alert" className="status-error">
                {preview.blocking_reason}
              </p>
            )}
          </div>
        )}

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!preview || Boolean(preview.blocking_reason) || mergeMutation.isPending}
            onClick={handleApply}
          >
            {mergeMutation.isPending ? "Wird zusammengeführt…" : "Endgültig zusammenführen"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// KI-Kategorievorschlaege
// ---------------------------------------------------------------------------

/** Ab dieser Modellsicherheit gilt ein Vorschlag als "sicher". */
const SAFE_SUGGESTION_CONFIDENCE = 0.75;

interface SuggestionEdit {
  name: string;
  groupId: string;
  budgetBucket: CategoryBudgetBucket | "";
  isTaxDeductible: boolean;
}

/**
 * Der Dialog, der die eigentliche Handarbeit ersetzt: die KI liest die noch
 * nicht zugeordneten Buchungen, bildet Gruppen gleicher Empfaenger und
 * schlaegt je Gruppe eine vorhandene oder eine fehlende Kategorie vor.
 *
 * Bewusst zweistufig. Der Lauf startet erst auf Knopfdruck (er belegt ein
 * lokales Modell fuer mehrere Sekunden), und angelegt wird ausschliesslich,
 * was hier ausgewaehlt ist - "KI darf vorschlagen, nicht erfinden" aus
 * docs/KATEGORISIERUNGSPLAN.md ist genau diese Trennung.
 */
function CategorySuggestionsModal({
  groups,
  initialTaskId,
  onClose,
}: {
  groups: CatalogGroup[];
  initialTaskId?: string;
  onClose: () => void;
}) {
  const enqueueMutation = useEnqueueAiTask();
  const applyMutation = useApplyCategorySuggestions();

  const [taskId, setTaskId] = useState<string | null>(initialTaskId ?? null);
  const taskQuery = useAiTask(taskId);
  const [preview, setPreview] = useState<CategorySuggestionPreview | null>(null);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [edits, setEdits] = useState<Record<string, SuggestionEdit>>({});
  const [assignTransactions, setAssignTransactions] = useState(true);
  const [result, setResult] = useState<CategorySuggestionResult | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [hydratedTaskId, setHydratedTaskId] = useState<string | null>(null);

  const activeGroups = groups.filter((group) => !group.is_archived);

  useEffect(() => {
    const task = taskQuery.data;
    const value = task?.result;
    if (!task || !value || hydratedTaskId === task.id || !Array.isArray(value.suggestions)) return;

    const restored = value as unknown as CategorySuggestionPreview;
    // Das Query-Ergebnis kommt nach dem Render. Im naechsten Event-Loop-Takt
    // wird daraus genau einmal der anschliessend frei editierbare Formularzustand.
    const timeoutId = window.setTimeout(() => {
      setHydratedTaskId(task.id);
      setPreview(restored);
      setSelected(
        Object.fromEntries(
          restored.suggestions.map((item) => [
            item.cluster_key,
            item.confidence >= SAFE_SUGGESTION_CONFIDENCE,
          ]),
        ),
      );
      setEdits(
        Object.fromEntries(
          restored.suggestions.map((item) => [
            item.cluster_key,
            {
              name: item.proposed_name ?? "",
              groupId: item.proposed_group_id ?? "",
              budgetBucket: item.proposed_budget_bucket ?? "",
              isTaxDeductible: item.proposed_is_tax_deductible,
            },
          ]),
        ),
      );
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [hydratedTaskId, taskQuery.data]);

  const startRun = () => {
    setFormError(null);
    setResult(null);
    // Ein Fehler beim Einreihen steht in enqueueMutation.error (unten als
    // AiErrorNotice: mit KI-Grund samt Aktion, sonst als Klartext).
    enqueueMutation.mutate(
      { task_type: "category_suggestions" },
      { onSuccess: (task) => setTaskId(task.id) },
    );
  };

  const taskWorking = isAiTaskWorking(taskQuery.data);

  const selectedKeys = preview
    ? preview.suggestions.filter((item) => selected[item.cluster_key]).map((i) => i.cluster_key)
    : [];

  // Ein neuer Vorschlag ohne Zielgruppe kann nicht angelegt werden - das
  // Backend lehnt ihn ab, deshalb wird er hier schon blockiert.
  const incompleteSelection = preview
    ? preview.suggestions.some(
        (item) =>
          selected[item.cluster_key] &&
          item.kind === "new_category" &&
          (!edits[item.cluster_key]?.name.trim() || !edits[item.cluster_key]?.groupId),
      )
    : false;

  const handleApply = () => {
    if (!preview) {
      return;
    }
    setFormError(null);
    applyMutation.mutate(
      {
        operation_id: preview.operation_id,
        request_id: newRequestId(),
        assign_transactions: assignTransactions,
        accepted: preview.suggestions
          .filter((item) => selected[item.cluster_key])
          .map((item) => {
            const edit = edits[item.cluster_key];
            if (item.kind === "existing_category") {
              return {
                cluster_key: item.cluster_key,
                use_existing_category_id: item.existing_category_id,
              };
            }
            return {
              cluster_key: item.cluster_key,
              name: edit?.name.trim() || item.proposed_name,
              group_id: edit?.groupId || item.proposed_group_id,
              budget_bucket: edit?.budgetBucket || null,
              is_tax_deductible: edit?.isTaxDeductible ?? item.proposed_is_tax_deductible,
            };
          }),
      },
      {
        onSuccess: (data) => {
          setResult(data);
          setPreview(null);
        },
        onError: (error) => {
          setFormError(errorText(error, "Die Vorschläge konnten nicht übernommen werden."));
          if (apiErrorDetail(error) === "category_preview_stale") {
            setPreview(null);
          }
        },
      },
    );
  };

  return (
    <Modal title="Kategorien aus Buchungen vorschlagen" onClose={onClose}>
      <div className="edit-transaction-form">
        {!preview && !result && (
          <>
            <p className="card-body">
              Die KI liest deine noch nicht zugeordneten Buchungen, fasst gleiche Empfänger zusammen
              und schlägt je Gruppe entweder eine vorhandene Kategorie oder eine fehlende neue vor.
              Angelegt wird nur, was du hier bestätigst – und nie aus einer einzelnen Buchung.
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={enqueueMutation.isPending || taskWorking}
                onClick={startRun}
              >
                {enqueueMutation.isPending
                  ? "Wird eingereiht …"
                  : taskWorking
                    ? "KI-Aufgabe läuft …"
                    : "Vorschläge berechnen"}
              </button>
            </div>
          </>
        )}

        {(enqueueMutation.isPending || taskWorking) && (
          <p role="status">
            {taskQuery.data?.status_message ??
              "Die Aufgabe wartet in der haushaltsweiten KI-Warteschlange."}
            {taskQuery.data?.status === "queued" &&
              " Du kannst dieses Fenster schließen und anderswo weiterarbeiten."}
          </p>
        )}

        {taskQuery.data?.status === "failed" && (
          // error_message endet mit "(Kennung: <Grund des KI-Kerns>)" - der
          // Text bleibt die Anzeige, der Grund liefert die passende Aktion.
          <AiReasonNotice
            role="alert"
            className="status-error"
            code={aiReasonCodeIn(taskQuery.data.error_message)}
            message={taskQuery.data.error_message ?? "Die KI-Aufgabe ist fehlgeschlagen."}
          />
        )}

        {preview && (
          <>
            <p className="card-body" role="status">
              {preview.uncategorized_transactions} Buchungen ohne Kategorie ·{" "}
              {preview.clustered_transactions} davon in {preview.suggestions.length} Gruppen
              beurteilt
              {(preview.unassessed_clusters ?? 0) > 0
                ? ` · ${preview.unassessed_clusters} ${preview.unassessed_clusters === 1 ? "Gruppe" : "Gruppen"} ohne KI-Urteil`
                : ""}
              {preview.skipped_singletons > 0
                ? ` · ${preview.skipped_singletons} Einzelfälle übersprungen`
                : ""}
              {preview.model_name ? ` · Modell: ${preview.model_name}` : ""}
            </p>
            {/* Teilergebnis: einzelne Gruppen ohne verwertbare Antwort oder ein
                vorzeitig beendeter Lauf. Ihre Buchungen bleiben offen. */}
            {(preview.warnings ?? []).map((warning, index) => (
              <AiReasonNotice
                key={`${warning.code}-${index}`}
                className={warning.level === "info" ? "placeholder-note" : "status-error"}
                code={warning.code}
                message={warning.message}
              />
            ))}

            {preview.suggestions.length === 0 ? (
              <p className="placeholder-note">
                Keine Gruppe mit mindestens zwei gleichartigen Buchungen gefunden. Einzelne
                Buchungen ordnest du direkt in der Buchungsliste zu.
              </p>
            ) : (
              <>
                <div className="category-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() =>
                      setSelected(
                        Object.fromEntries(
                          preview.suggestions.map((item) => [
                            item.cluster_key,
                            item.confidence >= SAFE_SUGGESTION_CONFIDENCE,
                          ]),
                        ),
                      )
                    }
                  >
                    Nur sichere auswählen
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() =>
                      setSelected(
                        Object.fromEntries(
                          preview.suggestions.map((item) => [item.cluster_key, true]),
                        ),
                      )
                    }
                  >
                    Alle auswählen
                  </button>
                  <button type="button" className="btn-secondary" onClick={() => setSelected({})}>
                    Keine
                  </button>
                </div>

                <ul className="suggestion-list">
                  {preview.suggestions.map((item) => {
                    const edit = edits[item.cluster_key];
                    const checked = Boolean(selected[item.cluster_key]);
                    return (
                      <li key={item.cluster_key} className="suggestion-item">
                        <label className="suggestion-head">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(event) =>
                              setSelected((current) => ({
                                ...current,
                                [item.cluster_key]: event.target.checked,
                              }))
                            }
                          />
                          <span className="suggestion-title">
                            {item.payee_label}
                            <span className="suggestion-meta">
                              {item.transaction_count} Buchungen ·{" "}
                              {formatMinorUnitsAsEuro(item.total_amount_minor)} ·{" "}
                              {item.first_booked_date} bis {item.last_booked_date}
                            </span>
                          </span>
                          <span
                            className={
                              item.confidence >= SAFE_SUGGESTION_CONFIDENCE
                                ? "status-badge status-badge--neutral"
                                : "status-badge status-badge--warning"
                            }
                          >
                            {Math.round(item.confidence * 100)} %
                          </span>
                        </label>

                        {item.kind === "existing_category" ? (
                          <p className="suggestion-body">
                            Passt zu <strong>{item.existing_category_name}</strong>
                          </p>
                        ) : (
                          <div className="suggestion-fields">
                            <div className="form-field">
                              <label
                                className="form-label"
                                htmlFor={`suggestion-name-${item.cluster_key}`}
                              >
                                Neue Kategorie
                              </label>
                              <input
                                id={`suggestion-name-${item.cluster_key}`}
                                className="form-input"
                                value={edit?.name ?? ""}
                                maxLength={120}
                                onChange={(event) =>
                                  setEdits((current) => ({
                                    ...current,
                                    [item.cluster_key]: {
                                      ...(current[item.cluster_key] as SuggestionEdit),
                                      name: event.target.value,
                                    },
                                  }))
                                }
                              />
                            </div>
                            <div className="form-field">
                              <label
                                className="form-label"
                                htmlFor={`suggestion-group-${item.cluster_key}`}
                              >
                                Gruppe
                              </label>
                              <select
                                id={`suggestion-group-${item.cluster_key}`}
                                className="form-input"
                                value={edit?.groupId ?? ""}
                                onChange={(event) =>
                                  setEdits((current) => ({
                                    ...current,
                                    [item.cluster_key]: {
                                      ...(current[item.cluster_key] as SuggestionEdit),
                                      groupId: event.target.value,
                                    },
                                  }))
                                }
                              >
                                <option value="">– bitte wählen –</option>
                                {activeGroups.map((group) => (
                                  <option key={group.id} value={group.id}>
                                    {group.name}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="form-field">
                              <label
                                className="form-label"
                                htmlFor={`suggestion-bucket-${item.cluster_key}`}
                              >
                                Budgetbereich
                              </label>
                              <select
                                id={`suggestion-bucket-${item.cluster_key}`}
                                className="form-input"
                                value={edit?.budgetBucket ?? ""}
                                onChange={(event) =>
                                  setEdits((current) => ({
                                    ...current,
                                    [item.cluster_key]: {
                                      ...(current[item.cluster_key] as SuggestionEdit),
                                      budgetBucket: event.target.value as CategoryBudgetBucket | "",
                                    },
                                  }))
                                }
                              >
                                {BUDGET_BUCKET_OPTIONS.map((option) => (
                                  <option key={option.value} value={option.value}>
                                    {option.label}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <label className="suggestion-checkbox">
                              <input
                                type="checkbox"
                                checked={edit?.isTaxDeductible ?? false}
                                onChange={(event) =>
                                  setEdits((current) => ({
                                    ...current,
                                    [item.cluster_key]: {
                                      ...(current[item.cluster_key] as SuggestionEdit),
                                      isTaxDeductible: event.target.checked,
                                    },
                                  }))
                                }
                              />
                              Steuerlich absetzbar
                            </label>
                          </div>
                        )}

                        {item.reason && <p className="suggestion-reason">{item.reason}</p>}
                      </li>
                    );
                  })}
                </ul>

                <label className="suggestion-checkbox">
                  <input
                    type="checkbox"
                    checked={assignTransactions}
                    onChange={(event) => setAssignTransactions(event.target.checked)}
                  />
                  Die betroffenen Buchungen gleich zuordnen
                </label>
              </>
            )}

            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={onClose}>
                Abbrechen
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={
                  selectedKeys.length === 0 || incompleteSelection || applyMutation.isPending
                }
                title={
                  incompleteSelection
                    ? "Für jeden ausgewählten neuen Vorschlag braucht es Name und Gruppe."
                    : undefined
                }
                onClick={handleApply}
              >
                {applyMutation.isPending ? "Wird übernommen…" : `${selectedKeys.length} übernehmen`}
              </button>
            </div>
          </>
        )}

        {result && (
          <div className="operation-preview" role="status">
            <p>
              {result.created_categories} Kategorien angelegt · {result.linked_existing} vorhandenen
              zugeordnet · {result.assigned_transactions} Buchungen kategorisiert
              {result.skipped_transactions > 0
                ? ` · ${result.skipped_transactions} übersprungen (bereits zugeordnet)`
                : ""}
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-primary" onClick={onClose}>
                Fertig
              </button>
            </div>
          </div>
        )}

        {enqueueMutation.isError && (
          <AiErrorNotice
            role="alert"
            className="status-error"
            error={enqueueMutation.error}
            fallback={errorText(
              enqueueMutation.error,
              "Die Vorschläge konnten nicht berechnet werden.",
            )}
          />
        )}
        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}
      </div>
    </Modal>
  );
}

function StandardPackageModal({ onClose }: { onClose: () => void }) {
  const applyMutation = useApplyStandardPackage();
  const previewQuery = useStandardPackagePreview(true);
  const [confirmed, setConfirmed] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const preview: StandardPackagePreview | null = previewQuery.data ?? null;

  const handleApply = () => {
    if (!preview) {
      return;
    }
    applyMutation.mutate(
      {
        operation_id: preview.operation_id,
        request_id: newRequestId(),
        confirmed_matches: confirmed,
      },
      {
        onSuccess: onClose,
        onError: (error) => {
          setFormError(errorText(error, "Standardpaket konnte nicht angewendet werden."));
          if (apiErrorDetail(error) === "category_preview_stale") {
            void previewQuery.refetch();
          }
        },
      },
    );
  };

  return (
    <Modal title="Standardkategorien ergänzen" onClose={onClose}>
      <div className="edit-transaction-form">
        <p className="card-body">
          Das Anwenden ist additiv und idempotent: Es legt nur fehlende Kategorien an und ändert,
          archiviert oder benennt nichts still um. Eine vorhandene Kategorie wird nur nach
          ausdrücklicher Bestätigung als Standardkategorie übernommen.
        </p>

        {previewQuery.isFetching && <p role="status">Vorschau wird berechnet…</p>}
        {previewQuery.isError && (
          <p role="alert" className="status-error">
            {errorText(previewQuery.error, "Vorschau konnte nicht geladen werden.")}
          </p>
        )}

        {preview && (
          <div className="operation-preview" role="status">
            <p>
              Neu: {preview.new_count} · bereits vorhanden: {preview.already_present_count} ·
              mögliche Entsprechung: {preview.possible_match_count} · Namenskonflikt:{" "}
              {preview.name_conflict_count}
            </p>
            {preview.new_groups.length > 0 && <p>Neue Gruppen: {preview.new_groups.join(", ")}</p>}

            {preview.entries.filter((entry) => entry.status === "possible_match").length > 0 && (
              <>
                <h3 className="card-heading">Mögliche Entsprechungen bestätigen</h3>
                <ul className="standard-package-matches">
                  {preview.entries
                    .filter((entry) => entry.status === "possible_match")
                    .map((entry) => (
                      <li key={entry.semantic_key}>
                        <label style={{ display: "flex", gap: "var(--space-2)" }}>
                          <input
                            type="checkbox"
                            checked={Boolean(confirmed[entry.semantic_key])}
                            onChange={(event) => {
                              setConfirmed((current) => {
                                const next = { ...current };
                                if (event.target.checked && entry.existing_category_id) {
                                  next[entry.semantic_key] = entry.existing_category_id;
                                } else {
                                  delete next[entry.semantic_key];
                                }
                                return next;
                              });
                            }}
                          />
                          <span>
                            „{entry.existing_category_name}“ ist {entry.group_name} · {entry.name}
                          </span>
                        </label>
                      </li>
                    ))}
                </ul>
              </>
            )}
          </div>
        )}

        {formError && (
          <p role="alert" className="status-error">
            {formError}
          </p>
        )}

        <div className="modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Abbrechen
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!preview || applyMutation.isPending}
            onClick={handleApply}
          >
            {applyMutation.isPending ? "Wird angewendet…" : "Ergänzung anwenden"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Seite
// ---------------------------------------------------------------------------

export function CategoriesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { canEdit, isOwner } = useCategoryContext();
  const catalogQuery = useCategoryCatalog({ includeArchived: true });
  const standardPackageQuery = useStandardPackageStatus();
  const reorderGroupsMutation = useReorderCategoryGroups();
  const reorderCategoriesMutation = useReorderCategories();
  const restoreGroupMutation = useRestoreCategoryGroup();

  const [showArchived, setShowArchived] = useState(false);
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  // Genau EIN offenes Zeilen-/Gruppenmenue, identifiziert als
  // "category:<id>" bzw. "group:<id>". Ein Set waere hier falsch: zwei
  // gleichzeitig offene Popups ueberlagern sich.
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());

  const linkedTaskId = searchParams.get("ai_task");
  const activeDialog =
    dialog ?? (linkedTaskId ? { kind: "ai-suggestions" as const, taskId: linkedTaskId } : null);

  const toggleMenu = (key: string) => setOpenMenu((current) => (current === key ? null : key));

  const toggleGroup = (groupId: string) =>
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (!next.delete(groupId)) {
        next.add(groupId);
      }
      return next;
    });

  const dragGroupIndex = useRef<number | null>(null);
  const dragCategory = useRef<{ groupId: string; index: number } | null>(null);

  const catalog = catalogQuery.data;
  const allGroups = useMemo(() => catalog?.groups ?? [], [catalog]);

  // Der Picker in den Dialogen braucht ausschließlich aktive Einträge - der
  // Katalog ist strukturell kompatibel zum Picker-Baum.
  const pickerGroups = useMemo<CategoryGroup[]>(
    () =>
      allGroups
        .filter((group) => !group.is_archived)
        .map((group) => ({
          ...group,
          categories: group.categories.filter((category) => !category.is_archived),
        })),
    [allGroups],
  );

  const normalizedSearch = normalizeCategorySearchText(search.trim());

  const visibleGroups = useMemo(() => {
    return allGroups
      .filter((group) => {
        if (!showArchived) {
          return !group.is_archived;
        }
        // Im Archiv-Modus zaehlen auch aktive Gruppen, die archivierte
        // Kategorien enthalten - sonst waeren die dort nicht auffindbar.
        return group.is_archived || group.categories.some((category) => category.is_archived);
      })
      .map((group) => {
        const categories = group.categories.filter(
          (category) => category.is_archived === showArchived,
        );
        if (!normalizedSearch) {
          return { group, categories };
        }
        const groupMatches = normalizeCategorySearchText(group.name).includes(normalizedSearch);
        const filtered = categories.filter((category) =>
          normalizeCategorySearchText(
            [category.name, ...(category.synonyms ?? []), category.description ?? ""].join(" "),
          ).includes(normalizedSearch),
        );
        return { group, categories: groupMatches ? categories : filtered };
      })
      .filter(({ group, categories }) => {
        if (!normalizedSearch) {
          return true;
        }
        return (
          categories.length > 0 ||
          normalizeCategorySearchText(group.name).includes(normalizedSearch)
        );
      })
      .sort((a, b) => a.group.sort_order - b.group.sort_order);
  }, [allGroups, normalizedSearch, showArchived]);

  const reorderError = (error: unknown) =>
    setPageError(errorText(error, "Die Sortierung konnte nicht gespeichert werden."));

  const applyGroupOrder = (from: number, to: number) => {
    if (!catalog) {
      return;
    }
    const activeGroups = allGroups.filter((group) => !group.is_archived);
    const reordered = moveItem(activeGroups, from, to);
    if (reordered === activeGroups) {
      return;
    }
    setPageError(null);
    reorderGroupsMutation.mutate(
      {
        taxonomy_revision: catalog.taxonomy_revision,
        ordered_ids: reordered.map((group) => group.id),
      },
      { onError: reorderError },
    );
  };

  const applyCategoryOrder = (group: CatalogGroup, from: number, to: number) => {
    if (!catalog) {
      return;
    }
    const active = group.categories.filter((category) => !category.is_archived);
    const reordered = moveItem(active, from, to);
    if (reordered === active) {
      return;
    }
    setPageError(null);
    reorderCategoriesMutation.mutate(
      {
        taxonomy_revision: catalog.taxonomy_revision,
        group_id: group.id,
        ordered_ids: reordered.map((category) => category.id),
      },
      { onError: reorderError },
    );
  };

  const closeDialog = () => {
    setDialog(null);
    if (searchParams.has("ai_task")) {
      const next = new URLSearchParams(searchParams);
      next.delete("ai_task");
      setSearchParams(next, { replace: true });
    }
  };

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Kategorien</h1>
          <p className="placeholder-note">
            Kategorien werden nie gelöscht, sondern archiviert – historische Buchungen, Budgets und
            Berichte bleiben dadurch vollständig.
          </p>
        </div>
      </div>

      <div className="category-toolbar">
        <div className="form-field category-toolbar-search">
          <label className="form-label" htmlFor="category-search">
            Suche
          </label>
          <input
            id="category-search"
            className="form-input"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Gruppe, Kategorie oder Synonym"
          />
        </div>

        <div className="filter-tabs" role="group" aria-label="Aktiv oder archiviert">
          <button
            type="button"
            className={showArchived ? "filter-tab" : "filter-tab filter-tab--active"}
            aria-pressed={!showArchived}
            onClick={() => setShowArchived(false)}
          >
            Aktiv
          </button>
          <button
            type="button"
            className={showArchived ? "filter-tab filter-tab--active" : "filter-tab"}
            aria-pressed={showArchived}
            onClick={() => setShowArchived(true)}
          >
            Archiviert
          </button>
        </div>

        {canEdit && (
          <div className="category-toolbar-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={allGroups.filter((group) => !group.is_archived).length === 0}
              onClick={() => {
                const firstGroup = allGroups.find((group) => !group.is_archived);
                if (firstGroup) {
                  setDialog({ kind: "category-create", groupId: firstGroup.id });
                }
              }}
            >
              Kategorie hinzufügen
            </button>

            {/* Bewusst gleichrangig neben "Kategorie hinzufügen": fuer einen
                bestehenden Buchungsbestand ist das der schnellere Weg zu einer
                brauchbaren Struktur als 50 Kategorien von Hand. */}
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setDialog({ kind: "ai-suggestions" })}
            >
              Kategorien vorschlagen (KI)
            </button>

            <div className="category-menu">
              <button
                type="button"
                className="btn-secondary"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                onClick={() => setMenuOpen((current) => !current)}
              >
                Weitere Aktionen ▾
              </button>
              {menuOpen && (
                <div className="category-menu-popup" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    className="category-menu-item"
                    onClick={() => {
                      setMenuOpen(false);
                      setDialog({ kind: "group-create" });
                    }}
                  >
                    Gruppe hinzufügen
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="category-menu-item"
                    disabled={!isOwner}
                    title={
                      isOwner
                        ? undefined
                        : "Ein haushaltsweites Standardpaket darf nur die Besitzerin bzw. der Besitzer anwenden."
                    }
                    onClick={() => {
                      setMenuOpen(false);
                      setDialog({ kind: "standard-package" });
                    }}
                  >
                    Standardkategorien ergänzen
                    {standardPackageQuery.data?.upgrade_available ? " (Version verfügbar)" : ""}
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {!canEdit && (
        <p className="placeholder-note" role="status">
          Deine Rolle in diesem Haushalt darf Kategorien ansehen, aber nicht ändern.
        </p>
      )}

      {catalogQuery.isLoading && <p role="status">Kategorien werden geladen…</p>}
      {catalogQuery.isError && (
        <p role="alert" className="status-error">
          Kategorien konnten nicht geladen werden.
        </p>
      )}

      {pageError && (
        <p role="alert" className="status-error">
          {pageError}
        </p>
      )}

      {catalog && visibleGroups.length === 0 && (
        <p className="placeholder-note">
          {showArchived ? "Keine archivierten Einträge." : "Noch keine Kategoriegruppen."}
        </p>
      )}

      <div className="category-group-list">
        {visibleGroups.map(({ group, categories }, groupIndex) => {
          const collapsed = collapsedGroups.has(group.id);
          return (
            <article
              key={group.id}
              className="card category-group-card"
              draggable={canEdit && !showArchived}
              onDragStart={() => {
                dragGroupIndex.current = groupIndex;
              }}
              onDragOver={(event) => {
                if (dragGroupIndex.current !== null) {
                  event.preventDefault();
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                const from = dragGroupIndex.current;
                dragGroupIndex.current = null;
                if (from !== null && from !== groupIndex) {
                  applyGroupOrder(from, groupIndex);
                }
              }}
            >
              <header className="category-group-header">
                <div className="category-group-title">
                  <button
                    type="button"
                    className="icon-btn"
                    aria-expanded={!collapsed}
                    aria-label={
                      collapsed
                        ? `Gruppe ${group.name} ausklappen`
                        : `Gruppe ${group.name} einklappen`
                    }
                    onClick={() => toggleGroup(group.id)}
                  >
                    {collapsed ? "▸" : "▾"}
                  </button>
                  {group.color && (
                    <span
                      className="category-picker-dot"
                      style={{ backgroundColor: group.color }}
                      aria-hidden="true"
                    />
                  )}
                  {group.icon && <span aria-hidden="true">{group.icon}</span>}
                  <h2 className="card-heading">{group.name}</h2>
                  {group.is_archived && (
                    <span className="status-badge status-badge--warning">Archiviert</span>
                  )}
                  <span className="status-badge status-badge--neutral">
                    {group.active_category_count} aktiv · {group.archived_category_count} archiviert
                  </span>
                </div>

                {canEdit && (
                  <div className="category-actions">
                    {!group.is_archived && (
                      <>
                        <button
                          type="button"
                          className="btn-secondary"
                          aria-label={`Kategorie zu ${group.name} hinzufügen`}
                          onClick={() => setDialog({ kind: "category-create", groupId: group.id })}
                        >
                          Kategorie hinzufügen
                        </button>
                        <button
                          type="button"
                          className="icon-btn"
                          aria-label={`Gruppe ${group.name} nach oben`}
                          title="Nach oben"
                          disabled={groupIndex === 0}
                          onClick={() => applyGroupOrder(groupIndex, groupIndex - 1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="icon-btn"
                          aria-label={`Gruppe ${group.name} nach unten`}
                          title="Nach unten"
                          disabled={groupIndex === visibleGroups.length - 1}
                          onClick={() => applyGroupOrder(groupIndex, groupIndex + 1)}
                        >
                          ↓
                        </button>
                        <RowMenu
                          label={`Weitere Aktionen für Gruppe ${group.name}`}
                          open={openMenu === `group:${group.id}`}
                          onToggle={() => toggleMenu(`group:${group.id}`)}
                        >
                          <button
                            type="button"
                            role="menuitem"
                            className="category-menu-item"
                            onClick={() => {
                              setOpenMenu(null);
                              setDialog({ kind: "group-rename", group });
                            }}
                          >
                            Umbenennen
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            className="category-menu-item"
                            onClick={() => {
                              setOpenMenu(null);
                              setDialog({ kind: "group-archive", group });
                            }}
                          >
                            Archivieren
                          </button>
                        </RowMenu>
                      </>
                    )}
                    {group.is_archived && (
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={restoreGroupMutation.isPending}
                        onClick={() => {
                          setPageError(null);
                          restoreGroupMutation.mutate(group.id, {
                            onError: (error) =>
                              setPageError(
                                errorText(error, "Gruppe konnte nicht wiederhergestellt werden."),
                              ),
                          });
                        }}
                      >
                        Wiederherstellen
                      </button>
                    )}
                  </div>
                )}
              </header>

              {!collapsed &&
                (categories.length === 0 ? (
                  <p className="placeholder-note">Keine Kategorien in dieser Gruppe.</p>
                ) : (
                  /* Tabelle statt Kachelzeilen: mit 50+ Kategorien braucht das
                   Auge eine Spaltenkante zum Runterlesen. Die frueheren
                   Meta-Paare flatterten mittig und wiederholten die Gruppe,
                   die schon in der Ueberschrift steht. */
                  <div className="category-table-wrap">
                    <table className="category-table">
                      <thead>
                        <tr>
                          <th scope="col" className="category-th">
                            Kategorie
                          </th>
                          <th scope="col" className="category-th category-th--type">
                            Art
                          </th>
                          <th scope="col" className="category-th category-th--budget">
                            Budget
                          </th>
                          <th scope="col" className="category-th category-th--tax">
                            Steuer
                          </th>
                          <th scope="col" className="category-th category-th--usage">
                            Nutzung
                          </th>
                          {canEdit && (
                            <th scope="col" className="category-th category-th--actions">
                              Aktionen
                            </th>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {categories.map((category, categoryIndex) => (
                          <tr
                            key={category.id}
                            className="category-tr"
                            draggable={canEdit && !showArchived}
                            onDragStart={(event) => {
                              event.stopPropagation();
                              dragCategory.current = { groupId: group.id, index: categoryIndex };
                            }}
                            onDragOver={(event) => {
                              // Drag-and-drop sortiert ausschliesslich innerhalb
                              // derselben Gruppe - ein Gruppenwechsel laeuft ueber
                              // "In andere Gruppe verschieben" mit Vorschau.
                              if (dragCategory.current?.groupId === group.id) {
                                event.preventDefault();
                                event.stopPropagation();
                              }
                            }}
                            onDrop={(event) => {
                              const dragged = dragCategory.current;
                              dragCategory.current = null;
                              if (!dragged || dragged.groupId !== group.id) {
                                return;
                              }
                              event.preventDefault();
                              event.stopPropagation();
                              applyCategoryOrder(group, dragged.index, categoryIndex);
                            }}
                          >
                            <td className="category-td">
                              <div className="category-row-main">
                                {category.color && (
                                  <span
                                    className="category-picker-dot"
                                    style={{ backgroundColor: category.color }}
                                    aria-hidden="true"
                                  />
                                )}
                                {category.icon && <span aria-hidden="true">{category.icon}</span>}
                                <span className="category-row-name">{category.name}</span>
                                {category.is_archived && (
                                  <span className="status-badge status-badge--warning">
                                    Archiviert
                                  </span>
                                )}
                                {category.merged_into_name && (
                                  <span className="status-badge status-badge--neutral">
                                    Zusammengeführt mit {category.merged_into_name}
                                  </span>
                                )}
                              </div>
                            </td>
                            <td className="category-td">
                              {defaultTypeLabel(category.default_type)}
                            </td>
                            <td className="category-td">{budgetCellLabel(category)}</td>
                            <td className="category-td">
                              {category.is_tax_deductible ? "absetzbar" : "—"}
                            </td>
                            <td className="category-td category-td--usage">
                              {category.usage ? category.usage.total : "–"}
                            </td>

                            {canEdit && (
                              <td className="category-td category-td--actions">
                                <div className="category-actions">
                                  {!category.is_archived ? (
                                    <>
                                      <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() =>
                                          setDialog({ kind: "category-edit", category })
                                        }
                                      >
                                        Bearbeiten
                                      </button>
                                      <button
                                        type="button"
                                        className="icon-btn"
                                        aria-label={`${category.name} nach oben`}
                                        title="Nach oben"
                                        disabled={categoryIndex === 0}
                                        onClick={() =>
                                          applyCategoryOrder(
                                            group,
                                            categoryIndex,
                                            categoryIndex - 1,
                                          )
                                        }
                                      >
                                        ↑
                                      </button>
                                      <button
                                        type="button"
                                        className="icon-btn"
                                        aria-label={`${category.name} nach unten`}
                                        title="Nach unten"
                                        disabled={categoryIndex === categories.length - 1}
                                        onClick={() =>
                                          applyCategoryOrder(
                                            group,
                                            categoryIndex,
                                            categoryIndex + 1,
                                          )
                                        }
                                      >
                                        ↓
                                      </button>
                                      <RowMenu
                                        label={`Weitere Aktionen für ${category.name}`}
                                        open={openMenu === `category:${category.id}`}
                                        onToggle={() => toggleMenu(`category:${category.id}`)}
                                      >
                                        <button
                                          type="button"
                                          role="menuitem"
                                          className="category-menu-item"
                                          onClick={() => {
                                            setOpenMenu(null);
                                            setDialog({ kind: "category-move", category });
                                          }}
                                        >
                                          In andere Gruppe verschieben
                                        </button>
                                        <button
                                          type="button"
                                          role="menuitem"
                                          className="category-menu-item"
                                          onClick={() => {
                                            setOpenMenu(null);
                                            setDialog({ kind: "category-archive", category });
                                          }}
                                        >
                                          Archivieren
                                        </button>
                                        {isOwner && (
                                          <button
                                            type="button"
                                            role="menuitem"
                                            className="category-menu-item"
                                            onClick={() => {
                                              setOpenMenu(null);
                                              setDialog({ kind: "category-merge", category });
                                            }}
                                          >
                                            Zusammenführen
                                          </button>
                                        )}
                                      </RowMenu>
                                    </>
                                  ) : (
                                    <button
                                      type="button"
                                      className="btn-secondary"
                                      onClick={() =>
                                        setDialog({ kind: "category-restore", category })
                                      }
                                    >
                                      Wiederherstellen
                                    </button>
                                  )}
                                </div>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
            </article>
          );
        })}
      </div>

      {activeDialog?.kind === "category-create" && (
        <CategoryFormModal
          groups={allGroups}
          category={null}
          initialGroupId={activeDialog.groupId}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "category-edit" && (
        <CategoryFormModal
          groups={allGroups}
          category={activeDialog.category}
          initialGroupId={activeDialog.category.group_id}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "group-create" && (
        <GroupFormModal group={null} onClose={closeDialog} />
      )}
      {activeDialog?.kind === "group-rename" && (
        <GroupFormModal group={activeDialog.group} onClose={closeDialog} />
      )}
      {activeDialog?.kind === "category-move" && (
        <MoveCategoryModal
          category={activeDialog.category}
          groups={allGroups}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "category-archive" && (
        <ArchiveCategoryModal
          category={activeDialog.category}
          pickerGroups={pickerGroups}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "category-restore" && (
        <RestoreCategoryModal
          category={activeDialog.category}
          groups={allGroups}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "category-merge" && (
        <MergeCategoryModal
          category={activeDialog.category}
          pickerGroups={pickerGroups}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "group-archive" && (
        <ArchiveGroupModal
          group={activeDialog.group}
          pickerGroups={pickerGroups}
          onClose={closeDialog}
        />
      )}
      {activeDialog?.kind === "standard-package" && <StandardPackageModal onClose={closeDialog} />}
      {activeDialog?.kind === "ai-suggestions" && (
        <CategorySuggestionsModal
          groups={allGroups}
          initialTaskId={activeDialog.taskId}
          onClose={closeDialog}
        />
      )}
    </section>
  );
}
