import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { formatMonthYear } from "../lib/dates";
import { formatMinorUnitsAsEuro } from "../lib/money";
import { BudgetPage } from "../pages/BudgetPage";

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

const meResponse = {
  user_id: "user-1",
  display_name: null,
  language: "de",
  timezone: "Europe/Vienna",
  currency: "EUR",
  households: [{ household_id: "household-1", name: "Testhaushalt", role: "owner" }],
};

// Der Budgetkern trennt Einnahmen strikt von Ausgaben: Einnahmekategorien
// fuehren ihren Istwert in income_actual_minor, spent_minor bleibt dort 0 und
// die Plan-/Ist-Summen enthalten nur Ausgabenkategorien. Der Uebertrag
// (rollover) gehoert zum verfuegbaren Budget und steckt in den Restwerten.
const mockBudgetSummary = {
  budget_id: "budget-1",
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
  // Der gemeldete Fehlerfall: 6.899,00 EUR Plan auf der Einnahmekategorie
  // "Gehalt". Sie gehoeren NICHT in total_planned_minor (das ist der
  // Ausgabentopf), sondern in die drei *_income_*-Summen. Ist 3.000,00 -
  // also stehen 3.899,00 noch aus.
  total_planned_income_minor: 689900,
  total_rollover_income_minor: 0,
  total_income_remaining_minor: 389900,
  uncategorized_spent_minor: 0,
  savings_rate_percent: 60,
  // Ist je Bucket: 80.000 + 40.000 + 0 + 0 (+ 0 unkategorisiert) = 120.000
  // = total_spent_minor. Plan je Bucket: 165.000 + 50.000 + 35.000 + 0
  // = 250.000 = total_planned_minor. Beide Invarianten sichert das Backend zu.
  bucket_needs_minor: 80000,
  bucket_wants_minor: 40000,
  bucket_savings_minor: 0,
  bucket_unassigned_spent_minor: 0,
  bucket_needs_planned_minor: 165000,
  bucket_wants_planned_minor: 50000,
  bucket_savings_planned_minor: 35000,
  bucket_unassigned_planned_minor: 0,
  bucket_needs_percent: 50,
  bucket_wants_percent: 30,
  bucket_savings_percent: 20,
  // Vorausschau-Kette des Backends, durchgerechnet:
  //   3.000,00 - 1.000,00 (regelmaessige Fixkosten) - 200,00 (Fixkosten-
  //   ruecklagen) - 300,00 (variable Planbetraege) = 1.500,00
  //   - 450,00 (Sparziele) = 1.050,00 wirklich frei.
  surplus_before_goal_reserves_minor: 150000,
  goal_reserves_monthly_minor: 45000,
  free_after_goal_reserves_minor: 105000,
  goal_reserve_count: 2,
  planning_expected_income_minor: 300000,
  planning_income_basis: "contract",
  regular_fixed_costs_monthly_minor: 100000,
  fixed_cost_reserves_monthly_minor: 20000,
  fixed_cost_reserve_count: 1,
  fixed_cost_reserve_items: [
    {
      contract_id: "contract-yearly",
      contract_name: "Hausrat Jahresbeitrag",
      frequency: "yearly",
      period_amount_minor: 120000,
      next_due_date: "2026-08-15",
      monthly_reserve_minor: 10000,
      accrued_minor: 120000,
      remaining_minor: 0,
      months_remaining: 0,
      due_this_month: true,
      is_covered: true,
      is_first_cycle: true,
    },
  ],
  variable_planned_budget_minor: 30000,
  variable_planned_excluded_contract_minor: 100000,
  variable_planned_excluded_goal_savings_minor: 0,
  total_calculational_reserves_minor: 65000,
  planning_truly_free_minor: 105000,
  // Vertragszahlen stehen NEBEN der Plan/Ist-Rechnung, nicht darin: sie sind
  // Erwartungen, die Werte darueber sind gebuchte Wirklichkeit. 1.750,00 EUR
  // von 3.000,00 EUR Ist-Einnahmen sind 58,3 % - im angespannten Bereich.
  fixed_costs_monthly_minor: 175000,
  fixed_costs_income_monthly_minor: 0,
  // Bezugsgroesse aller Quoten (hier das Monats-Ist, siehe
  // fixed_costs_income_basis) - 3.000,00 minus 1.750,00 = 1.250,00 verfuegbar.
  income_basis_minor: 300000,
  available_after_fixed_minor: 125000,
  fixed_costs_income_basis: "actual",
  fixed_costs_ratio_percent: 58.3,
  fixed_costs_contract_count: 4,
  groups: [
    {
      group_id: "grp-1",
      group_name: "Fixkosten",
      sort_order: 0,
      total_planned_minor: 100000,
      total_rollover_minor: 5000,
      total_spent_minor: 80000,
      total_remaining_minor: 25000,
      total_income_actual_minor: 0,
      total_planned_income_minor: 0,
      total_rollover_income_minor: 0,
      total_income_remaining_minor: 0,
      categories: [
        {
          category_id: "cat-1",
          category_name: "Miete",
          group_id: "grp-1",
          group_name: "Fixkosten",
          budget_bucket: "needs",
          default_type: "expense",
          rollover_rule: "none",
          planned_minor: 100000,
          spent_minor: 80000,
          income_actual_minor: 0,
          remaining_minor: 25000,
          rollover_minor: 5000,
          percent_used: 80,
          // Vormonat war guenstiger (750 statt 800 EUR), und die Miete ist
          // zugleich ein Vertrag, der diesen Monat faellig war - Forecast und
          // Ist stimmen ueberein (siehe Test weiter unten).
          prev_month_actual_minor: 75000,
          contract_forecast_minor: 80000,
          contract_forecast_sources: [
            {
              contract_id: "contract-1",
              contract_name: "Vermieter Musterstraße",
              amount_minor: 80000,
              due_date: "2026-08-01",
            },
          ],
          has_activity_ever: true,
        },
        // Kategorie ohne jede Bewegung DIESEN Monat: kein Plan, kein
        // Uebertrag, kein Ist - aber letzten Monat gebucht, deshalb im
        // "Jemals"-Filter trotzdem sichtbar.
        {
          category_id: "cat-3",
          category_name: "Rundfunkbeitrag",
          group_id: "grp-1",
          group_name: "Fixkosten",
          budget_bucket: "needs",
          default_type: "expense",
          rollover_rule: "none",
          planned_minor: 0,
          spent_minor: 0,
          income_actual_minor: 0,
          remaining_minor: 0,
          rollover_minor: 0,
          percent_used: 0,
          prev_month_actual_minor: 1836,
          contract_forecast_minor: 0,
          has_activity_ever: true,
        },
        // Kein Plan, kein Ist, kein Vormonat - einzig ein diesen Monat
        // faelliger Vertrag macht sie relevant. Belegt, dass der Forecast
        // allein schon unter "Diesen Monat" sichtbar haelt.
        {
          category_id: "cat-4",
          category_name: "Hausratversicherung",
          group_id: "grp-1",
          group_name: "Fixkosten",
          budget_bucket: "needs",
          default_type: "expense",
          rollover_rule: "none",
          planned_minor: 0,
          spent_minor: 0,
          income_actual_minor: 0,
          remaining_minor: 0,
          rollover_minor: 0,
          percent_used: 0,
          prev_month_actual_minor: 0,
          contract_forecast_minor: 48000,
          has_activity_ever: true,
        },
        // Echte Nullzeile: nie Plan, nie Ist, nie Vertrag - nur unter "Alle"
        // sichtbar.
        {
          category_id: "cat-5",
          category_name: "Nie genutzt",
          group_id: "grp-1",
          group_name: "Fixkosten",
          budget_bucket: "needs",
          default_type: "expense",
          rollover_rule: "none",
          planned_minor: 0,
          spent_minor: 0,
          income_actual_minor: 0,
          remaining_minor: 0,
          rollover_minor: 0,
          percent_used: 0,
          prev_month_actual_minor: 0,
          contract_forecast_minor: 0,
          has_activity_ever: false,
        },
      ],
    },
    {
      group_id: "grp-2",
      group_name: "Einnahmen",
      sort_order: 1,
      total_planned_minor: 0,
      total_rollover_minor: 0,
      total_spent_minor: 0,
      total_remaining_minor: 0,
      total_income_actual_minor: 300000,
      total_planned_income_minor: 689900,
      total_rollover_income_minor: 0,
      total_income_remaining_minor: 389900,
      categories: [
        {
          category_id: "cat-2",
          category_name: "Gehalt",
          group_id: "grp-2",
          group_name: "Einnahmen",
          budget_bucket: null,
          default_type: "income",
          rollover_rule: "none",
          planned_minor: 689900,
          spent_minor: 0,
          income_actual_minor: 300000,
          remaining_minor: 389900,
          rollover_minor: 0,
          percent_used: 43.5,
          prev_month_actual_minor: 300000,
          contract_forecast_minor: 0,
          has_activity_ever: true,
        },
      ],
    },
  ],
};

/**
 * Jahresmatrix zum selben Haushalt. Bewusst zwei Ausgabenkategorien in einer
 * Gruppe: nur dann unterscheidet sich die Gruppensumme von der einzelnen
 * Kategoriezeile und laesst sich pruefen.
 *
 * August (Monat 8): Miete 800,00 Ist gegen 1.000,00 Plan, Hausrat 480,00 Ist
 * ohne Plan, Gehalt 3.000,00 Einnahme. Gruppe "Fixkosten" also 1.280,00 Ist
 * und 1.000,00 Plan.
 */
function matrixMonths(entries: Record<number, { spent?: number; income?: number; plan?: number }>) {
  return Array.from({ length: 12 }, (_, index) => {
    const entry = entries[index + 1] ?? {};
    return {
      month: index + 1,
      planned_minor: entry.plan ?? 0,
      spent_minor: entry.spent ?? 0,
      income_actual_minor: entry.income ?? 0,
    };
  });
}

const mockAnnualMatrix = {
  year: 2026,
  rows: [
    {
      category_id: "cat-1",
      category_name: "Miete",
      group_id: "grp-1",
      group_name: "Fixkosten",
      group_sort_order: 0,
      category_sort_order: 0,
      budget_bucket: "needs",
      default_type: "expense",
      is_archived: false,
      months: matrixMonths({ 8: { spent: 80000, plan: 100000 } }),
      total_planned_minor: 100000,
      total_spent_minor: 80000,
      total_income_actual_minor: 0,
    },
    {
      category_id: "cat-4",
      category_name: "Hausratversicherung",
      group_id: "grp-1",
      group_name: "Fixkosten",
      group_sort_order: 0,
      category_sort_order: 1,
      budget_bucket: "needs",
      default_type: "expense",
      is_archived: false,
      months: matrixMonths({ 8: { spent: 48000 } }),
      total_planned_minor: 0,
      total_spent_minor: 48000,
      total_income_actual_minor: 0,
    },
    // Ohne jede Jahresbewegung - der Schalter "Kategorien ohne Bewegung" muss
    // sie ausblenden und mitzaehlen.
    {
      category_id: "cat-5",
      category_name: "Nie genutzt",
      group_id: "grp-1",
      group_name: "Fixkosten",
      group_sort_order: 0,
      category_sort_order: 2,
      budget_bucket: "needs",
      default_type: "expense",
      is_archived: false,
      months: matrixMonths({}),
      total_planned_minor: 0,
      total_spent_minor: 0,
      total_income_actual_minor: 0,
    },
    {
      category_id: "cat-2",
      category_name: "Gehalt",
      group_id: "grp-2",
      group_name: "Einnahmen",
      group_sort_order: 1,
      category_sort_order: 0,
      budget_bucket: null,
      default_type: "income",
      is_archived: false,
      months: matrixMonths({ 8: { income: 300000, plan: 689900 } }),
      total_planned_minor: 689900,
      total_spent_minor: 0,
      total_income_actual_minor: 300000,
    },
  ],
};

const mockReserveOverview = {
  year: 2026,
  month: 8,
  recommended_minor: 66429,
  deposited_minor: 10000,
  open_minor: 56429,
  total_balance_minor: 70000,
  fixed_reserve_recommended_minor: 21429,
  goal_reserve_recommended_minor: 45000,
  free_reserve_balance_minor: 20000,
  pots: [
    {
      id: "pot-contract",
      kind: "contract",
      source_id: "contract-yearly",
      name: "Besuch Schwiegermutter",
      frequency: "yearly",
      target_amount_minor: 150000,
      target_date: null,
      next_due_date: "2027-03-15",
      recommended_minor: 21429,
      deposited_minor: 10000,
      withdrawn_minor: 0,
      open_minor: 11429,
      balance_minor: 30000,
      next_month_recommended_minor: 20000,
      status: "partial",
      is_included_in_planning: true,
      movements: [],
    },
    {
      id: "pot-goal",
      kind: "goal",
      source_id: "goal-phone",
      name: "Neues Handy",
      frequency: null,
      target_amount_minor: 70000,
      target_date: "2028-08-01",
      next_due_date: null,
      recommended_minor: 45000,
      deposited_minor: 0,
      withdrawn_minor: 0,
      open_minor: 45000,
      balance_minor: 20000,
      next_month_recommended_minor: 2500,
      status: "open",
      is_included_in_planning: true,
      movements: [],
    },
    {
      id: "pot-free",
      kind: "free",
      source_id: null,
      name: "Freie Rücklage",
      frequency: null,
      target_amount_minor: null,
      target_date: null,
      next_due_date: null,
      recommended_minor: 0,
      deposited_minor: 0,
      withdrawn_minor: 0,
      open_minor: 0,
      balance_minor: 20000,
      next_month_recommended_minor: 0,
      status: "done",
      is_included_in_planning: false,
      movements: [],
    },
  ],
};

const skippedPotIds = new Set<string>();

function renderBudgetPage(
  summaryOverrides: Record<string, unknown> = {},
  initialEntry = "/budget",
) {
  const summary = { ...mockBudgetSummary, ...summaryOverrides };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/me")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(meResponse),
        });
      }
      // MUSS vor dem allgemeinen /v1/budgets/-Zweig stehen: sonst bekaeme die
      // Jahresmatrix die Monatszusammenfassung geliefert und die Komponente
      // liefe auf matrix.rows === undefined.
      if (url.includes("/matrix")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockAnnualMatrix),
        });
      }
      if (url.includes("/v1/budgets/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(summary),
        });
      }
      if (url.includes("/v1/reserves/overview")) {
        const overview = {
          ...mockReserveOverview,
          pots: mockReserveOverview.pots.map((pot) =>
            skippedPotIds.has(pot.id) ? { ...pot, status: "skipped" } : { ...pot },
          ),
        };
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(overview),
        });
      }
      if (url.includes("/v1/reserves/distribution-preview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              total_minor: 2000000,
              allocations: [
                { pot_id: "pot-contract", amount_minor: 11429 },
                { pot_id: "pot-goal", amount_minor: 45000 },
              ],
              free_remainder_minor: 1943571,
            }),
        });
      }
      if (url.includes("/v1/reserves/")) {
        if (url.includes("/month-decisions/skip") && typeof init?.body === "string") {
          const body = JSON.parse(init.body) as { pot_id: string };
          if (init.method === "DELETE") skippedPotIds.delete(body.pot_id);
          if (init.method === "PUT") skippedPotIds.add(body.pot_id);
        }
        return Promise.resolve({
          ok: true,
          status: init?.method === "PUT" || init?.method === "DELETE" ? 204 : 201,
          json: () =>
            Promise.resolve({
              id: "batch-1",
              occurred_on: "2026-08-10",
              kind: "deposit",
              total_minor: 11429,
              note: null,
              allocations: [{ pot_id: "pot-contract", amount_minor: 11429 }],
            }),
        });
      }
      // /v1/analytics (TransactionDrilldown) erwartet dieses Antwortformat,
      // ein leeres Array laesst TransactionTable mit "columns" auf undefined
      // abstuerzen - deshalb hier ein leeres, aber gueltiges AnalyticsResult.
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ grain: "rows", columns: [], rows: [], row_count: 0 }),
      });
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AuthProvider>
          <HouseholdProvider>
            <BudgetPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BudgetPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
    skippedPotIds.clear();
  });

  it("blendet Rücklagen per persistentem Schalter kompakt ein", async () => {
    const user = userEvent.setup();
    renderBudgetPage();

    const toggle = await screen.findByRole("checkbox", {
      name: "Rücklagen in den Monatsplan einrechnen",
    });
    expect(toggle).not.toBeChecked();
    expect(
      screen.queryByRole("heading", { name: /wirklich frei bleibt/i }),
    ).not.toBeInTheDocument();

    await user.click(toggle);
    expect(await screen.findByText("Wirklich frei nach Rücklagen")).toBeInTheDocument();
    expect(screen.getByText("Empfohlene Rücklagen")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /wirklich frei bleibt/i }),
    ).not.toBeInTheDocument();
    const reservesLink = screen.getByRole("link", { name: "Rücklagen öffnen" });
    expect(reservesLink).toHaveAttribute("href", "/budget?tab=reserves&year=2026&month=8");
    expect(window.localStorage.getItem("finanzplaner:budget:include-goal-reserves")).toBe("true");
  });

  it("öffnet den Rücklagen-Tab direkt und bestätigt eine Teilzahlung", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("finanzplaner:budget:include-goal-reserves", "true");
    renderBudgetPage({}, "/budget?tab=reserves&year=2026&month=8");

    expect(
      await screen.findByRole("heading", { name: "Rücklagen für August 2026" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: /wirklich frei bleibt/i }),
    ).toBeInTheDocument();
    const contractPot = screen.getByTestId("reserve-pot-contract");
    expect(within(contractPot).getByText("Besuch Schwiegermutter")).toBeInTheDocument();
    expect(within(contractPot).getByText("Teilweise")).toBeInTheDocument();
    expect(within(contractPot).getByText(/114,29.*offen/)).toBeInTheDocument();

    await user.click(within(contractPot).getByRole("button", { name: "Einzahlung" }));
    expect(await screen.findByRole("dialog", { name: /Einzahlung/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Verbindlich bestätigen" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Einzahlung wurde bestätigt"),
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/reserves/deposits"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("bestätigt offene Sollwerte erst nach editierbarer Auswahl", async () => {
    const user = userEvent.setup();
    renderBudgetPage({}, "/budget?tab=reserves&year=2026&month=8");
    await screen.findByRole("heading", { name: "Rücklagen für August 2026" });

    await user.click(screen.getByRole("button", { name: "Alle offenen Sollwerte einzahlen" }));
    const dialog = await screen.findByTestId("reserve-bulk-dialog");
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("/v1/reserves/deposits"),
      expect.objectContaining({ method: "POST" }),
    );

    await user.click(within(dialog).getByRole("checkbox", { name: /Neues Handy/ }));
    const amount = within(dialog).getByLabelText("Einzahlungsbetrag für Besuch Schwiegermutter");
    await user.clear(amount);
    await user.type(amount, "100");
    expect(within(dialog).getByText("100,00 €")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Verbindlich bestätigen" }));
    await waitFor(() => {
      const depositCall = vi
        .mocked(fetch)
        .mock.calls.find(
          ([input, init]) =>
            String(input).includes("/v1/reserves/deposits") && init?.method === "POST",
        );
      expect(depositCall).toBeDefined();
      expect(JSON.parse(String(depositCall?.[1]?.body))).toEqual(
        expect.objectContaining({
          total_minor: 10000,
          allocations: [{ pot_id: "pot-contract", amount_minor: 10000 }],
        }),
      );
    });
  });

  it("macht eine ausgesetzte Monatsentscheidung wieder rückgängig", async () => {
    const user = userEvent.setup();
    renderBudgetPage({}, "/budget?tab=reserves&year=2026&month=8");
    await screen.findByRole("heading", { name: "Rücklagen für August 2026" });
    const goalPot = screen.getByTestId("reserve-pot-goal");

    await user.click(within(goalPot).getByRole("button", { name: "Diesen Monat nicht" }));
    expect(
      await within(goalPot).findByRole("button", { name: "Auslassen rückgängig" }),
    ).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/reserves/month-decisions/skip"),
      expect.objectContaining({ method: "PUT" }),
    );

    await user.click(within(goalPot).getByRole("button", { name: "Auslassen rückgängig" }));
    expect(
      await within(goalPot).findByRole("button", { name: "Diesen Monat nicht" }),
    ).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/v1/reserves/month-decisions/skip"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("schreibt einen großen Betrag erst nach sichtbarer Verteilung", async () => {
    const user = userEvent.setup();
    renderBudgetPage({}, "/budget?tab=reserves&year=2026&month=8");
    await screen.findByRole("heading", { name: "Rücklagen für August 2026" });

    await user.type(screen.getByLabelText("Gesamtbetrag"), "20000");
    await user.click(screen.getByRole("button", { name: "Betrag zuordnen" }));
    const dialog = await screen.findByRole("dialog", { name: "Betrag explizit verteilen" });
    expect(
      within(dialog).getByText(/noch nicht zweckgebunden \/ freier Rest/i),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Betrag für Freie Rücklage")).toHaveValue("20000,00");
    expect(within(dialog).getByLabelText("Betrag für Besuch Schwiegermutter")).toHaveValue("");
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("/v1/reserves/distribution-preview"),
      expect.anything(),
    );
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("/v1/reserves/deposits"),
      expect.anything(),
    );

    await user.click(
      within(dialog).getByRole("button", {
        name: "Nach offenen/fälligen Töpfen vorschlagen",
      }),
    );
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/v1/reserves/distribution-preview"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(within(dialog).getByLabelText("Betrag für Besuch Schwiegermutter")).toHaveValue(
      "114,29",
    );
    expect(within(dialog).getByLabelText("Betrag für Freie Rücklage")).toHaveValue("19435,71");
    expect(fetch).not.toHaveBeenCalledWith(
      expect.stringContaining("/v1/reserves/deposits"),
      expect.anything(),
    );
  });

  it("rendert die Budget-Seite mit Wasserfall, 50/30/20 und Kategorietabelle", async () => {
    renderBudgetPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Budget" })).toBeInTheDocument();
    });

    await waitFor(() => {
      // Geplante Ausgaben - der Ausgabentopf, unveraendert ohne Einnahmenplan.
      expect(screen.getByText("2.500,00 €")).toBeInTheDocument();
    });

    // Reihenfolge des roten Fadens: Wasserfall, dann 50/30/20, dann Kategorien.
    expect(screen.getByTestId("budget-flow")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /50\/30\/20/ })).toBeInTheDocument();
    expect(screen.getByText("Fixkosten")).toBeInTheDocument();
    expect(screen.getByText("Miete")).toBeInTheDocument();
  });

  it("weist Einnahmen getrennt von den Ausgaben aus", async () => {
    renderBudgetPage();

    await waitFor(() => {
      expect(screen.getByText("Gehalt")).toBeInTheDocument();
    });

    // Das Gehalt erscheint als Einnahme-Ist (3.000,00 EUR) und darf weder im
    // gebuchten Ausgabenblock noch in den 50/30/20-Buckets auftauchen.
    expect(screen.getAllByText("3.000,00 €").length).toBeGreaterThan(0);
    expect(screen.getByText("1.200,00 €")).toBeInTheDocument(); // Gebucht (Ausgaben)
    // Rest = Plan + Uebertrag - Ist, der Uebertrag fliesst also mit ein.
    expect(screen.getByText("1.350,00 €")).toBeInTheDocument();
  });

  it("zeigt den Planbetrag einer Einnahmekategorie in der Gruppenzeile", async () => {
    renderBudgetPage();

    // Der gemeldete Fehler: 6.899,00 EUR Plan auf "Gehalt" wirkten sich
    // nirgends aus, weil sie nicht in total_planned_minor stecken. Die
    // Gruppenzeile liest sie jetzt aus den *_income_*-Summen.
    const incomeGroup = await screen.findByTestId("budget-group-grp-2");
    expect(incomeGroup).toHaveTextContent("6.899,00 €"); // Plan
    expect(incomeGroup).toHaveTextContent("3.000,00 €"); // Ist
    expect(incomeGroup).toHaveTextContent("3.899,00 €"); // noch ausstehend
    expect(within(incomeGroup).getAllByText("Einnahmen").length).toBeGreaterThan(0);

    // Der Ausgabentopf bleibt davon unberuehrt: Plan 1.000,00, Ist 800,00,
    // Rest 250,00 - kein Gehalt darin.
    const expenseGroup = screen.getByTestId("budget-group-grp-1");
    expect(expenseGroup).toHaveTextContent("1.000,00 €");
    expect(expenseGroup).toHaveTextContent("250,00 €");
    expect(expenseGroup).not.toHaveTextContent("6.899,00 €");
  });

  it("zeigt Fixkostenquote und Vertragszahlen benannt in der Fixkostenzeile", async () => {
    renderBudgetPage();

    await waitFor(() => {
      expect(screen.getByText("Fixkostenquote")).toBeInTheDocument();
    });

    expect(screen.getByTestId("budget-fixed-costs-ratio")).toHaveTextContent("58.3 %");
    // 50 bis 70 Prozent ist die mittlere Stufe der Ampel.
    expect(screen.getByText("Angespannt")).toBeInTheDocument();

    // Beide Vertragszahlen stehen weiterhin auf der Seite, aber nicht mehr als
    // zusammenhanglose Kacheln: 1.000,00 ist der Abzug der Kette (nur
    // regelmaessige Vertraege), 1.750,00 die Quotenbasis (alle Vertraege
    // normiert), 1.250,00 der Rest daraus.
    const fixedRow = screen.getByTestId("budget-flow-deduction-fixed");
    expect(fixedRow).toHaveTextContent("1.000,00 €");
    expect(fixedRow).toHaveTextContent("1.750,00 €");
    expect(fixedRow).toHaveTextContent("1.250,00 €");
    expect(screen.getByText(/Bezug: gebuchte Einnahmen/)).toBeInTheDocument();

    // Die gebuchten Zahlen bleiben unberuehrt - Vertraege sind Erwartungen und
    // duerfen nicht in die Plan/Ist-Rechnung sickern.
    expect(screen.getByText("1.200,00 €")).toBeInTheDocument();
    expect(screen.getByText("1.350,00 €")).toBeInTheDocument();
  });

  it("führt den Wasserfall ohne Rücklagen nur bis Stufe 2", async () => {
    const user = userEvent.setup();
    renderBudgetPage();

    // Stufe 1 nennt neben der Erwartung auch Plan, Ist und Rest der Einnahmen.
    const income = await screen.findByTestId("budget-flow-stage-income");
    expect(income).toHaveTextContent("3.000,00 €");
    expect(income).toHaveTextContent("6.899,00 €");

    // 3.000,00 - 1.000,00 regelmaessige Fixkosten = 2.000,00. Danach ist
    // Schluss, solange die Ruecklagen nicht eingerechnet werden.
    expect(screen.getByTestId("budget-flow-stage-after-fixed")).toHaveTextContent("2.000,00 €");
    expect(screen.queryByTestId("budget-flow-stage-surplus")).not.toBeInTheDocument();
    expect(screen.queryByTestId("budget-flow-stage-truly-free")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("checkbox", { name: "Rücklagen in den Monatsplan einrechnen" }),
    );

    // Mit Ruecklagen laeuft die Kette weiter: - 200,00 Fixkostenruecklagen
    // - 300,00 variable Planbetraege = 1.500,00, - 450,00 Sparziele = 1.050,00.
    expect(await screen.findByTestId("budget-flow-stage-surplus")).toHaveTextContent("1.500,00 €");
    expect(screen.getByTestId("budget-flow-stage-truly-free")).toHaveTextContent("1.050,00 €");
    expect(screen.getByText("Wirklich frei nach Rücklagen")).toBeInTheDocument();
  });

  it("nennt bei 50/30/20 Ziel, Plan und Ist als drei getrennte Zahlen", async () => {
    renderBudgetPage();

    const table = await screen.findByRole("table", { name: /Je Topf das Ziel/ });
    expect(within(table).getByRole("columnheader", { name: "Ziel" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Plan" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Ist" })).toBeInTheDocument();

    // Genau die Frage des Nutzers ("woher kommt Plan 350,00?"): Ziel sind 20 %
    // von 3.000,00 = 600,00, Plan sind die 350,00 aus den Sparkategorien,
    // Ist ist 0,00 - daher -250,00 zum Ziel und -350,00 zum Plan.
    const savings = within(table).getByRole("row", { name: /Sparen & Tilgen/ });
    expect(savings).toHaveTextContent("600,00 €");
    expect(savings).toHaveTextContent("350,00 €");
    expect(savings).toHaveTextContent("−250,00 €");
    expect(savings).toHaveTextContent("−350,00 €");

    // Und die Fussnote sagt, wie der Planwert zustande kommt.
    expect(screen.getByText(/Summe der Planbeträge aller Ausgabenkategorien/)).toBeInTheDocument();
    // Der Ist-Anteilsbalken bleibt daneben stehen, aber als andere Frage.
    expect(screen.getByTestId("budget-503020-distribution")).toBeInTheDocument();
  });

  it("schaltet die Jahresmatrix zwischen Ist und Plan um und summiert je Gruppe", async () => {
    const user = userEvent.setup();
    renderBudgetPage({}, "/budget?tab=matrix&year=2026&month=8");

    // Ist: Miete 800,00; Gruppe "Fixkosten" 800,00 + 480,00 = 1.280,00.
    expect(await screen.findByTestId("budget-matrix-cell-cat-1-8")).toHaveTextContent("800");
    const groupRow = () => screen.getByRole("button", { name: /Fixkosten/ }).closest("tr")!;
    expect(within(groupRow()).getByTitle(/Fixkosten · August 2026/)).toHaveTextContent("1.280");

    await user.click(screen.getByTestId("budget-matrix-mode-planned"));

    // Plan: nur die Miete hat einen Planbetrag, also 1.000,00 in beiden Zeilen.
    expect(screen.getByTestId("budget-matrix-cell-cat-1-8")).toHaveTextContent("1.000");
    expect(within(groupRow()).getByTitle(/Fixkosten · August 2026/)).toHaveTextContent("1.000");

    // Die Nullzeile bleibt ausgeblendet und wird gezaehlt.
    expect(screen.queryByText("Nie genutzt")).not.toBeInTheDocument();
    expect(screen.getByText(/1 ausgeblendet/)).toBeInTheDocument();
  });

  it("malt eine Quote ohne Einnahmenbasis nicht grün", async () => {
    // Das Backend liefert 0 %, wenn es weder gebuchte Einnahmen noch einen
    // Einnahmevertrag gibt. Gruen waere hier die gefaehrlichste Antwort: sie
    // behauptet Entwarnung, wo schlicht nichts bekannt ist.
    renderBudgetPage({
      total_income_minor: 0,
      income_basis_minor: 0,
      fixed_costs_ratio_percent: 0,
      fixed_costs_income_basis: "contract",
      available_after_fixed_minor: -175000,
    });

    await waitFor(() => {
      expect(screen.getByText("Fixkostenquote")).toBeInTheDocument();
    });

    expect(screen.getByTestId("budget-fixed-costs-ratio")).toHaveTextContent("—");
    expect(screen.getByText("Ohne Einnahmen")).toBeInTheDocument();
    expect(screen.queryByText("Gut")).not.toBeInTheDocument();
  });

  it("filtert Kategorien in drei Stufen: diesen Monat, jemals, alle", async () => {
    const user = userEvent.setup();
    renderBudgetPage();

    await waitFor(() => {
      expect(screen.getByText("Miete")).toBeInTheDocument();
    });

    // Hausratversicherung hat weder Plan noch Ist noch Vormonat - einzig der
    // in diesem Monat faellige Vertrag (Forecast) haelt sie sichtbar.
    expect(screen.getByText("Hausratversicherung")).toBeInTheDocument();
    // Der Rundfunkbeitrag hat DIESEN Monat nichts (weder Plan noch Uebertrag
    // noch Ist noch Forecast) und steht deshalb nicht in der Tabelle.
    expect(screen.queryByText("Rundfunkbeitrag")).not.toBeInTheDocument();
    expect(screen.queryByText("Nie genutzt")).not.toBeInTheDocument();
    expect(screen.getByText(/2 ausgeblendet/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Jemals" }));

    // "Jemals" holt den Rundfunkbeitrag zurueck (letzten Monat gebucht),
    // laesst die komplett unberuehrte Kategorie aber weiterhin aussen vor.
    expect(screen.getByText("Rundfunkbeitrag")).toBeInTheDocument();
    expect(screen.queryByText("Nie genutzt")).not.toBeInTheDocument();
    expect(screen.getByText(/1 ausgeblendet/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Alle" }));

    // Jetzt auch die Kategorie ohne jede Historie: ohne sie liesse sich kein
    // neuer Planbetrag vergeben.
    expect(screen.getByText("Nie genutzt")).toBeInTheDocument();
    expect(screen.queryByText(/ausgeblendet/)).not.toBeInTheDocument();
    // Die Gruppensumme kommt vom Backend und bleibt vom Filter unberuehrt.
    expect(screen.getByText("Fixkosten")).toBeInTheDocument();
  });

  it("zeigt Vormonat-Ist und Vertrags-Forecast als eigene Spalten", async () => {
    renderBudgetPage();

    await waitFor(() => {
      expect(screen.getByText("Miete")).toBeInTheDocument();
    });

    expect(screen.getByText("Vormonat")).toBeInTheDocument();
    expect(screen.getByText("Erwartet")).toBeInTheDocument();
    // Vormonat war guenstiger als diesen Monat - eigene, unverwechselbare Zahl.
    expect(screen.getByText("750,00 €")).toBeInTheDocument();
    // Forecast (Vertrag war diesen Monat faellig) und Ist (bereits gebucht)
    // stimmen ueberein - genau der Fall, in dem beide Spalten zusammen
    // bestaetigen, dass alles wie erwartet lief, statt sich zu widersprechen.
    expect(screen.getAllByText("800,00 €").length).toBeGreaterThanOrEqual(2);
  });

  it("öffnet beim Klick auf die Vormonat-Zelle den Buchungs-Drilldown für den Vormonat", async () => {
    const user = userEvent.setup();
    renderBudgetPage();

    await waitFor(() => {
      expect(screen.getByText("Miete")).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole("button", { name: "Buchungen des Vormonats zu Miete anzeigen" }),
    );

    // Budget-Monat des Mocks ist August 2026 - der Drilldown muss auf Juli
    // wechseln, nicht auf den aktuell angezeigten Monat.
    expect(
      await screen.findByRole("heading", {
        name: `Miete · ${formatMonthYear(new Date(2026, 6, 1))}`,
      }),
    ).toBeInTheDocument();

    // Hausratversicherung hat im Vormonat nichts (prev_month_actual_minor 0,
    // zeigt "—") - die Zelle darf dafuer nicht klickbar sein.
    expect(
      screen.queryByRole("button", { name: /Buchungen des Vormonats zu Hausratversicherung/ }),
    ).not.toBeInTheDocument();
  });

  it("erklärt die Erwartet-Spalte per Hover mit Vertragsname, Betrag und Fälligkeit", async () => {
    renderBudgetPage();

    await waitFor(() => {
      expect(screen.getByText("Miete")).toBeInTheDocument();
    });

    // Ueber formatMinorUnitsAsEuro gebaut statt hartkodiert: Intl.NumberFormat
    // setzt zwischen Betrag und "€" ein geschuetztes Leerzeichen (U+00A0),
    // kein normales - ein woertlicher String daneben wuerde nie exakt matchen.
    expect(screen.getByTestId("budget-forecast-cat-1")).toHaveAttribute(
      "title",
      `Vermieter Musterstraße: ${formatMinorUnitsAsEuro(80000)} · fällig 01.08.2026`,
    );
  });

  it("übernimmt beim Klick nur leere Planbeträge aus dem Vormonat", async () => {
    const user = userEvent.setup();
    // Vormonat (Juli): Miete war schon gefuellt (bleibt beim Kopieren
    // unberuehrt, da diesen Monat bereits ein Planbetrag steht), der
    // Rundfunkbeitrag hatte einen Planbetrag (diesen Monat 0 - wird
    // uebernommen), die uebrigen waren auch im Vormonat leer.
    const prevSummary = {
      ...mockBudgetSummary,
      year: 2026,
      month: 7,
      groups: [
        {
          ...mockBudgetSummary.groups[0],
          categories: [
            { ...mockBudgetSummary.groups[0]!.categories[0]!, planned_minor: 100000 },
            { ...mockBudgetSummary.groups[0]!.categories[1]!, planned_minor: 1836 },
            { ...mockBudgetSummary.groups[0]!.categories[2]!, planned_minor: 0 },
            { ...mockBudgetSummary.groups[0]!.categories[3]!, planned_minor: 0 },
          ],
        },
        {
          ...mockBudgetSummary.groups[1],
          categories: [{ ...mockBudgetSummary.groups[1]!.categories[0]!, planned_minor: 300000 }],
        },
      ],
    };

    let capturedBody: { allocations: unknown[] } | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (method === "PUT" && url.includes("/allocations")) {
          capturedBody = JSON.parse(init!.body as string);
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockBudgetSummary),
          });
        }
        if (url.includes("/v1/budgets/2026/7")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(prevSummary),
          });
        }
        if (url.includes("/v1/budgets/")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockBudgetSummary),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }),
    );

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <HouseholdProvider>
              <BudgetPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Miete")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Werte aus Vormonat übernehmen" }));

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody).toEqual({
      allocations: [{ category_id: "cat-3", planned_minor: 1836, rollover_rule: "none" }],
    });
    expect(await screen.findByText(/1 Planbetrag aus dem Vormonat übernommen/)).toBeInTheDocument();
  });
});
