import { useRef, useState } from "react";
import {
  useCostItems,
  useCostSections,
  useCreateCostItem,
  useCreateCostSection,
  useDeleteCostItem,
  useReplaceCostSchedule,
  useUpdateCostItem,
} from "../../hooks/useProjects";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type {
  CalculationMode,
  CostItem,
  CostItemCreateInput,
  CostItemStatus,
  CostSection,
} from "../../lib/types";
import { ActualsPanel } from "./ActualsPanel";
import { CostAiSuggestionPanel } from "./CostAiSuggestionPanel";
import { Modal } from "../Modal";

const STATUS_LABELS: Record<CostItemStatus, string> = {
  estimated: "Schätzung",
  quoted: "Angebot",
  committed: "Beauftragt",
  partially_paid: "Teilweise bezahlt",
  paid: "Bezahlt",
  cancelled: "Storniert",
};

const CALC_MODE_LABELS: Record<CalculationMode, string> = {
  fixed: "Fest",
  quantity: "Menge × Preis",
  percentage: "Prozentual",
};

type EditableCostItemField =
  "name" | "status" | "estimate_manual_minor" | "quoted_amount_minor" | "committed_amount_minor";

function amountDraft(value: number | null | undefined): string {
  return value == null ? "" : (value / 100).toFixed(2).replace(".", ",");
}

function handleCellKeyDown(event: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>): void {
  if (event.key === "Enter") {
    event.preventDefault();
    event.currentTarget.blur();
  }
  if (event.key === "Escape") {
    event.preventDefault();
    event.currentTarget.value = event.currentTarget.dataset.original ?? "";
    event.currentTarget.blur();
  }
}

interface CostPlanTableProps {
  projectId: string;
  scenarioId: string;
  initialAiTaskId?: string | null;
  onAiTaskClose?: () => void;
}

export function CostPlanTable({
  projectId,
  scenarioId,
  initialAiTaskId = null,
  onAiTaskClose,
}: CostPlanTableProps) {
  const { data: sections, isLoading: sectionsLoading } = useCostSections(projectId, scenarioId);
  const { data: items, isLoading: itemsLoading } = useCostItems(projectId, scenarioId);
  const createSection = useCreateCostSection();
  const [isAddSectionOpen, setIsAddSectionOpen] = useState(false);
  const [sectionName, setSectionName] = useState("");
  const [actualsItem, setActualsItem] = useState<CostItem | null>(null);
  const [isAiSuggestionOpen, setIsAiSuggestionOpen] = useState(false);
  const showAiSuggestion = isAiSuggestionOpen || Boolean(initialAiTaskId);

  if (sectionsLoading || itemsLoading) {
    return <p className="placeholder-note">Lade Kostenplan...</p>;
  }

  const itemsBySection = new Map<string, CostItem[]>();
  for (const item of items ?? []) {
    const list = itemsBySection.get(item.section_id) ?? [];
    list.push(item);
    itemsBySection.set(item.section_id, list);
  }

  const handleCreateSection = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = sectionName.trim();
    if (!trimmed) return;
    createSection.mutate(
      {
        projectId,
        scenarioId,
        input: { name: trimmed, cost_class: "direct", sort_order: sections?.length ?? 0 },
      },
      {
        onSuccess: () => {
          setIsAddSectionOpen(false);
          setSectionName("");
        },
      },
    );
  };

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: "var(--space-3)",
          marginBottom: "var(--space-3)",
        }}
      >
        <button type="button" className="btn-secondary" onClick={() => setIsAiSuggestionOpen(true)}>
          KI-Kostenvorschlag
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setIsAddSectionOpen(true)}
          disabled={isAddSectionOpen}
        >
          + Abschnitt
        </button>
      </div>

      {isAddSectionOpen && (
        <form onSubmit={handleCreateSection} className="card cost-add-section-form">
          <div className="form-field" style={{ flex: 1, marginBottom: 0 }}>
            <label className="form-label" htmlFor="section-name">
              Name des neuen Abschnitts *
            </label>
            <input
              id="section-name"
              className="form-input"
              value={sectionName}
              onChange={(e) => setSectionName(e.target.value)}
              required
              autoFocus
            />
          </div>
          <button type="submit" className="btn-primary" disabled={createSection.isPending}>
            {createSection.isPending ? "Wird angelegt …" : "Anlegen"}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setIsAddSectionOpen(false);
              setSectionName("");
            }}
          >
            Abbrechen
          </button>
        </form>
      )}

      {(sections ?? []).map((section) => (
        <CostSectionCard
          key={section.id}
          projectId={projectId}
          scenarioId={scenarioId}
          section={section}
          items={itemsBySection.get(section.id) ?? []}
          onManageActuals={setActualsItem}
        />
      ))}

      <CostTotalsCard sections={sections ?? []} itemsBySection={itemsBySection} />

      {(sections ?? []).length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
          <p className="card-body">Noch kein Kostenabschnitt angelegt.</p>
        </div>
      )}

      {actualsItem && (
        <Modal
          title={`Ist-Kosten: ${actualsItem.name}`}
          size="wide"
          onClose={() => setActualsItem(null)}
        >
          <ActualsPanel projectId={projectId} item={actualsItem} />
        </Modal>
      )}

      {showAiSuggestion && (
        <CostAiSuggestionPanel
          projectId={projectId}
          scenarioId={scenarioId}
          initialTaskId={initialAiTaskId}
          onClose={() => {
            setIsAiSuggestionOpen(false);
            onAiTaskClose?.();
          }}
        />
      )}
    </div>
  );
}

interface CostSectionCardProps {
  projectId: string;
  scenarioId: string;
  section: CostSection;
  items: CostItem[];
  onManageActuals: (item: CostItem) => void;
}

/**
 * Positionen einer Kostenabschnitts als Ledger: bestehende Zeilen direkt
 * editierbar, neue Position in der Anlegezeile unten - kein Modal mehr fuer
 * das, was staendig gebraucht wird (siehe Lebensmittel-Wochenplan als Vorbild).
 */
function CostSectionCard({
  projectId,
  scenarioId,
  section,
  items,
  onManageActuals,
}: CostSectionCardProps) {
  const addNameRef = useRef<HTMLInputElement>(null);
  const createMutation = useCreateCostItem();
  const updateMutation = useUpdateCostItem();
  const deleteMutation = useDeleteCostItem();
  const scheduleMutation = useReplaceCostSchedule();

  const [rowValidationErrors, setRowValidationErrors] = useState<Record<string, string>>({});
  const [addValidationError, setAddValidationError] = useState("");

  const [draftName, setDraftName] = useState("");
  const [draftCalcMode, setDraftCalcMode] = useState<CalculationMode>("fixed");
  const [draftEstimateEuro, setDraftEstimateEuro] = useState("");
  const [draftStatus, setDraftStatus] = useState<CostItemStatus>("estimated");
  const [draftQuotedEuro, setDraftQuotedEuro] = useState("");
  const [draftCommittedEuro, setDraftCommittedEuro] = useState("");

  const clearRowValidationError = (itemId: string) => {
    setRowValidationErrors((current) => {
      if (!(itemId in current)) return current;
      const next = { ...current };
      delete next[itemId];
      return next;
    });
  };

  const showRowValidationError = (itemId: string, message: string) => {
    setRowValidationErrors((current) => ({ ...current, [itemId]: message }));
  };

  const commitItemField = (item: CostItem, field: EditableCostItemField, rawValue: string) => {
    let value: string | number | null;

    if (field === "name") {
      value = rawValue.trim();
      if (!value) {
        showRowValidationError(item.id, "Name fehlt");
        return;
      }
    } else if (field === "estimate_manual_minor") {
      value = rawValue.trim() ? parseAmountToMinorUnits(rawValue) : 0;
      if (value == null) {
        showRowValidationError(item.id, "Betrag ungültig");
        return;
      }
    } else if (field === "quoted_amount_minor" || field === "committed_amount_minor") {
      value = rawValue.trim() ? parseAmountToMinorUnits(rawValue) : null;
      if (rawValue.trim() && value == null) {
        showRowValidationError(item.id, "Betrag ungültig");
        return;
      }
    } else {
      value = rawValue as CostItemStatus;
    }

    const previous = item[field as keyof CostItem] as string | number | null;
    if (value === previous || (value == null && previous == null)) {
      clearRowValidationError(item.id);
      return;
    }

    updateMutation.mutate(
      {
        projectId,
        scenarioId,
        itemId: item.id,
        version: item.version,
        input: { [field]: value },
      },
      { onSuccess: () => clearRowValidationError(item.id) },
    );
  };

  const handleCreate = (e: { preventDefault(): void }) => {
    e.preventDefault();
    const trimmedName = draftName.trim();
    if (!trimmedName) {
      setAddValidationError("Name fehlt");
      addNameRef.current?.focus();
      return;
    }

    const estimateMinor = draftEstimateEuro.trim() ? parseAmountToMinorUnits(draftEstimateEuro) : 0;
    if (draftCalcMode === "fixed" && estimateMinor == null) {
      setAddValidationError("Schätzung ungültig");
      return;
    }
    const quotedMinor = draftQuotedEuro.trim() ? parseAmountToMinorUnits(draftQuotedEuro) : null;
    if (draftQuotedEuro.trim() && quotedMinor == null) {
      setAddValidationError("Angebot ungültig");
      return;
    }
    const committedMinor = draftCommittedEuro.trim()
      ? parseAmountToMinorUnits(draftCommittedEuro)
      : null;
    if (draftCommittedEuro.trim() && committedMinor == null) {
      setAddValidationError("Auftrag ungültig");
      return;
    }

    const input: CostItemCreateInput = {
      section_id: section.id,
      name: trimmedName,
      calculation_mode: draftCalcMode,
      estimate_manual_minor: draftCalcMode === "fixed" ? (estimateMinor ?? 0) : undefined,
      status: draftStatus,
      quoted_amount_minor: quotedMinor ?? undefined,
      committed_amount_minor: committedMinor ?? undefined,
    };

    setAddValidationError("");
    createMutation.mutate(
      { projectId, scenarioId, input },
      {
        onSuccess: () => {
          setDraftName("");
          setDraftEstimateEuro("");
          setDraftQuotedEuro("");
          setDraftCommittedEuro("");
          setDraftStatus("estimated");
          setDraftCalcMode("fixed");
          addNameRef.current?.focus();
        },
      },
    );
  };

  return (
    <div className="card" style={{ marginBottom: "var(--space-4)" }}>
      <h2 className="card-heading">{section.name}</h2>
      <div className="txn-table-wrapper">
        <table className="txn-table cost-plan-table">
          <thead>
            <tr>
              <th className="txn-th txn-th--position">Position</th>
              <th className="txn-th txn-th--calc-mode">Berechnung</th>
              <th className="txn-th txn-th--status">Status</th>
              <th className="txn-th txn-th--amount-wide">Schätzung</th>
              <th className="txn-th txn-th--amount-wide">Angebot</th>
              <th className="txn-th txn-th--amount-wide">Auftrag</th>
              <th className="txn-th txn-th--amount-wide">Forecast</th>
              <th className="txn-th txn-th--amount-wide">Ist</th>
              <th className="txn-th txn-th--amount-wide">Rest</th>
              <th className="txn-th txn-th--actions-wide"></th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr className="txn-tr">
                <td className="cost-table-message" colSpan={10}>
                  Noch keine Position — die erste direkt unten eintragen.
                </td>
              </tr>
            )}
            {items.map((item) => {
              const rowIsSaving =
                updateMutation.isPending && updateMutation.variables?.itemId === item.id;
              const rowHasError =
                updateMutation.isError && updateMutation.variables?.itemId === item.id;
              const rowWasSaved =
                updateMutation.isSuccess && updateMutation.variables?.itemId === item.id;
              const validationError = rowValidationErrors[item.id];
              const fieldProps = (original: string) => ({
                "data-original": original,
                onKeyDown: handleCellKeyDown,
              });

              return (
                <tr key={item.id} className="txn-tr cost-edit-row">
                  <td className="txn-td txn-td--position">
                    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
                      <input
                        className="cost-cell-input cost-cell-input--strong"
                        aria-label={`${item.name}: Position`}
                        defaultValue={item.name}
                        onBlur={(e) => commitItemField(item, "name", e.currentTarget.value)}
                        {...fieldProps(item.name)}
                      />
                      {item.estimate_source === "ai" && (
                        <span
                          className="status-badge status-badge--neutral"
                          title={
                            `KI-Schätzung${
                              item.ai_confidence !== null && item.ai_confidence !== undefined
                                ? ` · ${Math.round(item.ai_confidence * 100)} % Konfidenz`
                                : ""
                            }` + (item.ai_rationale ? `: ${item.ai_rationale}` : "")
                          }
                        >
                          KI
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="txn-td txn-td--calc-mode cost-read-cell">
                    {CALC_MODE_LABELS[item.calculation_mode]}
                  </td>
                  <td className="txn-td txn-td--status">
                    <select
                      className="cost-cell-input"
                      aria-label={`${item.name}: Status`}
                      defaultValue={item.status}
                      onBlur={(e) => commitItemField(item, "status", e.currentTarget.value)}
                      {...fieldProps(item.status)}
                    >
                      {Object.entries(STATUS_LABELS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="txn-td txn-td--amount-wide">
                    {item.calculation_mode === "fixed" ? (
                      <input
                        inputMode="decimal"
                        className="cost-cell-input cost-cell-input--mono"
                        aria-label={`${item.name}: Schätzung`}
                        defaultValue={amountDraft(item.estimate_manual_minor)}
                        onBlur={(e) =>
                          commitItemField(item, "estimate_manual_minor", e.currentTarget.value)
                        }
                        {...fieldProps(amountDraft(item.estimate_manual_minor))}
                      />
                    ) : (
                      <span className="cost-read-cell">
                        {formatMinorUnitsAsEuro(item.estimate_minor)}
                      </span>
                    )}
                  </td>
                  <td className="txn-td txn-td--amount-wide">
                    <input
                      inputMode="decimal"
                      className="cost-cell-input cost-cell-input--mono"
                      aria-label={`${item.name}: Angebot`}
                      placeholder="—"
                      defaultValue={amountDraft(item.quoted_amount_minor)}
                      onBlur={(e) =>
                        commitItemField(item, "quoted_amount_minor", e.currentTarget.value)
                      }
                      {...fieldProps(amountDraft(item.quoted_amount_minor))}
                    />
                  </td>
                  <td className="txn-td txn-td--amount-wide">
                    <input
                      inputMode="decimal"
                      className="cost-cell-input cost-cell-input--mono"
                      aria-label={`${item.name}: Auftrag`}
                      placeholder="—"
                      defaultValue={amountDraft(item.committed_amount_minor)}
                      onBlur={(e) =>
                        commitItemField(item, "committed_amount_minor", e.currentTarget.value)
                      }
                      {...fieldProps(amountDraft(item.committed_amount_minor))}
                    />
                  </td>
                  <td className="txn-td txn-td--amount-wide cost-read-cell">
                    {formatMinorUnitsAsEuro(item.forecast_minor)}
                  </td>
                  <td className="txn-td txn-td--amount-wide cost-read-cell">
                    {formatMinorUnitsAsEuro(item.actual_amount_minor)}
                  </td>
                  <td className="txn-td txn-td--amount-wide cost-read-cell">
                    {formatMinorUnitsAsEuro(item.forecast_minor - item.actual_amount_minor)}
                  </td>
                  <td className="txn-td txn-td--actions-wide">
                    <div className="cost-row-actions">
                      <span
                        className={`cost-save-state ${validationError || rowHasError ? "cost-save-state--error" : ""}`}
                        role={validationError || rowHasError ? "alert" : "status"}
                      >
                        {validationError ??
                          (rowIsSaving
                            ? "Speichert …"
                            : rowHasError
                              ? "Fehler"
                              : rowWasSaved
                                ? "Gespeichert"
                                : "")}
                      </span>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => onManageActuals(item)}
                      >
                        Ist-Kosten
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        title="Restbetrag heute einplanen"
                        onClick={() =>
                          scheduleMutation.mutate({
                            projectId,
                            scenarioId: item.scenario_id,
                            itemId: item.id,
                            input: {
                              entries:
                                item.forecast_minor > 0
                                  ? [
                                      {
                                        due_on:
                                          item.expected_end_on ??
                                          new Date().toISOString().slice(0, 10),
                                        amount_minor: item.forecast_minor,
                                      },
                                    ]
                                  : [],
                            },
                          })
                        }
                      >
                        Zahlungsplan
                      </button>
                      <button
                        type="button"
                        className="cost-delete-button"
                        aria-label={`Position ${item.name} löschen`}
                        onClick={() =>
                          deleteMutation.mutate({
                            projectId,
                            scenarioId: item.scenario_id,
                            itemId: item.id,
                          })
                        }
                      >
                        Löschen
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            <tr className="cost-add-row">
              <td className="txn-td txn-td--position">
                <input
                  ref={addNameRef}
                  className="cost-cell-input cost-cell-input--strong"
                  aria-label="Neue Position: Name"
                  placeholder="z. B. Fenster"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreate(e);
                  }}
                />
              </td>
              <td className="txn-td txn-td--calc-mode">
                <select
                  className="cost-cell-input"
                  aria-label="Neue Position: Berechnung"
                  value={draftCalcMode}
                  onChange={(e) => setDraftCalcMode(e.target.value as CalculationMode)}
                >
                  <option value="fixed">Fest</option>
                  <option value="quantity">Menge × Preis</option>
                </select>
              </td>
              <td className="txn-td txn-td--status">
                <select
                  className="cost-cell-input"
                  aria-label="Neue Position: Status"
                  value={draftStatus}
                  onChange={(e) => setDraftStatus(e.target.value as CostItemStatus)}
                >
                  {Object.entries(STATUS_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </td>
              <td className="txn-td txn-td--amount-wide">
                {draftCalcMode === "fixed" ? (
                  <input
                    inputMode="decimal"
                    className="cost-cell-input cost-cell-input--mono"
                    aria-label="Neue Position: Schätzung"
                    placeholder="0,00"
                    value={draftEstimateEuro}
                    onChange={(e) => setDraftEstimateEuro(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleCreate(e);
                    }}
                  />
                ) : (
                  <span className="cost-add-row__hint">Wird berechnet</span>
                )}
              </td>
              <td className="txn-td txn-td--amount-wide">
                <input
                  inputMode="decimal"
                  className="cost-cell-input cost-cell-input--mono"
                  aria-label="Neue Position: Angebot"
                  placeholder="—"
                  value={draftQuotedEuro}
                  onChange={(e) => setDraftQuotedEuro(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreate(e);
                  }}
                />
              </td>
              <td className="txn-td txn-td--amount-wide">
                <input
                  inputMode="decimal"
                  className="cost-cell-input cost-cell-input--mono"
                  aria-label="Neue Position: Auftrag"
                  placeholder="—"
                  value={draftCommittedEuro}
                  onChange={(e) => setDraftCommittedEuro(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreate(e);
                  }}
                />
              </td>
              <td className="txn-td txn-td--amount-wide cost-add-row__hint">Wird berechnet</td>
              <td className="txn-td txn-td--amount-wide cost-add-row__hint">—</td>
              <td className="txn-td txn-td--amount-wide cost-add-row__hint">—</td>
              <td className="txn-td txn-td--actions-wide">
                <div className="cost-row-actions">
                  <span
                    className={`cost-save-state ${addValidationError || createMutation.isError ? "cost-save-state--error" : ""}`}
                    role={addValidationError || createMutation.isError ? "alert" : "status"}
                  >
                    {createMutation.isPending
                      ? "Wird angelegt …"
                      : (addValidationError ??
                        (createMutation.isError ? "Anlegen fehlgeschlagen" : ""))}
                  </span>
                  <button
                    type="button"
                    className="btn-primary cost-add-button"
                    disabled={createMutation.isPending}
                    onClick={handleCreate}
                  >
                    + Hinzufügen
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
          {/* Summe je Abschnitt. Ohne sie musste man die Zeilen im Kopf
              addieren, um zu sehen, was etwa der Erwerb insgesamt kostet -
              gerade bei Prozentpositionen, deren Betrag erst der Server
              ausrechnet, ist das nicht zumutbar. */}
          {items.length > 0 && (
            <tfoot>
              <tr className="txn-tr cost-total-row">
                <td className="txn-td" colSpan={3}>
                  <strong>Summe {section.name}</strong>
                </td>
                <td className="txn-td txn-td--amount-wide">
                  {formatMinorUnitsAsEuro(sumBy(items, (item) => item.estimate_minor))}
                </td>
                <td className="txn-td txn-td--amount-wide"></td>
                <td className="txn-td txn-td--amount-wide"></td>
                <td className="txn-td txn-td--amount-wide">
                  <strong>{formatMinorUnitsAsEuro(sumBy(items, (i) => i.forecast_minor))}</strong>
                </td>
                <td className="txn-td txn-td--amount-wide">
                  {formatMinorUnitsAsEuro(sumBy(items, (item) => item.actual_amount_minor))}
                </td>
                <td className="txn-td txn-td--amount-wide">
                  {formatMinorUnitsAsEuro(
                    sumBy(items, (item) => item.forecast_minor - item.actual_amount_minor),
                  )}
                </td>
                <td className="txn-td txn-td--actions-wide"></td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}

/** Summiert ein Feld ueber alle Positionen. */
function sumBy(items: CostItem[], pick: (item: CostItem) => number): number {
  return items.reduce((total, item) => total + pick(item), 0);
}

/**
 * Gesamtuebersicht am Fuss des Kostenplans.
 *
 * Die Kennzahlen ganz oben zeigen nur das Endergebnis. Beim Immobilienkauf ist
 * aber gerade die Aufteilung die Aussage: was kostet der Erwerb, was die
 * Modernisierung. Deshalb hier Abschnitt fuer Abschnitt und darunter die
 * Gesamtsumme - dieselben Zahlen wie oben, nur aufgeschluesselt.
 */
function CostTotalsCard({
  sections,
  itemsBySection,
}: {
  sections: CostSection[];
  itemsBySection: Map<string, CostItem[]>;
}) {
  const rows = sections
    .map((section) => {
      const items = itemsBySection.get(section.id) ?? [];
      return {
        id: section.id,
        name: section.name,
        estimate: sumBy(items, (item) => item.estimate_minor),
        forecast: sumBy(items, (item) => item.forecast_minor),
        actual: sumBy(items, (item) => item.actual_amount_minor),
        count: items.length,
      };
    })
    .filter((row) => row.count > 0);

  if (rows.length === 0) return null;

  const totalEstimate = rows.reduce((sum, row) => sum + row.estimate, 0);
  const totalForecast = rows.reduce((sum, row) => sum + row.forecast, 0);
  const totalActual = rows.reduce((sum, row) => sum + row.actual, 0);

  return (
    <div className="card" style={{ marginBottom: "var(--space-4)" }}>
      <h2 className="card-heading">Summen je Abschnitt</h2>
      <div className="txn-table-wrapper">
        <table className="txn-table">
          <thead>
            <tr>
              <th className="txn-th">Abschnitt</th>
              <th className="txn-th txn-th--amount-wide">Schätzung</th>
              <th className="txn-th txn-th--amount-wide">Forecast</th>
              <th className="txn-th txn-th--amount-wide">Ist</th>
              <th className="txn-th txn-th--amount-wide">Anteil</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr className="txn-tr" key={row.id}>
                <td className="txn-td">
                  <span className="txn-payee">{row.name}</span>
                  <div className="metric-hint">
                    {row.count} {row.count === 1 ? "Position" : "Positionen"}
                  </div>
                </td>
                <td className="txn-td txn-td--amount-wide">
                  {formatMinorUnitsAsEuro(row.estimate)}
                </td>
                <td className="txn-td txn-td--amount-wide">
                  {formatMinorUnitsAsEuro(row.forecast)}
                </td>
                <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(row.actual)}</td>
                <td className="txn-td txn-td--amount-wide">
                  {totalForecast > 0
                    ? `${((row.forecast / totalForecast) * 100).toLocaleString("de-DE", {
                        maximumFractionDigits: 1,
                      })} %`
                    : "—"}
                </td>
              </tr>
            ))}
            <tr className="txn-tr cost-total-row">
              <td className="txn-td">
                <strong>Gesamt</strong>
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(totalEstimate)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                <strong>{formatMinorUnitsAsEuro(totalForecast)}</strong>
              </td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(totalActual)}</td>
              <td className="txn-td txn-td--amount-wide">100 %</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="metric-hint" style={{ marginTop: "var(--space-2)" }}>
        Ohne Kostenpuffer des Szenarios — der steht in den Kennzahlen oben unter „Kostenpuffer“.
      </p>
    </div>
  );
}
