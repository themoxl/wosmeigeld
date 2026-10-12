import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { DatenbankPage } from "../pages/DatenbankPage";

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

const mockTables = {
  tables: [
    {
      name: "meal_weeks",
      group: "Lebensmittel",
      row_count: 3,
      is_deletable: true,
      primary_key: "id",
    },
    {
      name: "transactions",
      group: "Buchungen & Konten",
      row_count: 812,
      is_deletable: true,
      primary_key: "id",
    },
    {
      name: "ai_task_events",
      group: "KI",
      row_count: 0,
      is_deletable: true,
      primary_key: "id",
    },
  ],
  total_rows: 815,
};

const mockRows = {
  table: "meal_weeks",
  columns: [
    { name: "id", data_type: "uuid", is_nullable: false, is_primary_key: true },
    { name: "week_start", data_type: "date", is_nullable: false, is_primary_key: false },
    { name: "budget_minor", data_type: "bigint", is_nullable: false, is_primary_key: false },
    { name: "note", data_type: "text", is_nullable: true, is_primary_key: false },
  ],
  primary_key: "id",
  rows: [
    { id: "week-1", week_start: "2026-08-03", budget_minor: 10000, note: null },
    { id: "week-2", week_start: "2026-08-10", budget_minor: 12000, note: "Urlaub" },
  ],
  total: 2,
  limit: 50,
  offset: 0,
};

// Der Fall, um den es geht: eine Essenswoche nimmt Mahlzeiten, Einkaufslisten
// UND deren Posten mit — zwei Ebenen tiefer, als man beim Klicken denkt.
const mockPreview = {
  table: "meal_weeks",
  direct_rows: 1,
  effects: [
    {
      table: "shopping_items",
      rows: 14,
      effect: "delete",
      via_column: "shopping_list_id",
      depth: 2,
    },
    { table: "meals", rows: 5, effect: "delete", via_column: "meal_week_id", depth: 1 },
    { table: "shopping_lists", rows: 2, effect: "delete", via_column: "meal_week_id", depth: 1 },
  ],
  cascade_total: 21,
  detach_total: 0,
};

const mockPurgePreview = {
  household_name: "Testhaushalt",
  tables: [
    {
      name: "transactions",
      group: "Buchungen & Konten",
      row_count: 812,
      is_deletable: true,
      primary_key: "id",
    },
    {
      name: "meal_weeks",
      group: "Lebensmittel",
      row_count: 3,
      is_deletable: true,
      primary_key: "id",
    },
  ],
  total_rows: 815,
  kept_tables: ["audit_log", "categories", "category_groups", "household_members"],
};

function renderPage(options?: {
  preview?: Record<string, unknown>;
  requests?: Array<{ url: string; init?: RequestInit }>;
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      options?.requests?.push({ url, init });
      if (url.includes("/purge-preview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockPurgePreview),
        });
      }
      if (url.includes("/purge")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              household_name: "Testhaushalt",
              deleted_rows: 815,
              deleted_per_table: { transactions: 812, meal_weeks: 3 },
              audit_event_id: "audit-2",
            }),
        });
      }
      if (url.includes("/delete-preview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.preview ?? mockPreview),
        });
      }
      if (url.includes("/delete")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              table: "meal_weeks",
              deleted: 1,
              cascade_total: 21,
              audit_event_id: "audit-1",
            }),
        });
      }
      if (url.includes("/rows")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockRows) });
      }
      if (url.includes("/v1/data-explorer/tables")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockTables) });
      }
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <HouseholdProvider>
            <DatenbankPage />
          </HouseholdProvider>
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("DatenbankPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listet die Tabellen fachlich gruppiert mit Zeilenzahl", async () => {
    renderPage();

    expect(await screen.findByText("Lebensmittel")).toBeInTheDocument();
    expect(screen.getByText("Buchungen & Konten")).toBeInTheDocument();
    expect(screen.getByText("meal_weeks")).toBeInTheDocument();
    expect(screen.getByText("812")).toBeInTheDocument();
    // Die eine Zahl, die sagt "so viel liegt hier".
    expect(screen.getByText("815")).toBeInTheDocument();
  });

  it("zeigt Zeilen im Rohzustand statt aufbereitet", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText("meal_weeks"));

    // Beträge bleiben Cent-Zahlen — ein hier hübsch gerechneter Euro-Betrag
    // würde genau die Frage verschleiern, wegen der man die Tabelle aufmacht.
    expect(await screen.findByText("10000")).toBeInTheDocument();
    expect(screen.getByText("2026-08-03")).toBeInTheDocument();
    // NULL wird als solches erkennbar, nicht als leere Zelle.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("zeigt vor dem Löschen die vollständige Kaskade", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText("meal_weeks"));
    await user.click(await screen.findByLabelText("Zeile week-1 auswählen"));
    await user.click(screen.getByRole("button", { name: /1 löschen/ }));

    expect(await screen.findByText("Wird mitgelöscht")).toBeInTheDocument();
    // Zwei Ebenen tief — genau der Teil, den man ohne Vorschau nicht ahnt.
    expect(screen.getByText(/shopping_items/)).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText(/über shopping_list_id, Ebene 2/)).toBeInTheDocument();
    expect(screen.getByText(/lässt sich nicht rückgängig machen/)).toBeInTheDocument();
  });

  it("löscht kleine Mengen ohne zusätzliche Tippbestätigung", async () => {
    const user = userEvent.setup();
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    renderPage({ requests });

    await user.click(await screen.findByText("meal_weeks"));
    await user.click(await screen.findByLabelText("Zeile week-1 auswählen"));
    await user.click(screen.getByRole("button", { name: /1 löschen/ }));

    // 1 direkte + 21 abhängige = 22 Zeilen, unter der Schwelle von 25. Die
    // Vorschau kommt trotzdem — nur das Tippfeld bleibt weg.
    const confirmButton = await screen.findByRole("button", {
      name: /22 Zeile\(n\) endgültig löschen/,
    });
    expect(confirmButton).toBeEnabled();
    expect(screen.queryByRole("textbox", { name: /eintippen/i })).toBeNull();

    await user.click(confirmButton);
    await waitFor(() => {
      expect(
        requests.some((entry) => entry.url.includes("/delete") && !entry.url.includes("preview")),
      ).toBe(true);
    });
  });

  it("sperrt das Löschen, bis der Tabellenname getippt ist", async () => {
    const user = userEvent.setup();
    renderPage({
      preview: { ...mockPreview, cascade_total: 400, effects: mockPreview.effects },
    });

    await user.click(await screen.findByText("meal_weeks"));
    await user.click(await screen.findByLabelText("Zeile week-1 auswählen"));
    await user.click(screen.getByRole("button", { name: /1 löschen/ }));

    const confirmButton = await screen.findByRole("button", {
      name: /401 Zeile\(n\) endgültig löschen/,
    });
    // Ein Klick bei drei Zeilen ist eine Korrektur, bei vierhundert ein
    // anderer Vorgang — und der darf sich nicht gleich anfühlen.
    expect(confirmButton).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: /eintippen/i }), "meal_weeks");
    expect(confirmButton).toBeEnabled();
  });

  it("zeigt vor dem Leeren, was weggeht und was stehen bleibt", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Leeren vorbereiten/ }));

    // Nicht auf "815" prüfen: die Zahl steht auch als Gesamtstand im
    // Seitenkopf, und zwei Treffer lassen findByText scheitern.
    expect(await screen.findByText(/würden gelöscht/)).toBeInTheDocument();
    // Was geschützt ist, wird benannt — "leeren" soll nicht mehr versprechen
    // als es tut.
    expect(
      screen.getByText(/Bleibt stehen: audit_log, categories, category_groups, household_members/),
    ).toBeInTheDocument();
    expect(screen.getByText(/keinen Schnappschuss hast/)).toBeInTheDocument();
  });

  it("leert erst, wenn der Haushaltsname exakt getippt ist", async () => {
    const user = userEvent.setup();
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    renderPage({ requests });

    await user.click(await screen.findByRole("button", { name: /Leeren vorbereiten/ }));
    const knopf = await screen.findByRole("button", { name: /815 Zeilen endgültig löschen/ });
    expect(knopf).toBeDisabled();

    const feld = screen.getByRole("textbox", { name: /Haushaltsnamen eintippen/i });
    await user.type(feld, "Testhaushal");
    expect(knopf).toBeDisabled();

    await user.type(feld, "t");
    expect(knopf).toBeEnabled();

    await user.click(knopf);
    await waitFor(() => {
      const aufruf = requests.find(
        (entry) => entry.url.endsWith("/purge") && entry.init?.method === "POST",
      );
      expect(aufruf).toBeDefined();
      expect(JSON.parse(String(aufruf?.init?.body))).toEqual({ confirm_name: "Testhaushalt" });
    });
  });

  it("filtert die Tabellenliste", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("meal_weeks");
    await user.type(screen.getByLabelText("Tabelle suchen"), "transact");

    expect(screen.getByText("transactions")).toBeInTheDocument();
    expect(screen.queryByText("meal_weeks")).toBeNull();
  });
});
