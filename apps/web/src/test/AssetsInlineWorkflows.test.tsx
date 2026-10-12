import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { AssetsPage, DebtsPage } from "../pages/AssetsPage";

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

const asset = {
  id: "asset-1",
  name: "Tagesgeld",
  type: "asset",
  asset_class: "cash",
  current_value_minor: 125000,
  interest_rate_percent: 0,
  monthly_payment_minor: 0,
  account_id: null,
  payoff_months_projected: null,
  updated_at: "2026-08-01T00:00:00Z",
};

const debt = {
  id: "asset-2",
  name: "Autokredit",
  type: "debt",
  asset_class: "loan",
  current_value_minor: 1200000,
  interest_rate_percent: 3.5,
  monthly_payment_minor: 30000,
  account_id: null,
  payoff_months_projected: 44,
  updated_at: "2026-08-01T00:00:00Z",
};

const legacyClassAsset = {
  ...asset,
  id: "asset-3",
  name: "Altbestand",
  asset_class: "loan",
};

const netWorth = {
  total_assets_minor: 125000,
  total_debts_minor: 1200000,
  net_worth_minor: -1075000,
  assets_by_class: { cash: 125000 },
  debts_by_class: { loan: 1200000 },
};

const valuations = [
  {
    id: "valuation-1",
    asset_id: "asset-1",
    valued_on: "2026-08-04",
    value_minor: 125000,
    created_at: "2026-08-04T00:00:00Z",
  },
];

type CapturedRequest = { url: string; init?: RequestInit };

function methodOf(entry: CapturedRequest): string {
  return entry.init?.method ?? "GET";
}

function patchBodies(requests: CapturedRequest[], itemId: string) {
  return requests
    .filter((entry) => entry.url.endsWith(`/v1/assets/${itemId}`) && methodOf(entry) === "PATCH")
    .map((entry) => JSON.parse(String(entry.init?.body)));
}

function getCount(requests: CapturedRequest[], path: string): number {
  return requests.filter((entry) => entry.url.includes(path) && methodOf(entry) === "GET").length;
}

function renderAssetsPage(page: "assets" | "debts", requests: CapturedRequest[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      requests.push({ url, init });

      if (/\/v1\/assets\/asset-[123]\/valuations$/.test(url)) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(valuations),
        });
      }
      if (/\/v1\/assets\/asset-[123]$/.test(url) && init?.method === "PATCH") {
        const source = url.endsWith("asset-1") ? asset : debt;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...source, ...JSON.parse(String(init.body)) }),
        });
      }
      if (/\/v1\/assets\/asset-[12]$/.test(url) && init?.method === "DELETE") {
        return Promise.resolve({ ok: true, status: 204, text: () => Promise.resolve("") });
      }
      if (url.endsWith("/v1/assets") && init?.method === "POST") {
        const payload = JSON.parse(String(init.body));
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () => Promise.resolve({ ...(payload.type === "asset" ? asset : debt), ...payload }),
        });
      }
      if (url.endsWith("/v1/assets")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([asset, debt, legacyClassAsset]),
        });
      }
      if (url.endsWith("/v1/reports/net-worth")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(netWorth) });
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
            {page === "assets" ? <AssetsPage /> : <DebtsPage />}
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Inline-Workflows für Vermögen und Schulden", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("legt einen Vermögenswert in der permanenten Add-Zeile an und leert sie", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("assets", requests);

    const nameInput = await screen.findByLabelText("Neuer Vermögenswert: Bezeichnung");
    const valueInput = screen.getByLabelText("Neuer Vermögenswert: Aktueller Wert");
    expect(screen.queryByRole("button", { name: "+ Vermögenswert" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.type(nameInput, "ETF-Depot");
    await user.selectOptions(screen.getByLabelText("Neuer Vermögenswert: Klasse"), "depot");
    await user.type(valueInput, "12.500,00");
    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/assets") && methodOf(entry) === "POST",
      );
      expect(JSON.parse(String(request?.init?.body))).toEqual({
        name: "ETF-Depot",
        type: "asset",
        asset_class: "depot",
        current_value_minor: 1250000,
      });
      expect(nameInput).toHaveValue("");
      expect(valueInput).toHaveValue("");
      expect(nameInput).toHaveFocus();
    });
  });

  it("legt eine Schuld mit Zinssatz und Monatsrate inline an und leert sie", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("debts", requests);

    const nameInput = await screen.findByLabelText("Neue Schuld: Bezeichnung");
    const valueInput = screen.getByLabelText("Neue Schuld: Restschuld");
    expect(screen.queryByRole("button", { name: "+ Schuld erfassen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.type(nameInput, "Küchenkredit");
    await user.type(screen.getByLabelText("Neue Schuld: Zinssatz"), "4,25");
    await user.type(screen.getByLabelText("Neue Schuld: Rate pro Monat"), "250,00");
    await user.type(valueInput, "8.000,00");
    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/assets") && methodOf(entry) === "POST",
      );
      expect(JSON.parse(String(request?.init?.body))).toEqual({
        name: "Küchenkredit",
        type: "debt",
        asset_class: "loan",
        current_value_minor: 800000,
        interest_rate_percent: 4.25,
        monthly_payment_minor: 25000,
      });
      expect(nameInput).toHaveValue("");
      expect(valueInput).toHaveValue("");
      expect(nameInput).toHaveFocus();
    });
  });

  it("speichert Name und Klasse eines Vermögenswerts per Enter beziehungsweise Blur", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("assets", requests);

    const nameInput = await screen.findByLabelText("Tagesgeld: Bezeichnung");
    expect(nameInput).toHaveAttribute("title", "Tagesgeld");
    await user.clear(nameInput);
    await user.type(nameInput, "Liquiditätsreserve");
    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(patchBodies(requests, "asset-1")).toContainEqual({ name: "Liquiditätsreserve" });
    });

    const classSelect = screen.getByLabelText("Tagesgeld: Klasse");
    await user.selectOptions(classSelect, "depot");
    await user.tab();
    await waitFor(() => {
      expect(patchBodies(requests, "asset-1")).toContainEqual({ asset_class: "depot" });
    });
  });

  it("erhält eine historisch typfremde Bestandsklasse ohne unbeabsichtigten PATCH", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("assets", requests);

    const existingSelect = await screen.findByLabelText("Altbestand: Klasse");
    expect(existingSelect).toHaveValue("loan");
    expect(
      within(existingSelect).getByRole("option", { name: "Kredit (Bestand)" }),
    ).toBeInTheDocument();

    await user.click(existingSelect);
    await user.tab();
    expect(patchBodies(requests, "asset-3")).toHaveLength(0);

    const addSelect = screen.getByLabelText("Neuer Vermögenswert: Klasse");
    expect(within(addSelect).queryByRole("option", { name: /Kredit/ })).not.toBeInTheDocument();
  });

  it("speichert Zinssatz, Monatsrate und Restschuld einer Schuld direkt", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("debts", requests);

    const rateInput = await screen.findByLabelText("Autokredit: Zinssatz");
    await user.clear(rateInput);
    await user.type(rateInput, "3,75");
    await user.tab();

    const paymentInput = screen.getByLabelText("Autokredit: Rate pro Monat");
    await user.clear(paymentInput);
    await user.type(paymentInput, "350,00");
    await user.keyboard("{Enter}");

    const valueInput = screen.getByLabelText("Autokredit: Restschuld");
    await user.clear(valueInput);
    await user.type(valueInput, "11.500,00");
    await user.tab();

    await waitFor(() => {
      expect(patchBodies(requests, "asset-2")).toEqual(
        expect.arrayContaining([
          { interest_rate_percent: 3.75 },
          { monthly_payment_minor: 35000 },
          { current_value_minor: 1150000 },
        ]),
      );
    });
    expect(screen.getByText("44")).toBeInTheDocument();
  });

  it("stellt Escape ohne PATCH wieder her und hält ungültige Werte lokal", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("assets", requests);

    const nameInput = await screen.findByLabelText("Tagesgeld: Bezeichnung");
    await user.clear(nameInput);
    await user.type(nameInput, "Entwurf");
    await user.keyboard("{Escape}");
    expect(nameInput).toHaveValue("Tagesgeld");
    expect(patchBodies(requests, "asset-1")).toHaveLength(0);

    const valueInput = screen.getByLabelText("Tagesgeld: Aktueller Wert");
    await user.clear(valueInput);
    await user.type(valueInput, "-1");
    await user.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("Wert ungültig");
    expect(valueInput).toHaveValue("-1");
    expect(patchBodies(requests, "asset-1")).toHaveLength(0);

    await user.clear(nameInput);
    await user.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("Name fehlt");
    expect(nameInput).toHaveValue("");
  });

  it("lädt Verlauf und Nettovermögensdaten nach einer Wertänderung neu", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("assets", requests);

    await user.click(
      await screen.findByRole("button", { name: "Wertverlauf für Tagesgeld anzeigen" }),
    );
    expect(await screen.findByText("2026-08-04")).toBeInTheDocument();

    const initialAssetsGets = getCount(requests, "/v1/assets");
    const initialNetWorthGets = getCount(requests, "/v1/reports/net-worth");
    const initialValuationGets = getCount(requests, "/v1/assets/asset-1/valuations");

    const valueInput = screen.getByLabelText("Tagesgeld: Aktueller Wert");
    await user.clear(valueInput);
    await user.type(valueInput, "1.500,00");
    await user.tab();

    await waitFor(() => {
      expect(patchBodies(requests, "asset-1")).toContainEqual({ current_value_minor: 150000 });
      expect(getCount(requests, "/v1/assets")).toBeGreaterThan(initialAssetsGets);
      expect(getCount(requests, "/v1/reports/net-worth")).toBeGreaterThan(initialNetWorthGets);
      expect(getCount(requests, "/v1/assets/asset-1/valuations")).toBeGreaterThan(
        initialValuationGets,
      );
    });
  });

  it("löscht erst nach der expliziten Inline-Bestätigung", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("debts", requests);

    await user.click(await screen.findByRole("button", { name: "Schuld Autokredit löschen" }));
    expect(requests.some((entry) => methodOf(entry) === "DELETE")).toBe(false);
    expect(
      screen.getByRole("button", { name: "Schuld Autokredit wirklich löschen" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Schuld Autokredit wirklich löschen" }));
    await waitFor(() => {
      expect(
        requests.some(
          (entry) => entry.url.endsWith("/v1/assets/asset-2") && methodOf(entry) === "DELETE",
        ),
      ).toBe(true);
    });
  });

  it("kann die Inline-Löschbestätigung wieder abbrechen", async () => {
    const requests: CapturedRequest[] = [];
    const user = userEvent.setup();
    renderAssetsPage("assets", requests);

    await user.click(
      await screen.findByRole("button", { name: "Vermögenswert Tagesgeld löschen" }),
    );
    const actionCell = screen
      .getByRole("button", { name: "Vermögenswert Tagesgeld wirklich löschen" })
      .closest("td");
    await user.click(within(actionCell as HTMLElement).getByRole("button", { name: /abbrechen/i }));

    expect(
      screen.queryByRole("button", { name: "Vermögenswert Tagesgeld wirklich löschen" }),
    ).not.toBeInTheDocument();
    expect(requests.some((entry) => methodOf(entry) === "DELETE")).toBe(false);
  });
});
