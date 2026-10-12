/**
 * Register "Variable Kosten" der Berichtsseite.
 *
 * Geprueft wird gegen den Tabellen-Fallback: in jsdom liefert
 * `detectCanvasSupport()` false, jedes Diagramm faellt auf seine Tabelle
 * zurueck (siehe Chart.tsx). Das ist genau die Oberflaeche, die hier zaehlt -
 * und gleichzeitig die Zusicherung, dass kein Wert nur im Canvas steht.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ReportsPage } from "../pages/ReportsPage";

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

const mockCashflowTrend = {
  months: [
    { year: 2026, month: 5, income_minor: 300000, expense_minor: 200000, net_minor: 100000 },
    { year: 2026, month: 6, income_minor: 300000, expense_minor: 250000, net_minor: 50000 },
  ],
};

// Die Kategorien ergeben in der Summe GENAU den Monatsschnitt
// (300,00 + 33,34 + 16,66 = 350,00 EUR) - dieselbe Zusage, die der
// API-Test test_categories_sum_up_to_the_monthly_average festhaelt.
const mockVariableCosts = {
  generated_on: "2026-08-15",
  months: 3,
  monthly_average_minor: 35000,
  monthly_fixed_average_minor: 100000,
  categories: [
    {
      category_id: "cat-1",
      category_name: "Lebensmittel",
      group_name: "Bedürfnisse",
      monthly_average_minor: 30000,
      share_percent: 85.7,
      transaction_count: 12,
      trend: [30000, 30000, 30000],
    },
    {
      category_id: "cat-2",
      category_name: "Restaurant",
      group_name: "Wünsche",
      monthly_average_minor: 3334,
      share_percent: 9.5,
      transaction_count: 4,
      trend: [0, 10001, 0],
    },
    {
      category_id: null,
      category_name: "(ohne Kategorie)",
      group_name: null,
      monthly_average_minor: 1666,
      share_percent: 4.8,
      transaction_count: 1,
      trend: [5000, 0, 0],
    },
  ],
  series: [
    { year: 2026, month: 5, label: "Mai 2026", variable_minor: 35000, fixed_minor: 100000 },
    { year: 2026, month: 6, label: "Jun 2026", variable_minor: 40001, fixed_minor: 100000 },
    { year: 2026, month: 7, label: "Jul 2026", variable_minor: 30000, fixed_minor: 100000 },
  ],
};

const mockRowsResult = {
  grain: "rows",
  columns: [
    { key: "date", label: "Datum", kind: "date", is_money: false },
    { key: "payee", label: "Zahlungsempfänger", kind: "cat", is_money: false },
    { key: "amount", label: "Betrag", kind: "num", is_money: true },
  ],
  rows: [
    {
      date: "2026-06-14",
      payee: "Edeka",
      amount: -4200,
      split_id: "split-1",
      transaction_id: "tx-1",
    },
  ],
  total_row: null,
  row_count: 1,
  applied_limits: [],
  period_from: "2026-05-01",
  period_to: "2026-07-31",
  months_covered: 3,
};

function analyticsBodies() {
  return (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
    .filter((call) => String(call[0]).includes("/v1/analytics"))
    .map((call) => JSON.parse(String((call[1] as RequestInit).body)) as Record<string, unknown>)
    .filter((body) => body.grain === "rows");
}

function renderReportsPage(variableCosts: unknown = mockVariableCosts) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/reports/variable-costs")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(variableCosts),
        });
      }
      if (url.includes("/v1/reports/cashflow-trend")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockCashflowTrend),
        });
      }
      if (url.includes("/v1/analytics")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockRowsResult),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <ReportsPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openRegister() {
  renderReportsPage();
  await waitFor(() => {
    expect(screen.getByRole("tab", { name: "Variable Kosten" })).toBeInTheDocument();
  });
  await userEvent.click(screen.getByRole("tab", { name: "Variable Kosten" }));
  await waitFor(() => {
    expect(screen.getByRole("heading", { name: "Variable Kosten nach Kategorie" })).toBeVisible();
  });
}

describe("Register Variable Kosten", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stellt variable Kosten den Fixkosten gegenüber", async () => {
    await openRegister();

    // 350,00 EUR variabel gegen 1.000,00 EUR fix - das ist die Aussage, wegen
    // der es diesen Bericht gibt.
    expect(screen.getAllByText("350,00 €").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1.000,00 €").length).toBeGreaterThan(0);
    // 350 von 1.350 EUR Gesamtkosten.
    expect(screen.getByText("25.9 %")).toBeInTheDocument();

    // Der Zeitraum nennt ausdruecklich vollstaendige Monate; der laufende
    // Monat gehoert nicht dazu.
    expect(screen.getByTestId("report-scope")).toHaveTextContent("vollständige Monate");
  });

  it("zeigt die Monatsreihe als tabellarische Entsprechung des Diagramms", async () => {
    await openRegister();

    expect(screen.getByText("Mai 2026")).toBeInTheDocument();
    expect(screen.getByText("Jun 2026")).toBeInTheDocument();
    expect(screen.getByText("Jul 2026")).toBeInTheDocument();
    // Variabel + fix desselben Monats: 400,01 + 1.000,00 EUR.
    expect(screen.getByText("400,01 €")).toBeInTheDocument();
    expect(screen.getByText("1.400,01 €")).toBeInTheDocument();
  });

  it("führt die Kategorien absteigend auf und summiert sie zum Monatsschnitt", async () => {
    await openRegister();

    const table = screen.getByRole("table", { name: "Variable Kosten nach Kategorie" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getAllByRole("cell")[0]?.textContent)).toEqual([
      "Lebensmittel · Bedürfnisse",
      "Restaurant · Wünsche",
      "(ohne Kategorie)",
    ]);

    expect(within(table).getByText("300,00 €")).toBeInTheDocument();
    expect(within(table).getByText("33,34 €")).toBeInTheDocument();
    expect(within(table).getByText("16,66 €")).toBeInTheDocument();
    expect(within(table).getByText("85.7 %")).toBeInTheDocument();
    expect(within(table).getByText("12")).toBeInTheDocument();
  });

  it("öffnet aus einer Kategoriezeile die Buchungen des ganzen Zeitraums", async () => {
    await openRegister();

    await userEvent.click(screen.getByRole("button", { name: "Lebensmittel" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Edeka")).toBeInTheDocument();

    const detail = analyticsBodies().at(-1);
    // Erster Tag des aeltesten bis letzter Tag des juengsten Monats der Reihe.
    expect(detail?.period).toMatchObject({ from_date: "2026-05-01", to_date: "2026-07-31" });
    // Dieselbe Abgrenzung wie die Summe darueber: die Cashflow-Rolle, nicht
    // das Vorzeichen.
    expect(detail?.where).toEqual({
      all_of: [
        { condition: { field: "category", op: "eq", value: "cat-1" } },
        { condition: { field: "cashflow_role", op: "eq", value: "expense" } },
      ],
    });
  });

  it("behandelt „ohne Kategorie“ als eigene Zeile mit is_null-Drilldown", async () => {
    await openRegister();

    await userEvent.click(screen.getByRole("button", { name: "(ohne Kategorie)" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    const detail = analyticsBodies().at(-1);
    expect(detail?.where).toEqual({
      all_of: [
        { condition: { field: "category", op: "is_null", value: null } },
        { condition: { field: "cashflow_role", op: "eq", value: "expense" } },
      ],
    });
  });

  it("meldet einen leeren Zeitraum, statt eine leere Tabelle zu zeigen", async () => {
    renderReportsPage({
      ...mockVariableCosts,
      monthly_average_minor: 0,
      categories: [],
      series: [
        { year: 2026, month: 5, label: "Mai 2026", variable_minor: 0, fixed_minor: 0 },
        { year: 2026, month: 6, label: "Jun 2026", variable_minor: 0, fixed_minor: 0 },
        { year: 2026, month: 7, label: "Jul 2026", variable_minor: 0, fixed_minor: 0 },
      ],
      monthly_fixed_average_minor: 0,
    });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Variable Kosten" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Variable Kosten" }));

    await waitFor(() => {
      expect(screen.getByText("Keine variablen Ausgaben im gewählten Zeitraum.")).toBeVisible();
    });
  });
});
