import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BucketSplit } from "../components/budget/BucketSplit";
import type { BudgetSummary } from "../lib/types";

// Die Komponente liest nur ein Dutzend Felder der Zusammenfassung; ein
// vollstaendiges BudgetSummary-Literal (ueber 60 Felder) wuerde den Test nur
// verrauschen, deshalb der Cast.
const base = {
  income_basis_minor: 300000,
  fixed_costs_income_basis: "actual",
  bucket_needs_planned_minor: 160000,
  bucket_wants_planned_minor: 60000,
  bucket_savings_planned_minor: 35000,
  bucket_unassigned_planned_minor: 0,
  bucket_needs_minor: 80000,
  bucket_wants_minor: 40000,
  bucket_savings_minor: 0,
  bucket_unassigned_spent_minor: 0,
  bucket_needs_percent: 66.7,
  bucket_wants_percent: 33.3,
  bucket_savings_percent: 0,
} as unknown as BudgetSummary;

describe("BucketSplit", () => {
  it("stellt Ziel, Plan und Ist nebeneinander und nennt die Bezugsgroesse", () => {
    render(<BucketSplit budget={base} />);

    expect(screen.getByTestId("budget-503020-distribution")).toBeInTheDocument();
    expect(screen.getByText(/Ziel bezogen auf/)).toHaveTextContent("3.000,00");
    // 50 % von 3.000,00 EUR
    expect(screen.getByText("1.500,00 €")).toBeInTheDocument();
    // Plan 1.600,00 gegen Ziel 1.500,00
    expect(screen.getByText("+100,00 €")).toBeInTheDocument();
    // Ist 0,00 gegen Sparplan 350,00 - genau die Frage des Nutzers
    expect(screen.getByText("−350,00 €")).toBeInTheDocument();
    expect(screen.getByText(/Bei Sparen stehen/)).toHaveTextContent("350,00");
    expect(document.body.textContent).not.toContain("NaN");
  });

  it("laesst die Zielspalte weg, wenn es keine Bezugsgroesse gibt", () => {
    render(<BucketSplit budget={{ ...base, income_basis_minor: 0 } as BudgetSummary} />);

    expect(screen.getByText(/kein 50\/30\/20-Ziel berechnen/)).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Ziel" })).toBeNull();
    expect(document.body.textContent).not.toContain("NaN");
  });

  it("meldet den angeklickten Topf", async () => {
    const onShowCategories = vi.fn();
    render(
      <BucketSplit
        budget={
          {
            ...base,
            bucket_unassigned_planned_minor: 5000,
            bucket_unassigned_spent_minor: 2500,
          } as BudgetSummary
        }
        onShowCategories={onShowCategories}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /Ohne Bucket/ }));

    expect(onShowCategories).toHaveBeenCalledWith("unassigned");
  });

  it("bleibt bei fehlenden Bucket-Feldern ohne NaN lesbar", () => {
    render(<BucketSplit budget={{ fixed_costs_income_basis: "contract" } as BudgetSummary} />);

    expect(document.body.textContent).not.toContain("NaN");
    expect(screen.getByTestId("budget-503020-distribution")).toBeInTheDocument();
  });
});
