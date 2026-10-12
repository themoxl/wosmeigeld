import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { CategoriesPage } from "../pages/CategoriesPage";

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

const catalog = {
  taxonomy_revision: 7,
  installed_taxonomy_version: 1,
  available_taxonomy_version: 1,
  groups: [
    {
      id: "grp-mobilitaet",
      name: "Mobilität",
      sort_order: 0,
      is_archived: false,
      icon: null,
      color: null,
      semantic_key: "mobility",
      origin: "standard",
      version: 1,
      updated_at: null,
      active_category_count: 1,
      archived_category_count: 1,
      categories: [
        {
          id: "cat-oepnv",
          group_id: "grp-mobilitaet",
          name: "ÖPNV & Bahn",
          default_type: "expense",
          budget_bucket: "needs",
          is_archived: false,
          icon: null,
          color: null,
          is_budgeted: true,
          sort_order: 0,
          synonyms: ["Nahverkehr"],
          description: null,
          semantic_key: "mobility.transit",
          version: 2,
          is_tax_deductible: false,
          origin: "standard",
          taxonomy_version: 1,
          archived_at: null,
          merged_into_category_id: null,
          merged_into_name: null,
          updated_at: null,
          usage: {
            transaction_splits: 12,
            payee_defaults: 0,
            classification_rules: 1,
            contracts: 0,
            budget_allocations: 3,
            budget_rollovers: 0,
            savings_goals: 0,
            classification_decisions: 0,
            total: 16,
          },
        },
        {
          id: "cat-alt",
          group_id: "grp-mobilitaet",
          name: "Alte Fahrtkosten",
          default_type: "expense",
          budget_bucket: null,
          is_archived: true,
          icon: null,
          color: null,
          is_budgeted: false,
          sort_order: 1,
          synonyms: [],
          description: null,
          semantic_key: null,
          version: 4,
          is_tax_deductible: true,
          origin: "user",
          taxonomy_version: null,
          archived_at: "2026-01-01T00:00:00Z",
          merged_into_category_id: null,
          merged_into_name: null,
          updated_at: null,
          usage: null,
        },
      ],
    },
  ],
};

const standardPackageStatus = {
  installed_version: 1,
  available_version: 1,
  installed_at: "2026-01-01T00:00:00Z",
  taxonomy_revision: 7,
  upgrade_available: false,
};

/**
 * Antwort des KI-Vorschlagslaufs: ein Cluster ohne passende Kategorie (neu)
 * und einer, der auf eine vorhandene passt. Genau diese beiden Faelle muss der
 * Dialog unterschiedlich behandeln.
 */
const suggestionPreview = {
  operation_id: "op-vorschlaege",
  expires_at: "2099-01-01T00:00:00Z",
  taxonomy_revision: 7,
  model_name: "qwen2.5-7b-instruct",
  uncategorized_transactions: 84,
  clustered_transactions: 21,
  skipped_singletons: 9,
  suggestions: [
    {
      cluster_key: "ANTHROPIC",
      payee_label: "ANTHROPIC PBC",
      transaction_count: 7,
      total_amount_minor: -14700,
      first_booked_date: "2026-01-15",
      last_booked_date: "2026-07-15",
      samples: [],
      kind: "new_category",
      confidence: 0.9,
      reason: "Wiederkehrendes Software-Abo, keine passende Kategorie vorhanden.",
      existing_category_id: null,
      existing_category_name: null,
      proposed_name: "Software & Online-Dienste",
      proposed_group_id: "grp-mobilitaet",
      proposed_group_name: "Mobilität",
      proposed_default_type: "expense",
      proposed_budget_bucket: "needs",
      proposed_icon: null,
      proposed_synonyms: ["saas", "lizenz"],
      proposed_is_tax_deductible: true,
    },
    {
      cluster_key: "DB VERTRIEB",
      payee_label: "DB VERTRIEB",
      transaction_count: 4,
      total_amount_minor: -19600,
      first_booked_date: "2026-02-01",
      last_booked_date: "2026-06-01",
      samples: [],
      kind: "existing_category",
      confidence: 0.4,
      reason: "Bahnfahrten passen zu ÖPNV & Bahn.",
      existing_category_id: "cat-oepnv",
      existing_category_name: "ÖPNV & Bahn",
      proposed_name: null,
      proposed_group_id: null,
      proposed_group_name: null,
      proposed_default_type: null,
      proposed_budget_bucket: null,
      proposed_icon: null,
      proposed_synonyms: [],
      proposed_is_tax_deductible: false,
    },
  ],
};

const categorySuggestionTask = {
  id: "task-category-suggestions",
  task_type: "category_suggestions",
  status: "awaiting_confirmation",
  queue_order: 1,
  request_id: "test-request",
  input: {},
  result: suggestionPreview,
  result_summary: { suggestions: 2 },
  route_path: "/kategorien?ai_task=task-category-suggestions",
  current_stage: "awaiting_confirmation",
  progress_current: 0,
  progress_total: null,
  status_message: "2 Kategorienvorschläge warten auf deine Prüfung.",
  model_name: "qwen2.5-7b-instruct",
  error_message: null,
  cancel_requested: false,
  attempt_count: 1,
  category_operation_id: suggestionPreview.operation_id,
  classification_run_id: null,
  created_at: "2026-08-05T10:00:00Z",
  started_at: "2026-08-05T10:00:01Z",
  completed_at: "2026-08-05T10:00:02Z",
  heartbeat_at: "2026-08-05T10:00:02Z",
  updated_at: "2026-08-05T10:00:02Z",
  is_stale: false,
};

function renderCategoriesPage(
  role = "owner",
  task: Record<string, unknown> = categorySuggestionTask,
) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/v1/ai/tasks") && init?.method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 201,
        json: () => Promise.resolve(task),
      });
    }
    if (url.includes("/v1/ai/tasks/task-category-suggestions")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...task, events: [] }),
      });
    }
    if (url.includes("/v1/categories/suggestions/apply")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            created_categories: 1,
            linked_existing: 0,
            assigned_transactions: 7,
            skipped_transactions: 0,
            items: [],
            idempotent_replay: false,
          }),
      });
    }
    if (url.includes("/v1/me")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(meResponse(role)),
      });
    }
    if (url.includes("/v1/categories/catalog")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(catalog) });
    }
    if (url.includes("/v1/categories/standard-package")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(standardPackageStatus),
      });
    }
    if (url.endsWith("/v1/categories") && init?.method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 201,
        json: () =>
          Promise.resolve({
            ...catalog.groups[0]!.categories[0],
            id: "cat-neu",
            name: "Carsharing",
          }),
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
            <CategoriesPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

describe("CategoriesPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rendert Gruppen als Tabelle mit Spalten für Art, Budget, Steuer und Nutzung", async () => {
    renderCategoriesPage();

    expect(await screen.findByRole("heading", { name: "Mobilität" })).toBeInTheDocument();
    expect(screen.getByText("1 aktiv · 1 archiviert")).toBeInTheDocument();

    // Feste Spaltenkanten statt frei flatternder Meta-Paare - genau darum ist
    // es eine Tabelle. "Gruppe" fehlt bewusst: sie steht in der Überschrift.
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Kategorie", "Art", "Budget", "Steuer", "Nutzung", "Aktionen"]);

    const row = screen.getByRole("row", { name: /ÖPNV & Bahn/ });
    const cells = within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells.slice(0, 5)).toEqual(["ÖPNV & Bahn", "Ausgabe", "Bedarf", "—", "16"]);

    // Die archivierte Kategorie steckt hinter dem Umschalter, nicht in der
    // aktiven Liste.
    expect(screen.queryByText("Alte Fahrtkosten")).not.toBeInTheDocument();
  });

  it("zeigt Steuerrelevanz und einen fehlenden Budgetbereich als eigene Angabe", async () => {
    const user = userEvent.setup();
    renderCategoriesPage();

    await screen.findByRole("heading", { name: "Mobilität" });
    await user.click(screen.getByRole("button", { name: "Archiviert" }));

    const row = await screen.findByRole("row", { name: /Alte Fahrtkosten/ });
    const cells = within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    // Ohne Budgetbereich und nicht budgetiert stand früher zweimal dasselbe
    // ("kein Budgetbereich" + "nicht im Budget") - jetzt einmal.
    expect(cells[2]).toBe("nicht budgetiert");
    expect(cells[3]).toBe("absetzbar");
  });

  it("legt selten gebrauchte Zeilenaktionen hinter ein Punktmenü", async () => {
    const user = userEvent.setup();
    renderCategoriesPage();

    await screen.findByRole("heading", { name: "Mobilität" });

    // Geschlossen: nur Bearbeiten und die beiden Sortierpfeile sind sichtbar.
    expect(screen.queryByRole("menuitem", { name: "Archivieren" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Weitere Aktionen für ÖPNV & Bahn" }));

    expect(
      screen.getByRole("menuitem", { name: "In andere Gruppe verschieben" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Archivieren" })).toBeInTheDocument();
    // Owner-only laut Plan.
    expect(screen.getByRole("menuitem", { name: "Zusammenführen" })).toBeInTheDocument();
  });

  it("zeigt archivierte Einträge über den Umschalter mit Badge und Wiederherstellen-Aktion", async () => {
    const user = userEvent.setup();
    renderCategoriesPage();

    await screen.findByRole("heading", { name: "Mobilität" });
    await user.click(screen.getByRole("button", { name: "Archiviert" }));

    expect(await screen.findByText("Alte Fahrtkosten")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Wiederherstellen" })).toBeInTheDocument();
  });

  it("legt eine neue Kategorie über das Formular an", async () => {
    const user = userEvent.setup();
    const fetchMock = renderCategoriesPage();

    await screen.findByRole("heading", { name: "Mobilität" });
    await user.click(screen.getByRole("button", { name: "Kategorie hinzufügen" }));

    await user.type(await screen.findByLabelText("Name *"), "Carsharing");
    await user.selectOptions(screen.getByLabelText("Budgetbereich"), "wants");
    await user.type(screen.getByLabelText("Synonyme (kommagetrennt)"), "Miles, Sixt share");
    await user.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/categories") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall![1] as RequestInit).body)) as {
        name: string;
        group_id: string;
        budget_bucket: string;
        synonyms: string[];
      };
      expect(body.name).toBe("Carsharing");
      expect(body.group_id).toBe("grp-mobilitaet");
      expect(body.budget_bucket).toBe("wants");
      expect(body.synonyms).toEqual(["Miles", "Sixt share"]);
    });
  });

  it("schlägt Kategorien aus den Buchungen vor und übernimmt nur das Ausgewählte", async () => {
    const user = userEvent.setup();
    const fetchMock = renderCategoriesPage();

    await screen.findByRole("heading", { name: "Mobilität" });
    await user.click(screen.getByRole("button", { name: "Kategorien vorschlagen (KI)" }));

    // Der Lauf startet erst auf Knopfdruck - er belegt ein lokales Modell.
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/v1/ai/tasks") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
    await user.click(screen.getByRole("button", { name: "Vorschläge berechnen" }));

    expect(
      await screen.findByText(/84 Buchungen ohne Kategorie/, { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("Software & Online-Dienste")).toBeInTheDocument();
    expect(screen.getByText("Passt zu", { exact: false })).toBeInTheDocument();

    // Vorausgewählt ist nur der sichere Vorschlag (90 %), nicht der unsichere
    // mit 40 %.
    const applyButton = screen.getByRole("button", { name: "1 übernehmen" });
    await user.click(applyButton);

    await waitFor(() => {
      const applyCall = fetchMock.mock.calls.find(([input]) =>
        String(input).includes("/suggestions/apply"),
      );
      expect(applyCall).toBeDefined();
      const body = JSON.parse(String((applyCall![1] as RequestInit).body)) as {
        operation_id: string;
        assign_transactions: boolean;
        accepted: { cluster_key: string; name?: string; is_tax_deductible?: boolean }[];
      };
      expect(body.operation_id).toBe("op-vorschlaege");
      expect(body.assign_transactions).toBe(true);
      expect(body.accepted).toHaveLength(1);
      expect(body.accepted[0]!.cluster_key).toBe("ANTHROPIC");
      expect(body.accepted[0]!.name).toBe("Software & Online-Dienste");
      expect(body.accepted[0]!.is_tax_deductible).toBe(true);
    });

    expect(await screen.findByText(/7 Buchungen kategorisiert/)).toBeInTheDocument();
  });

  it("zeigt nicht beurteilte Gruppen und die Befunde eines Teilergebnisses", async () => {
    const user = userEvent.setup();
    renderCategoriesPage("owner", {
      ...categorySuggestionTask,
      result: {
        ...suggestionPreview,
        unassessed_clusters: 3,
        warnings: [
          {
            code: "model_output_unusable",
            message: "1 von 5 Clustern ohne verwertbare Antwort.",
            level: "fehler",
          },
          {
            code: "model_timeout",
            message:
              "Lauf beim 4. von 5 Clustern beendet: Der Modellaufruf hat das Zeitbudget überschritten.",
            level: "fehler",
          },
        ],
      },
    });

    await screen.findByRole("heading", { name: "Mobilität" });
    await user.click(screen.getByRole("button", { name: "Kategorien vorschlagen (KI)" }));
    await user.click(screen.getByRole("button", { name: "Vorschläge berechnen" }));

    expect(await screen.findByText(/84 Buchungen ohne Kategorie/)).toHaveTextContent(
      "3 Gruppen ohne KI-Urteil",
    );
    expect(screen.getByText("1 von 5 Clustern ohne verwertbare Antwort.")).toBeInTheDocument();
    expect(screen.getByText(/Lauf beim 4\. von 5 Clustern beendet/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Zeitbudget unter Einstellungen › KI anpassen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
  });

  it("nennt beim Fehlschlag der Vorschläge den Grund samt Abhilfe", async () => {
    const user = userEvent.setup();
    renderCategoriesPage("owner", {
      ...categorySuggestionTask,
      status: "failed",
      result: null,
      status_message: "Die KI hat keine verwertbaren Kategorienvorschläge geliefert.",
      error_message:
        "Das eingestellte Modell ist auf dem Modellserver nicht vorhanden. (Kennung: model_missing)",
    });

    await screen.findByRole("heading", { name: "Mobilität" });
    await user.click(screen.getByRole("button", { name: "Kategorien vorschlagen (KI)" }));
    await user.click(screen.getByRole("button", { name: "Vorschläge berechnen" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Das eingestellte Modell ist auf dem Modellserver");
    expect(alert).toHaveTextContent("Modell in LM Studio laden.");
    expect(
      within(alert).getByRole("link", { name: "Modell unter Einstellungen › KI wählen" }),
    ).toBeInTheDocument();
  });

  it("zeigt Betrachtern die Struktur ohne funktionslose Bearbeitungsschaltflächen", async () => {
    renderCategoriesPage("viewer");

    expect(await screen.findByRole("heading", { name: "Mobilität" })).toBeInTheDocument();
    expect(screen.getByText("ÖPNV & Bahn")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Deine Rolle in diesem Haushalt darf Kategorien ansehen, aber nicht ändern.",
      ),
    ).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Kategorie hinzufügen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archivieren" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Umbenennen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zusammenführen" })).not.toBeInTheDocument();
  });

  it("bietet Zusammenführen nur der Besitzerin bzw. dem Besitzer an", async () => {
    renderCategoriesPage("editor");

    expect(await screen.findByRole("heading", { name: "Mobilität" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bearbeiten" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zusammenführen" })).not.toBeInTheDocument();
  });

  it("hält für Tastaturbedienung immer Nach-oben-/Nach-unten-Aktionen bereit", async () => {
    renderCategoriesPage();

    await screen.findByRole("heading", { name: "Mobilität" });
    expect(screen.getByRole("button", { name: "ÖPNV & Bahn nach oben" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ÖPNV & Bahn nach unten" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Gruppe Mobilität nach oben" })).toBeInTheDocument();
  });
});
