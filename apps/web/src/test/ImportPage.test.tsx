import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ImportPage } from "../pages/ImportPage";
import { previewAmount, previewDate } from "../lib/importPreview";

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

const mockAccounts = [
  {
    id: "account-1",
    name: "Girokonto",
    account_type: "checking",
    institution: "Sparkasse",
    currency: "EUR",
    on_budget: true,
    opening_balance_minor: 100000,
    opening_balance_date: "2026-01-01",
    is_closed: false,
  },
];

const mockImportRun = {
  id: "import-run-1",
  account_id: "account-1",
  filename: "statement_2026.csv",
  file_size_bytes: 1024,
  detected_format: "csv_generic",
  status: "ready",
  total_rows: 3,
  new_count: 2,
  duplicate_count: 1,
  transfer_candidate_count: 0,
  error_count: 0,
  error_message: null,
  created_at: "2026-07-31T20:00:00Z",
  completed_at: null,
  // Ein unberuehrter Lauf: nichts uebernommen, also auch keine Buchung im
  // Bestand. Die Tests, die das Zuruecknehmen pruefen, setzen beide Zahlen
  // ausdruecklich hoch - sie steuern, welche Aktion die Zeile anbietet.
  committed_count: 0,
  active_transaction_count: 0,
};

/** Ein Lauf, aus dem Buchungen entstanden sind, die noch im Bestand stehen. */
const mockCommittedRun = {
  ...mockImportRun,
  status: "completed",
  completed_at: "2026-07-31T20:05:00Z",
  committed_count: 5,
  active_transaction_count: 5,
};

const mockImportRows = [
  {
    id: "row-1",
    import_run_id: "import-run-1",
    row_number: 1,
    raw_data: {},
    external_reference: null,
    booked_date: "2026-07-28",
    value_date: "2026-07-28",
    amount_minor: -1550,
    currency: "EUR",
    payee_raw: "REWE Markt",
    purpose: "Einkauf",
    bank_transaction_code: null,
    fingerprint: "fp1",
    status: "new",
    duplicate_of_transaction_id: null,
    committed_transaction_id: null,
  },
  {
    id: "row-2",
    import_run_id: "import-run-1",
    row_number: 2,
    raw_data: {},
    external_reference: null,
    booked_date: "2026-07-29",
    value_date: "2026-07-29",
    amount_minor: -2500,
    currency: "EUR",
    payee_raw: "Muster AG",
    purpose: "Abo",
    bank_transaction_code: null,
    fingerprint: "fp2",
    status: "duplicate_exact",
    duplicate_of_transaction_id: "tx-old-1",
    committed_transaction_id: null,
  },
  {
    id: "row-3",
    import_run_id: "import-run-1",
    row_number: 3,
    raw_data: {},
    external_reference: null,
    booked_date: "2026-07-30",
    value_date: "2026-07-30",
    amount_minor: 250000,
    currency: "EUR",
    payee_raw: "Arbeitgeber GmbH",
    purpose: "Gehalt",
    bank_transaction_code: null,
    fingerprint: "fp3",
    status: "new",
    duplicate_of_transaction_id: null,
    committed_transaction_id: null,
  },
];

// Vorschlag der lokalen KI (POST /v1/imports/{run_id}/mapping-suggestion).
const mockSuggestion = {
  available: true,
  reason: "ok",
  message: "Spaltenvorschlag erstellt.",
  source_columns: ["Buchungstag", "Umsatz", "Beguenstigter", "Verwendungszweck"],
  sample_row_count: 3,
  column_mapping: {
    booked_date: "Buchungstag",
    amount: "Umsatz",
    payee: "Beguenstigter",
    purpose: "Verwendungszweck",
  },
  date_format: "%d.%m.%Y",
  decimal_separator: ",",
  currency: "EUR",
  confidence: 0.82,
  model_name: "google/gemma-4-26b-a4b-qat",
  prompt_version: "map-v1",
};

const rawSample = {
  Buchungstag: "28.07.2026",
  Betrag: "-15,50",
  "Name Zahlungsbeteiligter": "REWE Markt",
  Verwendungszweck: "Einkauf",
  Alternative: "Anderer Empfänger",
  Ungültig: "31.02.2026",
  Mehrdeutig: "1.234",
};
const sampleRun = { ...mockImportRun, detected_columns: Object.keys(rawSample) };
const sampleRows = [{ ...mockImportRows[0], raw_data: rawSample }];
const savedProfile = {
  id: "profile-1",
  name: "MLP nur",
  bank_format: "csv_generic",
  column_mapping: { booked_date: "Buchungstag", amount: "Betrag", payee: "Alternative" },
  created_at: "2026-01-01",
};

interface MockedResponse {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
}

interface RenderOptions {
  runs?: Record<string, unknown>[];
  rowsByRun?: Record<string, Record<string, unknown>[]>;
  rows?: Record<string, unknown>[];
  profiles?: Record<string, unknown>[];
  getRun?: () => Record<string, unknown>;
  getRows?: () => Record<string, unknown>[];
  reparseResponse?: MockedResponse;
  uploadResponse?: Promise<MockedResponse>;
  profileDeleteResponse?: MockedResponse;
  /** Ueberschreibt den Importlauf (z. B. ohne Zielkonto). */
  run?: Record<string, unknown>;
  /** Ueberschreibt die Kontoliste (z. B. leer: Haushalt ohne Konto). */
  accounts?: Record<string, unknown>[];
  /** Antwort auf POST .../commit - erlaubt echte Fehlerkoerper. */
  commitResponse?: MockedResponse;
  /** Antwort auf POST .../mapping-suggestion. */
  suggestionResponse?: MockedResponse;
  /** Antwort auf PATCH /v1/imports/{id} (nachtraegliches Zielkonto). */
  patchResponse?: MockedResponse;
  /** Antwort auf DELETE /v1/imports/{id} (Lauf entfernen). */
  deleteResponse?: MockedResponse;
  /** Antwort auf POST /v1/imports/{id}/cancel (laufenden Import abbrechen). */
  cancelResponse?: MockedResponse;
  /** Antwort auf GET /v1/system/health - steuert die Worker-Zustandszeile. */
  healthResponse?: MockedResponse;
  /** Antwort auf GET /v1/imports/{id}/revert-preview (Vorschau der Ruecknahme). */
  revertPreviewResponse?: MockedResponse;
  /** Antwort auf POST /v1/imports/{id}/revert (Ruecknahme ausfuehren). */
  revertResponse?: MockedResponse;
}

function renderImportPage(options: RenderOptions = {}) {
  const run = options.run ?? mockImportRun;
  const accounts = options.accounts ?? mockAccounts;
  let profiles = options.profiles ?? [];

  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.endsWith("/reparse") && method === "POST") {
      return Promise.resolve(
        options.reparseResponse ?? {
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...run, status: "pending" }),
        },
      );
    }
    if (url.includes("/mapping-profiles/") && method === "DELETE") {
      const response = options.profileDeleteResponse ?? {
        ok: true,
        status: 204,
        json: () => Promise.resolve(null),
      };
      if (response.ok) profiles = profiles.filter((profile) => !url.endsWith(`/${profile.id}`));
      return Promise.resolve(response);
    }
    if (url.includes("/mapping-profiles/") && method === "PATCH") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...savedProfile, ...JSON.parse(String(init?.body)) }),
      });
    }
    const rowsMatch = url.match(/\/v1\/imports\/([^/]+)\/rows$/);
    if (rowsMatch && options.rowsByRun) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(options.rowsByRun?.[rowsMatch[1]!] ?? []),
      });
    }
    if (
      options.runs &&
      /\/v1\/imports\/[^/]+$/.test(url) &&
      method === "GET" &&
      !url.endsWith("mapping-profiles")
    ) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(options.runs?.find((item) => url.endsWith(String(item.id)))),
      });
    }

    if (url.includes("/v1/imports/import-run-1/mapping-suggestion") && method === "POST") {
      return Promise.resolve(
        options.suggestionResponse ?? {
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockSuggestion),
        },
      );
    }
    if (url.includes("/v1/imports/import-run-1/revert-preview")) {
      return Promise.resolve(
        options.revertPreviewResponse ?? {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              import_run_id: "import-run-1",
              filename: "umsaetze.csv",
              transactions_total: 5,
              revertable: 4,
              sum_minor: -12500,
              from_date: "2026-04-02",
              to_date: "2026-04-30",
              sum_minor_with_linked: -14000,
              from_date_with_linked: "2026-04-01",
              to_date_with_linked: "2026-04-30",
              blocked: [{ reason: "bearbeitet", label: "nachtraeglich bearbeitet", count: 1 }],
            }),
        },
      );
    }
    if (url.includes("/v1/imports/import-run-1/revert") && method === "POST") {
      return Promise.resolve(
        options.revertResponse ?? {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              import_run_id: "import-run-1",
              reverted: 4,
              kept: 1,
              blocked: [{ reason: "bearbeitet", label: "nachtraeglich bearbeitet", count: 1 }],
            }),
        },
      );
    }
    if (url.includes("/v1/imports/import-run-1/cancel") && method === "POST") {
      return Promise.resolve(
        options.cancelResponse ?? {
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...run, status: "cancelled" }),
        },
      );
    }
    if (url.includes("/v1/system/health")) {
      return Promise.resolve(
        options.healthResponse ?? {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              status: "ok",
              services: [{ service: "worker", status: "ok", checkedAt: "2026-08-05T12:00:00Z" }],
            }),
        },
      );
    }
    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
    }
    if (url.includes("/v1/accounts")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(accounts),
      });
    }
    if (url.includes("/v1/imports/upload") && method === "POST") {
      return (
        options.uploadResponse ??
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(run),
        })
      );
    }
    if (url.includes("/v1/imports/mapping-profiles")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(profiles) });
    }
    if (url.includes("/v1/imports/import-run-1/rows")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(options.getRows?.() ?? options.rows ?? mockImportRows),
      });
    }
    if (url.includes("/v1/imports/import-run-1/commit") && method === "POST") {
      return Promise.resolve(
        options.commitResponse ?? {
          ok: true,
          status: 200,
          json: () => Promise.resolve({ committed: 2 }),
        },
      );
    }
    if (url.includes("/v1/imports/import-run-1") && method === "PATCH") {
      return Promise.resolve(
        options.patchResponse ?? {
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...run, account_id: "account-1" }),
        },
      );
    }
    if (url.includes("/v1/imports/import-run-1") && method === "DELETE") {
      return Promise.resolve(
        options.deleteResponse ?? {
          ok: true,
          status: 204,
          json: () => Promise.resolve(null),
        },
      );
    }
    if (url.includes("/v1/imports/import-run-1")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(options.getRun?.() ?? run),
      });
    }
    if (url.includes("/v1/imports")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(options.runs ?? [run]),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });

  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <ImportPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  return { ...utils, fetchMock, queryClient };
}

/** Findet den letzten fetch-Aufruf mit der angegebenen HTTP-Methode. */
function lastCallWithMethod(
  fetchMock: ReturnType<typeof vi.fn>,
  method: string,
): { url: string; init: RequestInit } | null {
  const calls = fetchMock.mock.calls as [RequestInfo | URL, RequestInit | undefined][];
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    const call = calls[i];
    if (!call) continue;
    const [input, init] = call;
    if ((init?.method ?? "GET") === method) {
      return { url: typeof input === "string" ? input : String(input), init: init ?? {} };
    }
  }
  return null;
}

describe("ImportPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it.each([
    ["1.234,56", 123456],
    ["25,00-", -2500],
    ["123.45", 12345],
    ["1.234", null],
    ["1,234.56", null],
    ["NaN", null],
    ["", null],
  ])("prüft den Live-Betrag %s ohne irreführende Rundung", (input, expected) => {
    expect(previewAmount(input)).toBe(expected);
  });

  it.each([
    ["29.02.2024", "29.02.2024"],
    ["2026-07-28 00:00:00", "28.07.2026"],
    ["31.02.2026", null],
    ["2026-01-01T99:00:00", null],
    ["01/01/26", null],
  ])("prüft das Live-Datum %s als Kalenderdatum", (input, expected) => {
    expect(previewDate(input)).toBe(expected);
  });

  it("aktualisiert Beispiele und Live-Vorschau sofort und schützt den Entwurf vor Refetch", async () => {
    const { fetchMock, queryClient } = renderImportPage({ run: sampleRun, rows: sampleRows });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    expect(screen.getByLabelText(/Datum \(erforderlich\)/)).toHaveValue("Buchungstag");
    expect(screen.getByLabelText(/Betrag \(erforderlich\)/)).toHaveValue("Betrag");
    expect(screen.getByLabelText("Empfänger")).toHaveValue("Name Zahlungsbeteiligter");
    const preview = within(screen.getByLabelText("Live-Vorschau"));
    expect(preview.getByText("28.07.2026")).toBeInTheDocument();
    expect(preview.getByText(/15,50/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Empfänger"), { target: { value: "Alternative" } });
    expect(preview.getByText("Anderer Empfänger")).toBeInTheDocument();
    expect(preview.queryByText("REWE Markt")).not.toBeInTheDocument();
    expect(lastCallWithMethod(fetchMock, "POST")).toBeNull();
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["import-run"] });
    });
    expect(screen.getByLabelText("Empfänger")).toHaveValue("Alternative");
    expect(screen.getByRole("button", { name: "Zuordnung anwenden" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText(/Datum \(erforderlich\)/), {
      target: { value: "Ungültig" },
    });
    expect(screen.getByLabelText(/Datum \(erforderlich\)/)).toHaveAttribute("aria-invalid", "true");
    expect(preview.getByText("Datum prüfen")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zuordnung anwenden" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Datum \(erforderlich\)/), {
      target: { value: "Buchungstag" },
    });
    fireEvent.change(screen.getByLabelText(/Betrag \(erforderlich\)/), {
      target: { value: "Mehrdeutig" },
    });
    expect(preview.getByText("Betrag prüfen")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zuordnung anwenden" })).toBeDisabled();
  });

  it("zeigt sample_raw auch ohne Staging-Zeilen und lässt optionale Felder leer", async () => {
    renderImportPage({
      run: { ...sampleRun, status: "needs_mapping", sample_raw: [rawSample] },
      rows: [],
    });
    const preview = within(await screen.findByLabelText("Live-Vorschau"));
    await waitFor(() => expect(preview.getByText("REWE Markt")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Empfänger"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Verwendungszweck"), { target: { value: "" } });
    expect(preview.queryByText("REWE Markt")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zuordnung anwenden" })).toBeDisabled();
    expect(screen.getByText("Für diese Zuordnung die Datei erneut hochladen.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Zuordnung speichern oder laden"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Erkannte Zuordnung" } });
    expect(screen.getByRole("button", { name: "Zuordnung speichern" })).toBeEnabled();
  });

  it("sperrt die Übernahme eines geänderten Entwurfs bis zum Anwenden oder Verwerfen", async () => {
    const { fetchMock } = renderImportPage({ run: sampleRun, rows: sampleRows });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    fireEvent.change(screen.getByLabelText("Empfänger"), { target: { value: "Alternative" } });
    expect(screen.getByRole("button", { name: "1 Buchungen übernehmen" })).toBeDisabled();
    expect(screen.getByText(/Zuordnung zuerst anwenden/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Spaltenzuordnung schließen" }));
    expect(screen.getByRole("button", { name: "1 Buchungen übernehmen" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    expect(screen.getByLabelText("Empfänger")).toHaveValue("Name Zahlungsbeteiligter");
    expect(lastCallWithMethod(fetchMock, "POST")).toBeNull();
  });

  it("ersetzt eine vorhandene Zuordnung nur nach expliziter Auswahl per PATCH", async () => {
    const { fetchMock } = renderImportPage({
      run: sampleRun,
      rows: sampleRows,
      profiles: [savedProfile],
    });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    fireEvent.click(screen.getByText("Zuordnung speichern oder laden"));
    fireEvent.change(screen.getByLabelText("Speichern als"), { target: { value: "profile-1" } });
    expect(screen.getByLabelText("Name")).toHaveValue("MLP nur");
    expect(lastCallWithMethod(fetchMock, "PATCH")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Zuordnung ersetzen" }));
    await waitFor(() =>
      expect(lastCallWithMethod(fetchMock, "PATCH")?.url).toContain("/mapping-profiles/profile-1"),
    );
    expect(JSON.parse(String(lastCallWithMethod(fetchMock, "PATCH")?.init.body))).toEqual({
      name: "MLP nur",
      bank_format: "csv_generic",
      column_mapping: {
        booked_date: "Buchungstag",
        amount: "Betrag",
        payee: "Name Zahlungsbeteiligter",
        purpose: "Verwendungszweck",
      },
    });
  });

  it("bewahrt Datei und Entwurf bei fehlgeschlagenem Reparse und speichert kein Profil automatisch", async () => {
    const { fetchMock } = renderImportPage({
      run: sampleRun,
      rows: sampleRows,
      reparseResponse: {
        ok: false,
        status: 400,
        json: () => Promise.resolve({ detail: "Eine Zeile enthält einen ungültigen Betrag" }),
      },
    });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    fireEvent.change(screen.getByLabelText("Empfänger"), { target: { value: "Alternative" } });
    fireEvent.click(screen.getByRole("button", { name: "Zuordnung anwenden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ungültigen Betrag");
    expect(screen.getByLabelText("Empfänger")).toHaveValue("Alternative");
    expect(screen.getByRole("button", { name: "Zuordnung anwenden" })).toBeEnabled();
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("mapping-profiles") && init?.method === "POST",
      ),
    ).toHaveLength(0);
  });

  it("verwirft Auswahl und Entwurf beim Laufwechsel und hält den aktiven Lauf bei Historien-Refetch", async () => {
    const secondRun = { ...sampleRun, id: "import-run-2", filename: "anderes-konto.csv" };
    const { queryClient } = renderImportPage({
      runs: [sampleRun, secondRun],
      rowsByRun: {
        "import-run-1": sampleRows,
        "import-run-2": [
          {
            ...sampleRows[0],
            id: "row-two",
            import_run_id: "import-run-2",
            payee_raw: "Anderes Konto",
          },
        ],
      },
    });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByLabelText("Zeile 1 auswählen"));
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    fireEvent.change(screen.getByLabelText("Empfänger"), { target: { value: "Alternative" } });
    await act(async () =>
      queryClient.setQueryData(["imports", "household-1"], [secondRun, sampleRun]),
    );
    expect(screen.getByLabelText("Empfänger")).toHaveValue("Alternative");
    fireEvent.click(
      within(screen.getByText("anderes-konto.csv").closest("tr")!).getByRole("button", {
        name: "Anzeigen",
      }),
    );
    await screen.findByText("Anderes Konto");
    expect(screen.getByLabelText("Zeile 1 auswählen")).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    expect(screen.getByLabelText("Empfänger")).toHaveValue("Name Zahlungsbeteiligter");
  });

  it("pollt committing bis zum Abschluss", async () => {
    let calls = 0;
    renderImportPage({
      run: { ...mockImportRun, status: "committing" },
      getRun: () => ({ ...mockCommittedRun, status: ++calls < 2 ? "committing" : "completed" }),
    });
    expect(await screen.findByText("Buchungen werden übernommen")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("5 Buchungen übernommen")).toBeInTheDocument(), {
      timeout: 3500,
    });
    expect(calls).toBeGreaterThanOrEqual(2);
  });

  it("pollt auch alte laufende Imports bis ready und lädt dann die Zeilen", async () => {
    let calls = 0;
    const { fetchMock } = renderImportPage({
      run: { ...mockImportRun, status: "pending", created_at: "2000-01-01T00:00:00Z" },
      getRun: () => ({
        ...mockImportRun,
        status: ++calls < 2 ? "pending" : "ready",
        created_at: "2000-01-01T00:00:00Z",
      }),
    });
    expect(await screen.findByText("Datei wird analysiert")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/rows"))).toBe(false);
    await waitFor(() => expect(screen.getByText("REWE Markt")).toBeInTheDocument(), {
      timeout: 3500,
    });
    expect(calls).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("Verarbeitung unterbrochen")).not.toBeInTheDocument();
  });

  it("ersetzt nach Reparse desselben Laufs die alten Zeilen und verwirft die Auswahl", async () => {
    let reparsed = false;
    let pollCalls = 0;
    const { fetchMock } = renderImportPage({
      run: sampleRun,
      getRun: () => ({ ...sampleRun, status: reparsed && ++pollCalls < 2 ? "pending" : "ready" }),
      getRows: () =>
        reparsed
          ? [{ ...sampleRows[0], id: "row-reparsed", payee_raw: "Korrigierter Empfänger" }]
          : sampleRows,
      reparseResponse: {
        ok: true,
        status: 200,
        json: () => {
          reparsed = true;
          return Promise.resolve({ ...sampleRun, status: "pending" });
        },
      },
    });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByLabelText("Zeile 1 auswählen"));
    fireEvent.click(screen.getByRole("button", { name: "Spalten zuordnen" }));
    fireEvent.click(screen.getByRole("button", { name: "Zuordnung anwenden" }));
    expect(await screen.findByText("Datei wird analysiert")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Korrigierter Empfänger")).toBeInTheDocument(), {
      timeout: 3500,
    });
    expect(screen.queryByText("REWE Markt")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Zeile 1 auswählen")).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "1 Buchungen übernehmen" }));
    await waitFor(() => expect(lastCallWithMethod(fetchMock, "POST")?.url).toContain("/commit"));
    expect(lastCallWithMethod(fetchMock, "POST")?.init.body).toBe("{}");
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("mapping-profiles") && init?.method === "POST",
      ),
    ).toHaveLength(0);
  });

  it("löscht eine gespeicherte Zuordnung und kehrt zur automatischen Erkennung zurück", async () => {
    const { fetchMock } = renderImportPage({ profiles: [savedProfile] });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByText(/Gespeicherte Zuordnungen/));
    const select = screen.getByLabelText("Zuordnung für diese Datei");
    expect(select).toHaveValue("");
    fireEvent.change(select, { target: { value: "profile-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Zuordnung MLP nur löschen" }));
    expect(screen.getByText("Nur die Zuordnung wird gelöscht.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Löschen bestätigen" }));
    await waitFor(() =>
      expect(screen.queryByRole("option", { name: /MLP nur/ })).not.toBeInTheDocument(),
    );
    expect(select).toHaveValue("");
    expect(screen.getByText(/Bereits importierte Buchungen bleiben erhalten/)).toBeInTheDocument();
    expect(lastCallWithMethod(fetchMock, "DELETE")?.url).toContain("/mapping-profiles/profile-1");
    expect(screen.getByText("REWE Markt")).toBeInTheDocument();
  });

  it("behält eine Zuordnung sichtbar, wenn das Löschen fehlschlägt", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderImportPage({
      profiles: [savedProfile],
      profileDeleteResponse: {
        ok: false,
        status: 403,
        json: () => Promise.resolve({ detail: "Nur Leserecht" }),
      },
    });
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByText(/Gespeicherte Zuordnungen/));
    fireEvent.click(screen.getByRole("button", { name: "Zuordnung MLP nur löschen" }));
    fireEvent.click(screen.getByRole("button", { name: "Löschen bestätigen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Nur Leserecht");
    expect(screen.getByRole("option", { name: /MLP nur/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Löschen bestätigen" })).toBeEnabled();
  });

  it("zeigt beim Upload sofort Fortschritt, verhindert Doppelklicks und leert Dateifeld und Profil", async () => {
    let resolveUpload!: (response: MockedResponse) => void;
    const { fetchMock } = renderImportPage({
      profiles: [savedProfile],
      uploadResponse: new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    });
    await screen.findByText("REWE Markt");
    fireEvent.change(screen.getByLabelText("Zielkonto"), { target: { value: "account-1" } });
    const file = new File(["Datum;Betrag\n01.09.2026;12,00"], "September.csv", {
      type: "text/csv",
    });
    const initialInput = screen.getByLabelText("Datei auswählen");
    fireEvent.change(initialInput, { target: { files: [file] } });
    fireEvent.click(screen.getByText(/Gespeicherte Zuordnungen/));
    fireEvent.change(screen.getByLabelText("Zuordnung für diese Datei"), {
      target: { value: "profile-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Datei analysieren" }));
    const pending = await screen.findByRole("button", { name: "Wird hochgeladen …" });
    expect(pending).toBeDisabled();
    fireEvent.click(pending);
    expect(screen.getByText(/Datei wird übertragen/)).toBeInTheDocument();
    await act(async () =>
      resolveUpload({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...mockImportRun, filename: file.name }),
      }),
    );
    await waitFor(() => expect(screen.getByLabelText("Datei auswählen")).not.toBe(initialInput));
    expect(screen.getByLabelText("Datei auswählen")).toHaveValue("");
    expect(screen.getByLabelText("Zuordnung für diese Datei")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Datei entfernen" })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("/upload") && init?.method === "POST",
      ),
    ).toHaveLength(1);
  });

  it("zeigt abgeschlossene Imports mit aktuellen Zeilenstatus und ohne Übernahme-Aktion", async () => {
    renderImportPage({
      run: { ...mockCommittedRun, committed_count: 3, new_count: 99 },
      rows: mockImportRows.map((row) => ({ ...row, status: "committed" })),
    });
    expect(await screen.findByText("3 Buchungen übernommen")).toBeInTheDocument();
    await screen.findByText("REWE Markt");
    expect(screen.getByRole("button", { name: /^Neu/ })).toHaveTextContent("0");
    expect(screen.queryByRole("button", { name: /Buchungen übernehmen/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/neue Buchungen bereit/)).not.toBeInTheDocument();
  });

  it("übernimmt explizite Dubletten und macht leere Auswahl nicht wieder zu Alle", async () => {
    const { fetchMock } = renderImportPage();
    await screen.findByText("REWE Markt");
    fireEvent.click(screen.getByLabelText("Zeile 2 auswählen"));
    expect(screen.getByText(/Ausgewählte Dubletten/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Zeile 2 auswählen"));
    expect(screen.getByRole("button", { name: "0 Buchungen übernehmen" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Zeile 2 auswählen"));
    fireEvent.click(screen.getByRole("button", { name: "1 Buchungen übernehmen" }));
    await waitFor(() =>
      expect(lastCallWithMethod(fetchMock, "POST")?.init.body).toBe(
        JSON.stringify({ row_ids: ["row-2"] }),
      ),
    );
  });

  it("rendert das Importzentrum, Datei-Upload, Staging-Tabelle und Metriken", async () => {
    renderImportPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Importzentrum" })).toBeInTheDocument();
      expect(screen.getAllByText(/statement_2026.csv/).length).toBeGreaterThan(0);
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
      expect(screen.getByText("Muster AG")).toBeInTheDocument();
      expect(screen.getByText("Arbeitgeber GmbH")).toBeInTheDocument();
    });
  });

  it("filtert Zeilen nach Status (Nur Neue / Nur Dubletten)", async () => {
    renderImportPage();

    await waitFor(() => {
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    });

    const onlyNewBtn = screen.getByRole("button", { name: /^Neu/ });
    fireEvent.click(onlyNewBtn);

    expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    expect(screen.getByText("Arbeitgeber GmbH")).toBeInTheDocument();
    expect(screen.queryByText("Muster AG")).not.toBeInTheDocument();

    const onlyDupesBtn = screen.getByRole("button", { name: /^Dubletten/ });
    fireEvent.click(onlyDupesBtn);

    expect(screen.getByText("Muster AG")).toBeInTheDocument();
    expect(screen.queryByText("REWE Markt")).not.toBeInTheDocument();
  });

  it("führt die Commit-Aktion aus und zeigt die Bestätigung an", async () => {
    renderImportPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /2 Buchungen übernehmen/ })).toBeInTheDocument();
    });

    const commitBtn = screen.getByRole("button", { name: /2 Buchungen übernehmen/ });
    fireEvent.click(commitBtn);

    await waitFor(() => {
      expect(screen.getByText(/2 Buchungen erfolgreich ins Konto übernommen/)).toBeInTheDocument();
    });
  });

  it("zeigt bei einem HTTP-Fehler die Meldung des Servers statt 'API nicht erreichbar'", async () => {
    // Wortlaut wie im Backend (services/api/.../imports.py) - die Meldung nennt
    // seit der nachtraeglichen Zuweisung ausdruecklich, dass kein erneuter
    // Upload noetig ist.
    const serverDetail =
      "Dem Importlauf ist kein Zielkonto zugewiesen. Bitte weise dem Importlauf ein Zielkonto zu - " +
      "das geht nachtraeglich, ein erneuter Upload ist dafuer nicht noetig.";
    renderImportPage({
      commitResponse: {
        ok: false,
        status: 400,
        json: () => Promise.resolve({ detail: serverDetail }),
      },
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /2 Buchungen übernehmen/ })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /2 Buchungen übernehmen/ }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(serverDetail);
    });
    expect(screen.queryByText(/Die API ist nicht erreichbar/)).not.toBeInTheDocument();
  });

  it("deaktiviert 'Übernehmen' mit Begründung, solange der Lauf kein Zielkonto hat", async () => {
    renderImportPage({ run: { ...mockImportRun, account_id: null } });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /2 Buchungen übernehmen/ })).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: /2 Buchungen übernehmen/ })).toBeDisabled();
    expect(screen.getByText(/Bitte zuerst ein Zielkonto zuweisen/)).toBeInTheDocument();
    expect(
      screen.getAllByText(/Diesem Importlauf ist kein Zielkonto zugewiesen/).length,
    ).toBeGreaterThan(0);
  });

  it("bietet bei einem Lauf ohne Zielkonto eine Kontoauswahl an und weist das Konto per PATCH zu", async () => {
    const { fetchMock } = renderImportPage({ run: { ...mockImportRun, account_id: null } });

    const select = await screen.findByLabelText(/Zielkonto nachträglich zuweisen/);
    // Der alte, falsche Hinweis auf einen erneuten Upload darf nicht mehr da sein.
    expect(screen.queryByText(/lässt sich nachträglich nicht ergänzen/)).not.toBeInTheDocument();

    const assignBtn = screen.getByRole("button", { name: "Zielkonto zuweisen" });
    expect(assignBtn).toBeDisabled();

    fireEvent.change(select, { target: { value: "account-1" } });
    expect(assignBtn).toBeEnabled();
    fireEvent.click(assignBtn);

    await waitFor(() => {
      expect(lastCallWithMethod(fetchMock, "PATCH")).not.toBeNull();
    });
    const patchCall = lastCallWithMethod(fetchMock, "PATCH")!;
    expect(patchCall.url).toContain("/v1/imports/import-run-1");
    expect(patchCall.init.body).toBe(JSON.stringify({ account_id: "account-1" }));

    await waitFor(() => {
      expect(screen.getByText(/Zielkonto zugewiesen: Girokonto/)).toBeInTheDocument();
    });
  });

  it("zeigt keine Kontoauswahl, wenn der Lauf bereits ein Zielkonto hat", async () => {
    renderImportPage();

    await waitFor(() => {
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    });

    expect(screen.queryByLabelText(/Zielkonto nachträglich zuweisen/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zielkonto zuweisen" })).not.toBeInTheDocument();
  });

  it("zeigt keine Kontoauswahl, wenn der Lauf bereits übernommen wurde", async () => {
    renderImportPage({ run: { ...mockImportRun, account_id: null, status: "completed" } });

    await waitFor(() => {
      expect(
        screen.getAllByText(/Diesem Importlauf ist kein Zielkonto zugewiesen/).length,
      ).toBeGreaterThan(0);
    });

    expect(screen.queryByLabelText(/Zielkonto nachträglich zuweisen/)).not.toBeInTheDocument();
    expect(screen.getByText(/Nachtragen lässt sich das Zielkonto nur/)).toBeInTheDocument();
  });

  it("verweist auf das Anlegen eines Kontos, wenn der Haushalt gar kein Konto hat", async () => {
    renderImportPage({ run: { ...mockImportRun, account_id: null }, accounts: [] });

    await waitFor(() => {
      expect(screen.getByText(/In diesem Haushalt existiert noch kein Konto/)).toBeInTheDocument();
    });

    expect(screen.queryByLabelText(/Zielkonto nachträglich zuweisen/)).not.toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Konten" });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]).toHaveAttribute("href", "/konten");
  });

  it("zeigt die Servermeldung, wenn das Zuweisen des Zielkontos abgelehnt wird", async () => {
    const serverDetail =
      "Der Importlauf kann nicht mehr geaendert werden: Status ist 'completed', aenderbar ist nur " +
      "ein Lauf im Status 'ready'.";
    renderImportPage({
      run: { ...mockImportRun, account_id: null },
      patchResponse: {
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail: serverDetail }),
      },
    });

    const select = await screen.findByLabelText(/Zielkonto nachträglich zuweisen/);
    fireEvent.change(select, { target: { value: "account-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Zielkonto zuweisen" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(serverDetail);
    });
    expect(screen.queryByText(/Die API ist nicht erreichbar/)).not.toBeInTheDocument();
  });

  it("entfernt einen Importlauf erst nach Rückfrage und ruft dabei DELETE auf", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { fetchMock } = renderImportPage();

    const deleteBtn = await screen.findByRole("button", { name: "Entfernen" });
    fireEvent.click(deleteBtn);

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0]?.[0]).toContain("statement_2026.csv");

    await waitFor(() => {
      expect(lastCallWithMethod(fetchMock, "DELETE")).not.toBeNull();
    });
    expect(lastCallWithMethod(fetchMock, "DELETE")!.url).toContain("/v1/imports/import-run-1");

    await waitFor(() => {
      expect(
        screen.getByText(/wurde samt seiner eingelesenen Zeilen entfernt/),
      ).toBeInTheDocument();
    });
    confirmSpy.mockRestore();
  });

  it("entfernt nichts, wenn die Rückfrage abgebrochen wird", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { fetchMock } = renderImportPage();

    fireEvent.click(await screen.findByRole("button", { name: "Entfernen" }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(lastCallWithMethod(fetchMock, "DELETE")).toBeNull();
    confirmSpy.mockRestore();
  });

  it("bietet bei einem bereits übernommenen Lauf das Entfernen der Buchungen statt des Laufs an", async () => {
    renderImportPage({ run: mockCommittedRun });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Anzeigen" })).toBeInTheDocument();
    });

    // Der Lauf selbst bleibt gesperrt - er ist der Herkunftsnachweis der
    // entstandenen Buchungen. Statt einer blossen Begruendung gibt es aber
    // den Weg, der tatsaechlich hilft.
    expect(screen.queryByRole("button", { name: "Entfernen" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Buchungen entfernen" })).toBeInTheDocument();
  });

  it("bietet den Weg auch bei einem teilweise übernommenen Lauf auf 'ready' an", async () => {
    // Der Fall aus der Praxis: die neuen Zeilen sind uebernommen, die offenen
    // Dubletten halten den Lauf auf 'ready'. Er sieht damit unberuehrt aus,
    // ist es aber nicht - frueher bot die Zeile hier ein Entfernen an, das der
    // Server mit 409 ablehnte, ohne eine Alternative zu nennen.
    renderImportPage({
      run: { ...mockImportRun, status: "ready", committed_count: 5, active_transaction_count: 5 },
    });

    expect(await screen.findByRole("button", { name: "Buchungen entfernen" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Entfernen" })).not.toBeInTheDocument();
  });

  it("nennt den Grund, wenn aus dem Lauf keine Buchung mehr im Bestand steht", async () => {
    // Bereits zurueckgenommen: entfernen bleibt gesperrt (committed_count
    // steht), zu entfernen gibt es aber auch nichts mehr. Ein Knopf waere hier
    // eine Sackgasse.
    renderImportPage({ run: { ...mockCommittedRun, active_transaction_count: 0 } });

    expect(await screen.findByText(/Als Nachweis gespeichert/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Buchungen entfernen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Entfernen" })).not.toBeInTheDocument();
  });

  it("zeigt vor dem Entfernen, was betroffen ist und was stehen bleibt", async () => {
    renderImportPage({ run: mockCommittedRun });

    fireEvent.click(await screen.findByRole("button", { name: "Buchungen entfernen" }));

    expect(await screen.findByText("4 von 5 Buchungen")).toBeInTheDocument();
    expect(screen.getByText("2026-04-02 bis 2026-04-30")).toBeInTheDocument();
    expect(screen.getByText(/nachtraeglich bearbeitet/)).toBeInTheDocument();
    // Der Bestaetigungsknopf nennt die Zahl, damit niemand blind zustimmt.
    expect(
      await screen.findByRole("button", { name: "4 Buchungen entfernen" }),
    ).toBeInTheDocument();
  });

  it("entfernt die Buchungen und meldet, was stehen blieb", async () => {
    const { fetchMock } = renderImportPage({ run: mockCommittedRun });

    fireEvent.click(await screen.findByRole("button", { name: "Buchungen entfernen" }));
    fireEvent.click(await screen.findByRole("button", { name: "4 Buchungen entfernen" }));

    expect(
      await screen.findByText(
        /wurden 4 Buchungen entfernt\. 1 blieben stehen, weil daran inzwischen etwas hängt\./,
      ),
    ).toBeInTheDocument();
    // Der Ablauf, um dessentwillen es die Aktion gibt, steht in der Meldung.
    expect(screen.getByText(/Die Datei lässt sich jetzt erneut einlesen/)).toBeInTheDocument();
    expect(lastCallWithMethod(fetchMock, "POST")?.url).toContain("/v1/imports/import-run-1/revert");
  });

  it("nimmt mit gesetztem Haken auch die verknüpften Buchungen mit", async () => {
    const { fetchMock } = renderImportPage({ run: mockCommittedRun });

    fireEvent.click(await screen.findByRole("button", { name: "Buchungen entfernen" }));
    await screen.findByText("4 von 5 Buchungen");

    fireEvent.click(screen.getByRole("checkbox", { name: /Auch diese/ }));

    // Anzahl, Summe und Zeitraum schalten auf den groesseren Satz um - ohne
    // Nachladen, damit der Dialog nicht zwischendurch einen anderen Bestand
    // sieht als den, den er gerade gezeigt hat.
    expect(screen.getByText("5 von 5 Buchungen")).toBeInTheDocument();
    expect(screen.getByText("2026-04-01 bis 2026-04-30")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "5 Buchungen entfernen" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "5 Buchungen entfernen" }));

    await waitFor(() => {
      const call = lastCallWithMethod(fetchMock, "POST");
      expect(call?.url).toContain("/v1/imports/import-run-1/revert");
      expect(JSON.parse(String(call?.init.body))).toEqual({ include_linked: true });
    });
  });

  it("nennt nach dem Mitnehmen, welche Verknüpfungen dabei gelöst wurden", async () => {
    renderImportPage({
      run: mockCommittedRun,
      revertResponse: {
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            import_run_id: "import-run-1",
            reverted: 5,
            kept: 0,
            blocked: [],
            untangled: [
              {
                kind: "vertrag_faelligkeit",
                label: "Vertragsfaelligkeiten wieder offen",
                count: 2,
              },
            ],
          }),
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Buchungen entfernen" }));
    await screen.findByText("4 von 5 Buchungen");
    fireEvent.click(screen.getByRole("checkbox", { name: /Auch diese/ }));
    fireEvent.click(screen.getByRole("button", { name: "5 Buchungen entfernen" }));

    // Die Nebenwirkung gehoert in die Meldung - sonst entdeckt der Nutzer die
    // wieder offene Faelligkeit erst Wochen spaeter.
    expect(
      await screen.findByText(/Dabei gelöst: 2 Vertragsfaelligkeiten wieder offen\./),
    ).toBeInTheDocument();
  });

  it("bietet den Haken nicht an, wenn gar nichts verknüpft ist", async () => {
    renderImportPage({
      run: mockCommittedRun,
      revertPreviewResponse: {
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            import_run_id: "import-run-1",
            filename: "umsaetze.csv",
            transactions_total: 4,
            revertable: 4,
            sum_minor: -12500,
            from_date: "2026-04-02",
            to_date: "2026-04-30",
            sum_minor_with_linked: -12500,
            from_date_with_linked: "2026-04-02",
            to_date_with_linked: "2026-04-30",
            blocked: [],
          }),
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Buchungen entfernen" }));
    await screen.findByText("4 von 4 Buchungen");

    expect(screen.queryByRole("checkbox", { name: /Auch diese/ })).not.toBeInTheDocument();
  });

  it("lässt das Entfernen nicht zu, wenn die Vorschau nichts mehr findet", async () => {
    renderImportPage({
      run: mockCommittedRun,
      revertPreviewResponse: {
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            import_run_id: "import-run-1",
            filename: "umsaetze.csv",
            transactions_total: 0,
            revertable: 0,
            sum_minor: 0,
            from_date: null,
            to_date: null,
            blocked: [],
          }),
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Buchungen entfernen" }));

    expect(await screen.findByText(/Es gibt nichts zu entfernen/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "0 Buchungen entfernen" })).toBeDisabled();
  });

  it("zeigt die Servermeldung, wenn das Entfernen abgelehnt wird", async () => {
    const serverDetail =
      "Ein bereits uebernommener Importlauf wird nicht entfernt. Aus ihm sind bereits 105 " +
      "Buchungen entstanden, die dadurch still ihre Herkunft verlieren wuerden. Um die " +
      "Buchungen loszuwerden, nutze 'Buchungen entfernen' - danach laesst sich dieselbe " +
      "Datei erneut einlesen. Der Importlauf bleibt als Nachweis bestehen.";
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderImportPage({
      deleteResponse: {
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail: serverDetail }),
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Entfernen" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(serverDetail);
    });
    expect(screen.getByRole("button", { name: "Historie neu laden" })).toBeInTheDocument();
    expect(screen.queryByText(/Die API ist nicht erreichbar/)).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("lässt den Upload-Knopf bedienbar und nennt beim Klick, was fehlt", async () => {
    // Der Kern des gemeldeten Fehlers: der Knopf war deaktiviert, sobald
    // selectedFile leer war. Nach einem erfolgreichen Upload trifft das zu,
    // waehrend das Dateifeld weiter einen Namen anzeigte - fuer den Nutzer
    // passierte beim Klick "nichts", ohne jede Erklaerung.
    const { fetchMock } = renderImportPage();
    await waitFor(() => {
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    });

    const uploadButton = screen.getByRole("button", { name: /Datei analysieren/ });
    expect(uploadButton).toBeEnabled();

    fireEvent.click(uploadButton);

    await waitFor(() => {
      expect(screen.getByText(/Bitte eine Datei auswählen/)).toBeInTheDocument();
    });
    // Kein Upload ohne Datei - der Waechter greift, statt still zu scheitern.
    expect(lastCallWithMethod(fetchMock, "POST")).toBeNull();
  });

  it("bietet bei einem wartenden Lauf Abbrechen an statt „bitte warten“", async () => {
    // Frueher stand in der Aktionsspalte nur grauer Text; ohne laufenden
    // Worker war der Lauf damit weder zu beenden noch zu entfernen.
    const { fetchMock } = renderImportPage({
      run: { ...mockImportRun, status: "pending", total_rows: 0 },
    });

    const cancelButton = await screen.findByRole("button", { name: "Abbrechen" });
    expect(screen.queryByText(/bitte warten und die Historie/)).not.toBeInTheDocument();

    fireEvent.click(cancelButton);

    await waitFor(() => {
      expect(screen.getByText(/wurde abgebrochen/)).toBeInTheDocument();
    });
    const call = lastCallWithMethod(fetchMock, "POST");
    expect(call?.url).toContain("/v1/imports/import-run-1/cancel");
  });

  it("sagt es, wenn der Hintergrunddienst gar nicht läuft", async () => {
    renderImportPage({
      run: { ...mockImportRun, status: "pending", total_rows: 0 },
      healthResponse: {
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            status: "degraded",
            services: [
              {
                service: "worker",
                status: "down",
                detail: "kein Heartbeat gefunden",
                checkedAt: "2026-08-05T12:00:00Z",
              },
            ],
          }),
      },
    });

    await waitFor(() => {
      expect(screen.getByText(/Die Verarbeitung steht still/)).toBeInTheDocument();
    });
    expect(screen.getByText(/kein Heartbeat gefunden/)).toBeInTheDocument();
    // Die Zusicherung, dass nichts verloren geht, gehoert dazu - sonst laedt
    // der Nutzer dieselbe Datei mehrfach hoch.
    expect(screen.getByText(/Die Datei bleibt gespeichert/)).toBeInTheDocument();
  });

  it("zeigt bei needs_mapping sofort die Spaltenzuordnung mit optionaler KI-Hilfe", async () => {
    renderImportPage({
      run: {
        ...mockImportRun,
        status: "needs_mapping",
        detected_columns: ["Wertstellung", "Zahlungsbetrag"],
      },
    });
    expect(await screen.findByLabelText(/Datum \(erforderlich\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("Live-Vorschau")).toBeInTheDocument();
    expect(screen.getByText("Hilfe bei der Zuordnung").closest("details")).not.toHaveAttribute(
      "open",
    );
  });

  it("zeigt die Originalspalten der Datei auf Wunsch an", async () => {
    // raw_data lieferte der Server immer, der Client zeigte es nie - genau
    // deshalb war nicht erkennbar, welche Spalte wo gelandet ist.
    renderImportPage({
      run: { ...mockImportRun, detected_columns: ["Buchungsdatum", "Beschreibung"] },
    });

    await waitFor(() => {
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    });
    expect(screen.queryByText(/nicht zugeordnet/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Originalspalten" }));

    expect(screen.getByRole("columnheader", { name: /Buchungsdatum/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Beschreibung/ })).toBeInTheDocument();
  });

  it("zeigt den KI-Spaltenvorschlag an und übernimmt ihn erst nach Bestätigung", async () => {
    renderImportPage();

    await waitFor(() => {
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Spalten zuordnen/ }));
    // Die Auswahl startet LEER. Eine Vorbelegung waere hier geraten - welche
    // Spalten die Datei fuehrt, steht erst nach dem Einlesen fest.
    const dateSelect = screen.getByLabelText(/Datum \(erforderlich\)/);
    expect(dateSelect).toHaveValue("");

    fireEvent.click(screen.getByText("Hilfe bei der Zuordnung"));
    fireEvent.click(screen.getByRole("button", { name: /Spaltenvorschlag von der lokalen KI/ }));

    await waitFor(() => {
      expect(screen.getByText(/google\/gemma-4-26b-a4b-qat/)).toBeInTheDocument();
    });
    expect(screen.getByText("KI-Vorschlag")).toBeInTheDocument();
    // Nicht still angewendet: die Auswahl bleibt leer, bis der Nutzer klickt.
    expect(dateSelect).toHaveValue("");

    fireEvent.click(screen.getByRole("button", { name: /Vorschlag ins Formular übernehmen/ }));

    expect(dateSelect).toHaveValue("Buchungstag");
    expect(screen.getByLabelText(/Betrag \(erforderlich\)/)).toHaveValue("Umsatz");
  });

  it("stört den Ablauf nicht, wenn kein KI-Vorschlag verfügbar ist", async () => {
    renderImportPage({
      suggestionResponse: {
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            ...mockSuggestion,
            available: false,
            reason: "model_unavailable",
            message:
              "Der Modellserver ist nicht erreichbar oder bietet kein chatfähiges Modell an.",
            column_mapping: {},
            model_name: null,
          }),
      },
    });

    await waitFor(() => {
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Spalten zuordnen/ }));
    fireEvent.click(screen.getByText("Hilfe bei der Zuordnung"));
    fireEvent.click(screen.getByRole("button", { name: /Spaltenvorschlag von der lokalen KI/ }));

    await waitFor(() => {
      expect(screen.getByText(/Kein KI-Vorschlag verfügbar/)).toHaveTextContent(
        /Der Modellserver ist nicht erreichbar/,
      );
    });
    // Der Grund bringt seine Abhilfe mit - der Schluessel steht nicht im Text.
    const notice = screen.getByText(/Kein KI-Vorschlag verfügbar/);
    expect(notice).toHaveTextContent("LM Studio starten und ein Modell laden.");
    expect(notice).not.toHaveTextContent("model_unavailable");
    // Die Zuordnung von Hand bleibt unveraendert nutzbar - ein KI-Ausfall
    // darf den Import nie blockieren.
    expect(screen.getByLabelText(/Datum \(erforderlich\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Zuordnung anwenden/ })).toBeInTheDocument();
  });

  it("nennt beim KI-Spaltenvorschlag den Grund des KI-Kerns samt Weg zur Einstellung", async () => {
    renderImportPage({
      suggestionResponse: {
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            ...mockSuggestion,
            available: false,
            reason: "model_budget_exhausted",
            message:
              "Das Antwortbudget war aufgebraucht, bevor eine Antwort stand - meist hat das Denken (Reasoning) den freien Platz verbraucht.",
            column_mapping: {},
          }),
      },
    });

    await waitFor(() => {
      expect(screen.getByText("REWE Markt")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Spalten zuordnen/ }));
    fireEvent.click(screen.getByText("Hilfe bei der Zuordnung"));
    fireEvent.click(screen.getByRole("button", { name: /Spaltenvorschlag von der lokalen KI/ }));

    const notice = await screen.findByText(/Kein KI-Vorschlag verfügbar/);
    expect(notice).toHaveTextContent("Das Antwortbudget war aufgebraucht");
    expect(
      screen.getByRole("link", { name: "Reasoning unter Einstellungen › KI umstellen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
    expect(screen.queryByText("KI-Vorschlag")).not.toBeInTheDocument();
  });
});
