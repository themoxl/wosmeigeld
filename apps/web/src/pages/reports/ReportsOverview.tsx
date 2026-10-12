import { useMemo } from "react";
import { formatMonthYear } from "../../lib/dates";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { AnalyticsQuery, AnalyticsResult, CashflowTrendResponse } from "../../lib/types";
import { ReportLimitNotice, ReportMonthlyTable } from "./ReportMonthlyTable";
import {
  pivotMonthlyResult,
  reportMonth,
  type MonthlyPivot,
  type MonthlyPivotRow,
} from "./reportPivots";

type OpenDrilldown = (title: string, query: AnalyticsQuery) => void;

function signedDelta(value: number) {
  return `${value > 0 ? "+" : ""}${formatMinorUnitsAsEuro(value)}`;
}

function kpiTone(value: number) {
  return value >= 0 ? "transaction-amount--positive" : "transaction-amount--negative";
}

function Kpi({
  label,
  value,
  comparison,
  periodHint,
  rate = false,
}: {
  label: string;
  value: number;
  comparison: string;
  periodHint: string;
  rate?: boolean;
}) {
  return (
    <article className="report-kpi">
      <span className="report-kpi__label">{label}</span>
      <strong className={`report-kpi__value ${kpiTone(value)}`}>
        {rate ? `${value.toFixed(1)} %` : formatMinorUnitsAsEuro(value)}
      </strong>
      <span className="report-kpi__comparison">{comparison}</span>
      <span className="report-kpi__period">{periodHint}</span>
    </article>
  );
}

function cashflowPivot(trend: CashflowTrendResponse, today: Date): MonthlyPivot {
  const months = trend.months.map((entry) => reportMonth(entry.year, entry.month, today));
  const row = (
    identityKey: string,
    label: string,
    getValue: (index: number) => number,
    hasBookings: (index: number) => boolean,
  ): MonthlyPivotRow => {
    const values = Object.fromEntries(months.map((month, index) => [month.key, getValue(index)]));
    const counts = Object.fromEntries(
      months.map((month, index) => [month.key, hasBookings(index) ? 1 : 0]),
    );
    const sum = Object.values(values).reduce<number>((total, value) => total + value, 0);
    return {
      id: identityKey,
      identityKey,
      label,
      groupId: null,
      groupLabel: null,
      values,
      counts,
      sum,
      average: months.length ? Math.round(sum / months.length) : 0,
      count: Object.values(counts).reduce((total, count) => total + count, 0),
      share: 0,
    };
  };
  const rows = [
    row(
      "income",
      "Einnahmen",
      (index) => trend.months[index]?.income_minor ?? 0,
      (index) => (trend.months[index]?.income_minor ?? 0) !== 0,
    ),
    row(
      "expense",
      "Ausgaben",
      (index) => -(trend.months[index]?.expense_minor ?? 0),
      (index) => (trend.months[index]?.expense_minor ?? 0) !== 0,
    ),
    row(
      "net",
      "Saldo",
      (index) => trend.months[index]?.net_minor ?? 0,
      (index) => {
        const month = trend.months[index];
        return Boolean(month && (month.income_minor !== 0 || month.expense_minor !== 0));
      },
    ),
  ];
  return {
    months,
    rows,
    monthTotals: {},
    sum: 0,
    average: 0,
    count: rows.reduce((total, entry) => total + entry.count, 0),
  };
}

export function ReportsOverview({
  trend,
  trendLoading,
  trendError,
  groups,
  groupsLoading,
  groupsError,
  onOpen,
  rowsQuery,
}: {
  trend?: CashflowTrendResponse;
  trendLoading: boolean;
  trendError: boolean;
  groups?: AnalyticsResult;
  groupsLoading: boolean;
  groupsError: boolean;
  onOpen: OpenDrilldown;
  rowsQuery: (spec: {
    field?: string | null;
    value?: string | null;
    from_date: string;
    to_date: string;
    direction?: "expense" | "income" | null;
    cashflowRole?: "expense" | "income" | "net" | null;
  }) => AnalyticsQuery;
}) {
  const today = useMemo(() => new Date(), []);
  const pivot = useMemo(() => (trend ? cashflowPivot(trend, today) : null), [trend, today]);
  const groupPivot = useMemo(
    () => (groups ? pivotMonthlyResult(groups, "category_group", undefined, today) : null),
    [groups, today],
  );
  const completeMonths = (trend?.months ?? []).filter(
    (entry) => !(entry.year === today.getFullYear() && entry.month === today.getMonth() + 1),
  );
  const decision = completeMonths.at(-1) ?? trend?.months.at(-1);
  const previous = decision
    ? completeMonths
        .filter(
          (entry) =>
            entry.year < decision.year ||
            (entry.year === decision.year && entry.month < decision.month),
        )
        .at(-1)
    : undefined;
  const periodRows = trend?.months ?? [];
  const periodIncome = periodRows.reduce((sum, entry) => sum + entry.income_minor, 0);
  const periodExpense = periodRows.reduce((sum, entry) => sum + entry.expense_minor, 0);
  const periodNet = periodRows.reduce((sum, entry) => sum + entry.net_minor, 0);
  const decisionLabel = decision
    ? formatMonthYear(new Date(decision.year, decision.month - 1, 1))
    : "Monat";
  const decisionSavings =
    decision && decision.income_minor > 0 ? (decision.net_minor / decision.income_minor) * 100 : 0;
  const previousSavings =
    previous && previous.income_minor > 0 ? (previous.net_minor / previous.income_minor) * 100 : 0;
  const periodSavings = periodIncome > 0 ? (periodNet / periodIncome) * 100 : 0;

  if (trendLoading && !trend) return <p className="report-state">Monatsübersicht wird geladen…</p>;
  if (trendError)
    return (
      <p className="report-state report-state--error">
        Die Monatsübersicht konnte nicht geladen werden.
      </p>
    );
  if (!trend || trend.months.length === 0 || !pivot || !decision) {
    return <p className="report-state">Für diesen Zeitraum liegen keine Cashflow-Daten vor.</p>;
  }

  const compare = (current: number, before: number | undefined, rate = false) =>
    before === undefined
      ? "Kein vollständiger Vormonat zum Vergleich"
      : rate
        ? `${current - before >= 0 ? "+" : ""}${(current - before).toFixed(1)} Prozentpunkte zum Vormonat`
        : `${signedDelta(current - before)} zum Vormonat`;

  return (
    <div className="reports-ledger-stack">
      <section aria-labelledby="report-kpis-title">
        <div className="report-section-heading">
          <div>
            <p className="report-eyebrow">Entscheidungsmonat</p>
            <h2 id="report-kpis-title">{decisionLabel}</h2>
          </div>
          <p>Der jüngste vollständig abgeschlossene Monat.</p>
        </div>
        <div className="report-kpi-strip">
          <Kpi
            label={`Einnahmen · ${decisionLabel}`}
            value={decision.income_minor}
            comparison={compare(decision.income_minor, previous?.income_minor)}
            periodHint={`Ø Zeitraum ${formatMinorUnitsAsEuro(Math.round(periodIncome / periodRows.length))}`}
          />
          <Kpi
            label={`Ausgaben · ${decisionLabel}`}
            value={-decision.expense_minor}
            comparison={compare(
              -decision.expense_minor,
              previous ? -previous.expense_minor : undefined,
            )}
            periodHint={`Ø Zeitraum ${formatMinorUnitsAsEuro(-Math.round(periodExpense / periodRows.length))}`}
          />
          <Kpi
            label={`Saldo · ${decisionLabel}`}
            value={decision.net_minor}
            comparison={compare(decision.net_minor, previous?.net_minor)}
            periodHint={`Summe Zeitraum ${formatMinorUnitsAsEuro(periodNet)}`}
          />
          <Kpi
            label={`Sparquote · ${decisionLabel}`}
            value={decisionSavings}
            comparison={compare(decisionSavings, previous ? previousSavings : undefined, true)}
            periodHint={`Zeitraum ${periodSavings.toFixed(1)} %`}
            rate
          />
        </div>
      </section>

      <section className="report-ledger-section" aria-labelledby="cashflow-ledger-title">
        <div className="report-section-heading">
          <div>
            <p className="report-eyebrow">Monat für Monat</p>
            <h2 id="cashflow-ledger-title">Cashflow-Kontrollbuch</h2>
          </div>
          <p>Exakte Werte; ein Klick öffnet die Buchungen dieses Monats.</p>
        </div>
        <ReportMonthlyTable
          pivot={pivot}
          labelHeader="Cashflow"
          showCount={false}
          showFooter={false}
          tone="signed"
          ariaLabel="Cashflow nach Monat"
          onCellClick={(row, month) => {
            const cashflowRole =
              row.identityKey === "income"
                ? "income"
                : row.identityKey === "expense"
                  ? "expense"
                  : "net";
            onOpen(
              `${row.label} · ${month.label}`,
              rowsQuery({ from_date: month.from, to_date: month.to, cashflowRole }),
            );
          }}
        />
      </section>

      <section className="report-ledger-section" aria-labelledby="drivers-ledger-title">
        <div className="report-section-heading">
          <div>
            <p className="report-eyebrow">Ausgabentreiber</p>
            <h2 id="drivers-ledger-title">Kategoriegruppen nach Monat</h2>
          </div>
          <p>Die größten Treiber je Monat, ohne verdeckte Chart-Werte.</p>
        </div>
        {groupsLoading && !groups ? (
          <p className="report-state">Ausgabentreiber werden geladen…</p>
        ) : null}
        {groupsError ? (
          <p className="report-state report-state--error">
            Die Ausgabentreiber konnten nicht geladen werden.
          </p>
        ) : null}
        {groups && groupPivot && groupPivot.rows.length > 0 ? (
          <>
            <ReportLimitNotice result={groups} />
            <ReportMonthlyTable
              pivot={groupPivot}
              labelHeader="Gruppe"
              showShare
              ariaLabel="Ausgaben nach Kategoriegruppe und Monat"
              onCellClick={(row, month) =>
                onOpen(
                  `${row.label} · ${month.label}`,
                  rowsQuery({
                    field: "category_group",
                    value: row.id,
                    from_date: month.from,
                    to_date: month.to,
                    direction: "expense",
                  }),
                )
              }
            />
          </>
        ) : groups && !groupsLoading && !groupsError ? (
          <p className="report-state">Keine Ausgaben im gewählten Zeitraum.</p>
        ) : null}
      </section>
    </div>
  );
}
