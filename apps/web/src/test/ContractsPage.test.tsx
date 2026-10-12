import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ContractsPage } from "../pages/ContractsPage";

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

function contract(overrides: Record<string, unknown> = {}) {
  return {
    id: "contract-1",
    household_id: "household-1",
    name: "Musterfunk Festnetz",
    provider_label: "Musterfunk Deutschland GmbH",
    contract_type: "telecom",
    payee_id: null,
    account_id: null,
    category_id: null,
    contract_key: null,
    contract_key_source: null,
    direction: "expense",
    amount_minor: 3995,
    frequency: "monthly",
    due_rule: "day_of_month",
    due_day: 20,
    next_due_date: "2026-08-20",
    first_seen_date: null,
    last_seen_date: null,
    tolerance_days: 4,
    tolerance_amount_minor: 0,
    status: "active",
    cancelled_on: null,
    ends_on: null,
    min_term_months: null,
    cancellation_period_days: null,
    installments_total: null,
    installments_paid: 0,
    source: "manual",
    detection_confidence: null,
    missed_periods: 0,
    last_matched_at: null,
    starts_on: null,
    renewal_term_months: null,
    provider_template_key: null,
    created_at: "2026-01-01T00:00:00Z",
    term_deadline: null,
    note: null,
    reserve_enabled: false,
    reserve_started_on: null,
    reserve_starting_amount_minor: 0,
    reserve_monthly_minor: 0,
    version: 1,
    ...overrides,
  };
}

// Kategoriebaum fuer die kategorie-fuehrende Vertragsliste (Filter, Sortierung,
// "Nach Kategorie"-Register, Fixkostenziel-Gruppierung). Zwei Gruppen mit je
// einer Kategorie reichen, um Gruppen-Sortierung ("Versicherungen" vor
// "Wohnen") und den unkategorisierten Rest ("Nicht kategorisiert") zu pruefen.
function categoryFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "category-x",
    group_id: "group-x",
    name: "Kategorie X",
    default_type: "expense",
    budget_bucket: "needs",
    is_archived: false,
    icon: null,
    color: "#4C6EF5",
    is_budgeted: true,
    sort_order: 1,
    synonyms: [],
    description: null,
    semantic_key: null,
    version: 1,
    is_tax_deductible: false,
    ...overrides,
  };
}

const categoryGroups = [
  {
    id: "group-versicherungen",
    name: "Versicherungen",
    sort_order: 1,
    is_archived: false,
    icon: null,
    color: "#4C6EF5",
    semantic_key: "insurance",
    version: 1,
    categories: [
      categoryFixture({
        id: "category-hausrat",
        group_id: "group-versicherungen",
        // Bewusst nicht "Hausratversicherung": das ist schon der Vertragsname
        // in den Fixtures oben, ein gleichlautender Kategoriename machte
        // "getByText" in der Tabellenzeile zweideutig.
        name: "Hausrat",
        color: "#4C6EF5",
      }),
    ],
  },
  {
    id: "group-wohnen",
    name: "Wohnen",
    sort_order: 2,
    is_archived: false,
    icon: null,
    color: "#12B886",
    semantic_key: "housing",
    version: 1,
    categories: [
      categoryFixture({
        id: "category-miete",
        group_id: "group-wohnen",
        name: "Miete",
        color: "#12B886",
      }),
    ],
  },
];

const contracts = [
  contract({
    // Laufende Mindestlaufzeit: die Zeile muss den Countdown bis zum letzten
    // Kuendigungstermin zeigen, nicht bis zum Laufzeitende.
    min_term_months: 24,
    cancellation_period_days: 90,
    term_deadline: {
      kind: "fixed_term",
      deadline_on: "2026-08-24",
      term_ends_on: "2026-11-22",
      days_left: 21,
      renewal_index: 1,
    },
  }),
  contract({
    id: "contract-2",
    name: "Wohnungsmiete",
    provider_label: null,
    contract_type: "rent",
    amount_minor: 115000,
    next_due_date: "2026-08-02",
    // Aktiv UND seit zwei Faelligkeiten unbezahlt - genau der Fall, in dem die
    // Liste warnen muss, ohne den Status schon eigenmaechtig zu aendern.
    missed_periods: 2,
    last_seen_date: "2026-06-02",
    category_id: "category-miete",
  }),
  contract({
    id: "contract-3",
    name: "Gehalt",
    provider_label: null,
    contract_type: "income",
    direction: "income",
    amount_minor: 320000,
    next_due_date: "2026-08-28",
  }),
  // Die beiden folgenden bilden den Fall aus Plan I11 ab: eine Jahrespraemie
  // ueber 600 EUR kostet mehr als ein Abo fuer 9,99 EUR/Monat, steht aber bei
  // jeder Sortierung nach Monatsbetrag hinter ihm.
  contract({
    id: "contract-4",
    name: "Hausratversicherung",
    provider_label: null,
    contract_type: "insurance",
    amount_minor: 60000,
    frequency: "yearly",
    next_due_date: "2026-12-01",
    category_id: "category-hausrat",
    reserve_enabled: true,
    reserve_started_on: "2026-08-01",
    reserve_monthly_minor: 5000,
  }),
  contract({
    id: "contract-5",
    name: "Streamingabo",
    provider_label: null,
    contract_type: "subscription",
    amount_minor: 999,
    next_due_date: "2026-08-05",
  }),
];

const summary = {
  // 115000 (Miete) + 3995 (Telekommunikation) = 118995 im Monat,
  // 1380000 + 47940 = 1427940 im Jahr, 320000 - 118995 = 201005 netto.
  monthly_expense_minor: 118995,
  yearly_expense_minor: 1427940,
  monthly_income_minor: 320000,
  yearly_income_minor: 3840000,
  monthly_net_minor: 201005,
  active_count: 3,
  paused_count: 0,
  cancelled_count: 0,
  ended_count: 0,
  // Absteigend nach Monatskosten, wie contract_service.py sie liefert -
  // deckt sich mit den fuenf Vertraegen oben (Gehalt, Miete, Musterfunk,
  // Hausrat, Streaming).
  by_type: [
    { contract_type: "income", count: 1, monthly_minor: 320000, yearly_minor: 3840000 },
    { contract_type: "rent", count: 1, monthly_minor: 115000, yearly_minor: 1380000 },
    { contract_type: "telecom", count: 1, monthly_minor: 3995, yearly_minor: 47940 },
    { contract_type: "insurance", count: 1, monthly_minor: 5000, yearly_minor: 60000 },
    { contract_type: "subscription", count: 1, monthly_minor: 999, yearly_minor: 11988 },
  ],
  // Kategorie ist die fuehrende Gruppierung: Musterfunk Festnetz, Gehalt und
  // Streamingabo bleiben ohne Kategorie und buendeln sich unter
  // `category_id: null`; Wohnungsmiete und Hausratversicherung tragen ihre
  // Kategorie aus den contracts oben.
  by_category: [
    { category_id: null, count: 3, monthly_minor: 326493, yearly_minor: 3917916 },
    { category_id: "category-miete", count: 1, monthly_minor: 115000, yearly_minor: 1380000 },
    { category_id: "category-hausrat", count: 1, monthly_minor: 5000, yearly_minor: 60000 },
  ],
  upcoming: [],
  ending_soon: [],
  yearly_by_contract: [],
  term_deadlines: [
    {
      contract_id: "contract-4",
      name: "Hausratversicherung",
      contract_type: "insurance",
      amount_minor: 60000,
      deadline_on: "2026-09-30",
      term_ends_on: "2026-12-31",
      days_left: 58,
    },
  ],
};

// Erwartete Faelligkeiten fuer die Prognosekarte. Die Namen sind bewusst
// andere als in der Vertragsliste - sonst faende getByRole("row", ...) zwei
// Zeilen und die Zuordnung im Test waere Zufall.
const forecastItems = [
  {
    contract_id: "contract-9",
    name: "Stromabschlag",
    contract_type: "utility",
    direction: "expense",
    due_date: "2026-08-05",
    amount_minor: 8900,
  },
  {
    contract_id: "contract-10",
    name: "Nebenjob",
    contract_type: "income",
    direction: "income",
    due_date: "2026-08-10",
    amount_minor: 50000,
  },
];

// Antwort von GET /v1/contracts/cashflow: Kalendermonate statt rollierender
// Tagesfenster. Der zweite Monat wiederholt dieselben Vertraege - genau daran
// zeigt sich die Kumulation ueber PERIODEN, die die frueherere zeilenweise
// laufende Summe abgeloest hat.
const cashflow = {
  granularity: "month",
  generated_on: "2026-08-04",
  periods: [
    {
      period_start: "2026-08-01",
      period_end: "2026-08-31",
      label: "August 2026",
      expense_minor: 8900,
      income_minor: 50000,
      net_minor: 41100,
      remaining_expense_minor: 8900,
      remaining_income_minor: 50000,
      remaining_net_minor: 41100,
      cumulative_net_minor: 41100,
      items: forecastItems,
    },
    {
      period_start: "2026-09-01",
      period_end: "2026-09-30",
      label: "September 2026",
      expense_minor: 8900,
      income_minor: 50000,
      net_minor: 41100,
      remaining_expense_minor: 8900,
      remaining_income_minor: 50000,
      remaining_net_minor: 41100,
      cumulative_net_minor: 82200,
      items: forecastItems,
    },
  ],
};

const costTarget = {
  target_ratio_percent: 40,
  target_monthly_minor: null,
  target_date: "2027-01-01",
  note: null,
  is_configured: true,
  current_monthly_minor: 120000,
  current_yearly_minor: 1440000,
  current_ratio_percent: 48,
  income_basis_minor: 250000,
  income_basis: "actual",
  contract_count: 4,
  effective_target_monthly_minor: 100000,
  gap_monthly_minor: 20000,
  gap_yearly_minor: 240000,
  on_track: false,
  active_scenario: null,
};

// Fuer die Buchungs-Nachschlagesuche im Anlege-Formular (AmountLookupPanel):
// eine gebuchte Zahlung, an der sich der tatsaechliche Betrag ablesen laesst,
// wenn der Anbieterkatalog zu einem Anbieter nichts findet.
const lookupTransactions = [
  {
    id: "txn-musterassekuranz",
    account_id: "account-1",
    booked_date: "2026-03-14",
    value_date: "2026-03-14",
    amount_minor: -3499,
    currency: "EUR",
    payee_label: "Musterassekuranz Versicherung AG",
    note: null,
    status: "posted",
    is_transfer: false,
    category_id: null,
    is_split: false,
    is_archived: false,
    version: 1,
  },
];

const providerSuggestions = [
  {
    semantic_key: "musterfunk",
    provider_name: "Musterfunk",
    contract_type: "telecom",
    default_category_key: "utilities_telecom",
    category_id: null,
    typical_frequency: "monthly",
    typical_min_term_months: 24,
    typical_cancellation_days: 90,
  },
];

// Duplikatvorschlag im Format von GET /v1/contracts/duplicate-suggestions.
function duplicateSuggestion(overrides: Record<string, unknown> = {}) {
  return {
    type: "contract_duplicate_suggestion",
    target_id: "contract-1",
    source_ids: ["contract-2"],
    payee_label: "Musterfunk Deutschland GmbH",
    frequency: "monthly",
    direction: "expense",
    older: {
      contract_id: "contract-2",
      name: "Musterfunk Festnetz alt",
      provider_label: "Musterfunk",
      amount_minor: 4995,
      status: "active",
      first_seen_date: "2025-08-01",
      last_seen_date: "2026-01-01",
    },
    newer: {
      contract_id: "contract-1",
      name: "Musterfunk Festnetz",
      provider_label: "Musterfunk",
      amount_minor: 3995,
      status: "active",
      first_seen_date: "2026-02-01",
      last_seen_date: "2026-07-01",
    },
    gap_days: 30,
    amount_delta_minor: -1000,
    evidence_strength: "strong",
    reason_codes: ["same_contract_key", "amount_transition"],
    ...overrides,
  };
}

// Antwort von POST /v1/contracts/ai-crosscheck. Der Endpunkt antwortet auch
// im Fehlerfall mit 200 und legt den Klartext in `meldung` - deshalb hat der
// Mock keinen Fehlerzweig.
const emptyCrosscheck = {
  items: [],
  geprueft: 0,
  uebersprungen: 0,
  uebersprungen_gruende: [],
  truncated: false,
  modell_name: null,
  meldung: null,
};

// Ein beurteiltes Paar plus ein wegen verschiedener Vertragsschluessel
// uebersprungenes. Beide Partner sind Vertraege aus der geladenen Liste -
// sonst fehlte der Zielvertrag fuer die Merge-Vorschau.
const aiCrosscheck = {
  items: [
    {
      contract_a: { id: "contract-1", name: "Musterfunk Festnetz", amount_minor: 3995 },
      contract_b: { id: "contract-2", name: "Wohnungsmiete", amount_minor: 115000 },
      gehoert_zusammen: true,
      begruendung:
        "Beide Zahlungen nennen dieselbe Kundenkontonummer, und der Betrag wechselt genau zum Stichtag der Preissenkung.",
      quelle: "ai",
    },
  ],
  geprueft: 1,
  uebersprungen: 1,
  uebersprungen_gruende: [
    {
      contract_a: { id: "contract-4", name: "Hausratversicherung", amount_minor: 60000 },
      contract_b: { id: "contract-5", name: "Streamingabo", amount_minor: 999 },
      grund: "different_contract_key",
    },
  ],
  truncated: false,
  modell_name: "gemma-4-26b",
  meldung: null,
};

// Ergebnis von POST /v1/contracts/merge. `merge_id` ist der Anker fuer das
// sofortige Rueckgaengigmachen (Plan 19.7).
const mergeResult = {
  contract: contract({
    contract_key: "DE000200000100000000000000001234567",
    contract_key_source: "mandate",
  }),
  merged_count: 1,
  moved_occurrences: 3,
  moved_history_entries: 1,
  merge_id: "merge-1",
};

// ---------------------------------------------------------------------------
// Belege je Preisstufe, freie Buchungen, Nachtragen (Plan 19.7/19.11)
// ---------------------------------------------------------------------------
//
// Die Daten bilden einen Festnetzvertrag ab: zwei Preisstufen, die beide am
// Tag der Datenuebernahme beginnen und nichts belegen, vier zugeordnete
// Buchungen ausserhalb dieser Stufen - und drei aeltere Buchungen derselben
// Mandatsreferenz, die voellig frei danebenliegen.

function amountStage(overrides: Record<string, unknown> = {}) {
  return {
    id: "stage-1",
    contract_id: "contract-1",
    amount_minor: 4995,
    valid_from: "2026-08-03",
    valid_to: "2026-08-03",
    delta_minor: null,
    change_reason: "initial",
    source_transaction_id: null,
    ...overrides,
  };
}

const amountEvidence = {
  contract_id: "contract-1",
  stages: [
    {
      stage: amountStage(),
      transactions: [],
      transaction_count: 0,
      has_source_transaction: false,
      has_evidence: false,
    },
    {
      stage: amountStage({
        id: "stage-2",
        amount_minor: 3995,
        valid_to: null,
        delta_minor: -1000,
      }),
      transactions: [],
      transaction_count: 0,
      has_source_transaction: false,
      has_evidence: false,
    },
  ],
  transactions_outside_stages: [
    {
      transaction_id: "txn-apr",
      booked_date: "2026-04-20",
      amount_minor: -3995,
      payee_label: "Musterfunk Deutschland GmbH",
      purpose_excerpt: "Rechnung April",
      account_id: null,
      due_date: "2026-04-20",
      is_stage_source: false,
    },
  ],
  stages_without_evidence: 2,
  assigned_transaction_count: 4,
};

function freeTransaction(overrides: Record<string, unknown> = {}) {
  return {
    transaction_id: "txn-jan",
    booked_date: "2026-01-21",
    amount_minor: -4995,
    payee_label: "Musterfunk Deutschland GmbH",
    purpose_excerpt: "Rechnung Januar",
    account_id: null,
    due_date: null,
    is_stage_source: false,
    ...overrides,
  };
}

const freeTransactionItems = [
  freeTransaction(),
  freeTransaction({
    transaction_id: "txn-feb",
    booked_date: "2026-02-18",
    purpose_excerpt: "Rechnung Februar",
  }),
  freeTransaction({
    transaction_id: "txn-mar",
    booked_date: "2026-03-18",
    purpose_excerpt: "Rechnung März",
  }),
  freeTransaction({
    transaction_id: "txn-gym",
    booked_date: "2026-03-02",
    amount_minor: -1999,
    payee_label: "Fitnessstudio Nord",
    purpose_excerpt: "Monatsbeitrag",
  }),
];

const defaultFreeFilter = {
  date_from: "2023-08-04",
  date_to: "2026-08-04",
  amount_min_minor: 0,
  amount_max_minor: 27976,
  search: "musterfunk",
  from_contract_defaults: true,
};

const assignmentPreview = {
  contract_id: "contract-1",
  contract_name: "Musterfunk Festnetz",
  transactions: freeTransactionItems.slice(0, 3),
  sources: [{ contract_id: null, contract_name: null, transaction_count: 3 }],
  unknown_transaction_ids: [],
  first_seen_date_before: "2026-04-20",
  first_seen_date_after: "2026-01-21",
  last_seen_date_before: "2026-07-20",
  last_seen_date_after: "2026-07-20",
  amount_stages_before: [amountStage(), amountStage({ id: "stage-2", amount_minor: 3995 })],
  amount_stages_after: [
    {
      amount_minor: 4995,
      valid_from: "2026-01-21",
      valid_to: "2026-03-18",
      delta_minor: null,
      transaction_count: 3,
      source_transaction_id: "txn-jan",
    },
    {
      amount_minor: 3995,
      valid_from: "2026-04-20",
      valid_to: null,
      delta_minor: -1000,
      transaction_count: 4,
      source_transaction_id: "txn-apr",
    },
  ],
  stages_without_evidence_before: 2,
};

const recalculation = {
  contract_id: "contract-1",
  changed: true,
  stages_written: 2,
  stages_removed: 2,
  manual_stages_kept: 0,
  amount_stages: [],
  first_seen_date: "2026-01-21",
  last_seen_date: "2026-07-20",
  resulting_amount_minor: 3995,
  contract_amount_minor: 3995,
};

// Antwort von POST /v1/contracts/{id}/ai-transaction-check. Ein Treffer, eine
// Absage - und kein Konfidenzfeld, weder im Schema noch hier.
const aiTransactionCheck = {
  contract_id: "contract-1",
  items: [
    {
      transaction_id: "txn-jan",
      booked_date: "2026-01-21",
      amount_minor: -4995,
      payee_label: "Musterfunk Deutschland GmbH",
      gehoert_zum_vertrag: true,
      begruendung:
        "Dieselbe Mandatsreferenz wie die zugeordneten Buchungen; der Betrag entspricht dem Preis vor der Senkung.",
      quelle: "ai",
    },
    {
      transaction_id: "txn-gym",
      booked_date: "2026-03-02",
      amount_minor: -1999,
      payee_label: "Fitnessstudio Nord",
      gehoert_zum_vertrag: false,
      begruendung: "Anderer Empfänger und ein Betrag, der zu keiner Preisstufe passt.",
      quelle: "ai",
    },
  ],
  geprueft: 2,
  uebersprungen: 0,
  uebersprungen_gruende: [],
  truncated: false,
  kandidaten_gesamt: 4,
  vorauswahl: defaultFreeFilter,
  modell_name: "gemma-4-26b",
  meldung: null,
};

function aiTask(
  id: string,
  taskType: "contract_duplicate_crosscheck" | "contract_transaction_check",
  result: Record<string, unknown>,
  input: Record<string, unknown> = {},
) {
  return {
    id,
    task_type: taskType,
    status: "awaiting_confirmation",
    queue_order: 1,
    request_id: "test-request",
    input,
    result,
    result_summary: {},
    route_path: `/fixkosten?ai_task=${id}`,
    current_stage: "awaiting_confirmation",
    progress_current: 0,
    progress_total: null,
    status_message: "Ergebnis wartet auf deine Prüfung.",
    model_name: "gemma-4-26b",
    error_message: null,
    cancel_requested: false,
    attempt_count: 1,
    category_operation_id: null,
    classification_run_id: null,
    created_at: "2026-08-05T10:00:00Z",
    started_at: "2026-08-05T10:00:01Z",
    completed_at: "2026-08-05T10:00:02Z",
    heartbeat_at: "2026-08-05T10:00:02Z",
    updated_at: "2026-08-05T10:00:02Z",
    is_stale: false,
  };
}

const undoPreview = {
  merge_id: "merge-1",
  target_id: "contract-1",
  target_name: "Musterfunk Festnetz",
  source_ids: ["contract-2"],
  source_names: ["Wohnungsmiete"],
  already_undone: false,
  can_undo: true,
  conflict_reasons: [],
  new_occurrence_count: 0,
  merged_at: "2026-08-04T10:00:00Z",
};

function renderContractsPage(
  role = "owner",
  duplicateSuggestions: { items: unknown[]; total: number; truncated: boolean } = {
    items: [],
    total: 0,
    truncated: false,
  },
  crosscheck: Record<string, unknown> = emptyCrosscheck,
  // Haelt die Antwort der Gegenpruefung auf, bis der Test sie freigibt - nur
  // so ist der Ladezustand ueberhaupt beobachtbar. Ohne Tor antwortet der Mock
  // im selben Microtask und die Zwischenanzeige existiert nie.
  crosscheckGate?: Promise<void>,
  // Nur die Buchungspruefung des Sprachmodells wird je Test ausgetauscht; alle
  // uebrigen neuen Endpunkte antworten immer gleich.
  options: { aiTransactionCheck?: Record<string, unknown>; scenarios?: unknown[] } = {},
) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.includes("/v1/contracts/duplicate-suggestions")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(duplicateSuggestions),
      });
    }
    if (url.includes("/amount-history/evidence")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(amountEvidence),
      });
    }
    if (url.includes("/amount-history/recalculate") && method === "POST") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(recalculation) });
    }
    if (url.includes("/free-transactions")) {
      // Der Mock sucht wirklich - sonst waere "die Auswahl filtert" nur eine
      // Aussage ueber den Query-String und nicht ueber die Liste.
      const params = new URL(url, "http://localhost").searchParams;
      const needle = (params.get("search") ?? "").toLowerCase();
      const items = freeTransactionItems.filter(
        (item) => !needle || item.payee_label.toLowerCase().includes(needle),
      );
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            items,
            total: items.length,
            truncated: false,
            limit: 25,
            offset: 0,
            applied_filter: {
              ...defaultFreeFilter,
              search: params.get("search") ?? defaultFreeFilter.search,
              from_contract_defaults: params.get("use_contract_defaults") !== "false",
            },
          }),
      });
    }
    if (url.includes("/assignment-preview") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(assignmentPreview),
      });
    }
    if (url.endsWith("/v1/ai/tasks") && method === "POST") {
      const body = JSON.parse(String(init?.body)) as {
        task_type: "contract_duplicate_crosscheck" | "contract_transaction_check";
        input: Record<string, unknown>;
      };
      const task =
        body.task_type === "contract_transaction_check"
          ? aiTask(
              "task-contract-transactions",
              body.task_type,
              options.aiTransactionCheck ?? aiTransactionCheck,
              body.input,
            )
          : aiTask("task-contract-crosscheck", body.task_type, crosscheck, body.input);
      const answer = { ok: true, status: 201, json: () => Promise.resolve(task) };
      return body.task_type === "contract_duplicate_crosscheck" && crosscheckGate
        ? crosscheckGate.then(() => answer)
        : Promise.resolve(answer);
    }
    if (url.includes("/v1/ai/tasks/task-contract-")) {
      const transactionTask = url.includes("task-contract-transactions");
      const task = transactionTask
        ? aiTask(
            "task-contract-transactions",
            "contract_transaction_check",
            options.aiTransactionCheck ?? aiTransactionCheck,
            { contract_id: "contract-1" },
          )
        : aiTask("task-contract-crosscheck", "contract_duplicate_crosscheck", crosscheck);
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ...task, events: [] }),
      });
    }
    if (url.endsWith("/reassign") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            transaction_id: "txn-jan",
            previous_contract_id: null,
            target_contract_id: "contract-1",
            occurrence_changed: true,
          }),
      });
    }
    if (url.endsWith("/v1/contracts/merge") && method === "POST") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(mergeResult) });
    }
    if (url.endsWith("/undo-preview")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(undoPreview) });
    }
    if (url.endsWith("/v1/contracts/merges/merge-1/undo") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ contract: contract(), restored_source_count: 1 }),
      });
    }
    if (url.includes("/v1/review/accept") && method === "POST") {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(null) });
    }
    if (url.endsWith("/v1/categories") && method === "GET") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(categoryGroups),
      });
    }
    if (url.includes("/v1/me")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(meResponse(role)),
      });
    }
    // Die Einnahmenansicht wertet seit Stufe 6 zusaetzlich aus.
    if (url.includes("/v1/contracts/income-summary")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            generated_on: "2026-08-14",
            monthly_income_minor: 320000,
            monthly_fixed_minor: 100000,
            by_person: [],
            by_kind: [],
            trend: [],
          }),
      });
    }
    if (url.includes("/v1/people")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
    if (url.includes("/v1/contracts/summary")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(summary) });
    }
    if (url.includes("/v1/contracts/cashflow")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(cashflow) });
    }
    if (url.includes("/v1/contracts/cost-target")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(costTarget) });
    }
    if (url.includes("/v1/contracts/scenarios")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(options.scenarios ?? []),
      });
    }
    if (url.includes("/v1/contracts/forecast")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(forecastItems) });
    }
    if (url.includes("/v1/contracts/provider-suggestions")) {
      // Filtert wirklich, statt immer alles zurueckzugeben - sonst liesse
      // sich der "nichts gefunden"-Zustand nicht von einem Treffer
      // unterscheiden. Die Richtung spiegelt die echte Musterpruefung: der
      // eingetippte Text ist lang (voller Vertragsname), der Anbietername im
      // Katalog kurz und darin als Teilstring enthalten.
      const params = new URL(url, "http://localhost").searchParams;
      const q = (params.get("q") ?? "").toLowerCase();
      const items = providerSuggestions.filter((item) =>
        q.includes(item.provider_name.toLowerCase()),
      );
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(items) });
    }
    if (url.includes("/v1/contracts/settings")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ auto_end_after_missed: "3" }),
      });
    }
    if (url.includes("/v1/contracts/merge/preview")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            target_id: "contract-1",
            target_name: "Musterfunk Festnetz",
            source_names: ["Wohnungsmiete"],
            moved_occurrences: 0,
            moved_history_entries: 1,
            moved_events: 1,
            resulting_amount_minor: 3995,
            resulting_frequency: "monthly",
            amount_steps: [],
            blocking_reason: null,
          }),
      });
    }
    if (url.includes("/v1/contracts/duplicate-check")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }
    if (url.endsWith("/v1/contracts/rescan") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            contracts_examined: 3,
            matched_count: 5,
            price_change_count: 1,
            missing_count: 2,
            ended_count: 0,
          }),
      });
    }
    if (url.includes("/v1/contracts/contract-1/status") && method === "POST") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(contract({ status: "ended", version: 2 })),
      });
    }
    if (url.match(/\/v1\/contracts\/contract-\d$/) && method === "PATCH") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(contract({ name: "Musterfunk neu", version: 2 })),
      });
    }
    if (url.match(/\/v1\/contracts\/contract-\d$/) && method === "DELETE") {
      return Promise.resolve({ ok: true, status: 204, json: () => Promise.resolve(null) });
    }
    if (url.endsWith("/v1/contracts") && method === "POST") {
      return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve(contract()) });
    }
    if (url.includes("/v1/contracts?") || url.endsWith("/v1/contracts")) {
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(contracts) });
    }
    if (url.includes("/v1/transactions?") && method === "GET") {
      // Bildet den echten Server nach: search_regex laeuft durch einen
      // richtigen RegExp, ein ungueltiges Muster liefert 422 statt eines
      // Treffers - genau das Verhalten von GET /v1/transactions
      // (services/api/app/api/v1/routes/transactions.py).
      const params = new URL(url, "http://localhost").searchParams;
      const search = params.get("search");
      const searchRegex = params.get("search_regex");
      if (searchRegex !== null) {
        let pattern: RegExp;
        try {
          pattern = new RegExp(searchRegex, "i");
        } catch {
          return Promise.resolve({
            ok: false,
            status: 422,
            json: () => Promise.resolve({ detail: "invalid_regex_pattern" }),
          });
        }
        const items = lookupTransactions.filter((t) => pattern.test(t.payee_label ?? ""));
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(items) });
      }
      const needle = (search ?? "").toLowerCase();
      const items = lookupTransactions.filter((t) =>
        (t.payee_label ?? "").toLowerCase().includes(needle),
      );
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(items) });
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
            <ContractsPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

describe("ContractsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("zeigt Ausgabenverträge mit negativem Vorzeichen und blendet Einnahmen aus", async () => {
    renderContractsPage();

    expect(await screen.findByText("Musterfunk Festnetz")).toBeInTheDocument();
    expect(screen.getByText("Wohnungsmiete")).toBeInTheDocument();
    // Das Gehalt liegt im Register "Einnahmen" und darf die Fixkostenliste nicht
    // verwaessern.
    expect(screen.queryByText("Gehalt")).not.toBeInTheDocument();

    const row = screen.getByRole("row", { name: /Musterfunk Festnetz/ });
    expect(within(row).getByText("-39,95 €")).toBeInTheDocument();
    expect(within(row).getByText("-479,40 €")).toBeInTheDocument();
  });

  it("wechselt über das Register zu den Einnahmen", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Einnahmen" }));

    expect(await screen.findByText("Gehalt")).toBeInTheDocument();
    expect(screen.queryByText("Musterfunk Festnetz")).not.toBeInTheDocument();
    // Einnahmen stehen positiv da, waehrend Ausgaben negativ dargestellt werden.
    const row = screen.getByRole("row", { name: /Gehalt/ });
    expect(within(row).getByText("3.200,00 €")).toBeInTheDocument();
  });

  it("legt selten gebrauchte Zeilenaktionen hinter ein Punktmenü", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    expect(screen.queryByRole("menuitem", { name: "Löschen" })).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Weitere Aktionen für Musterfunk Festnetz" }),
    );

    expect(screen.getByRole("menuitem", { name: "Betrag korrigieren" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Als beendet markieren" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Kein Vertrag" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Löschen" })).toBeInTheDocument();
  });

  it("schickt beim Bearbeiten einen PATCH mit If-Match und positivem Betrag", async () => {
    const fetchMock = renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    const row = screen.getByRole("row", { name: /Musterfunk Festnetz/ });
    await userEvent.click(within(row).getByRole("button", { name: "Bearbeiten" }));

    const nameField = await screen.findByLabelText("Name *");
    await userEvent.clear(nameField);
    await userEvent.type(nameField, "Musterfunk neu");
    await userEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/contracts/contract-1") &&
          (init as RequestInit)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      const init = patchCall![1] as RequestInit;
      // apiFetch reicht die Header als Headers-Instanz weiter, nicht als Objekt.
      expect((init.headers as Headers).get("If-Match")).toBe('"1"');
      const body = JSON.parse(String(init.body));
      expect(body.name).toBe("Musterfunk neu");
      // Der Betrag geht immer positiv raus, die Richtung getrennt.
      expect(body.amount_minor).toBe(3995);
      expect(body.direction).toBe("expense");
    });
  });

  it("zeigt Rücklagenfelder nur für geeignete Ausgaben und aktiviert neue Jahreskosten", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");
    await userEvent.click(screen.getByRole("button", { name: "+ Vertrag" }));
    await screen.findByLabelText("Name *");

    expect(
      screen.queryByText("Fixkosten-Rücklage", { selector: "legend" }),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Rhythmus"), "yearly");

    expect(screen.getByText("Fixkosten-Rücklage", { selector: "legend" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Kosten über die Monate verteilen/)).toBeChecked();
    expect(screen.getByLabelText("Bereits für diese Zahlung zurückgelegt")).toHaveValue("0,00");
    expect(screen.getByLabelText("Rücklagenplanung ab")).toHaveValue(
      new Date().toISOString().slice(0, 10),
    );
    const preview = screen.getByLabelText("Rücklagenvorschau");
    expect(within(preview).getByText("Periodenbetrag")).toBeInTheDocument();
    expect(within(preview).getByText("Nächste Fälligkeit")).toBeInTheDocument();
    expect(within(preview).getByText("Erste Monatsrate")).toBeInTheDocument();
    expect(within(preview).getByText(/kein tatsächlicher Kontostand/i)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Richtung"), "income");
    expect(
      screen.queryByText("Fixkosten-Rücklage", { selector: "legend" }),
    ).not.toBeInTheDocument();
  });

  it("kennzeichnet aktiv geglättete Jahreskosten mit der Serverrate", async () => {
    renderContractsPage();
    const row = await screen.findByRole("row", { name: /Hausratversicherung/ });
    expect(within(row).getByText("Fixkosten-Rücklage")).toBeInTheDocument();
    expect(within(row).getByText("50,00 € / Monat")).toBeInTheDocument();
  });

  it("liefert der mobilen Vertragskarte lesbare Feldgruppen statt gequetschter Spalten", async () => {
    renderContractsPage();
    const row = await screen.findByRole("row", { name: /Hausratversicherung/ });
    expect(row).toHaveClass("contract-list-card");
    expect(row.querySelector('[data-label="Vertrag"]')).toHaveClass("contract-list-card__name");
    expect(row.querySelector('[data-label="Rhythmus"]')).toHaveClass(
      "contract-list-card__frequency",
    );
    expect(row.querySelector('[data-label="Status"]')).toHaveClass("contract-list-card__status");
    expect(row.querySelector('[data-label="Betrag"]')).toHaveTextContent("-600,00 €");
    expect(row.querySelector('[data-label="Pro Jahr"]')).toHaveTextContent("-600,00 €");
    expect(row.querySelector('[data-label="Aktionen"]')).toHaveClass("contract-list-card__actions");
  });

  it("beendet einen Vertrag über das Zeilenmenü", async () => {
    const fetchMock = renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(
      screen.getByRole("button", { name: "Weitere Aktionen für Musterfunk Festnetz" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Als beendet markieren" }));

    await waitFor(() => {
      const statusCall = fetchMock.mock.calls.find(([input]) =>
        String(input).endsWith("/v1/contracts/contract-1/status"),
      );
      expect(statusCall).toBeDefined();
      expect(JSON.parse(String((statusCall![1] as RequestInit).body))).toEqual({
        status: "ended",
      });
    });
  });

  it("bietet das Zusammenführen erst ab zwei ausgewählten Zeilen an", async () => {
    const fetchMock = renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    expect(
      screen.queryByRole("button", { name: "Zu einem Vertrag zusammenführen" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "Musterfunk Festnetz auswählen" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Wohnungsmiete auswählen" }));
    await userEvent.click(screen.getByRole("button", { name: "Zu einem Vertrag zusammenführen" }));

    await waitFor(() => {
      const previewCall = fetchMock.mock.calls.find(([input]) =>
        String(input).endsWith("/v1/contracts/merge/preview"),
      );
      expect(previewCall).toBeDefined();
      // Ziel ist der Vertrag mit der spaetesten Faelligkeit.
      expect(JSON.parse(String((previewCall![1] as RequestInit).body))).toEqual({
        target_id: "contract-1",
        source_ids: ["contract-2"],
      });
    });
    expect(await screen.findByText(/1 Preisstufe\(n\) wandern in den Verlauf/)).toBeInTheDocument();
  });

  it("zeigt eine schlüsselbewusste Duplikatkarte und öffnet daraus das Zusammenführen", async () => {
    const suggestion = {
      type: "contract_duplicate_suggestion",
      target_id: "contract-1",
      source_ids: ["contract-2"],
      payee_label: "Musterfunk Deutschland GmbH",
      frequency: "monthly",
      direction: "expense",
      older: {
        contract_id: "contract-2",
        name: "Musterfunk Festnetz alt",
        provider_label: "Musterfunk",
        amount_minor: 4995,
        status: "active",
        first_seen_date: "2025-08-01",
        last_seen_date: "2026-01-01",
      },
      newer: {
        contract_id: "contract-1",
        name: "Musterfunk Festnetz",
        provider_label: "Musterfunk",
        amount_minor: 3995,
        status: "active",
        first_seen_date: "2026-02-01",
        last_seen_date: "2026-07-01",
      },
      gap_days: 30,
      amount_delta_minor: -1000,
      evidence_strength: "strong",
      reason_codes: ["same_contract_key", "amount_transition"],
    };
    const fetchMock = renderContractsPage("owner", {
      items: [suggestion],
      total: 1,
      truncated: false,
    });
    await screen.findByText("Musterfunk Festnetz");

    expect(
      await screen.findByText("1 möglicher doppelter Vertrag / Preiswechsel"),
    ).toBeInTheDocument();
    expect(screen.getByText("starker Beleg")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Zusammenführen" }));

    await waitFor(() => {
      const previewCall = fetchMock.mock.calls.find(([input]) =>
        String(input).endsWith("/v1/contracts/merge/preview"),
      );
      expect(previewCall).toBeDefined();
      expect(JSON.parse(String((previewCall![1] as RequestInit).body))).toEqual({
        target_id: "contract-1",
        source_ids: ["contract-2"],
      });
    });
  });

  it("merkt ein 'gehören nicht zusammen' dauerhaft über den Review-Endpunkt", async () => {
    const suggestion = {
      type: "contract_duplicate_suggestion",
      target_id: "contract-1",
      source_ids: ["contract-2"],
      payee_label: "Musterfunk Deutschland GmbH",
      frequency: "monthly",
      direction: "expense",
      older: {
        contract_id: "contract-2",
        name: "Musterfunk Festnetz alt",
        provider_label: "Musterfunk",
        amount_minor: 4995,
        status: "active",
        first_seen_date: null,
        last_seen_date: "2026-01-01",
      },
      newer: {
        contract_id: "contract-1",
        name: "Musterfunk Festnetz",
        provider_label: "Musterfunk",
        amount_minor: 3995,
        status: "active",
        first_seen_date: "2026-02-01",
        last_seen_date: null,
      },
      gap_days: 30,
      amount_delta_minor: -1000,
      evidence_strength: "review",
      reason_codes: ["non_overlapping_periods"],
    };
    const fetchMock = renderContractsPage("owner", {
      items: [suggestion],
      total: 1,
      truncated: false,
    });
    await screen.findByText("Musterfunk Festnetz");
    await screen.findByText("zur Prüfung");

    await userEvent.click(screen.getByRole("button", { name: "Gehören nicht zusammen" }));

    await waitFor(() => {
      const dismissCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/review/accept") && (init as RequestInit)?.method === "POST",
      );
      expect(dismissCall).toBeDefined();
      expect(JSON.parse(String((dismissCall![1] as RequestInit).body))).toEqual({
        type: "contract_duplicate_suggestion",
        target_id: "contract-1",
        source_ids: ["contract-2"],
        dismiss: true,
      });
    });
  });

  it("stößt den Abgleich an und meldet, was er gefunden hat", async () => {
    const fetchMock = renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Jetzt abgleichen" }));

    await waitFor(() => {
      const rescanCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/contracts/rescan") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(rescanCall).toBeDefined();
    });

    // Geprueft und zugeordnet stehen immer da, die Sonderfaelle nur wenn sie
    // eingetreten sind - ended_count ist 0 und darf deshalb fehlen.
    const message = await screen.findByRole("status");
    expect(message).toHaveTextContent("3 Verträge geprüft");
    expect(message).toHaveTextContent("5 Zahlung(en) zugeordnet");
    expect(message).toHaveTextContent("1 Preisänderung(en) erkannt");
    expect(message).toHaveTextContent("2 Fälligkeit(en) ausgeblieben");
    expect(message).not.toHaveTextContent("automatisch beendet");
  });

  it("warnt in der Liste bei ausgebliebener Zahlung", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    const missedRow = screen.getByRole("row", { name: /Wohnungsmiete/ });
    expect(within(missedRow).getByText("Zahlung ausgeblieben")).toBeInTheDocument();
    // Der Status bleibt trotzdem "Aktiv": der Abgleich zaehlt nur, er entscheidet
    // nicht.
    expect(within(missedRow).getByText("Aktiv")).toBeInTheDocument();

    const payingRow = screen.getByRole("row", { name: /Musterfunk Festnetz/ });
    expect(within(payingRow).queryByText("Zahlung ausgeblieben")).not.toBeInTheDocument();
  });

  it("zeigt die Kündigungsfrist als Countdown auf den letzten Kündigungstermin", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    const row = screen.getByRole("row", { name: /Musterfunk Festnetz/ });
    // Die Kante ist der Kuendigungstermin, nicht das Laufzeitende: wer bis zum
    // 22.11. wartet, hat die Frist am 24.08. bereits verpasst.
    expect(within(row).getByText("24.08.2026")).toBeInTheDocument();
    expect(within(row).getByText("noch 21 Tage")).toBeInTheDocument();
    expect(within(row).queryByText("22.11.2026")).not.toBeInTheDocument();

    // Ohne Mindestlaufzeit gibt es nichts zu verpassen - dort waeren
    // Tageszahlen erfundene Dringlichkeit.
    const rentRow = screen.getByRole("row", { name: /Wohnungsmiete/ });
    expect(within(rentRow).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
  });

  it("listet ablaufende Kündigungsfristen als eigene Karte", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    // Eigenes Register statt einer weiteren Karte unter der Vertragsliste.
    await userEvent.click(screen.getByRole("button", { name: "Kündigungsfristen" }));

    const deadlineDate = await screen.findByText("30.09.2026");
    const card = deadlineDate.closest(".card");
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByRole("heading")).toHaveTextContent("Kündigungsfristen");
    expect(within(card as HTMLElement).getByText("noch 58 Tage")).toBeInTheDocument();
  });

  it("sortiert auf Wunsch nach Jahreskosten statt nach Fälligkeit", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    // Die Prognosekarte bringt eine zweite Tabelle mit; ohne diese Eingrenzung
    // liefe die Reihenfolgepruefung ueber beide.
    const positionOf = (name: string) => {
      const table = screen.getByRole("columnheader", { name: "Laufzeit" }).closest("table");
      expect(table).not.toBeNull();
      return within(table as HTMLElement)
        .getAllByRole("row")
        .slice(1)
        .findIndex((row) => row.textContent?.includes(name));
    };

    // Standard ist die Faelligkeit: das Abo (05.08.) steht vor der
    // Jahrespraemie (01.12.).
    expect(positionOf("Streamingabo")).toBeLessThan(positionOf("Hausratversicherung"));

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sortieren nach/ }),
      "yearly",
    );

    // Nach Jahreskosten dreht sich das um: 600 EUR im Jahr sind mehr als
    // 9,99 EUR im Monat (119,88 EUR im Jahr) - genau der Fall aus Plan I11,
    // den eine Sortierung nach Monatsbetrag falsch herum zeigt.
    expect(positionOf("Wohnungsmiete")).toBe(0);
    expect(positionOf("Hausratversicherung")).toBe(1);
    expect(positionOf("Musterfunk Festnetz")).toBe(2);
    expect(positionOf("Streamingabo")).toBe(3);
  });

  it("filtert die Vertragsliste nach Kategorie", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    // Auf die Vertragstabelle eingrenzen - "Hausratversicherung" steht sonst
    // auch in der Kuendigungsfristen-Karte, die keinem Filter unterliegt.
    const table = () => screen.getByRole("columnheader", { name: "Laufzeit" }).closest("table");
    expect(within(table() as HTMLElement).getByText("Wohnungsmiete")).toBeInTheDocument();

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Kategorie" }),
      "category-hausrat",
    );

    const filtered = within(table() as HTMLElement);
    expect(filtered.getByText("Hausratversicherung")).toBeInTheDocument();
    expect(filtered.queryByText("Musterfunk Festnetz")).not.toBeInTheDocument();
    expect(filtered.queryByText("Wohnungsmiete")).not.toBeInTheDocument();
    expect(filtered.queryByText("Streamingabo")).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Kategorie" }), "all");
    expect(within(table() as HTMLElement).getByText("Musterfunk Festnetz")).toBeInTheDocument();
  });

  it("filtert die Vertragsliste auf unkategorisierte Verträge", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    const table = () => screen.getByRole("columnheader", { name: "Laufzeit" }).closest("table");

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Kategorie" }),
      "uncategorized",
    );

    const filtered = within(table() as HTMLElement);
    expect(filtered.getByText("Musterfunk Festnetz")).toBeInTheDocument();
    expect(filtered.getByText("Streamingabo")).toBeInTheDocument();
    expect(filtered.queryByText("Wohnungsmiete")).not.toBeInTheDocument();
    expect(filtered.queryByText("Hausratversicherung")).not.toBeInTheDocument();
  });

  it("sortiert auf Wunsch nach Kategorie statt nach Fälligkeit", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    const positionOf = (name: string) => {
      const table = screen.getByRole("columnheader", { name: "Laufzeit" }).closest("table");
      expect(table).not.toBeNull();
      return within(table as HTMLElement)
        .getAllByRole("row")
        .slice(1)
        .findIndex((row) => row.textContent?.includes(name));
    };

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Sortieren nach/ }),
      "category",
    );

    // Kategorisierte Vertraege zuerst, alphabetisch nach "Gruppe · Kategorie"
    // ("Versicherungen · Hausrat" vor "Wohnen · Miete"); ohne Kategorie
    // faellt ans Ende, alphabetisch nach Name.
    expect(positionOf("Hausratversicherung")).toBe(0);
    expect(positionOf("Wohnungsmiete")).toBe(1);
    expect(positionOf("Musterfunk Festnetz")).toBe(2);
    expect(positionOf("Streamingabo")).toBe(3);
  });

  it("zeigt im Register „Nach Kategorie“ Oberpunkte je Kategorie-Gruppe mit Zwischensumme", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Nach Kategorie" }));

    const heading = await screen.findByRole("heading", { name: "Verträge nach Kategorie" });
    const card = heading.closest(".card") as HTMLElement;

    // Die Gruppen-Zwischensumme ("Wohnen") und die Kategorie darunter
    // ("Miete") stehen getrennt - "Gruppe · Kategorie" waere unter der
    // Gruppenueberschrift nur eine Wiederholung. Der Zeilenname beginnt mit
    // dem Auf-/Zuklapp-Pfeil, deshalb kein "^"-Anker vor dem Gruppennamen.
    const groupRow = within(card).getByRole("row", { name: /Wohnen/ });
    expect(within(groupRow).getByText("1.150,00 €")).toBeInTheDocument();
    // 115.000 von insgesamt 446.493 Minor-Einheiten monatlich - gerundet 26 %.
    expect(within(groupRow).getByText("26 %")).toBeInTheDocument();
    expect(within(groupRow).getByRole("button", { name: "Wohnen zuklappen" })).toBeInTheDocument();

    const categoryRow = within(card).getByRole("row", { name: /Miete/ });
    expect(within(categoryRow).getByText("1.150,00 €")).toBeInTheDocument();

    expect(within(card).getByText("Versicherungen")).toBeInTheDocument();
    expect(within(card).getByText("Hausrat")).toBeInTheDocument();
    // Musterfunk Festnetz, Gehalt und Streamingabo haben keine Kategorie und
    // buendeln sich zu einer Zeile statt zu verschwinden - gleichzeitig
    // Gruppe und einziger Eintrag, keine doppelte Zeile darunter.
    const uncategorizedRow = within(card).getByRole("row", { name: /Nicht kategorisiert/ });
    expect(within(uncategorizedRow).getByText("3")).toBeInTheDocument();
  });

  it("sortiert die Kategorie-Übersicht per Klick auf die Spaltenköpfe", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Nach Kategorie" }));
    const heading = await screen.findByRole("heading", { name: "Verträge nach Kategorie" });
    const card = heading.closest(".card") as HTMLElement;
    const rowLabelAt = (index: number) =>
      within(card).getAllByRole("row")[index]?.textContent ?? "";

    // Vorbelegung: absteigend nach Monatskosten - "Nicht kategorisiert" ist
    // trotz der hoechsten Summe die letzte Zeile (siehe Test oben), "Wohnen"
    // (1.150 €) steht vor "Versicherungen" (50 €). Kein "^"-Anker: die
    // Gruppenzeile beginnt mit dem Auf-/Zuklapp-Pfeil.
    expect(rowLabelAt(1)).toMatch(/Wohnen/);
    expect(rowLabelAt(3)).toMatch(/Versicherungen/);

    // Klick auf "Kategorie" sortiert alphabetisch (A-Z) statt nach Betrag -
    // "Nicht kategorisiert" bleibt trotzdem die letzte Zeile.
    await userEvent.click(screen.getByRole("button", { name: "Nach Kategorie sortieren" }));
    expect(rowLabelAt(1)).toMatch(/Versicherungen/);
    expect(rowLabelAt(3)).toMatch(/Wohnen/);
    expect(within(card).getAllByRole("row").at(-1)?.textContent).toMatch(/Nicht kategorisiert/);

    // Zweiter Klick auf denselben Kopf kehrt die Richtung um (Z-A).
    await userEvent.click(screen.getByRole("button", { name: "Nach Kategorie sortieren" }));
    expect(rowLabelAt(1)).toMatch(/Wohnen/);
    expect(rowLabelAt(3)).toMatch(/Versicherungen/);
  });

  it("klappt eine Kategorie-Gruppe ein und aus", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Nach Kategorie" }));
    const heading = await screen.findByRole("heading", { name: "Verträge nach Kategorie" });
    const card = heading.closest(".card") as HTMLElement;

    // "Miete" steht zunaechst offen da (Standard: aufgeklappt).
    expect(within(card).getByText("Miete")).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Wohnen zuklappen" }));
    expect(within(card).queryByText("Miete")).not.toBeInTheDocument();
    // Die Zwischensumme der eingeklappten Gruppe bleibt sichtbar - nur die
    // Kategorien darunter verschwinden.
    expect(within(card).getByText("Wohnen")).toBeInTheDocument();

    await userEvent.click(within(card).getByRole("button", { name: "Wohnen aufklappen" }));
    expect(within(card).getByText("Miete")).toBeInTheDocument();
  });

  it("zeigt die Cashflow-Prognose je Kalendermonat und kumuliert über die Perioden", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    // Eigenes Register statt einer weiteren Karte unter der Vertragsliste.
    await userEvent.click(screen.getByRole("button", { name: "Cashflow-Prognose" }));

    const augustRow = await screen.findByRole("row", { name: /August 2026/ });
    expect(within(augustRow).getByText("-89,00 €")).toBeInTheDocument();
    expect(within(augustRow).getByText("500,00 €")).toBeInTheDocument();
    // Saldo und Kumuliert sind im ersten Monat derselbe Wert.
    expect(within(augustRow).getAllByText("411,00 €")).toHaveLength(2);

    // Kumuliert wird ueber die PERIODEN. Die frueherere Variante addierte
    // Einzelfaelligkeiten quer durch den Kalender zu einer Zahl, die mit nichts
    // vergleichbar war - und zeigte deshalb bei einem Vertrag ueber 20 EUR eine
    // laufende Summe von 29,99 EUR.
    const septemberRow = screen.getByRole("row", { name: /September 2026/ });
    expect(within(septemberRow).getByText("822,00 €")).toBeInTheDocument();
  });

  it("klappt die Einzelfälligkeiten einer Periode auf", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    // Eigenes Register statt einer weiteren Karte unter der Vertragsliste.
    await userEvent.click(screen.getByRole("button", { name: "Cashflow-Prognose" }));

    // Vor dem Aufklappen steht nur die Periodenzeile da: der fruehere
    // Zeilendeckel von zwoelf Faelligkeiten liess den Rest lautlos
    // verschwinden, jetzt sind sie vollstaendig im Detail.
    const toggle = await screen.findByRole("button", { name: "August 2026" });
    expect(screen.queryByRole("row", { name: /Stromabschlag/ })).not.toBeInTheDocument();

    await userEvent.click(toggle);

    const detailRow = await screen.findByRole("row", { name: /Stromabschlag/ });
    expect(within(detailRow).getByText("05.08.2026 · Energie")).toBeInTheDocument();
  });

  it("zeigt im Register „Fixkostenziel“ die Lücke zum Ziel", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Fixkostenziel" }));

    expect(await screen.findByRole("heading", { name: "Fixkostenziel" })).toBeInTheDocument();
    expect(screen.getByText("Noch zu senken")).toBeInTheDocument();
    // 1.200 EUR heute gegen 1.000 EUR Ziel: 200 EUR im Monat, 2.400 im Jahr.
    expect(screen.getByText("200,00 €")).toBeInTheDocument();
    expect(screen.getByText("2.400,00 € im Jahr")).toBeInTheDocument();
    // Ohne Plan bleibt der Rechner leer, statt eine Ersparnis zu behaupten.
    expect(screen.getByText(/Noch kein Plan angelegt/)).toBeInTheDocument();
  });

  it("gruppiert den Senkungsplan nach Kategorie mit Zwischensummen", async () => {
    const scenario = {
      id: "scenario-1",
      name: "Sparplan",
      is_active: true,
      items: [
        { contract_id: "contract-2", action: "cancel", target_amount_minor: null, note: null },
      ],
    };
    renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      emptyCrosscheck,
      undefined,
      { scenarios: [scenario] },
    );
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Fixkostenziel" }));
    await screen.findByRole("heading", { name: "Fixkostenziel" });

    const table = () =>
      screen.getByRole("columnheader", { name: "Vorhaben" }).closest("table") as HTMLElement;
    const positionOf = (text: string) =>
      within(table())
        .getAllByRole("row")
        .findIndex((row) => row.textContent?.includes(text));

    // Gruppen stehen nach Jahreskosten geordnet (13.800 € Wohnen vor 600 €
    // Versicherungen), "Nicht kategorisiert" faellt trotz hoeherer Summe ans
    // Ende - dieselbe Regel wie bei der Kategorie-Sortierung der Vertragsliste.
    // Gruppiert wird nach der Kategorie-GRUPPE, nicht der einzelnen Kategorie
    // (dieselbe Oberpunkt-Logik wie im Register "Nach Kategorie").
    expect(positionOf("Wohnen")).toBeGreaterThanOrEqual(0);
    expect(positionOf("Wohnen")).toBeLessThan(positionOf("Versicherungen"));
    expect(positionOf("Versicherungen")).toBeLessThan(positionOf("Nicht kategorisiert"));

    // Die einzelne Kategorie ("Miete") steht als Notiz an der Vertragszeile,
    // weil eine Gruppe mehrere Kategorien buendeln kann.
    const mieteRow = within(table()).getByRole("row", { name: /Wohnungsmiete/ });
    expect(within(mieteRow).getByText("Miete")).toBeInTheDocument();

    // Das Vorhaben "Kuendigen" fuer die Miete zeigt sich als geplante
    // Ersparnis in der Gruppen-Zwischensumme, letzte Spalte ("Spart") - hier
    // zufaellig derselbe Betrag wie "Pro Monat", weil bei einer einzigen,
    // vollstaendig gekuendigten Position beide gleich hoch sind.
    const wohnenGroupRow = within(table()).getByRole("row", { name: /Wohnen/ });
    expect(wohnenGroupRow.querySelector("td:last-child")).toHaveTextContent("1.150,00 €");
    expect(
      within(wohnenGroupRow).getByRole("button", { name: "Wohnen zuklappen" }),
    ).toBeInTheDocument();
  });

  it("klappt eine Gruppe im Senkungsplan ein und aus", async () => {
    const scenario = {
      id: "scenario-1",
      name: "Sparplan",
      is_active: true,
      items: [],
    };
    renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      emptyCrosscheck,
      undefined,
      { scenarios: [scenario] },
    );
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Fixkostenziel" }));
    await screen.findByRole("heading", { name: "Fixkostenziel" });

    expect(screen.getByText("Wohnungsmiete")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Wohnen zuklappen" }));
    expect(screen.queryByText("Wohnungsmiete")).not.toBeInTheDocument();
    // Die Gruppen-Zwischensumme bleibt sichtbar, nur die Vertraege darunter
    // verschwinden.
    expect(screen.getByRole("button", { name: "Wohnen aufklappen" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Wohnen aufklappen" }));
    expect(screen.getByText("Wohnungsmiete")).toBeInTheDocument();
  });

  it("sortiert den Senkungsplan per Klick auf die Spaltenköpfe", async () => {
    const scenario = { id: "scenario-1", name: "Sparplan", is_active: true, items: [] };
    renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      emptyCrosscheck,
      undefined,
      { scenarios: [scenario] },
    );
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Fixkostenziel" }));
    await screen.findByRole("heading", { name: "Fixkostenziel" });

    const table = () =>
      screen.getByRole("columnheader", { name: "Vorhaben" }).closest("table") as HTMLElement;
    const positionOf = (text: string) =>
      within(table())
        .getAllByRole("row")
        .findIndex((row) => row.textContent?.includes(text));

    // Vorbelegung: Jahreskosten absteigend - "Wohnen" (13.800 €) vor
    // "Versicherungen" (600 €).
    expect(positionOf("Wohnen")).toBeLessThan(positionOf("Versicherungen"));

    // Klick auf "Vertrag" sortiert alphabetisch nach Gruppenname statt nach
    // Betrag - "Versicherungen" kommt vor "Wohnen".
    await userEvent.click(screen.getByRole("button", { name: "Nach Vertrag sortieren" }));
    expect(positionOf("Versicherungen")).toBeLessThan(positionOf("Wohnen"));
  });

  it("übernimmt einen Anbieter aus dem Katalog ins Anlegen-Formular", async () => {
    const fetchMock = renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "+ Vertrag" }));
    const nameField = await screen.findByLabelText("Name *");
    await userEvent.type(nameField, "MUSTERFUNK DEUTSCHLAND GMBH FESTNETZ");
    // Der Katalog wird beim Verlassen des Feldes befragt, nicht bei jedem
    // Tastendruck.
    await userEvent.tab();

    const suggestion = await screen.findByRole("button", {
      name: /Musterfunk · Telekommunikation/,
    });
    await userEvent.click(suggestion);

    expect(screen.getByLabelText("Anbieter")).toHaveValue("Musterfunk");
    // Der eingetippte Name bleibt stehen - die Vorlage benennt den Anbieter,
    // nicht den Vertrag.
    expect(nameField).toHaveValue("MUSTERFUNK DEUTSCHLAND GMBH FESTNETZ");

    // Mindestlaufzeit/Kuendigungsfrist stehen im Register "Laufzeit &
    // Kuendigung" - die Uebernahme setzt sie unabhaengig vom aktiven Register,
    // sichtbar wird das erst nach dem Wechsel dorthin.
    await userEvent.click(screen.getByRole("tab", { name: "Laufzeit & Kündigung" }));
    expect(screen.getByLabelText("Mindestlaufzeit (Monate)")).toHaveValue(24);
    expect(screen.getByLabelText("Kündigungsfrist (Tage)")).toHaveValue(90);
    await userEvent.click(screen.getByRole("tab", { name: "Basis" }));

    await userEvent.type(screen.getByLabelText("Betrag (€) *"), "39,95");
    await userEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/contracts") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall![1] as RequestInit).body));
      expect(body.provider_template_key).toBe("musterfunk");
      expect(body.contract_type).toBe("telecom");
      expect(body.cancellation_period_days).toBe(90);
      expect(body.min_term_months).toBe(24);
    });
  });

  it("meldet, wenn die Anbietersuche keinen Treffer findet", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "+ Vertrag" }));
    const nameField = await screen.findByLabelText("Name *");
    await userEvent.type(nameField, "Musterassekuranz Hausratversicherung");
    await userEvent.tab();

    // Vorher blieb eine Nichttreffer-Suche unsichtbar - nicht von "es gibt
    // keine Suche" zu unterscheiden. Jetzt meldet sich die Box auch leer.
    expect(await screen.findByRole("heading", { name: "Anbietersuche" })).toBeInTheDocument();
    expect(screen.getByText(/Kein bekannter Anbieter gefunden/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Musterfunk · Telekommunikation/ }),
    ).not.toBeInTheDocument();
  });

  it("findet den Betrag per Volltextsuche in eigenen Buchungen und übernimmt ihn", async () => {
    const fetchMock = renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "+ Vertrag" }));
    await screen.findByLabelText("Name *");

    await userEvent.type(screen.getByLabelText("Text in Empfänger oder Zweck"), "Musterassekuranz");
    await userEvent.click(screen.getByRole("button", { name: "Suchen" }));

    const hit = await screen.findByText("Musterassekuranz Versicherung AG");
    const row = hit.closest("li") as HTMLElement;
    expect(within(row).getByText("-34,99 €")).toBeInTheDocument();

    await userEvent.click(within(row).getByRole("button", { name: "Betrag übernehmen" }));
    expect(screen.getByLabelText("Betrag (€) *")).toHaveValue("34,99");

    const lookupCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/v1/transactions?"),
    );
    expect(lookupCall).toBeDefined();
    expect(String(lookupCall![0])).toContain("search=Musterassekuranz");
  });

  it("sucht regex-fähig und meldet ein ungültiges Muster statt eines Treffers", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "+ Vertrag" }));
    await screen.findByLabelText("Name *");

    await userEvent.click(screen.getByLabelText("Regulärer Ausdruck (Regex)"));
    await userEvent.type(
      screen.getByLabelText("Text in Empfänger oder Zweck"),
      "mustera.?ssekuranz",
    );
    await userEvent.click(screen.getByRole("button", { name: "Suchen" }));

    expect(await screen.findByText("Musterassekuranz Versicherung AG")).toBeInTheDocument();

    // Ein ungueltiges Muster meldet sich verstaendlich, statt abzustuerzen
    // oder stillschweigend nichts zu finden (die eigentliche Absicherung
    // liegt im Server, siehe test_transactions_phase2.py).
    await userEvent.clear(screen.getByLabelText("Text in Empfänger oder Zweck"));
    await userEvent.type(screen.getByLabelText("Text in Empfänger oder Zweck"), "(unclosed");
    await userEvent.click(screen.getByRole("button", { name: "Suchen" }));

    expect(await screen.findByText("Ungültiger regulärer Ausdruck.")).toBeInTheDocument();
  });

  it("springt nach dem Anlegen direkt zum Betragsverlauf mit geöffneter Buchungsauswahl", async () => {
    renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "+ Vertrag" }));
    const nameField = await screen.findByLabelText("Name *");
    await userEvent.type(nameField, "Musterassekuranz Hausratversicherung");
    await userEvent.type(screen.getByLabelText("Betrag (€) *"), "12,34");
    await userEvent.click(screen.getByRole("button", { name: "Speichern" }));

    // Der Anlegen-Dialog schliesst sich, und statt auf die Liste zurueck geht
    // es direkt in den Betragsverlauf des frisch erzeugten Vertrags - mit
    // bereits aufgeklapptem "Fehlende Buchungen nachtragen": genau der
    // Schritt, der ohne bekannten Betrag als naechstes ansteht, statt den
    // Betrag zu raten.
    expect(
      await screen.findByRole("heading", { name: "Fehlende Buchungen nachtragen" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Neuer Vertrag" })).not.toBeInTheDocument();
  });

  it("zeigt einem Viewer keine Bearbeitungsaktionen", async () => {
    renderContractsPage("viewer");
    await screen.findByText("Musterfunk Festnetz");

    expect(screen.queryByRole("button", { name: "+ Vertrag" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Jetzt abgleichen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Verträge gegenprüfen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Gegenpruefung durch das Sprachmodell (Plan 19.11)
  // -------------------------------------------------------------------------

  it("stößt die Gegenprüfung an, auch wenn die deterministische Prüfung nichts gefunden hat", async () => {
    // Der Anlassfall: ein Haushalt hat drei Musterfunk-Zeilen, aber ohne
    // contract_key und ohne Zeiträume findet die deterministische Abfrage
    // nichts - der Knopf muss trotzdem da sein.
    const fetchMock = renderContractsPage();
    await screen.findByText("Musterfunk Festnetz");

    expect(await screen.findByText("Doppelte Verträge und Preiswechsel")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/ai/tasks") && (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      // Ohne Einschraenkung: der Lauf betrachtet alle Paare des Haushalts.
      expect(JSON.parse(String((call![1] as RequestInit).body))).toMatchObject({
        task_type: "contract_duplicate_crosscheck",
        input: {},
      });
    });
  });

  it("zeigt während der Gegenprüfung einen Ladezustand", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderContractsPage("owner", { items: [], total: 0, truncated: false }, emptyCrosscheck, gate);
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    // Das lokale Modell braucht spuerbar Zeit - ohne sichtbaren Ladezustand
    // wirkt der Knopf wie ohne Funktion und wird ein zweites Mal gedrueckt.
    const busy = await screen.findByRole("button", { name: "Wird eingereiht …" });
    expect(busy).toBeDisabled();
    expect(screen.getByText(/Vertragsprüfung wartet in der KI-Warteschlange/)).toBeInTheDocument();

    release();
    expect(await screen.findByRole("button", { name: "Verträge gegenprüfen" })).toBeInTheDocument();
  });

  it("zeigt KI-Vorschläge mit Begründung in der bestehenden Duplikatkarte", async () => {
    renderContractsPage("owner", { items: [], total: 0, truncated: false }, aiCrosscheck);
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    const card = (
      await screen.findByRole("heading", { name: "Gegenprüfung des Sprachmodells" })
    ).closest(".card") as HTMLElement;
    expect(card).not.toBeNull();
    // Herkunft und Klartextbegruendung stehen zusammen: ein Modellvorschlag
    // darf nie wie ein deterministischer Beleg aussehen (Leitsatz 8).
    expect(within(card).getByText("Vorschlag des Sprachmodells")).toBeInTheDocument();
    expect(within(card).getByText("gehört vermutlich zusammen")).toBeInTheDocument();
    expect(
      within(card).getByText(/Beide Zahlungen nennen dieselbe Kundenkontonummer/),
    ).toBeInTheDocument();
    // Keine erfundene Prozent-Konfidenz - weder im Antwortschema noch hier.
    expect(card.textContent).not.toMatch(/%/);
  });

  it("führt einen KI-Vorschlag über die vorhandene Merge-Vorschau zusammen", async () => {
    const fetchMock = renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      aiCrosscheck,
    );
    await screen.findByText("Musterfunk Festnetz");
    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    const card = (
      await screen.findByRole("heading", { name: "Gegenprüfung des Sprachmodells" })
    ).closest(".card") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Zusammenführen" }));

    // Kein zweiter Zusammenfuehrungsmechanismus: derselbe Vorschau-Endpunkt
    // und dieselbe Zielregel wie bei der Auswahl in der Liste (spaeteste
    // Faelligkeit gewinnt, hier contract-1 am 20.08. vor contract-2 am 02.08.).
    await waitFor(() => {
      const previewCall = fetchMock.mock.calls.find(([input]) =>
        String(input).endsWith("/v1/contracts/merge/preview"),
      );
      expect(previewCall).toBeDefined();
      expect(JSON.parse(String((previewCall![1] as RequestInit).body))).toEqual({
        target_id: "contract-1",
        source_ids: ["contract-2"],
      });
    });
  });

  it("verneint einen KI-Vorschlag über den vorhandenen Verwerfen-Weg", async () => {
    const fetchMock = renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      aiCrosscheck,
    );
    await screen.findByText("Musterfunk Festnetz");
    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    const card = (
      await screen.findByRole("heading", { name: "Gegenprüfung des Sprachmodells" })
    ).closest(".card") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Gehören nicht zusammen" }));

    await waitFor(() => {
      const dismissCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/review/accept") && (init as RequestInit)?.method === "POST",
      );
      expect(dismissCall).toBeDefined();
      expect(JSON.parse(String((dismissCall![1] as RequestInit).body))).toEqual({
        type: "contract_duplicate_suggestion",
        target_id: "contract-1",
        source_ids: ["contract-2"],
        dismiss: true,
      });
    });
    // Das verneinte Paar verschwindet sofort - es kann sich nicht selbst
    // erneuern, weil es aus einer Mutation und nicht aus einer Abfrage kommt.
    await waitFor(() =>
      expect(
        screen.queryByText(/Beide Zahlungen nennen dieselbe Kundenkontonummer/),
      ).not.toBeInTheDocument(),
    );
  });

  it("meldet einen abgeschnittenen Lauf, ohne das interne Limit als Gesamtzahl zu nennen", async () => {
    renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      { ...aiCrosscheck, truncated: true },
    );
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    const summaryLine = await screen.findByText(
      /Es gibt weitere Paare, die dieser Lauf noch nicht/,
    );
    expect(summaryLine).toHaveTextContent("Gegenprüfung: 1 Paar(e) beurteilt");
    expect(summaryLine).toHaveTextContent("1 übersprungen");
    // MAX_PAIRS ist eine interne Obergrenze und darf nirgends als Menge
    // erscheinen - gleiche Regel wie bei /duplicate-suggestions.
    expect(summaryLine.textContent).not.toMatch(/\b12\b/);
  });

  it("meldet ein nicht erreichbares Sprachmodell ruhig statt als Absturz", async () => {
    renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      {
        ...emptyCrosscheck,
        meldung:
          "Die Gegenprüfung braucht das lokale Sprachmodell, und das ist gerade nicht erreichbar. Bitte LM Studio starten. An den Verträgen wurde nichts verändert.",
      },
    );
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    expect(
      await screen.findByText(/das ist gerade nicht erreichbar/, { exact: false }),
    ).toBeInTheDocument();
    // `meldung` ist der Fehlerkanal, nicht der HTTP-Status: keine rote
    // Fehlerbox, kein role="alert".
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Der Knopf bleibt bedienbar, sobald LM Studio laeuft.
    expect(screen.getByRole("button", { name: "Verträge gegenprüfen" })).toBeEnabled();
  });

  it("bietet bei einem Teilergebnis die Aktion zum Grund des KI-Kerns an", async () => {
    renderContractsPage(
      "owner",
      { items: [], total: 0, truncated: false },
      {
        ...emptyCrosscheck,
        meldung:
          "Für 2 von 3 Vertragspaaren kam vom Sprachmodell keine verwertbare Antwort (model_timeout ×2). Der Modellaufruf hat das Zeitbudget überschritten. 1 Vertragspaare beurteilt. An den Verträgen wurde nichts verändert.",
      },
    );
    await screen.findByText("Musterfunk Festnetz");

    await userEvent.click(screen.getByRole("button", { name: "Verträge gegenprüfen" }));

    expect(
      await screen.findByText(/Für 2 von 3 Vertragspaaren kam vom Sprachmodell/),
    ).toHaveTextContent("Der Modellaufruf hat das Zeitbudget überschritten.");
    expect(
      screen.getByRole("link", { name: "Zeitbudget unter Einstellungen › KI anpassen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Rueckgaengig machen und Query-Invalidierung (Plan 19.7/19.10/19.12)
  // -------------------------------------------------------------------------

  it("bietet unmittelbar nach dem Zusammenführen das Rückgängigmachen an", async () => {
    const fetchMock = renderContractsPage("owner", {
      items: [duplicateSuggestion()],
      total: 1,
      truncated: false,
    });
    await screen.findByText("starker Beleg");

    await userEvent.click(screen.getByRole("button", { name: "Zusammenführen" }));
    const previewDialog = await screen.findByRole("dialog", { name: "Verträge zusammenführen" });
    await within(previewDialog).findByText(/1 Preisstufe\(n\) wandern in den Verlauf/);
    await userEvent.click(within(previewDialog).getByRole("button", { name: "Zusammenführen" }));

    // Bis Phase 5 schloss sich der Dialog hier und "Rückgängig" stand nur tief
    // im Ereignisverlauf - Akzeptanzkriterium 19.12 damit nicht erfüllt.
    const doneDialog = await screen.findByRole("dialog", { name: "Zusammengeführt" });
    expect(
      within(doneDialog).getByRole("button", { name: "Zusammenführung rückgängig machen" }),
    ).toBeInTheDocument();
    // Der abgeleitete Vertragsschlüssel ist der Beleg dafür, dass der Abgleich
    // die Zeilen künftig als einen Vertrag erkennt.
    expect(
      within(doneDialog).getByText(/Vertragsschlüssel übernommen \(aus der Mandatsreferenz\)/),
    ).toBeInTheDocument();

    await userEvent.click(
      within(doneDialog).getByRole("button", { name: "Zusammenführung rückgängig machen" }),
    );

    await waitFor(() => {
      const undoCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/v1/contracts/merges/merge-1/undo") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(undoCall).toBeDefined();
    });
    expect(
      await screen.findByText(/Die Quellverträge stehen mit ihren Beträgen/),
    ).toBeInTheDocument();
  });

  it("invalidiert nach dem Zusammenführen auch die Vorschlags-Query", async () => {
    const fetchMock = renderContractsPage("owner", {
      items: [duplicateSuggestion()],
      total: 1,
      truncated: false,
    });
    await screen.findByText("starker Beleg");

    const suggestionCalls = () =>
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/v1/contracts/duplicate-suggestions"),
      ).length;
    const before = suggestionCalls();
    expect(before).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "Zusammenführen" }));
    const previewDialog = await screen.findByRole("dialog", { name: "Verträge zusammenführen" });
    await within(previewDialog).findByText(/1 Preisstufe\(n\) wandern in den Verlauf/);
    await userEvent.click(within(previewDialog).getByRole("button", { name: "Zusammenführen" }));

    // Die Lücke aus 19.14 Punkt 9: ohne diese Invalidierung stand die
    // Duplikatkarte nach dem Merge weiter mit dem inzwischen ausgeblendeten
    // Quellvertrag da und bot denselben Merge ein zweites Mal an.
    await waitFor(() => expect(suggestionCalls()).toBeGreaterThan(before));
  });

  it("invalidiert nach dem Rückgängigmachen auch die Vorschlags-Query", async () => {
    const fetchMock = renderContractsPage("owner", {
      items: [duplicateSuggestion()],
      total: 1,
      truncated: false,
    });
    await screen.findByText("starker Beleg");

    const suggestionCalls = () =>
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/v1/contracts/duplicate-suggestions"),
      ).length;

    await userEvent.click(screen.getByRole("button", { name: "Zusammenführen" }));
    const previewDialog = await screen.findByRole("dialog", { name: "Verträge zusammenführen" });
    await within(previewDialog).findByText(/1 Preisstufe\(n\) wandern in den Verlauf/);
    await userEvent.click(within(previewDialog).getByRole("button", { name: "Zusammenführen" }));

    const doneDialog = await screen.findByRole("dialog", { name: "Zusammengeführt" });
    await waitFor(() => expect(suggestionCalls()).toBeGreaterThan(0));
    // Zähler zurücksetzen, damit die nächste Abfrage eindeutig zum Undo gehört
    // und nicht noch die Nachwehen der Merge-Invalidierung mitzählt.
    fetchMock.mockClear();

    await userEvent.click(
      within(doneDialog).getByRole("button", { name: "Zusammenführung rückgängig machen" }),
    );

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/v1/contracts/merges/merge-1/undo") &&
            (init as RequestInit)?.method === "POST",
        ),
      ).toBe(true),
    );
    // Nach dem Undo stehen die Quellverträge wieder einzeln da - der
    // Vorschlag ist damit wieder gültig und muss neu geladen werden.
    await waitFor(() => expect(suggestionCalls()).toBeGreaterThan(0));
  });

  it("lädt nach dem Verwerfen eines Vorschlags Verträge und Vorschläge neu", async () => {
    const fetchMock = renderContractsPage("owner", {
      items: [duplicateSuggestion({ evidence_strength: "review" })],
      total: 1,
      truncated: false,
    });
    await screen.findByText("zur Prüfung");

    const suggestionCalls = () =>
      fetchMock.mock.calls.filter(([input]) =>
        String(input).includes("/v1/contracts/duplicate-suggestions"),
      ).length;
    // Die Vertragsliste wird ohne Filter ohne Query-String geladen, die
    // vollstaendige Liste der Karte mit `?include_ended=true` - beide zaehlen.
    const contractListCalls = () =>
      fetchMock.mock.calls.filter(([input]) => /\/v1\/contracts(\?|$)/.test(String(input))).length;
    const beforeSuggestions = suggestionCalls();
    const beforeContracts = contractListCalls();

    await userEvent.click(screen.getByRole("button", { name: "Gehören nicht zusammen" }));

    // Ein Dismissal schreibt ein Vertragsereignis und ändert damit auch, was
    // die Gegenprüfung noch fragen darf - beide Seiten müssen neu laden.
    await waitFor(() => {
      expect(suggestionCalls()).toBeGreaterThan(beforeSuggestions);
      expect(contractListCalls()).toBeGreaterThan(beforeContracts);
    });
  });
});
