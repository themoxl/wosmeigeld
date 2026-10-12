import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { useDebtPlansByFinancingSource, useDebtSchedule } from "../../hooks/useDebtPlans";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { DebtScheduleRow } from "../../lib/types";
import { Chart } from "../charts/Chart";
import {
  axisStyle,
  baseOption,
  formatAxisAmount,
  formatTooltipAmount,
  readPalette,
} from "../charts/chartTheme";

// Tilgungsverlauf einer Finanzierungsquelle (Zins, Tilgung, Restschuld je Jahr).
//
// Der Rechenkern liefert das seit P2 monatsgenau ueber
// GET /v1/debt-plans/{id}/schedule; bis hierher gab es dafuer keine Ansicht -
// `useDebtSchedule` war Tote-Hook-Code. Bewusst INLINE statt als Modal: der
// Sinn der Ansicht ist der Vergleich zwischen Varianten, und ein Dialog
// verdeckt genau die Tabelle, gegen die man vergleichen will.

interface DebtSchedulePanelProps {
  financingSourceId: string;
  financingSourceName: string;
}

interface YearRow {
  year: number;
  payment: number;
  interest: number;
  principal: number;
  closing: number;
  /** Monate ohne Tilgungsanteil - macht die tilgungsfreie Zeit sichtbar. */
  repaymentFreeMonths: number;
}

function aggregateByYear(rows: DebtScheduleRow[]): YearRow[] {
  const years = new Map<number, YearRow>();
  for (const row of rows) {
    const year = Math.floor(row.month / 12) + 1;
    const entry = years.get(year) ?? {
      year,
      payment: 0,
      interest: 0,
      principal: 0,
      closing: 0,
      repaymentFreeMonths: 0,
    };
    // Rate gesamt ist der tatsaechliche Abfluss. Beim endfaelligen Darlehen
    // gehoert deshalb die Schluss-Sondertilgung zwingend in das letzte Jahr.
    entry.payment += row.cash_outflow_minor;
    entry.interest += row.interest_paid_minor;
    entry.principal += row.principal_payment_minor + row.special_repayment_minor;
    // Der letzte Monat des Jahres bestimmt den Jahresendstand; die Zeilen
    // kommen aufsteigend, deshalb genuegt Ueberschreiben.
    entry.closing = row.closing_balance_minor;
    if (row.principal_payment_minor === 0 && row.interest_accrued_minor > 0) {
      entry.repaymentFreeMonths += 1;
    }
    years.set(year, entry);
  }
  return [...years.values()].sort((a, b) => a.year - b.year);
}

export function DebtSchedulePanel({
  financingSourceId,
  financingSourceName,
}: DebtSchedulePanelProps) {
  const { data: plans, isLoading: plansLoading } = useDebtPlansByFinancingSource(financingSourceId);
  const planId = plans?.[0]?.id ?? null;
  const { data: schedule, isLoading, isError, error } = useDebtSchedule(planId);
  const [showMonths, setShowMonths] = useState(false);

  const yearRows = useMemo(() => aggregateByYear(schedule?.rows ?? []), [schedule]);

  const option = useMemo<EChartsOption>(() => {
    const palette = readPalette();
    const axis = axisStyle(palette);
    return {
      ...baseOption(palette),
      legend: { ...baseOption(palette).legend, data: ["Zinsen", "Tilgung", "Restschuld"] },
      tooltip: {
        ...baseOption(palette).tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        valueFormatter: (value) => formatTooltipAmount(Number(value)),
      },
      xAxis: {
        type: "category",
        data: yearRows.map((r) => `Jahr ${r.year}`),
        ...axis,
      },
      yAxis: [
        {
          type: "value",
          name: "pro Jahr",
          ...axis,
          axisLabel: { ...axis.axisLabel, formatter: (v: number) => formatAxisAmount(v) },
        },
        {
          type: "value",
          name: "Restschuld",
          ...axis,
          axisLabel: { ...axis.axisLabel, formatter: (v: number) => formatAxisAmount(v) },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "Zinsen",
          type: "bar",
          stack: "rate",
          data: yearRows.map((r) => r.interest),
        },
        {
          name: "Tilgung",
          type: "bar",
          stack: "rate",
          data: yearRows.map((r) => r.principal),
        },
        {
          name: "Restschuld",
          type: "line",
          yAxisIndex: 1,
          symbol: "none",
          lineStyle: { width: 2, color: palette.textSecondary },
          itemStyle: { color: palette.textSecondary },
          data: yearRows.map((r) => r.closing),
        },
      ],
    };
  }, [yearRows]);

  if (plansLoading || isLoading) {
    return <p className="placeholder-note">Lade Tilgungsverlauf...</p>;
  }
  if (!planId) {
    return (
      <p className="placeholder-note">
        Für „{financingSourceName}“ ist noch kein Tilgungsplan hinterlegt.
      </p>
    );
  }
  if (isError || !schedule) {
    return (
      <p className="status-error" role="alert">
        Tilgungsverlauf konnte nicht berechnet werden
        {error instanceof Error ? `: ${error.message}` : "."}
      </p>
    );
  }

  const firstRow = schedule.rows[0];
  const repaymentFreeTotal = yearRows.reduce((sum, r) => sum + r.repaymentFreeMonths, 0);
  const hasSpecialRepayment = schedule.rows.some((row) => row.special_repayment_minor > 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <div className="metric-grid">
        <div className="card metric-card">
          <span className="metric-label">Anfangsrate</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(firstRow?.cash_outflow_minor ?? 0)}
          </span>
          <span className="metric-hint">pro Monat</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Höchste Rate</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(schedule.peak_payment_minor)}
          </span>
          <span className="metric-hint">im gesamten Verlauf</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">Zinsen gesamt</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(schedule.total_interest_minor)}
          </span>
          <span className="metric-hint">über die gerechnete Laufzeit</span>
        </div>
        <div className="card metric-card">
          <span className="metric-label">
            {schedule.payoff_month === null ? "Restschuld am Ende" : "Vollständig getilgt"}
          </span>
          <span className="metric-value">
            {schedule.payoff_month === null
              ? formatMinorUnitsAsEuro(schedule.ending_balance_minor)
              : `nach ${Math.floor(schedule.payoff_month / 12)} J ${schedule.payoff_month % 12} M`}
          </span>
          <span className="metric-hint">
            {schedule.payoff_month === null
              ? "Anschlussfinanzierung nötig"
              : hasSpecialRepayment
                ? "inkl. geplanter Schlusszahlung"
                : "ohne Sondertilgung"}
          </span>
        </div>
      </div>

      {repaymentFreeTotal > 0 && (
        <p className="metric-hint">
          {repaymentFreeTotal} tilgungsfreie Monate: In dieser Zeit zahlst du nur Zinsen, die
          Restschuld bleibt unverändert stehen. In der Grafik sind das die Jahre ohne
          Tilgungs-Balken.
        </p>
      )}
      {schedule.first_negative_amortization_month !== null && (
        <p className="status-error" role="alert">
          Ab Monat {schedule.first_negative_amortization_month} deckt die Rate nicht einmal die
          Zinsen — die Restschuld wächst.
        </p>
      )}

      <Chart
        option={option}
        height={320}
        description={`Tilgungsverlauf für ${financingSourceName}: Zinsen und Tilgung je Jahr als gestapelte Balken, Restschuld als Linie.`}
        tableFallback={<YearTable rows={yearRows} />}
      />

      <div className="chart-toolbar">
        <button
          type="button"
          className="chart-toggle"
          onClick={() => setShowMonths((value) => !value)}
          aria-pressed={showMonths}
        >
          {showMonths ? "Jahresansicht" : "Monatsansicht"}
        </button>
      </div>

      {showMonths ? <MonthTable rows={schedule.rows} /> : <YearTable rows={yearRows} />}
    </div>
  );
}

function YearTable({ rows }: { rows: YearRow[] }) {
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table">
        <thead>
          <tr>
            <th className="txn-th">Jahr</th>
            <th className="txn-th txn-th--amount-wide">Rate gesamt</th>
            <th className="txn-th txn-th--amount-wide">davon Zinsen</th>
            <th className="txn-th txn-th--amount-wide">davon Tilgung</th>
            <th className="txn-th txn-th--amount-wide">Restschuld</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className="txn-tr" key={row.year}>
              <td className="txn-td">
                <span className="txn-payee">{`Jahr ${row.year}`}</span>
                {row.repaymentFreeMonths > 0 && (
                  <span
                    className="status-badge status-badge--neutral"
                    style={{ marginLeft: "var(--space-2)" }}
                  >
                    {row.repaymentFreeMonths} M tilgungsfrei
                  </span>
                )}
              </td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(row.payment)}</td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(row.interest)}</td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.principal)}
              </td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(row.closing)}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr className="txn-tr">
              <td className="txn-td" colSpan={5}>
                <span className="metric-hint">Keine Zahlungen im Rechenhorizont.</span>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function MonthTable({ rows }: { rows: DebtScheduleRow[] }) {
  return (
    <div className="txn-table-wrapper" style={{ maxHeight: "26rem", overflowY: "auto" }}>
      <table className="txn-table">
        <thead>
          <tr>
            <th className="txn-th">Monat</th>
            <th className="txn-th txn-th--amount-wide">Rate</th>
            <th className="txn-th txn-th--amount-wide">Zinsen</th>
            <th className="txn-th txn-th--amount-wide">Tilgung</th>
            <th className="txn-th txn-th--amount-wide">Restschuld</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className="txn-tr" key={row.month}>
              <td className="txn-td">
                <span className="txn-payee">
                  {`${Math.floor(row.month / 12) + 1}/${(row.month % 12) + 1}`}
                </span>
                {row.principal_payment_minor === 0 && row.interest_accrued_minor > 0 && (
                  <span
                    className="status-badge status-badge--neutral"
                    style={{ marginLeft: "var(--space-2)" }}
                  >
                    tilgungsfrei
                  </span>
                )}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.cash_outflow_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.interest_paid_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.principal_payment_minor + row.special_repayment_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.closing_balance_minor)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
