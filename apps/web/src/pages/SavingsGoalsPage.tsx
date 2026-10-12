import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { TransactionDrilldown } from "../components/analytics/TransactionDrilldown";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { useFormatDate } from "../hooks/useProfile";
import { createGoal, listGoals, updateGoal } from "../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type {
  AnalyticsQuery,
  GoalStatus,
  SavingsGoal,
  SavingsGoalCreateInput,
  SavingsGoalUpdateInput,
  SavingsPlanningMode,
} from "../lib/types";

const GOAL_STATUS_LABELS: Record<GoalStatus, string> = {
  active: "Aktiv",
  reached: "Erreicht",
  cancelled: "Archiviert",
};

const GOAL_STATUS_CLASSES: Record<GoalStatus, string> = {
  active: "status-badge--highlight",
  reached: "status-badge--positive",
  cancelled: "status-badge--neutral",
};

type GoalFilter = "current" | "active" | "reached" | "cancelled" | "all";
type EditableGoalField = "name" | "starting_amount_minor" | "target_amount_minor";

function amountDraft(value: number): string {
  return (value / 100).toFixed(2).replace(".", ",");
}

function handleFieldKeyDown(event: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>) {
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

function goalTransactionsQuery(goalId: string): AnalyticsQuery {
  return {
    grain: "rows",
    group_by: [],
    measures: [],
    fields: ["date", "account", "payee", "purpose", "category", "amount", "status"],
    period: { kind: "all_time" },
    where: { condition: { field: "goal", op: "eq", value: goalId } },
    order_by: [],
    limit: 200,
    include_transfers: true,
    include_archived_categories: true,
    only_on_budget: false,
  };
}

function planningModeOf(goal: SavingsGoal): SavingsPlanningMode {
  return goal.planning_mode ?? "target_date";
}

export function SavingsGoalsPage({ embedded = false }: { embedded?: boolean }) {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();
  const addNameRef = useRef<HTMLInputElement>(null);
  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const [filter, setFilter] = useState<GoalFilter>("current");
  const [drilldownGoal, setDrilldownGoal] = useState<SavingsGoal | null>(null);
  const [name, setName] = useState("");
  const [targetEuro, setTargetEuro] = useState("");
  const [startingEuro, setStartingEuro] = useState("");
  const [planningMode, setPlanningMode] = useState<SavingsPlanningMode>("target_date");
  const [targetDate, setTargetDate] = useState("");
  const [monthlyEuro, setMonthlyEuro] = useState("");
  const [priority, setPriority] = useState(3);
  const [includeInPlanning, setIncludeInPlanning] = useState(true);
  const [addValidationError, setAddValidationError] = useState("");
  const [rowValidationErrors, setRowValidationErrors] = useState<Record<string, string>>({});

  const {
    data: goals,
    isLoading,
    isError,
  } = useQuery<SavingsGoal[]>({
    queryKey: ["goals", householdId],
    queryFn: () => listGoals(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const allGoals = goals ?? [];
  const currentGoals = allGoals.filter((goal) => goal.status !== "cancelled");
  const filteredGoals = allGoals.filter((goal) => {
    if (filter === "current") return goal.status !== "cancelled";
    if (filter === "all") return true;
    return goal.status === filter;
  });
  const filterCounts: Record<GoalFilter, number> = {
    current: currentGoals.length,
    active: allGoals.filter((goal) => goal.status === "active").length,
    reached: allGoals.filter((goal) => goal.status === "reached").length,
    cancelled: allGoals.filter((goal) => goal.status === "cancelled").length,
    all: allGoals.length,
  };
  const totalTargetMinor = currentGoals.reduce((sum, goal) => sum + goal.target_amount_minor, 0);
  const totalCurrentMinor = currentGoals.reduce((sum, goal) => sum + goal.current_amount_minor, 0);
  const outstandingMinor = currentGoals.reduce(
    (sum, goal) => sum + Math.max(goal.target_amount_minor - goal.current_amount_minor, 0),
    0,
  );
  const reserveMinor = currentGoals.reduce(
    (sum, goal) => sum + (goal.effective_monthly_reserve_minor ?? 0),
    0,
  );

  const clearRowError = (goalId: string) => {
    setRowValidationErrors((current) => {
      if (!(goalId in current)) return current;
      const next = { ...current };
      delete next[goalId];
      return next;
    });
  };

  const createMutation = useMutation({
    mutationFn: (input: SavingsGoalCreateInput) => createGoal(token!, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["goals", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["budget", householdId] });
      setName("");
      setTargetEuro("");
      setStartingEuro("");
      setTargetDate("");
      setMonthlyEuro("");
      setPriority(3);
      setAddValidationError("");
      addNameRef.current?.focus();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ goalId, patch }: { goalId: string; patch: SavingsGoalUpdateInput }) =>
      updateGoal(token!, householdId, goalId, patch),
    onMutate: ({ goalId }) => clearRowError(goalId),
    onSuccess: (_goal, { goalId }) => {
      clearRowError(goalId);
      void queryClient.invalidateQueries({ queryKey: ["goals", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["budget", householdId] });
    },
  });

  const showRowError = (goalId: string, message: string) =>
    setRowValidationErrors((current) => ({ ...current, [goalId]: message }));

  const commitSimpleField = (goal: SavingsGoal, field: EditableGoalField, rawValue: string) => {
    let value: string | number;
    if (field === "name") {
      value = rawValue.trim();
      if (!value) return showRowError(goal.id, "Name fehlt");
    } else {
      value = rawValue.trim() ? (parseAmountToMinorUnits(rawValue) ?? -1) : 0;
      if (field === "target_amount_minor" && value <= 0) {
        return showRowError(goal.id, "Zielbetrag ungültig");
      }
      if (field === "starting_amount_minor" && value < 0) {
        return showRowError(goal.id, "Startbetrag ungültig");
      }
    }
    if (goal[field] !== value)
      updateMutation.mutate({ goalId: goal.id, patch: { [field]: value } });
  };

  const commitPlanningMode = (goal: SavingsGoal, mode: SavingsPlanningMode) => {
    if (mode === planningModeOf(goal)) return;
    const complete =
      mode === "target_date" ? Boolean(goal.target_date) : Boolean(goal.monthly_contribution_minor);
    updateMutation.mutate({
      goalId: goal.id,
      patch: { planning_mode: mode, include_in_planning: complete && goal.include_in_planning },
    });
  };

  const commitTargetDate = (goal: SavingsGoal, value: string) => {
    const target_date = value || null;
    if (target_date === goal.target_date) return;
    updateMutation.mutate({
      goalId: goal.id,
      patch: {
        target_date,
        ...(target_date ? {} : { include_in_planning: false }),
      },
    });
  };

  const commitMonthlyAmount = (goal: SavingsGoal, rawValue: string) => {
    const amount = parseAmountToMinorUnits(rawValue);
    if (amount == null || amount <= 0) return showRowError(goal.id, "Monatsbetrag ungültig");
    if (amount !== goal.monthly_contribution_minor) {
      updateMutation.mutate({
        goalId: goal.id,
        patch: { monthly_contribution_minor: amount },
      });
    }
  };

  const togglePlanning = (goal: SavingsGoal, checked: boolean) => {
    const complete =
      planningModeOf(goal) === "target_date"
        ? Boolean(goal.target_date)
        : Boolean(goal.monthly_contribution_minor && goal.monthly_contribution_minor > 0);
    if (checked && !complete) {
      showRowError(
        goal.id,
        planningModeOf(goal) === "target_date"
          ? "Bitte zuerst ein Zieldatum wählen"
          : "Bitte zuerst einen Monatsbetrag angeben",
      );
      return;
    }
    updateMutation.mutate({ goalId: goal.id, patch: { include_in_planning: checked } });
  };

  const handleCreate = (event: React.FormEvent) => {
    event.preventDefault();
    const normalizedName = name.trim();
    const targetMinor = parseAmountToMinorUnits(targetEuro);
    const startingMinor = startingEuro.trim() ? parseAmountToMinorUnits(startingEuro) : 0;
    const monthlyMinor = monthlyEuro.trim() ? parseAmountToMinorUnits(monthlyEuro) : null;
    if (!normalizedName) {
      setAddValidationError("Bezeichnung fehlt");
      addNameRef.current?.focus();
      return;
    }
    if (targetMinor == null || targetMinor <= 0)
      return setAddValidationError("Zielbetrag ungültig");
    if (startingMinor == null || startingMinor < 0)
      return setAddValidationError("Startbetrag ungültig");
    if (planningMode === "target_date" && !targetDate && includeInPlanning) {
      return setAddValidationError("Für die Monatsplanung fehlt das Zieldatum");
    }
    if (planningMode === "monthly_amount" && (monthlyMinor == null || monthlyMinor <= 0)) {
      return setAddValidationError("Bitte einen positiven Monatsbetrag angeben");
    }
    setAddValidationError("");
    createMutation.mutate({
      name: normalizedName,
      target_amount_minor: targetMinor,
      starting_amount_minor: startingMinor,
      planning_mode: planningMode,
      target_date: planningMode === "target_date" ? targetDate || null : null,
      monthly_contribution_minor: planningMode === "monthly_amount" ? monthlyMinor : null,
      include_in_planning: includeInPlanning,
      priority,
    });
  };

  return (
    <section className={`goals-page ${embedded ? "goals-page--embedded" : "page"}`}>
      {!embedded && (
        <div className="page-header">
          <div>
            <h1 className="page-heading">Spartöpfe</h1>
            <p className="placeholder-note">Ziele, Termine und kalkulatorische Rücklagen planen.</p>
          </div>
        </div>
      )}

      <div className="metric-grid" aria-label="Kennzahlen der aktuellen Spartöpfe">
        <div className="card metric-card">
          <span className="metric-label">Zielsumme</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(totalTargetMinor)}</span>
          <span className="metric-hint">Aktuelle Spartöpfe</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Bereits gespart</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(totalCurrentMinor)}</span>
          <span className="metric-hint">Startbeträge plus zugeordnete Buchungen</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Noch offen</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(outstandingMinor)}</span>
          <span className="metric-hint">Bis zur Zielsumme</span>
        </div>
        <div className="card metric-card planning-metric-card">
          <span className="metric-label">Monatlich gebunden</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(reserveMinor)}</span>
          <span className="metric-hint">Kalkulatorisch · keine Buchung</span>
        </div>
      </div>

      <form className="card savings-create-panel" onSubmit={handleCreate}>
        <div className="goal-section-toolbar">
          <div>
            <h2 className="goal-section-title">Neuen Spartopf anlegen</h2>
            <p className="goal-section-hint">Zieltermin oder festen Monatsbetrag wählen.</p>
          </div>
          <span className="status-badge status-badge--highlight">Kalkulatorisch</span>
        </div>
        <div className="savings-create-grid">
          <label className="field-label">
            Bezeichnung
            <input
              ref={addNameRef}
              className="field-input"
              aria-label="Neues Sparziel: Bezeichnung"
              placeholder="z. B. Familienbesuch"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="field-label">
            Zielbetrag
            <input
              className="field-input goal-cell-input--mono"
              inputMode="decimal"
              aria-label="Neues Sparziel: Zielbetrag"
              placeholder="1.500,00"
              value={targetEuro}
              onChange={(event) => setTargetEuro(event.target.value)}
            />
          </label>
          <label className="field-label">
            Bereits gespart
            <input
              className="field-input goal-cell-input--mono"
              inputMode="decimal"
              aria-label="Neues Sparziel: Startbetrag"
              placeholder="0,00"
              value={startingEuro}
              onChange={(event) => setStartingEuro(event.target.value)}
            />
          </label>
          <label className="field-label">
            Planung
            <select
              className="field-input"
              aria-label="Neues Sparziel: Planungsart"
              value={planningMode}
              onChange={(event) => setPlanningMode(event.target.value as SavingsPlanningMode)}
            >
              <option value="target_date">Zieltermin vorgeben</option>
              <option value="monthly_amount">Monatsbetrag vorgeben</option>
            </select>
          </label>
          {planningMode === "target_date" ? (
            <label className="field-label">
              Zieldatum
              <input
                type="date"
                className="field-input goal-cell-input--mono"
                aria-label="Neues Sparziel: Zieldatum"
                value={targetDate}
                onChange={(event) => setTargetDate(event.target.value)}
              />
            </label>
          ) : (
            <label className="field-label">
              Monatsbetrag
              <input
                className="field-input goal-cell-input--mono"
                inputMode="decimal"
                aria-label="Neues Sparziel: Monatsbetrag"
                placeholder="50,00"
                value={monthlyEuro}
                onChange={(event) => setMonthlyEuro(event.target.value)}
              />
            </label>
          )}
          <label className="field-label">
            Priorität
            <input
              type="number"
              min={1}
              max={5}
              className="field-input goal-cell-input--mono"
              aria-label="Neues Sparziel: Priorität"
              value={priority}
              onChange={(event) => setPriority(Number(event.target.value))}
            />
          </label>
        </div>
        <div className="savings-create-footer">
          <label className="planning-checkbox">
            <input
              type="checkbox"
              checked={includeInPlanning}
              onChange={(event) => setIncludeInPlanning(event.target.checked)}
            />
            Im Monatsplan berücksichtigen
          </label>
          <span
            className="goal-save-state goal-save-state--error"
            role={addValidationError ? "alert" : "status"}
          >
            {addValidationError || (createMutation.isError ? "Anlegen fehlgeschlagen" : "")}
          </span>
          <button
            type="submit"
            className="btn-primary goal-add-button"
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? "Wird angelegt …" : "+ Hinzufügen"}
          </button>
        </div>
      </form>

      <div className="goal-filter-bar">
        <span className="goal-filter-label">Ansicht</span>
        <div
          className="direction-toggle goal-filter-toggle"
          role="group"
          aria-label="Spartöpfe filtern"
        >
          {(
            [
              ["current", "Aktuell"],
              ["active", "Aktiv"],
              ["reached", "Erreicht"],
              ["cancelled", "Archiviert"],
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
        <Link to="/sparziele/zuordnen" className="btn-secondary goal-assignment-link">
          Buchungen zuordnen
        </Link>
      </div>

      {isLoading && <div className="card goal-table-message">Spartöpfe werden geladen …</div>}
      {isError && (
        <div className="card goal-table-message goal-table-message--error">
          Spartöpfe konnten nicht geladen werden.
        </div>
      )}
      {!isLoading && !isError && filteredGoals.length === 0 && (
        <div className="card goal-table-message">Keine Spartöpfe in dieser Ansicht.</div>
      )}

      <div className="savings-goal-list" aria-label="Spartöpfe">
        {filteredGoals.map((goal) => {
          const progress = Math.max(
            0,
            Math.min(Math.round((goal.current_amount_minor / goal.target_amount_minor) * 100), 100),
          );
          const mode = planningModeOf(goal);
          const isArchived = goal.status === "cancelled";
          const isReached = goal.status === "reached";
          const rowIsSaving =
            updateMutation.isPending && updateMutation.variables?.goalId === goal.id;
          const rowHasError =
            updateMutation.isError && updateMutation.variables?.goalId === goal.id;
          const rowWasSaved =
            updateMutation.isSuccess && updateMutation.variables?.goalId === goal.id;
          const planningIncluded = goal.include_in_planning ?? Boolean(goal.target_date);
          const effectiveReserve =
            goal.effective_monthly_reserve_minor ?? goal.monthly_required_minor;
          const projectedDate = goal.projected_completion_date ?? goal.target_date;
          const priorityValue = goal.priority ?? 3;
          const fieldProps = (original: string) => ({
            "data-original": original,
            onKeyDown: handleFieldKeyDown,
          });
          return (
            <article
              key={goal.id}
              className={`card savings-goal-card ${isArchived ? "savings-goal-card--archived" : ""}`}
            >
              <header className="savings-goal-card__header">
                {isArchived ? (
                  <h3>{goal.name}</h3>
                ) : (
                  <input
                    className="goal-cell-input goal-cell-input--strong savings-goal-card__name"
                    aria-label={`${goal.name}: Bezeichnung`}
                    defaultValue={goal.name}
                    onBlur={(event) => commitSimpleField(goal, "name", event.currentTarget.value)}
                    {...fieldProps(goal.name)}
                  />
                )}
                <div className="savings-goal-card__badges">
                  {planningIncluded && !isArchived && !isReached && (
                    <span className="status-badge status-badge--highlight">Kalkulatorisch</span>
                  )}
                  <span className={`status-badge ${GOAL_STATUS_CLASSES[goal.status]}`}>
                    {GOAL_STATUS_LABELS[goal.status]}
                  </span>
                </div>
              </header>

              <div className="savings-goal-card__money">
                <label className="field-label">
                  Startbetrag
                  {isArchived ? (
                    <span className="goal-money">
                      {formatMinorUnitsAsEuro(goal.starting_amount_minor)}
                    </span>
                  ) : (
                    <input
                      className="goal-cell-input goal-cell-input--mono"
                      inputMode="decimal"
                      aria-label={`${goal.name}: Startbetrag`}
                      defaultValue={amountDraft(goal.starting_amount_minor)}
                      onBlur={(e) =>
                        commitSimpleField(goal, "starting_amount_minor", e.currentTarget.value)
                      }
                      {...fieldProps(amountDraft(goal.starting_amount_minor))}
                    />
                  )}
                </label>
                <div className="field-label">
                  Aktueller Stand
                  <strong className="goal-money">
                    {formatMinorUnitsAsEuro(goal.current_amount_minor)}
                  </strong>
                  <span className="goal-cell-detail">
                    davon Buchungen{" "}
                    {formatMinorUnitsAsEuro(goal.current_amount_minor - goal.starting_amount_minor)}
                  </span>
                </div>
                <label className="field-label">
                  Zielbetrag
                  {isArchived ? (
                    <span className="goal-money">
                      {formatMinorUnitsAsEuro(goal.target_amount_minor)}
                    </span>
                  ) : (
                    <input
                      className="goal-cell-input goal-cell-input--mono"
                      inputMode="decimal"
                      aria-label={`${goal.name}: Zielbetrag`}
                      defaultValue={amountDraft(goal.target_amount_minor)}
                      onBlur={(e) =>
                        commitSimpleField(goal, "target_amount_minor", e.currentTarget.value)
                      }
                      {...fieldProps(amountDraft(goal.target_amount_minor))}
                    />
                  )}
                </label>
              </div>

              <div className="savings-goal-card__progress">
                <div
                  className="goal-progress"
                  role="progressbar"
                  aria-label={`${goal.name}: ${progress} Prozent erreicht`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={progress}
                >
                  <span className="goal-progress__bar" style={{ width: `${progress}%` }} />
                </div>
                <strong>{progress} %</strong>
                <span>
                  {formatMinorUnitsAsEuro(
                    Math.max(goal.target_amount_minor - goal.current_amount_minor, 0),
                  )}{" "}
                  fehlen
                </span>
              </div>

              <div className="savings-goal-card__planning">
                <label className="field-label">
                  Planungsart
                  <select
                    className="goal-cell-input"
                    aria-label={`${goal.name}: Planungsart`}
                    value={mode}
                    disabled={isArchived || isReached}
                    onChange={(event) =>
                      commitPlanningMode(goal, event.target.value as SavingsPlanningMode)
                    }
                  >
                    <option value="target_date">Zieltermin vorgeben</option>
                    <option value="monthly_amount">Monatsbetrag vorgeben</option>
                  </select>
                </label>
                {mode === "target_date" ? (
                  <label className="field-label">
                    Zieldatum
                    {isArchived ? (
                      <span className="goal-date">
                        {goal.target_date ? formatDate(goal.target_date) : "Kein Datum"}
                      </span>
                    ) : (
                      <input
                        type="date"
                        className="goal-cell-input goal-cell-input--mono"
                        aria-label={`${goal.name}: Zieldatum`}
                        defaultValue={goal.target_date ?? ""}
                        onBlur={(event) => commitTargetDate(goal, event.currentTarget.value)}
                        {...fieldProps(goal.target_date ?? "")}
                      />
                    )}
                  </label>
                ) : (
                  <label className="field-label">
                    Monatsbetrag
                    {isArchived ? (
                      <span className="goal-money">
                        {formatMinorUnitsAsEuro(goal.monthly_contribution_minor ?? 0)}
                      </span>
                    ) : (
                      <input
                        className="goal-cell-input goal-cell-input--mono"
                        inputMode="decimal"
                        aria-label={`${goal.name}: Monatsbetrag`}
                        defaultValue={amountDraft(goal.monthly_contribution_minor ?? 0)}
                        onBlur={(event) => commitMonthlyAmount(goal, event.currentTarget.value)}
                        {...fieldProps(amountDraft(goal.monthly_contribution_minor ?? 0))}
                      />
                    )}
                  </label>
                )}
                <label className="field-label">
                  Priorität
                  <input
                    type="number"
                    min={1}
                    max={5}
                    className="goal-cell-input goal-cell-input--mono"
                    aria-label={`${goal.name}: Priorität`}
                    defaultValue={priorityValue}
                    disabled={isArchived}
                    onBlur={(event) => {
                      const value = Number(event.currentTarget.value);
                      if (!Number.isInteger(value) || value < 1 || value > 5)
                        return showRowError(goal.id, "Priorität 1–5");
                      if (value !== priorityValue)
                        updateMutation.mutate({ goalId: goal.id, patch: { priority: value } });
                    }}
                    {...fieldProps(String(priorityValue))}
                  />
                </label>
                <label className="planning-checkbox planning-checkbox--card">
                  <input
                    type="checkbox"
                    aria-label={`${goal.name}: Im Monatsplan berücksichtigen`}
                    checked={planningIncluded && !isReached && !isArchived}
                    disabled={isReached || isArchived}
                    onChange={(event) => togglePlanning(goal, event.target.checked)}
                  />
                  Im Monatsplan berücksichtigen
                </label>
              </div>

              <div className="savings-goal-card__result">
                <div>
                  <span>{isReached ? "Status" : "Monatliche Rücklage"}</span>
                  <strong>
                    {isReached ? "Ziel erreicht" : formatMinorUnitsAsEuro(effectiveReserve)}
                  </strong>
                </div>
                <div>
                  <span>Erwartetes Ziel</span>
                  <strong>
                    {projectedDate ? formatDate(projectedDate) : "Plan unvollständig"}
                  </strong>
                </div>
                <p>Kalkulatorischer Wert. Es wird keine Ausgabe oder Überweisung erzeugt.</p>
              </div>

              <footer className="savings-goal-card__footer">
                <span
                  className={`goal-save-state ${rowValidationErrors[goal.id] || rowHasError ? "goal-save-state--error" : ""}`}
                  role={rowValidationErrors[goal.id] || rowHasError ? "alert" : "status"}
                >
                  {rowValidationErrors[goal.id] ??
                    (rowIsSaving
                      ? "Speichert …"
                      : rowHasError
                        ? "Speichern fehlgeschlagen"
                        : rowWasSaved
                          ? "Gespeichert"
                          : "")}
                </span>
                <button
                  type="button"
                  className="goal-row-button"
                  aria-label={`Buchungen für Sparziel ${goal.name} anzeigen`}
                  onClick={() => setDrilldownGoal(goal)}
                >
                  Buchungen
                </button>
                <button
                  type="button"
                  className={isArchived ? "btn-primary goal-row-primary" : "goal-archive-button"}
                  aria-label={`Sparziel ${goal.name} ${isArchived ? "wiederherstellen" : "archivieren"}`}
                  disabled={rowIsSaving}
                  onClick={() =>
                    updateMutation.mutate({
                      goalId: goal.id,
                      patch: { status: isArchived ? "active" : "cancelled" },
                    })
                  }
                >
                  {isArchived ? "Wiederherstellen" : "Archivieren"}
                </button>
              </footer>
            </article>
          );
        })}
      </div>

      {drilldownGoal && (
        <TransactionDrilldown
          title={`Buchungen · ${drilldownGoal.name}`}
          query={goalTransactionsQuery(drilldownGoal.id)}
          onClose={() => setDrilldownGoal(null)}
        />
      )}
    </section>
  );
}
