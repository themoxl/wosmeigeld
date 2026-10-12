import type { components } from "@finanzplaner/contracts";
import { apiFetch } from "../../lib/api";
import type { AnalyticsFilterNode, AnalyticsQuery } from "../../lib/types";

export type VariableCostsResponse = components["schemas"]["VariableCostsResponse"];

/**
 * Aufschluesselung der variablen Kosten (1-36 Monate, Standard 12).
 *
 * Der Server betrachtet die letzten `months` VOLLEN Monate und grenzt
 * "variabel" genauso ab wie die Liquiditaetsvorschau (ohne die Kategorien, die
 * ein Vertrag deckt). Deshalb wird hier nichts nachgerechnet - eine zweite
 * Rechnung im Browser erzeugte genau den Widerspruch, den der Bericht
 * aufloesen soll.
 */
export function getVariableCosts(
  token: string,
  householdId: string,
  months: number,
): Promise<VariableCostsResponse> {
  return apiFetch<VariableCostsResponse>(`/v1/reports/variable-costs?months=${months}`, {
    token,
    householdId,
  });
}

function expenseFilter(extra?: AnalyticsFilterNode): AnalyticsFilterNode {
  const direction: AnalyticsFilterNode = {
    condition: { field: "direction", op: "eq", value: "expense" },
  };
  return extra ? { all_of: [direction, extra] } : direction;
}

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

export function payeeMonthlyQuery(months: number, categoryId?: string | null): AnalyticsQuery {
  const categoryFilter =
    categoryId === undefined
      ? undefined
      : categoryId === null
        ? ({ condition: { field: "category", op: "is_null", value: null } } as AnalyticsFilterNode)
        : ({
            condition: { field: "category", op: "eq", value: categoryId },
          } as AnalyticsFilterNode);
  return {
    ...reportBaseQuery(months),
    group_by: [{ field: "payee" }, { field: "date", granularity: "month" }],
    measures: [
      { field: "amount", aggregate: "sum" },
      { field: "amount", aggregate: "count" },
    ],
    where: expenseFilter(categoryFilter),
  };
}

export function comparisonQuery(
  from: string,
  to: string,
  dimension: "category" | "date:month",
): AnalyticsQuery {
  return {
    ...reportBaseQuery(12),
    period: { kind: "custom", from_date: from, to_date: to },
    group_by:
      dimension === "category"
        ? [{ field: "category" }]
        : [{ field: "date", granularity: "month" }],
    measures: [
      { field: "amount", aggregate: "sum" },
      { field: "amount", aggregate: "count" },
    ],
    where: expenseFilter(),
  };
}
