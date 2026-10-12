import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { components } from "@finanzplaner/contracts";
import { useAuth } from "../../auth/AuthProvider";
import { useHousehold } from "../../household/HouseholdProvider";
import { Chart } from "../../components/charts/Chart";
import { stackedBarOption } from "../../components/charts/buildOptions";
import { monthRange, rowsQuery } from "../../lib/analytics";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { AnalyticsQuery, AnalyticsResult } from "../../lib/types";
import { getVariableCosts, type VariableCostsResponse } from "./reportQueries";

// Register "Variable Kosten" (Gegenstueck zum Register "Fixkosten").
//
// Die Liquiditaetsvorschau nennt eine einzige grosse Zahl - "variable Ausgaben
// ca. 4.000 EUR / Monat". Ohne Aufschluesselung ist sie nicht handhabbar:
// Fixkosten kann man kaum beeinflussen, variable Kosten schon, aber nur, wenn
// man sieht, woraus sie bestehen. Genau das steht hier.
//
// Die Zahlen kommen unveraendert von `GET /v1/reports/variable-costs`; hier
// wird nichts nachgerechnet. Der Server wendet dieselbe Abgrenzung an wie die
// Vorschau (`household_forecast_service.covered_category_ids`) - eine zweite
// Rechnung im Browser wuerde genau den Widerspruch erzeugen, den der Bericht
// aufloesen soll.

type VariableCostCategory = components["schemas"]["VariableCostCategory"];
type VariableCostMonth = components["schemas"]["VariableCostMonth"];

type OpenDrilldown = (title: string, query: AnalyticsQuery) => void;

/**
 * Kein `enabled`-Schalter wie bei den Registern, die ReportsPage zentral
 * laedt: diese Komponente wird ueberhaupt nur gerendert, wenn ihr Register
 * offen ist (wie YearComparisonReport). Ein zusaetzliches Flag waere immer
 * `true` und damit eine Behauptung, die niemand prueft.
 */
function useVariableCosts(months: number) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<VariableCostsResponse>({
    queryKey: ["variable-costs", selectedHouseholdId, months],
    queryFn: () => getVariableCosts(accessToken as string, selectedHouseholdId as string, months),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
}

/**
 * Formt die Monatsreihe in die Gestalt, die die Diagrammbauer erwarten.
 *
 * `stackedBarOption` nimmt ein AnalyticsResult - nicht weil diese Zahlen aus
 * dem Explorer kaemen (die Fixkostenreihe kann er gar nicht liefern, sie
 * stammt aus `contract_amount_history`), sondern weil das die gemeinsame
 * Schnittstelle aller Diagramme dieses Projekts ist. Dasselbe Vorgehen wie im
 * Register "Fixkosten".
 */
function costsChartResult(series: VariableCostMonth[]): AnalyticsResult {
  const rows = series.flatMap((month) => {
    const key = `${month.year}-${String(month.month).padStart(2, "0")}-01`;
    return [
      {
        "date:month": key,
        kind: "variable",
        kind_label: "Variable Kosten",
        sum_amount: month.variable_minor,
      },
      {
        "date:month": key,
        kind: "fixed",
        kind_label: "Fixkosten",
        sum_amount: month.fixed_minor,
      },
    ];
  });
  return {
    grain: "aggregate",
    columns: [],
    rows,
    total_row: null,
    row_count: rows.length,
    truncated: false,
    applied_limits: [],
    next_cursor: null,
    query_id: "variable-costs",
    period_from: null,
    period_to: null,
    months_covered: series.length,
  };
}

function Kpi({
  label,
  value,
  detail,
  hint,
}: {
  label: string;
  value: string;
  detail: string;
  hint: string;
}) {
  return (
    <article className="report-kpi">
      <span className="report-kpi__label">{label}</span>
      <strong className="report-kpi__value">{value}</strong>
      <span className="report-kpi__comparison">{detail}</span>
      <span className="report-kpi__period">{hint}</span>
    </article>
  );
}

const SPARK_WIDTH = 72;
const SPARK_HEIGHT = 18;
const SPARK_BAR_RATIO = 0.6;

/**
 * Ein Balken je Monat statt einer Linie: Monatswerte sind Einzelbetraege,
 * keine stetige Groesse - eine Linie wuerde zwischen zwei Monaten
 * interpolieren, was es hier nicht gibt. Rein dekorativ (aria-hidden); die
 * Zahlen stehen als Titel an der Zelle und vollstaendig im Drilldown.
 */
function Sparkline({ values }: { values: number[] }) {
  const peak = Math.max(...values.map((value) => Math.abs(value)), 0);
  if (peak === 0 || values.length === 0) return null;
  const slot = SPARK_WIDTH / values.length;
  const barWidth = Math.max(1.5, slot * SPARK_BAR_RATIO);

  return (
    <svg
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      width={SPARK_WIDTH}
      height={SPARK_HEIGHT}
      aria-hidden="true"
      focusable="false"
    >
      {values.map((value, index) => {
        const height = Math.max(1, (Math.abs(value) / peak) * (SPARK_HEIGHT - 2));
        return (
          <rect
            key={index}
            x={index * slot + (slot - barWidth) / 2}
            y={SPARK_HEIGHT - height}
            width={barWidth}
            height={height}
            rx="1"
            fill="currentColor"
            opacity={value === 0 ? 0.2 : 0.75}
          />
        );
      })}
    </svg>
  );
}

/** Tabellarische Entsprechung des Diagramms (Pflicht, siehe Chart.tsx). */
function CostsByMonthTable({ series }: { series: VariableCostMonth[] }) {
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table">
        <thead>
          <tr>
            <th className="txn-th">Monat</th>
            <th className="txn-th txn-th--amount">Variabel</th>
            <th className="txn-th txn-th--amount">Fix</th>
            <th className="txn-th txn-th--amount">Zusammen</th>
          </tr>
        </thead>
        <tbody>
          {series.map((month) => (
            <tr key={`${month.year}-${month.month}`} className="txn-tr">
              <td className="txn-td">{month.label}</td>
              <td className="txn-td txn-td--amount transaction-amount--negative">
                {formatMinorUnitsAsEuro(month.variable_minor)}
              </td>
              <td className="txn-td txn-td--amount">{formatMinorUnitsAsEuro(month.fixed_minor)}</td>
              <td className="txn-td txn-td--amount">
                {formatMinorUnitsAsEuro(month.variable_minor + month.fixed_minor)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function VariableCostsReport({ months, onOpen }: { months: number; onOpen: OpenDrilldown }) {
  const report = useVariableCosts(months);
  const data = report.data;
  const chartResult = useMemo(() => costsChartResult(data?.series ?? []), [data?.series]);

  // Zeitraum des Drilldowns: erster Tag des aeltesten bis letzter Tag des
  // juengsten ausgewiesenen Monats - der laufende Monat gehoert bewusst NICHT
  // dazu, sonst zeigte die Detailliste Buchungen, die in der Summe darueber
  // nicht enthalten sind.
  const period = useMemo(() => {
    const first = data?.series[0];
    const last = data?.series.at(-1);
    if (!first || !last) return null;
    return {
      from: monthRange(first.year, first.month).from,
      to: monthRange(last.year, last.month).to,
      label: `${first.label}–${last.label}`,
    };
  }, [data?.series]);

  if (report.isLoading && !data) {
    return <p className="report-state">Variable Kosten werden geladen…</p>;
  }
  if (report.isError) {
    return (
      <p className="report-state report-state--error">
        Die variablen Kosten konnten nicht geladen werden.
      </p>
    );
  }
  if (!data || !period) {
    return <p className="report-state">Für diesen Zeitraum liegen keine Buchungen vor.</p>;
  }

  const total = data.monthly_average_minor + data.monthly_fixed_average_minor;
  const variableShare = total > 0 ? (data.monthly_average_minor / total) * 100 : 0;
  const biggest: VariableCostCategory | undefined = data.categories[0];

  return (
    <div className="reports-ledger-stack">
      <section aria-labelledby="variable-costs-kpis-title">
        <div className="report-section-heading">
          <div>
            <p className="report-eyebrow">Der Hebel</p>
            <h2 id="variable-costs-kpis-title">Variable Kosten</h2>
          </div>
          <p>
            Fixkosten sind kaum zu ändern, variable Kosten schon — deshalb steht hier, woraus sie
            bestehen.
          </p>
        </div>
        <div className="report-kpi-strip">
          <Kpi
            label="Variable Kosten · Ø Monat"
            value={formatMinorUnitsAsEuro(data.monthly_average_minor)}
            detail={`${data.categories.length} Kategorie(n)`}
            hint={period.label}
          />
          <Kpi
            label="Fixkosten · Ø Monat"
            value={formatMinorUnitsAsEuro(data.monthly_fixed_average_minor)}
            detail="Vereinbarte Verträge, nicht Buchungen"
            hint={period.label}
          />
          <Kpi
            label="Anteil variabel"
            value={`${variableShare.toFixed(1)} %`}
            detail={`von ${formatMinorUnitsAsEuro(total)} Gesamtkosten je Monat`}
            hint="Variable und fixe Kosten zusammen"
          />
          <Kpi
            label="Größter Posten"
            value={biggest ? formatMinorUnitsAsEuro(biggest.monthly_average_minor) : "—"}
            detail={biggest ? biggest.category_name : "Keine variablen Kosten erfasst"}
            hint={biggest ? `${biggest.share_percent.toFixed(1)} % der variablen Kosten` : ""}
          />
        </div>
      </section>

      <section className="report-ledger-section" aria-labelledby="variable-costs-chart-title">
        <div className="report-section-heading">
          <div>
            <p className="report-eyebrow">Monat für Monat</p>
            <h2 id="variable-costs-chart-title">Variabel gegen fix</h2>
          </div>
          <p>Nur vollständige Monate; der laufende Monat würde den Schnitt verfälschen.</p>
        </div>
        <Chart
          option={stackedBarOption(chartResult, "date:month", "kind", "sum_amount")}
          height={320}
          description="Variable und fixe Kosten je Monat, gestapelt."
          tableFallback={<CostsByMonthTable series={data.series} />}
        />
      </section>

      <section className="report-ledger-section" aria-labelledby="variable-costs-table-title">
        <div className="report-section-heading">
          <div>
            <p className="report-eyebrow">Woraus sie bestehen</p>
            <h2 id="variable-costs-table-title">Variable Kosten nach Kategorie</h2>
          </div>
          <p>Ein Klick auf eine Zeile öffnet die Buchungen dahinter.</p>
        </div>
        {data.categories.length === 0 ? (
          <p className="report-state">Keine variablen Ausgaben im gewählten Zeitraum.</p>
        ) : (
          <div className="txn-table-wrapper">
            <table className="txn-table" aria-label="Variable Kosten nach Kategorie">
              <thead>
                <tr>
                  <th className="txn-th">Kategorie</th>
                  <th className="txn-th txn-th--amount">Ø Monat</th>
                  <th className="txn-th txn-th--amount">Anteil</th>
                  <th className="txn-th txn-th--amount">Buchungen</th>
                  <th className="txn-th txn-th--amount">Verlauf</th>
                </tr>
              </thead>
              <tbody>
                {data.categories.map((category) => {
                  const open = () =>
                    onOpen(
                      `${category.category_name} · ${period.label}`,
                      rowsQuery({
                        field: "category",
                        value: category.category_id,
                        from_date: period.from,
                        to_date: period.to,
                        // Fachliche Cashflow-Zuordnung statt `direction`: der
                        // Bericht zaehlt genau die Splits mit der Rolle
                        // "expense" (inklusive Erstattungen, die dort den
                        // Verbrauch senken). Mit einem Vorzeichenfilter zeigte
                        // die Detailliste eine andere Menge als ihre Summe.
                        cashflowRole: "expense",
                      }),
                    );
                  return (
                    <tr
                      key={category.category_id ?? "uncategorized"}
                      className="txn-tr"
                      onClick={open}
                    >
                      <td className="txn-td">
                        <button
                          type="button"
                          className="report-value-button"
                          onClick={(event) => {
                            // Sonst liefe zusaetzlich der Zeilen-Handler.
                            event.stopPropagation();
                            open();
                          }}
                        >
                          {category.category_name}
                        </button>
                        {category.group_name ? (
                          <span className="report-kpi__period"> · {category.group_name}</span>
                        ) : null}
                      </td>
                      <td className="txn-td txn-td--amount transaction-amount--negative">
                        {formatMinorUnitsAsEuro(category.monthly_average_minor)}
                      </td>
                      <td className="txn-td txn-td--amount">
                        {category.share_percent.toFixed(1)} %
                      </td>
                      <td className="txn-td txn-td--amount">{category.transaction_count}</td>
                      <td
                        className="txn-td txn-td--amount transaction-amount--negative"
                        title={category.trend
                          .map(
                            (value, index) =>
                              `${data.series[index]?.label ?? ""}: ${formatMinorUnitsAsEuro(value)}`,
                          )
                          .join(" · ")}
                      >
                        <Sparkline values={category.trend} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
