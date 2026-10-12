import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { listAccounts, listGoals } from "../../lib/api";
import {
  useCreateFinancingSource,
  useDeleteFinancingSource,
  useEquityAvailability,
  useFinancialSummary,
  useFinancingSources,
  useProject,
  useUpdateFinancingSource,
} from "../../hooks/useProjects";
import { useDebtSchedule } from "../../hooks/useDebtPlans";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../../lib/money";
import type { FinancingSource, FinancingSourceStatus, FinancingSourceType } from "../../lib/types";
import { AddFundingSourceDialog } from "./AddFundingSourceDialog";
import { DebtSchedulePanel } from "./DebtSchedulePanel";
import { LoanConfigurationEditor } from "./LoanConfigurationEditor";

const SOURCE_TYPE_LABELS: Record<FinancingSourceType, string> = {
  cash_equity: "Eigenkapital",
  asset_sale: "Vermögensverkauf",
  commercial_loan: "Bankdarlehen",
  subsidized_loan: "Förderkredit",
  public_grant: "Zuschuss",
  private_contribution: "Private Zuwendung",
  non_cash_equity: "Eigen-/Sachleistung",
  other: "Sonstiges",
};

const STATUS_LABELS: Record<FinancingSourceStatus, string> = {
  idea: "Idee",
  requested: "Beantragt",
  likely: "Wahrscheinlich",
  approved: "Genehmigt",
  contracted: "Vertraglich fixiert",
  received: "Erhalten",
  rejected: "Abgelehnt",
};

const LOAN_TYPES: FinancingSourceType[] = ["commercial_loan", "subsidized_loan"];

export function FinancingPanel({
  projectId,
  scenarioId,
  focusSourceId,
}: {
  projectId: string;
  scenarioId: string;
  focusSourceId?: string | null;
}) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const { data: sources, isLoading, isError } = useFinancingSources(projectId, scenarioId);
  const { data: summary } = useFinancialSummary(projectId, scenarioId);
  const { data: equity } = useEquityAvailability(projectId);
  const { data: project } = useProject(projectId);
  const deleteMutation = useDeleteFinancingSource();
  const [editingId, setEditingId] = useState<string | null>(focusSourceId ?? null);
  const [expandedScheduleId, setExpandedScheduleId] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isFundingOpen, setIsFundingOpen] = useState(false);

  const accountsQuery = useQuery({
    queryKey: ["accounts", selectedHouseholdId],
    queryFn: () => listAccounts(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
  const goalsQuery = useQuery({
    queryKey: ["goals", selectedHouseholdId],
    queryFn: () => listGoals(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
  const accountNames = useMemo(
    () => new Map((accountsQuery.data ?? []).map((account) => [account.id, account.name])),
    [accountsQuery.data],
  );

  useEffect(() => {
    if (!focusSourceId || !(sources ?? []).some((source) => source.id === focusSourceId)) return;
    requestAnimationFrame(() => {
      setEditingId(focusSourceId);
      const sourceElement = document.getElementById(`financing-source-${focusSourceId}`);
      if (typeof sourceElement?.scrollIntoView === "function") {
        sourceElement.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }, [focusSourceId, sources]);

  if (isLoading) return <p className="placeholder-note">Lade Finanzierungsfahrplan…</p>;
  if (isError)
    return (
      <p className="status-error" role="alert">
        Finanzierung konnte nicht geladen werden.
      </p>
    );

  const cost = summary?.funding_gap.cash_required_until_completion_minor ?? 0;
  const financed = Math.max(cost - (summary?.funding_gap.planned_gap_minor ?? cost), 0);
  const gap = Math.max(summary?.funding_gap.planned_gap_minor ?? 0, 0);
  const coverage = cost > 0 ? Math.min(financed / cost, 1) * 100 : 0;

  return (
    <div className="financing-roadmap">
      <section className="coverage-file" aria-label="Finanzierungsdeckung">
        <div className="coverage-file__header">
          <div>
            <span className="eyebrow">Finanzierungsakte</span>
            <h2>Geld folgt Kosten</h2>
          </div>
          <div className="coverage-file__actions">
            {project && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setIsFundingOpen(true)}
              >
                + Förderung wählen
              </button>
            )}
            <button type="button" className="btn-primary" onClick={() => setIsCreating(true)}>
              + Quelle anlegen
            </button>
          </div>
        </div>
        <div
          className="coverage-strip"
          aria-label={`${Math.round(coverage)} Prozent der Projektkosten finanziert`}
        >
          <span className="coverage-strip__fill" style={{ width: `${coverage}%` }} />
        </div>
        <dl className="coverage-totals">
          <div>
            <dt>Projektkosten</dt>
            <dd>{formatMinorUnitsAsEuro(cost)}</dd>
          </div>
          <div>
            <dt>Davon finanziert</dt>
            <dd>{formatMinorUnitsAsEuro(financed)}</dd>
          </div>
          <div className={gap > 0 ? "coverage-totals__gap" : "coverage-totals__covered"}>
            <dt>Offene Lücke</dt>
            <dd>{formatMinorUnitsAsEuro(gap)}</dd>
          </div>
        </dl>
        <p className="coverage-file__statement">
          {gap > 0
            ? `Kauf und frühe Zahlungen können zuerst belegt werden; für spätere Maßnahmen bleiben ${formatMinorUnitsAsEuro(gap)} offen.`
            : "Kauf und geplante Maßnahmen sind durch die eingetragenen Quellen gedeckt."}
        </p>
      </section>

      {isCreating && (
        <NewSourceEditor
          projectId={projectId}
          scenarioId={scenarioId}
          accounts={accountsQuery.data ?? []}
          goals={goalsQuery.data ?? []}
          onClose={() => setIsCreating(false)}
        />
      )}

      <div className="financing-source-list">
        {(sources ?? []).map((source) => {
          const isLoan = LOAN_TYPES.includes(source.source_type);
          const isEditing = editingId === source.id;
          return (
            <article
              className={`financing-source ${isEditing ? "financing-source--editing" : ""}`}
              id={`financing-source-${source.id}`}
              key={source.id}
            >
              {isLoan ? (
                <LoanSourceSummary source={source} />
              ) : (
                <SimpleSourceSummary
                  source={source}
                  accountName={source.account_id ? accountNames.get(source.account_id) : undefined}
                />
              )}
              <div className="financing-source__toolbar">
                {isLoan && source.debt_plan && (
                  <button
                    type="button"
                    className="btn-secondary"
                    aria-expanded={expandedScheduleId === source.id}
                    aria-controls={`schedule-${source.id}`}
                    onClick={() =>
                      setExpandedScheduleId((current) => (current === source.id ? null : source.id))
                    }
                  >
                    {expandedScheduleId === source.id
                      ? "Tilgungsverlauf schließen"
                      : "Tilgungsverlauf"}
                  </button>
                )}
                <button
                  type="button"
                  className="btn-secondary"
                  aria-expanded={isEditing}
                  aria-controls={`editor-${source.id}`}
                  onClick={() =>
                    setEditingId((current) => (current === source.id ? null : source.id))
                  }
                >
                  {isEditing
                    ? "Bearbeitung schließen"
                    : source.debt_plan || !isLoan
                      ? "Bearbeiten"
                      : "Tilgungsplan anlegen"}
                </button>
                <button
                  type="button"
                  className="btn-link financing-source__delete"
                  onClick={() =>
                    deleteMutation.mutate({ projectId, scenarioId, sourceId: source.id })
                  }
                >
                  Quelle löschen
                </button>
              </div>
              {isEditing && (
                <div id={`editor-${source.id}`} className="financing-source__editor">
                  {isLoan ? (
                    <LoanConfigurationEditor
                      projectId={projectId}
                      scenarioId={scenarioId}
                      source={source}
                      sourceType={source.source_type as "commercial_loan" | "subsidized_loan"}
                      onCancel={() => setEditingId(null)}
                      onSaved={() => setEditingId(null)}
                    />
                  ) : (
                    <SimpleSourceEditor
                      projectId={projectId}
                      scenarioId={scenarioId}
                      source={source}
                      sourceType={source.source_type}
                      accounts={accountsQuery.data ?? []}
                      goals={goalsQuery.data ?? []}
                      onCancel={() => setEditingId(null)}
                      onSaved={() => setEditingId(null)}
                    />
                  )}
                </div>
              )}
              {expandedScheduleId === source.id && source.debt_plan && (
                <div id={`schedule-${source.id}`} className="financing-source__schedule">
                  <DebtSchedulePanel
                    financingSourceId={source.id}
                    financingSourceName={source.name}
                  />
                </div>
              )}
            </article>
          );
        })}
        {(sources ?? []).length === 0 && (
          <div className="financing-empty">
            <strong>Noch keine Finanzierung eingetragen</strong>
            <p>
              Lege zuerst Eigenkapital, Bankdarlehen oder eine Förderung an. Kredite können sofort
              oder passend zum Kostenplan abgerufen werden.
            </p>
            <button type="button" className="btn-primary" onClick={() => setIsCreating(true)}>
              Erste Quelle anlegen
            </button>
          </div>
        )}
      </div>

      {equity && equity.accounts.length > 0 && (
        <details className="equity-details">
          <summary>Verfügbares Eigenkapital je Konto</summary>
          <div className="equity-details__list">
            {equity.accounts.map((account) => (
              <div key={account.account_id}>
                <strong>{account.account_name}</strong>
                <span>{formatMinorUnitsAsEuro(account.available_minor)} verfügbar</span>
                <small>
                  {formatMinorUnitsAsEuro(account.reserved_by_other_projects_minor)} anderweitig
                  reserviert
                </small>
              </div>
            ))}
          </div>
        </details>
      )}

      {isFundingOpen && project && (
        <AddFundingSourceDialog
          project={project}
          scenarioId={scenarioId}
          onClose={() => setIsFundingOpen(false)}
        />
      )}
    </div>
  );
}

function LoanSourceSummary({ source }: { source: FinancingSource }) {
  const plan = source.debt_plan;
  const scheduleQuery = useDebtSchedule(plan?.id ?? null);
  const firstPeriod = plan?.rate_periods?.[0];
  const regularRow = scheduleQuery.data?.rows.find(
    (row) => row.scheduled_payment_minor > 0 && row.month >= (plan?.repayment_free_months ?? 0),
  );
  const bindingEnd = firstPeriod?.ends_after_month;
  const bindingRow =
    bindingEnd === null || bindingEnd === undefined
      ? null
      : scheduleQuery.data?.rows.find((row) => row.month === bindingEnd);
  const draws =
    plan?.draw_mode === "immediate"
      ? [{ occurs_after_month: 0, amount_minor: plan.opening_balance_minor }]
      : (plan?.draws ?? []);
  const nextDraw = draws.find((draw) => draw.occurs_after_month >= 0);
  return (
    <div className="financing-source__body">
      <header className="financing-source__header">
        <div>
          <div className="financing-source__title">
            <h3>{source.name}</h3>
            <span
              className={`status-badge ${source.status === "contracted" || source.status === "received" ? "status-badge--positive" : "status-badge--neutral"}`}
            >
              {STATUS_LABELS[source.status]}
            </span>
          </div>
          <p>
            {SOURCE_TYPE_LABELS[source.source_type]}
            {source.funding_program_version_id ? " · aus Förderkatalog" : ""}
          </p>
        </div>
        <strong className="financing-source__amount">
          {formatMinorUnitsAsEuro(source.planned_amount_minor)}
        </strong>
      </header>
      {plan ? (
        <>
          <dl className="loan-facts">
            <div>
              <dt>Sollzins</dt>
              <dd>
                {firstPeriod
                  ? `${(firstPeriod.nominal_rate_bps_annual / 100).toLocaleString("de-DE", { minimumFractionDigits: 2 })} %`
                  : "—"}
                <small>{firstPeriod?.rate_kind === "contracted" ? "vertraglich" : "Annahme"}</small>
              </dd>
            </div>
            <div>
              <dt>Tilgung / Laufzeit</dt>
              <dd>
                {plan.amortization_mode === "initial_repayment"
                  ? `${((plan.initial_repayment_rate_bps ?? 0) / 100).toLocaleString("de-DE")} % anfänglich`
                  : plan.amortization_mode === "full_repayment_term"
                    ? `${plan.target_term_months} Monate`
                    : "feste Rate"}
                <small>
                  {plan.repayment_free_months
                    ? `${plan.repayment_free_months} Monate tilgungsfrei`
                    : "Tilgung ab Start"}
                </small>
              </dd>
            </div>
            <div>
              <dt>Reguläre Monatsrate</dt>
              <dd>
                {scheduleQuery.isLoading
                  ? "lädt…"
                  : formatMinorUnitsAsEuro(
                      regularRow?.cash_outflow_minor ?? plan.fixed_payment_minor ?? 0,
                    )}
                <small>{plan.repayment_free_months ? "nach der Anlaufzeit" : "ab Beginn"}</small>
              </dd>
            </div>
            <div>
              <dt>{bindingRow ? "Restschuld nach Bindung" : "Tilgungsende"}</dt>
              <dd>
                {bindingRow
                  ? formatMinorUnitsAsEuro(bindingRow.closing_balance_minor)
                  : scheduleQuery.data?.payoff_month
                    ? `Monat ${scheduleQuery.data.payoff_month}`
                    : "offen"}
                <small>
                  {firstPeriod?.ends_after_month !== null
                    ? `Bindung ${((firstPeriod?.ends_after_month ?? -1) + 1) / 12} Jahre`
                    : "ohne festes Bindungsende"}
                </small>
              </dd>
            </div>
          </dl>
          <div className="loan-draw-line">
            <div>
              <span>Auszahlung</span>
              <strong>
                {plan.draw_mode === "immediate"
                  ? "sofort"
                  : plan.draw_mode === "cost_plan"
                    ? "nach Kostenplan"
                    : "in eigenen Tranchen"}
              </strong>
            </div>
            <MoneyFollowsCosts draws={draws} />
            <p>
              {nextDraw
                ? `Nächster Abruf: ${nextDraw.occurs_after_month === 0 ? "Kauf / Start" : `Monat ${nextDraw.occurs_after_month}`} · ${formatMinorUnitsAsEuro(nextDraw.amount_minor)}`
                : "Noch kein Abruf geplant"}
            </p>
          </div>
        </>
      ) : (
        <div className="loan-missing">
          <strong>Kreditbetrag ist erfasst, die Rückzahlung noch nicht.</strong>
          <span>
            Öffne „Tilgungsplan anlegen“ – Betrag und Förderkonditionen sind bereits vorbelegt.
          </span>
        </div>
      )}
    </div>
  );
}

function MoneyFollowsCosts({
  draws,
}: {
  draws: { occurs_after_month: number; amount_minor: number }[];
}) {
  if (draws.length === 0) return <div className="money-flow money-flow--empty">Keine Abrufe</div>;
  const maxMonth = Math.max(...draws.map((draw) => draw.occurs_after_month), 1);
  return (
    <div className="money-flow" aria-label="Geld folgt Kosten">
      <span className="money-flow__rail" />
      {draws.slice(0, 8).map((draw, index) => (
        <span
          key={`${draw.occurs_after_month}-${index}`}
          className="money-flow__draw"
          style={{ left: `${Math.min((draw.occurs_after_month / maxMonth) * 92 + 4, 96)}%` }}
          title={`${draw.occurs_after_month === 0 ? "Start" : `Monat ${draw.occurs_after_month}`}: ${formatMinorUnitsAsEuro(draw.amount_minor)}`}
        >
          <i />
          <small>
            {draw.occurs_after_month === 0
              ? "Start"
              : `J${Math.floor(draw.occurs_after_month / 12) + 1}`}
          </small>
        </span>
      ))}
    </div>
  );
}

function SimpleSourceSummary({
  source,
  accountName,
}: {
  source: FinancingSource;
  accountName?: string;
}) {
  return (
    <div className="financing-source__body">
      <header className="financing-source__header">
        <div>
          <div className="financing-source__title">
            <h3>{source.name}</h3>
            <span className="status-badge status-badge--neutral">
              {STATUS_LABELS[source.status]}
            </span>
          </div>
          <p>
            {SOURCE_TYPE_LABELS[source.source_type]}
            {accountName ? ` · ${accountName}` : ""}
          </p>
        </div>
        <strong className="financing-source__amount">
          {formatMinorUnitsAsEuro(source.planned_amount_minor)}
        </strong>
      </header>
      <p className="simple-source-note">
        Verfügbar{" "}
        {source.available_on
          ? new Date(source.available_on).toLocaleDateString("de-DE")
          : "ab Projektstart"}
      </p>
    </div>
  );
}

function NewSourceEditor({
  projectId,
  scenarioId,
  accounts,
  goals,
  onClose,
}: {
  projectId: string;
  scenarioId: string;
  accounts: { id: string; name: string }[];
  goals: { id: string; name: string }[];
  onClose: () => void;
}) {
  const [type, setType] = useState<FinancingSourceType>("commercial_loan");
  return (
    <section className="financing-new">
      <div className="financing-new__header">
        <div>
          <span className="eyebrow">Neue Quelle</span>
          <h3>Wie wird das Vorhaben finanziert?</h3>
        </div>
        <label className="form-field">
          <span className="form-label">Art</span>
          <select
            className="form-input"
            value={type}
            onChange={(event) => setType(event.target.value as FinancingSourceType)}
          >
            {Object.entries(SOURCE_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {LOAN_TYPES.includes(type) ? (
        <LoanConfigurationEditor
          key={type}
          projectId={projectId}
          scenarioId={scenarioId}
          source={null}
          sourceType={type as "commercial_loan" | "subsidized_loan"}
          onCancel={onClose}
          onSaved={onClose}
        />
      ) : (
        <SimpleSourceEditor
          projectId={projectId}
          scenarioId={scenarioId}
          source={null}
          sourceType={type}
          accounts={accounts}
          goals={goals}
          onCancel={onClose}
          onSaved={onClose}
        />
      )}
    </section>
  );
}

function SimpleSourceEditor({
  projectId,
  scenarioId,
  source,
  sourceType,
  accounts,
  goals,
  onCancel,
  onSaved,
}: {
  projectId: string;
  scenarioId: string;
  source: FinancingSource | null;
  sourceType: FinancingSourceType;
  accounts: { id: string; name: string }[];
  goals: { id: string; name: string }[];
  onCancel: () => void;
  onSaved: () => void;
}) {
  const createMutation = useCreateFinancingSource();
  const updateMutation = useUpdateFinancingSource();
  const [name, setName] = useState(source?.name ?? SOURCE_TYPE_LABELS[sourceType]);
  const [amount, setAmount] = useState(
    source ? String(source.planned_amount_minor / 100).replace(".", ",") : "",
  );
  const [status, setStatus] = useState<FinancingSourceStatus>(source?.status ?? "idea");
  const [availableOn, setAvailableOn] = useState(source?.available_on ?? "");
  const [accountId, setAccountId] = useState(source?.account_id ?? accounts[0]?.id ?? "");
  const [goalId, setGoalId] = useState(source?.savings_goal_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const amountMinor = parseAmountToMinorUnits(amount) ?? 0;
    if (!name.trim()) return setError("Bitte eine Bezeichnung eintragen.");
    if (amountMinor <= 0) return setError("Bitte einen Betrag größer als 0 € eintragen.");
    if (sourceType === "cash_equity" && !accountId)
      return setError("Bitte das Konto für das Eigenkapital auswählen.");
    const input = {
      name: name.trim(),
      status,
      planned_amount_minor: amountMinor,
      available_on: availableOn || undefined,
      account_id: sourceType === "cash_equity" ? accountId : undefined,
      savings_goal_id: goalId || undefined,
    };
    if (source)
      updateMutation.mutate(
        { projectId, scenarioId, sourceId: source.id, version: source.version, input },
        { onSuccess: onSaved },
      );
    else
      createMutation.mutate(
        { projectId, scenarioId, input: { ...input, source_type: sourceType } },
        { onSuccess: onSaved },
      );
  };
  const pending = createMutation.isPending || updateMutation.isPending;
  return (
    <form className="simple-source-editor" onSubmit={submit}>
      <div className="form-grid-3">
        <label className="form-field">
          <span className="form-label">Bezeichnung</span>
          <input
            className="form-input"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="form-field">
          <span className="form-label">Betrag (€)</span>
          <input
            className="form-input form-input--mono"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </label>
        <label className="form-field">
          <span className="form-label">Status</span>
          <select
            className="form-input"
            value={status}
            onChange={(event) => setStatus(event.target.value as FinancingSourceStatus)}
          >
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="form-field">
          <span className="form-label">Verfügbar am</span>
          <input
            type="date"
            className="form-input"
            value={availableOn}
            onChange={(event) => setAvailableOn(event.target.value)}
          />
        </label>
        {sourceType === "cash_equity" && (
          <label className="form-field">
            <span className="form-label">Konto</span>
            <select
              className="form-input"
              value={accountId}
              onChange={(event) => setAccountId(event.target.value)}
            >
              <option value="">Bitte wählen</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {goals.length > 0 && (
          <label className="form-field">
            <span className="form-label">Sparziel (optional)</span>
            <select
              className="form-input"
              value={goalId}
              onChange={(event) => setGoalId(event.target.value)}
            >
              <option value="">Keines</option>
              {goals.map((goal) => (
                <option key={goal.id} value={goal.id}>
                  {goal.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {(error || createMutation.error || updateMutation.error) && (
        <p className="status-error" role="alert">
          {error ?? "Quelle konnte nicht gespeichert werden."}
        </p>
      )}
      <div className="loan-editor__actions">
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Abbrechen
        </button>
        <button type="submit" className="btn-primary" disabled={pending}>
          {pending ? "Speichert…" : "Quelle speichern"}
        </button>
      </div>
    </form>
  );
}
