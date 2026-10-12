import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { CostImportExportPanel } from "../components/projects/CostImportExportPanel";

// Excel-/CSV-Kostenplanimport (Plan 10, P4): Preview/Apply-Assistent im Tab
// "Import/Export" der Projektdetailseite.

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

function meResponse() {
  return {
    user_id: "user-1",
    display_name: null,
    language: "de",
    timezone: "Europe/Vienna",
    currency: "EUR",
    households: [{ household_id: "household-1", name: "Testhaushalt", role: "owner" }],
  };
}

const previewResponse = {
  preview_fingerprint: "fp-1",
  detected_format: "csv_generic",
  sheet_names: [],
  selected_sheet: null,
  rows: [
    {
      row_number: 1,
      section_name: "Rohbau",
      item_name: "Fundament",
      status: "estimated",
      calculation_mode: "fixed",
      quantity: null,
      unit_label: null,
      unit_price_minor: null,
      estimate_manual_minor: 500000,
      quoted_amount_minor: null,
      committed_amount_minor: null,
      vendor_label: null,
      expected_start_on: null,
      expected_end_on: null,
      note: null,
      matched_section_id: "section-1",
      matched_item_id: "item-1",
      errors: [],
    },
    {
      row_number: 2,
      section_name: "Rohbau",
      item_name: null,
      status: "unknown",
      calculation_mode: "fixed",
      quantity: null,
      unit_label: null,
      unit_price_minor: null,
      estimate_manual_minor: null,
      quoted_amount_minor: null,
      committed_amount_minor: null,
      vendor_label: null,
      expected_start_on: null,
      expected_end_on: null,
      note: null,
      matched_section_id: null,
      matched_item_id: null,
      errors: ["item_name fehlt"],
    },
  ],
  sums: {
    row_count: 2,
    rows_with_errors_count: 1,
    new_items_count: 1,
    matched_items_count: 1,
    estimate_total_minor: 500000,
    quoted_total_minor: 0,
    committed_total_minor: 0,
  },
};

const applyResult = {
  request_id: "req-1",
  idempotent_replay: false,
  created_item_ids: ["new-item-1"],
  updated_item_ids: ["item-1"],
  skipped_row_numbers: [2],
};

interface RenderOptions {
  applyStatus?: "success" | "conflict";
}

function renderPanel(options: RenderOptions = {}) {
  const applyStatus = options.applyStatus ?? "success";

  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.includes("/v1/me")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse()) });
    }
    if (url.includes("/cost-import-profiles") && method === "GET") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
    if (url.includes("/cost-import/preview") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(previewResponse),
      });
    }
    if (url.includes("/cost-import/apply") && method === "POST") {
      if (applyStatus === "conflict") {
        return Promise.resolve({
          ok: false,
          status: 409,
          text: () => Promise.resolve(JSON.stringify({ detail: "preview_fingerprint_mismatch" })),
          json: () => Promise.resolve({ detail: "preview_fingerprint_mismatch" }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(applyResult) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <HouseholdProvider>
          <CostImportExportPanel projectId="project-1" scenarioId="scenario-1" />
        </HouseholdProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
  return fetchMock;
}

async function goToPreviewStep() {
  await userEvent.click(screen.getByRole("button", { name: "Kostenplan importieren" }));
  await screen.findByText("Kostenplan importieren", { selector: "h2" });

  const file = new File(["Abschnitt,Position\nRohbau,Fundament"], "kostenplan.csv", {
    type: "text/csv",
  });
  const fileInput = screen.getByLabelText(/Datei \(\.csv, \.xlsx\)/) as HTMLInputElement;
  await userEvent.upload(fileInput, file);

  await userEvent.click(screen.getByRole("button", { name: "Vorschau anzeigen" }));
  await screen.findByText("Fundament");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Kostenplan-Import", () => {
  it("zeigt die Vorschauzeilen und Summen", async () => {
    renderPanel();
    await goToPreviewStep();

    expect(screen.getAllByText("Rohbau")).toHaveLength(2);
    expect(screen.getByText("item_name fehlt")).toBeInTheDocument();
    expect(screen.getAllByText("5.000,00 €").length).toBeGreaterThan(0);

    // Sums-Kacheln (Beschriftungen sind eindeutig, die Zahlenwerte
    // ueberschneiden sich mit den Zeilennummern der Vorschautabelle).
    expect(screen.getByText("Zeilen")).toBeInTheDocument();
    expect(screen.getByText("Neue Positionen")).toBeInTheDocument();
    expect(screen.getByText("Erkannte Positionen")).toBeInTheDocument();
    expect(screen.getByText("Zeilen mit Fehlern")).toBeInTheDocument();
  });

  it("zwingt eine Zeile mit Fehlern zum Ueberspringen", async () => {
    renderPanel();
    await goToPreviewStep();

    const errorRowSelect = screen.getByLabelText("Aktion für Zeile 2") as HTMLSelectElement;
    expect(errorRowSelect).toBeDisabled();
    expect(errorRowSelect).toHaveValue("skip");
  });

  it("sendet beim Anwenden genau die vom Nutzer gewaehlten Resolutions", async () => {
    const fetchMock = renderPanel();
    await goToPreviewStep();

    // Zeile 1 ist vorbelegt mit "update" (matched_item_id gesetzt) - Nutzer
    // waehlt stattdessen ausdruecklich "Neu anlegen".
    const row1Select = screen.getByLabelText("Aktion für Zeile 1") as HTMLSelectElement;
    expect(row1Select).toHaveValue("update");
    await userEvent.selectOptions(row1Select, "create");

    await userEvent.click(screen.getByRole("button", { name: "Import anwenden" }));

    await screen.findByText(/Position\(en\) angelegt/);

    const applyCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        typeof input === "string" &&
        input.includes("/cost-import/apply") &&
        init?.method === "POST",
    );
    expect(applyCall).toBeDefined();
    const formData = applyCall?.[1]?.body as FormData;
    const resolutions = JSON.parse(formData.get("resolutions_json") as string);
    expect(resolutions).toEqual([
      { row_number: 1, action: "create", target_item_id: null },
      { row_number: 2, action: "skip", target_item_id: null },
    ]);
    expect(formData.get("preview_fingerprint")).toBe("fp-1");
    expect(typeof formData.get("request_id")).toBe("string");
  });

  it("zeigt bei 409 Fingerprint-Mismatch eine eigene Meldung statt eines generischen Fehlers", async () => {
    renderPanel({ applyStatus: "conflict" });
    await goToPreviewStep();

    await userEvent.click(screen.getByRole("button", { name: "Import anwenden" }));

    expect(
      await screen.findByText(/Die Daten haben sich seit der Vorschau geändert/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vorschau neu laden" })).toBeInTheDocument();
  });

  it("zeigt nach erfolgreichem Apply die Zusammenfassung", async () => {
    renderPanel();
    await goToPreviewStep();

    await userEvent.click(screen.getByRole("button", { name: "Import anwenden" }));

    expect(
      await screen.findByText("1 Position(en) angelegt, 1 aktualisiert, 1 übersprungen."),
    ).toBeInTheDocument();
  });
});
