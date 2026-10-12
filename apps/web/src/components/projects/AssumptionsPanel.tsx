import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { listGoals } from "../../lib/api";
import {
  useCashflowAssumptions,
  useCreateRecurringImpact,
  useDeleteGoalAssumption,
  useDeleteRecurringImpact,
  useFinancialSummary,
  useFinancingSources,
  useGoalAssumptions,
  useRecurringImpacts,
  useSaveCashflowAssumptions,
  useSaveGoalAssumption,
  useSuggestCashflowAssumptions,
} from "../../hooks/useProjects";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type { CashflowAssumptions, RecurringImpactType } from "../../lib/types";
import { Modal } from "../Modal";
import { CashflowTimelinePanel } from "./CashflowTimelinePanel";
import { useDebtSchedule } from "../../hooks/useDebtPlans";
import type { FinancingSource } from "../../lib/types";

const IMPACT_TYPE_LABELS: Record<RecurringImpactType, string> = {
  income: "Zusätzliches Einkommen",
  expense_increase: "Zusätzliche Ausgabe",
  expense_decrease: "Eingesparte Ausgabe",
};

interface AssumptionsPanelProps {
  projectId: string;
  scenarioId: string;
}

export function AssumptionsPanel({ projectId, scenarioId }: AssumptionsPanelProps) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const { data: summary } = useFinancialSummary(projectId, scenarioId);
  const assumptionsQuery = useCashflowAssumptions(projectId, scenarioId);
  const { data: recurringImpacts } = useRecurringImpacts(projectId, scenarioId);
  const { data: goalAssumptions } = useGoalAssumptions(projectId, scenarioId);
  const { data: financingSources } = useFinancingSources(projectId, scenarioId);
  const [, setSearchParams] = useSearchParams();
  const goalsQuery = useQuery({
    queryKey: ["goals", selectedHouseholdId],
    queryFn: () => listGoals(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });

  const [isImpactOpen, setIsImpactOpen] = useState(false);
  const [isGoalAssumptionOpen, setIsGoalAssumptionOpen] = useState(false);
  const deleteImpact = useDeleteRecurringImpact();
  const deleteGoalAssumption = useDeleteGoalAssumption();
  const goalNames = new Map((goalsQuery.data ?? []).map((g) => [g.id, g.name]));

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      {assumptionsQuery.isLoading ? (
        <p className="placeholder-note">Lade Haushaltsannahmen...</p>
      ) : (
        <CashflowAssumptionsForm
          key={assumptionsQuery.data?.updated_at ?? "neu"}
          projectId={projectId}
          scenarioId={scenarioId}
          saved={assumptionsQuery.data ?? null}
        />
      )}

      <section className="assumption-section assumption-section--financing">
        <div className="assumption-section__heading">
          <div>
            <span className="eyebrow">Aus Finanzierung übernommen</span>
            <h2>Neue Kredite</h2>
          </div>
          <p>
            Kreditkonditionen werden hier nur gelesen. Änderungen an Zins, Tilgung oder Abrufen
            erfolgen einmalig in der Finanzierungsakte.
          </p>
        </div>
        <div className="assumption-loan-list">
          {(financingSources ?? [])
            .filter((source) => source.debt_plan)
            .map((source) => (
              <TransferredLoan
                key={source.id}
                source={source}
                onOpen={() =>
                  setSearchParams({
                    tab: "finanzierung",
                    scenario_id: scenarioId,
                    source: source.id,
                  })
                }
              />
            ))}
          {(financingSources ?? []).filter((source) => source.debt_plan).length === 0 && (
            <p className="placeholder-note">
              Noch keine Kreditkonfiguration vorhanden. Lege sie im Tab Finanzierung an.
            </p>
          )}
        </div>
        <p className="assumption-separation-note">
          <strong>Haushaltsannahmen</strong> beschreiben Einkommen und Lebenshaltung. Die
          <strong> Finanzierungsannahmen</strong> wie Anschlusszins oder Abrufzeitpunkt stammen
          direkt aus dem jeweiligen Kredit.
        </p>
      </section>

      {summary?.affordability && (
        <div className="metric-grid" style={{ marginTop: "var(--space-4)" }}>
          <div className="card metric-card">
            <span className="metric-label">Niedrigste Reserve</span>
            <span className="metric-value">
              {formatMinorUnitsAsEuro(summary.affordability.lowest_reserve_minor)}
            </span>
            <span className="metric-hint">Monat {summary.affordability.lowest_reserve_month}</span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Monate mit negativer Reserve</span>
            <span className="metric-value">{summary.affordability.negative_months_count}</span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Monatsüberschuss (mit Vorhaben)</span>
            <span className="metric-value">
              {formatMinorUnitsAsEuro(summary.affordability.monthly_surplus_after_minor)}
            </span>
            <span className="metric-hint">
              Vorher: {formatMinorUnitsAsEuro(summary.affordability.monthly_surplus_before_minor)}
            </span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Höchste Schuldendienstrate</span>
            <span className="metric-value">
              {formatMinorUnitsAsEuro(summary.affordability.peak_debt_service_minor)}
            </span>
          </div>
        </div>
      )}

      <CashflowTimelinePanel projectId={projectId} scenarioId={scenarioId} />

      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 className="card-heading">Folgekosten und -einnahmen</h2>
          <button type="button" className="btn-secondary" onClick={() => setIsImpactOpen(true)}>
            + Folgekosten
          </button>
        </div>
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Name</th>
                <th className="txn-th txn-th--calc-mode">Art</th>
                <th className="txn-th txn-th--amount-wide">Betrag</th>
                <th className="txn-th txn-th--actions-wide"></th>
              </tr>
            </thead>
            <tbody>
              {(recurringImpacts ?? []).map((impact) => (
                <tr className="txn-tr" key={impact.id}>
                  <td className="txn-td">{impact.name}</td>
                  <td className="txn-td txn-td--calc-mode">
                    {IMPACT_TYPE_LABELS[impact.impact_type]}
                  </td>
                  <td className="txn-td txn-td--amount-wide">
                    {formatMinorUnitsAsEuro(impact.amount_minor)}
                  </td>
                  <td className="txn-td txn-td--actions-wide">
                    <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() =>
                          deleteImpact.mutate({ projectId, scenarioId, impactId: impact.id })
                        }
                      >
                        Löschen
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {(recurringImpacts ?? []).length === 0 && (
                <tr className="txn-tr">
                  <td className="txn-td" colSpan={4}>
                    <span className="metric-hint">Keine Folgekosten hinterlegt.</span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: "var(--space-4)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 className="card-heading">Zielwirkung</h2>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setIsGoalAssumptionOpen(true)}
          >
            + Sparziel verknüpfen
          </button>
        </div>
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Sparziel</th>
                <th className="txn-th txn-th--amount-wide">Entnahme</th>
                <th className="txn-th txn-th--amount-wide">Schutzminimum</th>
                <th className="txn-th txn-th--actions-wide"></th>
              </tr>
            </thead>
            <tbody>
              {(goalAssumptions ?? []).map((assumption) => (
                <tr className="txn-tr" key={assumption.id}>
                  <td className="txn-td">
                    {goalNames.get(assumption.savings_goal_id) ?? assumption.savings_goal_id}
                  </td>
                  <td className="txn-td txn-td--amount-wide">
                    {formatMinorUnitsAsEuro(assumption.withdrawal_minor ?? 0)}
                  </td>
                  <td className="txn-td txn-td--amount-wide">
                    {formatMinorUnitsAsEuro(assumption.protected_minimum_minor ?? 0)}
                  </td>
                  <td className="txn-td txn-td--actions-wide">
                    <div className="actions-cell" style={{ justifyContent: "flex-end" }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() =>
                          deleteGoalAssumption.mutate({
                            projectId,
                            scenarioId,
                            goalAssumptionId: assumption.id,
                          })
                        }
                      >
                        Löschen
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {(goalAssumptions ?? []).length === 0 && (
                <tr className="txn-tr">
                  <td className="txn-td" colSpan={4}>
                    <span className="metric-hint">Kein Sparziel verknüpft.</span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {summary && summary.goal_impacts.length > 0 && (
          <ul style={{ marginTop: "var(--space-3)" }}>
            {summary.goal_impacts.map((impact) => (
              <li key={impact.savings_goal_id}>
                {impact.goal_name}:{" "}
                {impact.protection_breached && (
                  <span className="status-badge status-badge--warning">Schutzminimum verletzt</span>
                )}{" "}
                {impact.delay_months !== null && impact.delay_months > 0 && (
                  <span>Verzögerung um {impact.delay_months} Monate</span>
                )}
                {!impact.reachable && <span>Ziel unter diesen Annahmen nicht erreichbar</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {isImpactOpen && (
        <RecurringImpactDialog
          projectId={projectId}
          scenarioId={scenarioId}
          onClose={() => setIsImpactOpen(false)}
        />
      )}

      {isGoalAssumptionOpen && (
        <GoalAssumptionDialog
          projectId={projectId}
          scenarioId={scenarioId}
          goals={goalsQuery.data ?? []}
          onClose={() => setIsGoalAssumptionOpen(false)}
        />
      )}
    </div>
  );
}

function TransferredLoan({ source, onOpen }: { source: FinancingSource; onOpen: () => void }) {
  const plan = source.debt_plan;
  const schedule = useDebtSchedule(plan?.id ?? null);
  if (!plan) return null;
  const firstRate = (plan.rate_periods ?? [])[0];
  const assumedRate = (plan.rate_periods ?? []).find((period) => period.rate_kind === "assumed");
  const regular = schedule.data?.rows.find(
    (row) => row.scheduled_payment_minor > 0 && row.month >= plan.repayment_free_months,
  );
  return (
    <article className="assumption-loan">
      <header>
        <div>
          <h3>{source.name}</h3>
          <span className="status-badge status-badge--highlight">Finanzierung</span>
        </div>
        <strong>{formatMinorUnitsAsEuro(source.planned_amount_minor)}</strong>
      </header>
      <dl>
        <div>
          <dt>Start / Abruf</dt>
          <dd>
            {new Date(plan.starts_on).toLocaleDateString("de-DE")} ·{" "}
            {plan.draw_mode === "immediate"
              ? "sofort"
              : plan.draw_mode === "cost_plan"
                ? "nach Kostenplan"
                : `${(plan.draws ?? []).length} Tranchen`}
          </dd>
        </div>
        <div>
          <dt>Sollzins</dt>
          <dd>
            {firstRate
              ? `${(firstRate.nominal_rate_bps_annual / 100).toLocaleString("de-DE", { minimumFractionDigits: 2 })} %`
              : "—"}
            {firstRate && (
              <small>{firstRate.rate_kind === "contracted" ? "vertraglich" : "Annahme"}</small>
            )}
          </dd>
        </div>
        <div>
          <dt>Tilgung / Laufzeit</dt>
          <dd>
            {plan.amortization_mode === "initial_repayment"
              ? `${((plan.initial_repayment_rate_bps ?? 0) / 100).toLocaleString("de-DE")} % anfänglich`
              : plan.amortization_mode === "full_repayment_term"
                ? `${plan.target_term_months} Monate`
                : "feste Monatsrate"}
            <small>{plan.repayment_free_months} Monate tilgungsfrei</small>
          </dd>
        </div>
        <div>
          <dt>Monatsrate</dt>
          <dd>
            {schedule.isLoading
              ? "lädt…"
              : formatMinorUnitsAsEuro(
                  regular?.cash_outflow_minor ?? plan.fixed_payment_minor ?? 0,
                )}
          </dd>
        </div>
        <div>
          <dt>Anschlusszins</dt>
          <dd>
            {assumedRate
              ? `${(assumedRate.nominal_rate_bps_annual / 100).toLocaleString("de-DE", { minimumFractionDigits: 2 })} %`
              : "keine Annahme"}
          </dd>
        </div>
      </dl>
      <button type="button" className="btn-link" onClick={onOpen}>
        In Finanzierung öffnen →
      </button>
    </article>
  );
}

/** Minor Units als Eingabetext, leer bei "nicht gesetzt". */
function toEuroInput(minorUnits: number | undefined): string {
  if (minorUnits === undefined) return "";
  return String(minorUnits / 100).replace(".", ",");
}

function CashflowAssumptionsForm({
  projectId,
  scenarioId,
  saved,
}: {
  projectId: string;
  scenarioId: string;
  saved: CashflowAssumptions | null;
}) {
  // Startwerte kommen aus dem gespeicherten Datensatz. Der Aufrufer haengt
  // einen `key` an diese Komponente, damit ein neu geladener Datensatz sie
  // frisch aufbaut - ein `setState` im Effect waere unter den
  // React-Compiler-Regeln dieses Projekts ein Fehler.
  const [startingLiquidityEuro, setStartingLiquidityEuro] = useState(
    toEuroInput(saved?.starting_liquidity_minor),
  );
  const [netIncomeEuro, setNetIncomeEuro] = useState(toEuroInput(saved?.monthly_net_income_minor));
  const [livingCostsEuro, setLivingCostsEuro] = useState(
    toEuroInput(saved?.monthly_living_costs_minor),
  );
  const [housingEuro, setHousingEuro] = useState(toEuroInput(saved?.monthly_current_housing_minor));
  const [existingDebtEuro, setExistingDebtEuro] = useState(
    toEuroInput(saved?.monthly_existing_debt_service_minor),
  );

  const suggestMutation = useSuggestCashflowAssumptions();
  const saveMutation = useSaveCashflowAssumptions();

  const applySuggestion = () => {
    suggestMutation.mutate(
      { projectId, scenarioId },
      {
        onSuccess: (suggestion) => {
          setStartingLiquidityEuro(
            String(suggestion.starting_liquidity_minor / 100).replace(".", ","),
          );
          setNetIncomeEuro(String(suggestion.monthly_net_income_minor / 100).replace(".", ","));
          setLivingCostsEuro(String(suggestion.monthly_living_costs_minor / 100).replace(".", ","));
          setExistingDebtEuro(
            String(suggestion.monthly_existing_debt_service_minor / 100).replace(".", ","),
          );
        },
      },
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    saveMutation.mutate({
      projectId,
      scenarioId,
      input: {
        starting_liquidity_minor: parseAmountToMinorUnits(startingLiquidityEuro) ?? 0,
        monthly_net_income_minor: parseAmountToMinorUnits(netIncomeEuro) ?? 0,
        monthly_living_costs_minor: parseAmountToMinorUnits(livingCostsEuro) ?? 0,
        monthly_current_housing_minor: parseAmountToMinorUnits(housingEuro) ?? 0,
        monthly_existing_debt_service_minor: parseAmountToMinorUnits(existingDebtEuro) ?? 0,
        monthly_other_commitments_minor: 0,
      },
    });
  };

  return (
    <div className="card">
      <div className="assumptions-form__header">
        <h2 className="card-heading">Haushaltsannahmen</h2>
        <button
          type="button"
          className="btn-secondary"
          onClick={applySuggestion}
          disabled={suggestMutation.isPending}
        >
          Aus Buchungshistorie vorschlagen
        </button>
      </div>
      <form className="assumptions-form" onSubmit={handleSubmit}>
        <div className="form-field">
          <label className="form-label" htmlFor="assumption-liquidity">
            Verfügbare Liquidität (€)
          </label>
          <input
            id="assumption-liquidity"
            className="form-input form-input--mono"
            value={startingLiquidityEuro}
            onChange={(e) => setStartingLiquidityEuro(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="assumption-income">
            Monatliches Nettoeinkommen (€)
          </label>
          <input
            id="assumption-income"
            className="form-input form-input--mono"
            value={netIncomeEuro}
            onChange={(e) => setNetIncomeEuro(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="assumption-living">
            Monatliche Lebenshaltungskosten (€)
          </label>
          <input
            id="assumption-living"
            className="form-input form-input--mono"
            value={livingCostsEuro}
            onChange={(e) => setLivingCostsEuro(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="assumption-housing">
            Aktuelle Wohnkosten (€)
          </label>
          <input
            id="assumption-housing"
            className="form-input form-input--mono"
            value={housingEuro}
            onChange={(e) => setHousingEuro(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="assumption-debt">
            Bestehender Schuldendienst (€/Monat)
          </label>
          <input
            id="assumption-debt"
            className="form-input form-input--mono"
            value={existingDebtEuro}
            onChange={(e) => setExistingDebtEuro(e.target.value)}
          />
        </div>
        {saveMutation.isError && (
          <p className="status-error" role="alert" style={{ gridColumn: "1 / -1" }}>
            Annahmen konnten nicht gespeichert werden.
          </p>
        )}
        <button
          type="submit"
          className="btn-primary"
          style={{ gridColumn: "1 / -1" }}
          disabled={saveMutation.isPending}
        >
          Annahmen speichern
        </button>
      </form>
    </div>
  );
}

function RecurringImpactDialog({
  projectId,
  scenarioId,
  onClose,
}: {
  projectId: string;
  scenarioId: string;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [impactType, setImpactType] = useState<RecurringImpactType>("expense_increase");
  const [amountEuro, setAmountEuro] = useState("");
  const [startsOn, setStartsOn] = useState(new Date().toISOString().slice(0, 10));
  const createMutation = useCreateRecurringImpact();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const amountMinor = parseAmountToMinorUnits(amountEuro) ?? 0;
    if (!name.trim() || amountMinor <= 0) return;
    createMutation.mutate(
      {
        projectId,
        scenarioId,
        input: {
          name: name.trim(),
          impact_type: impactType,
          amount_minor: amountMinor,
          frequency: "monthly",
          starts_on: startsOn,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Modal title="Neue Folgekosten" onClose={onClose}>
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
      >
        <div className="form-field">
          <label className="form-label" htmlFor="impact-name">
            Name *
          </label>
          <input
            id="impact-name"
            className="form-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="impact-type">
            Art
          </label>
          <select
            id="impact-type"
            className="form-input"
            value={impactType}
            onChange={(e) => setImpactType(e.target.value as RecurringImpactType)}
          >
            {Object.entries(IMPACT_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="impact-amount">
            Monatlicher Betrag (€) *
          </label>
          <input
            id="impact-amount"
            className="form-input form-input--mono"
            value={amountEuro}
            onChange={(e) => setAmountEuro(e.target.value)}
            required
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="impact-starts">
            Beginn
          </label>
          <input
            id="impact-starts"
            type="date"
            className="form-input"
            value={startsOn}
            onChange={(e) => setStartsOn(e.target.value)}
          />
        </div>
        {createMutation.isError && (
          <p className="status-error" role="alert">
            Folgekosten konnten nicht gespeichert werden.
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={createMutation.isPending}>
          Speichern
        </button>
      </form>
    </Modal>
  );
}

function GoalAssumptionDialog({
  projectId,
  scenarioId,
  goals,
  onClose,
}: {
  projectId: string;
  scenarioId: string;
  goals: { id: string; name: string }[];
  onClose: () => void;
}) {
  const [savingsGoalId, setSavingsGoalId] = useState(goals[0]?.id ?? "");
  const [withdrawalEuro, setWithdrawalEuro] = useState("0");
  const [protectedMinimumEuro, setProtectedMinimumEuro] = useState("0");
  const [baselineContributionEuro, setBaselineContributionEuro] = useState("0");
  const [projectContributionEuro, setProjectContributionEuro] = useState("0");
  const saveMutation = useSaveGoalAssumption();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!savingsGoalId) return;
    saveMutation.mutate(
      {
        projectId,
        scenarioId,
        input: {
          savings_goal_id: savingsGoalId,
          withdrawal_minor: parseAmountToMinorUnits(withdrawalEuro) ?? 0,
          protected_minimum_minor: parseAmountToMinorUnits(protectedMinimumEuro) ?? 0,
          baseline_monthly_contribution_minor:
            parseAmountToMinorUnits(baselineContributionEuro) ?? 0,
          project_monthly_contribution_minor: parseAmountToMinorUnits(projectContributionEuro) ?? 0,
        },
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Modal title="Sparziel verknüpfen" onClose={onClose}>
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
      >
        <div className="form-field">
          <label className="form-label" htmlFor="goal-assumption-goal">
            Sparziel *
          </label>
          <select
            id="goal-assumption-goal"
            className="form-input"
            value={savingsGoalId}
            onChange={(e) => setSavingsGoalId(e.target.value)}
            required
          >
            <option value="">Bitte wählen</option>
            {goals.map((goal) => (
              <option key={goal.id} value={goal.id}>
                {goal.name}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="goal-withdrawal">
            Geplante Entnahme (€)
          </label>
          <input
            id="goal-withdrawal"
            className="form-input form-input--mono"
            value={withdrawalEuro}
            onChange={(e) => setWithdrawalEuro(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="goal-protected">
            Schutzminimum (€)
          </label>
          <input
            id="goal-protected"
            className="form-input form-input--mono"
            value={protectedMinimumEuro}
            onChange={(e) => setProtectedMinimumEuro(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="goal-baseline">
            Bisheriger Sparbeitrag (€/Monat)
          </label>
          <input
            id="goal-baseline"
            className="form-input form-input--mono"
            value={baselineContributionEuro}
            onChange={(e) => setBaselineContributionEuro(e.target.value)}
          />
        </div>
        <div className="form-field">
          <label className="form-label" htmlFor="goal-project">
            Sparbeitrag nach Vorhaben (€/Monat)
          </label>
          <input
            id="goal-project"
            className="form-input form-input--mono"
            value={projectContributionEuro}
            onChange={(e) => setProjectContributionEuro(e.target.value)}
          />
        </div>
        {saveMutation.isError && (
          <p className="status-error" role="alert">
            Zielannahme konnte nicht gespeichert werden.
          </p>
        )}
        <button type="submit" className="btn-primary" disabled={saveMutation.isPending}>
          Speichern
        </button>
      </form>
    </Modal>
  );
}
