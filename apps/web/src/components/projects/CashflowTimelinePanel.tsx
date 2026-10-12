import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { useProjectTimeline } from "../../hooks/useProjects";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { CashflowMonthRow } from "../../lib/types";
import { Chart } from "../charts/Chart";
import {
  axisStyle,
  baseOption,
  formatAxisAmount,
  formatTooltipAmount,
  readPalette,
} from "../charts/chartTheme";

// Die vollstaendige Aufstellung: was reinkommt, was rausgeht, was das Vorhaben
// kostet und was am Monatsende uebrig bleibt.
//
// Der Rechenkern liefert das seit P2 monatsgenau ueber
// GET /projects/{id}/scenarios/{sid}/timeline - `useProjectTimeline` war bis
// hierher toter Hook-Code. Die Kennzahlen weiter oben zeigen nur Endergebnisse
// ("niedrigste Reserve", "Monatsueberschuss"); wo diese Zahlen herkommen und
// wann es eng wird, stand nirgends.

interface CashflowTimelinePanelProps {
  projectId: string;
  scenarioId: string;
}

interface YearRow {
  year: number;
  income: number;
  living: number;
  housing: number;
  existingDebt: number;
  other: number;
  projectOutflows: number;
  loanInflows: number;
  newDebtService: number;
  recurring: number;
  goals: number;
  net: number;
  reserveEnd: number;
}

function aggregateByYear(rows: CashflowMonthRow[]): YearRow[] {
  const years = new Map<number, YearRow>();
  for (const row of rows) {
    const year = Math.floor(row.month / 12) + 1;
    const entry = years.get(year) ?? {
      year,
      income: 0,
      living: 0,
      housing: 0,
      existingDebt: 0,
      other: 0,
      projectOutflows: 0,
      loanInflows: 0,
      newDebtService: 0,
      recurring: 0,
      goals: 0,
      net: 0,
      reserveEnd: 0,
    };
    entry.income += row.baseline_income_minor;
    entry.living += row.baseline_living_costs_minor;
    entry.housing += row.current_housing_minor;
    entry.existingDebt += row.existing_debt_service_minor;
    entry.other += row.other_commitments_minor;
    entry.projectOutflows += row.project_cost_outflows_minor;
    entry.loanInflows += row.loan_and_grant_inflows_minor;
    entry.newDebtService += row.new_debt_service_minor;
    entry.recurring += row.recurring_project_impacts_minor;
    entry.goals += row.goal_contributions_minor;
    entry.net += row.net_household_cashflow_minor;
    // Die Reserve ist ein Stand, keine Summe: der letzte Monat des Jahres gilt.
    entry.reserveEnd = row.liquid_reserve_end_minor;
    years.set(year, entry);
  }
  return [...years.values()].sort((a, b) => a.year - b.year);
}

export function CashflowTimelinePanel({ projectId, scenarioId }: CashflowTimelinePanelProps) {
  const { data, isLoading, isError } = useProjectTimeline(projectId, scenarioId);
  const [showMonths, setShowMonths] = useState(false);

  const months = useMemo(() => data?.months ?? [], [data]);
  const yearRows = useMemo(() => aggregateByYear(months), [months]);

  const option = useMemo<EChartsOption>(() => {
    const palette = readPalette();
    const axis = axisStyle(palette);
    return {
      ...baseOption(palette),
      legend: {
        ...baseOption(palette).legend,
        data: ["Einnahmen", "Lebenshaltung & Wohnen", "Vorhaben", "Kapitaldienst", "Reserve"],
      },
      tooltip: {
        ...baseOption(palette).tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        valueFormatter: (value) => formatTooltipAmount(Number(value)),
      },
      xAxis: { type: "category", data: yearRows.map((r) => `Jahr ${r.year}`), ...axis },
      yAxis: [
        {
          type: "value",
          name: "pro Jahr",
          ...axis,
          axisLabel: { ...axis.axisLabel, formatter: (v: number) => formatAxisAmount(v) },
        },
        {
          type: "value",
          name: "Reserve",
          ...axis,
          axisLabel: { ...axis.axisLabel, formatter: (v: number) => formatAxisAmount(v) },
          splitLine: { show: false },
        },
      ],
      series: [
        { name: "Einnahmen", type: "bar", data: yearRows.map((r) => r.income + r.recurring) },
        {
          name: "Lebenshaltung & Wohnen",
          type: "bar",
          stack: "aus",
          data: yearRows.map((r) => -(r.living + r.housing + r.existingDebt + r.other)),
        },
        {
          name: "Vorhaben",
          type: "bar",
          stack: "aus",
          data: yearRows.map((r) => -r.projectOutflows),
        },
        {
          name: "Kapitaldienst",
          type: "bar",
          stack: "aus",
          data: yearRows.map((r) => -r.newDebtService),
        },
        {
          name: "Reserve",
          type: "line",
          yAxisIndex: 1,
          symbol: "none",
          lineStyle: { width: 2, color: palette.textSecondary },
          itemStyle: { color: palette.textSecondary },
          data: yearRows.map((r) => r.reserveEnd),
        },
      ],
    };
  }, [yearRows]);

  if (isLoading) {
    return <p className="placeholder-note">Lade Aufstellung...</p>;
  }
  if (isError || !data) {
    return (
      <p className="status-error" role="alert">
        Die Aufstellung konnte nicht berechnet werden.
      </p>
    );
  }
  if (months.length === 0) {
    return (
      <p className="placeholder-note">
        Noch keine Aufstellung — dafür braucht es Haushaltsannahmen und mindestens eine
        Kostenposition oder Finanzierungsquelle.
      </p>
    );
  }

  const lowest = months.reduce((worst, row) =>
    row.liquid_reserve_end_minor < worst.liquid_reserve_end_minor ? row : worst,
  );
  const negativeMonths = months.filter((row) => row.liquid_reserve_end_minor < 0).length;

  return (
    <div className="card" style={{ marginTop: "var(--space-4)" }}>
      <h2 className="card-heading">Aufstellung Monat für Monat</h2>
      <p className="metric-hint">
        Alles, woraus sich die Kennzahlen oben ergeben: Einnahmen, Lebenshaltung, laufende
        Wohnkosten, die Auszahlungen für das Vorhaben, der Kapitaldienst der Darlehen und was am
        Ende übrig bleibt.
      </p>

      <div className="metric-grid" style={{ margin: "var(--space-3) 0" }}>
        <div className="metric-card">
          <span className="metric-label">Niedrigste Reserve</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(lowest.liquid_reserve_end_minor)}
          </span>
          <span className="metric-hint">
            in Monat {lowest.month} (Jahr {Math.floor(lowest.month / 12) + 1})
          </span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Monate im Minus</span>
          <span className="metric-value">{negativeMonths}</span>
          <span className="metric-hint">von {months.length} gerechneten</span>
        </div>
        <div className="metric-card">
          <span className="metric-label">Reserve am Ende</span>
          <span className="metric-value">
            {formatMinorUnitsAsEuro(months[months.length - 1]?.liquid_reserve_end_minor ?? 0)}
          </span>
        </div>
      </div>

      <Chart
        option={option}
        height={320}
        description="Jahresaufstellung: Einnahmen als Balken nach oben, Ausgaben nach unten, Reserve als Linie."
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

      {showMonths ? <MonthTable rows={months} /> : <YearTable rows={yearRows} />}
    </div>
  );
}

const HEADERS = [
  "Einnahmen",
  "Lebenshaltung",
  "Wohnen bisher",
  "Bestehende Kredite",
  "Vorhaben",
  "Kredit-Auszahlung",
  "Kapitaldienst",
  "Folgekosten",
  "Sparbeiträge",
  "Netto",
  "Reserve",
];

function HeaderRow({ first }: { first: string }) {
  return (
    <tr>
      <th className="txn-th">{first}</th>
      {HEADERS.map((label) => (
        <th className="txn-th txn-th--amount-wide" key={label}>
          {label}
        </th>
      ))}
    </tr>
  );
}

function YearTable({ rows }: { rows: YearRow[] }) {
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table">
        <thead>
          <HeaderRow first="Jahr" />
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className="txn-tr" key={row.year}>
              <td className="txn-td">
                <span className="txn-payee">{`Jahr ${row.year}`}</span>
              </td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(row.income)}</td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(-row.living)}</td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(-row.housing)}</td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.existingDebt)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.projectOutflows)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.loanInflows)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.newDebtService)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.recurring)}
              </td>
              <td className="txn-td txn-td--amount-wide">{formatMinorUnitsAsEuro(-row.goals)}</td>
              <td className="txn-td txn-td--amount-wide">
                <strong>{formatMinorUnitsAsEuro(row.net)}</strong>
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.reserveEnd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MonthTable({ rows }: { rows: CashflowMonthRow[] }) {
  return (
    <div className="txn-table-wrapper" style={{ maxHeight: "26rem", overflowY: "auto" }}>
      <table className="txn-table">
        <thead>
          <HeaderRow first="Monat" />
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className="txn-tr" key={row.month}>
              <td className="txn-td">
                <span className="txn-payee">
                  {`${Math.floor(row.month / 12) + 1}/${(row.month % 12) + 1}`}
                </span>
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.baseline_income_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.baseline_living_costs_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.current_housing_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.existing_debt_service_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.project_cost_outflows_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.loan_and_grant_inflows_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.new_debt_service_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.recurring_project_impacts_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(-row.goal_contributions_minor)}
              </td>
              <td className="txn-td txn-td--amount-wide">
                <strong>{formatMinorUnitsAsEuro(row.net_household_cashflow_minor)}</strong>
              </td>
              <td className="txn-td txn-td--amount-wide">
                {formatMinorUnitsAsEuro(row.liquid_reserve_end_minor)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
