import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { SavingsPlanningPage } from "../pages/SavingsPlanningPage";

const session = {
  access_token: "test-access-token",
  user: { id: "user-1", email: "test@example.com" },
};

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signInWithPassword: vi.fn(),
      signUp: vi.fn(),
      signOut: vi.fn(),
    },
  },
}));

const meResponse = {
  user_id: "user-1",
  display_name: null,
  language: "de",
  timezone: "Europe/Berlin",
  currency: "EUR",
  households: [{ household_id: "household-1", name: "Testhaushalt", role: "owner" }],
};

const goal = {
  id: "goal-1",
  name: "Familienbesuch",
  target_amount_minor: 150000,
  starting_amount_minor: 30000,
  current_amount_minor: 50000,
  target_date: "2027-06-01",
  monthly_required_minor: 10000,
  planning_mode: "target_date",
  monthly_contribution_minor: null,
  include_in_planning: true,
  priority: 1,
  effective_monthly_reserve_minor: 10000,
  projected_completion_date: "2027-06-01",
  planning_status: "on_track",
  account_id: null,
  category_id: null,
  status: "active",
  created_at: "2026-08-01T00:00:00Z",
};

const budget = {
  surplus_before_goal_reserves_minor: 90000,
  goal_reserves_monthly_minor: 10000,
  free_after_goal_reserves_minor: 80000,
  goal_reserve_count: 1,
  planning_expected_income_minor: 300000,
  planning_income_basis: "contract",
  regular_fixed_costs_monthly_minor: 160000,
  fixed_cost_reserves_monthly_minor: 10000,
  fixed_cost_reserve_count: 1,
  fixed_cost_reserve_items: [],
  variable_planned_budget_minor: 40000,
  variable_planned_excluded_contract_minor: 0,
  variable_planned_excluded_goal_savings_minor: 0,
  total_calculational_reserves_minor: 20000,
  planning_truly_free_minor: 80000,
};

const wish = {
  id: "wish-1",
  name: "Neues Handy",
  price_minor: 70000,
  priority: 2,
  note: null,
  goal_id: null,
  status: "open",
  buy_traffic_light: "red",
  coverage_ratio: 0.2,
  shortfall_minor: 56000,
  earliest_purchase_date: "2027-02-01",
  created_at: "2026-08-01T00:00:00Z",
};

type RequestRecord = { url: string; init?: RequestInit };

function renderPage(requests: RequestRecord[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      requests.push({ url, init });
      if (/\/v1\/me(?:\?|$)/.test(url)) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.endsWith("/v1/goals/planning-preview") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              target_amount_minor: 70000,
              current_amount_minor: 0,
              remaining_amount_minor: 70000,
              planning_mode: "monthly_amount",
              monthly_required_minor: 5000,
              effective_monthly_reserve_minor: 5000,
              projected_completion_date: "2027-10-01",
              planning_status: "on_track",
            }),
        });
      }
      if (url.includes("/start-savings-plan") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () =>
            Promise.resolve({
              goal: {
                ...goal,
                id: "goal-2",
                name: wish.name,
                target_amount_minor: wish.price_minor,
              },
              wishlist_item: { ...wish, goal_id: "goal-2" },
            }),
        });
      }
      if (url.endsWith("/v1/goals")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([goal]) });
      }
      if (url.endsWith("/v1/wishlist")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([wish]) });
      }
      if (url.includes("/v1/budgets/")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(budget) });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sparen"]}>
        <AuthProvider>
          <HouseholdProvider>
            <SavingsPlanningPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Sparen & Wünsche", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("zeigt vier Tabs, startet einen Wunsch-Sparplan und berechnet ein Szenario", async () => {
    const requests: RequestRecord[] = [];
    const user = userEvent.setup();
    renderPage(requests);

    expect(await screen.findByRole("heading", { name: "Sparen & Wünsche" })).toBeInTheDocument();
    for (const label of ["Übersicht", "Spartöpfe", "Wünsche", "Analyse"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    const equation = await screen.findByRole("region", {
      name: "Kalkulatorische Haushaltsplanung",
    });
    expect(
      within(equation).getByText("Fixkosten-Rücklagen", { selector: "strong" }),
    ).toBeInTheDocument();
    expect(
      within(equation).getByText("Spartöpfe & Wünsche", { selector: "strong" }),
    ).toBeInTheDocument();
    expect(within(equation).getByText("Wirklich frei", { selector: "strong" })).toBeInTheDocument();
    expect(within(equation).getByRole("link", { name: "Verträgen" })).toHaveAttribute(
      "href",
      "/fixkosten",
    );

    await user.click(screen.getByRole("button", { name: "Wünsche" }));
    await user.click(await screen.findByRole("button", { name: "Sparplan starten" }));
    const dialog = screen.getByRole("dialog", { name: /Sparplan für Neues Handy/ });
    await user.selectOptions(within(dialog).getByLabelText("Planungsart"), "monthly_amount");
    await user.type(within(dialog).getByLabelText("Monatsbetrag"), "50,00");
    await user.click(within(dialog).getByRole("button", { name: "Spartopf anlegen" }));

    await waitFor(() => {
      const request = requests.find((entry) => entry.url.includes("/start-savings-plan"));
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toEqual({
        planning_mode: "monthly_amount",
        target_date: null,
        monthly_contribution_minor: 5000,
        starting_amount_minor: 0,
        include_in_planning: true,
      });
    });

    await user.click(screen.getByRole("button", { name: "Analyse" }));
    await user.type(screen.getByLabelText("Zielbetrag"), "700,00");
    await user.selectOptions(screen.getByLabelText("Berechnung"), "monthly_amount");
    await user.type(screen.getByLabelText("Monatsbetrag"), "50,00");
    await user.click(screen.getByRole("button", { name: "Sparplan berechnen" }));

    expect(await screen.findByText(/Ziel voraussichtlich/)).toHaveTextContent("01.10.2027");
    expect(requests.some((entry) => entry.url.endsWith("/v1/goals/planning-preview"))).toBe(true);
  });
});
