import { Chart } from "../charts/Chart";
import { forecastMultiCompareOption } from "../charts/buildOptions";
import { useScenarioComparison } from "../../hooks/useScenarios";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import { monthNameShort } from "../../lib/dates";
import type { ScenarioComparisonEntry } from "../../lib/types";

// Vergleich mehrerer gespeicherter Szenarien (Plan Stufe 7).
//
// Alle Linien teilen sich den Haushaltshorizont statt ihres eigenen: vier
// Verlaeufe in einem Diagramm brauchen eine gemeinsame Zeitachse, sonst
// vergleicht man Aepfel mit Birnen. Das entscheidet der Server.

const eur = formatMinorUnitsAsEuro;
const percent = (value: number) => `${value.toFixed(1).replace(".", ",")} %`;

function verdictOf(entry: ScenarioComparisonEntry, baseHasShortfall: boolean): string {
  const simNegative = entry.simulation.metrics.first_negative_on;
  if (baseHasShortfall && !simNegative) return "Engpass gelöst";
  if (!baseHasShortfall && simNegative) return "erzeugt Engpass";
  if (simNegative) {
    const shift = entry.comparison.first_negative_shift_days;
    return shift ? `Engpass +${shift} Tage` : "Engpass bleibt";
  }
  return "kein Engpass";
}

export function ScenarioComparison({
  scenarioIds,
  includeSavings,
}: {
  scenarioIds: string[];
  includeSavings: boolean;
}) {
  const query = useScenarioComparison(scenarioIds, includeSavings);

  if (scenarioIds.length === 0) return null;

  if (query.isLoading) {
    return (
      <div className="card">
        <p className="placeholder-note" role="status">
          Vergleich wird gerechnet…
        </p>
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="card status-error" role="alert">
        Vergleich konnte nicht gerechnet werden.
      </div>
    );
  }

  const { base, entries } = query.data;
  const baseHasShortfall = Boolean(base.metrics.first_negative_on);
  const labels = base.monthly.map(
    (m) => `${monthNameShort(m.month - 1)} ${String(m.year).slice(2)}`,
  );
  const series = [
    { name: "Basis", values: base.monthly.map((m) => m.closing_balance_minor), dashed: true },
    ...entries.map((entry) => ({
      name: entry.scenario.name,
      values: entry.simulation.monthly.map((m) => m.closing_balance_minor),
      dashed: false,
    })),
  ];

  return (
    <div className="card" data-testid="scenario-comparison">
      <h2 className="card-heading">Vergleich</h2>
      <Chart
        option={forecastMultiCompareOption(labels, series, base.safety_buffer_minor)}
        height={280}
        description={`Kontostand von Basis und ${entries.length} Szenarien im Vergleich`}
        tableFallback={
          <div className="txn-table-wrapper">
            <table className="txn-table" data-testid="scenario-comparison-series">
              <thead>
                <tr>
                  <th className="txn-th">Monat</th>
                  {series.map((s) => (
                    <th key={s.name} className="txn-th txn-th--amount">
                      {s.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {labels.map((label, index) => (
                  <tr key={label}>
                    <td className="txn-td">{label}</td>
                    {series.map((s) => (
                      <td key={s.name} className="txn-td txn-td--amount">
                        {eur(s.values[index] ?? 0)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        }
      />

      <div className="txn-table-wrapper" style={{ marginTop: "var(--space-4)" }}>
        <table className="txn-table" data-testid="scenario-comparison-matrix">
          <thead>
            <tr>
              <th className="txn-th">Szenario</th>
              <th className="txn-th txn-th--amount">Überschuss</th>
              <th className="txn-th txn-th--amount">Sparquote</th>
              <th className="txn-th txn-th--amount">Fixkostenquote</th>
              <th className="txn-th txn-th--amount">Nach 24 Mon.</th>
              <th className="txn-th">Engpass</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="txn-td">
                <strong>Basis</strong>
              </td>
              <td className="txn-td txn-td--amount">
                {eur(base.metrics.average_monthly_surplus_minor)}
              </td>
              <td className="txn-td txn-td--amount">{percent(base.metrics.savings_rate_percent)}</td>
              <td className="txn-td txn-td--amount">
                {percent(base.metrics.fixed_cost_ratio_percent)}
              </td>
              <td className="txn-td txn-td--amount">
                {eur(base.metrics.balance_in_24_months_minor)}
              </td>
              <td className="txn-td">
                {baseHasShortfall ? "▲ vorhanden" : "keiner"}
              </td>
            </tr>
            {entries.map((entry) => {
              const m = entry.simulation.metrics;
              const solved = baseHasShortfall && !m.first_negative_on;
              return (
                <tr key={entry.scenario.id}>
                  <td className="txn-td">{entry.scenario.name}</td>
                  <td className="txn-td txn-td--amount transaction-amount--positive">
                    {eur(m.average_monthly_surplus_minor)}
                  </td>
                  <td className="txn-td txn-td--amount">{percent(m.savings_rate_percent)}</td>
                  <td className="txn-td txn-td--amount">{percent(m.fixed_cost_ratio_percent)}</td>
                  <td className="txn-td txn-td--amount">
                    {eur(m.balance_in_24_months_minor)}
                  </td>
                  <td
                    className={`txn-td ${solved ? "transaction-amount--positive" : m.first_negative_on ? "transaction-amount--negative" : ""}`}
                  >
                    {verdictOf(entry, baseHasShortfall)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="card-note" style={{ marginTop: "var(--space-3)" }}>
        Ein höherer Kontostand allein löst kein Liquiditätsrisiko — die letzte Spalte sagt, ob der
        Engpass wirklich verschwindet.
      </p>
    </div>
  );
}
