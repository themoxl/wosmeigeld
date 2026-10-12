import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { InsightsPage } from "../pages/InsightsPage";

// Die Seite hat zwei Zusagen, die hier gepruefte werden:
//
// 1. Der Bericht steht ohne Modell vollstaendig da; die KI-Formulierung ist
//    ein ausdruecklich angeforderter Zusatz und laeuft nicht beim Oeffnen mit.
// 2. Jede Empfehlung fuehrt zu den Buchungen dahinter - eine Kennzahl ohne
//    diesen Weg waere eine Behauptung.

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
      onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
      signInWithPassword: vi.fn(),
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

const report = {
  from_date: "2025-08-01",
  to_date: "2026-07-31",
  months_covered: 12,
  monthly_income_minor: 690000,
  monthly_expense_minor: 640000,
  monthly_net_minor: 50000,
  monthly_potential_minor: 12500,
  model_name: null,
  insights: [
    {
      key: "category_trend:cat-restaurant",
      kind: "category_trend",
      severity: "opportunity",
      title: "Restaurant: 125,00 € pro Monat mehr als sonst",
      detail: "In den letzten 3 Monaten waren es im Schnitt 275,00 € pro Monat, davor 150,00 €.",
      amount_minor: 27500,
      monthly_potential_minor: 12500,
      category_id: "cat-restaurant",
      category_name: "Restaurant",
      evidence: [
        { label: "Ø letzte 3 Monate", amount_minor: 27500, count: null },
        { label: "Ø davor (9 Monate)", amount_minor: 15000, count: null },
      ],
      drilldown: {
        field: "category",
        value: "cat-restaurant",
        from_date: "2025-08-01",
        to_date: "2026-07-31",
        direction: "expense",
      },
      ai_advice: null,
    },
  ],
};

const drilldownResult = {
  grain: "rows",
  columns: [
    { key: "date", label: "Datum", kind: "date", is_money: false },
    { key: "payee", label: "Zahlungsempfänger", kind: "cat", is_money: false },
    { key: "amount", label: "Betrag", kind: "num", is_money: true },
  ],
  rows: [
    {
      date: "2026-07-14",
      payee: "Trattoria Roma",
      amount: -4200,
      split_id: "split-1",
      transaction_id: "tx-1",
    },
  ],
  row_count: 1,
  total_row: null,
  applied_limits: [],
  period_from: "2025-08-01",
  period_to: "2026-07-31",
  months_covered: 12,
};

function renderInsightsPage(aiReport?: Record<string, unknown>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    void init;
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
    }
    if (url.includes("/v1/insights")) {
      const withAi = url.includes("with_ai=true");
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve(
            withAi && aiReport
              ? aiReport
              : withAi
                ? {
                    ...report,
                    model_name: "test-model",
                    insights: report.insights.map((item) => ({
                      ...item,
                      ai_advice: "Koch an zwei Abenden pro Woche selbst.",
                    })),
                  }
                : report,
          ),
      });
    }
    if (url.includes("/v1/analytics")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(drilldownResult),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <InsightsPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

describe("InsightsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt Kennzahlen und Empfehlungen ohne KI-Lauf", async () => {
    const fetchMock = renderInsightsPage();

    expect(
      await screen.findByRole("heading", { name: "Restaurant: 125,00 € pro Monat mehr als sonst" }),
    ).toBeInTheDocument();
    expect(screen.getByText("12 Monate", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("125,00 € / Monat")).toBeInTheDocument();
    expect(screen.getByText("Chance")).toBeInTheDocument();

    // Der teure Modelllauf startet nicht von selbst.
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("with_ai=true"))).toBe(
      false,
    );
    expect(screen.queryByText("KI-Rat")).not.toBeInTheDocument();
  });

  it("holt die KI-Formulierung erst auf Anforderung und lässt die Zahlen unverändert", async () => {
    const user = userEvent.setup();
    const fetchMock = renderInsightsPage();

    await screen.findByRole("heading", { name: /Restaurant/ });
    await user.click(screen.getByRole("button", { name: "Von der KI kommentieren lassen" }));

    expect(await screen.findByText("KI-Rat")).toBeInTheDocument();
    expect(screen.getByText("Koch an zwei Abenden pro Woche selbst.")).toBeInTheDocument();
    // Dieselbe Kernzahl wie vorher - die Formulierung ist ein Zusatz, keine
    // zweite Rechnung.
    expect(screen.getByText("125,00 € / Monat")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("with_ai=true"))).toBe(
      true,
    );
  });

  it("benennt eine fehlende KI-Formulierung samt Grund statt sie still wegzulassen", async () => {
    const user = userEvent.setup();
    renderInsightsPage({
      ...report,
      model_name: null,
      ai_warnings: [
        {
          code: "model_missing",
          message:
            "Das eingestellte Modell ist auf dem Modellserver nicht vorhanden. Es wird bewusst kein anderes ersatzweise verwendet.",
          level: "fehler",
        },
      ],
    });

    await screen.findByRole("heading", { name: /Restaurant/ });
    await user.click(screen.getByRole("button", { name: "Von der KI kommentieren lassen" }));

    const notice = await screen.findByText(/KI-Formulierung nicht verfügbar:/);
    expect(notice).toHaveTextContent("Das eingestellte Modell ist auf dem Modellserver");
    expect(notice).toHaveTextContent("Modell in LM Studio laden.");
    expect(notice).not.toHaveTextContent("model_missing");
    // Die gerechneten Empfehlungen bleiben vollstaendig stehen.
    expect(screen.getByText("125,00 € / Monat")).toBeInTheDocument();
    expect(screen.queryByText("KI-Rat")).not.toBeInTheDocument();
  });

  it("öffnet zu jeder Empfehlung die Buchungen dahinter", async () => {
    const user = userEvent.setup();
    const fetchMock = renderInsightsPage();

    await screen.findByRole("heading", { name: /Restaurant/ });
    await user.click(screen.getByRole("button", { name: "Buchungen ansehen" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Trattoria Roma")).toBeInTheDocument();

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([input]) => String(input).includes("/v1/analytics"));
      expect(call).toBeDefined();
      const body = JSON.parse(String((call![1] as RequestInit).body)) as {
        grain: string;
        period: { from_date: string; to_date: string };
        where: unknown;
      };
      // Die Detailliste entsteht aus derselben Abfrage wie die Summe darüber:
      // gleicher Zeitraum, gleiche Kategorie, nur Zeilen statt Aggregat.
      expect(body.grain).toBe("rows");
      expect(body.period.from_date).toBe("2025-08-01");
      expect(body.period.to_date).toBe("2026-07-31");
      expect(JSON.stringify(body.where)).toContain("cat-restaurant");
      expect(JSON.stringify(body.where)).toContain("expense");
    });
  });
});
