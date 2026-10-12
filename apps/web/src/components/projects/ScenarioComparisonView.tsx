import { useState } from "react";
import { useScenarioComparison } from "../../hooks/useProjects";
import { formatMinorUnitsAsEuro } from "../../lib/money";
import type { ProjectScenario } from "../../lib/types";

interface ScenarioComparisonViewProps {
  projectId: string;
  scenarios: ProjectScenario[];
}

const METRIC_ROWS: {
  key:
    | "estimated_cost_minor"
    | "forecast_cash_cost_minor"
    | "contingency_minor"
    | "project_cash_cost_minor"
    | "project_economic_cost_minor";
  label: string;
}[] = [
  { key: "estimated_cost_minor", label: "Schätzung" },
  { key: "forecast_cash_cost_minor", label: "Forecast (Cash)" },
  { key: "contingency_minor", label: "Kostenpuffer" },
  { key: "project_cash_cost_minor", label: "Projektkosten (Cash)" },
  { key: "project_economic_cost_minor", label: "Ökonomische Kosten" },
];

export function ScenarioComparisonView({ projectId, scenarios }: ScenarioComparisonViewProps) {
  const [selected, setSelected] = useState<string[]>(() => scenarios.slice(0, 2).map((s) => s.id));

  const { data: comparison, isLoading } = useScenarioComparison(projectId, selected);

  const toggle = (scenarioId: string) => {
    setSelected((current) =>
      current.includes(scenarioId)
        ? current.filter((id) => id !== scenarioId)
        : current.length < 4
          ? [...current, scenarioId]
          : current,
    );
  };

  return (
    <div style={{ marginTop: "var(--space-4)" }}>
      <div className="card" style={{ marginBottom: "var(--space-4)" }}>
        <h2 className="card-heading">Szenarien auswählen (2–4)</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
          {scenarios.map((scenario) => (
            <label
              key={scenario.id}
              style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}
            >
              <input
                type="checkbox"
                checked={selected.includes(scenario.id)}
                onChange={() => toggle(scenario.id)}
              />
              {scenario.name}
            </label>
          ))}
        </div>
      </div>

      {selected.length < 2 && <p className="placeholder-note">Mindestens zwei Szenarien wählen.</p>}
      {isLoading && <p className="placeholder-note">Lade Vergleich...</p>}

      {comparison && (
        <div className="txn-table-wrapper">
          <table className="txn-table">
            <thead>
              <tr>
                <th className="txn-th">Kennzahl</th>
                {comparison.rows.map((row) => (
                  <th key={row.scenario_id} className="txn-th txn-th--amount-wide">
                    {row.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {METRIC_ROWS.map((metric) => (
                <tr key={metric.key} className="txn-tr">
                  <td className="txn-td">{metric.label}</td>
                  {comparison.rows.map((row) => (
                    <td key={row.scenario_id} className="txn-td txn-td--amount-wide">
                      {formatMinorUnitsAsEuro(row[metric.key])}
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="txn-tr">
                <td className="txn-td">Warnungen</td>
                {comparison.rows.map((row) => (
                  <td key={row.scenario_id} className="txn-td">
                    {row.warnings.length > 0 ? row.warnings.join(", ") : "—"}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
