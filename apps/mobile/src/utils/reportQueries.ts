// Die Abfragen hinter den Berichtsregistern (docs/ANALYSE-EXPLORER-PLAN.md,
// Baustein E). Wortgleiche Portierung von
// apps/web/src/pages/reports/reportQueries.ts - absichtlich, denn beide
// Oberflaechen sollen auf dieselbe Frage dieselbe Summe zeigen. Weicht eine
// Seite hier ab (Transfers eingeschlossen, Nicht-Budgetkonten, anderes
// Kuerungsgrain), sieht das fuer den Nutzer nach einem Datenfehler aus.
//
// Reine Datenkonstruktion ohne Renderer: so ist die Abfrageform ohne
// laufenden Screen pruefbar.
import type { AnalyticsFilterNode, AnalyticsQuery } from "../api/types";

function expenseFilter(extra?: AnalyticsFilterNode): AnalyticsFilterNode {
  const direction: AnalyticsFilterNode = {
    condition: { field: "direction", op: "eq", value: "expense" },
  };
  return extra ? { all_of: [direction, extra] } : direction;
}

/**
 * Gemeinsamer Zuschnitt aller Berichte: gebuchte Betraege ohne Transfers, nur
 * Budgetkonten, archivierte Kategorien eingeschlossen (sonst faellt die
 * Vergangenheit still aus der Reihe, sobald eine Kategorie archiviert wird).
 */
export function reportBaseQuery(months: number): AnalyticsQuery {
  return {
    grain: "aggregate",
    group_by: [],
    measures: [],
    period: { kind: "last_n_months", n: months },
    include_transfers: false,
    include_archived_categories: true,
    only_on_budget: true,
  };
}

/** Register "Überblick": Ausgaben je Kategoriegruppe und Monat. */
export function groupMonthlyQuery(months: number): AnalyticsQuery {
  return {
    ...reportBaseQuery(months),
    group_by: [{ field: "category_group" }, { field: "date", granularity: "month" }],
    measures: [
      { field: "amount", aggregate: "sum" },
      { field: "amount", aggregate: "count" },
    ],
    where: expenseFilter(),
  };
}

/** Register "Kategorien". */
export function categoryMonthlyQuery(months: number): AnalyticsQuery {
  return {
    ...reportBaseQuery(months),
    group_by: [
      { field: "category_group" },
      { field: "category" },
      { field: "date", granularity: "month" },
    ],
    measures: [
      { field: "amount", aggregate: "sum" },
      { field: "amount", aggregate: "count" },
    ],
    where: expenseFilter(),
  };
}

/** Register "Empfänger". */
export function payeeMonthlyQuery(months: number): AnalyticsQuery {
  return {
    ...reportBaseQuery(months),
    group_by: [{ field: "payee" }, { field: "date", granularity: "month" }],
    measures: [
      { field: "amount", aggregate: "sum" },
      { field: "amount", aggregate: "count" },
    ],
    where: expenseFilter(),
  };
}

/**
 * Register "Jahresvergleich": ein Zeitraum, nach Kategorie gruppiert. Der
 * Aufrufer stellt zwei solche Abfragen (gewaehltes Jahr und Vorjahr) mit
 * gleich langen Zeitraeumen - siehe fairYearPeriods().
 */
export function yearComparisonQuery(from: string, to: string): AnalyticsQuery {
  return {
    ...reportBaseQuery(12),
    period: { kind: "custom", from_date: from, to_date: to },
    group_by: [{ field: "category" }],
    measures: [
      { field: "amount", aggregate: "sum" },
      { field: "amount", aggregate: "count" },
    ],
    where: expenseFilter(),
  };
}

/** Obergrenze der Buchungsliste hinter einer Zahl. */
export const DRILLDOWN_ROW_LIMIT = 200;

export interface DrilldownSpec {
  /** Dimensionsfeld des angetippten Segments, z. B. "category_group". */
  field?: string | null;
  /** Wert der Dimension; `null` steht fuer "ohne Zuordnung". */
  value?: string | null;
  from_date: string;
  to_date: string;
  direction?: "expense" | "income" | null;
  /** Fachliche Cashflow-Zuordnung; anders als `direction` nicht vom Vorzeichen abgeleitet. */
  cashflowRole?: "expense" | "income" | "net" | null;
}

/**
 * Die Buchungen hinter einer Zahl (Portierung von rowsQuery() aus
 * apps/web/src/lib/analytics.ts).
 *
 * Der Zuschnitt muss dem der Summe entsprechen - dieselbe Transfer- und
 * Budgetkontenregel -, sonst zeigt die Detailliste Buchungen, die in der Zahl
 * darueber gar nicht stecken. `limit: 200` ist die Obergrenze der Liste; wird
 * sie erreicht, sagt der Screen das ausdruecklich, statt stillschweigend
 * abzuschneiden.
 */
export function drilldownRowsQuery(spec: DrilldownSpec): AnalyticsQuery {
  const conditions: AnalyticsFilterNode[] = [];

  if (spec.field) {
    conditions.push(
      spec.value === null || spec.value === undefined
        ? { condition: { field: spec.field, op: "is_null", value: null } }
        : { condition: { field: spec.field, op: "eq", value: spec.value } },
    );
  }
  if (spec.direction) {
    conditions.push({ condition: { field: "direction", op: "eq", value: spec.direction } });
  }
  if (spec.cashflowRole) {
    conditions.push({
      condition: {
        field: "cashflow_role",
        op: spec.cashflowRole === "net" ? "in" : "eq",
        value: spec.cashflowRole === "net" ? ["income", "expense"] : spec.cashflowRole,
      },
    });
  }

  return {
    grain: "rows",
    group_by: [],
    measures: [],
    fields: [],
    period: { kind: "custom", from_date: spec.from_date, to_date: spec.to_date },
    order_by: [],
    limit: DRILLDOWN_ROW_LIMIT,
    include_transfers: false,
    include_archived_categories: true,
    only_on_budget: true,
    where:
      conditions.length === 0
        ? null
        : conditions.length === 1
          ? conditions[0]
          : { all_of: conditions },
  };
}
