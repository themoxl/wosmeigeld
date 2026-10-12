import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AnnualMatrixTable,
  formatCompactMinorUnits,
  hasAnnualActivity,
} from "../components/budget/AnnualMatrixTable";
import type { AnnualMatrixResponse, AnnualMatrixRow } from "../lib/types";

// Die Matrix zeigt ein abgeschlossenes Jahr, damit weder "laufender Monat"
// noch "Zukunft" vom Testzeitpunkt abhaengen.
const YEAR = 2024;

function months(
  values: Partial<Record<number, { spent?: number; income?: number; plan?: number }>>,
) {
  return Array.from({ length: 12 }, (_, index) => {
    const entry = values[index + 1] ?? {};
    return {
      month: index + 1,
      planned_minor: entry.plan ?? 0,
      spent_minor: entry.spent ?? 0,
      income_actual_minor: entry.income ?? 0,
    };
  });
}

function row(overrides: Partial<AnnualMatrixRow> & Pick<AnnualMatrixRow, "category_id">) {
  const base: AnnualMatrixRow = {
    category_id: overrides.category_id,
    category_name: "Kategorie",
    group_id: "grp-expense",
    group_name: "Fixkosten",
    group_sort_order: 1,
    category_sort_order: 1,
    budget_bucket: "needs",
    default_type: "expense",
    is_archived: false,
    months: months({}),
    total_planned_minor: 0,
    total_spent_minor: 0,
    total_income_actual_minor: 0,
  };
  return { ...base, ...overrides };
}

const miete = row({
  category_id: "cat-miete",
  category_name: "Miete",
  months: months({ 1: { spent: 115000, plan: 100000 }, 2: { spent: 21526, plan: 100000 } }),
  total_planned_minor: 200000,
  total_spent_minor: 136526,
});

const gehalt = row({
  category_id: "cat-gehalt",
  category_name: "Gehalt",
  group_id: "grp-income",
  group_name: "Einnahmen",
  group_sort_order: 0,
  default_type: "income",
  budget_bucket: null,
  months: months({ 1: { income: 300000, plan: 289900 } }),
  total_planned_minor: 289900,
  total_income_actual_minor: 300000,
});

const schlafend = row({
  category_id: "cat-leer",
  category_name: "Nie genutzt",
  category_sort_order: 2,
});

const matrix: AnnualMatrixResponse = { year: YEAR, rows: [gehalt, miete, schlafend] };

function renderMatrix(showEmpty = false, onDrilldown = vi.fn()) {
  render(
    <AnnualMatrixTable
      matrix={matrix}
      year={YEAR}
      showEmpty={showEmpty}
      onDrilldown={onDrilldown}
    />,
  );
  return onDrilldown;
}

describe("formatCompactMinorUnits", () => {
  it("kuerzt auf ganze Euro mit Tausenderpunkt", () => {
    expect(formatCompactMinorUnits(115000)).toBe("1.150");
    expect(formatCompactMinorUnits(21526)).toBe("215");
    expect(formatCompactMinorUnits(0)).toBe("0");
  });

  it("setzt im Abweichungsmodus immer ein Vorzeichen", () => {
    expect(formatCompactMinorUnits(15000, true)).toBe("+150");
    expect(formatCompactMinorUnits(-15000, true)).toBe("-150");
    expect(formatCompactMinorUnits(0, true)).toBe("±0");
  });

  it("verraet Restbetraege unter einem Euro, statt sie auf 0 zu runden", () => {
    expect(formatCompactMinorUnits(30)).toBe("<1");
    expect(formatCompactMinorUnits(-30, true)).toBe("-<1");
  });
});

describe("hasAnnualActivity", () => {
  it("erkennt Zeilen ohne Plan, Ausgabe und Einnahme", () => {
    expect(hasAnnualActivity(schlafend)).toBe(false);
    expect(hasAnnualActivity(miete)).toBe(true);
    expect(hasAnnualActivity(gehalt)).toBe(true);
  });
});

describe("AnnualMatrixTable", () => {
  it("buendelt die Zeilen nach Gruppe und filtert Kategorien ohne Bewegung", () => {
    renderMatrix();
    expect(screen.getByRole("button", { name: /Einnahmen/ })).toBeInTheDocument();
    expect(screen.getByText("Miete")).toBeInTheDocument();
    expect(screen.queryByText("Nie genutzt")).not.toBeInTheDocument();
  });

  it("zeigt Kategorien ohne Bewegung, sobald showEmpty gesetzt ist", () => {
    renderMatrix(true);
    expect(screen.getByText("Nie genutzt")).toBeInTheDocument();
  });

  it("liest bei Einnahmen income_actual_minor statt spent_minor", () => {
    renderMatrix();
    expect(screen.getByTestId("budget-matrix-cell-cat-gehalt-1")).toHaveTextContent("3.000");
    // Das Gehalt gehoert in die Einnahmenzeile, nicht in die Ausgabenzeile.
    const income = screen.getByTestId("budget-matrix-footer-income");
    expect(within(income).getAllByTitle("3.000,00 €").length).toBeGreaterThan(0);
    const expense = screen.getByTestId("budget-matrix-footer-expense");
    expect(within(expense).getAllByTitle("1.365,26 €").length).toBeGreaterThan(0);
  });

  it("stellt die Fusszeile als Einnahmen minus Ausgaben auf", () => {
    renderMatrix();
    // Januar: 3.000,00 Einnahme - 1.150,00 Ausgabe = 1.850,00 Saldo.
    const balance = screen.getByTestId("budget-matrix-footer-balance");
    expect(within(balance).getAllByTitle("1.850,00 €").length).toBeGreaterThan(0);
    // Jahressaldo: 3.000,00 - 1.365,26 = 1.634,74.
    expect(within(balance).getAllByTitle("1.634,74 €").length).toBeGreaterThan(0);
  });

  it("meldet Kategorie, Name und Monat an den Drilldown", async () => {
    const user = userEvent.setup();
    const onDrilldown = renderMatrix();
    await user.click(screen.getByRole("button", { name: /Miete im Januar anzeigen/ }));
    expect(onDrilldown).toHaveBeenCalledWith({
      categoryId: "cat-miete",
      categoryName: "Miete",
      month: 1,
    });
  });

  it("macht Zellen ohne gebuchtes Ist nicht fokussierbar", () => {
    renderMatrix();
    const empty = screen.getByTestId("budget-matrix-cell-cat-miete-6");
    expect(within(empty).queryByRole("button")).not.toBeInTheDocument();
    expect(empty).toHaveTextContent("–");
  });

  it("rechnet im Abweichungsmodus Ist minus Plan mit Vorzeichen", async () => {
    const user = userEvent.setup();
    renderMatrix();
    await user.click(screen.getByTestId("budget-matrix-mode-deviation"));
    // Miete Januar: 1.150,00 Ist - 1.000,00 Plan = +150,00 (ueber Plan).
    expect(screen.getByTestId("budget-matrix-cell-cat-miete-1")).toHaveTextContent("+150");
    // Februar: 215,26 - 1.000,00 = -784,74 (unter Plan).
    expect(screen.getByTestId("budget-matrix-cell-cat-miete-2")).toHaveTextContent("-785");
  });

  it("klappt eine Gruppe ein, ohne die Fusszeile zu veraendern", async () => {
    const user = userEvent.setup();
    renderMatrix();
    const balanceBefore = screen.getByTestId("budget-matrix-footer-balance").textContent;

    const toggle = screen.getByRole("button", { name: /Fixkosten/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);

    expect(screen.queryByText("Miete")).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("budget-matrix-footer-balance").textContent).toBe(balanceBefore);
  });
});
