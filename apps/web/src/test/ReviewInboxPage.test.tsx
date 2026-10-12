import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ReviewInboxPage } from "../pages/ReviewInboxPage";

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

/**
 * Der Laufstatus liegt seit Ausbaustufe 0 des KI-Plans vollstaendig auf dem
 * Server: die Seite fragt mit GET /v1/ai/classify-runs?limit=1 den juengsten
 * Lauf ab, statt eine Run-ID in lokalem State zu halten.
 */
function makeRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "run-1",
    status: "pending",
    run_type: "classification",
    current_stage: null,
    since_date: "2026-05-01",
    total_count: 1,
    processed_count: 0,
    success_count: 0,
    error_count: 0,
    error_message: null,
    created_at: "2026-08-01T00:00:00Z",
    completed_at: null,
    heartbeat_at: null,
    cancel_requested: false,
    is_stale: false,
    ...overrides,
  };
}

function makeClassificationTask(overrides: Record<string, unknown> = {}) {
  return {
    id: "task-classification",
    task_type: "transaction_classification",
    status: "queued",
    queue_order: 1,
    request_id: "test-request",
    input: {},
    result: null,
    result_summary: {},
    route_path: "/review?ai_task=task-classification",
    current_stage: "queued",
    progress_current: 0,
    progress_total: 1,
    status_message: "Wartet auf die Verarbeitung",
    model_name: null,
    error_message: null,
    cancel_requested: false,
    attempt_count: 0,
    category_operation_id: null,
    classification_run_id: "run-1",
    created_at: "2026-08-05T10:00:00Z",
    started_at: null,
    completed_at: null,
    heartbeat_at: null,
    updated_at: "2026-08-05T10:00:00Z",
    is_stale: false,
    ...overrides,
  };
}

/**
 * Ein erkannter Vertrag (Erkennung v2).
 *
 * `amount_minor` ist - wie in `contracts` - IMMER positiv, die Richtung steht
 * getrennt in `direction`. Das alte `expected_amount_minor` war negativ; wer
 * das verwechselt, zeigt Ausgaben als Einnahmen an.
 */
function makeContractSuggestion(overrides: Record<string, unknown> = {}) {
  return {
    type: "contract_suggestion",
    source_transaction_id: "txn-musterfunk-6",
    display_name: "Musterfunk Deutschland",
    payee_label: "MUSTERFUNK DEUTSCHLAND GMBH",
    contract_key: "M-4711",
    contract_key_source: "mandate",
    amount_minor: 3995,
    direction: "expense",
    amount_tolerance_minor: 0,
    frequency: "monthly",
    first_date: "2026-01-03",
    last_date: "2026-06-03",
    occurrences_count: 6,
    contract_type: "telecom",
    installments_total: null,
    installments_paid: null,
    amount_steps: [
      {
        amount_minor: 4995,
        first_date: "2026-01-03",
        last_date: "2026-05-03",
        occurrences_count: 5,
      },
      {
        amount_minor: 3995,
        first_date: "2026-06-03",
        last_date: "2026-06-03",
        occurrences_count: 1,
      },
    ],
    suggested_category_id: null,
    detection_confidence: 0.71,
    confidence: "suggested",
    is_uncertain: false,
    score_breakdown: {
      date_stability: 0.9,
      amount_stability: 0.8,
      occurrence_count: 1,
      category_affinity: 1,
      payment_method: 1,
      total: 0.71,
    },
    ...overrides,
  };
}

const mockReviewInbox = {
  total_count: 1,
  unclassified_open_count: 1,
  unclassified_total_count: 1,
  unclassified_offset: 0,
  unclassified_limit: 100,
  unclassified: [
    {
      type: "unclassified",
      transaction_id: "txn-1",
      account_id: "acc-1",
      booked_date: "2026-08-01",
      amount_minor: -1599,
      payee_label: "Netflix",
      note: "Monatsabo",
    },
  ],
  possible_duplicates: [],
  transfer_candidates: [],
  // Dauerhaft leer: der Server liefert Vertragsvorschlaege jetzt in
  // contract_suggestions (siehe rules_service.get_review_inbox).
  recurring_suggestions: [],
  contract_ended_checks: [],
  contract_suggestions: [],
  contract_duplicate_suggestions: [],
};

const CATEGORY_GROUPS = [
  {
    id: "grp-telecom",
    name: "Wohnen & Verträge",
    sort_order: 0,
    categories: [
      {
        id: "cat-telecom",
        group_id: "grp-telecom",
        name: "Internet & Telefon",
        default_type: "expense",
        budget_bucket: "needs",
        is_archived: false,
      },
    ],
  },
];

/** Rendert die Seite; fetch stubbt der jeweilige Test selbst. */
function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <ReviewInboxPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderReviewInboxPage() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/me")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(meResponse),
        });
      }
      if (url.includes("/v1/review/history-matches")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              checked_count: 1,
              matched_count: 1,
              items: [
                {
                  transaction_id: "txn-1",
                  matched: true,
                  category_id: "cat-entertainment",
                  category_label: "Freizeit · Abos",
                  evidence_count: 4,
                  ambiguous: false,
                  source: "rule",
                  rule_id: "rule-netflix",
                  rule_name: "Regel für Netflix",
                },
              ],
            }),
        });
      }
      if (url.includes("/v1/review")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockReviewInbox),
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
            <ReviewInboxPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReviewInboxPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    // Der CategoryPicker merkt sich zuletzt gewaehlte Kategorien in
    // localStorage. Ohne dieses Zuruecksetzen traegt ein Test seine Auswahl in
    // den naechsten, wo dieselbe Kategorie dann doppelt in der Liste steht -
    // einmal unter "Zuletzt verwendet", einmal in ihrer Gruppe.
    window.localStorage.clear();
  });

  it("rendert die Review-Inbox mit unklassifizierten Umsätzen", async () => {
    const user = userEvent.setup();
    renderReviewInboxPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Review-Inbox" })).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText("Netflix")).toBeInTheDocument();
    });

    expect(screen.getByText("-15,99 €")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Bestand & Regeln prüfen (ohne KI)",
      }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Bestand & Regeln prüfen (ohne KI)" }));
    expect(await screen.findByText("1 von 1 ohne KI gefunden")).toBeInTheDocument();
    expect(screen.getByText("⚙ Regel „Regel für Netflix“ · Freizeit · Abos")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "⚙ Regelvorschlag übernehmen" })).toBeInTheDocument();

    expect(
      screen.getByRole("button", {
        name: "✨ KI-Kategorisierung für alle offenen Buchungen starten",
      }),
    ).toBeInTheDocument();
  });

  it("zeigt die Gesamtzahl und filtert offene Buchungen serverseitig", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/review")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...mockReviewInbox,
              total_count: 488,
              unclassified_open_count: 488,
              unclassified_total_count: url.includes("unclassified_search") ? 1 : 488,
              unclassified_offset: 0,
              unclassified_limit: 100,
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
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "Unklassifizierte Umsätze (488)" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nächste 100" })).toBeInTheDocument();
    await user.type(
      screen.getByRole("searchbox", { name: "Empfänger oder Verwendungszweck filtern" }),
      "Github",
    );

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("unclassified_search=Github"),
        ),
      ).toBe(true);
    });
  });

  it("behält beim langsamen Tippen über jeden Server-Request Fokus und DOM-Instanz", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/me")) {
        return { ok: true, status: 200, json: () => Promise.resolve(meResponse) };
      }
      if (url.includes("/v1/review")) {
        // Ein kurzer echter Request-Zustand stellt sicher, dass der Test sowohl
        // den Start als auch die Ankunft jeder gefilterten Antwort durchläuft.
        await new Promise((resolve) => window.setTimeout(resolve, 30));
        return {
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...mockReviewInbox,
              total_count: 488,
              unclassified_open_count: 488,
              unclassified_total_count: url.includes("unclassified_search") ? 1 : 488,
              unclassified_offset: 0,
              unclassified_limit: 100,
            }),
        };
      }
      return { ok: true, status: 200, json: () => Promise.resolve([]) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <HouseholdProvider>
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const searchInput = await screen.findByRole("searchbox", {
      name: "Empfänger oder Verwendungszweck filtern",
    });
    await user.click(searchInput);

    let query = "";
    for (const character of "git") {
      query += character;
      await user.type(searchInput, character);
      expect(searchInput).toHaveFocus();

      await waitFor(
        () => {
          expect(
            fetchMock.mock.calls.some(([input]) =>
              String(input).includes(`unclassified_search=${query}`),
            ),
          ).toBe(true);
        },
        { timeout: 1_000 },
      );
      await waitFor(() => {
        expect(
          screen.getByRole("searchbox", {
            name: "Empfänger oder Verwendungszweck filtern",
          }),
        ).toBe(searchInput);
        expect(searchInput).toHaveFocus();
      });

      // Bewusst länger als das 250-ms-Debounce warten: Der nächste Buchstabe
      // wird erst nach abgeschlossener Serverrunde eingegeben.
      await new Promise((resolve) => window.setTimeout(resolve, 280));
    }

    expect(searchInput).toHaveValue("git");
  });

  it("zeigt KI-Badge an wenn Entscheidungsprotokoll vorhanden ist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (url.includes("/v1/review")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockReviewInbox),
          });
        }
        if (url.includes("/v1/ai/decisions")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve([
                {
                  id: "dec-1",
                  transaction_id: "txn-1",
                  payee_name: "Netflix",
                  category_id: "cat-entertainment",
                  confidence: 0.87,
                  reason_code: "merchant_and_context_match",
                  source: "lm_studio",
                  model_name: "qwen2.5-coder-7b",
                  created_at: "2026-08-01T00:00:00Z",
                },
              ]),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }),
    );

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <HouseholdProvider>
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("✨ KI 87% (merchant_and_context_match)")).toBeInTheDocument();
    });
  });

  // Der Vorfilter für bekannte Empfänger (Klassifikationslauf im Worker)
  // schreibt Vorschläge mit source='history' und ohne Modellnamen - es lief
  // kein Modell. Ein "✨ KI"-Chip würde dem Nutzer an dieser Stelle eine
  // Modellaussage vorspielen, die es nie gab.
  it("weist einen Vorschlag aus dem eigenen Bestand nicht als KI aus", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (url.includes("/v1/review")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockReviewInbox),
          });
        }
        if (url.includes("/v1/ai/decisions")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve([
                {
                  id: "dec-2",
                  transaction_id: "txn-1",
                  payee_name: "Netflix",
                  category_id: "cat-entertainment",
                  confidence: 0.95,
                  reason_code: "known_payee",
                  source: "history",
                  model_name: null,
                  created_at: "2026-08-01T00:00:00Z",
                },
              ]),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }),
    );

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <HouseholdProvider>
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("↩ Bekannt 95% (known_payee)")).toBeInTheDocument();
    });
    // Gegenprobe: kein Konfidenz-Chip behauptet hier eine KI-Klassifikation.
    // (Der Knopf "KI fragen" darf natürlich stehen bleiben - deshalb wird auf
    // den Chiptext geprüft, nicht auf das Wort.)
    expect(screen.queryByText(/✨ KI \d+%/)).not.toBeInTheDocument();
  });

  it("begründet jeden Vertragsvorschlag und stellt unsichere ans Ende", async () => {
    const uncertain = makeContractSuggestion({
      source_transaction_id: "txn-helmsauer-2",
      display_name: "Mustermakler",
      payee_label: "MUSTERMAKLER GRUPPE",
      contract_key: null,
      contract_key_source: null,
      amount_minor: 3200,
      contract_type: "insurance",
      occurrences_count: 2,
      amount_steps: [
        {
          amount_minor: 3200,
          first_date: "2026-02-10",
          last_date: "2026-05-12",
          occurrences_count: 2,
        },
      ],
      detection_confidence: 0.47,
      confidence: "uncertain",
      is_uncertain: true,
      score_breakdown: {
        date_stability: 0.3,
        amount_stability: 1,
        occurrence_count: 0.33,
        category_affinity: 0,
        payment_method: 1,
        total: 0.47,
      },
    });

    // Der unsichere Vorschlag kommt hier bewusst ZUERST vom Server: die Seite
    // muss ihn selbst ans Ende sortieren, sonst wird er zwischen
    // bestätigungsreifen Verträgen mit durchgewinkt.
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (url.includes("/v1/review")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                ...mockReviewInbox,
                total_count: 2,
                unclassified_open_count: 0,
                unclassified_total_count: 0,
                unclassified: [],
                contract_suggestions: [uncertain, makeContractSuggestion()],
              }),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }),
    );

    const { container } = renderPage();

    expect(
      await screen.findByRole("heading", { name: "Erkannte Verträge (2)" }),
    ).toBeInTheDocument();

    // Der Score wird mit allen fünf Bestandteilen erklärt - je Vorschlag einmal.
    for (const label of [
      "Datumsstabilität",
      "Betragsstabilität",
      "Vorkommen",
      "Kategorie",
      "Zahlungsart",
    ]) {
      expect(screen.getAllByText(label)).toHaveLength(2);
    }
    expect(screen.getAllByText("zählt 30 %")).toHaveLength(2);

    // Die Preisstufen stehen im Vorschlag: EIN Vertrag, nicht zwei.
    const stepsText = container.querySelector(".contract-suggestion__steps")?.textContent ?? "";
    expect(stepsText).toMatch(/-49,95\s€ bis Mai 2026, seit Juni 2026 -39,95\s€/);
    expect(stepsText).toMatch(/ein\s?Vertrag mit Betragsverlauf/);

    // Unsicher: gekennzeichnet, erklärt und an letzter Stelle.
    expect(screen.getByText("Unsicher · 47 % Erkennungsgüte")).toBeInTheDocument();
    expect(screen.getByText("71 % Erkennungsgüte")).toBeInTheDocument();
    expect(screen.getByText(/Ab hier unsicher/)).toBeInTheDocument();

    const cards = container.querySelectorAll(".contract-suggestions > .contract-suggestion");
    expect(cards).toHaveLength(2);
    expect(cards[0]?.textContent).toContain("Musterfunk Deutschland");
    expect(cards[1]?.textContent).toContain("Mustermakler");
    expect(cards[1]?.className).toContain("contract-suggestion--uncertain");
  });

  it("öffnet die Erkennungsdetails mit den Buchungen je Preisstufe", async () => {
    const user = userEvent.setup();
    const suggestion = makeContractSuggestion();
    const detail = {
      suggestion,
      transactions: [
        {
          transaction_id: "txn-musterfunk-6",
          account_id: "acc-1",
          account_name: "Hauptkonto",
          booked_date: "2026-06-03",
          amount_minor: -3995,
          payee_label: "MUSTERFUNK DEUTSCHLAND GMBH",
          note: "Rechnung Juni",
          amount_step_index: 1,
        },
        {
          transaction_id: "txn-musterfunk-5",
          account_id: "acc-1",
          account_name: "Hauptkonto",
          booked_date: "2026-05-03",
          amount_minor: -4995,
          payee_label: "MUSTERFUNK DEUTSCHLAND GMBH",
          note: "Rechnung Mai",
          amount_step_index: 0,
        },
      ],
    };

    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/review/contract-suggestions/txn-musterfunk-6")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(detail) });
      }
      if (url.includes("/v1/review")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...mockReviewInbox,
              total_count: 1,
              unclassified_open_count: 0,
              unclassified_total_count: 0,
              unclassified: [],
              contract_suggestions: [suggestion],
            }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    await user.click(
      await screen.findByRole("button", {
        name: "Details und zugehörige Buchungen für Musterfunk Deutschland anzeigen",
      }),
    );

    expect(
      await screen.findByRole("heading", { name: "Erkennungsdetails · Musterfunk Deutschland" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Rechnung Mai")).toBeInTheDocument();
    // Ohne die Preisstufenspalte liesse sich nicht sehen, welche Buchung zu
    // welchem Preis gehörte.
    expect(screen.getByText("-49,95 € (ab Jan. 2026)")).toBeInTheDocument();
    expect(screen.getByText("-39,95 € (ab Juni 2026)")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/v1/review/contract-suggestions/txn-musterfunk-6"),
      ),
    ).toBe(true);
  });

  it("übernimmt einen Vorschlag als EINEN Vertrag mit Betragsverlauf", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/categories")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(CATEGORY_GROUPS),
        });
      }
      if (url.includes("/v1/review/accept")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
      }
      if (url.includes("/v1/review")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...mockReviewInbox,
              total_count: 1,
              unclassified_open_count: 0,
              unclassified_total_count: 0,
              unclassified: [],
              contract_suggestions: [makeContractSuggestion()],
            }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    await user.click(await screen.findByRole("button", { name: "Als Vertrag übernehmen" }));

    const dialog = await screen.findByRole("dialog", { name: "Vertrag übernehmen" });
    expect(dialog.textContent).toMatch(/Es entsteht ein einziger Vertrag mit Betragsverlauf/);

    await user.click(await screen.findByLabelText(/Ziel-Kategorie/));
    await user.click(await screen.findByRole("option", { name: /Internet & Telefon/ }));
    await user.click(screen.getByRole("button", { name: "Bestätigen & Speichern" }));

    await waitFor(() => {
      const acceptCall = fetchMock.mock.calls.find(([input]) =>
        String(input).includes("/v1/review/accept"),
      );
      expect(acceptCall).toBeDefined();
    });

    const acceptCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/v1/review/accept"),
    );
    const body = JSON.parse(String((acceptCall?.[1] as RequestInit | undefined)?.body));
    // Betrag, Rhythmus und Preisstufen ermittelt der Server erneut - der
    // Client schickt bewusst nur Anker und Kategorie.
    expect(body).toEqual({
      type: "contract_suggestion",
      source_transaction_id: "txn-musterfunk-6",
      category_id: "cat-telecom",
    });
  });

  it("führt zwei Einträge desselben Vertrags auf den jüngeren zusammen", async () => {
    const user = userEvent.setup();
    // Die Signatur nimmt init mit auf, damit der gesendete Body geprüft werden
    // kann - fetchMock.mock.calls ist sonst ein Ein-Element-Tupel.
    const duplicate = {
      type: "contract_duplicate_suggestion",
      target_id: "contract-neu",
      source_ids: ["contract-alt"],
      payee_label: "MaxEnergy",
      frequency: "monthly",
      direction: "expense",
      older: {
        contract_id: "contract-alt",
        name: "MaxEnergy Strom",
        provider_label: "MaxEnergy",
        amount_minor: 14700,
        status: "active",
        first_seen_date: "2025-07-01",
        last_seen_date: "2026-06-01",
      },
      newer: {
        contract_id: "contract-neu",
        name: "MaxEnergy Strom neu",
        provider_label: "MaxEnergy",
        amount_minor: 12300,
        status: "active",
        first_seen_date: "2026-07-01",
        last_seen_date: "2026-08-01",
      },
      gap_days: 30,
      amount_delta_minor: -2400,
    };

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/review/accept")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
      }
      if (url.includes("/v1/review")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              ...mockReviewInbox,
              total_count: 1,
              unclassified_open_count: 0,
              unclassified_total_count: 0,
              unclassified: [],
              contract_duplicate_suggestions: [duplicate],
            }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Vermutlich derselbe Vertrag (1)" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/30 Tage dazwischen/)).toBeInTheDocument();
    expect(screen.getByText("-24,00 €")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Zusammenführen" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes("/v1/review/accept")),
      ).toBe(true);
    });

    const acceptCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/v1/review/accept"),
    );
    const body = JSON.parse(String((acceptCall?.[1] as RequestInit | undefined)?.body));
    // Ziel ist IMMER der jüngere Vertrag - er trägt den aktuellen Preis.
    expect(body).toEqual({
      type: "contract_duplicate_suggestion",
      target_id: "contract-neu",
      source_ids: ["contract-alt"],
      dismiss: false,
    });
  });

  it("startet einen Klassifizierungslauf und zeigt den Fortschritt an", async () => {
    let started = false;

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (url.includes("/v1/review")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockReviewInbox),
          });
        }
        if (url.endsWith("/v1/ai/tasks") && init?.method === "POST") {
          started = true;
          return Promise.resolve({
            ok: true,
            status: 201,
            json: () => Promise.resolve(makeClassificationTask()),
          });
        }
        if (url.includes("/v1/ai/classify-runs")) {
          if (!started) {
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
          }
          // Der Fortschritt kommt aus dem Poll auf die Laufliste, nicht mehr
          // aus einer im Client gemerkten Run-ID.
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve([makeRun({ status: "running", processed_count: 1 })]),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }),
    );

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <HouseholdProvider>
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const user = userEvent.setup();
    const startButton = await screen.findByRole("button", {
      name: "✨ KI-Kategorisierung für alle offenen Buchungen starten",
    });
    // Der Knopf ist so lange deaktiviert, bis Sitzung und Haushalt geladen
    // sind - erst dann kann ein echter Klick etwas auslösen.
    await waitFor(() => expect(startButton).toBeEnabled());
    await user.click(startButton);

    await waitFor(
      () => {
        expect(screen.getByText(/1 von 1 klassifiziert/)).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
  }, 8000);

  it("findet einen laufenden Lauf ohne vorherigen Klick wieder (Reload/Seitenwechsel)", async () => {
    // Kern der Ausbaustufe 0: dieser Browser hat den Lauf nie gestartet - er
    // liest ihn beim Mount vom Server. Vorher zeigte die neu geladene Seite
    // faelschlich wieder den Startknopf.
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (url.includes("/v1/review")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockReviewInbox),
          });
        }
        if (url.includes("/v1/ai/classify-runs") && init?.method !== "POST") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve([
                makeRun({ status: "running", total_count: 42, processed_count: 17 }),
              ]),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }),
    );

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <HouseholdProvider>
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/17 von 42 klassifiziert/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lauf abbrechen" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "✨ KI-Kategorisierung für alle offenen Buchungen starten",
      }),
    ).not.toBeInTheDocument();
  });

  it("zeigt eine Fehlermeldung wenn der Klassifizierungslauf fehlschlägt", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/v1/me")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(meResponse),
          });
        }
        if (url.includes("/v1/review")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(mockReviewInbox),
          });
        }
        const failedRun = makeRun({
          id: "run-2",
          status: "failed",
          error_message: "LM Studio nicht erreichbar oder kein Modell geladen.",
          completed_at: "2026-08-01T00:01:00Z",
        });
        if (url.endsWith("/v1/ai/tasks") && init?.method === "POST") {
          return Promise.resolve({
            ok: true,
            status: 201,
            json: () =>
              Promise.resolve(
                makeClassificationTask({
                  status: "failed",
                  error_message: "LM Studio nicht erreichbar oder kein Modell geladen.",
                }),
              ),
          });
        }
        if (url.includes("/v1/ai/classify-runs")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve([failedRun]),
          });
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
      }),
    );

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AuthProvider>
            <HouseholdProvider>
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const user = userEvent.setup();
    const startButton = await screen.findByRole("button", {
      name: "✨ KI-Kategorisierung für alle offenen Buchungen starten",
    });
    // Der Knopf ist so lange deaktiviert, bis Sitzung und Haushalt geladen
    // sind - erst dann kann ein echter Klick etwas auslösen.
    await waitFor(() => expect(startButton).toBeEnabled());
    await user.click(startButton);

    await waitFor(() => {
      expect(
        screen.getByText(/Klassifizierung fehlgeschlagen: LM Studio nicht erreichbar/),
      ).toBeInTheDocument();
    });

    // Ein gescheiterter Lauf darf den Neustart nicht verdecken.
    expect(
      screen.getByRole("button", {
        name: "✨ KI-Kategorisierung für alle offenen Buchungen starten",
      }),
    ).toBeInTheDocument();
  });

  it("legt bei einer Korrektur keine zusaetzliche Regel per POST /v1/rules an", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/categories")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                id: "grp-1",
                name: "Lebenshaltung",
                sort_order: 0,
                categories: [
                  {
                    id: "cat-1",
                    group_id: "grp-1",
                    name: "Abos",
                    default_type: "expense",
                    budget_bucket: "wants",
                    is_archived: false,
                  },
                ],
              },
            ]),
        });
      }
      if (url.includes("/v1/review/correct")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ status: "corrected" }),
        });
      }
      if (url.includes("/v1/review/rule-preview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              rule_id: mockReviewInbox.unclassified[0]!.transaction_id,
              rule_name: "Regel fuer Netflix",
              scope: "uncategorized",
              dry_run: true,
              matched: 3,
              applied: 3,
              unchanged: 0,
              skipped: 0,
              conflict: 0,
              truncated: false,
              items: [],
            }),
        });
      }
      if (url.includes("/v1/review/history-match/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              matched: false,
              category_id: null,
              category_label: null,
              evidence_count: 0,
              ambiguous: false,
            }),
        });
      }
      if (url.includes("/v1/review")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockReviewInbox),
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
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Korrigieren / Regel erstellen" }));
    expect(screen.getByRole("dialog", { name: "Fall korrigieren & Regel erstellen" })).toHaveClass(
      "review-correction-modal",
    );

    // Zuerst wird ohne Modellaufruf im bereits kategorisierten Bestand
    // gesucht. Erst wenn dort nichts eindeutig ist, braucht es die manuelle
    // Auswahl oder den separaten KI-Knopf.
    await user.click(screen.getByRole("button", { name: "Bestand & Regeln prüfen" }));
    expect(await screen.findByText(/noch kein bestätigtes Vorbild/)).toBeInTheDocument();

    // Auch der Regelvergleich offener Buchungen funktioniert vor der
    // Kategorienwahl und schreibt noch nichts.
    await user.click(
      screen.getByRole("button", {
        name: "🔍 Vorschau offener Treffer prüfen",
      }),
    );
    expect(await screen.findByText(/3 offene Treffer/)).toBeInTheDocument();
    const previewCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/v1/review/rule-preview"),
    );
    expect(JSON.parse(String((previewCall?.[1] as RequestInit).body))).toMatchObject({
      category_id: null,
      match_payee: true,
      match_exact_amount: true,
    });

    // Die Zielkategorie laeuft jetzt ueber den durchsuchbaren Picker
    // (role="combobox" + role="listbox"), nicht mehr ueber ein natives
    // <select> - jeder Treffer traegt den vollen Pfad "Gruppe · Kategorie".
    await user.click(await screen.findByLabelText(/Ziel-Kategorie/));
    await user.click(await screen.findByRole("option", { name: /Lebenshaltung · Abos/ }));
    await user.click(
      await screen.findByRole("button", { name: "Regel anlegen & 3 Treffer zuordnen" }),
    );

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([input]) => String(input).includes("/v1/review/correct")),
      ).toHaveLength(1);
    });

    const correctCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/v1/review/correct"),
    );
    expect(JSON.parse(String((correctCall?.[1] as RequestInit).body))).toMatchObject({
      create_rule_always: true,
      rule_match_payee: true,
      rule_match_exact_amount: true,
      apply_rule_to_existing: true,
    });

    // Die Regel legt allein das Backend an (create_rule_always). Ein
    // zusaetzliches POST /v1/rules erzeugte frueher doppelte Regeln.
    // (Lesende GETs auf /v1/rules sind erlaubt: die Seite zeigt damit eine
    // konkurrierende bestehende Regel an, statt sie still zu ueberschreiben.)
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith("/v1/rules") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toHaveLength(0);
  });

  it("uebernimmt sichere KI-Vorschlaege erst nach der Vorschau", async () => {
    const user = userEvent.setup();
    const batchResult = {
      min_confidence: 0.85,
      applied: 1,
      unchanged: 1,
      skipped: 1,
      conflict: 0,
      idempotent_replay: false,
      items: [
        {
          transaction_id: "11111111-1111-1111-1111-111111111111",
          category_id: "cat-1",
          category_name: "Abos",
          confidence: 0.93,
          reason_code: "merchant_and_context_match",
          status: "applied",
          reason: null,
          current_version: 2,
        },
        {
          transaction_id: "22222222-2222-2222-2222-222222222222",
          category_id: null,
          category_name: null,
          confidence: 0.4,
          reason_code: "no_matching_category",
          status: "skipped",
          reason: "no_match",
          current_version: null,
        },
        {
          transaction_id: "33333333-3333-3333-3333-333333333333",
          category_id: "cat-1",
          category_name: "Abos",
          confidence: 0.9,
          reason_code: "merchant_and_context_match",
          status: "unchanged",
          reason: "already_categorized",
          current_version: 5,
        },
      ],
    };

    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/v1/me")) {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(meResponse) });
      }
      if (url.includes("/v1/ai/proposals/preview") || url.includes("/v1/ai/proposals/apply")) {
        void init;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(batchResult),
        });
      }
      if (url.includes("/v1/review")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockReviewInbox),
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
              <ReviewInboxPage />
            </HouseholdProvider>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await user.click(
      await screen.findByRole("button", { name: "Vorschläge des letzten Laufs prüfen" }),
    );

    // Erst die Vorschau - sie darf noch nichts angewendet haben.
    await screen.findByText(/Sichere Lauf-Vorschläge – Vorschau/);
    expect(screen.getByText(/kein passender Treffer der KI/)).toBeInTheDocument();
    expect(screen.getByText(/bereits kategorisiert/)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([i]) => String(i).includes("/v1/ai/proposals/apply")),
    ).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "1 Buchung(en) übernehmen" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([i]) => String(i).includes("/v1/ai/proposals/apply")),
      ).toHaveLength(1);
    });
  });

  it("zählt bei einer abgebrochenen Dubletten-Einschätzung nur beurteilte Paare", async () => {
    const user = userEvent.setup();
    const duplicate = (id: string, payee: string) => ({
      type: "possible_duplicate",
      import_row_id: id,
      import_run_id: "import-1",
      booked_date: "2026-08-01",
      amount_minor: -1599,
      payee_raw: payee,
      purpose: null,
      duplicate_of_transaction_id: `txn-${id}`,
      duplicate_of: null,
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      if (url.includes("/v1/me")) return json(meResponse);
      if (url.includes("/v1/review/duplicates/assess")) {
        return json({
          available: true,
          reason: "model_budget_exhausted",
          message:
            "1 von 2 Paaren beurteilt (aufgeteilt in 2 Stapel nach Platz im Kontextfenster); die uebrigen nicht: Das Antwortbudget war aufgebraucht, bevor eine Antwort stand.",
          model_name: "test-model",
          prompt_version: "dup-v3",
          assessments: [
            {
              import_row_id: "row-1",
              verdict: "same",
              reason: "Gleiche Referenz und gleicher Betrag.",
              confidence: 0.95,
            },
            {
              import_row_id: "row-2",
              verdict: "unsure",
              reason:
                "Nicht beurteilt: Das Antwortbudget war aufgebraucht, bevor eine Antwort stand.",
              confidence: 0,
            },
          ],
        });
      }
      if (url.includes("/v1/review")) {
        return json({
          ...mockReviewInbox,
          possible_duplicates: [duplicate("row-1", "Stadtwerke"), duplicate("row-2", "Bäckerei")],
        });
      }
      return json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByRole("heading", { name: "Mögliche Dubletten (2)" });
    for (const box of screen.getAllByRole("checkbox", { name: "Zeile vom 2026-08-01 auswählen" })) {
      await user.click(box);
    }
    await user.click(screen.getByRole("button", { name: "Von KI einschätzen lassen" }));

    const hinweis = await screen.findByText(/1 von 2 Paaren beurteilt/);
    expect(hinweis).toHaveTextContent("Das Antwortbudget war aufgebraucht");
    expect(
      screen.getByRole("link", { name: "Reasoning unter Einstellungen › KI umstellen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
    expect(screen.queryByText(/2 beurteilt/)).not.toBeInTheDocument();
    expect(screen.getByText("KI: dieselbe Buchung")).toBeInTheDocument();
    // Das nicht gefragte Paar ist kein Urteil "unsicher".
    expect(screen.getByText("KI: nicht beurteilt")).toBeInTheDocument();
    expect(screen.queryByText("KI: unsicher")).not.toBeInTheDocument();
  });
});
