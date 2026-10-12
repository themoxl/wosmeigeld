import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { SettingsPage } from "../pages/SettingsPage";
import type { AIHealthResponse, AIKeyStatus, AIModelCatalog, AISettings } from "../lib/types";

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

const membersResponse = [
  {
    user_id: "user-1",
    role: "owner",
    status: "active",
    invited_at: "2026-01-01T00:00:00Z",
    joined_at: "2026-01-01T00:00:00Z",
    display_name: "Max Mustermann",
  },
];

const invitesResponse = [
  {
    id: "invite-1",
    household_id: "household-1",
    code: "ABCD1234",
    role: "editor",
    created_by: "user-1",
    created_at: "2026-08-01T00:00:00Z",
    expires_at: "2026-08-08T00:00:00Z",
    revoked_at: null,
    used_by: null,
    used_at: null,
  },
];

/**
 * KI-Einstellungen des Haushalts (Migration 0011). Frueher lagen diese Werte
 * nur im localStorage - der Server sah sie nie.
 */
const aiSettingsResponse: AISettings = {
  is_enabled: true,
  auto_categorize_min_confidence: 0.85,
  // Anbieterwahl (app/llm/providers.py): lokal oder Anthropic (Claude).
  provider: "local",
  providers: [
    {
      id: "local",
      name: "Lokal (LM Studio, Ollama, eigener Server)",
      url: null,
      key_required: false,
      leaves_house: false,
      model_required: false,
      hint: "Auf diesem oder einem anderen Rechner im eigenen Netz.",
    },
    {
      id: "anthropic",
      name: "Anthropic (Claude)",
      url: "https://api.anthropic.com",
      key_required: true,
      leaves_house: true,
      model_required: true,
      hint: "Claude über die Messages-API.",
    },
  ],
  provider_keys: [
    {
      provider: "anthropic",
      target: "https://api.anthropic.com",
      stored: false,
      ending: null,
      since: null,
      problem: null,
    },
  ],
  base_url: "http://localhost:1234/v1",
  base_url_source: "environment",
  model_name: null,
  model_source: "automatic",
  sampling_mode: "model",
  sampling_mode_source: "automatic",
  temperature: 1,
  temperature_source: "automatic",
  top_p: 0.95,
  top_p_source: "automatic",
  top_k: 64,
  top_k_source: "automatic",
  repeat_penalty: 1,
  repeat_penalty_source: "automatic",
  min_p: 0,
  min_p_source: "automatic",
  presence_penalty: 0,
  presence_penalty_source: "automatic",
  request_timeout_seconds: 120,
  request_timeout_seconds_source: "environment",
  // Migration 0012: Kontextfenster und Reasoning werden von hier aus in LM
  // Studio durchgestellt. null heisst "so lassen, wie geladen".
  context_length: null,
  context_length_source: "environment",
  reasoning: null,
  reasoning_source: "environment",
  // Relatives Budget: kein festes Ausgabebudget mehr, Vorgaben aus dem Schema.
  context_cap_tokens: null,
  answer_reserve_share: 0.25,
  result_page_share: 0.1,
  run_time_limit_minutes: 20,
  start_chars_per_token: 3,
  start_estimate_error: 0.15,
  context_line_enabled: true,
  assistant_retention_days: 90,
  legacy_max_output_tokens: null,
  schema_defaults: {
    is_enabled: true,
    auto_categorize_min_confidence: 0.85,
    request_timeout_seconds: 120,
    context_cap_tokens: null,
    answer_reserve_share: 0.25,
    result_page_share: 0.1,
    run_time_limit_minutes: 20,
    start_chars_per_token: 3,
    start_estimate_error: 0.15,
    context_line_enabled: true,
    assistant_retention_days: 90,
  },
};

const aiModelsResponse = {
  base_url: "http://localhost:1234/v1",
  is_connected: true,
  supports_detail_api: true,
  message: "3 chatfaehige Modelle, davon 1 geladen.",
  models: [
    {
      id: "google/gemma-4-12b-qat",
      kind: "vlm",
      state: "loaded",
      publisher: "google",
      arch: "gemma4",
      quantization: "Q4_0",
      max_context_tokens: 262144,
      loaded_context_tokens: 8192,
      capabilities: [],
      is_chat_capable: true,
      // Aus GET /api/v1/models. Nur damit erscheint der Reasoning-Schalter.
      reasoning_options: ["off", "on"],
      reasoning_default: "on",
      sampling_profile: {
        id: "gemma-4",
        label: "Gemma 4 (4B/12B/26B)",
        model_examples: ["Gemma 4 12B", "Gemma 4 26B A4B"],
        model_markers: ["gemma-4", "gemma4"],
        reasoning_mode: "any",
        temperature: 1,
        top_p: 0.95,
        top_k: 64,
        repeat_penalty: null,
        min_p: null,
        presence_penalty: null,
        note: "Google veröffentlicht diese Sampling-Werte für Gemma 4.",
        source_url:
          "https://huggingface.co/google/gemma-4-26B-A4B/blob/main/generation_config.json",
      },
    },
    {
      id: "qwen/qwen3-8b",
      kind: "llm",
      state: "not-loaded",
      publisher: "qwen",
      arch: "qwen3",
      quantization: "Q4_K_M",
      max_context_tokens: 131072,
      loaded_context_tokens: null,
      capabilities: [],
      is_chat_capable: true,
    },
    {
      id: "openai/gpt-oss-20b",
      kind: "llm",
      state: "not-loaded",
      publisher: "openai",
      arch: "gpt-oss",
      quantization: "Q4_K_M",
      max_context_tokens: 131072,
      loaded_context_tokens: null,
      capabilities: [],
      is_chat_capable: true,
      reasoning_options: ["low", "medium", "high"],
      reasoning_default: "medium",
    },
    {
      id: "text-embedding-nomic-embed-text-v1.5",
      kind: "embeddings",
      state: "loaded",
      publisher: "nomic-ai",
      arch: "nomic-bert",
      quantization: "F32",
      max_context_tokens: 2048,
      loaded_context_tokens: 2048,
      capabilities: [],
      is_chat_capable: false,
    },
  ],
  sampling_profiles: [
    {
      id: "gemma-4",
      label: "Gemma 4 (4B/12B/26B)",
      model_examples: ["Gemma 4 12B", "Gemma 4 26B A4B"],
      model_markers: ["gemma-4", "gemma4"],
      reasoning_mode: "any",
      temperature: 1,
      top_p: 0.95,
      top_k: 64,
      repeat_penalty: null,
      min_p: null,
      presence_penalty: null,
      note: "Google veröffentlicht diese Sampling-Werte für Gemma 4.",
      source_url: "https://huggingface.co/google/gemma-4-26B-A4B/blob/main/generation_config.json",
    },
  ],
};

const aiHealthResponse: AIHealthResponse = {
  status: "connected",
  is_connected: true,
  base_url: "http://localhost:1234/v1",
  active_model: "google/gemma-4-12b-qat",
  canary_passed: true,
  supports_json_schema: true,
  models_count: 3,
  message: "Modell „google/gemma-4-12b-qat“ aktiv, Canary bestanden (strukturierte Ausgabe).",
  model_source: "automatic",
  model_state: "loaded",
  chat_models_count: 2,
  context_tokens: 8192,
  window: {
    loaded: 8192,
    cap: null,
    effective: 8192,
    source: "/api/v1/models",
    reason: null,
    parallel: 1,
    max_context: 262144,
  },
  reserve_tokens: 2048,
  measurement: {
    chars_per_token: 3.41,
    estimate_error: 0.042,
    calls: 12,
    is_start_value: false,
    error_is_start_value: false,
    largest_need_tokens: 3100,
  },
  warnings: [],
};

/** Modellliste bei Claude: Fenster, Antwortgrenze und Denkstufen je Modell. */
const claudeModelsResponse: AIModelCatalog = {
  base_url: "https://api.anthropic.com",
  is_connected: true,
  provider: "anthropic",
  supports_detail_api: true,
  message: "2 Modelle bei Anthropic (Claude) verfügbar.",
  sampling_profiles: [],
  models: [
    {
      id: "claude-opus-5",
      display_name: "Claude Opus 5",
      max_context_tokens: 1000000,
      loaded_context_tokens: 1000000,
      output_limit_tokens: 128000,
      is_chat_capable: true,
      // Ohne "off": dieses Modell laesst Denken nicht abschalten.
      reasoning_options: ["on", "low", "medium", "high", "xhigh", "max"],
      reasoning_default: null,
    },
    {
      id: "claude-haiku-5",
      display_name: null,
      max_context_tokens: 200000,
      loaded_context_tokens: 200000,
      output_limit_tokens: 64000,
      is_chat_capable: true,
      reasoning_options: ["off", "on"],
      reasoning_default: null,
    },
  ],
};

interface RenderExtras {
  /** Antwort von GET /v1/ai/models; Vorgabe ist der lokale Katalog. */
  models?: unknown;
  /** Antwort von PUT/DELETE /v1/ai/provider-keys/…; Vorgabe ist Erfolg. */
  keyResponse?: { ok: boolean; status: number; body: unknown };
}

function renderSettingsPage(
  healthOverride?: Partial<AIHealthResponse>,
  settingsOverride?: Partial<AISettings>,
  initialPath = "/einstellungen",
  extras: RenderExtras = {},
) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/v1/ai/provider-keys/")) {
      const stored = init?.method === "PUT";
      const status: AIKeyStatus = {
        provider: "anthropic",
        target: "https://api.anthropic.com",
        stored,
        ending: stored ? "…wxyz" : null,
        since: stored ? "2026-10-11T08:00:00Z" : null,
        problem: null,
      };
      const response = extras.keyResponse ?? { ok: true, status: 200, body: status };
      return Promise.resolve({
        ok: response.ok,
        status: response.status,
        json: () => Promise.resolve(response.body),
      });
    }
    if (url.includes("/v1/ai/settings")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...aiSettingsResponse, ...settingsOverride }),
      });
    }
    // Vor der Modellliste pruefen: "/v1/ai/models" ist ein Praefix von
    // "/v1/ai/models/load" und wuerde den Ladeaufruf sonst verschlucken.
    if (url.includes("/v1/ai/models/load")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            model_name: "google/gemma-4-12b-qat",
            changed: true,
            instance_id: "google/gemma-4-12b-qat:2",
            context_length: 32768,
            load_time_seconds: 38.7,
            message: "„google/gemma-4-12b-qat“ neu geladen mit 32768 Tokens Kontext (38.7 s).",
          }),
      });
    }
    if (url.includes("/v1/ai/models")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(extras.models ?? aiModelsResponse),
      });
    }
    if (url.includes("/v1/ai/health")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...aiHealthResponse, ...healthOverride }),
      });
    }
    {
      if (url.includes("/v1/me")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(meResponse),
        });
      }
      if (url.includes("/v1/households/members")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(membersResponse),
        });
      }
      if (url.includes("/v1/households/invites")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(invitesResponse),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
  });
  vi.stubGlobal("fetch", fetchMock);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AuthProvider>
          <HouseholdProvider>
            <SettingsPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

// Register lassen inaktive Bereiche bewusst ungerendert (Tabs.tsx) - jeder
// Test muss deshalb erst ins passende Register wechseln, bevor er dort etwas
// erwartet.
async function goToTab(name: string) {
  await userEvent.click(screen.getByRole("tab", { name }));
}

describe("SettingsPage – Reiter in der Adresse", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("oeffnet mit ?tab=ki direkt den KI-Reiter (Links aus KI-Hinweisen)", () => {
    renderSettingsPage(undefined, undefined, "/einstellungen?tab=ki");
    expect(screen.getByRole("tab", { name: "KI" })).toHaveAttribute("aria-selected", "true");
  });

  it("faellt bei unbekanntem Reiter auf Haushalt zurueck", () => {
    renderSettingsPage(undefined, undefined, "/einstellungen?tab=gibtsnicht");
    expect(screen.getByRole("tab", { name: "Haushalt" })).toHaveAttribute("aria-selected", "true");
  });
});

/** Koerper aller PUT /v1/ai/settings in Aufrufreihenfolge. */
function settingsPutBodies(fetchMock: ReturnType<typeof renderSettingsPage>) {
  return fetchMock.mock.calls
    .filter(
      ([input, init]) =>
        String(input).includes("/v1/ai/settings") &&
        (init as RequestInit | undefined)?.method === "PUT",
    )
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)) as Record<string, unknown>);
}

describe("SettingsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rendert Mitglieder und offene Einladungen ohne Absturz", async () => {
    renderSettingsPage();
    await goToTab("Mitglieder");

    await waitFor(() => {
      expect(screen.getByText("Max Mustermann")).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText("ABCD1234")).toBeInTheDocument();
    });
  });

  it("bietet die in LM Studio installierten Chat-Modelle zur Auswahl an", async () => {
    renderSettingsPage();
    await goToTab("KI");

    // Erst warten, bis die Modellliste da ist - das Auswahlfeld selbst
    // existiert schon vorher (mit dem Automatik-Eintrag).
    await screen.findByRole("option", { name: /gemma-4-12b-qat/ });
    const select = screen.getByLabelText("Modell");
    const options = within(select)
      .getAllByRole("option")
      .map((option) => option.textContent);

    // Ohne Wahl entscheidet die Discovery - das ist ein eigener Eintrag, kein
    // leerer Wert.
    expect(options[0]).toBe("Automatisch (erstes geladenes Modell)");
    expect(options.some((label) => label?.includes("google/gemma-4-12b-qat"))).toBe(true);
    // Ladezustand und Kontextfenster stehen dran - beides entscheidet, ob ein
    // Modell brauchbar ist.
    expect(options.some((label) => label?.includes("geladen"))).toBe(true);
    expect(options.some((label) => label?.includes("Tokens Kontext"))).toBe(true);

    // Einbettungsmodelle sind KEINE Option: sie beherrschen keine
    // Chat-Antworten. Genau dieses Modell konnte die alte models[0]-Logik
    // erwischen.
    expect(options.some((label) => label?.includes("text-embedding"))).toBe(false);
    expect(
      screen.getByText("Einbettungsmodelle stehen nicht zur Wahl", { exact: false }),
    ).toBeInTheDocument();
  });

  it("zeigt die Live-Rechnung aus Fenster, Reserve und Messung", async () => {
    renderSettingsPage();
    await goToTab("KI");

    // Kein festes Ausgabebudget mehr - weder Feld noch Erklaertext.
    expect(screen.queryByLabelText(/Ausgabebudget/)).not.toBeInTheDocument();
    expect(await screen.findByTestId("ai-budget-line")).toHaveTextContent(
      "Fenster 8.192 (LM Studio /api/v1/models) · Reserve 2.048 · gemessen 3,41 Zeichen/Token, Abstand 4,2 % aus 12 Aufrufen",
    );
  });

  it("nennt Startwerte, solange für das Modell nichts gemessen ist", async () => {
    renderSettingsPage({
      measurement: {
        chars_per_token: 3,
        estimate_error: 0.15,
        calls: 0,
        is_start_value: true,
        error_is_start_value: true,
      },
    });
    await goToTab("KI");

    expect(await screen.findByTestId("ai-budget-line")).toHaveTextContent(
      "Startwert 3,0 Zeichen/Token, Abstand 15 % (noch keine Messung)",
    );
  });

  it("nennt „Kontext höchstens“ als wirksam, wenn der Deckel greift", async () => {
    renderSettingsPage({
      window: {
        loaded: 32768,
        cap: 16384,
        effective: 16384,
        source: "/api/v1/models",
      },
      reserve_tokens: 4096,
    });
    await goToTab("KI");

    expect(await screen.findByTestId("ai-budget-line")).toHaveTextContent(
      "Fenster 16.384 (Kontext höchstens; geladen 32.768) · Reserve 4.096",
    );
  });

  it("sagt ohne bekanntes Fenster, was zu tun ist", async () => {
    renderSettingsPage({
      context_tokens: null,
      window: {
        loaded: null,
        cap: null,
        effective: null,
        source: null,
        reason: "Das geladene Kontextfenster ist unbekannt: kein geladenes Modell",
      },
      reserve_tokens: null,
    });
    await goToTab("KI");

    expect(
      await screen.findByText(/Modell in LM Studio laden oder „Kontext höchstens“ setzen/),
    ).toBeInTheDocument();
    expect(screen.getByText(/kein geladenes Modell/)).toBeInTheDocument();
    expect(screen.queryByTestId("ai-budget-line")).not.toBeInTheDocument();
  });

  it("zeigt die benannten Befunde des Servers mit ihrer Stufe", async () => {
    // Frueher eine Liste von Texten; jetzt Objekte. Als Text gerendert stuerzte
    // React damit ab.
    renderSettingsPage({
      warnings: [
        {
          code: "model_context_unknown",
          message: "Das Kontextfenster des Modells ist unbekannt.",
          level: "fehler",
        },
        {
          code: "model_not_loaded",
          message: "Das Modell ist auf dem Server nicht geladen.",
          level: "warnung",
        },
        {
          code: "model_cap_above_window",
          message: "„Kontext höchstens“ liegt über dem geladenen Fenster.",
          level: "info",
        },
      ],
    });
    await goToTab("KI");

    const list = await screen.findByRole("list", { name: "Hinweise zur KI" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Fehler");
    expect(items[0]).toHaveTextContent("Das Kontextfenster des Modells ist unbekannt.");
    expect(items[1]).toHaveTextContent("Warnung");
    expect(items[2]).toHaveTextContent("Info");
  });

  it("zeigt die Vorgabe aus dem Schema und setzt per clear_fields zurück", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage(undefined, { answer_reserve_share: 0.4 });
    await goToTab("KI");

    const reserve = await screen.findByLabelText("Antwortreserve (% des Fensters)");
    expect(reserve).toHaveValue(40);
    expect(screen.getByText(/Vorgabe: 25 %/)).toBeInTheDocument();
    expect(screen.getByText(/Vorgabe: 3,0\./)).toBeInTheDocument();
    expect(screen.getByText(/Vorgabe: leer – das geladene Fenster gilt/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Antwortreserve zurücksetzen" }));
    expect(reserve).toHaveValue(25);
    expect(screen.getByRole("button", { name: "Antwortreserve zurücksetzen" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => expect(settingsPutBodies(fetchMock)).toHaveLength(1));
    const body = settingsPutBodies(fetchMock)[0]!;
    // Zurueckgesetzt wird ueber die Spaltenvorgabe, nicht ueber eine Zahl
    // aus der Oberflaeche.
    expect(body.clear_fields).toContain("answer_reserve_share");
    expect(body).not.toHaveProperty("answer_reserve_share");
    expect(body).not.toHaveProperty("max_output_tokens");
  });

  it("speichert Anteile als Anteil und leeres „Kontext höchstens“ als Zurücksetzen", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage(undefined, { context_cap_tokens: 16384 });
    await goToTab("KI");

    const reserve = await screen.findByLabelText("Antwortreserve (% des Fensters)");
    await user.clear(reserve);
    await user.type(reserve, "30");
    await user.clear(screen.getByLabelText("Startwert Schätzabstand (%)"));
    await user.type(screen.getByLabelText("Startwert Schätzabstand (%)"), "12.5");
    await user.clear(screen.getByLabelText("Kontext höchstens (Tokens)"));
    await user.click(screen.getByLabelText("Kontextzeile an das Modell"));
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => expect(settingsPutBodies(fetchMock)).toHaveLength(1));
    const body = settingsPutBodies(fetchMock)[0]!;
    expect(body).toMatchObject({
      answer_reserve_share: 0.3,
      result_page_share: 0.1,
      run_time_limit_minutes: 20,
      start_chars_per_token: 3,
      start_estimate_error: 0.125,
      context_line_enabled: false,
    });
    expect(body).not.toHaveProperty("context_cap_tokens");
    expect(body.clear_fields).toContain("context_cap_tokens");
    expect(body.clear_fields).not.toContain("answer_reserve_share");
  });

  it("weist Anteile außerhalb des Schemabereichs vor dem Senden zurück", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage();
    await goToTab("KI");

    const reserve = await screen.findByLabelText("Antwortreserve (% des Fensters)");
    await user.clear(reserve);
    await user.type(reserve, "95");
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    // Die Grenzen stehen als min/max am Feld - der Browser verweigert das
    // Absenden selbst; die eigene Pruefung im Handler ist die zweite Linie.
    expect(reserve).toBeInvalid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(settingsPutBodies(fetchMock)).toHaveLength(0);
  });

  it("schaltet die KI für den Haushalt aus", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage();
    await goToTab("KI");

    const toggle = await screen.findByLabelText("KI in diesem Haushalt verwenden");
    expect(toggle).toBeChecked();
    await user.click(toggle);
    expect(screen.getByText(/Ausgeschaltet ruft keine KI-Funktion ein Modell auf/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => expect(settingsPutBodies(fetchMock)).toHaveLength(1));
    expect(settingsPutBodies(fetchMock)[0]).toMatchObject({ is_enabled: false });
  });

  it("weist einmal auf das frühere feste Ausgabebudget hin", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage(undefined, { legacy_max_output_tokens: 4000 });
    await goToTab("KI");

    expect(
      await screen.findByText(/Das frühere feste Ausgabebudget \(4\.000 Tokens\) gilt nicht mehr/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Verstanden" }));

    await waitFor(() => expect(settingsPutBodies(fetchMock)).toHaveLength(1));
    // Nur den Hinweis quittieren - keine anderen Felder mitsenden.
    expect(settingsPutBodies(fetchMock)[0]).toEqual({
      clear_fields: ["legacy_max_output_tokens"],
    });
  });

  it("speichert die Modellwahl serverseitig statt im Browser", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage();
    await goToTab("KI");

    await screen.findByRole("option", { name: /qwen3-8b/ });
    await user.selectOptions(screen.getByLabelText("Modell"), "qwen/qwen3-8b");
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/v1/ai/settings") &&
          (init as RequestInit | undefined)?.method === "PUT",
      );
      expect(call).toBeDefined();
      const body = JSON.parse(String((call![1] as RequestInit).body)) as {
        model_name: string;
      };
      expect(body.model_name).toBe("qwen/qwen3-8b");
      // Das feste Ausgabebudget ist entfallen.
      expect(body).not.toHaveProperty("max_output_tokens");
    });
  });

  it("speichert Kontextfenster und Reasoning gemeinsam mit dem Rest", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage();
    await goToTab("KI");

    await screen.findByRole("option", { name: /gemma-4-12b-qat/ });
    await user.selectOptions(screen.getByLabelText("Modell"), "google/gemma-4-12b-qat");

    const contextInput = await screen.findByLabelText("Kontextfenster (Tokens)");
    await user.clear(contextInput);
    await user.type(contextInput, "32768");
    // Der Schalter erscheint erst, wenn LM Studio fuer dieses Modell Optionen
    // meldet - deshalb nach der Modellwahl.
    await user.selectOptions(screen.getByLabelText("Lautes Denken (Reasoning)"), "off");
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/v1/ai/settings") &&
          (init as RequestInit | undefined)?.method === "PUT",
      );
      expect(call).toBeDefined();
      const body = JSON.parse(String((call![1] as RequestInit).body)) as {
        context_length: number;
        reasoning: string;
        clear_fields: string[];
      };
      expect(body.context_length).toBe(32768);
      expect(body.reasoning).toBe("off");
      // Gesetzte Felder duerfen NICHT zugleich im Zuruecksetzen stehen.
      expect(body.clear_fields).not.toContain("context_length");
      expect(body.clear_fields).not.toContain("reasoning");
    });
  });

  it("zeigt das Gemma-Profil und sendet manuelle Sampling-Werte exakt", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage();
    await goToTab("KI");

    expect(await screen.findByText(/Wirksam für.*gemma-4-12b-qat/)).toHaveTextContent(
      "Temperatur 1 · Top P 0.95 · Top K 64",
    );

    await user.selectOptions(screen.getByLabelText("Sampling"), "manual");
    const temperature = screen.getByLabelText("Sampling Temperatur");
    await user.clear(temperature);
    await user.type(temperature, "0.8");
    const repeatPenalty = screen.getByLabelText("Sampling Wiederholungsstrafe");
    await user.clear(repeatPenalty);
    await user.type(repeatPenalty, "1.05");
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/v1/ai/settings") &&
          (init as RequestInit | undefined)?.method === "PUT",
      );
      const body = JSON.parse(String((call![1] as RequestInit).body)) as {
        sampling_mode: string;
        temperature: number;
        top_p: number;
        top_k: number;
        repeat_penalty: number;
        min_p: number;
        presence_penalty: number;
      };
      expect(body).toMatchObject({
        sampling_mode: "manual",
        temperature: 0.8,
        top_p: 0.95,
        top_k: 64,
        repeat_penalty: 1.05,
        min_p: 0,
        presence_penalty: 0,
      });
    });
  });

  it("lässt Reasoning auch bei automatischer Modellwahl steuern", async () => {
    renderSettingsPage();
    await goToTab("KI");

    const reasoning = await screen.findByLabelText("Lautes Denken (Reasoning)");
    expect(reasoning).toBeInTheDocument();
    expect(
      within(reasoning).getByRole("option", { name: /Modellstandard.*An/ }),
    ).toBeInTheDocument();
    expect(within(reasoning).getByRole("option", { name: "An" })).toBeInTheDocument();
    expect(within(reasoning).getByRole("option", { name: "Aus" })).toBeInTheDocument();
    expect(within(reasoning).queryByRole("option", { name: "Hoch" })).not.toBeInTheDocument();
  });

  it("zeigt bei einem Effort-Modell dessen gemeldete Reasoning-Stufen", async () => {
    const user = userEvent.setup();
    renderSettingsPage();
    await goToTab("KI");

    await screen.findByRole("option", { name: /gpt-oss-20b/ });
    await user.selectOptions(screen.getByLabelText("Modell"), "openai/gpt-oss-20b");

    const reasoning = screen.getByLabelText("Lautes Denken (Reasoning)");
    expect(within(reasoning).getByRole("option", { name: /Modellstandard.*Mittel/ })).toBeVisible();
    expect(within(reasoning).getByRole("option", { name: "Niedrig" })).toBeVisible();
    expect(within(reasoning).getByRole("option", { name: "Mittel" })).toBeVisible();
    expect(within(reasoning).getByRole("option", { name: "Hoch" })).toBeVisible();
    expect(within(reasoning).queryByRole("option", { name: "An" })).not.toBeInTheDocument();
  });

  it("blendet den Reasoning-Schalter aus, wenn das Modell keinen kennt", async () => {
    const user = userEvent.setup();
    renderSettingsPage();
    await goToTab("KI");

    // qwen3-8b traegt in der Attrappe keine reasoning_options - einen Regler
    // anzubieten, den der Server mit HTTP 400 quittiert, waere schlimmer als
    // keiner.
    await screen.findByRole("option", { name: /qwen3-8b/ });
    await user.selectOptions(screen.getByLabelText("Modell"), "qwen/qwen3-8b");

    expect(screen.queryByLabelText("Lautes Denken (Reasoning)")).not.toBeInTheDocument();
  });

  it("lädt das Modell auf Knopfdruck in LM Studio und meldet das Ergebnis", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage();
    await goToTab("KI");

    await screen.findByRole("option", { name: /gemma-4-12b-qat/ });
    await user.selectOptions(screen.getByLabelText("Modell"), "google/gemma-4-12b-qat");
    await user.click(screen.getByRole("button", { name: "In LM Studio laden" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/v1/ai/models/load") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(call).toBeDefined();
    });

    // Die Rueckmeldung nennt das tatsaechlich geladene Fenster - nicht das
    // gewuenschte.
    expect(await screen.findByText(/neu geladen mit 32768 Tokens/)).toBeInTheDocument();
  });

  it("lässt bei automatischer Modellwahl nichts laden", async () => {
    renderSettingsPage();
    await goToTab("KI");

    // Ohne ausdrueckliche Wahl gibt es kein Modell, das geladen werden
    // koennte - der Knopf bleibt gesperrt, statt in einen 400er zu laufen.
    expect(await screen.findByRole("button", { name: "In LM Studio laden" })).toBeDisabled();
  });
});

/** Alle Aufrufe an /v1/ai/provider-keys/… in Aufrufreihenfolge. */
function keyCalls(fetchMock: ReturnType<typeof renderSettingsPage>) {
  return fetchMock.mock.calls.filter(([input]) => String(input).includes("/v1/ai/provider-keys/"));
}

describe("SettingsPage – KI-Anbieter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt bei Claude den Datenhinweis und den Schlüsselabschnitt, ohne LM-Studio-Felder", async () => {
    const user = userEvent.setup();
    renderSettingsPage();
    await goToTab("KI");

    const provider = await screen.findByLabelText("Anbieter");
    expect(provider).toHaveValue("local");
    // Lokal bleibt alles wie bisher - kein Hinweis, kein Schluessel.
    expect(screen.getByLabelText("LM-Studio URL")).toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Neuer API-Schlüssel")).not.toBeInTheDocument();

    await user.selectOptions(provider, "anthropic");

    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Mit Claude verlassen die Daten den Haushalt");
    expect(note).toHaveTextContent("auch Gesundheitsdokumente");
    expect(note).toHaveTextContent("Anthropic (Server in den USA)");
    expect(note).toHaveTextContent("Nur der Owner kann den Anbieter wechseln.");
    expect(note).toHaveTextContent("Claude über die Messages-API.");

    expect(screen.getByRole("heading", { name: "API-Schlüssel" })).toBeInTheDocument();
    const keyInput = screen.getByLabelText("Neuer API-Schlüssel");
    expect(keyInput).toHaveAttribute("type", "password");
    expect(keyInput).toHaveAttribute("autocomplete", "off");
    expect(screen.getByTestId("ai-api-key-status")).toHaveTextContent("Kein Schlüssel hinterlegt");
    expect(screen.getByRole("button", { name: "Entfernen" })).toBeDisabled();

    // Was es bei Claude nicht gibt, steht auch nicht da.
    expect(screen.queryByLabelText("LM-Studio URL")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Kontextfenster (Tokens)")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "In LM Studio laden" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Sampling")).not.toBeInTheDocument();

    // Die Modelle des lokalen Servers passen nicht zu Claude.
    const model = screen.getByLabelText("Modell");
    expect(model).toHaveValue("");
    expect(
      within(model)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Bitte Modell wählen"]);
  });

  it("sendet beim Speichern den Anbieter und lässt ausgeblendete Felder unangetastet", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage(undefined, {
      model_name: "google/gemma-4-12b-qat",
      model_source: "household",
      context_length: 32768,
      reasoning: "on",
    });
    await goToTab("KI");

    await user.selectOptions(await screen.findByLabelText("Anbieter"), "anthropic");
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => expect(settingsPutBodies(fetchMock)).toHaveLength(1));
    const body = settingsPutBodies(fetchMock)[0]!;
    expect(body.provider).toBe("anthropic");
    // Modell und Reasoning des lokalen Servers passen nicht zu Claude.
    expect(body.model_name).toBeNull();
    expect(body.clear_fields).toEqual(expect.arrayContaining(["model_name", "reasoning"]));
    // Server-URL, Ladefenster und Sampling bleiben, wie sie gespeichert sind:
    // weder gesendet noch zurueckgesetzt.
    for (const field of [
      "base_url",
      "context_length",
      "sampling_mode",
      "temperature",
      "top_p",
      "top_k",
      "repeat_penalty",
      "min_p",
      "presence_penalty",
    ]) {
      expect(body, field).not.toHaveProperty(field);
    }
    expect(body.clear_fields).not.toContain("custom_base_url");
    expect(body.clear_fields).not.toContain("context_length");
  });

  it("schickt den unveränderten lokalen Anbieter beim Speichern mit", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage();
    await goToTab("KI");

    await screen.findByLabelText("Anbieter");
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));

    await waitFor(() => expect(settingsPutBodies(fetchMock)).toHaveLength(1));
    expect(settingsPutBodies(fetchMock)[0]).toMatchObject({
      provider: "local",
      base_url: "http://localhost:1234/v1",
      sampling_mode: "model",
    });
  });

  it("hinterlegt den API-Schlüssel per PUT und leert danach die Eingabe", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage(undefined, { provider: "anthropic" });
    await goToTab("KI");

    const keyInput = await screen.findByLabelText("Neuer API-Schlüssel");
    expect(screen.getByRole("button", { name: "Hinterlegen" })).toBeDisabled();
    await user.type(keyInput, "sk-ant-api03-geheim-abcd");
    await user.click(screen.getByRole("button", { name: "Hinterlegen" }));

    await waitFor(() => expect(keyCalls(fetchMock)).toHaveLength(1));
    const [input, init] = keyCalls(fetchMock)[0]!;
    expect(String(input)).toMatch(/\/v1\/ai\/provider-keys\/anthropic$/);
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ api_key: "sk-ant-api03-geheim-abcd" });

    await waitFor(() => expect(keyInput).toHaveValue(""));
    expect(await screen.findByText("Schlüssel hinterlegt.")).toBeInTheDocument();
    // Eigene Aktion: der Schluessel geht nie ueber "Einstellungen speichern".
    expect(settingsPutBodies(fetchMock)).toHaveLength(0);
  });

  it("zeigt den Stand eines hinterlegten Schlüssels – nie den Wert", async () => {
    renderSettingsPage(undefined, {
      provider: "anthropic",
      provider_keys: [
        {
          provider: "anthropic",
          target: "https://api.anthropic.com",
          stored: true,
          ending: "…abcd",
          since: "2026-10-01T09:30:00Z",
          problem: null,
        },
      ],
    });
    await goToTab("KI");

    expect(await screen.findByTestId("ai-api-key-status")).toHaveTextContent(
      "Hinterlegt seit 01.10.2026 (…abcd)",
    );
    expect(screen.getByLabelText("Neuer API-Schlüssel")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Entfernen" })).toBeEnabled();
  });

  it("entfernt den Schlüssel per DELETE", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage(undefined, {
      provider: "anthropic",
      provider_keys: [
        {
          provider: "anthropic",
          target: "https://api.anthropic.com",
          stored: true,
          ending: "…abcd",
          since: "2026-10-01T09:30:00Z",
          problem: null,
        },
      ],
    });
    await goToTab("KI");

    await user.click(await screen.findByRole("button", { name: "Entfernen" }));

    await waitFor(() => expect(keyCalls(fetchMock)).toHaveLength(1));
    const [input, init] = keyCalls(fetchMock)[0]!;
    expect(String(input)).toMatch(/\/v1\/ai\/provider-keys\/anthropic$/);
    expect(init?.method).toBe("DELETE");
    expect(await screen.findByText("Schlüssel entfernt.")).toBeInTheDocument();
  });

  it("erklärt ein abgelehntes Hinterlegen ohne Owner-Recht", async () => {
    const user = userEvent.setup();
    renderSettingsPage(undefined, { provider: "anthropic" }, undefined, {
      keyResponse: { ok: false, status: 403, body: { detail: "household_owner_required" } },
    });
    await goToTab("KI");

    await user.type(await screen.findByLabelText("Neuer API-Schlüssel"), "sk-ant-api03-x");
    await user.click(screen.getByRole("button", { name: "Hinterlegen" }));

    expect(
      await screen.findByText("Nur der Owner kann den API-Schlüssel hinterlegen."),
    ).toBeInTheDocument();
    // Die Eingabe bleibt stehen - nichts ist hinterlegt.
    expect(screen.getByLabelText("Neuer API-Schlüssel")).toHaveValue("sk-ant-api03-x");
  });

  it("bietet bei Claude Modelle mit Fenster und Antwortgrenze an – ohne „Automatisch“", async () => {
    const user = userEvent.setup();
    const fetchMock = renderSettingsPage(undefined, { provider: "anthropic" }, undefined, {
      models: claudeModelsResponse,
    });
    await goToTab("KI");

    await screen.findByRole("option", { name: /Claude Opus 5/ });
    const model = screen.getByLabelText("Modell");
    expect(
      within(model)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "Bitte Modell wählen",
      "Claude Opus 5 · 1.000.000 Token Fenster, Antwort bis 128.000",
      "claude-haiku-5 · 200.000 Token Fenster, Antwort bis 64.000",
    ]);
    expect(screen.getByText(/wählt der Finanzplaner bewusst kein Modell selbst/)).toBeVisible();
    // Ohne Modellwahl gibt es auch keine gemeldeten Denkstufen.
    expect(screen.queryByLabelText("Lautes Denken (Reasoning)")).not.toBeInTheDocument();

    await user.selectOptions(model, "claude-opus-5");
    const reasoning = screen.getByLabelText("Lautes Denken (Reasoning)");
    expect(within(reasoning).getByRole("option", { name: "Maximal" })).toBeInTheDocument();
    expect(within(reasoning).getByRole("option", { name: "Sehr hoch" })).toBeInTheDocument();
    // Dieses Modell laesst Denken nicht abschalten - also kein "Aus".
    expect(within(reasoning).queryByRole("option", { name: "Aus" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Abschalten lässt sich das Denken bei diesem Modell nicht/),
    ).toBeVisible();

    await user.selectOptions(reasoning, "max");
    await user.click(screen.getByRole("button", { name: "Einstellungen speichern" }));
    await waitFor(() => expect(settingsPutBodies(fetchMock)).toHaveLength(1));
    expect(settingsPutBodies(fetchMock)[0]).toMatchObject({
      provider: "anthropic",
      model_name: "claude-opus-5",
      reasoning: "max",
    });
  });
});
