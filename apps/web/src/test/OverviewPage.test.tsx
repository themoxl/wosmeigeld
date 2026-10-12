import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { OverviewPage } from "../pages/OverviewPage";

// docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt 11.7: Uebersichtskarte
// mit aktiven Vorhaben, naechster grosser Zahlung, gesicherter Luecke und
// wichtigster Warnung. Eigener Endpunkt (`/v1/projects/dashboard-summary`),
// eigene Kennzahlenreihe unter den Fixkosten - nicht vermischt mit den
// Ist-Kennzahlen des Berichts oben (siehe Kommentar in OverviewPage.tsx).

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

const overviewReport = {
  current_year: 2026,
  current_month: 8,
  total_income_minor: 400000,
  total_expense_minor: 250000,
  net_savings_minor: 150000,
  savings_rate_percent: 37.5,
  budget_remaining_minor: 50000,
  open_review_count: 0,
  top_expense_categories: [],
  cashflow_last_6_months: [],
};

function renderOverviewPage(dashboardSummary: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/reports/overview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(overviewReport),
        });
      }
      if (url.includes("/v1/projects/dashboard-summary")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(dashboardSummary),
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
            <OverviewPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OverviewPage – Vorhaben-Uebersichtskarte", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt aktive Vorhaben, naechste Zahlung, gesicherte Luecke und wichtigste Warnung", async () => {
    renderOverviewPage({
      active_project_count: 2,
      next_payment: {
        project_id: "project-b",
        project_name: "Fenster",
        cost_item_name: "Fenstereinbau",
        due_on: "2027-02-01",
        amount_minor: 3000000,
      },
      total_secured_gap_minor: 20000000,
      top_warning: {
        project_id: "project-a",
        project_name: "Rohbau",
        code: "secured_funding_gap",
      },
    });

    await waitFor(() => {
      expect(screen.getByText("Aktive Vorhaben")).toBeInTheDocument();
    });
    expect(screen.getByText("2")).toBeInTheDocument();

    expect(screen.getByText("Nächste große Zahlung")).toBeInTheDocument();
    expect(screen.getByText("30.000,00 €")).toBeInTheDocument();
    expect(screen.getByText(/Fenster.*Fenstereinbau.*2027-02-01/)).toBeInTheDocument();

    expect(screen.getByText("Gesicherte Lücke")).toBeInTheDocument();
    expect(screen.getByText("200.000,00 €")).toBeInTheDocument();

    expect(screen.getByText("Wichtigste Warnung")).toBeInTheDocument();
    expect(
      screen.getByText("Gesicherte Finanzierung deckt die Projektkosten noch nicht."),
    ).toBeInTheDocument();
    expect(screen.getByText("Rohbau")).toBeInTheDocument();
  });

  it("zeigt Platzhalter, wenn es keine offene Zahlung oder Warnung gibt", async () => {
    renderOverviewPage({
      active_project_count: 0,
      next_payment: null,
      total_secured_gap_minor: 0,
      top_warning: null,
    });

    await waitFor(() => {
      expect(screen.getByText("Aktive Vorhaben")).toBeInTheDocument();
    });
    expect(screen.getByText("Keine geplante Zahlung offen")).toBeInTheDocument();
    expect(screen.getByText("Keine offenen Warnungen")).toBeInTheDocument();
  });

  it("blendet die Vorhabenkarte defensiv aus, wenn die Antwort keine gueltigen Kennzahlen enthaelt", async () => {
    // Wie hasFixedCostsFields: eine leere/veraltete Antwort darf die Seite
    // nicht mit undefined-Zugriffen abschiessen, sondern die Karte faellt
    // einfach weg. Der Standard-Mock liefert fuer unbekannte Endpunkte "[]".
    renderOverviewPage([]);

    await waitFor(() => {
      expect(screen.getByText("Einnahmen")).toBeInTheDocument();
    });
    expect(screen.queryByText("Aktive Vorhaben")).not.toBeInTheDocument();
  });
});
