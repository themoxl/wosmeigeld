import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { TransactionsPage } from "../pages/TransactionsPage";

// Buchungsregister (Architekturplan 10.3) mit den nachgezogenen Filtern aus
// docs/ANALYSE-EXPLORER-PLAN.md ("Noch offen: die fehlenden Filter im
// bestehenden Buchungsregister").
//
// Die Arbeitsteilung wird hier ausdruecklich mitgeprueft: Volltextsuche und
// Zeitraum gehen an den Server, Spaltenfilter und Gruppierung wirken auf die
// geladenen Zeilen. Beides zu vermischen waere die eine Zusage, die eine
// Tabelle nicht halten kann.

const authState = vi.hoisted(() => ({
  session: {
    access_token: "test-access-token",
    user: { id: "user-1", email: "test@example.com" },
  },
}));

// Die Tabelle ist virtualisiert. jsdom hat weder Layout noch ResizeObserver,
// react-virtual meldet dort deshalb konsequent NULL sichtbare Zeilen - der
// Tabellenkoerper bliebe leer, und zwar unabhaengig davon, ob Filter,
// Sortierung und Gruppierung richtig rechnen. Der Stub gibt alle Zeilen frei
// und laesst genau das pruefbar werden; die Virtualisierung selbst deckt der
// Playwright-Test e2e/keyboard-nav.spec.ts in einem echten Browser ab.
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({
    count,
    getItemKey,
  }: {
    count: number;
    getItemKey?: (index: number) => string | number;
  }) => ({
    getVirtualItems: () =>
      Array.from({ length: count }, (_unused, index) => ({
        index,
        key: getItemKey ? getItemKey(index) : index,
        start: index * 40,
        end: (index + 1) * 40,
        size: 40,
      })),
    getTotalSize: () => count * 40,
    measureElement: () => undefined,
    scrollToIndex: () => undefined,
  }),
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

const accounts = [
  { id: "acc-1", name: "Girokonto", account_type: "checking", currency: "EUR", on_budget: true },
  { id: "acc-2", name: "Sparkonto", account_type: "savings", currency: "EUR", on_budget: true },
];

// Erstattungsmarken kommen als eigener Abruf und werden je Buchung
// zusammengefuehrt. Je Test ueberschreibbar.
let reimbursementMarks: Record<string, unknown>[] = [];

const categoryGroups = [
  {
    id: "grp-1",
    name: "Haushalt",
    position: 1,
    categories: [
      { id: "cat-1", name: "Lebensmittel", position: 1 },
      { id: "cat-2", name: "Restaurant", position: 2 },
    ],
  },
];

function makeTransaction(overrides: Record<string, unknown>) {
  return {
    id: "tx-x",
    account_id: "acc-1",
    booked_date: "2026-07-01",
    value_date: null,
    amount_minor: -1000,
    currency: "EUR",
    payee_label: null,
    note: null,
    status: "booked",
    is_transfer: false,
    is_archived: false,
    version: 1,
    category_id: null,
    is_split: false,
    ...overrides,
  };
}

const transactions = [
  makeTransaction({
    id: "tx-1",
    account_id: "acc-1",
    booked_date: "2026-07-03",
    amount_minor: -2500,
    payee_label: "Nordlicht Bäckerei",
    category_id: "cat-1",
  }),
  makeTransaction({
    id: "tx-2",
    account_id: "acc-2",
    booked_date: "2026-07-02",
    amount_minor: -12000,
    payee_label: "Südwind Tankstelle",
    category_id: "cat-2",
  }),
  makeTransaction({
    id: "tx-3",
    account_id: "acc-1",
    booked_date: "2026-07-01",
    amount_minor: 300000,
    payee_label: "Gehalt Juli",
    status: "reconciled",
  }),
];

const requestedUrls: string[] = [];

function renderRegister() {
  requestedUrls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      requestedUrls.push(url);
      const json = (data: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });

      if (url.includes("/v1/me")) return json(meResponse);
      if (url.includes("/v1/accounts")) return json(accounts);
      if (url.includes("/v1/categories/catalog")) return json({ groups: [] });
      if (url.includes("/v1/categories")) return json(categoryGroups);
      if (url.includes("/v1/goals")) return json([]);
      if (url.includes("/v1/reimbursements/marks")) return json(reimbursementMarks);
      if (url.includes("/v1/transactions")) {
        // Die Volltextsuche ist serverseitig - der Stub verhaelt sich hier wie
        // der Endpunkt und liefert nur noch die Treffer zurueck.
        const search = new URL(url, "http://localhost").searchParams.get("search");
        if (!search) return json(transactions);
        const needle = search.toLocaleLowerCase();
        return json(
          transactions.filter((tx) =>
            String(tx.payee_label ?? "")
              .toLocaleLowerCase()
              .includes(needle),
          ),
        );
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
            <TransactionsPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function bodyRows() {
  const table = screen.getByRole("table");
  const body = within(table).getAllByRole("rowgroup")[1];
  return within(body as HTMLElement).getAllByRole("row");
}

describe("Buchungsregister", () => {
  beforeEach(() => {
    // Spaltenwahl liegt im localStorage - ohne Zuruecksetzen truege ein Test
    // den Zustand des vorherigen mit sich.
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt die geladenen Buchungen", async () => {
    renderRegister();

    await waitFor(() => {
      expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument();
    });
    expect(screen.getByText("Südwind Tankstelle")).toBeInTheDocument();
    expect(screen.getByText("Gehalt Juli")).toBeInTheDocument();
  });

  it("schickt die Volltextsuche an den Server", async () => {
    renderRegister();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());

    await userEvent.type(screen.getByLabelText("Buchungen durchsuchen"), "nordlicht{Enter}");

    await waitFor(() => {
      expect(requestedUrls.some((url) => url.includes("search=nordlicht"))).toBe(true);
    });
    await waitFor(() => {
      expect(screen.queryByText("Südwind Tankstelle")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument();
  });

  it("filtert eine Spalte ueber das Kopfmenue, ohne neu zu laden", async () => {
    renderRegister();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());
    const requestsBefore = requestedUrls.length;

    await userEvent.click(
      screen.getByRole("button", { name: "Betrag: filtern, sortieren, gruppieren" }),
    );
    const menu = screen.getByRole("dialog", { name: "Spaltenmenü Betrag" });
    await userEvent.selectOptions(within(menu).getByLabelText("Filteroperator für Betrag"), "gt");
    await userEvent.type(within(menu).getByLabelText("Wert für Betrag"), "0");
    await userEvent.click(within(menu).getByRole("button", { name: "Filter übernehmen" }));

    await waitFor(() => {
      expect(screen.queryByText("Nordlicht Bäckerei")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Gehalt Juli")).toBeInTheDocument();
    // Ein Spaltenfilter wirkt auf die geladenen Zeilen - er darf keine neue
    // Abfrage ausloesen.
    expect(requestedUrls.length).toBe(requestsBefore);
    expect(screen.getByText(/Betrag > 0,00/)).toBeInTheDocument();
  });

  it("sortiert per Klick auf die Spaltenbeschriftung", async () => {
    renderRegister();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Nach Betrag sortieren" }));

    await waitFor(() => {
      expect(bodyRows()[0]?.textContent).toContain("Gehalt Juli");
    });

    await userEvent.click(screen.getByRole("button", { name: "Nach Betrag sortieren" }));
    await waitFor(() => {
      expect(bodyRows()[0]?.textContent).toContain("Südwind Tankstelle");
    });
  });

  it("gruppiert nach Konto und zeigt Zwischensummen", async () => {
    renderRegister();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());

    await userEvent.selectOptions(screen.getByLabelText("Gruppierungsebene hinzufügen"), "account");

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Girokonto zuklappen" })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Sparkonto zuklappen" })).toBeInTheDocument();
    // Girokonto: -25,00 + 3.000,00
    expect(screen.getByText("2.975,00 €")).toBeInTheDocument();
    expect(screen.getByText("2 Buchungen")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Girokonto zuklappen" }));
    await waitFor(() => {
      expect(screen.queryByText("Nordlicht Bäckerei")).not.toBeInTheDocument();
    });
    // Die zugeklappte Gruppe bleibt sichtbar - nur ihre Buchungen sind es nicht.
    expect(screen.getByRole("button", { name: "Girokonto aufklappen" })).toBeInTheDocument();
  });

  it("blendet eine Spalte ueber das Kopfmenue aus", async () => {
    renderRegister();
    await waitFor(() => expect(screen.getByText("Nordlicht Bäckerei")).toBeInTheDocument());
    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Status: filtern, sortieren, gruppieren" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Spalte ausblenden" }));

    await waitFor(() => {
      expect(screen.queryByRole("columnheader", { name: "Status" })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("columnheader", { name: "Datum" })).toBeInTheDocument();
  });

  it("benennt die Kopfzelle weiterhin nach der Spalte, nicht nach ihren Schaltflaechen", () => {
    // Die Kopfzelle traegt jetzt Schaltflaechen. Ohne ausdrueckliche
    // Beschriftung waere ihr zugaenglicher Name deren Text - und die Spalte
    // fuer Screenreader wie Tests nicht mehr auffindbar.
    renderRegister();
    return waitFor(() => {
      expect(screen.getByRole("columnheader", { name: "Kategorie" })).toBeInTheDocument();
      expect(screen.getByRole("columnheader", { name: "Betrag" })).toBeInTheDocument();
    });
  });
});

describe("Buchungsregister: Erstattung", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    reimbursementMarks = [];
    vi.unstubAllGlobals();
  });

  it("zeigt den erwarteten Betrag in der Spalte Erstattung", async () => {
    reimbursementMarks = [
      {
        id: "m1",
        transaction_id: "tx-1",
        state: "expected",
        expectation_mode: "quota",
        expectation_rate: "0.8",
        expectation_minor: null,
        expected_minor: 23154,
        source: "manual",
        rule_id: null,
        converted_at: null,
        created_at: "2026-08-10T10:00:00Z",
        updated_at: "2026-08-10T10:00:00Z",
        booked_date: "2026-07-01",
        amount_minor: -25000,
        payee_label: "Dr. Müller",
        note: null,
      },
    ];
    renderRegister();

    expect((await screen.findAllByText("Erstattung")).length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(screen.getByText("231,54 €")).toBeInTheDocument();
    });
  });

  it("nennt eine ausgenommene Buchung als solche statt sie leer zu lassen", async () => {
    reimbursementMarks = [
      {
        id: "m2",
        transaction_id: "tx-1",
        state: "excluded",
        expectation_mode: "full",
        expectation_rate: null,
        expectation_minor: null,
        expected_minor: null,
        source: "manual",
        rule_id: null,
        converted_at: null,
        created_at: "2026-08-10T10:00:00Z",
        updated_at: "2026-08-10T10:00:00Z",
        booked_date: "2026-07-01",
        amount_minor: -25000,
        payee_label: "Dr. Müller",
        note: null,
      },
    ];
    renderRegister();

    await waitFor(() => {
      expect(screen.getByText("Ausgenommen")).toBeInTheDocument();
    });
  });
});
