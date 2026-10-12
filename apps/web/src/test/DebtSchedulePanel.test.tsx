import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { DebtSchedulePanel } from "../components/projects/DebtSchedulePanel";
import type { DebtSchedule } from "../lib/types";

// Tilgungsverlauf (GET /v1/debt-plans/{id}/schedule). Der Rechenkern liefert
// Monatszeilen; geprueft wird hier die Jahresverdichtung und vor allem, dass
// eine tilgungsfreie Phase als solche sichtbar wird - genau dafuer gibt es die
// Ansicht. In jsdom kann ECharts nicht zeichnen, deshalb faellt `Chart`
// automatisch auf die Tabelle zurueck (siehe components/charts/Chart.tsx).

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

const plan = {
  id: "plan-1",
  household_id: "household-1",
  name: "KfW 261",
  project_financing_source_id: "source-1",
  version: 1,
};

// Darlehen 120.000 EUR zu 3 % p. a.: zwoelf Monate nur Zinsen (300 EUR),
// danach Annuitaet 600 EUR mit 300 EUR Tilgungsanteil.
function buildRows(): DebtSchedule["rows"] {
  const rows: DebtSchedule["rows"] = [];
  for (let month = 0; month < 12; month += 1) {
    rows.push({
      month,
      opening_balance_minor: 12_000_000,
      draw_minor: 0,
      capitalized_fee_minor: 0,
      interest_accrued_minor: 30_000,
      interest_paid_minor: 30_000,
      interest_due_carry_minor: 0,
      commitment_interest_accrued_minor: 0,
      commitment_interest_paid_minor: 0,
      scheduled_payment_minor: 30_000,
      principal_payment_minor: 0,
      special_repayment_minor: 0,
      overpayment_minor: 0,
      cash_outflow_minor: 30_000,
      closing_balance_minor: 12_000_000,
      nominal_rate_bps_annual: 300,
      rate_kind: "contracted",
      warnings: [],
    });
  }
  for (let index = 0; index < 12; index += 1) {
    const closing = 12_000_000 - (index + 1) * 30_000;
    rows.push({
      month: 12 + index,
      opening_balance_minor: closing + 30_000,
      draw_minor: 0,
      capitalized_fee_minor: 0,
      interest_accrued_minor: 30_000,
      interest_paid_minor: 30_000,
      interest_due_carry_minor: 0,
      commitment_interest_accrued_minor: 0,
      commitment_interest_paid_minor: 0,
      scheduled_payment_minor: 60_000,
      principal_payment_minor: 30_000,
      special_repayment_minor: 0,
      overpayment_minor: 0,
      cash_outflow_minor: 60_000,
      closing_balance_minor: closing,
      nominal_rate_bps_annual: 300,
      rate_kind: "contracted",
      warnings: [],
    });
  }
  return rows;
}

const schedule: DebtSchedule = {
  debt_plan_id: "plan-1",
  total_drawn_minor: 12_000_000,
  total_interest_minor: 720_000,
  total_capitalized_interest_minor: 0,
  total_commitment_interest_minor: 0,
  total_fees_minor: 0,
  total_cash_payments_minor: 1_080_000,
  ending_balance_minor: 11_640_000,
  payoff_month: null,
  peak_payment_minor: 60_000,
  first_negative_amortization_month: null,
  warnings: [],
  rows: buildRows(),
};

function buildBulletSchedule(): DebtSchedule {
  const rows: DebtSchedule["rows"] = Array.from({ length: 120 }, (_, month) => {
    const isFinal = month === 119;
    return {
      month,
      opening_balance_minor: 10_000_000,
      draw_minor: 0,
      capitalized_fee_minor: 0,
      interest_accrued_minor: 5_750,
      interest_paid_minor: 5_750,
      interest_due_carry_minor: 0,
      commitment_interest_accrued_minor: 0,
      commitment_interest_paid_minor: 0,
      scheduled_payment_minor: 5_750,
      principal_payment_minor: 0,
      special_repayment_minor: isFinal ? 10_000_000 : 0,
      overpayment_minor: 0,
      cash_outflow_minor: isFinal ? 10_005_750 : 5_750,
      closing_balance_minor: isFinal ? 0 : 10_000_000,
      nominal_rate_bps_annual: 69,
      rate_kind: "contracted",
      warnings: [],
    };
  });
  return {
    ...schedule,
    total_drawn_minor: 10_000_000,
    total_interest_minor: 690_000,
    total_cash_payments_minor: 10_690_000,
    ending_balance_minor: 0,
    payoff_month: 119,
    peak_payment_minor: 10_005_750,
    first_negative_amortization_month: null,
    rows,
  };
}

function renderPanel(scheduleResponse = schedule) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse()) });
    }
    if (url.includes("/v1/debt-plans/plan-1/schedule")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(scheduleResponse),
      });
    }
    if (url.includes("/v1/debt-plans")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([plan]) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <HouseholdProvider>
          <DebtSchedulePanel financingSourceId="source-1" financingSourceName="KfW 261" />
        </HouseholdProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("DebtSchedulePanel", () => {
  it("verdichtet den Monatsverlauf auf Jahre und weist die tilgungsfreie Zeit aus", async () => {
    renderPanel();

    // Jahr 1: zwoelf Raten a 300 EUR, davon alles Zins, keine Tilgung.
    expect((await screen.findAllByText("Jahr 1")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("12 M tilgungsfrei").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/12 tilgungsfreie Monate/).textContent?.includes("Restschuld bleibt"),
    ).toBe(true);

    // Restschuld steht am Ende von Jahr 1 unveraendert bei 120.000 EUR und
    // sinkt erst in Jahr 2 - das ist die Aussage der ganzen Ansicht.
    expect(screen.getAllByText("120.000,00 €").length).toBeGreaterThan(0);
    expect(screen.getAllByText("116.400,00 €").length).toBeGreaterThan(0);
  });

  it("zeigt Anfangsrate, Spitzenrate und Gesamtzinsen als Kennzahlen", async () => {
    renderPanel();

    expect(await screen.findByText("Anfangsrate")).toBeTruthy();
    expect(screen.getAllByText("300,00 €").length).toBeGreaterThan(0);
    expect(screen.getAllByText("600,00 €").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7.200,00 €").length).toBeGreaterThan(0);
    // Ohne Volltilgung im Horizont muss die Restschuld am Ende stehen, nicht
    // eine erfundene Tilgungsdauer.
    expect(screen.getByText("Restschuld am Ende")).toBeTruthy();
    expect(screen.getByText("Anschlussfinanzierung nötig")).toBeTruthy();
  });

  it("zeigt beim endfälligen Darlehen Schlusszahlung und keine falsche Unterzahlung", async () => {
    renderPanel(buildBulletSchedule());

    expect(await screen.findByText("Höchste Rate")).toBeTruthy();
    expect(screen.getAllByText("100.057,50 €").length).toBeGreaterThan(0);
    // Jahr 10: 12 Zinsraten à 57,50 EUR plus 100.000 EUR Schlusszahlung.
    expect(screen.getAllByText("100.690,00 €").length).toBeGreaterThan(0);
    expect(screen.queryByText(/deckt die Rate nicht einmal die Zinsen/)).toBeNull();
  });
});
