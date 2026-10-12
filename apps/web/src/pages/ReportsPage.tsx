import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { Chart } from "../components/charts/Chart";
import { TransactionDrilldown } from "../components/analytics/TransactionDrilldown";
import { stackedBarOption } from "../components/charts/buildOptions";
import { useAnalyticsQuery } from "../hooks/useAnalytics";
import { contractTypeLabel } from "../hooks/useContracts";
import { rowsQuery } from "../lib/analytics";
import { getCashflowTrend, getFixedCostsTrend, getProjectReportsOverview } from "../lib/api";
import { toIsoDateString } from "../lib/dates";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type {
  AnalyticsQuery,
  AnalyticsResult,
  FixedCostsMonthData,
  FixedCostsTrendResponse,
  ProjectReportRow,
} from "../lib/types";
import { CategoryMonthlyReport } from "./reports/CategoryMonthlyReport";
import { PayeeMonthlyReport } from "./reports/PayeeMonthlyReport";
import { ReportsOverview } from "./reports/ReportsOverview";
import { VariableCostsReport } from "./reports/VariableCostsReport";
import { YearComparisonReport } from "./reports/YearComparisonReport";
import {
  categoryMonthlyQuery,
  groupMonthlyQuery,
  payeeMonthlyQuery,
} from "./reports/reportQueries";

// Berichte (docs/ANALYSE-EXPLORER-PLAN.md, Baustein E).
//
// Vorher: zwei Tabellen ohne Filter und ohne Drilldown. Jetzt Register mit
// gemeinsamem Kopf, Diagrammen und der Zusage aus Leitplanke 1 - von jeder
// Zahl fuehrt hoechstens ein Klick zu den Buchungen dahinter.
//
// Ausnahme ist das Register "Fixkosten" (docs/VERTRAEGE-FIXKOSTEN-PLAN.md,
// Abschnitt 12): seine Zeitreihe stammt aus contract_amount_history, also aus
// VEREINBARTEN Betraegen. Dahinter liegen keine Buchungen, zu denen ein
// Drilldown fuehren koennte - eine Jahrespraemie ist in allen zwoelf Monaten
// vereinbart und in genau einem gebucht.
//
// Aus demselben Grund ist "Vorhaben" (docs/VORHABEN-GROSSINVESTITIONEN-
// PLAN.md, Abschnitt 11.7) ein eigenes Register mit eigenem Datenpfad: eine
// Bausumme haengt an Kostenpositionen/Tilgungsplaenen, nicht an
// transaction_splits (siehe Modul-Docstring von analytics_service.py - der
// Explorer ist an dieses Koerungsgrain gebunden, ein Join ueber
// Projektzuordnungen wuerde Summen bei mehreren Positionen je Buchung
// vervielfachen). Keine Vermischung mit dem Cashflow-Istbericht oben.

type Register =
  "ueberblick" | "kategorien" | "empfaenger" | "vergleich" | "fixkosten" | "variabel" | "vorhaben";

const REGISTERS: { key: Register; label: string }[] = [
  { key: "ueberblick", label: "Überblick" },
  { key: "kategorien", label: "Kategorien" },
  { key: "empfaenger", label: "Empfänger" },
  { key: "vergleich", label: "Jahresvergleich" },
  { key: "fixkosten", label: "Fixkosten" },
  { key: "variabel", label: "Variable Kosten" },
  { key: "vorhaben", label: "Vorhaben" },
];

const MONTH_COUNTS = [12, 24] as const;

export function ReportsPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const [register, setRegister] = useState<Register>("ueberblick");
  const [months, setMonths] = useState<number>(12);
  // Titel mit im Zustand: "Buchungen dahinter" allein sagt bei zehn
  // anklickbaren Stellen nicht mehr, hinter WAS man gerade schaut.
  const [drilldown, setDrilldown] = useState<{ title: string; query: AnalyticsQuery } | null>(null);

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const categoryQuery = useMemo(() => categoryMonthlyQuery(months), [months]);
  const payeeQuery = useMemo(() => payeeMonthlyQuery(months), [months]);
  const groupQuery = useMemo(() => groupMonthlyQuery(months), [months]);

  const trend = useQuery({
    queryKey: ["cashflow-trend", householdId, months],
    queryFn: () => getCashflowTrend(token as string, householdId, months),
    enabled: Boolean(token && householdId),
  });

  const fixedCosts = useQuery<FixedCostsTrendResponse>({
    queryKey: ["fixed-costs-trend", householdId, months],
    queryFn: () => getFixedCostsTrend(token as string, householdId, months),
    enabled: Boolean(token && householdId) && register === "fixkosten",
  });

  const projectReports = useQuery<ProjectReportRow[]>({
    queryKey: ["project-reports-overview", householdId],
    queryFn: () => getProjectReportsOverview(token as string, householdId),
    enabled: Boolean(token && householdId) && register === "vorhaben",
  });

  const categories = useAnalyticsQuery(categoryQuery, register === "kategorien");
  const payees = useAnalyticsQuery(payeeQuery, register === "empfaenger");
  const groups = useAnalyticsQuery(groupQuery, register === "ueberblick");

  const activeResult =
    register === "kategorien"
      ? categories.data
      : register === "empfaenger"
        ? payees.data
        : register === "ueberblick"
          ? groups.data
          : undefined;
  const firstTrendMonth = trend.data?.months[0];
  const lastTrendMonth = trend.data?.months.at(-1);
  const firstFixedMonth = fixedCosts.data?.months[0];
  const lastFixedMonth = fixedCosts.data?.months.at(-1);
  const scopeFrom =
    activeResult?.period_from ??
    (register === "ueberblick" && firstTrendMonth
      ? `${firstTrendMonth.year}-${String(firstTrendMonth.month).padStart(2, "0")}-01`
      : register === "fixkosten" && firstFixedMonth
        ? `${firstFixedMonth.year}-${String(firstFixedMonth.month).padStart(2, "0")}-01`
        : null);
  const scopeTo =
    activeResult?.period_to ??
    (register === "ueberblick" && lastTrendMonth
      ? toIsoDateString(new Date(lastTrendMonth.year, lastTrendMonth.month, 0))
      : register === "fixkosten" && lastFixedMonth
        ? toIsoDateString(new Date(lastFixedMonth.year, lastFixedMonth.month, 0))
        : null);
  const today = new Date();
  const activePeriodIsPartial = Boolean(
    (register === "ueberblick" || register === "kategorien" || register === "empfaenger") &&
    scopeTo?.startsWith(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`),
  );
  const dateLabel = (value: string) =>
    new Intl.DateTimeFormat("de-DE").format(new Date(`${value}T12:00:00`));

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Berichte</h1>
          <p className="placeholder-note">
            Jede Zahl lässt sich anklicken und führt zu den Buchungen dahinter.
          </p>
        </div>
        <div className="direction-toggle">
          {MONTH_COUNTS.map((count) => (
            <button
              key={count}
              type="button"
              className={`direction-btn ${months === count ? "direction-btn--active" : ""}`}
              onClick={() => setMonths(count)}
            >
              {count} Monate
            </button>
          ))}
        </div>
      </div>

      <p className="report-scope" data-testid="report-scope">
        {register === "vergleich"
          ? "Like-for-like Jahresvergleich · nur vollständige Monate · Transfers aus · nur Budgetkonten"
          : register === "fixkosten"
            ? `Letzte ${months} Monate · vereinbarte Vertragsbeträge`
            : register === "variabel"
              ? // Der laufende Monat fehlt hier absichtlich: er ist erst halb
                // gebucht und zoege jeden Monatsschnitt nach unten.
                `Letzte ${months} vollständige Monate · ohne vertragsgedeckte Kategorien`
              : register === "vorhaben"
                ? "Aktive Vorhaben · ausgewähltes Szenario"
                : `Letzte ${months} Monate · Transfers aus · nur Budgetkonten`}
        {scopeFrom && scopeTo ? ` · ${dateLabel(scopeFrom)}–${dateLabel(scopeTo)}` : ""}
        {activePeriodIsPartial ? " · letzter Monat laufend/unvollständig" : ""}
      </p>

      <div className="filter-tabs" role="tablist">
        {REGISTERS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            role="tab"
            aria-selected={register === entry.key}
            className={`filter-tab ${register === entry.key ? "filter-tab--active" : ""}`}
            onClick={() => setRegister(entry.key)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {register === "ueberblick" && (
        <ReportsOverview
          trend={trend.data}
          trendLoading={trend.isLoading}
          trendError={trend.isError}
          groups={groups.data}
          groupsLoading={groups.isLoading}
          groupsError={groups.isError}
          rowsQuery={rowsQuery}
          onOpen={(title, query) => setDrilldown({ title, query })}
        />
      )}

      {register === "kategorien" && (
        <CategoryMonthlyReport
          result={categories.data}
          isLoading={categories.isLoading}
          isError={categories.isError}
          months={months}
          onOpen={(title, query) => setDrilldown({ title, query })}
        />
      )}

      {register === "empfaenger" && (
        <PayeeMonthlyReport
          result={payees.data}
          isLoading={payees.isLoading}
          isError={payees.isError}
          onOpen={(title, query) => setDrilldown({ title, query })}
        />
      )}

      {register === "vergleich" && (
        <YearComparisonReport onOpen={(title, query) => setDrilldown({ title, query })} />
      )}

      {register === "fixkosten" && (
        <FixedCostsRegister
          months={fixedCosts.data?.months ?? []}
          isLoading={fixedCosts.isLoading}
        />
      )}

      {register === "variabel" && (
        <VariableCostsReport
          months={months}
          onOpen={(title, query) => setDrilldown({ title, query })}
        />
      )}

      {register === "vorhaben" && (
        <VorhabenRegister rows={projectReports.data ?? []} isLoading={projectReports.isLoading} />
      )}

      {drilldown && (
        <TransactionDrilldown
          title={drilldown.title}
          subtitle="Buchungen hinter dieser Zahl"
          query={drilldown.query}
          onClose={() => setDrilldown(null)}
        />
      )}
    </section>
  );
}

function MetricCard({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: number;
  tone: "positive" | "negative";
  hint?: string;
}) {
  return (
    <div className="card metric-card">
      <span className="metric-label">{label}</span>
      <span
        className={`metric-value ${
          tone === "positive" ? "transaction-amount--positive" : "transaction-amount--negative"
        }`}
      >
        {formatMinorUnitsAsEuro(value)}
      </span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

function monthLabel(entry: { year: number; month: number }): string {
  return `${String(entry.month).padStart(2, "0")}/${String(entry.year).slice(2)}`;
}

/**
 * Formt die Zeitreihe in die Gestalt, die die Diagrammbauer erwarten.
 *
 * `stackedBarOption` nimmt ein AnalyticsResult - nicht weil diese Zahlen aus
 * dem Explorer kaemen (das koennen sie nicht, siehe Kopf der Datei), sondern
 * weil das die gemeinsame Schnittstelle aller Diagramme dieses Projekts ist.
 * Ein zweiter Diagrammweg nur fuer dieses Register haette eigene Farben,
 * eigene Achsenformate und eigene Fehler.
 */
function fixedCostsChartResult(months: FixedCostsMonthData[]): AnalyticsResult {
  const rows = months.flatMap((month) =>
    month.by_type.map((entry) => ({
      "date:month": `${month.year}-${String(month.month).padStart(2, "0")}-01`,
      contract_type: entry.contract_type,
      contract_type_label: contractTypeLabel(entry.contract_type),
      sum_amount: entry.amount_minor,
    })),
  );
  return {
    grain: "aggregate",
    columns: [],
    rows,
    total_row: null,
    row_count: rows.length,
    truncated: false,
    applied_limits: [],
    next_cursor: null,
    query_id: "fixed-costs-trend",
    period_from: null,
    period_to: null,
    months_covered: months.length,
  };
}

/**
 * Register "Fixkosten": was war in diesem Monat vereinbart?
 *
 * Die Zahlen sind bewusst NICHT dieselben wie im Cashflow und auch nicht
 * dieselben wie `BudgetSummary.fixed_costs_monthly_minor` - der Trend zeigt
 * auch pausierte und inzwischen beendete Vertraege in den Monaten, in denen sie
 * liefen. Wer die Vergangenheit nach dem heutigen Status umschriebe, liesse
 * jede Steigerung kleiner aussehen, als sie war.
 */
function FixedCostsRegister({
  months,
  isLoading,
}: {
  months: FixedCostsMonthData[];
  isLoading: boolean;
}) {
  const chartResult = useMemo(() => fixedCostsChartResult(months), [months]);
  const latest = months.length > 0 ? months[months.length - 1] : undefined;
  const latestTypes = useMemo(
    () => [...(latest?.by_type ?? [])].sort((a, b) => b.amount_minor - a.amount_minor),
    [latest],
  );

  if (isLoading) {
    return <p className="placeholder-note">Lade Fixkostenverlauf…</p>;
  }

  if (months.length === 0 || !latest) {
    return (
      <div className="card">
        <h2 className="card-heading">Fixkosten im Verlauf</h2>
        <p className="placeholder-note">
          Noch keine Verträge erfasst. Sobald Miete, Versicherungen oder Abonnements angelegt sind,
          steht hier ihre Entwicklung — aus dem vereinbarten Betrag, nicht aus den Buchungen.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="metric-grid">
        <MetricCard
          label={`Fixkosten ${monthLabel(latest)}`}
          value={latest.fixed_costs_minor}
          tone="negative"
          hint={`${latest.contract_count} Vertrag/Verträge, auf den Monat gerechnet`}
        />
        <div className="card metric-card">
          <span className="metric-label">Gegenüber Vorjahr</span>
          <span
            className={`metric-value ${
              latest.yoy_percent === null
                ? ""
                : latest.yoy_percent > 0
                  ? "transaction-amount--negative"
                  : "transaction-amount--positive"
            }`}
          >
            {latest.yoy_percent === null
              ? "—"
              : `${latest.yoy_percent > 0 ? "+" : ""}${latest.yoy_percent.toFixed(1)} %`}
          </span>
          <span className="metric-hint">
            {latest.yoy_percent === null
              ? "Vorjahresmonat nicht bekannt"
              : "Derselbe Monat im Vorjahr"}
          </span>
        </div>
        <MetricCard
          label="Hochgerechnet aufs Jahr"
          value={latest.fixed_costs_minor * 12}
          tone="negative"
          hint="Zwölf Monate zum heutigen Stand"
        />
      </div>

      <div className="card">
        <h2 className="card-heading">Fixkosten je Monat</h2>
        <p className="placeholder-note">
          Maßgeblich ist der zum Monatsletzten vereinbarte Preis, nicht die Buchung. Eine
          Beitragserhöhung ist damit sichtbar, bevor die erste höhere Abbuchung ankommt.
        </p>
        <Chart
          option={stackedBarOption(chartResult, "date:month", "contract_type", "sum_amount")}
          height={320}
          description="Vereinbarte Fixkosten je Monat, gestapelt nach Vertragsart."
          tableFallback={<FixedCostsTable months={months} />}
        />
      </div>

      <div className="card">
        <h2 className="card-heading">Nach Vertragsart ({monthLabel(latest)})</h2>
        {latestTypes.length === 0 ? (
          <p className="placeholder-note">Für diesen Monat ist kein Vertrag hinterlegt.</p>
        ) : (
          <div className="txn-table-wrapper">
            <table className="txn-table">
              <thead>
                <tr>
                  <th className="txn-th">Vertragsart</th>
                  <th className="txn-th txn-th--amount">Pro Monat</th>
                  <th className="txn-th txn-th--amount">Pro Jahr</th>
                  <th className="txn-th txn-th--amount">Anteil</th>
                </tr>
              </thead>
              <tbody>
                {latestTypes.map((entry) => (
                  <tr key={entry.contract_type} className="txn-tr">
                    <td className="txn-td">{contractTypeLabel(entry.contract_type)}</td>
                    <td className="txn-td txn-td--amount transaction-amount--negative">
                      {formatMinorUnitsAsEuro(entry.amount_minor)}
                    </td>
                    <td className="txn-td txn-td--amount">
                      {formatMinorUnitsAsEuro(entry.amount_minor * 12)}
                    </td>
                    <td className="txn-td txn-td--amount">
                      {latest.fixed_costs_minor > 0
                        ? `${Math.round((entry.amount_minor / latest.fixed_costs_minor) * 100)} %`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

/** Tabellarische Entsprechung des Fixkostendiagramms. */
function FixedCostsTable({ months }: { months: FixedCostsMonthData[] }) {
  return (
    <div className="txn-table-wrapper">
      <table className="txn-table">
        <thead>
          <tr>
            <th className="txn-th">Monat</th>
            <th className="txn-th txn-th--amount">Fixkosten</th>
            <th className="txn-th txn-th--amount">Verträge</th>
            <th className="txn-th txn-th--amount">ggü. Vorjahr</th>
          </tr>
        </thead>
        <tbody>
          {months.map((month) => (
            <tr key={`${month.year}-${month.month}`} className="txn-tr">
              <td className="txn-td">{monthLabel(month)}</td>
              <td className="txn-td txn-td--amount transaction-amount--negative">
                {formatMinorUnitsAsEuro(month.fixed_costs_minor)}
              </td>
              <td className="txn-td txn-td--amount">{month.contract_count}</td>
              <td
                className={`txn-td txn-td--amount ${
                  month.yoy_percent === null
                    ? ""
                    : month.yoy_percent > 0
                      ? "transaction-amount--negative"
                      : "transaction-amount--positive"
                }`}
              >
                {month.yoy_percent === null
                  ? "—"
                  : `${month.yoy_percent > 0 ? "+" : ""}${month.yoy_percent.toFixed(1)} %`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Register "Vorhaben" (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, 11.7):
 * Forecast/Ist/Abweichung und Finanzierungskosten je aktivem Vorhaben mit
 * ausgewaehltem Szenario.
 *
 * Jede Zeile kommt unveraendert vom Server (`GET /v1/projects/reports-
 * overview`, dieselbe Aggregation wie fuer die Dashboardkarte) - keine
 * Neuberechnung hier, damit diese Tabelle nie von dem abweicht, was die
 * Vorhaben-Detailseite fuer dasselbe Projekt zeigt. Kein Diagramm: bei
 * typischerweise wenigen gleichzeitig laufenden Vorhaben liest sich eine
 * Tabelle mit vier Geldspalten klarer als ein gruppierter Balken je Projekt.
 */
function VorhabenRegister({ rows, isLoading }: { rows: ProjectReportRow[]; isLoading: boolean }) {
  const totals = useMemo(
    () =>
      rows.reduce(
        (acc, row) => ({
          forecast: acc.forecast + row.forecast_cost_minor,
          actual: acc.actual + row.actual_cost_minor,
          financing: acc.financing + row.financing_cost_minor,
        }),
        { forecast: 0, actual: 0, financing: 0 },
      ),
    [rows],
  );

  if (isLoading) {
    return <p className="placeholder-note">Lade Vorhabenübersicht…</p>;
  }

  if (rows.length === 0) {
    return (
      <div className="card">
        <h2 className="card-heading">Vorhaben</h2>
        <p className="placeholder-note">
          Kein aktives Vorhaben mit ausgewähltem Szenario. Sobald ein Vorhaben angelegt und ein
          Szenario ausgewählt ist, stehen hier Forecast, Ist, Abweichung und Finanzierungskosten.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="metric-grid">
        <div className="card metric-card">
          <span className="metric-label">Aktive Vorhaben</span>
          <span className="metric-value">{rows.length}</span>
        </div>
        <MetricCard label="Forecast (Cash)" value={totals.forecast} tone="negative" />
        <MetricCard label="Ist" value={totals.actual} tone="negative" />
        <MetricCard label="Finanzierungskosten" value={totals.financing} tone="negative" />
      </div>

      <div className="card">
        <h2 className="card-heading">Vorhaben im Detail</h2>
        <p className="placeholder-note">
          Getrennt vom Cashflow-Istbericht oben: eine Bausumme ist keine Buchung, sondern eine
          Kennzahl aus dem Vorhaben-Modul (Kostenplan, Ist-Zuordnung, Tilgungsplan).
        </p>
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Vorhaben</th>
                <th className="txn-th">Szenario</th>
                <th className="txn-th txn-th--amount">Forecast (Cash)</th>
                <th className="txn-th txn-th--amount">Ist</th>
                <th className="txn-th txn-th--amount">Abweichung</th>
                <th className="txn-th txn-th--amount">Finanzierungskosten</th>
                <th className="txn-th txn-th--amount">Warnungen</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.project_id} className="txn-tr">
                  <td className="txn-td">{row.project_name}</td>
                  <td className="txn-td">{row.scenario_name}</td>
                  <td className="txn-td txn-td--amount">
                    {formatMinorUnitsAsEuro(row.forecast_cost_minor)}
                  </td>
                  <td className="txn-td txn-td--amount">
                    {formatMinorUnitsAsEuro(row.actual_cost_minor)}
                  </td>
                  <td
                    className={`txn-td txn-td--amount ${
                      row.variance_minor >= 0
                        ? "transaction-amount--positive"
                        : "transaction-amount--negative"
                    }`}
                  >
                    {formatMinorUnitsAsEuro(row.variance_minor)}
                  </td>
                  <td className="txn-td txn-td--amount">
                    {formatMinorUnitsAsEuro(row.financing_cost_minor)}
                  </td>
                  <td className="txn-td txn-td--amount">
                    {row.warnings.length > 0 ? row.warnings.length : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="txn-tr explorer-total">
                <td className="txn-td">Summe</td>
                <td className="txn-td" />
                <td className="txn-td txn-td--amount">{formatMinorUnitsAsEuro(totals.forecast)}</td>
                <td className="txn-td txn-td--amount">{formatMinorUnitsAsEuro(totals.actual)}</td>
                <td className="txn-td txn-td--amount">
                  {formatMinorUnitsAsEuro(totals.forecast - totals.actual)}
                </td>
                <td className="txn-td txn-td--amount">
                  {formatMinorUnitsAsEuro(totals.financing)}
                </td>
                <td className="txn-td" />
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </>
  );
}
