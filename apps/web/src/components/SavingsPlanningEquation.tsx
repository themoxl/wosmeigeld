import { Link } from "react-router-dom";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type { BudgetSummary } from "../lib/types";

type EquationRow = {
  label: string;
  hint: string;
  value: number;
  operation: "+" | "−";
  reserve?: boolean;
};

export function SavingsPlanningEquation({ budget }: { budget: BudgetSummary }) {
  const isNegative = budget.planning_truly_free_minor < 0;
  const rows: EquationRow[] = [
    {
      label: "Erwartete Einnahmen",
      hint:
        budget.planning_income_basis === "contract"
          ? "Aus laufenden Einnahmeverträgen"
          : "Keine Einnahmeverträge – Basis ist das Monats-Ist",
      value: budget.planning_expected_income_minor,
      operation: "+",
    },
    {
      label: "Regelmäßige Fixkosten",
      hint: "Monatliche und wöchentliche Ausgabenverträge",
      value: budget.regular_fixed_costs_monthly_minor,
      operation: "−",
    },
    {
      label: "Fixkosten-Rücklagen",
      hint: `${budget.fixed_cost_reserve_count} geglättete Quartals-, Halbjahres- oder Jahreskosten`,
      value: budget.fixed_cost_reserves_monthly_minor,
      operation: "−",
      reserve: true,
    },
    {
      label: "Variable Monatsbudgets",
      hint:
        budget.variable_planned_budget_minor === 0
          ? "Noch kein separater variabler Planbetrag hinterlegt"
          : "Summe übriger Kategorie-Planbeträge; Verträge und Spartöpfe sind herausgerechnet",
      value: budget.variable_planned_budget_minor,
      operation: "−",
    },
    {
      label: "Spartöpfe & Wünsche",
      hint: `${budget.goal_reserve_count} im Monatsplan`,
      value: budget.goal_reserves_monthly_minor,
      operation: "−",
      reserve: true,
    },
  ];

  return (
    <section className="planning-equation" aria-label="Kalkulatorische Haushaltsplanung">
      <header className="planning-equation__header">
        <div>
          <span className="status-badge status-badge--highlight">Kalkulatorisch</span>
          <h2>Was nach allen Planungsschichten wirklich frei bleibt</h2>
        </div>
        <span className="planning-equation__count">
          Rücklagen gesamt {formatMinorUnitsAsEuro(budget.total_calculational_reserves_minor)}
        </span>
      </header>
      <div className="planning-equation__ledger">
        {rows.map((row) => (
          <div
            className={`planning-equation__row ${row.reserve ? "planning-equation__row--reserve" : ""}`}
            key={row.label}
          >
            <span className="planning-equation__sign" aria-hidden="true">
              {row.operation}
            </span>
            <span>
              <strong>{row.label}</strong>
              <small>{row.hint}</small>
            </span>
            <b>{formatMinorUnitsAsEuro(row.value)}</b>
          </div>
        ))}
        <div
          className={`planning-equation__total ${isNegative ? "planning-equation__total--gap" : ""}`}
        >
          <span>
            <strong>Wirklich frei</strong>
            <small>
              {isNegative
                ? "Die Planung hat noch eine monatliche Lücke"
                : budget.variable_planned_budget_minor === 0
                  ? "Vorläufig – variable Monatsbudgets sind noch 0"
                  : "Nach allen hinterlegten Planungsschichten"}
            </small>
          </span>
          <b>{formatMinorUnitsAsEuro(budget.planning_truly_free_minor)}</b>
        </div>
      </div>
      <p className="planning-equation__note">
        Rechnerische Annahme, kein Kontostand: Es entstehen keine Buchungen. Fixkosten-Rücklagen
        pflegst du bei den <Link to="/fixkosten">Verträgen</Link>.
      </p>
    </section>
  );
}
