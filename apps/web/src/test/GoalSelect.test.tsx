import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GoalSelect } from "../pages/transactions/GoalSelect";
import type { SavingsGoal } from "../lib/types";

const baseGoal: SavingsGoal = {
  id: "goal-active",
  name: "Notgroschen",
  target_amount_minor: 300000,
  starting_amount_minor: 0,
  current_amount_minor: 50000,
  target_date: null,
  monthly_required_minor: 0,
  planning_mode: "target_date",
  monthly_contribution_minor: null,
  include_in_planning: false,
  priority: 3,
  effective_monthly_reserve_minor: 0,
  projected_completion_date: null,
  planning_status: "incomplete",
  account_id: null,
  category_id: null,
  status: "active",
  created_at: "2026-08-01T00:00:00Z",
};

const archivedGoal: SavingsGoal = {
  ...baseGoal,
  id: "goal-archived",
  name: "Altes Ziel",
  status: "cancelled",
};

describe("GoalSelect", () => {
  it("bietet archivierte Ziele nicht fuer neue Zuordnungen an", () => {
    render(
      <GoalSelect
        aria-label="Sparziel"
        goals={[baseGoal, archivedGoal]}
        value=""
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("option", { name: "Notgroschen" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Altes Ziel/ })).not.toBeInTheDocument();
  });

  it("erhaelt eine bestehende Verknuepfung zu einem archivierten Ziel sichtbar", () => {
    render(
      <GoalSelect
        aria-label="Sparziel"
        goals={[baseGoal, archivedGoal]}
        value="goal-archived"
        onChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("combobox", { name: "Sparziel" })).toHaveValue("goal-archived");
    expect(screen.getByRole("option", { name: "Altes Ziel (Archiviert)" })).toBeInTheDocument();
  });
});
