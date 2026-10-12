import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { SavingsPlanningEquation } from "../components/SavingsPlanningEquation";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { useFormatDate } from "../hooks/useProfile";
import { getBudgetSummary, listGoals, previewSavingsPlan } from "../lib/api";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type {
  BudgetSummary,
  SavingsGoal,
  SavingsPlanPreview,
  SavingsPlanningMode,
} from "../lib/types";
import { SavingsGoalsPage } from "./SavingsGoalsPage";
import { WishlistPage } from "./WishlistPage";

type SavingsTab = "overview" | "goals" | "wishes" | "analysis";

const TABS: Array<{ id: SavingsTab; label: string }> = [
  { id: "overview", label: "Übersicht" },
  { id: "goals", label: "Spartöpfe" },
  { id: "wishes", label: "Wünsche" },
  { id: "analysis", label: "Analyse" },
];

function normalizedTab(value: string | null): SavingsTab {
  return TABS.some((tab) => tab.id === value) ? (value as SavingsTab) : "overview";
}

function GoalCockpitRow({ goal }: { goal: SavingsGoal }) {
  const { formatDate } = useFormatDate();
  const progress = Math.max(
    0,
    Math.min(Math.round((goal.current_amount_minor / goal.target_amount_minor) * 100), 100),
  );
  const projected = goal.projected_completion_date ?? goal.target_date;
  return (
    <article className="savings-cockpit-row">
      <div className="savings-cockpit-row__identity">
        <div>
          <strong>{goal.name}</strong>
          <span>
            Priorität {goal.priority ?? 3} ·{" "}
            {goal.planning_mode === "monthly_amount" ? "Fester Monatsbetrag" : "Zieltermin"}
          </span>
        </div>
        <span
          className={`status-badge ${goal.include_in_planning ? "status-badge--highlight" : "status-badge--neutral"}`}
        >
          {goal.include_in_planning ? "Im Monatsplan" : "Nicht eingeplant"}
        </span>
      </div>
      <div className="savings-cockpit-row__progress">
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
        <span>{progress} %</span>
      </div>
      <div className="savings-cockpit-row__numbers">
        <span>
          <small>Stand</small>
          <strong>{formatMinorUnitsAsEuro(goal.current_amount_minor)}</strong>
        </span>
        <span>
          <small>Monatlich</small>
          <strong>{formatMinorUnitsAsEuro(goal.effective_monthly_reserve_minor ?? 0)}</strong>
        </span>
        <span>
          <small>Erwartetes Ziel</small>
          <strong>{projected ? formatDate(projected) : "Plan fehlt"}</strong>
        </span>
      </div>
    </article>
  );
}

function OverviewTab({
  goals,
  budget,
  isLoading,
  isError,
}: {
  goals: SavingsGoal[];
  budget?: BudgetSummary;
  isLoading: boolean;
  isError: boolean;
}) {
  const active = goals.filter((goal) => goal.status === "active");
  const totalTarget = active.reduce((sum, goal) => sum + goal.target_amount_minor, 0);
  const saved = active.reduce((sum, goal) => sum + goal.current_amount_minor, 0);
  const missing = active.reduce(
    (sum, goal) => sum + Math.max(goal.target_amount_minor - goal.current_amount_minor, 0),
    0,
  );
  const reached = goals.filter((goal) => goal.status === "reached").length;

  if (isLoading) return <div className="card goal-table-message">Sparplanung wird geladen …</div>;
  if (isError)
    return (
      <div className="card goal-table-message goal-table-message--error">
        Sparplanung konnte nicht geladen werden.
      </div>
    );
  return (
    <div className="savings-tab-stack">
      <div className="metric-grid" aria-label="Sparplanung im Überblick">
        <div className="card metric-card">
          <span className="metric-label">Aktive Zielsumme</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(totalTarget)}</span>
          <span className="metric-hint">
            {active.length} aktiv · {reached} erreicht
          </span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Bereits gespart</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(saved)}</span>
          <span className="metric-hint">Split-basiert, ohne Archive</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Noch offen</span>
          <span className="metric-value">{formatMinorUnitsAsEuro(missing)}</span>
          <span className="metric-hint">Bis zu allen aktiven Zielen</span>
        </div>
        <div className="card metric-card planning-metric-card">
          <span className="metric-label">Kalkulatorisch gebunden</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(budget?.total_calculational_reserves_minor ?? 0)}
          </span>
          <span className="metric-hint">Fixkosten-Rücklagen plus Spartöpfe</span>
        </div>
      </div>
      {budget && <SavingsPlanningEquation budget={budget} />}
      <section aria-labelledby="active-saving-pots-title">
        <div className="goal-section-toolbar">
          <div>
            <h2 id="active-saving-pots-title" className="goal-section-title">
              Aktive Spartöpfe
            </h2>
            <p className="goal-section-hint">
              Stand, nächste Rücklage und erwarteter Zielzeitpunkt.
            </p>
          </div>
        </div>
        {active.length === 0 ? (
          <div className="card savings-empty-state">
            <strong>Noch kein aktiver Spartopf</strong>
            <span>Lege im Tab „Spartöpfe“ ein Ziel mit Termin oder Monatsbetrag an.</span>
          </div>
        ) : (
          <div className="savings-cockpit-list">
            {active
              .sort((a, b) => (a.priority ?? 3) - (b.priority ?? 3))
              .map((goal) => (
                <GoalCockpitRow key={goal.id} goal={goal} />
              ))}
          </div>
        )}
      </section>
    </div>
  );
}

function AnalysisTab({ goals, budget }: { goals: SavingsGoal[]; budget?: BudgetSummary }) {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const { formatDate } = useFormatDate();
  const [targetEuro, setTargetEuro] = useState("");
  const [currentEuro, setCurrentEuro] = useState("");
  const [mode, setMode] = useState<SavingsPlanningMode>("target_date");
  const [targetDate, setTargetDate] = useState("");
  const [monthlyEuro, setMonthlyEuro] = useState("");
  const [validationError, setValidationError] = useState("");
  const previewMutation = useMutation<
    SavingsPlanPreview,
    Error,
    { target: number; current: number; monthly: number | null }
  >({
    mutationFn: ({ target, current, monthly }) =>
      previewSavingsPlan(session!.access_token, selectedHouseholdId!, {
        target_amount_minor: target,
        current_amount_minor: current,
        planning_mode: mode,
        target_date: mode === "target_date" ? targetDate : null,
        monthly_contribution_minor: mode === "monthly_amount" ? monthly : null,
      }),
  });
  const contributing = goals.filter((goal) => (goal.effective_monthly_reserve_minor ?? 0) > 0);
  const gap = budget ? Math.max(-budget.planning_truly_free_minor, 0) : 0;
  const submitPreview = (event: React.FormEvent) => {
    event.preventDefault();
    const target = parseAmountToMinorUnits(targetEuro);
    const current = currentEuro.trim() ? parseAmountToMinorUnits(currentEuro) : 0;
    const monthly = monthlyEuro.trim() ? parseAmountToMinorUnits(monthlyEuro) : null;
    if (target == null || target <= 0 || current == null || current < 0)
      return setValidationError("Bitte gültige Beträge angeben.");
    if (mode === "target_date" && !targetDate)
      return setValidationError("Bitte ein Zieldatum wählen.");
    if (mode === "monthly_amount" && (monthly == null || monthly <= 0))
      return setValidationError("Bitte einen positiven Monatsbetrag angeben.");
    setValidationError("");
    previewMutation.mutate({ target, current, monthly });
  };

  return (
    <div className="savings-analysis-grid">
      <section
        className={`card savings-capacity-card ${gap > 0 ? "savings-capacity-card--gap" : ""}`}
      >
        <span className="status-badge status-badge--highlight">Kalkulatorisch</span>
        <h2>
          {gap > 0
            ? "Die Rücklagen passen noch nicht ins Monatsbudget"
            : "Die Rücklagen passen ins Monatsbudget"}
        </h2>
        <p>
          {gap > 0
            ? `Es fehlen monatlich ${formatMinorUnitsAsEuro(gap)}.`
            : `Nach allen Planungsschichten bleiben ${formatMinorUnitsAsEuro(budget?.planning_truly_free_minor ?? 0)} wirklich frei.`}
        </p>
        {budget && <SavingsPlanningEquation budget={budget} />}
        <div className="savings-contribution-list">
          <h3>Beitrag je Spartopf</h3>
          {contributing.length === 0 ? (
            <p className="placeholder-note">Noch kein Spartopf ist im Monatsplan aktiv.</p>
          ) : (
            contributing.map((goal) => (
              <div key={goal.id}>
                <span>{goal.name}</span>
                <strong>{formatMinorUnitsAsEuro(goal.effective_monthly_reserve_minor)}</strong>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="card savings-whatif-card">
        <div>
          <span className="status-badge status-badge--neutral">Was wäre, wenn?</span>
          <h2>Sparzeit berechnen</h2>
          <p className="placeholder-note">Ohne einen Spartopf anzulegen.</p>
        </div>
        <form onSubmit={submitPreview} className="savings-whatif-form">
          <label className="field-label">
            Zielbetrag
            <input
              className="field-input"
              inputMode="decimal"
              placeholder="1.500,00"
              value={targetEuro}
              onChange={(event) => setTargetEuro(event.target.value)}
            />
          </label>
          <label className="field-label">
            Bereits gespart
            <input
              className="field-input"
              inputMode="decimal"
              placeholder="0,00"
              value={currentEuro}
              onChange={(event) => setCurrentEuro(event.target.value)}
            />
          </label>
          <label className="field-label">
            Berechnung
            <select
              className="field-input"
              value={mode}
              onChange={(event) => setMode(event.target.value as SavingsPlanningMode)}
            >
              <option value="target_date">Rate bis Zieltermin</option>
              <option value="monthly_amount">Zieltermin aus Monatsbetrag</option>
            </select>
          </label>
          {mode === "target_date" ? (
            <label className="field-label">
              Gewünschtes Datum
              <input
                type="date"
                className="field-input"
                value={targetDate}
                onChange={(event) => setTargetDate(event.target.value)}
              />
            </label>
          ) : (
            <label className="field-label">
              Monatsbetrag
              <input
                className="field-input"
                inputMode="decimal"
                placeholder="50,00"
                value={monthlyEuro}
                onChange={(event) => setMonthlyEuro(event.target.value)}
              />
            </label>
          )}
          {(validationError || previewMutation.isError) && (
            <p className="goal-form-error" role="alert">
              {validationError || "Berechnung fehlgeschlagen."}
            </p>
          )}
          <button className="btn-primary" type="submit" disabled={previewMutation.isPending}>
            {previewMutation.isPending ? "Berechnet …" : "Sparplan berechnen"}
          </button>
        </form>
        {previewMutation.data && (
          <div className="savings-whatif-result" aria-live="polite">
            <span>Ergebnis</span>
            {mode === "target_date" ? (
              <strong>
                {formatMinorUnitsAsEuro(previewMutation.data.monthly_required_minor)} pro Monat
              </strong>
            ) : (
              <strong>
                Ziel voraussichtlich{" "}
                {previewMutation.data.projected_completion_date
                  ? formatDate(previewMutation.data.projected_completion_date)
                  : "nicht erreichbar"}
              </strong>
            )}
            <small>
              Annahme: eine Einzahlung je Monat; Centbeträge werden zugunsten des Zieltermins
              aufgerundet.
            </small>
          </div>
        )}
      </section>
    </div>
  );
}

export function SavingsPlanningPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = normalizedTab(searchParams.get("tab"));
  const householdId = selectedHouseholdId ?? "";
  const today = new Date();
  const goalsQuery = useQuery<SavingsGoal[]>({
    queryKey: ["goals", householdId],
    queryFn: () => listGoals(session!.access_token, householdId),
    enabled: Boolean(session && householdId),
  });
  const budgetQuery = useQuery<BudgetSummary>({
    queryKey: ["budget", householdId, today.getFullYear(), today.getMonth() + 1],
    queryFn: () =>
      getBudgetSummary(
        session!.access_token,
        householdId,
        today.getFullYear(),
        today.getMonth() + 1,
      ),
    enabled: Boolean(session && householdId),
  });

  return (
    <section className="page savings-planning-page" data-testid="page-savings-planning">
      <div className="page-header savings-planning-header">
        <div>
          <h1 className="page-heading">Sparen & Wünsche</h1>
          <p className="placeholder-note">
            Wünsche sammeln, Spartöpfe planen und sehen, was monatlich wirklich frei bleibt.
          </p>
        </div>
        <span className="savings-planning-header__eyebrow">
          Haushaltsplanung · ohne Doppelzählung
        </span>
      </div>
      <nav className="savings-tabs" aria-label="Sparen und Wünsche">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`savings-tab ${activeTab === tab.id ? "savings-tab--active" : ""}`}
            aria-current={activeTab === tab.id ? "page" : undefined}
            onClick={() => setSearchParams({ tab: tab.id })}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <div className="savings-tab-content">
        {activeTab === "overview" && (
          <OverviewTab
            goals={goalsQuery.data ?? []}
            budget={budgetQuery.data}
            isLoading={goalsQuery.isLoading || budgetQuery.isLoading}
            isError={goalsQuery.isError || budgetQuery.isError}
          />
        )}
        {activeTab === "goals" && <SavingsGoalsPage embedded />}
        {activeTab === "wishes" && <WishlistPage embedded />}
        {activeTab === "analysis" && (
          <AnalysisTab goals={goalsQuery.data ?? []} budget={budgetQuery.data} />
        )}
      </div>
    </section>
  );
}
