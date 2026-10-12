import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ExplorerPage } from "../pages/ExplorerPage";

// Buchungs-Explorer (docs/ANALYSE-EXPLORER-PLAN.md, Baustein C).
//
// Geprueft wird das, was die Seite gegenueber dem Register ausmacht: Filter
// je Spalte gegen den Server, mehrstufige Gruppierung mit aufklappbaren
// Zwischensummen und die Zusage, dass eine aufgeklappte Ebene dieselbe Menge
// zeigt, die die Zeile darueber summiert.

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

const fieldCatalog = {
  fields: [
    { key: "date", label: "Datum", kind: "date", groupable: true, filterable: true },
    { key: "account", label: "Konto", kind: "cat", groupable: true, filterable: true },
    { key: "payee", label: "Zahlungsempfänger", kind: "cat", groupable: true, filterable: true },
    { key: "category", label: "Kategorie", kind: "cat", groupable: true, filterable: true },
    {
      key: "amount",
      label: "Betrag",
      kind: "num",
      is_money: true,
      groupable: false,
      filterable: true,
    },
    { key: "status", label: "Status", kind: "cat", groupable: true, filterable: true },
  ],
  default_row_fields: ["date", "account", "payee", "category", "amount", "status"],
};

const rowsResult = {
  grain: "rows",
  columns: [
    { key: "date", label: "Datum", kind: "date" },
    { key: "account", label: "Konto", kind: "cat" },
    { key: "payee", label: "Zahlungsempfänger", kind: "cat" },
    { key: "category", label: "Kategorie", kind: "cat" },
    { key: "amount", label: "Betrag", kind: "num", is_money: true },
    { key: "status", label: "Status", kind: "cat" },
    { key: "split_id", label: "Split", kind: "cat" },
    { key: "transaction_id", label: "Buchung", kind: "cat" },
  ],
  rows: [
    {
      date: "2026-07-03",
      account: "acc-1",
      account_label: "Girokonto",
      payee: "Nordlicht Bäckerei",
      category: "cat-1",
      category_label: "Lebensmittel",
      amount: -2500,
      status: "Gebucht",
      split_id: "split-1",
      transaction_id: "tx-1",
    },
  ],
  total_row: { sum_amount: -2500 },
  row_count: 1,
  applied_limits: [],
  query_id: "q-rows",
  months_covered: 12,
};

const aggregateResult = {
  grain: "aggregate",
  columns: [
    { key: "account", label: "Konto", kind: "cat" },
    { key: "category", label: "Kategorie", kind: "cat" },
    { key: "sum_amount", label: "Summe Betrag", kind: "num", is_money: true },
    { key: "avg_per_month_amount", label: "Ø/Monat Betrag", kind: "num", is_money: true },
    { key: "count_amount", label: "Anzahl Betrag", kind: "num" },
  ],
  rows: [
    {
      account: "acc-1",
      account_label: "Girokonto",
      category: "cat-1",
      category_label: "Lebensmittel",
      sum_amount: -3000,
      avg_per_month_amount: -250,
      count_amount: 3,
    },
    {
      account: "acc-1",
      account_label: "Girokonto",
      category: "cat-2",
      category_label: "Restaurant",
      sum_amount: -1200,
      avg_per_month_amount: -100,
      count_amount: 2,
    },
    {
      account: "acc-2",
      account_label: "Sparkonto",
      category: "cat-1",
      category_label: "Lebensmittel",
      sum_amount: -800,
      avg_per_month_amount: -66,
      count_amount: 1,
    },
  ],
  total_row: { sum_amount: -5000, avg_per_month_amount: -416, count_amount: 6 },
  row_count: 3,
  applied_limits: [],
  query_id: "q-agg",
  months_covered: 12,
};

const analyticsBodies: Record<string, unknown>[] = [];

function renderExplorer() {
  analyticsBodies.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const json = (data: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });

      if (url.includes("/v1/me")) return json(meResponse);
      if (url.includes("/v1/analytics/fields")) return json(fieldCatalog);
      if (url.includes("/v1/analytics/facets")) {
        return json({
          field: "account",
          values: [
            { value: "acc-1", label: "Girokonto", count: 5 },
            { value: "acc-2", label: "Sparkonto", count: 1 },
          ],
          capped: false,
        });
      }
      if (url.includes("/v1/analytics")) {
        const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
        analyticsBodies.push(body);
        return json(body.grain === "aggregate" ? aggregateResult : rowsResult);
      }
      return json([]);
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <ExplorerPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Explorer", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt Einzelbuchungen mit benannten Spaltenkoepfen", async () => {
    renderExplorer();

    await waitFor(() => {
      expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument();
    });
    // Die Kopfzelle traegt jetzt Schaltflaechen - ihr Name muss trotzdem die
    // Spalte bleiben.
    expect(screen.getByRole("columnheader", { name: "Betrag" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Kategorie" })).toBeInTheDocument();
  });

  it("schickt einen Spaltenfilter als Bedingung an den Server", async () => {
    renderExplorer();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());

    await userEvent.click(
      screen.getByRole("button", { name: "Betrag: filtern, sortieren, gruppieren" }),
    );
    const menu = screen.getByRole("dialog", { name: "Spaltenmenü Betrag" });
    await userEvent.selectOptions(within(menu).getByLabelText("Filteroperator für Betrag"), "lt");
    await userEvent.type(within(menu).getByLabelText("Wert für Betrag"), "-10");
    await userEvent.click(within(menu).getByRole("button", { name: "Filter übernehmen" }));

    await waitFor(() => {
      expect(
        analyticsBodies.some(
          (body) =>
            JSON.stringify(body.where ?? {}).includes('"field":"amount"') &&
            JSON.stringify(body.where ?? {}).includes('"op":"lt"'),
        ),
      ).toBe(true);
    });
    expect(screen.getByText(/Betrag < -10,00/)).toBeInTheDocument();
  });

  it("gruppiert mehrstufig und rechnet jede Ebene aus ihren Kindern hoch", async () => {
    renderExplorer();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());

    await userEvent.selectOptions(screen.getByLabelText("Gruppierungsebene hinzufügen"), "account");
    // Mit nur einer Achse ist jede Gruppe zugleich die unterste Ebene - sie
    // klappt deshalb zu den Buchungen auf, nicht zu einer weiteren Gruppe.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Buchungen zu Girokonto anzeigen" }),
      ).toBeInTheDocument();
    });

    await userEvent.selectOptions(
      screen.getByLabelText("Gruppierungsebene hinzufügen"),
      "category",
    );

    // Alle Pruefungen ausdruecklich im Gruppenbaum: das Diagramm faellt in
    // jsdom auf seine tabellarische Entsprechung zurueck und zeigt dieselben
    // Betraege noch einmal.
    const tree = () => document.querySelector(".grid-tree-table") as HTMLElement;

    // Girokonto = -30,00 + -12,00; Sparkonto = -8,00
    await waitFor(() => {
      expect(within(tree()).getByText("-42,00 €")).toBeInTheDocument();
    });
    expect(within(tree()).getByText("-8,00 €")).toBeInTheDocument();

    // Die zweite Ebene erscheint erst beim Aufklappen.
    expect(within(tree()).queryByText("Restaurant")).not.toBeInTheDocument();
    await userEvent.click(within(tree()).getByRole("button", { name: "Girokonto aufklappen" }));
    await waitFor(() => {
      expect(within(tree()).getByText("Restaurant")).toBeInTheDocument();
    });

    // Und die Abfrage traegt beide Achsen in der gewaehlten Reihenfolge.
    const lastAggregate = [...analyticsBodies]
      .reverse()
      .find((body) => body.grain === "aggregate") as { group_by?: { field: string }[] } | undefined;
    expect(lastAggregate?.group_by?.map((dimension) => dimension.field)).toEqual([
      "account",
      "category",
    ]);
  });

  it("nimmt einen Filter ueber die Chip-Leiste wieder zurueck", async () => {
    renderExplorer();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());

    await userEvent.click(
      screen.getByRole("button", { name: "Betrag: filtern, sortieren, gruppieren" }),
    );
    const menu = screen.getByRole("dialog", { name: "Spaltenmenü Betrag" });
    await userEvent.selectOptions(within(menu).getByLabelText("Filteroperator für Betrag"), "lt");
    await userEvent.type(within(menu).getByLabelText("Wert für Betrag"), "-10");
    await userEvent.click(within(menu).getByRole("button", { name: "Filter übernehmen" }));

    const chip = await screen.findByRole("button", { name: /Betrag < -10,00/ });
    await userEvent.click(chip);

    await waitFor(() => {
      expect(screen.queryByText(/Betrag < -10,00/)).not.toBeInTheDocument();
    });
  });
});
