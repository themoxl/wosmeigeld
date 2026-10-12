import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { CategoryPicker } from "../components/CategoryPicker";
import { EditTransactionModal } from "../pages/transactions/EditTransactionModal";
import type { Category, CategoryGroup } from "../lib/categoryTypes";
import type { Account, Transaction } from "../lib/types";
import "../styles/global.css";

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

function makeCategory(overrides: Partial<Category> & Pick<Category, "id" | "group_id" | "name">) {
  return {
    default_type: "expense",
    budget_bucket: "needs",
    is_archived: false,
    icon: null,
    color: null,
    is_budgeted: true,
    sort_order: 0,
    synonyms: [],
    description: null,
    semantic_key: null,
    version: 1,
    is_tax_deductible: false,
    ...overrides,
  } satisfies Category;
}

const categoryGroups: CategoryGroup[] = [
  {
    id: "grp-mobilitaet",
    name: "Mobilität",
    sort_order: 0,
    is_archived: false,
    icon: null,
    color: null,
    semantic_key: null,
    version: 1,
    categories: [
      makeCategory({
        id: "cat-oepnv",
        group_id: "grp-mobilitaet",
        name: "ÖPNV & Bahn",
        sort_order: 0,
        synonyms: ["Nahverkehr", "Deutschlandticket"],
      }),
      makeCategory({
        id: "cat-tanken",
        group_id: "grp-mobilitaet",
        name: "Tanken & Laden",
        sort_order: 1,
        synonyms: ["Sprit", "Benzin"],
      }),
    ],
  },
  {
    id: "grp-lebensmittel",
    name: "Lebensmittel & Gastronomie",
    sort_order: 1,
    is_archived: false,
    icon: null,
    color: null,
    semantic_key: null,
    version: 1,
    categories: [
      makeCategory({
        id: "cat-supermarkt",
        group_id: "grp-lebensmittel",
        name: "Lebensmittel",
        sort_order: 0,
        synonyms: ["Edeka", "REWE"],
      }),
    ],
  },
  {
    // Leere Gruppen erscheinen in der Verwaltung, nicht im Picker.
    id: "grp-leer",
    name: "Leere Gruppe",
    sort_order: 2,
    is_archived: false,
    icon: null,
    color: null,
    semantic_key: null,
    version: 1,
    categories: [],
  },
];

function Providers({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>{children}</HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

interface PickerHostProps {
  initialValue?: string;
  archivedSelectionLabel?: string | null;
}

function PickerHost({ initialValue = "", archivedSelectionLabel = null }: PickerHostProps) {
  const [value, setValue] = useState(initialValue);
  return (
    <>
      <label className="form-label" htmlFor="test-category-picker">
        Kategorie
      </label>
      <CategoryPicker
        id="test-category-picker"
        categoryGroups={categoryGroups}
        value={value}
        onChange={setValue}
        archivedSelectionLabel={archivedSelectionLabel}
      />
      <output data-testid="picker-value">{value}</output>
    </>
  );
}

function stubFetch(extra?: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const custom = extra?.(url, init);
    if (custom) {
      return Promise.resolve(custom);
    }
    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPicker(props: PickerHostProps = {}) {
  const fetchMock = stubFetch();
  render(
    <Providers>
      <PickerHost {...props} />
    </Providers>,
  );
  return fetchMock;
}

describe("CategoryPicker", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("findet eine Kategorie über ein Synonym und zeigt den vollständigen Pfad", async () => {
    const user = userEvent.setup();
    renderPicker();

    const combobox = screen.getByRole("combobox", { name: "Kategorie" });
    await user.type(combobox, "edeka");

    const listbox = await screen.findByRole("listbox");
    const options = within(listbox).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("Lebensmittel & Gastronomie · Lebensmittel");
  });

  it("lässt lange Kategoriepfade im verfügbaren Platz mehrzeilig umbrechen", async () => {
    const user = userEvent.setup();
    renderPicker();

    await user.type(screen.getByRole("combobox", { name: "Kategorie" }), "edeka");

    const listbox = await screen.findByRole("listbox");
    const label = within(listbox).getByText("Lebensmittel & Gastronomie · Lebensmittel");
    const textBlock = label.closest(".category-picker-option-text");
    const listboxStyles = getComputedStyle(listbox);
    const labelStyles = getComputedStyle(label);
    const textBlockStyles = getComputedStyle(textBlock as Element);

    expect(listboxStyles.minWidth).toBe("min(260px, 100%)");
    expect(labelStyles.whiteSpace).toBe("normal");
    expect(labelStyles.overflowWrap).toBe("anywhere");
    expect(labelStyles.textOverflow).toBe("clip");
    expect(textBlockStyles.flexGrow).toBe("1");
  });

  it("behandelt Umlautumschriften gleichwertig (oepnv findet ÖPNV, Mobilitaet findet Mobilität)", async () => {
    const user = userEvent.setup();
    renderPicker();

    const combobox = screen.getByRole("combobox", { name: "Kategorie" });
    await user.type(combobox, "oepnv");

    let listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByRole("option", { name: /Mobilität · ÖPNV & Bahn/ })).toBeVisible();

    await user.clear(combobox);
    await user.type(combobox, "mobilitaet");

    listbox = await screen.findByRole("listbox");
    const options = within(listbox).getAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveTextContent("Mobilität · ÖPNV & Bahn");
    expect(options[1]).toHaveTextContent("Mobilität · Tanken & Laden");
  });

  it("blendet leere Gruppen aus", async () => {
    const user = userEvent.setup();
    renderPicker();

    await user.click(screen.getByRole("combobox", { name: "Kategorie" }));

    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).queryByText("Leere Gruppe")).not.toBeInTheDocument();
    expect(within(listbox).getByText("Mobilität")).toBeInTheDocument();
  });

  it("ist vollständig per Tastatur bedienbar", async () => {
    const user = userEvent.setup();
    renderPicker();

    const combobox = screen.getByRole("combobox", { name: "Kategorie" });
    expect(combobox).toHaveAttribute("aria-expanded", "false");

    combobox.focus();
    await user.keyboard("{ArrowDown}");
    expect(combobox).toHaveAttribute("aria-expanded", "true");

    const listbox = await screen.findByRole("listbox");
    expect(combobox).toHaveAttribute("aria-controls", listbox.id);

    // Index 0 ist "Ohne Kategorie"; ein ArrowDown steht auf der ersten
    // echten Kategorie und aria-activedescendant zeigt genau dorthin.
    await user.keyboard("{ArrowDown}");
    const options = within(listbox).getAllByRole("option");
    expect(combobox).toHaveAttribute("aria-activedescendant", options[1]?.id);

    await user.keyboard("{End}");
    expect(combobox).toHaveAttribute(
      "aria-activedescendant",
      options[options.length - 1]?.id ?? "",
    );

    await user.keyboard("{Home}{ArrowDown}{Enter}");
    expect(screen.getByTestId("picker-value")).toHaveTextContent("cat-oepnv");
    expect(combobox).toHaveValue("Mobilität · ÖPNV & Bahn");
    expect(combobox).toHaveAttribute("aria-expanded", "false");

    // Escape schließt das Popup wieder, ohne die Auswahl zu verändern.
    await user.keyboard("{ArrowDown}");
    expect(combobox).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(combobox).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("picker-value")).toHaveTextContent("cat-oepnv");
  });

  it("markiert die aktuelle Auswahl über aria-selected, nicht nur über Farbe", async () => {
    const user = userEvent.setup();
    renderPicker({ initialValue: "cat-tanken" });

    await user.click(screen.getByRole("combobox", { name: "Kategorie" }));
    const listbox = await screen.findByRole("listbox");

    expect(within(listbox).getByText("Aktuell gewählt")).toBeInTheDocument();
    const selected = within(listbox).getAllByRole("option", { selected: true });
    expect(selected.length).toBeGreaterThan(0);
    selected.forEach((option) => expect(option).toHaveTextContent("Mobilität · Tanken & Laden"));
  });

  it("zeigt eine archivierte Zuordnung schreibgeschützt mit Badge und lässt sie auf eine aktive Kategorie ändern", async () => {
    const user = userEvent.setup();
    renderPicker({ initialValue: "cat-alt", archivedSelectionLabel: "Freizeit (alt)" });

    const combobox = screen.getByRole("combobox", { name: "Kategorie" });
    expect(combobox).toHaveValue("Freizeit (alt)");
    expect(screen.getByText("Archiviert")).toBeInTheDocument();

    await user.click(combobox);
    const listbox = await screen.findByRole("listbox");
    // Die archivierte Zuordnung erscheint lesbar, aber nicht auswählbar.
    expect(within(listbox).getByText(/Archiviert – bleibt in der Historie/)).toBeInTheDocument();
    expect(
      within(listbox).queryByRole("option", { name: /Freizeit \(alt\)/ }),
    ).not.toBeInTheDocument();

    await user.click(within(listbox).getByRole("option", { name: /Mobilität · ÖPNV & Bahn/ }));
    expect(screen.getByTestId("picker-value")).toHaveTextContent("cat-oepnv");
  });

  it("bietet bei erfolgloser Suche die Schnellanlage an und wählt die neue Kategorie direkt aus", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url, init) => {
      if (url.endsWith("/v1/categories") && init?.method === "POST") {
        return {
          ok: true,
          status: 201,
          json: () =>
            Promise.resolve(
              makeCategory({
                id: "cat-neu",
                group_id: "grp-mobilitaet",
                name: "Fahrradwerkstatt",
              }),
            ),
        };
      }
      return null;
    });

    render(
      <Providers>
        <PickerHost />
      </Providers>,
    );

    const combobox = screen.getByRole("combobox", { name: "Kategorie" });
    await user.type(combobox, "Fahrradwerkstatt");

    const createOption = await screen.findByRole("option", {
      name: /Neue Kategorie „Fahrradwerkstatt“ erstellen/,
    });
    await user.click(createOption);

    await waitFor(() => {
      expect(screen.getByTestId("picker-value")).toHaveTextContent("cat-neu");
    });

    const postCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/v1/categories") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(postCall).toBeDefined();
    const body = JSON.parse(String((postCall![1] as RequestInit).body)) as {
      name: string;
      group_id: string;
    };
    expect(body.name).toBe("Fahrradwerkstatt");
    expect(body.group_id).toBe("grp-mobilitaet");
  });
});

// ---------------------------------------------------------------------------
// Sichtbares Kategoriefeld im allgemeinen Bearbeiten-Dialog
// ---------------------------------------------------------------------------

const accounts: Account[] = [
  {
    id: "acc-1",
    household_id: "household-1",
    name: "Girokonto",
    account_type: "checking",
    currency: "EUR",
    institution: null,
    iban_last4: null,
    is_budget_account: true,
    is_closed: false,
    opening_balance_minor: 0,
    current_balance_minor: 0,
  } as unknown as Account,
];

function makeTransaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: "txn-1",
    account_id: "acc-1",
    booked_date: "2026-08-01",
    value_date: null,
    amount_minor: -1599,
    currency: "EUR",
    payee_label: "Netflix",
    note: null,
    status: "booked",
    is_transfer: false,
    category_id: null,
    is_split: false,
    is_archived: false,
    version: 3,
    ...overrides,
  } as Transaction;
}

/** GET /v1/transactions/{id}/provenance - der Dialog zeigt zusätzlich die Herkunft. */
const provenanceResponse = {
  transaction_id: "txn-1",
  source: "manual",
  payee_label: "Netflix",
  payee_raw: null,
  purpose_raw: null,
  note: null,
  bank_transaction_code: null,
  external_reference: null,
  fingerprint: null,
  import_origin: null,
  splits: [],
};

describe("EditTransactionModal – sichtbares Kategoriefeld", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt die Kategorie direkt nach dem Zahlungsempfänger und ändert sie versionsgeprüft", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch((url, init) => {
      if (url.includes("/provenance")) {
        return { ok: true, status: 200, json: () => Promise.resolve(provenanceResponse) };
      }
      if (url.includes("/v1/transactions/txn-1/category") && init?.method === "PUT") {
        return { ok: true, status: 200, json: () => Promise.resolve({}) };
      }
      if (url.includes("/v1/transactions/txn-1") && init?.method === "PATCH") {
        return { ok: true, status: 200, json: () => Promise.resolve({}) };
      }
      return null;
    });

    render(
      <Providers>
        <EditTransactionModal
          transaction={makeTransaction()}
          accounts={accounts}
          categoryGroups={categoryGroups}
          onEditSplits={vi.fn()}
          onClose={vi.fn()}
        />
      </Providers>,
    );

    const fields = screen.getAllByText(/Zahlungsempfänger|Kategorie/).map((el) => el.textContent);
    expect(fields.indexOf("Zahlungsempfänger")).toBeLessThan(fields.indexOf("Kategorie"));

    const combobox = screen.getByRole("combobox", { name: "Kategorie" });
    await user.type(combobox, "Nahverkehr");
    await user.click(await screen.findByRole("option", { name: /Mobilität · ÖPNV & Bahn/ }));

    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/v1/transactions/txn-1/category") &&
          (init as RequestInit | undefined)?.method === "PUT",
      );
      expect(call).toBeDefined();
      const body = JSON.parse(String((call![1] as RequestInit).body)) as {
        category_id: string;
        expected_version: number;
      };
      expect(body.category_id).toBe("cat-oepnv");
      expect(body.expected_version).toBe(3);
    });
  });

  it("überschreibt eine Split-Buchung nicht, sondern verweist auf den Split-Editor", async () => {
    const user = userEvent.setup();
    const onEditSplits = vi.fn();
    stubFetch((url) =>
      url.includes("/provenance")
        ? { ok: true, status: 200, json: () => Promise.resolve(provenanceResponse) }
        : null,
    );

    render(
      <Providers>
        <EditTransactionModal
          transaction={makeTransaction({ is_split: true })}
          accounts={accounts}
          categoryGroups={categoryGroups}
          onEditSplits={onEditSplits}
          onClose={vi.fn()}
        />
      </Providers>,
    );

    expect(screen.getByText("Mehrere Kategorien")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Kategorie" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Splits bearbeiten" }));
    expect(onEditSplits).toHaveBeenCalledTimes(1);
  });
});
