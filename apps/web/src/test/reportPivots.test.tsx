import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { rowsQuery } from "../lib/analytics";
import type { AnalyticsResult } from "../lib/types";
import { ReportMonthlyTable } from "../pages/reports/ReportMonthlyTable";
import { CategoryMonthlyReport } from "../pages/reports/CategoryMonthlyReport";
import { ReportsOverview } from "../pages/reports/ReportsOverview";
import {
  compareDimensionResults,
  fairYearPeriods,
  pivotMonthlyResult,
} from "../pages/reports/reportPivots";
import {
  categoryMonthlyQuery,
  comparisonQuery,
  payeeMonthlyQuery,
} from "../pages/reports/reportQueries";

function result(rows: AnalyticsResult["rows"]): AnalyticsResult {
  return {
    grain: "aggregate",
    columns: [],
    rows,
    total_row: null,
    row_count: rows.length,
    truncated: false,
    applied_limits: [],
    next_cursor: null,
    query_id: "pivot-test",
    period_from: "2026-01-01",
    period_to: "2026-03-31",
    months_covered: 3,
  };
}

describe("report monthly pivots", () => {
  it("behält doppelte Kategorienamen per ID getrennt und füllt fehlende Monate chronologisch", () => {
    const pivot = pivotMonthlyResult(
      result([
        {
          category: "cat-a",
          category_label: "Lebensmittel",
          "date:month": "2026-01-01",
          sum_amount: -10000,
          count_amount: 1,
        },
        {
          category: "cat-a",
          category_label: "Lebensmittel",
          "date:month": "2026-03-01",
          sum_amount: -20000,
          count_amount: 2,
        },
        {
          category: "cat-b",
          category_label: "Lebensmittel",
          "date:month": "2026-02-01",
          sum_amount: -5000,
          count_amount: 1,
        },
      ]),
      "category",
      undefined,
      new Date(2026, 7, 4),
    );

    expect(pivot.months.map((month) => month.key)).toEqual(["2026-01", "2026-02", "2026-03"]);
    expect(pivot.rows).toHaveLength(2);
    expect(pivot.rows.map((row) => row.identityKey)).toEqual(["cat-a", "cat-b"]);
    expect(pivot.rows[0]?.values["2026-02"]).toBeNull();
    expect(pivot.rows[0]).toMatchObject({ sum: -30000, average: -10000, count: 3 });
    expect(pivot.rows[0]?.share).toBeCloseTo(30000 / 35000);
    expect(pivot.monthTotals).toEqual({
      "2026-01": -10000,
      "2026-02": -5000,
      "2026-03": -20000,
    });
  });

  it("zeigt exakte Monatswerte und leere Werte als Mittelpunkt statt als Nullbetrag", () => {
    const pivot = pivotMonthlyResult(
      result([
        {
          payee: "payee-a",
          payee_label: "Edeka",
          "date:month": "2026-01-01",
          sum_amount: -12345,
          count_amount: 2,
        },
      ]),
      "payee",
      undefined,
      new Date(2026, 7, 4),
    );
    render(
      <ReportMonthlyTable pivot={pivot} labelHeader="Empfänger" ariaLabel="Test-Monatsbuch" />,
    );
    const table = screen.getByRole("table", { name: "Test-Monatsbuch" });
    expect(within(table).getByText("Jan. 2026")).toBeInTheDocument();
    expect(within(table).getByText("Feb. 2026")).toBeInTheDocument();
    expect(within(table).getAllByText("-123,45 €").length).toBeGreaterThan(0);
    expect(within(table).getAllByText("·").length).toBeGreaterThanOrEqual(2);
    expect(table).toHaveClass("report-monthly-table");
    const shell = table.closest(".report-table-shell");
    expect(shell?.firstElementChild).toHaveClass("report-table-scroll-hint");
  });

  it("hält gebuchte Nullsummen anhand der Anzahl sichtbar und drilldown-fähig", async () => {
    const pivot = pivotMonthlyResult(
      result([
        {
          payee: "payee-a",
          payee_label: "Edeka",
          "date:month": "2026-01-01",
          sum_amount: 0,
          count_amount: 2,
        },
      ]),
      "payee",
      undefined,
      new Date(2026, 7, 4),
    );
    const onCellClick = vi.fn();
    render(
      <ReportMonthlyTable
        pivot={pivot}
        labelHeader="Empfänger"
        ariaLabel="Nullsummen"
        onCellClick={onCellClick}
      />,
    );
    const cell = screen.getByRole("button", {
      name: /Edeka, Jan. 2026: 0,00.*anzeigen/,
    });
    expect(cell).toHaveTextContent("0,00 €");
    cell.focus();
    await userEvent.keyboard("{Enter}");
    expect(onCellClick).toHaveBeenCalledOnce();
  });

  it("macht auch einen Cashflow-Saldo von null bei vorhandenen Buchungen nachvollziehbar", async () => {
    const onOpen = vi.fn();
    render(
      <ReportsOverview
        trend={{
          months: [
            {
              year: 2026,
              month: 1,
              income_minor: 10000,
              expense_minor: 10000,
              net_minor: 0,
            },
          ],
        }}
        trendLoading={false}
        trendError={false}
        groupsLoading={false}
        groupsError={false}
        onOpen={onOpen}
        rowsQuery={rowsQuery}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Saldo, Jan. 2026: 0,00.*anzeigen/ }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it("kennzeichnet den aktuellen Antwortmonat ausdrücklich als laufend", () => {
    const current = {
      ...result([
        {
          category: "cat-a",
          category_label: "Lebensmittel",
          "date:month": "2026-08-01",
          sum_amount: -1000,
          count_amount: 1,
        },
      ]),
      period_from: "2026-08-01",
      period_to: "2026-08-04",
      months_covered: 1,
    };
    const pivot = pivotMonthlyResult(current, "category", undefined, new Date(2026, 7, 4));
    render(
      <ReportMonthlyTable pivot={pivot} labelHeader="Kategorie" ariaLabel="Laufender Monat" />,
    );
    expect(screen.getByText("laufend")).toBeInTheDocument();
  });
});

describe("report states", () => {
  it("zeigt Laden, Fehler und leere Antwort explizit", () => {
    const onOpen = () => undefined;
    const loading = render(
      <CategoryMonthlyReport isLoading isError={false} months={12} onOpen={onOpen} />,
    );
    expect(screen.getByText("Kategorien werden geladen…")).toBeInTheDocument();
    loading.unmount();

    const error = render(
      <CategoryMonthlyReport isLoading={false} isError months={12} onOpen={onOpen} />,
    );
    expect(
      screen.getByText("Der Kategorienbericht konnte nicht geladen werden."),
    ).toBeInTheDocument();
    error.unmount();

    render(
      <CategoryMonthlyReport
        result={result([])}
        isLoading={false}
        isError={false}
        months={12}
        onOpen={onOpen}
      />,
    );
    expect(
      screen.getByText("Keine kategorisierten Ausgaben im gewählten Zeitraum."),
    ).toBeInTheDocument();
  });
});

describe("fair year comparison", () => {
  it("vergleicht im laufenden Jahr nur identische vollständige Monate", () => {
    expect(fairYearPeriods(2026, new Date(2026, 7, 4))).toMatchObject({
      monthCount: 7,
      selectedFrom: "2026-01-01",
      selectedTo: "2026-07-31",
      previousFrom: "2025-01-01",
      previousTo: "2025-07-31",
    });
    expect(fairYearPeriods(2024, new Date(2026, 7, 4))).toMatchObject({
      monthCount: 12,
      selectedTo: "2024-12-31",
      previousTo: "2023-12-31",
    });
  });

  it("erhält Vorzeichen und berechnet Euro- sowie Prozentdifferenzen ohne abs-Maskierung", () => {
    const selected = result([
      { category: "cat-a", category_label: "Wohnen", sum_amount: -12000 },
      { category: "cat-b", category_label: "Erstattung", sum_amount: 2000 },
    ]);
    const previous = result([
      { category: "cat-a", category_label: "Wohnen", sum_amount: -10000 },
      { category: "cat-b", category_label: "Erstattung", sum_amount: 1000 },
    ]);
    const entries = compareDimensionResults(selected, previous, "category");
    expect(entries.find((entry) => entry.id === "cat-a")).toMatchObject({
      selected: -12000,
      previous: -10000,
      delta: -2000,
      deltaPercent: -20,
    });
    expect(entries.find((entry) => entry.id === "cat-b")).toMatchObject({
      selected: 2000,
      previous: 1000,
      delta: 1000,
      deltaPercent: 100,
    });
  });
});

describe("report queries", () => {
  it("fragt vollständige Monatsmatrizen ohne stilles Top-N ab", () => {
    const categories = categoryMonthlyQuery(24);
    const payees = payeeMonthlyQuery(24);
    expect(categories.top_n).toBeUndefined();
    expect(payees.top_n).toBeUndefined();
    expect(categories.group_by).toEqual([
      { field: "category_group" },
      { field: "category" },
      { field: "date", granularity: "month" },
    ]);
    expect(payees.group_by).toEqual([{ field: "payee" }, { field: "date", granularity: "month" }]);
  });

  it("setzt faire Vergleichszeiträume und erhält Null-Kategorien im Drilldown", () => {
    expect(comparisonQuery("2026-01-01", "2026-07-31", "category").period).toEqual({
      kind: "custom",
      from_date: "2026-01-01",
      to_date: "2026-07-31",
    });
    const drilldown = rowsQuery({
      field: "category",
      value: null,
      from_date: "2026-06-01",
      to_date: "2026-06-30",
      direction: "expense",
    });
    expect(drilldown.period).toMatchObject({ from_date: "2026-06-01", to_date: "2026-06-30" });
    expect(JSON.stringify(drilldown.where)).toContain('"op":"is_null"');
  });

  it("filtert Cashflow-Drilldowns nach fachlicher Zuordnung statt nach Vorzeichen", () => {
    const income = rowsQuery({
      from_date: "2026-06-01",
      to_date: "2026-06-30",
      cashflowRole: "income",
    });
    expect(income.where).toEqual({
      condition: { field: "cashflow_role", op: "eq", value: "income" },
    });

    const net = rowsQuery({
      from_date: "2026-06-01",
      to_date: "2026-06-30",
      cashflowRole: "net",
    });
    expect(net.where).toEqual({
      condition: {
        field: "cashflow_role",
        op: "in",
        value: ["income", "expense"],
      },
    });
  });
});
