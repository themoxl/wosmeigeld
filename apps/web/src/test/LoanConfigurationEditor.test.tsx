import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoanConfigurationEditor } from "../components/projects/LoanConfigurationEditor";
import type { CostDrawPreviewInput, FinancingSource } from "../lib/types";

const calls = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  previews: [] as CostDrawPreviewInput[],
}));

vi.mock("../hooks/useProjects", () => ({
  useCreateFinancingConfiguration: () => ({
    isPending: false,
    error: null,
    mutate: (
      input: unknown,
      options: { onSuccess: (result: { source: { id: string } }) => void },
    ) => {
      calls.create(input);
      options.onSuccess({ source: { id: "created-source" } });
    },
  }),
  useUpdateFinancingConfiguration: () => ({
    isPending: false,
    error: null,
    mutate: (
      input: unknown,
      options: { onSuccess: (result: { source: { id: string } }) => void },
    ) => {
      calls.update(input);
      options.onSuccess({ source: { id: "source-1" } });
    },
  }),
  useFinancingDrawPreview: (
    _projectId: string,
    _scenarioId: string,
    input: CostDrawPreviewInput,
    enabled: boolean,
  ) => {
    calls.previews.push(input);
    const allocated = Math.min(input.principal_limit_minor, 10_000_000);
    return {
      data: enabled
        ? {
            draws: [{ occurs_after_month: 0, amount_minor: allocated, status: "planned" }],
            scheduled_cost_minor: 10_000_000,
            allocated_minor: allocated,
            unallocated_credit_minor: Math.max(input.principal_limit_minor - allocated, 0),
            uncovered_cost_minor: Math.max(10_000_000 - allocated, 0),
          }
        : undefined,
      isFetching: false,
      isError: false,
    };
  },
}));

const source = {
  id: "source-1",
  scenario_id: "scenario-1",
  name: "Jung kauft Alt 308",
  source_type: "subsidized_loan",
  status: "contracted",
  planned_amount_minor: 14_000_000,
  principal_limit_minor: 14_000_000,
  available_on: "2027-02-01",
  account_id: null,
  savings_goal_id: null,
  asset_id: null,
  funding_program_version_id: "version-308",
  gross_amount_minor: null,
  deductions_minor: null,
  sort_order: 0,
  note: null,
  version: 3,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  has_debt_plan: true,
  funding_loan_terms: null,
  selected_loan_terms: { variant_id: "annuity_26_35" },
  debt_plan: {
    id: "plan-1",
    project_financing_source_id: "source-1",
    name: "Jung kauft Alt 308",
    creditor_label: "KfW",
    status: "active",
    principal_limit_minor: 14_000_000,
    opening_balance_minor: 0,
    starts_on: "2027-02-01",
    payment_interval_months: 1,
    amortization_mode: "full_repayment_term",
    fixed_payment_minor: null,
    initial_repayment_rate_bps: null,
    target_term_months: 360,
    repayment_free_months: 60,
    payment_free_months: 0,
    capitalize_interest_during_payment_free: false,
    commitment_free_months: 0,
    commitment_rate_bps_per_month: 0,
    recast_payment_on_rate_change: true,
    draw_mode: "cost_plan",
    rate_periods: [
      {
        id: "rate-1",
        starts_after_month: 0,
        ends_after_month: 119,
        nominal_rate_bps_annual: 53,
        rate_kind: "contracted",
        label: "Sollzinsbindung",
      },
      {
        id: "rate-2",
        starts_after_month: 120,
        ends_after_month: null,
        nominal_rate_bps_annual: 450,
        rate_kind: "assumed",
        label: "Anschlusszins",
      },
    ],
    draws: [{ id: "draw-1", occurs_after_month: 0, amount_minor: 10_000_000, status: "planned" }],
    fees: [],
    special_repayments: [],
    version: 2,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
  cost_draw_preview: {
    draws: [{ occurs_after_month: 0, amount_minor: 10_000_000, status: "planned" }],
    scheduled_cost_minor: 10_000_000,
    allocated_minor: 10_000_000,
    unallocated_credit_minor: 4_000_000,
    uncovered_cost_minor: 0,
  },
} as unknown as FinancingSource;

function renderEditor(currentSource: FinancingSource | null = source) {
  return render(
    <LoanConfigurationEditor
      projectId="project-1"
      scenarioId="scenario-1"
      source={currentSource}
      sourceType="subsidized_loan"
      onCancel={vi.fn()}
      onSaved={vi.fn()}
    />,
  );
}

describe("LoanConfigurationEditor", () => {
  beforeEach(() => {
    calls.create.mockClear();
    calls.update.mockClear();
    calls.previews.length = 0;
  });

  it("hydratisiert alle Kreditwerte, speichert sie und zeigt sie beim Wiederöffnen", async () => {
    const user = userEvent.setup();
    const first = renderEditor();
    expect(screen.getByLabelText("Bezeichnung")).toHaveValue("Jung kauft Alt 308");
    expect(screen.getByRole("option", { name: "Abgelehnt" })).toBeInTheDocument();
    expect(screen.getByLabelText("Darlehensrahmen (€)")).toHaveValue("140000");
    expect(screen.getByLabelText("Sollzins (% p. a.)")).toHaveValue("0,53");
    expect(screen.getByLabelText("Ziel-Laufzeit (Monate)")).toHaveValue("360");
    expect(screen.getByLabelText(/^Tilgungsfreie Monate/)).toHaveValue("60");
    expect(screen.getByRole("radio", { name: /Nach Kostenplan abrufen/ })).toBeChecked();

    await user.clear(screen.getByLabelText("Sollzins (% p. a.)"));
    await user.type(screen.getByLabelText("Sollzins (% p. a.)"), "0,75");
    await user.click(screen.getByRole("button", { name: "Änderungen speichern" }));
    await waitFor(() => expect(calls.update).toHaveBeenCalledOnce());
    expect(calls.update.mock.calls[0]?.[0].input.debt_plan.rate_periods[0]).toMatchObject({
      nominal_rate_bps_annual: 75,
      starts_after_month: 0,
      ends_after_month: 119,
    });

    first.unmount();
    const reopened = {
      ...source,
      debt_plan: {
        ...source.debt_plan!,
        rate_periods: (source.debt_plan!.rate_periods ?? []).map((period, index) =>
          index === 0 ? { ...period, nominal_rate_bps_annual: 75 } : period,
        ),
      },
    } as FinancingSource;
    renderEditor(reopened);
    expect(screen.getByLabelText("Sollzins (% p. a.)")).toHaveValue("0,75");
    expect(screen.getByLabelText("Ziel-Laufzeit (Monate)")).toHaveValue("360");
  });

  it("meldet einen ungültigen Zinssatz am Feld und wirft nicht", async () => {
    const user = userEvent.setup();
    renderEditor(null);
    await user.type(screen.getByLabelText("Darlehensrahmen (€)"), "100000");
    await user.type(screen.getByLabelText("Sollzins (% p. a.)"), "abc");
    await user.click(screen.getByRole("button", { name: "Kredit & Tilgungsplan anlegen" }));

    expect(screen.getByLabelText("Sollzins (% p. a.)")).toHaveAttribute("aria-invalid", "true");
    expect(
      screen.getAllByText("Zinsphase 1: Bitte einen gültigen Sollzins eintragen.").length,
    ).toBeGreaterThan(0);
    expect(calls.create).not.toHaveBeenCalled();
  });

  it("berechnet die Kostenplan-Vorschau live aus Betrag, Start und Modus", async () => {
    const user = userEvent.setup();
    renderEditor(null);
    const amount = screen.getByLabelText("Darlehensrahmen (€)");
    await user.type(amount, "50000");
    await user.click(screen.getByRole("radio", { name: /Nach Kostenplan abrufen/ }));
    expect(await screen.findByText("Belegt: 50.000,00 €")).toBeInTheDocument();

    await user.clear(amount);
    await user.type(amount, "80000");
    expect(await screen.findByText("Belegt: 80.000,00 €")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Auszahlung ab"));
    await user.type(screen.getByLabelText("Auszahlung ab"), "2028-06-01");

    expect(calls.previews.at(-1)).toMatchObject({
      principal_limit_minor: 8_000_000,
      starts_on: "2028-06-01",
      draw_mode: "cost_plan",
    });
    await user.click(screen.getByRole("radio", { name: /Gesamter Betrag sofort/ }));
    expect(screen.queryByText("Automatische Abruf-Vorschau")).not.toBeInTheDocument();
  });
});
