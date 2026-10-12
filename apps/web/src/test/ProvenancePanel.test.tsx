import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ProvenancePanel } from "../components/ProvenancePanel";
import type { SplitProvenance, TransactionProvenance } from "../lib/types";

// docs/ANALYSE-EXPLORER-PLAN.md, Baustein A: geprueft wird die Zusage, dass
// die Oberflaeche aus den Herkunftsfeldern einen lesbaren Satz macht - und
// gerade KEINE rohen reason_code-Werte anzeigt.

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

const baseSplit: SplitProvenance = {
  split_id: "split-1",
  amount_minor: -4250,
  category_id: "cat-1",
  category_name: "Lebensmittel",
  category_group_name: "Lebensmittel & Gastronomie",
  category_is_archived: false,
  category_source: "unset",
  rule_id: null,
  rule_name: null,
  rule_priority: null,
  rule_is_active: null,
  matched_on: null,
  decision_id: null,
  model_name: null,
  reason_code: null,
  confidence: null,
  categorized_at: null,
  categorized_by: null,
};

function provenanceResponse(split: Partial<SplitProvenance>): TransactionProvenance {
  return {
    transaction_id: "tx-1",
    source: "import",
    payee_label: "REWE MARKT GMBH",
    payee_raw: "REWE MARKT GMBH",
    purpose_raw: "EINKAUF REWE FILIALE 42",
    note: null,
    bank_transaction_code: "N024",
    external_reference: "EREF-4711",
    fingerprint: "abc",
    import_origin: {
      import_run_id: "run-1",
      filename: "umsaetze.csv",
      detected_format: "csv_generic",
      row_number: 2,
      imported_at: "2026-03-02T10:00:00Z",
    },
    splits: [{ ...baseSplit, ...split }],
  };
}

function renderPanel(provenance: TransactionProvenance) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/provenance")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(provenance),
        });
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
            <ProvenancePanel transactionId="tx-1" />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProvenancePanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("erklaert eine Regelzuordnung im Klartext samt getroffenem Kriterium", async () => {
    renderPanel(
      provenanceResponse({
        category_source: "rule",
        rule_id: "rule-1",
        rule_name: "REWE ist Supermarkt",
        rule_priority: 100,
        rule_is_active: true,
        matched_on: {
          rule_name: "REWE ist Supermarkt",
          criteria: [{ field: "purpose", pattern: "REWE" }],
        },
        categorized_at: "2026-03-02T10:00:00Z",
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("Durch Regel zugeordnet")).toBeInTheDocument();
    });

    const reason = screen.getByText(/Gesetzt durch die Regel/);
    expect(reason).toHaveTextContent("REWE ist Supermarkt");
    expect(reason).toHaveTextContent("Priorität 100");
    expect(reason).toHaveTextContent("weil der Verwendungszweck");
    expect(reason).toHaveTextContent("REWE");
  });

  it("nennt eine inzwischen deaktivierte Regel als solche", async () => {
    renderPanel(
      provenanceResponse({
        category_source: "rule",
        rule_id: "rule-1",
        rule_name: "Alte Regel",
        rule_priority: 0,
        rule_is_active: false,
        matched_on: {
          rule_name: "Alte Regel",
          criteria: [{ field: "payee_raw", pattern: "REWE" }],
        },
      }),
    );

    await waitFor(() => {
      expect(screen.getByText(/inzwischen deaktiviert/)).toBeInTheDocument();
    });
  });

  it("zeigt bei einem KI-Vorschlag Modell und Sicherheit, aber keinen reason_code", async () => {
    renderPanel(
      provenanceResponse({
        category_source: "ai",
        decision_id: "dec-1",
        model_name: "qwen3-14b",
        reason_code: "merchant_and_context_match",
        confidence: 0.87,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("Von der KI vorgeschlagen")).toBeInTheDocument();
    });

    const reason = screen.getByText(/Vorschlag der lokalen KI/);
    expect(reason).toHaveTextContent("qwen3-14b");
    expect(reason).toHaveTextContent("87 %");
    expect(screen.queryByText(/merchant_and_context_match/)).not.toBeInTheDocument();
  });

  // Der Vorfilter fuer bekannte Empfaenger (Klassifikationslauf im Worker)
  // leitet die Kategorie aus dem eigenen Bestand ab, ohne ein Modell zu
  // fragen. "Vorschlag der lokalen KI" waere hier eine Falschaussage.
  it("erklaert eine Zuordnung aus dem eigenen Bestand ohne die KI zu nennen", async () => {
    renderPanel(
      provenanceResponse({
        category_source: "history",
        decision_id: "dec-2",
        model_name: null,
        reason_code: "known_payee",
        confidence: 0.95,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("Aus der Haushaltshistorie")).toBeInTheDocument();
    });

    const reason = screen.getByText(/Aus dem eigenen Bestand abgeleitet/);
    expect(reason).toHaveTextContent("kein Modell gefragt");
    expect(reason).toHaveTextContent("95 %");
    expect(screen.queryByText(/Vorschlag der lokalen KI/)).not.toBeInTheDocument();
    expect(screen.queryByText(/known_payee/)).not.toBeInTheDocument();
  });

  it("behauptet fuer Altbestand keine Herkunft", async () => {
    renderPanel(provenanceResponse({ category_source: "unknown" }));

    await waitFor(() => {
      expect(screen.getByText("Herkunft nicht erfasst")).toBeInTheDocument();
    });
    expect(screen.getByText(/vor der Herkunftsaufzeichnung/)).toBeInTheDocument();
  });

  it("zeigt Bankrohtext und Importherkunft", async () => {
    renderPanel(provenanceResponse({ category_source: "manual" }));

    await waitFor(() => {
      expect(screen.getByText("EINKAUF REWE FILIALE 42")).toBeInTheDocument();
    });
    expect(screen.getByText("N024")).toBeInTheDocument();
    expect(screen.getByText("umsaetze.csv")).toBeInTheDocument();
  });

  it("markiert eine archivierte Kategorie, statt sie zu verschweigen", async () => {
    renderPanel(
      provenanceResponse({
        category_source: "manual",
        category_is_archived: true,
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("archiviert")).toBeInTheDocument();
    });
    expect(screen.getByText("Lebensmittel")).toBeInTheDocument();
  });
});
