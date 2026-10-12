/**
 * Gespeicherte Haushaltsszenarien (Plan Stufe 7).
 *
 * Der Entwurf bleibt der Normalfall; gespeichert wird, was man wiedersehen
 * will. Geprueft wird hier vor allem, was an den Server geht - die Wirkung
 * selbst rechnet derselbe Kern wie die ungespeicherte Simulation.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ScenarioBar } from "../components/forecast/ScenarioBar";
import { changesToDrafts, draftsToChanges } from "../components/forecast/changeDrafts";
import type { ChangeDraft } from "../components/forecast/changeDrafts";
import type { HouseholdScenarioChange } from "../lib/types";

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

const scenariosResponse = [
  {
    id: "s1",
    name: "Person B arbeitet wieder",
    description: null,
    status: "draft",
    horizon_months: null,
    color_key: null,
    origin: "manual",
    change_count: 2,
    created_at: "2026-08-14T08:00:00Z",
    updated_at: "2026-08-14T08:00:00Z",
    version: 1,
  },
  {
    id: "s2",
    name: "Fixkosten senken",
    description: null,
    status: "active",
    horizon_months: null,
    color_key: null,
    origin: "contract_scenario",
    change_count: 3,
    created_at: "2026-08-01T08:00:00Z",
    updated_at: "2026-08-01T08:00:00Z",
    version: 1,
  },
];

const draft: ChangeDraft = {
  id: "d1",
  enabled: true,
  amountText: "200,00",
  change: {
    change_type: "expense_add",
    label: "Kinderbetreuung",
    amount_mode: "absolute",
    amount_minor: -20000,
    frequency: "monthly",
    due_day: 1,
    starts_on: "2026-10-01",
    confidence: "estimated",
  },
};

interface RequestRecord {
  url: string;
  init?: RequestInit;
}

function stubFetch(): RequestRecord[] {
  const requests: RequestRecord[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      requests.push({ url, init });
      const ok = (body: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      if (/\/v1\/me(?:\?|$)/.test(url)) return ok(meResponse);
      if (url.includes("/v1/scenarios") && init?.method === "POST") {
        return ok({ ...scenariosResponse[0], id: "s3", name: "Neu", changes: [] });
      }
      if (url.includes("/v1/scenarios")) return ok(scenariosResponse);
      return ok([]);
    }),
  );
  return requests;
}

function renderBar(props: Partial<Parameters<typeof ScenarioBar>[0]> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onLoad = vi.fn();
  const onCompare = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <ScenarioBar
              drafts={[draft]}
              activeId={null}
              onLoad={onLoad}
              compared={[]}
              onCompare={onCompare}
              maxCompared={4}
              canEdit
              {...props}
            />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onLoad, onCompare };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ScenarioBar", () => {
  it("weist übernommene Fixkostenpläne als solche aus", async () => {
    stubFetch();
    renderBar();

    await screen.findByTestId("scenario-bar");
    expect(await screen.findByText("Person B arbeitet wieder")).toBeInTheDocument();
    // Herkunft sichtbar, damit klar ist, warum das Szenario schon da ist.
    expect(screen.getByText(/aus dem Fixkostenplan/)).toBeInTheDocument();
  });

  it("speichert den Entwurf unter einem Namen", async () => {
    const requests = stubFetch();
    const user = userEvent.setup();
    const { onLoad } = renderBar();

    await screen.findByTestId("scenario-bar");
    await user.click(screen.getByRole("button", { name: "Als Szenario speichern" }));
    await user.type(screen.getByLabelText("Name des Szenarios"), "Kita ab Oktober");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      expect(requests.some((r) => r.init?.method === "POST")).toBe(true);
    });
    const post = requests.find((r) => r.init?.method === "POST");
    const body = JSON.parse(String(post?.init?.body)) as {
      name: string;
      changes: { change_type: string; include_in_forecast: boolean; sort_order: number }[];
    };
    expect(body.name).toBe("Kita ab Oktober");
    expect(body.changes).toHaveLength(1);
    expect(body.changes[0]?.change_type).toBe("expense_add");
    expect(body.changes[0]?.include_in_forecast).toBe(true);
    // Nach dem Speichern ist das neue Szenario geladen.
    await waitFor(() => expect(onLoad).toHaveBeenCalledWith("s3"));
  });

  it("begrenzt den Vergleich auf vier Szenarien", async () => {
    stubFetch();
    renderBar({ compared: ["a", "b", "c", "d"], maxCompared: 4 });

    const bar = await screen.findByTestId("scenario-bar");
    expect(within(bar).getByText(/nicht mehr lesbar/)).toBeInTheDocument();
    // Weitere Auswahl ist gesperrt, nicht nur unerwünscht.
    expect(await screen.findByLabelText("Person B arbeitet wieder vergleichen")).toBeDisabled();
  });

  it("blendet Schreibaktionen für Leserechte aus", async () => {
    stubFetch();
    renderBar({ canEdit: false });

    await screen.findByTestId("scenario-bar");
    expect(screen.queryByRole("button", { name: "Als Szenario speichern" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /löschen/i })).not.toBeInTheDocument();
  });
});

describe("Umwandlung Entwurf ↔ gespeichertes Szenario", () => {
  it("überträgt den ausgeschalteten Zustand, statt die Zeile zu verlieren", () => {
    const changes = draftsToChanges([{ ...draft, enabled: false }]);
    expect(changes).toHaveLength(1);
    expect(changes[0]?.include_in_forecast).toBe(false);
  });

  it("schreibt den Betrag als positiven Text zurück ins Eingabefeld", () => {
    const saved: HouseholdScenarioChange[] = [
      {
        ...draft.change,
        id: "c1",
        include_in_forecast: true,
        sort_order: 0,
        note: null,
      } as HouseholdScenarioChange,
    ];
    const drafts = changesToDrafts(saved);
    expect(drafts).toHaveLength(1);
    // Das Vorzeichen folgt dem Typ, nicht der Eingabe - im Feld steht 200,00.
    expect(drafts[0]?.amountText).toBe("200,00");
    expect(drafts[0]?.change.amount_minor).toBe(-20000);
    expect(drafts[0]?.enabled).toBe(true);
  });
});
