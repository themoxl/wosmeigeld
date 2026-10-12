import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { CostPlanTable } from "../components/projects/CostPlanTable";

// Ist-Kosten-Zuordnung (Plan 4.6/7.2, P4). Die Zuordnung haengt an genau
// einer Kostenposition und wird deshalb ueber den "Ist-Kosten"-Knopf je
// Positionszeile in CostPlanTable geoeffnet (siehe
// components/projects/ActualsPanel.tsx) statt ueber einen eigenen Tab.

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

const section = {
  id: "section-1",
  scenario_id: "scenario-1",
  name: "Rohbau",
  cost_class: "direct",
  sort_order: 0,
  version: 1,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const item = {
  id: "item-1",
  scenario_id: "scenario-1",
  section_id: "section-1",
  semantic_key: null,
  name: "Fundament",
  cost_kind: "cash",
  status: "committed",
  calculation_mode: "fixed",
  estimate_manual_minor: 500000,
  quantity: null,
  unit_label: null,
  unit_price_minor: null,
  percentage_rate_bps: null,
  percentage_basis: null,
  percentage_basis_item_id: null,
  quoted_amount_minor: 500000,
  committed_amount_minor: 500000,
  contingency_eligible: true,
  funding_tags: [],
  vendor_label: null,
  expected_start_on: null,
  expected_end_on: null,
  sort_order: 0,
  note: null,
  estimate_minor: 500000,
  forecast_minor: 500000,
  actual_amount_minor: 20000,
  version: 1,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const archivedActual = {
  id: "actual-archived",
  cost_item_id: "item-1",
  source: "manual",
  transaction_id: null,
  amount_minor: 20000,
  occurred_on: "2026-06-01",
  note: "Alter Beleg",
  is_archived_link: true,
  created_by: null,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

const candidate = {
  transaction_id: "txn-1",
  account_id: "account-1",
  booked_date: "2026-07-01",
  amount_minor: -50000,
  payee_label: "Baumarkt",
  note: null,
  allocated_minor: 0,
  remaining_minor: 50000,
};

interface MockActual {
  id: string;
  cost_item_id: string;
  source: string;
  transaction_id: string | null;
  amount_minor: number;
  occurred_on: string | null;
  note: string | null;
  is_archived_link: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

function renderCostPlanTable() {
  let actuals: MockActual[] = [archivedActual];
  let nextActualId = 1;

  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse()) });
    }
    if (url.endsWith("/scenarios/scenario-1/cost-sections") && method === "GET") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([section]) });
    }
    if (url.endsWith("/scenarios/scenario-1/cost-items") && method === "GET") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([{ ...item, actual_amount_minor: sumAmounts(actuals) }]),
      });
    }
    if (url.includes("/transaction-candidates")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([candidate]) });
    }
    if (url.endsWith("/cost-items/item-1/actuals") && method === "GET") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(actuals) });
    }
    if (url.endsWith("/cost-items/item-1/actuals") && method === "POST") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      if (body.amount_minor > 100000) {
        return Promise.resolve({
          ok: false,
          status: 422,
          text: () => Promise.resolve(JSON.stringify({ detail: "actuals_overbooking" })),
          json: () => Promise.resolve({ detail: "actuals_overbooking" }),
        });
      }
      const created = {
        id: `actual-new-${nextActualId++}`,
        cost_item_id: "item-1",
        source: body.source,
        transaction_id: body.transaction_id ?? null,
        amount_minor: body.amount_minor,
        occurred_on: body.occurred_on ?? null,
        note: body.note ?? null,
        is_archived_link: false,
        created_by: "user-1",
        created_at: "2026-08-04T00:00:00Z",
        updated_at: "2026-08-04T00:00:00Z",
      };
      actuals = [...actuals, created];
      return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve(created) });
    }
    if (url.includes("/cost-actuals/") && method === "DELETE") {
      const actualId = url.split("/cost-actuals/")[1];
      actuals = actuals.filter((a) => a.id !== actualId);
      return Promise.resolve({ ok: true, status: 204, json: () => Promise.resolve(undefined) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  function sumAmounts(list: typeof actuals) {
    return list.reduce((sum, a) => sum + a.amount_minor, 0);
  }

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <HouseholdProvider>
          <CostPlanTable projectId="project-1" scenarioId="scenario-1" />
        </HouseholdProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
  return fetchMock;
}

async function openActualsModal() {
  await screen.findByDisplayValue("Fundament");
  await userEvent.click(screen.getByRole("button", { name: "Ist-Kosten" }));
  await screen.findByText("Ist-Kosten: Fundament");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Ist-Kosten-Zuordnung", () => {
  it("zeigt vorhandene Zuordnungen inkl. archivierte-Link-Warnung", async () => {
    renderCostPlanTable();
    await openActualsModal();

    expect(await screen.findByText("Alter Beleg")).toBeInTheDocument();
    expect(screen.getByText("archivierte Buchung")).toBeInTheDocument();
    // "200,00 €" erscheint sowohl in der Ist-Spalte der Kostentabelle (die
    // im Hintergrund weiter im DOM steht) als auch in der Zuordnungszeile.
    expect(screen.getAllByText("200,00 €").length).toBeGreaterThanOrEqual(1);
  });

  it("legt eine manuelle Zuordnung an und die Liste zeigt sie nach dem Refetch", async () => {
    const fetchMock = renderCostPlanTable();
    await openActualsModal();

    await userEvent.type(screen.getByLabelText("Betrag (€) *"), "150");
    fireEvent.change(screen.getByLabelText("Datum *"), { target: { value: "2026-08-01" } });
    await userEvent.type(screen.getByLabelText("Notiz"), "Rechnung Baustoffe");
    await userEvent.click(screen.getByRole("button", { name: "Hinzufügen" }));

    expect(await screen.findByText("Rechnung Baustoffe")).toBeInTheDocument();

    const createCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        typeof input === "string" &&
        input.endsWith("/cost-items/item-1/actuals") &&
        init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    const body = JSON.parse((createCall?.[1]?.body as string) ?? "{}");
    expect(body).toMatchObject({
      source: "manual",
      amount_minor: 15000,
      occurred_on: "2026-08-01",
      note: "Rechnung Baustoffe",
    });
  });

  it("zeigt eine konkrete Meldung bei Ueberbuchung statt eines generischen Fehlers", async () => {
    renderCostPlanTable();
    await openActualsModal();

    await userEvent.type(screen.getByLabelText("Betrag (€) *"), "5000");
    fireEvent.change(screen.getByLabelText("Datum *"), { target: { value: "2026-08-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Hinzufügen" }));

    expect(
      await screen.findByText(
        "Dieser Betrag übersteigt den noch verfügbaren Betrag dieser Buchung.",
      ),
    ).toBeInTheDocument();
  });

  it("entfernt eine Zuordnung", async () => {
    const fetchMock = renderCostPlanTable();
    await openActualsModal();

    await screen.findByText("Alter Beleg");
    await userEvent.click(screen.getByRole("button", { name: "Entfernen" }));

    await waitFor(() => expect(screen.queryByText("Alter Beleg")).not.toBeInTheDocument());
    await screen.findByText("Noch keine Ist-Kosten zugeordnet.");

    const deleteCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        typeof input === "string" &&
        input.includes("/cost-actuals/actual-archived") &&
        init?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
  });
});
