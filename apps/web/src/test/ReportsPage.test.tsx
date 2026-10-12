import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ReportsPage } from "../pages/ReportsPage";

// docs/ANALYSE-EXPLORER-PLAN.md, Baustein E. Diagramme rendern auf Canvas und
// sind in jsdom nicht pruefbar - deshalb hat jedes Diagramm einen Umschalter
// "Als Tabelle anzeigen". Der ist gleichzeitig Barrierefreiheit und die
// Testoberflaeche; genau darueber laufen diese Tests.

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

// Quelle des Fixkostenregisters ist contract_amount_history, also der
// VEREINBARTE Betrag - nicht der gebuchte. Deshalb kann der Analyse-Explorer
// diese Zahlen nicht liefern und sie stimmen bewusst nicht mit dem Cashflow
// oben ueberein.
const mockFixedCostsTrend = {
  months: [
    {
      year: 2025,
      month: 7,
      fixed_costs_minor: 50000,
      contract_count: 2,
      yoy_percent: null,
      by_type: [
        { contract_type: "telecom", amount_minor: 35000 },
        { contract_type: "insurance", amount_minor: 15000 },
      ],
    },
    {
      year: 2026,
      month: 7,
      fixed_costs_minor: 60000,
      contract_count: 3,
      yoy_percent: 20,
      by_type: [
        { contract_type: "telecom", amount_minor: 40000 },
        { contract_type: "insurance", amount_minor: 20000 },
      ],
    },
  ],
};

/** Antwort des Analyse-Endpunkts fuer eine Gruppierung nach Kategorie. */
const mockCategoryResult = {
  grain: "aggregate",
  columns: [
    { key: "category", label: "Kategorie", kind: "cat", is_money: false },
    { key: "category_label", label: "Kategorie (Name)", kind: "cat", is_money: false },
    { key: "sum_amount", label: "Summe Betrag", kind: "num", is_money: true },
    { key: "avg_per_month_amount", label: "Ø/Monat Betrag", kind: "num", is_money: true },
    { key: "count_amount", label: "Anzahl Betrag", kind: "num", is_money: false },
  ],
  rows: [
    // "date:month" traegt die Monatsachse der Matrix. Dieselbe Antwort
    // bedient im Test alle Aggregatabfragen; die reinen Kategorietabellen
    // ignorieren die Spalte.
    {
      category_group: "group-household",
      category_group_label: "Haushalt & Alltag",
      category: "cat-1",
      category_label: "Lebensmittel",
      "date:month": "2026-06-01",
      sum_amount: -73500,
      avg_per_month_amount: -6125,
      count_amount: 17,
    },
    {
      category_group: "group-leisure",
      category_group_label: "Freizeit",
      category: "cat-2",
      category_label: "Restaurant",
      "date:month": "2026-06-01",
      sum_amount: -12000,
      avg_per_month_amount: -1000,
      count_amount: 3,
    },
  ],
  total_row: { sum_amount: -85500 },
  row_count: 2,
  truncated: false,
  applied_limits: [],
  next_cursor: null,
  query_id: "abc123",
  period_from: "2025-07-01",
  period_to: "2026-06-30",
  months_covered: 12,
};

const mockGroupResult = {
  ...mockCategoryResult,
  rows: [
    {
      category_group: "group-household",
      category_group_label: "Haushalt & Alltag",
      "date:month": "2026-05-01",
      sum_amount: -50000,
      count_amount: 10,
    },
    {
      category_group: "group-household",
      category_group_label: "Haushalt & Alltag",
      "date:month": "2026-06-01",
      sum_amount: -73500,
      count_amount: 17,
    },
  ],
  total_row: { sum_amount: -123500, count_amount: 27 },
};

const mockPayeeResult = {
  ...mockCategoryResult,
  rows: [
    {
      payee: "payee-1",
      payee_label: "Edeka",
      "date:month": "2026-05-01",
      sum_amount: -24000,
      count_amount: 4,
    },
    {
      payee: "payee-1",
      payee_label: "Edeka",
      "date:month": "2026-06-01",
      sum_amount: -31000,
      count_amount: 5,
    },
  ],
  total_row: { sum_amount: -55000, count_amount: 9 },
};

// Register "Vorhaben" (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, 11.7): eigener
// Datenpfad ueber `/v1/projects/reports-overview`, keine Analytics-Abfrage.
const mockProjectReports = [
  {
    project_id: "project-a",
    project_name: "Rohbau",
    project_type: "home_build",
    scenario_id: "scenario-a",
    scenario_name: "Basis",
    forecast_cost_minor: 20000000,
    actual_cost_minor: 5000000,
    variance_minor: 15000000,
    financing_cost_minor: 1200000,
    warnings: ["secured_funding_gap"],
  },
  {
    project_id: "project-b",
    project_name: "Fenster",
    project_type: "renovation",
    scenario_id: "scenario-b",
    scenario_name: "Basis",
    forecast_cost_minor: 3000000,
    actual_cost_minor: 3000000,
    variance_minor: 0,
    financing_cost_minor: 0,
    warnings: [],
  },
];

/** Antwort derselben Abfrage mit grain=rows - das Ziel jedes Drilldowns. */
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
      payee: "Trattoria Roma",
      amount: -4200,
      split_id: "split-1",
      transaction_id: "tx-1",
    },
  ],
  total_row: null,
  row_count: 1,
  applied_limits: [],
  period_from: "2026-06-01",
  period_to: "2026-06-30",
  months_covered: 1,
};

function renderReportsPage(cashflowTrend = mockCashflowTrend) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/reports/cashflow-trend")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(cashflowTrend),
        });
      }
      if (url.includes("/v1/reports/fixed-costs-trend")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockFixedCostsTrend),
        });
      }
      if (url.includes("/v1/projects/reports-overview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockProjectReports),
        });
      }
      if (url.includes("/v1/analytics")) {
        const body = JSON.parse(String((init as RequestInit | undefined)?.body ?? "{}")) as {
          grain?: string;
          group_by?: { field?: string }[];
        };
        const fields = (body.group_by ?? []).map((dimension) => dimension.field);
        const aggregate = fields.includes("payee")
          ? mockPayeeResult
          : fields.includes("category_group") && !fields.includes("category")
            ? mockGroupResult
            : mockCategoryResult;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(body.grain === "rows" ? mockRowsResult : aggregate),
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

describe("ReportsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt Kennzahlen aus dem Cashflow-Trend", async () => {
    renderReportsPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Berichte" })).toBeInTheDocument();
    });

    // Der jüngste vollständig abgeschlossene Monat ist der primäre Zeitraum,
    // nicht mehr die missverständliche Gesamtsumme über zwölf Monate.
    await waitFor(() => {
      expect(screen.getByText("Einnahmen · Juni 2026")).toBeInTheDocument();
    });
    expect(screen.getAllByText("3.000,00 €").length).toBeGreaterThan(0);
    expect(screen.getAllByText("-2.500,00 €").length).toBeGreaterThan(0);
    expect(screen.getByText("Sparquote · Juni 2026")).toBeInTheDocument();
  });

  it("faellt auf die tabellarische Entsprechung zurueck, wenn kein Diagramm gezeichnet werden kann", async () => {
    // In jsdom gibt es keinen Canvas-Kontext, ECharts scheitert also
    // zwangslaeufig. Genau dieser Fall wird hier geprueft: das Diagramm faellt
    // auf seine Tabelle zurueck, statt die Seite leer zu lassen.
    renderReportsPage();

    await waitFor(() => {
      expect(screen.getAllByText("Mai 2026").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("Juni 2026").length).toBeGreaterThan(0);
  });

  it("wechselt in das Kategorien-Register und zeigt die Monatsmatrix", async () => {
    renderReportsPage();

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Kategorien" })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("tab", { name: "Kategorien" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Kategorien nach Monat" })).toBeInTheDocument();
    });
    // Die Kategorietabelle des Registers zeigt aufgeloeste Namen, keine UUIDs.
    expect(screen.getAllByText("Lebensmittel").length).toBeGreaterThan(0);
  });

  it("öffnet aus einer Cashflow-Zelle die Buchungen genau dieses Monats", async () => {
    renderReportsPage();

    await waitFor(() => {
      expect(screen.getAllByText("Juni 2026").length).toBeGreaterThan(0);
    });

    await userEvent.click(screen.getByRole("button", { name: /Ausgaben, Juni 2026:.*anzeigen/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Trattoria Roma")).toBeInTheDocument();

    const rowsCall = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .filter((call) => String(call[0]).includes("/v1/analytics"))
      .map((call) => JSON.parse(String((call[1] as RequestInit).body)) as Record<string, unknown>)
      .find((body) => body.grain === "rows");
    expect(rowsCall).toBeDefined();
    // Genau der angeklickte Monat, nicht der ganze Berichtszeitraum.
    expect(rowsCall?.period).toMatchObject({ from_date: "2026-06-01", to_date: "2026-06-30" });
    expect(rowsCall?.where).toEqual({
      condition: { field: "cashflow_role", op: "eq", value: "expense" },
    });
  });

  it("öffnet aus einer Matrixzelle die Buchungen der Kategorie in diesem Monat", async () => {
    renderReportsPage();

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Kategorien" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Kategorien" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Kategorien nach Monat" })).toBeInTheDocument();
    });

    const cell = screen.getByRole("button", {
      name: /Lebensmittel, Juni 2026:.*anzeigen/,
    });
    expect(cell).toBeDefined();
    await userEvent.click(cell!);

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("klappt eine Kategorie zu Empfängern auf und öffnet deren exakten Monatsdrilldown", async () => {
    renderReportsPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Kategorien" }));

    const categoryToggle = await screen.findByRole("button", {
      name: "Lebensmittel",
    });
    await userEvent.click(categoryToggle);

    const payeeTable = await screen.findByRole("table", {
      name: "Empfänger in Lebensmittel nach Monat",
    });
    expect(within(payeeTable).getByText("Edeka")).toBeInTheDocument();
    await userEvent.click(
      within(payeeTable).getByRole("button", { name: /Edeka, Juni 2026:.*anzeigen/ }),
    );

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    const rowsCalls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .filter((call) => String(call[0]).includes("/v1/analytics"))
      .map((call) => JSON.parse(String((call[1] as RequestInit).body)) as Record<string, unknown>)
      .filter((body) => body.grain === "rows");
    const detail = rowsCalls.at(-1);
    expect(detail?.period).toMatchObject({ from_date: "2026-06-01", to_date: "2026-06-30" });
    expect(JSON.stringify(detail?.where)).toContain("cat-1");
    expect(JSON.stringify(detail?.where)).toContain("payee-1");
  });

  it("zeigt im Register Fixkosten die vereinbarte Zeitreihe je Vertragsart", async () => {
    renderReportsPage();

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Fixkosten" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Fixkosten" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Fixkosten je Monat" })).toBeInTheDocument();
    });

    // Der jüngste Monat steht als Kennzahl oben, die Reihe darunter in der
    // tabellarischen Entsprechung des Diagramms.
    expect(screen.getAllByText("600,00 €").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+20.0 %").length).toBeGreaterThan(0);
    expect(screen.getByText("07/25")).toBeInTheDocument();
    expect(screen.getByText("07/26")).toBeInTheDocument();

    // Aufgeschlüsselt nach Vertragsart, mit aufgelösten Beschriftungen statt
    // der Schlüssel aus der Datenbank.
    expect(screen.getByText("Telekommunikation")).toBeInTheDocument();
    expect(screen.getByText("Versicherung")).toBeInTheDocument();
    expect(screen.getByText("400,00 €")).toBeInTheDocument();
    // Hochgerechnet aufs Jahr: 600,00 EUR mal zwölf.
    expect(screen.getByText("7.200,00 €")).toBeInTheDocument();
  });

  it("zeigt im Register Vorhaben Forecast, Ist, Abweichung und Finanzierungskosten je Projekt", async () => {
    renderReportsPage();

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Vorhaben" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Vorhaben" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Vorhaben im Detail" })).toBeInTheDocument();
    });

    // Zwei aktive Vorhaben mit ausgewaehltem Szenario aus dem Mock.
    expect(screen.getByText("Rohbau")).toBeInTheDocument();
    expect(screen.getByText("Fenster")).toBeInTheDocument();

    // Forecast/Ist/Abweichung/Finanzierungskosten des ersten Vorhabens. Die
    // beiden letzten Werte tauchen zusaetzlich in der Summenzeile auf, weil
    // Projekt "Fenster" dort mit 0 beitraegt - deshalb getAllByText.
    expect(screen.getByText("200.000,00 €")).toBeInTheDocument();
    expect(screen.getByText("50.000,00 €")).toBeInTheDocument();
    expect(screen.getAllByText("150.000,00 €").length).toBeGreaterThan(0);
    expect(screen.getAllByText("12.000,00 €").length).toBeGreaterThan(0);

    // Summe je Spalte in der Fusszeile.
    expect(screen.getByText("Summe")).toBeInTheDocument();
  });

  it("zeigt im Register Vorhaben einen Hinweis ohne aktive Vorhaben", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (url.includes("/v1/reports/cashflow-trend")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockCashflowTrend),
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
              <ReportsPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Vorhaben" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Vorhaben" }));

    await waitFor(() => {
      expect(
        screen.getByText(/Kein aktives Vorhaben mit ausgewähltem Szenario/),
      ).toBeInTheDocument();
    });
  });

  it("kennzeichnet jede angewandte Grenze, statt still zu kürzen", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
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
            json: () =>
              Promise.resolve({
                ...mockCategoryResult,
                rows: [
                  ...mockCategoryResult.rows,
                  { category: "__rest__", category_label: "Übrige", sum_amount: -5000 },
                ],
                truncated: true,
                applied_limits: [{ kind: "top_n", limit: 2, dropped_label: "Übrige" }],
              }),
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
              <ReportsPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Kategorien" })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("tab", { name: "Kategorien" }));

    // Die Restzeile erscheint als solche benannt - eine stillschweigend
    // gekuerzte Auswertung liest sich sonst wie eine vollstaendige.
    await waitFor(() => {
      expect(screen.getAllByText("Übrige").length).toBeGreaterThan(0);
    });
  });

  it("übernimmt die Teilperiodenwarnung nicht in den fairen Jahresvergleich", async () => {
    const today = new Date();
    renderReportsPage({
      months: [
        ...mockCashflowTrend.months,
        {
          year: today.getFullYear(),
          month: today.getMonth() + 1,
          income_minor: 10000,
          expense_minor: 8000,
          net_minor: 2000,
        },
      ],
    });

    await screen.findByTestId("report-scope");
    await userEvent.click(screen.getByRole("tab", { name: "Jahresvergleich" }));
    const scope = screen.getByTestId("report-scope");
    expect(scope).toHaveTextContent("nur vollständige Monate");
    expect(scope).not.toHaveTextContent("laufend/unvollständig");
  });
});
