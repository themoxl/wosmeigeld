import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { BudgetFlow } from "../components/budget/BudgetFlow";
import type { BudgetSummary } from "../lib/types";

const budget = {
  budget_id: "b1",
  year: 2026,
  month: 8,
  status: "open",
  method: "tracking",
  closed_at: null,
  total_income_minor: 300000,
  total_planned_minor: 250000,
  total_rollover_minor: 5000,
  total_spent_minor: 120000,
  total_remaining_minor: 135000,
  total_planned_income_minor: 689900,
  total_rollover_income_minor: 0,
  total_income_remaining_minor: 389900,
  uncategorized_spent_minor: 4210,
  savings_rate_percent: 60,
  bucket_needs_minor: 80000,
  bucket_wants_minor: 40000,
  bucket_savings_minor: 0,
  bucket_unassigned_spent_minor: 0,
  bucket_needs_planned_minor: 0,
  bucket_wants_planned_minor: 0,
  bucket_savings_planned_minor: 0,
  bucket_unassigned_planned_minor: 0,
  bucket_needs_percent: 50,
  bucket_wants_percent: 30,
  bucket_savings_percent: 20,
  surplus_before_goal_reserves_minor: 150000,
  goal_reserves_monthly_minor: 45000,
  free_after_goal_reserves_minor: 105000,
  goal_reserve_count: 2,
  planning_expected_income_minor: 300000,
  planning_income_basis: "contract",
  regular_fixed_costs_monthly_minor: 100000,
  fixed_cost_reserves_monthly_minor: 20000,
  fixed_cost_reserve_count: 1,
  fixed_cost_reserve_items: [],
  variable_planned_budget_minor: 30000,
  variable_planned_excluded_contract_minor: 100000,
  variable_planned_excluded_goal_savings_minor: 0,
  total_calculational_reserves_minor: 65000,
  planning_truly_free_minor: 105000,
  fixed_costs_monthly_minor: 175000,
  fixed_costs_income_monthly_minor: 0,
  income_basis_minor: 300000,
  available_after_fixed_minor: 125000,
  fixed_costs_income_basis: "actual",
  fixed_costs_ratio_percent: 58.3,
  fixed_costs_contract_count: 4,
  groups: [],
} as unknown as BudgetSummary;

function renderFlow(overrides: Partial<BudgetSummary> = {}, includeReserves = false) {
  const onChange = vi.fn();
  const onOpenReserves = vi.fn();
  render(
    <MemoryRouter>
      <BudgetFlow
        budget={{ ...budget, ...overrides }}
        includeReserves={includeReserves}
        onIncludeReservesChange={onChange}
        onOpenReserves={onOpenReserves}
        year={2026}
        month={8}
      />
    </MemoryRouter>,
  );
  return { onChange, onOpenReserves };
}

describe("BudgetFlow smoke", () => {
  it("zeigt die Kette bis Stufe 2, wenn Rücklagen aus sind", async () => {
    const { onChange } = renderFlow();
    expect(screen.getByTestId("budget-flow-stage-income")).toBeInTheDocument();
    expect(screen.getByTestId("budget-flow-stage-after-fixed")).toHaveTextContent("2.000,00");
    expect(screen.queryByTestId("budget-flow-stage-truly-free")).not.toBeInTheDocument();
    expect(screen.getByTestId("budget-fixed-costs-ratio")).toHaveTextContent("58.3 %");
    expect(screen.getByTestId("budget-fixed-costs-ratio-bar")).toBeInTheDocument();
    expect(screen.getByText("Angespannt")).toBeInTheDocument();
    expect(screen.getByText(/Bezug: gebuchte Einnahmen/)).toBeInTheDocument();
    // Der Hinweis nennt ausdruecklich auch die Planbetraege: der Schalter
    // blendet die ganze Kette ab Stufe 3 aus, nicht nur die Ruecklagen.
    expect(
      screen.getByText(/Einrechnen, um Planbeträge, Fixkosten-Rücklagen und Sparziele abzuziehen/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /wirklich frei bleibt/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("checkbox", { name: "Rücklagen in den Monatsplan einrechnen" }),
    );
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("zeigt Stufe 3 und 4 mit Rücklagen", async () => {
    const { onOpenReserves } = renderFlow({}, true);
    expect(screen.getByTestId("budget-flow-stage-surplus")).toHaveTextContent("1.500,00");
    expect(screen.getByTestId("budget-flow-stage-truly-free")).toHaveTextContent("1.050,00");
    expect(screen.getByText("Wirklich frei nach Rücklagen")).toBeInTheDocument();
    expect(screen.getByText("Empfohlene Rücklagen")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Rücklagen öffnen" });
    expect(link).toHaveAttribute("href", "/budget?tab=reserves&year=2026&month=8");
    // Die Adresse allein reicht nicht: der aktive Tab liegt im Zustand der
    // Budgetseite, deshalb muss der Klick zusaetzlich melden.
    await userEvent.click(link);
    expect(onOpenReserves).toHaveBeenCalled();
  });

  it("überlebt fehlende Felder und Nullbasis ohne NaN", () => {
    renderFlow(
      {
        planning_expected_income_minor: 0,
        income_basis_minor: 0,
        total_planned_minor: 0,
        total_rollover_minor: 0,
        fixed_costs_ratio_percent: 0,
        fixed_costs_monthly_minor: 0,
        total_planned_income_minor: undefined as unknown as number,
        uncategorized_spent_minor: undefined as unknown as number,
        planning_truly_free_minor: -12345,
      },
      true,
    );
    expect(document.body.textContent).not.toContain("NaN");
    expect(screen.getByTestId("budget-fixed-costs-ratio")).toHaveTextContent("—");
    expect(screen.queryByTestId("budget-flow-uncategorized")).not.toBeInTheDocument();
    expect(screen.getByTestId("budget-flow-stage-truly-free").className).toContain(
      "budget-flow__row--gap",
    );
    for (const fill of document.querySelectorAll<HTMLElement>(".budget-flow__bar-fill")) {
      expect(fill.style.width).toBe("0%");
    }
  });

  it("haelt Plan, Ist und Rest der Einnahmen in einer Rechnung", () => {
    // total_income_minor enthaelt zusaetzlich 100,00 Zufluss ohne Kategorie.
    // Stuende dieser groessere Wert als "Ist" zwischen Plan und Rest, ergaeben
    // 6.899,00 - 3.100,00 nicht die ausgewiesenen 3.899,00.
    renderFlow({ total_income_minor: 310000 });
    const income = screen.getByTestId("budget-flow-stage-income");
    expect(income).toHaveTextContent("6.899,00 €");
    expect(income).toHaveTextContent("3.000,00 €");
    expect(income).toHaveTextContent("3.899,00 €");
    expect(income).toHaveTextContent("Zusätzlich ohne Kategorie eingegangen");
    expect(income).toHaveTextContent("100,00 €");
  });

  it("weist unkategorisierte Ausgaben aus", () => {
    renderFlow();
    expect(screen.getByTestId("budget-flow-uncategorized")).toHaveTextContent("42,10");
    expect(screen.getByTestId("budget-booked-progress")).toBeInTheDocument();
  });
});
