import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { SavingsGoalsPage } from "../pages/SavingsGoalsPage";
import { WishlistPage } from "../pages/WishlistPage";

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
  timezone: "Europe/Berlin",
  currency: "EUR",
  households: [{ household_id: "household-1", name: "Testhaushalt", role: "owner" }],
};

const goal = {
  id: "goal-1",
  name: "Notgroschen",
  target_amount_minor: 300000,
  starting_amount_minor: 50000,
  current_amount_minor: 90000,
  target_date: "2027-08-01",
  monthly_required_minor: 17500,
  account_id: null,
  category_id: null,
  status: "active",
  created_at: "2026-08-01T00:00:00Z",
};

const reachedGoal = {
  ...goal,
  id: "goal-2",
  name: "Urlaub erreicht",
  target_amount_minor: 50000,
  current_amount_minor: 50000,
  monthly_required_minor: 0,
  status: "reached",
};

const archivedGoal = {
  ...goal,
  id: "goal-3",
  name: "Altes Sparziel",
  target_amount_minor: 900000,
  status: "cancelled",
};

const wish = {
  id: "wish-1",
  name: "Tourenrad",
  price_minor: 120000,
  priority: 2,
  note: "Mit Gepäckträger",
  goal_id: "goal-1",
  buy_traffic_light: "yellow",
  coverage_ratio: 0.75,
  shortfall_minor: 30000,
  earliest_purchase_date: "2026-10-01",
  status: "open",
  created_at: "2026-08-01T00:00:00Z",
};

const deferredWish = {
  ...wish,
  id: "wish-2",
  name: "Espressomaschine",
  price_minor: 50000,
  status: "deferred",
};

const purchasedWish = {
  ...wish,
  id: "wish-3",
  name: "Gekaufte Kamera",
  price_minor: 90000,
  status: "purchased",
};

const archivedWish = {
  ...wish,
  id: "wish-4",
  name: "Archivierte Konsole",
  price_minor: 70000,
  status: "archived",
};

type CapturedRequest = { url: string; init?: RequestInit };

function requestBody(requests: CapturedRequest[], path: string, method: string) {
  const request = requests.find(
    (entry) => entry.url.includes(path) && entry.init?.method === method,
  );
  return request ? JSON.parse(String(request.init?.body)) : undefined;
}

function renderGoalsPage(
  page: "savings" | "wishlist",
  requests: CapturedRequest[],
  options?: {
    goals?: Array<Record<string, unknown>>;
    wishes?: Array<Record<string, unknown>>;
  },
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      requests.push({ url, init });

      if (/\/v1\/goals\/goal-[123]$/.test(url) && init?.method === "PATCH") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...goal, ...JSON.parse(String(init.body)) }),
        });
      }
      if (url.endsWith("/v1/goals") && init?.method === "POST") {
        return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve(goal) });
      }
      if (url.endsWith("/v1/goals")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.goals ?? [goal]),
        });
      }
      if (/\/v1\/wishlist\/wish-[1234]$/.test(url) && init?.method === "PATCH") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...wish, ...JSON.parse(String(init.body)) }),
        });
      }
      if (url.endsWith("/v1/wishlist") && init?.method === "POST") {
        return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve(wish) });
      }
      if (url.endsWith("/v1/wishlist")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.wishes ?? [wish]),
        });
      }
      if (url.includes("/v1/analytics")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              grain: "rows",
              columns: [],
              rows: [],
              total_row: null,
              row_count: 0,
              query_id: "goal-drilldown",
              applied_limits: [],
            }),
        });
      }
      if (/\/v1\/me(?:\?|$)/.test(url)) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
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
            {page === "savings" ? <SavingsGoalsPage /> : <WishlistPage />}
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Inline- und Lebenszyklus-Workflows für Sparziele und Wunschliste", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("legt ein Sparziel in der permanenten Tabellenzeile an und leert sie danach", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("savings", requests);

    const nameInput = await screen.findByLabelText("Neues Sparziel: Bezeichnung");
    const targetInput = screen.getByLabelText("Neues Sparziel: Zielbetrag");
    expect(screen.queryByRole("button", { name: "+ Neues Sparziel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.type(nameInput, "Urlaub");
    await user.type(screen.getByLabelText("Neues Sparziel: Startbetrag"), "250,00");
    await user.type(targetInput, "2500,00");
    await user.type(screen.getByLabelText("Neues Sparziel: Zieldatum"), "2027-08-01");
    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      expect(requestBody(requests, "/v1/goals", "POST")).toEqual({
        name: "Urlaub",
        target_amount_minor: 250000,
        starting_amount_minor: 25000,
        planning_mode: "target_date",
        target_date: "2027-08-01",
        monthly_contribution_minor: null,
        include_in_planning: true,
        priority: 3,
      });
      expect(nameInput).toHaveValue("");
      expect(targetInput).toHaveValue("");
      expect(nameInput).toHaveFocus();
    });
  });

  it("speichert Sparzielzellen per Enter, stellt per Escape wieder her und validiert lokal", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("savings", requests);

    const nameInput = await screen.findByLabelText("Notgroschen: Bezeichnung");
    await user.clear(nameInput);
    await user.type(nameInput, "Reserve");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(requestBody(requests, "/v1/goals/goal-1", "PATCH")).toEqual({ name: "Reserve" });
    });

    const dateInput = screen.getByLabelText("Notgroschen: Zieldatum");
    await user.click(dateInput);
    await user.clear(dateInput);
    await user.type(dateInput, "2028-01-12");
    await user.keyboard("{Escape}");
    expect(dateInput).toHaveValue("2027-08-01");
    expect(
      requests.some(
        (entry) =>
          entry.init?.method === "PATCH" &&
          JSON.parse(String(entry.init.body)).target_date === "2028-01-12",
      ),
    ).toBe(false);

    const targetInput = screen.getByLabelText("Notgroschen: Zielbetrag");
    await user.clear(targetInput);
    await user.type(targetInput, "0");
    await user.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("Zielbetrag ungültig");
    expect(targetInput).toHaveValue("0");
    expect(
      requests.some(
        (entry) =>
          entry.init?.method === "PATCH" &&
          Object.hasOwn(JSON.parse(String(entry.init.body)), "target_amount_minor"),
      ),
    ).toBe(false);
  });

  it("zeigt automatisch erreichte Ziele und korrekte Filterzählungen", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("savings", requests, { goals: [goal, reachedGoal, archivedGoal] });

    expect(await screen.findByRole("button", { name: "Aktuell (2)" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Aktiv (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Erreicht (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archiviert (1)" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Urlaub erreicht")).toBeInTheDocument();
    expect(screen.getByText("Ziel erreicht")).toBeInTheDocument();
    expect(screen.queryByText("Altes Sparziel")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Archiviert (1)" }));
    expect(screen.getByText("Altes Sparziel")).toBeInTheDocument();
    expect(screen.queryByLabelText("Altes Sparziel: Bezeichnung")).not.toBeInTheDocument();
  });

  it("archiviert und restauriert Sparziele direkt per PATCH ohne DELETE", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("savings", requests, { goals: [goal, reachedGoal, archivedGoal] });

    await user.click(
      await screen.findByRole("button", { name: "Sparziel Notgroschen archivieren" }),
    );
    await waitFor(() => {
      expect(requestBody(requests, "/v1/goals/goal-1", "PATCH")).toEqual({
        status: "cancelled",
      });
    });

    await user.click(screen.getByRole("button", { name: "Archiviert (1)" }));
    expect(
      screen.getByRole("button", { name: "Sparziel Altes Sparziel wiederherstellen" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Buchungen für Sparziel Altes Sparziel anzeigen" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Altes Sparziel: Bezeichnung")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Sparziel Altes Sparziel wiederherstellen" }),
    );
    await waitFor(() => {
      expect(requestBody(requests, "/v1/goals/goal-3", "PATCH")).toEqual({ status: "active" });
      expect(requests.some((entry) => entry.init?.method === "DELETE")).toBe(false);
    });
  });

  it("behält den Buchungsdrilldown mit dem exakten Sparziel-Filter", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("savings", requests);

    await user.click(
      await screen.findByRole("button", {
        name: "Buchungen für Sparziel Notgroschen anzeigen",
      }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Buchungen · Notgroschen" }),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(requestBody(requests, "/v1/analytics", "POST")).toMatchObject({
        grain: "rows",
        group_by: [],
        measures: [],
        period: { kind: "all_time" },
        where: { condition: { field: "goal", op: "eq", value: "goal-1" } },
        include_transfers: true,
        include_archived_categories: true,
        only_on_budget: false,
        limit: 200,
      });
    });
  });

  it("legt einen offenen Wunsch in der permanenten Tabellenzeile an und leert sie danach", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("wishlist", requests);

    const nameInput = await screen.findByLabelText("Neuer Wunsch: Bezeichnung");
    const priceInput = screen.getByLabelText("Neuer Wunsch: Preis");
    expect(screen.queryByRole("button", { name: "+ Neuer Wunsch" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.type(nameInput, "Kamera");
    await user.type(priceInput, "799,00");
    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      const payload = requestBody(requests, "/v1/wishlist", "POST");
      expect(payload).toMatchObject({ name: "Kamera", price_minor: 79900, priority: 1 });
      expect(payload).not.toHaveProperty("status");
      expect(nameInput).toHaveValue("");
      expect(priceInput).toHaveValue("");
      expect(nameInput).toHaveFocus();
    });
  });

  it("filtert Wunschstatus und berechnet KPIs nur aus offenen Wünschen", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("wishlist", requests, {
      wishes: [wish, deferredWish, purchasedWish, archivedWish],
    });

    await screen.findByDisplayValue("Tourenrad");
    const metrics = await screen.findByLabelText("Kennzahlen der offenen Wunschliste");
    await waitFor(() => {
      expect(within(metrics).getByText("1.200,00 €")).toBeInTheDocument();
    });
    const countCard = within(metrics).getByText("Offene Wünsche").closest(".card");
    expect(within(countCard as HTMLElement).getByText("1")).toBeInTheDocument();
    expect(within(metrics).getByText("Mit Sparziel")).toBeInTheDocument();
    expect(within(metrics).queryByText("Offener Fehlbetrag")).not.toBeInTheDocument();
    expect(screen.queryByText(/% gedeckt/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Deckung / Kaufzeitpunkt" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Offen (1)" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Zurückgestellt (1)" })).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Espressomaschine")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Zurückgestellt (1)" }));
    expect(screen.getByDisplayValue("Espressomaschine")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Tourenrad")).not.toBeInTheDocument();
  });

  it("speichert Wunschzellen auf Blur, stellt Escape wieder her und validiert lokal", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("wishlist", requests);

    const nameInput = await screen.findByLabelText("Tourenrad: Bezeichnung");
    await user.clear(nameInput);
    await user.type(nameInput, "Reiserad");
    await user.tab();
    await waitFor(() => {
      expect(requestBody(requests, "/v1/wishlist/wish-1", "PATCH")).toEqual({
        name: "Reiserad",
      });
    });

    const noteInput = screen.getByLabelText("Tourenrad: Notiz");
    expect(noteInput.tagName).toBe("TEXTAREA");
    expect(noteInput).toHaveAttribute("rows", "3");
    await user.click(noteInput);
    await user.clear(noteInput);
    await user.type(noteInput, "Neuer Entwurf");
    await user.keyboard("{Escape}");
    expect(noteInput).toHaveValue("Mit Gepäckträger");
    expect(
      requests.some(
        (entry) =>
          entry.init?.method === "PATCH" &&
          Object.hasOwn(JSON.parse(String(entry.init.body)), "note"),
      ),
    ).toBe(false);

    const priorityInput = screen.getByLabelText("Tourenrad: Priorität");
    await user.clear(priorityInput);
    await user.type(priorityInput, "6");
    await user.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("Priorität 1–5");
    expect(priorityInput).toHaveValue(6);
    expect(
      requests.some(
        (entry) =>
          entry.init?.method === "PATCH" &&
          Object.hasOwn(JSON.parse(String(entry.init.body)), "priority"),
      ),
    ).toBe(false);
  });

  it("behandelt einen statuslosen Legacy-Wunsch überall als offen", async () => {
    const requests: CapturedRequest[] = [];
    const legacyWish = Object.fromEntries(
      Object.entries(wish).filter(([key]) => key !== "status"),
    ) as unknown as typeof wish;
    renderGoalsPage("wishlist", requests, { wishes: [legacyWish] });

    expect(await screen.findByRole("button", { name: "Offen (1)" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByDisplayValue("Tourenrad")).toBeInTheDocument();
    expect(screen.getByLabelText("Tourenrad: Status")).toHaveValue("open");
  });

  it("speichert Wunschstatus direkt per PATCH und archiviert ohne DELETE", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderGoalsPage("wishlist", requests, { wishes: [deferredWish] });

    await user.click(await screen.findByRole("button", { name: "Zurückgestellt (1)" }));
    const statusSelect = screen.getByLabelText("Espressomaschine: Status");
    await user.selectOptions(statusSelect, "purchased");
    await user.tab();

    await waitFor(() => {
      expect(requestBody(requests, "/v1/wishlist/wish-2", "PATCH")).toEqual({
        status: "purchased",
      });
      expect(requests.some((entry) => entry.init?.method === "DELETE")).toBe(false);
    });
  });

  it("bewahrt archivierte Zielverknüpfungen, schließt sie aber in der Add-Zeile aus", async () => {
    const requests: CapturedRequest[] = [];
    renderGoalsPage("wishlist", requests, {
      goals: [goal, archivedGoal],
      wishes: [{ ...wish, goal_id: archivedGoal.id }],
    });

    const existingSelect = await screen.findByLabelText("Tourenrad: Sparziel");
    expect(
      within(existingSelect).getByRole("option", { name: "Altes Sparziel (Archiviert)" }),
    ).toBeInTheDocument();
    expect(existingSelect).toHaveValue("goal-3");

    const addSelect = screen.getByLabelText("Neuer Wunsch: Sparziel");
    expect(within(addSelect).getByRole("option", { name: "Notgroschen" })).toBeInTheDocument();
    expect(
      within(addSelect).queryByRole("option", { name: /Altes Sparziel/ }),
    ).not.toBeInTheDocument();
  });
});
