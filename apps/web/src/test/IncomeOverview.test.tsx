/**
 * Einnahmenauswertung nach Person und Art (Plan Stufe 6).
 *
 * Geprueft wird gegen den Tabellen-Fallback: in jsdom liefert
 * `detectCanvasSupport()` false, jedes Diagramm faellt auf seine Tabelle
 * zurueck (siehe Chart.tsx).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { IncomeOverview } from "../components/contracts/IncomeOverview";

const session = {
  access_token: "test-token",
  user: { id: "user-1", email: "test@finanzplaner.test" },
};

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session } })),
      onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
      signInWithPassword: vi.fn(),
      signUp: vi.fn(),
      signOut: vi.fn(),
    },
  },
}));

const meResponse = {
  id: "user-1",
  email: "test@finanzplaner.test",
  display_name: "Test",
  date_format: "de",
  households: [{ household_id: "hh-1", name: "Musterhaushalt", role: "owner" }],
};

const peopleResponse = [
  {
    id: "p1",
    household_id: "hh-1",
    name: "Person A",
    member_user_id: null,
    sort_order: 0,
    is_active: true,
  },
  {
    id: "p2",
    household_id: "hh-1",
    name: "Person B",
    member_user_id: null,
    sort_order: 1,
    is_active: true,
  },
];

// 300000 + 51000 = 351000 Einnahmen, davon 150000 Fixkosten, bleiben 201000.
const summaryResponse = {
  generated_on: "2026-08-14",
  monthly_income_minor: 351000,
  monthly_fixed_minor: 150000,
  by_person: [
    {
      person_id: "p1",
      person_name: "Person A",
      person_is_active: true,
      contract_count: 1,
      monthly_minor: 300000,
      by_kind: [{ income_kind: "salary", contract_count: 1, monthly_minor: 300000 }],
    },
    {
      person_id: null,
      person_name: null,
      person_is_active: true,
      contract_count: 1,
      monthly_minor: 51000,
      by_kind: [{ income_kind: "benefit", contract_count: 1, monthly_minor: 51000 }],
    },
  ],
  by_kind: [
    { income_kind: "salary", contract_count: 1, monthly_minor: 300000 },
    { income_kind: "benefit", contract_count: 1, monthly_minor: 51000 },
  ],
  trend: [
    {
      period_start: "2026-07-01",
      label: "Jul 2026",
      income_minor: 351000,
      fixed_minor: 150000,
      net_minor: 201000,
    },
    {
      period_start: "2026-08-01",
      label: "Aug 2026",
      income_minor: 351000,
      fixed_minor: 150000,
      net_minor: 201000,
    },
  ],
};

interface RequestRecord {
  url: string;
  init?: RequestInit;
}

function stubFetch(options: { createFails?: boolean } = {}): RequestRecord[] {
  const requests: RequestRecord[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      requests.push({ url, init });
      const ok = (body: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      if (/\/v1\/me(?:\?|$)/.test(url)) return ok(meResponse);
      if (url.includes("/v1/contracts/income-summary")) return ok(summaryResponse);
      if (url.includes("/v1/people") && init?.method === "POST") {
        if (options.createFails) {
          return Promise.resolve({
            ok: false,
            status: 409,
            text: () =>
              Promise.resolve(JSON.stringify({ detail: "Diesen Namen gibt es bereits." })),
          });
        }
        return ok({ ...peopleResponse[0], id: "p3", name: "Neu" });
      }
      if (url.includes("/v1/people")) return ok(peopleResponse);
      return ok([]);
    }),
  );
  return requests;
}

function renderOverview(canEdit = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/fixkosten?richtung=einnahmen"]}>
        <AuthProvider>
          <HouseholdProvider>
            <IncomeOverview canEdit={canEdit} />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IncomeOverview", () => {
  it("gruppiert Einnahmen nach Person und weist Nichtzugeordnetes aus", async () => {
    stubFetch();
    renderOverview();

    const table = await screen.findByTestId("income-by-person");
    expect(within(table).getByText("Person A")).toBeInTheDocument();
    expect(within(table).getByText("3.000,00 €")).toBeInTheDocument();
    // Eine Einnahme ohne Person verschwindet nicht, sie wird benannt.
    expect(within(table).getByText("Ohne Person")).toBeInTheDocument();
    expect(within(table).getByText("510,00 €")).toBeInTheDocument();
    // Anteil an den Gesamteinnahmen, damit die Gruppierung etwas aussagt.
    expect(within(table).getByText("85 %")).toBeInTheDocument();
  });

  it("stellt Einnahmen den Fixkosten gegenüber", async () => {
    stubFetch();
    renderOverview();

    const trend = await screen.findByTestId("income-trend");
    expect(within(trend).getAllByText("3.510,00 €")).toHaveLength(2);
    expect(within(trend).getAllByText("1.500,00 €")).toHaveLength(2);
  });

  it("legt eine Person an und meldet einen doppelten Namen im Klartext", async () => {
    const requests = stubFetch({ createFails: true });
    const user = userEvent.setup();
    renderOverview();

    await screen.findByTestId("income-by-person");
    await user.type(screen.getByLabelText("Neue Person"), "person b");
    await user.click(screen.getByRole("button", { name: "Hinzufügen" }));

    await waitFor(() => {
      expect(requests.some((r) => r.url.includes("/v1/people") && r.init?.method === "POST")).toBe(
        true,
      );
    });
    // Der Unique-Index auf lower(name) ist der Kern der Stufe - der Nutzer
    // muss den Grund lesen koennen, nicht einen Statuscode.
    expect(await screen.findByRole("alert")).toHaveTextContent("Diesen Namen gibt es bereits.");
  });

  it("blendet Schreibaktionen für Leserechte aus", async () => {
    stubFetch();
    renderOverview(false);

    await screen.findByTestId("income-by-person");
    expect(screen.queryByLabelText("Neue Person")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Umbenennen" })).not.toBeInTheDocument();
  });
});
