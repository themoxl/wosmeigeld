import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { RulesPage } from "../pages/RulesPage";

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

const mockRules = [
  {
    id: "rule-1",
    household_id: "household-1",
    name: "Rewe Supermarkt Regel",
    priority: 10,
    is_active: true,
    match_payee_raw: "Rewe",
    match_purpose: null,
    match_min_amount_minor: null,
    match_max_amount_minor: null,
    match_account_id: null,
    action_payee_id: null,
    action_category_id: "cat-1",
    action_set_transfer: false,
    action_note: null,
    source: "user",
    // Wird vom Trigger classification_rules_version_trg gepflegt.
    version: 1,
  },
];

// Ergebnis eines rueckwirkenden Regellaufs. Vorschau und Anwendung liefern
// dieselbe Struktur; nur dry_run unterscheidet sie.
function applyResult(dryRun: boolean) {
  return {
    rule_id: "rule-1",
    rule_name: "Rewe Supermarkt Regel",
    scope: "uncategorized",
    dry_run: dryRun,
    matched: 2,
    applied: 1,
    unchanged: 0,
    skipped: 1,
    conflict: 0,
    truncated: false,
    items: [
      {
        transaction_id: "tx-1",
        booked_date: "2026-07-07",
        amount_minor: 150000,
        payee: null,
        purpose: "Abrechnung vom 30.06.2026",
        status: "applied",
        reason: null,
        previous_category_id: null,
      },
      {
        transaction_id: "tx-2",
        booked_date: "2026-06-05",
        amount_minor: 300000,
        payee: null,
        purpose: "Abrechnung vom 29.05.2026",
        status: "skipped",
        reason: "already_categorized",
        previous_category_id: "cat-1",
      },
    ],
  };
}

// Sammellauf ueber alle Regeln: dieselben Zaehler wie beim Einzellauf, dazu
// eine Zeile je Regel.
function applyAllResult(dryRun: boolean) {
  return {
    scope: "uncategorized",
    dry_run: dryRun,
    rules_total: 2,
    rules_evaluated: 1,
    transactions_scanned: 12,
    matched: 2,
    applied: 1,
    unchanged: 0,
    skipped: 1,
    conflict: 0,
    truncated: false,
    rules: [
      {
        rule_id: "rule-1",
        rule_name: "Rewe Supermarkt Regel",
        priority: 10,
        matched: 2,
        applied: 1,
        unchanged: 0,
        skipped: 1,
        conflict: 0,
        excluded_reason: null,
      },
      {
        rule_id: "rule-2",
        rule_name: "Alte Regel",
        priority: 0,
        matched: 0,
        applied: 0,
        unchanged: 0,
        skipped: 0,
        conflict: 0,
        excluded_reason: "inactive",
      },
    ],
    items: [],
  };
}

function renderRulesPage() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
    }
    if (url.includes("/v1/rules/apply-all/preview")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(applyAllResult(true)),
      });
    }
    if (url.includes("/v1/rules/apply-all")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(applyAllResult(false)),
      });
    }
    // Vor der generischen /v1/rules-Regel: sonst faengt die Regelliste die
    // Apply-Aufrufe ab und der Test prueft nichts.
    if (url.includes("/v1/rules/rule-1/apply/preview")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(applyResult(true)),
      });
    }
    if (url.includes("/v1/rules/rule-1/apply")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(applyResult(false)),
      });
    }
    if (url.includes("/v1/rules/rule-1") && init?.method === "PATCH") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            ...mockRules[0],
            name: "Rewe Regel neu",
            version: 2,
          }),
      });
    }
    if (url.includes("/v1/rules")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mockRules) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <RulesPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

describe("RulesPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rendert Klassifikationsregeln mit echten Backend-Feldern ohne Absturz", async () => {
    renderRulesPage();

    await waitFor(() => {
      expect(screen.getByText("Rewe Supermarkt Regel")).toBeInTheDocument();
    });

    expect(screen.getByText("Rewe")).toBeInTheDocument();
  });

  it("bearbeitet eine bestehende Regel per PATCH", async () => {
    const user = userEvent.setup();
    const fetchMock = renderRulesPage();

    const editButton = await screen.findByRole("button", {
      name: "Regel Rewe Supermarkt Regel bearbeiten",
    });
    await user.click(editButton);

    // Das Formular ist mit den Werten der Regel vorbelegt.
    const nameInput = await screen.findByLabelText("Name *");
    expect(nameInput).toHaveValue("Rewe Supermarkt Regel");

    await user.clear(nameInput);
    await user.type(nameInput, "Rewe Regel neu");
    await user.click(screen.getByRole("button", { name: "Änderungen speichern" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/v1/rules/rule-1") &&
          (init as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      const body = JSON.parse(String((patchCall![1] as RequestInit).body)) as {
        name: string;
        match_payee_raw: string | null;
      };
      expect(body.name).toBe("Rewe Regel neu");
      expect(body.match_payee_raw).toBe("Rewe");
    });
  });

  it("wendet eine Regel erst nach einer Vorschau auf den Bestand an", async () => {
    const user = userEvent.setup();
    const fetchMock = renderRulesPage();

    await user.click(
      await screen.findByRole("button", {
        name: "Regel Rewe Supermarkt Regel auf Bestand anwenden",
      }),
    );

    // Ohne Vorschau darf nichts geschrieben werden koennen.
    const applyButton = screen.getByRole("button", { name: "Anwenden" });
    expect(applyButton).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Vorschau" }));

    await waitFor(() => {
      expect(screen.getByText(/Vorschau: 2 Treffer/)).toBeInTheDocument();
    });
    expect(screen.getByText("Abrechnung vom 30.06.2026")).toBeInTheDocument();
    expect(screen.getByText("Hat bereits eine Kategorie")).toBeInTheDocument();

    const previewCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/apply/preview"),
    );
    const previewBody = JSON.parse(String((previewCall![1] as RequestInit).body)) as {
      scope: string;
      from_date: string | null;
    };
    expect(previewBody.scope).toBe("uncategorized");
    expect(previewBody.from_date).toBeNull();

    await user.click(screen.getByRole("button", { name: "Anwenden" }));

    await waitFor(() => {
      expect(screen.getByText(/Angewendet: 2 Treffer/)).toBeInTheDocument();
    });
    const writeCall = fetchMock.mock.calls.find(
      ([input]) =>
        String(input).includes("/v1/rules/rule-1/apply") && !String(input).includes("/preview"),
    );
    expect(writeCall).toBeDefined();
  });

  it("verwirft eine Vorschau, sobald sich der Umfang aendert", async () => {
    const user = userEvent.setup();
    renderRulesPage();

    await user.click(
      await screen.findByRole("button", {
        name: "Regel Rewe Supermarkt Regel auf Bestand anwenden",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Vorschau" }));
    await waitFor(() => {
      expect(screen.getByText(/Vorschau: 2 Treffer/)).toBeInTheDocument();
    });

    // Ein geaenderter Umfang macht die Trefferliste ungueltig - sonst stuende
    // ueber ihr ein Filter, nach dem gar nicht gesucht wurde.
    await user.selectOptions(screen.getByLabelText("Umfang"), "all");

    expect(screen.queryByText(/Vorschau: 2 Treffer/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anwenden" })).toBeDisabled();
  });

  it("wendet alle Regeln in einem Durchgang an und nennt uebergangene Regeln", async () => {
    const user = userEvent.setup();
    const fetchMock = renderRulesPage();

    // Erst wenn die Regelliste steht, ist der Knopf nutzbar - ohne Regeln
    // gaebe es nichts anzuwenden.
    await screen.findByText("Rewe Supermarkt Regel");
    await user.click(screen.getByRole("button", { name: "▶ Alle Regeln auf Bestand anwenden" }));

    // Auch der Sammellauf schreibt erst nach einer Vorschau.
    expect(screen.getByRole("button", { name: "Anwenden" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Vorschau" }));

    await waitFor(() => {
      expect(screen.getByText(/Vorschau: 2 Treffer/)).toBeInTheDocument();
    });
    expect(screen.getByText(/1 von 2 Regeln ausgewertet/)).toBeInTheDocument();
    // Eine Regel lief nicht mit - der Grund steht in der Tabelle, statt dass
    // sie kommentarlos fehlt.
    expect(screen.getByText("Inaktiv")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Anwenden" }));

    await waitFor(() => {
      expect(screen.getByText(/Angewendet: 2 Treffer/)).toBeInTheDocument();
    });
    const writeCall = fetchMock.mock.calls.find(
      ([input]) =>
        String(input).includes("/v1/rules/apply-all") && !String(input).includes("/preview"),
    );
    expect(writeCall).toBeDefined();
    const body = JSON.parse(String((writeCall![1] as RequestInit).body)) as { scope: string };
    expect(body.scope).toBe("uncategorized");
  });
});
