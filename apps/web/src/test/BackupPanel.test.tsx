import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { BackupPanel } from "../components/BackupPanel";

// Einstellungen → Sicherung. Geprueft wird vor allem das, was NICHT passieren
// darf: kein Einspielen ohne Vorschau, keines ohne abgetippten Haushaltsnamen,
// und keines fuer jemanden, dem der Haushalt nicht gehoert.

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

function meResponse(role: string) {
  return {
    user_id: "user-1",
    display_name: null,
    language: "de",
    timezone: "Europe/Vienna",
    currency: "EUR",
    households: [{ household_id: "household-1", name: "Testhaushalt", role }],
  };
}

const infoResponse = {
  household_name: "Testhaushalt",
  table_count: 3,
  total_rows: 8711,
  tables: [
    { table: "transactions", rows: 1017 },
    { table: "accounts", rows: 4 },
    { table: "categories", rows: 62 },
  ],
  excluded: ["Hochgeladene Dokumente (Rechnungen, Belege) - die liegen im Dateispeicher."],
};

const vorschauResponse = {
  household_name: "Testhaushalt",
  source_household_name: "Testhaushalt",
  source_household_id: "household-1",
  created_at: "2026-08-09T10:00:00Z",
  schema_matches: true,
  rows_in_file: 8711,
  rows_in_household: 9000,
  tables: [
    {
      table: "transactions",
      rows_in_file: 1017,
      rows_in_household: 1100,
      handling: "ersetzen",
      note: null,
    },
    {
      table: "categories",
      rows_in_file: 62,
      rows_in_household: 62,
      handling: "ueberschreiben",
      note: "Wird Zeile fuer Zeile ersetzt.",
    },
  ],
  unknown_tables: [],
};

const ergebnisResponse = {
  household_name: "Testhaushalt",
  deleted_rows: 9000,
  imported_rows: 8711,
  imported_per_table: { transactions: 1017 },
  skipped_tables: ["household_members"],
  cleared_user_references: 0,
  schema_matches: true,
};

function renderPanel({ role = "owner" }: { role?: string } = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();

    if (url.includes("/v1/backup/info")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(infoResponse) });
    }
    if (url.includes("/v1/backup/export")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: {
          get: (name: string) =>
            name === "Content-Disposition"
              ? 'attachment; filename="finanzplaner-Testhaushalt-20260809-1200.zip"'
              : null,
        },
        blob: () => Promise.resolve(new Blob(["PK"], { type: "application/zip" })),
      });
    }
    if (url.includes("/v1/backup/preview")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(vorschauResponse),
      });
    }
    if (url.includes("/v1/backup/import")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(ergebnisResponse),
      });
    }
    if (url.includes("/v1/me")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(meResponse(role)),
      });
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
            <BackupPanel />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

function sicherungsdatei(): File {
  return new File(["PK"], "sicherung.zip", { type: "application/zip" });
}

describe("BackupPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("nennt den Umfang der Sicherung, bevor sie erstellt wird", async () => {
    renderPanel();
    // Ohne Zahl ist "Sicherung" nur ein Versprechen.
    expect(await screen.findByText(/8\.711 Datensätze/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sicherung herunterladen/ })).toBeEnabled();
  });

  it("lädt die Datei unter dem Namen herunter, den der Server vorgibt", async () => {
    const erzeugt = vi.fn(() => "blob:test");
    vi.stubGlobal("URL", { createObjectURL: erzeugt, revokeObjectURL: vi.fn() });
    const klick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    renderPanel();
    await screen.findByText(/8\.711 Datensätze/);
    await userEvent.click(screen.getByRole("button", { name: /Sicherung herunterladen/ }));

    await waitFor(() => expect(klick).toHaveBeenCalled());
    expect(erzeugt).toHaveBeenCalled();
  });

  it("zeigt nach der Dateiwahl erst die Vorschau, nicht das Ergebnis", async () => {
    renderPanel();
    await screen.findByText(/8\.711 Datensätze/);

    await userEvent.upload(screen.getByLabelText(/Sicherungsdatei/), sicherungsdatei());

    expect(await screen.findByText(/Sie enthält 8\.711 Datensätze/)).toBeInTheDocument();
    // Der Knopf steht da, ist aber gesperrt - der Name fehlt noch.
    expect(screen.getByRole("button", { name: /8\.711 Datensätze einspielen/ })).toBeDisabled();
  });

  it("gibt das Einspielen erst nach dem abgetippten Haushaltsnamen frei", async () => {
    const fetchMock = renderPanel();
    await screen.findByText(/8\.711 Datensätze/);
    await userEvent.upload(screen.getByLabelText(/Sicherungsdatei/), sicherungsdatei());
    await screen.findByText(/Sie enthält 8\.711 Datensätze/);

    const feld = screen.getByPlaceholderText("Testhaushalt");
    await userEvent.type(feld, "Falscher Name");
    expect(screen.getByRole("button", { name: /einspielen/ })).toBeDisabled();

    await userEvent.clear(feld);
    await userEvent.type(feld, "Testhaushalt");

    const knopf = screen.getByRole("button", { name: /einspielen/ });
    expect(knopf).toBeEnabled();
    await userEvent.click(knopf);

    expect(await screen.findByText(/Eingespielt\./)).toBeInTheDocument();
    const aufrufe = fetchMock.mock.calls.map(([url]) => String(url));
    expect(aufrufe.some((url) => url.includes("/v1/backup/import"))).toBe(true);
  });

  it("bietet Nicht-Eigentümern das Einspielen gar nicht erst an", async () => {
    renderPanel({ role: "editor" });
    await screen.findByText(/8\.711 Datensätze/);

    expect(await screen.findByText(/Nur die Eigentümerin oder der Eigentümer/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Sicherungsdatei/)).not.toBeInTheDocument();
  });
});
