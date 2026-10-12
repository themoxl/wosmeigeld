import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  createWishlistItem,
  listGoals,
  listWishlistItems,
  startWishlistSavingsPlan,
  updateWishlistItem,
} from "../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type {
  SavingsGoal,
  SavingsPlanningMode,
  TrafficLightStatus,
  WishlistItem,
  WishlistItemUpdateInput,
  WishlistStatus,
} from "../lib/types";

const TRAFFIC_LIGHT_LABEL: Record<TrafficLightStatus, string> = {
  green: "Jetzt leistbar",
  yellow: "Bald leistbar",
  red: "Noch sparen",
};

const TRAFFIC_LIGHT_CLASS: Record<TrafficLightStatus, string> = {
  green: "status-badge--positive",
  yellow: "status-badge--highlight",
  red: "status-badge--error",
};

const WISHLIST_STATUS_LABELS: Record<WishlistStatus, string> = {
  open: "Offen",
  deferred: "Zurückgestellt",
  purchased: "Gekauft",
  archived: "Archiviert",
};

type WishlistFilter = WishlistStatus | "all";

function amountDraft(value: number): string {
  return (value / 100).toFixed(2).replace(".", ",");
}

function handleCellKeyDown(
  event: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
): void {
  if (event.key === "Enter") {
    if (event.currentTarget.tagName === "TEXTAREA" && !event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    event.currentTarget.blur();
  }
  if (event.key === "Escape") {
    event.preventDefault();
    event.currentTarget.value = event.currentTarget.dataset.original ?? "";
    event.currentTarget.blur();
  }
}

function wishlistStatus(item: WishlistItem): WishlistStatus {
  // Alte API- und Cache-Antworten hatten noch keinen Lebenszyklus. Diese
  // Einträge entsprechen fachlich offenen Wünschen und werden überall so behandelt.
  return item.status ?? "open";
}

export function WishlistPage({ embedded = false }: { embedded?: boolean }) {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const addNameRef = useRef<HTMLInputElement>(null);

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const [filter, setFilter] = useState<WishlistFilter>("open");
  const [name, setName] = useState("");
  const [priceEuro, setPriceEuro] = useState("");
  const [priority, setPriority] = useState(1);
  const [note, setNote] = useState("");
  const [goalId, setGoalId] = useState("");
  const [addValidationError, setAddValidationError] = useState("");
  const [rowValidationErrors, setRowValidationErrors] = useState<Record<string, string>>({});
  const [planWish, setPlanWish] = useState<WishlistItem | null>(null);
  const [planMode, setPlanMode] = useState<SavingsPlanningMode>("target_date");
  const [planTargetDate, setPlanTargetDate] = useState("");
  const [planMonthlyEuro, setPlanMonthlyEuro] = useState("");
  const [planStartingEuro, setPlanStartingEuro] = useState("");
  const [planInclude, setPlanInclude] = useState(true);
  const [planError, setPlanError] = useState("");

  const {
    data: items,
    isLoading,
    isError,
  } = useQuery<WishlistItem[]>({
    queryKey: ["wishlist", householdId],
    queryFn: () => listWishlistItems(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const { data: goals } = useQuery<SavingsGoal[]>({
    queryKey: ["goals", householdId],
    queryFn: () => listGoals(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const allItems = items ?? [];
  const openItems = allItems.filter((item) => wishlistStatus(item) === "open");
  const filteredItems = [...allItems]
    .filter((item) => filter === "all" || wishlistStatus(item) === filter)
    .sort((a, b) => a.priority - b.priority);
  const filterCounts: Record<WishlistFilter, number> = {
    open: openItems.length,
    deferred: allItems.filter((item) => wishlistStatus(item) === "deferred").length,
    purchased: allItems.filter((item) => wishlistStatus(item) === "purchased").length,
    archived: allItems.filter((item) => wishlistStatus(item) === "archived").length,
    all: allItems.length,
  };
  const newItemGoals = (goals ?? []).filter((goal) => goal.status !== "cancelled");
  const totalValueMinor = openItems.reduce((sum, item) => sum + item.price_minor, 0);
  const affordableCount = openItems.filter((item) => item.buy_traffic_light === "green").length;
  const linkedGoalCount = openItems.filter(
    (item) => item.goal_id && newItemGoals.some((goal) => goal.id === item.goal_id),
  ).length;

  const clearRowValidationError = (itemId: string) => {
    setRowValidationErrors((current) => {
      if (!(itemId in current)) return current;
      const next = { ...current };
      delete next[itemId];
      return next;
    });
  };

  const createMutation = useMutation({
    mutationFn: (input: {
      name: string;
      price_minor: number;
      priority: number;
      note?: string;
      goal_id?: string;
    }) => createWishlistItem(token!, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["wishlist", householdId] });
      setName("");
      setPriceEuro("");
      setPriority(1);
      setNote("");
      setGoalId("");
      setAddValidationError("");
      addNameRef.current?.focus();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ itemId, patch }: { itemId: string; patch: WishlistItemUpdateInput }) =>
      updateWishlistItem(token!, householdId, itemId, patch),
    onMutate: ({ itemId }) => clearRowValidationError(itemId),
    onSuccess: (_item, { itemId }) => {
      clearRowValidationError(itemId);
      void queryClient.invalidateQueries({ queryKey: ["wishlist", householdId] });
    },
  });

  const startPlanMutation = useMutation({
    mutationFn: (input: {
      itemId: string;
      planning_mode: SavingsPlanningMode;
      target_date: string | null;
      monthly_contribution_minor: number | null;
      starting_amount_minor: number;
      include_in_planning: boolean;
    }) =>
      startWishlistSavingsPlan(token!, householdId, input.itemId, {
        planning_mode: input.planning_mode,
        target_date: input.target_date,
        monthly_contribution_minor: input.monthly_contribution_minor,
        starting_amount_minor: input.starting_amount_minor,
        include_in_planning: input.include_in_planning,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["wishlist", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["goals", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["budget", householdId] });
      setPlanWish(null);
      setPlanTargetDate("");
      setPlanMonthlyEuro("");
      setPlanStartingEuro("");
      setPlanError("");
    },
  });

  const showRowValidationError = (itemId: string, message: string) => {
    setRowValidationErrors((current) => ({ ...current, [itemId]: message }));
  };

  const commitItemField = (
    item: WishlistItem,
    field: keyof WishlistItemUpdateInput,
    rawValue: string,
  ) => {
    let value: string | number | null = rawValue;

    if (field === "name") {
      value = rawValue.trim();
      if (!value) {
        showRowValidationError(item.id, "Name fehlt");
        return;
      }
    } else if (field === "price_minor") {
      value = parseAmountToMinorUnits(rawValue);
      if (value == null || value <= 0) {
        showRowValidationError(item.id, "Preis ungültig");
        return;
      }
    } else if (field === "priority") {
      value = Number(rawValue);
      if (!Number.isInteger(value) || value < 1 || value > 5) {
        showRowValidationError(item.id, "Priorität 1–5");
        return;
      }
    } else if (field === "goal_id") {
      value = rawValue || null;
    } else if (field === "note") {
      value = rawValue.trim() || null;
    } else if (field === "status") {
      value = rawValue as WishlistStatus;
    }

    const previous = field === "status" ? wishlistStatus(item) : item[field as keyof WishlistItem];
    if (value === previous || (value == null && previous == null)) {
      clearRowValidationError(item.id);
      return;
    }

    updateMutation.mutate({ itemId: item.id, patch: { [field]: value } });
  };

  const handleCreate = (event: React.FormEvent) => {
    event.preventDefault();
    const normalizedName = name.trim();
    const priceMinor = parseAmountToMinorUnits(priceEuro);

    if (!normalizedName) {
      setAddValidationError("Bezeichnung fehlt");
      addNameRef.current?.focus();
      return;
    }
    if (priceMinor == null || priceMinor <= 0) {
      setAddValidationError("Preis ungültig");
      return;
    }
    if (!Number.isInteger(priority) || priority < 1 || priority > 5) {
      setAddValidationError("Priorität 1–5");
      return;
    }

    setAddValidationError("");
    createMutation.mutate({
      name: normalizedName,
      price_minor: priceMinor,
      priority,
      note: note.trim() || undefined,
      goal_id: goalId || undefined,
    });
  };

  const handleStartPlan = (event: React.FormEvent) => {
    event.preventDefault();
    if (!planWish) return;
    const monthlyMinor = planMonthlyEuro.trim() ? parseAmountToMinorUnits(planMonthlyEuro) : null;
    const startingMinor = planStartingEuro.trim() ? parseAmountToMinorUnits(planStartingEuro) : 0;
    if (startingMinor == null || startingMinor < 0) {
      setPlanError("Bereits gespart ist ungültig");
      return;
    }
    if (planMode === "target_date" && !planTargetDate) {
      setPlanError("Bitte ein Zieldatum wählen");
      return;
    }
    if (planMode === "monthly_amount" && (monthlyMinor == null || monthlyMinor <= 0)) {
      setPlanError("Bitte einen positiven Monatsbetrag angeben");
      return;
    }
    setPlanError("");
    startPlanMutation.mutate({
      itemId: planWish.id,
      planning_mode: planMode,
      target_date: planMode === "target_date" ? planTargetDate : null,
      monthly_contribution_minor: planMode === "monthly_amount" ? monthlyMinor : null,
      starting_amount_minor: startingMinor,
      include_in_planning: planInclude,
    });
  };

  return (
    <section className={`goals-page ${embedded ? "goals-page--embedded" : "page"}`}>
      {!embedded && (
        <div className="page-header">
          <div>
            <h1 className="page-heading">Wunschliste</h1>
            <p className="placeholder-note">
              Ideen mit Preis, Priorität und ausführlichen Notizen sammeln. Einen Sparplan startest
              du erst, wenn aus dem Wunsch ein konkretes Ziel wird.
            </p>
          </div>
        </div>
      )}

      <div className="metric-grid" aria-label="Kennzahlen der offenen Wunschliste">
        <div className="card metric-card">
          <span className="metric-label">Offener Wunschwert</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(totalValueMinor)}</span>
          <span className="metric-hint">Nur offene Wünsche</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Rechnerisch leistbar</span>
          <span className="metric-value">{affordableCount}</span>
          <span className="metric-hint">Kaufampel aus dem Monatsbudget, keine Rücklage</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Mit Sparziel</span>
          <span className="metric-value">{linkedGoalCount}</span>
          <span className="metric-hint">Bewusst in einen Sparplan übernommen</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Offene Wünsche</span>
          <span className="metric-value">{openItems.length}</span>
          <span className="metric-hint">Aktuell in Planung</span>
        </div>
      </div>

      <div className="goal-filter-bar">
        <span className="goal-filter-label">Ansicht</span>
        <div
          className="direction-toggle goal-filter-toggle"
          role="group"
          aria-label="Wünsche filtern"
        >
          {(
            [
              ["open", "Offen"],
              ["deferred", "Zurückgestellt"],
              ["purchased", "Gekauft"],
              ["archived", "Archiviert"],
              ["all", "Alle"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`direction-btn ${filter === value ? "direction-btn--active" : ""}`}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label} ({filterCounts[value]})
            </button>
          ))}
        </div>
      </div>

      <section aria-labelledby="wishlist-ledger-title">
        <div className="goal-section-toolbar">
          <div>
            <h2 id="wishlist-ledger-title" className="goal-section-title">
              Wunsch-Ledger
            </h2>
            <p className="goal-section-hint">
              Notizen dürfen mehrzeilig sein. Mit Strg + Enter oder Klick außerhalb speichern.
            </p>
          </div>
          <span className="goal-keyboard-hint">Tab · Enter · Esc</span>
        </div>

        <form id="new-wishlist-item-form" onSubmit={handleCreate} />
        <div className="txn-table-wrapper goal-ledger-wrap">
          <table className="txn-table goal-ledger goal-ledger--wishlist">
            <thead>
              <tr>
                <th className="txn-th">Wunsch</th>
                <th className="txn-th">Priorität</th>
                <th className="txn-th">Status</th>
                <th className="txn-th">Sparziel</th>
                <th className="txn-th">Kaufampel (Plan)</th>
                <th className="txn-th">Preis</th>
                <th className="txn-th">Notiz</th>
                <th className="txn-th">Aktion</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td className="goal-table-message" colSpan={8}>
                    Wunschliste wird geladen …
                  </td>
                </tr>
              )}
              {isError && (
                <tr>
                  <td className="goal-table-message goal-table-message--error" colSpan={8}>
                    Wunschliste konnte nicht geladen werden.
                  </td>
                </tr>
              )}
              {!isLoading && !isError && filteredItems.length === 0 && (
                <tr>
                  <td className="goal-table-message" colSpan={8}>
                    Keine Wünsche in dieser Ansicht — ein neuer Wunsch kann direkt unten eingetragen
                    werden.
                  </td>
                </tr>
              )}
              {filteredItems.map((item) => {
                const itemStatus = wishlistStatus(item);
                const linkedGoalIsAvailable =
                  !item.goal_id || (goals ?? []).some((goal) => goal.id === item.goal_id);
                const rowGoals = (goals ?? []).filter(
                  (goal) => goal.status !== "cancelled" || goal.id === item.goal_id,
                );
                const hasActiveLinkedGoal = Boolean(
                  item.goal_id &&
                  (goals ?? []).some(
                    (goal) => goal.id === item.goal_id && goal.status !== "cancelled",
                  ),
                );
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
                  <tr key={item.id} className="txn-tr goal-edit-row">
                    <td className="txn-td">
                      <input
                        className="goal-cell-input goal-cell-input--strong"
                        aria-label={`${item.name}: Bezeichnung`}
                        defaultValue={item.name}
                        onBlur={(event) => commitItemField(item, "name", event.currentTarget.value)}
                        {...fieldProps(item.name)}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        type="number"
                        min={1}
                        max={5}
                        className="goal-cell-input goal-cell-input--mono"
                        aria-label={`${item.name}: Priorität`}
                        defaultValue={item.priority}
                        onBlur={(event) =>
                          commitItemField(item, "priority", event.currentTarget.value)
                        }
                        {...fieldProps(String(item.priority))}
                      />
                    </td>
                    <td className="txn-td">
                      <select
                        className="goal-cell-input"
                        aria-label={`${item.name}: Status`}
                        defaultValue={itemStatus}
                        onBlur={(event) =>
                          commitItemField(item, "status", event.currentTarget.value)
                        }
                        {...fieldProps(itemStatus)}
                      >
                        {(Object.keys(WISHLIST_STATUS_LABELS) as WishlistStatus[]).map((status) => (
                          <option key={status} value={status}>
                            {WISHLIST_STATUS_LABELS[status]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="txn-td">
                      <select
                        className="goal-cell-input"
                        aria-label={`${item.name}: Sparziel`}
                        defaultValue={item.goal_id ?? ""}
                        onBlur={(event) =>
                          commitItemField(item, "goal_id", event.currentTarget.value)
                        }
                        {...fieldProps(item.goal_id ?? "")}
                      >
                        <option value="">Kein Sparziel</option>
                        {item.goal_id && !linkedGoalIsAvailable && (
                          <option value={item.goal_id}>Sparziel nicht verfügbar</option>
                        )}
                        {rowGoals.map((goal) => (
                          <option key={goal.id} value={goal.id}>
                            {goal.name}
                            {goal.status === "cancelled" ? " (Archiviert)" : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="txn-td">
                      <span
                        className={`status-badge goal-traffic-badge ${TRAFFIC_LIGHT_CLASS[item.buy_traffic_light]}`}
                        title="Rechnerische Orientierung aus dem Monatsbudget und einem verknüpften Sparziel – keine bestätigte Rücklage."
                      >
                        <span className="goal-traffic-dot" aria-hidden="true" />
                        {TRAFFIC_LIGHT_LABEL[item.buy_traffic_light]}
                      </span>
                    </td>
                    <td className="txn-td">
                      <input
                        inputMode="decimal"
                        className="goal-cell-input goal-cell-input--mono"
                        aria-label={`${item.name}: Preis`}
                        defaultValue={amountDraft(item.price_minor)}
                        onBlur={(event) =>
                          commitItemField(item, "price_minor", event.currentTarget.value)
                        }
                        {...fieldProps(amountDraft(item.price_minor))}
                      />
                    </td>
                    <td className="txn-td">
                      <textarea
                        className="goal-cell-input goal-cell-note"
                        aria-label={`${item.name}: Notiz`}
                        defaultValue={item.note ?? ""}
                        placeholder="Gedanken, Modell, Farbe, Links oder warum dir der Wunsch wichtig ist …"
                        rows={3}
                        onBlur={(event) => commitItemField(item, "note", event.currentTarget.value)}
                        {...fieldProps(item.note ?? "")}
                      />
                    </td>
                    <td className="txn-td goal-action-cell">
                      <span
                        className={`goal-save-state ${validationError || rowHasError ? "goal-save-state--error" : ""}`}
                        role={validationError || rowHasError ? "alert" : "status"}
                      >
                        {validationError ??
                          (rowIsSaving
                            ? "Speichert …"
                            : rowHasError
                              ? "Speichern fehlgeschlagen"
                              : rowWasSaved
                                ? "Gespeichert"
                                : "")}
                      </span>
                      {(itemStatus === "open" || itemStatus === "deferred") &&
                        !hasActiveLinkedGoal && (
                          <button
                            type="button"
                            className="btn-primary goal-row-primary"
                            onClick={() => {
                              setPlanWish(item);
                              setPlanMode("target_date");
                              setPlanTargetDate("");
                              setPlanMonthlyEuro("");
                              setPlanStartingEuro("");
                              setPlanError("");
                            }}
                          >
                            Sparplan starten
                          </button>
                        )}
                    </td>
                  </tr>
                );
              })}
              <tr className="goal-add-row">
                <td className="txn-td">
                  <input
                    ref={addNameRef}
                    form="new-wishlist-item-form"
                    className="goal-cell-input goal-cell-input--strong"
                    aria-label="Neuer Wunsch: Bezeichnung"
                    placeholder="z. B. Neues Fahrrad"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </td>
                <td className="txn-td">
                  <input
                    form="new-wishlist-item-form"
                    type="number"
                    min={1}
                    max={5}
                    className="goal-cell-input goal-cell-input--mono"
                    aria-label="Neuer Wunsch: Priorität"
                    value={priority}
                    onChange={(event) => setPriority(Number(event.target.value))}
                  />
                </td>
                <td className="txn-td goal-add-row__hint">Offen</td>
                <td className="txn-td">
                  <select
                    form="new-wishlist-item-form"
                    className="goal-cell-input"
                    aria-label="Neuer Wunsch: Sparziel"
                    value={goalId}
                    onChange={(event) => setGoalId(event.target.value)}
                  >
                    <option value="">Kein Sparziel</option>
                    {newItemGoals.map((goal) => (
                      <option key={goal.id} value={goal.id}>
                        {goal.name}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="txn-td goal-add-row__hint">Wird berechnet</td>
                <td className="txn-td">
                  <input
                    form="new-wishlist-item-form"
                    inputMode="decimal"
                    className="goal-cell-input goal-cell-input--mono"
                    aria-label="Neuer Wunsch: Preis"
                    placeholder="0,00"
                    value={priceEuro}
                    onChange={(event) => setPriceEuro(event.target.value)}
                  />
                </td>
                <td className="txn-td">
                  <textarea
                    form="new-wishlist-item-form"
                    className="goal-cell-input goal-cell-note"
                    aria-label="Neuer Wunsch: Notiz"
                    placeholder="Ausführliche Notiz zum Wunsch …"
                    rows={3}
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </td>
                <td className="txn-td goal-action-cell">
                  <div className="goal-row-actions">
                    <span
                      className={`goal-save-state ${addValidationError || createMutation.isError ? "goal-save-state--error" : ""}`}
                      role={addValidationError || createMutation.isError ? "alert" : "status"}
                    >
                      {createMutation.isPending
                        ? "Wird angelegt …"
                        : addValidationError ||
                          (createMutation.isError ? "Anlegen fehlgeschlagen" : "Neue Zeile")}
                    </span>
                    <button
                      form="new-wishlist-item-form"
                      type="submit"
                      className="btn-primary goal-add-button"
                      disabled={createMutation.isPending}
                    >
                      + Hinzufügen
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      {planWish && (
        <div className="modal-overlay" role="presentation">
          <section
            className="modal-panel goal-plan-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="start-savings-plan-title"
          >
            <div className="modal-header">
              <div>
                <span className="status-badge status-badge--highlight">Kalkulatorisch</span>
                <h2 id="start-savings-plan-title" className="modal-title">
                  Sparplan für {planWish.name}
                </h2>
                <p className="goal-form-hint">
                  Zielbetrag {formatMinorUnitsAsEuro(planWish.price_minor)} · Wunsch bleibt
                  erhalten.
                </p>
              </div>
              <button
                type="button"
                className="modal-close"
                aria-label="Sparplan schließen"
                onClick={() => setPlanWish(null)}
              >
                ×
              </button>
            </div>
            <form className="goal-modal-form" onSubmit={handleStartPlan}>
              <div className="goal-modal-grid">
                <label className="field-label">
                  Planungsart
                  <select
                    className="field-input"
                    value={planMode}
                    onChange={(event) => setPlanMode(event.target.value as SavingsPlanningMode)}
                  >
                    <option value="target_date">Zieltermin vorgeben</option>
                    <option value="monthly_amount">Monatsbetrag vorgeben</option>
                  </select>
                </label>
                {planMode === "target_date" ? (
                  <label className="field-label">
                    Zieldatum
                    <input
                      type="date"
                      className="field-input"
                      value={planTargetDate}
                      onChange={(event) => setPlanTargetDate(event.target.value)}
                    />
                  </label>
                ) : (
                  <label className="field-label">
                    Monatsbetrag
                    <input
                      className="field-input goal-cell-input--mono"
                      inputMode="decimal"
                      placeholder="50,00"
                      value={planMonthlyEuro}
                      onChange={(event) => setPlanMonthlyEuro(event.target.value)}
                    />
                  </label>
                )}
                <label className="field-label">
                  Bereits gespart
                  <input
                    className="field-input goal-cell-input--mono"
                    inputMode="decimal"
                    placeholder="0,00"
                    value={planStartingEuro}
                    onChange={(event) => setPlanStartingEuro(event.target.value)}
                  />
                </label>
              </div>
              <label className="planning-checkbox">
                <input
                  type="checkbox"
                  checked={planInclude}
                  onChange={(event) => setPlanInclude(event.target.checked)}
                />
                Im Monatsplan berücksichtigen
              </label>
              {(planError || startPlanMutation.isError) && (
                <p className="goal-form-error" role="alert">
                  {planError || "Sparplan konnte nicht angelegt werden."}
                </p>
              )}
              <div className="goal-modal-actions">
                <button type="button" className="btn-secondary" onClick={() => setPlanWish(null)}>
                  Abbrechen
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={startPlanMutation.isPending}
                >
                  {startPlanMutation.isPending ? "Wird gestartet …" : "Spartopf anlegen"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}
