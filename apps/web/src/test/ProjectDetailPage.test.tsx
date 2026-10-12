import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ProjectDetailPage } from "../pages/ProjectDetailPage";

const authState = vi.hoisted(() => ({
  session: {
    access_token: "test-access-token",
    user: { id: "user-1", email: "test@example.com" },
  },
}));

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session: authState.session } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signInWithPassword: vi.fn(),
      signUp: vi.fn(),
      signOut: vi.fn(),
    },
  },
}));

function meResponse() {
  return {
    user_id: "user-1",
    display_name: null,
    language: "de",
    timezone: "Europe/Vienna",
    currency: "EUR",
    households: [{ household_id: "household-1", name: "Testhaushalt", role: "owner" }],
  };
}

const project = {
  id: "project-1",
  name: "Hausbau Musterstrasse",
  project_type: "home_build",
  status: "idea",
  currency: "EUR",
  country_code: "DE",
  region_code: null,
  postal_code: null,
  municipality: null,
  planned_start_on: null,
  planned_completion_on: null,
  actual_start_on: null,
  actual_completion_on: null,
  result_asset_id: null,
  note: null,
  version: 1,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const scenario = {
  id: "scenario-1",
  project_id: "project-1",
  name: "Basis",
  scenario_kind: "baseline",
  status: "draft",
  source_scenario_id: null,
  as_of_date: "2026-08-01",
  contingency_mode: "none",
  contingency_rate_bps: null,
  contingency_fixed_minor: null,
  minimum_liquidity_reserve_minor: 0,
  calculation_horizon_months: 360,
  selected_at: null,
  version: 1,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const calculation = {
  scenario_id: "scenario-1",
  scenario_version: 1,
  input_fingerprint: "abc",
  engine_version: "v1",
  as_of_date: "2026-08-01",
  estimated_cost_minor: 8500000,
  quoted_cost_minor: 8500000,
  committed_cost_minor: 8500000,
  forecast_cash_cost_minor: 8500000,
  non_cash_cost_minor: 0,
  contingency_minor: 0,
  project_cash_cost_minor: 8500000,
  project_economic_cost_minor: 8500000,
  actual_cost_minor: 0,
  remaining_forecast_minor: 8500000,
  per_item_estimate_minor: {},
  per_item_forecast_minor: {},
  warnings: ["cost_schedule_incomplete"],
};

const financialSummary = {
  scenario_id: "scenario-1",
  funding_gap: {
    cash_required_until_completion_minor: 8500000,
    planned_cash_funding_minor: 0,
    secured_cash_funding_minor: 0,
    planned_gap_minor: 8500000,
    secured_gap_minor: 8500000,
  },
  affordability: null,
  goal_impacts: [],
  warnings: ["funding_gap"],
};

const equityAvailability = { accounts: [] };

const configuredLoan = {
  id: "loan-source-1",
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
  debt_plan: {
    id: "plan-1",
    project_financing_source_id: "loan-source-1",
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
    draws: [],
    fees: [],
    special_repayments: [],
    version: 2,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
  cost_draw_preview: {
    draws: [],
    scheduled_cost_minor: 0,
    allocated_minor: 0,
    unallocated_credit_minor: 14_000_000,
    uncovered_cost_minor: 0,
  },
};

const fundingCheckRun = {
  id: "run-1",
  scenario_id: "scenario-1",
  scenario_version: 1,
  input_fingerprint: "abc",
  engine_version: "v1",
  warnings: [],
  created_at: "2026-08-01T00:00:00Z",
  matches: [
    {
      id: "match-1",
      program_version_id: "pv-1",
      program_id: "p-1",
      program_semantic_key: "kfw_124_wohneigentumsprogramm",
      program_title: "KfW-Wohneigentumsprogramm (124)",
      provider_name: "KfW",
      status: "eligible",
      unknown_reason: null,
      estimated_grant_min_minor: null,
      estimated_grant_max_minor: null,
      estimated_loan_min_minor: 10000000,
      estimated_loan_max_minor: 10000000,
      criteria_met: [],
      criteria_violated: [],
      criteria_missing: [],
      criteria_manual: [],
      catalog_age_days: 0,
      checked_at: "2026-08-01T00:00:00Z",
    },
  ],
};

function renderDetailPage(financingSources: unknown[] = []) {
  let fundingCheckHasRun = false;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse()) });
    }
    if (url.endsWith("/v1/projects/project-1")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(project) });
    }
    if (url.endsWith("/v1/projects/project-1/scenarios")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([scenario]) });
    }
    if (url.includes("/calculate") && method === "POST") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(calculation) });
    }
    if (url.includes("/financial-summary")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(financialSummary),
      });
    }
    if (url.includes("/equity-availability")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(equityAvailability),
      });
    }
    if (url.includes("/financing-sources") && method === "GET") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(financingSources),
      });
    }
    if (url.includes("/v1/debt-plans/plan-1/schedule")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ debt_plan_id: "plan-1", rows: [], warnings: [] }),
      });
    }
    if (url.endsWith("/v1/accounts")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([{ id: "account-1", name: "Girokonto" }]),
      });
    }
    if (url.includes("/eligibility-profile")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(null) });
    }
    if (url.includes("/funding-check") && method === "POST") {
      fundingCheckHasRun = true;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(fundingCheckRun),
      });
    }
    if (url.includes("/funding-matches")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(fundingCheckHasRun ? fundingCheckRun : null),
      });
    }
    if (url.includes("/financing-sources") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 201,
        json: () =>
          Promise.resolve({
            id: "source-1",
            scenario_id: "scenario-1",
            name: "Zuschuss Eltern",
            source_type: "public_grant",
            status: "idea",
            planned_amount_minor: 500000,
            principal_limit_minor: null,
            available_on: null,
            account_id: null,
            savings_goal_id: null,
            asset_id: null,
            funding_program_version_id: null,
            gross_amount_minor: null,
            deductions_minor: null,
            sort_order: 0,
            note: null,
            version: 1,
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
            has_debt_plan: false,
          }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/vorhaben/project-1"]}>
        <AuthProvider>
          <HouseholdProvider>
            <Routes>
              <Route path="/vorhaben/:id" element={<ProjectDetailPage />} />
            </Routes>
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProjectDetailPage", () => {
  it("zeigt Projektname, Status und Kennzahlen der Uebersicht", async () => {
    renderDetailPage();
    await screen.findByText("Hausbau Musterstrasse");
    expect(screen.getByText("Idee")).toBeInTheDocument();
    expect((await screen.findAllByText("85.000,00 €")).length).toBeGreaterThan(0);
  });

  it("zeigt die Warnung bei unvollstaendigem Zahlungsplan", async () => {
    renderDetailPage();
    await screen.findByText(/Zahlungsplan unvollständig/);
  });

  it("bietet den Statuswechsel nach Planung an", async () => {
    renderDetailPage();
    await screen.findByText("Hausbau Musterstrasse");
    expect(screen.getByRole("button", { name: "→ Planung" })).toBeInTheDocument();
  });

  it("wechselt in den Kosten-Tab", async () => {
    renderDetailPage();
    await screen.findByText("Hausbau Musterstrasse");
    await userEvent.click(screen.getByRole("tab", { name: "Kosten" }));
    await screen.findByText("+ Abschnitt");
  });

  it("wechselt in den Finanzierung-Tab und zeigt die Finanzierungsluecke", async () => {
    renderDetailPage();
    await screen.findByText("Hausbau Musterstrasse");
    await userEvent.click(screen.getByRole("tab", { name: "Finanzierung" }));
    await screen.findByText("+ Quelle anlegen");
    expect(screen.getByText("Offene Lücke")).toBeInTheDocument();
    expect((await screen.findAllByText("85.000,00 €")).length).toBeGreaterThan(0);
  });

  it("wechselt in den Annahmen-Tab und zeigt das Formular fuer Haushaltsannahmen", async () => {
    renderDetailPage();
    await screen.findByText("Hausbau Musterstrasse");
    await userEvent.click(screen.getByRole("tab", { name: "Haushalt & Belastung" }));
    await screen.findByRole("heading", { name: "Haushaltsannahmen" });
    expect(screen.getByText("Folgekosten und -einnahmen")).toBeInTheDocument();
    expect(screen.getByText("Zielwirkung")).toBeInTheDocument();
  });

  it("öffnet einen übernommenen Kredit aus den Annahmen direkt im Inline-Editor", async () => {
    renderDetailPage([configuredLoan]);
    await screen.findByText("Hausbau Musterstrasse");
    await userEvent.click(screen.getByRole("tab", { name: "Haushalt & Belastung" }));
    expect(await screen.findByText("Jung kauft Alt 308")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /In Finanzierung öffnen/ }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Finanzierung" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(await screen.findByLabelText("Bezeichnung")).toHaveValue("Jung kauft Alt 308");
    expect(screen.getByRole("button", { name: "Änderungen speichern" })).toBeInTheDocument();
  });

  it("legt eine neue Finanzierungsquelle an", async () => {
    const fetchMock = renderDetailPage();
    await screen.findByText("Hausbau Musterstrasse");
    await userEvent.click(screen.getByRole("tab", { name: "Finanzierung" }));
    await userEvent.click(await screen.findByRole("button", { name: "+ Quelle anlegen" }));
    await userEvent.selectOptions(screen.getByLabelText("Art"), "public_grant");
    await userEvent.clear(screen.getByLabelText("Bezeichnung"));
    await userEvent.type(screen.getByLabelText("Bezeichnung"), "Zuschuss Eltern");
    await userEvent.type(screen.getByLabelText("Betrag (€)"), "5000");
    await userEvent.click(screen.getByRole("button", { name: "Quelle speichern" }));

    // Dialog schliesst nach erfolgreichem Anlegen (der Fetch-Mock haelt
    // keinen Serverzustand, daher wird hier nicht die aktualisierte Tabelle
    // geprueft, sondern der abgesendete Request).
    await waitFor(() => expect(screen.queryByText("Neue Quelle")).not.toBeInTheDocument());
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        typeof input === "string" &&
        input.includes("/financing-sources") &&
        init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    const body = JSON.parse((createCall?.[1]?.body as string) ?? "{}");
    expect(body).toMatchObject({
      name: "Zuschuss Eltern",
      source_type: "public_grant",
      planned_amount_minor: 500000,
    });
  });
});
