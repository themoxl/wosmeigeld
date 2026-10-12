import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AddFundingSourceDialog } from "../components/projects/AddFundingSourceDialog";
import type { MajorProject } from "../lib/types";

const calls = vi.hoisted(() => ({
  source: vi.fn(),
  configuration: vi.fn(),
}));

const loanTerms = {
  rate_as_of: "2026-08-09",
  variants: [
    {
      id: "annuity_7_10",
      label: "Annuität 7–10 Jahre",
      repayment_type: "annuity",
      min_term_months: 84,
      max_term_months: 120,
      fixed_rate_months: 120,
      min_repayment_free_months: 12,
      max_repayment_free_months: 24,
      nominal_rate_bps_annual: 1,
      effective_rate_bps_annual: 1,
    },
    {
      id: "annuity_11_25",
      label: "Annuität 11–25 Jahre",
      repayment_type: "annuity",
      min_term_months: 132,
      max_term_months: 300,
      fixed_rate_months: 120,
      min_repayment_free_months: 12,
      max_repayment_free_months: 36,
      nominal_rate_bps_annual: 25,
      effective_rate_bps_annual: 25,
    },
    {
      id: "annuity_26_35",
      label: "Annuität 26–35 Jahre",
      repayment_type: "annuity",
      min_term_months: 312,
      max_term_months: 420,
      fixed_rate_months: 120,
      min_repayment_free_months: 12,
      max_repayment_free_months: 60,
      nominal_rate_bps_annual: 53,
      effective_rate_bps_annual: 53,
    },
    {
      id: "bullet_7_10",
      label: "Endfälliges Darlehen 7–10 Jahre",
      repayment_type: "bullet",
      min_term_months: 84,
      max_term_months: 120,
      fixed_rate_months: 120,
      min_repayment_free_months: 0,
      max_repayment_free_months: 0,
      nominal_rate_bps_annual: 69,
      effective_rate_bps_annual: 69,
    },
  ],
};

vi.mock("../hooks/useFunding", () => ({
  useFundingPrograms: () => ({
    isLoading: false,
    data: [
      {
        id: "program-308",
        title: "Jung kauft Alt – Wohneigentum für Familien (308)",
        provider_name: "KfW",
        benefit_type: "loan",
        is_stale: false,
      },
    ],
  }),
  useFundingProgram: () => ({
    isLoading: false,
    data: {
      id: "program-308",
      title: "Jung kauft Alt – Wohneigentum für Familien (308)",
      provider_name: "KfW",
      current_version: {
        id: "version-308",
        benefit_type: "loan",
        benefit_json: {
          type: "loan_tiered",
          tier_field: "children_count",
          tiers: [{ min: 1, max: 1, amount_minor: 14_000_000 }],
        },
        loan_terms_json: loanTerms,
      },
    },
  }),
  useEligibilityProfile: () => ({ data: { facts: { children_count: 1 } } }),
}));

vi.mock("../hooks/useProfile", async () => {
  const { formatDate, formatDateTime } = await import("../lib/dates");
  return {
    useFormatDate: () => ({
      dateFormat: "de" as const,
      formatDate: (value: string | Date) => formatDate(value, "de"),
      formatDateTime: (value: string | Date) => formatDateTime(value, "de"),
    }),
  };
});

vi.mock("../hooks/useProjects", () => ({
  useFinancingDrawPreview: () => ({
    data: {
      draws: [{ occurs_after_month: 0, amount_minor: 14_000_000, status: "planned" }],
      scheduled_cost_minor: 14_000_000,
      allocated_minor: 14_000_000,
      unallocated_credit_minor: 0,
      uncovered_cost_minor: 0,
    },
    isFetching: false,
    isError: false,
  }),
  useCreateFinancingConfiguration: () => ({
    isPending: false,
    isError: false,
    error: null,
    mutate: (input: unknown, options: { onSuccess: () => void }) => {
      calls.configuration(input);
      options.onSuccess();
    },
  }),
  useCreateFinancingSource: () => ({
    isPending: false,
    isError: false,
    mutate: (input: unknown, options: { onSuccess: (source: { id: string }) => void }) => {
      calls.source(input);
      options.onSuccess({ id: "source-1" });
    },
  }),
}));

const project = {
  id: "project-1",
  country_code: "DE",
  region_code: "DE-BY",
  project_type: "property_purchase",
} as MajorProject;

async function openConditions() {
  const user = userEvent.setup();
  render(<AddFundingSourceDialog project={project} scenarioId="scenario-1" onClose={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Auswählen" }));
  return user;
}

describe("AddFundingSourceDialog KfW 308", () => {
  beforeEach(() => {
    Object.values(calls).forEach((mock) => mock.mockClear());
  });

  it("requires a variant and auto-populates the coupled 35-year terms", async () => {
    const user = await openConditions();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Förderung: Jung kauft Alt/ })).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(6);
    const submit = screen.getByRole("button", { name: "Als Finanzierungsquelle übernehmen" });
    expect(submit).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /Annuität 26–35 Jahre/ }));
    await user.selectOptions(screen.getByLabelText("Gesamtlaufzeit"), "420");
    await user.selectOptions(screen.getByLabelText("Tilgungsfreie Anlaufzeit"), "60");

    expect(screen.getByLabelText("Sollzins (% p. a.)")).toHaveValue("0,53");
    expect(screen.getByLabelText("Sollzins (% p. a.)")).toHaveAttribute("readonly");
    await user.click(submit);

    await waitFor(() => expect(calls.configuration).toHaveBeenCalled());
    expect(calls.configuration.mock.calls[0]?.[0].input.debt_plan).toMatchObject({
      draw_mode: "cost_plan",
      principal_limit_minor: 14_000_000,
      amortization_mode: "full_repayment_term",
      target_term_months: 360,
      repayment_free_months: 60,
    });
    expect(calls.configuration.mock.calls[0]?.[0].input.debt_plan.rate_periods).toEqual([
      expect.objectContaining({ nominal_rate_bps_annual: 53, ends_after_month: 119 }),
      expect.objectContaining({
        nominal_rate_bps_annual: 450,
        starts_after_month: 120,
        label: "Anschlusszins (Annahme)",
      }),
    ]);
  });

  it("creates the final principal payment for the bullet option", async () => {
    const user = await openConditions();
    await user.click(screen.getByRole("radio", { name: /Endfälliges Darlehen/ }));
    await user.selectOptions(screen.getByLabelText("Gesamtlaufzeit"), "120");
    await user.click(screen.getByRole("button", { name: "Als Finanzierungsquelle übernehmen" }));

    await waitFor(() => expect(calls.configuration).toHaveBeenCalled());
    expect(calls.configuration.mock.calls[0]?.[0].input.debt_plan).toMatchObject({
      principal_limit_minor: 14_000_000,
      amortization_mode: "initial_repayment",
      initial_repayment_rate_bps: 0,
    });
    expect(calls.configuration.mock.calls[0]?.[0].input.debt_plan.special_repayments).toEqual([
      expect.objectContaining({ occurs_after_month: 119, amount_minor: 14_000_000 }),
    ]);
  });
});
