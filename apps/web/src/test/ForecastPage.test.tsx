/**
 * Liquiditaetsvorschau.
 *
 * Geprueft wird gegen den Tabellen-Fallback und die textlichen Befunde, nicht
 * gegen das Canvas: in jsdom liefert `detectCanvasSupport()` false, jedes
 * Diagramm faellt auf seine Tabelle zurueck (siehe Chart.tsx). Das ist genau
 * die Oberflaeche, die hier geprueft werden soll.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthProvider";
import { addMonths, lastOfMonth, nextMonthValue } from "../components/forecast/changeDrafts";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { ForecastPage } from "../pages/ForecastPage";

/** Dieselbe Grundlage wie in der Seite: der Monat nach dem heutigen. */
const startMonth = nextMonthValue(new Date());

const session = {
  access_token: "test-token",
  user: { id: "user-1", email: "test@finanzplaner.test" },
};

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session } })),
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
  id: "user-1",
  email: "test@finanzplaner.test",
  display_name: "Test",
  date_format: "de",
  households: [{ household_id: "hh-1", name: "Musterhaushalt", role: "owner" }],
};

/** 90 Tage ab dem 13.08.2026, der Engpass liegt bewusst im Oktober. */
function buildDaily() {
  const days = [];
  const start = new Date(2026, 7, 13);
  for (let i = 0; i < 90; i += 1) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const isCause = iso === "2026-10-01";
    days.push({
      on: iso,
      opening_balance_minor: isCause ? 288430 : 120000 - i * 500 + 5032,
      variable_minor: 5032,
      // 288430 - 150000 (Miete + KFZ) - 5032 (variabel) = 133398
      balance_minor: iso === "2026-10-27" ? -28243 : isCause ? 133398 : 120000 - i * 500,
      is_event: isCause,
      flows: isCause
        ? [
            { label: "Miete", amount_minor: -100000, source_kind: "contract", source_id: "c1" },
            {
              label: "KFZ-Versicherung",
              amount_minor: -50000,
              source_kind: "contract",
              source_id: "c2",
            },
          ]
        : [],
    });
  }
  return days;
}

const forecastResponse = {
  as_of: "2026-08-13",
  horizon_months: 24,
  granularity: "day",
  opening_liquidity_minor: 110000,
  bound_reserve_minor: 34000,
  safety_buffer_minor: 50000,
  daily: buildDaily(),
  monthly: [
    {
      year: 2026,
      month: 8,
      // 300000 - 30008 - 94753 = 175239; 110000 (Startpunkt) + 175239 = 285239
      income_minor: 300000,
      fixed_minor: 30008,
      variable_minor: 94753,
      net_minor: 175239,
      closing_balance_minor: 285239,
    },
    {
      year: 2026,
      month: 10,
      // 351000 - 231809 - 155992 = -36801
      income_minor: 351000,
      fixed_minor: 231809,
      variable_minor: 155992,
      net_minor: -36801,
      closing_balance_minor: -28243,
    },
  ],
  metrics: {
    lowest_balance_minor: -28243,
    lowest_balance_on: "2026-10-27",
    first_negative_on: "2026-10-22",
    first_below_buffer_on: "2026-08-19",
    recovery_on: "2026-10-28",
    days_below_zero: 9,
    days_below_buffer: 71,
    needed_against_negative_minor: 28243,
    needed_for_buffer_minor: 78243,
    // 351000 - 150000 - 156000 = 45000 Ueberschuss; 45000/351000 = 12,8 %;
    // 150000/351000 = 42,7 % Fixkostenquote; 110000/150000 = 0,73 Monate.
    average_monthly_surplus_minor: 45000,
    income_monthly_minor: 351000,
    fixed_monthly_minor: 150000,
    variable_monthly_minor: 156000,
    savings_rate_percent: 12.8,
    fixed_cost_ratio_percent: 42.7,
    reserve_without_income_months: 0.73,
    balance_in_3_months_minor: 92720,
    balance_in_6_months_minor: 159119,
    balance_in_12_months_minor: 285183,
    balance_in_24_months_minor: 454476,
  },
  cause: {
    on: "2026-10-01",
    // -100000 + -50000 = -150000
    total_minor: -150000,
    flows: [
      { label: "Miete", amount_minor: -100000, source_kind: "contract", source_id: "c1" },
      { label: "KFZ-Versicherung", amount_minor: -50000, source_kind: "contract", source_id: "c2" },
    ],
  },
  warnings: [
    { code: "below_zero", level: "critical", context: {} },
    { code: "below_buffer", level: "watch", context: {} },
    { code: "variable_from_history", level: "info", context: {} },
  ],
  quality: {
    level: "limited",
    last_booking_on: "2026-08-12",
    contract_count: 11,
    income_count: 2,
    variable_basis: "history",
    excluded_account_names: ["Tagesgeld"],
    contracts_ending_in_horizon: 0,
  },
};

const upcomingResponse = [
  {
    on: "2026-10-01",
    // -100000 + -50000 = -150000
    total_minor: -150000,
    balance_after_minor: 160098,
    is_heavy: true,
    flows: [
      { label: "Miete", amount_minor: -100000, source_kind: "contract", source_id: "c1" },
      { label: "KFZ-Versicherung", amount_minor: -50000, source_kind: "contract", source_id: "c2" },
    ],
  },
  {
    on: "2026-10-28",
    total_minor: 300000,
    balance_after_minor: 269757,
    is_heavy: false,
    flows: [{ label: "Gehalt", amount_minor: 300000, source_kind: "contract", source_id: "c3" }],
  },
];

const simulationResponse = {
  base: forecastResponse,
  simulation: {
    ...forecastResponse,
    metrics: {
      ...forecastResponse.metrics,
      lowest_balance_minor: 5187,
      lowest_balance_on: "2026-08-27",
      first_negative_on: null,
      // 45000 + 137000 = 182000; 182000/488000 = 37,3 %; 150000/488000 = 30,7 %.
      average_monthly_surplus_minor: 182000,
      savings_rate_percent: 37.3,
      fixed_cost_ratio_percent: 30.7,
      reserve_without_income_months: 0.64,
      balance_in_24_months_minor: 3460476,
    },
    warnings: [{ code: "simulation_tax_class_hint", level: "watch", context: {} }],
  },
  comparison: {
    surplus_delta_minor: 137000,
    // 37,3 - 12,8 = 24,5; 30,7 - 42,7 = -12,0
    savings_rate_delta_percent: 24.5,
    fixed_cost_ratio_delta_percent: -12.0,
    reserve_without_income_delta_months: -0.09,
    balance_delta_3_months_minor: 40000,
    balance_delta_6_months_minor: 90000,
    balance_delta_12_months_minor: 1360000,
    balance_delta_24_months_minor: 3006000,
    first_negative_shift_days: null,
    resolves_shortfall: true,
  },
  contributions: [
    {
      change_index: 0,
      monthly_minor: 20000,
      resolves_shortfall: false,
      shift_days: 4,
      first_negative_on: "2026-10-26",
    },
  ],
};

const settingsResponse = {
  safety_buffer_minor: 50000,
  variable_basis: "max",
  variable_lookback_months: 6,
  horizon_months: 24,
  updated_at: "2026-08-01T10:00:00Z",
};

const accountsResponse = [
  {
    id: "acc-1",
    name: "Girokonto",
    account_type: "checking",
    institution: null,
    currency: "EUR",
    on_budget: true,
    opening_balance_minor: 92000,
    opening_balance_date: null,
    is_closed: false,
    forecast_liquidity: null,
  },
  {
    id: "acc-2",
    name: "Tagesgeld",
    account_type: "savings",
    institution: null,
    currency: "EUR",
    on_budget: false,
    opening_balance_minor: 840000,
    opening_balance_date: null,
    is_closed: false,
    forecast_liquidity: false,
  },
];

const contractsResponse = [
  {
    id: "c1",
    name: "Miete",
    direction: "expense",
    amount_minor: 100000,
    frequency: "monthly",
    next_due_date: "2026-09-01",
  },
  {
    id: "c3",
    name: "Gehalt Person A",
    direction: "income",
    amount_minor: 300000,
    frequency: "monthly",
    next_due_date: "2026-08-28",
  },
];

interface RequestRecord {
  url: string;
  init?: RequestInit;
}

function stubFetch(
  options: {
    settingsUpdatedAt?: string | null;
    simulateError?: { status: number; body: unknown };
  } = {},
): RequestRecord[] {
  const requests: RequestRecord[] = [];
  const settings = {
    ...settingsResponse,
    updated_at:
      options.settingsUpdatedAt === undefined
        ? settingsResponse.updated_at
        : options.settingsUpdatedAt,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      requests.push({ url, init });
      const ok = (body: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
      if (/\/v1\/me(?:\?|$)/.test(url)) return ok(meResponse);
      if (url.includes("/v1/forecast/upcoming")) return ok(upcomingResponse);
      if (url.includes("/v1/forecast/simulate")) {
        if (options.simulateError) {
          return Promise.resolve({
            ok: false,
            status: options.simulateError.status,
            json: () => Promise.resolve(options.simulateError?.body),
          });
        }
        return ok(simulationResponse);
      }
      if (url.includes("/v1/forecast/settings")) return ok(settings);
      if (url.includes("/v1/forecast")) return ok(forecastResponse);
      if (url.includes("/v1/accounts")) return ok(accountsResponse);
      if (url.includes("/v1/contracts")) return ok(contractsResponse);
      return ok([]);
    }),
  );
  return requests;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/vorschau"]}>
        <AuthProvider>
          <HouseholdProvider>
            <ForecastPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ForecastPage", () => {
  it("nennt Datum, Fehlbetraege und Ursachenkette des Engpasses", async () => {
    stubFetch();
    renderPage();

    const verdict = await screen.findByTestId("forecast-verdict");
    expect(within(verdict).getByText(/22\.10\.2026/)).toBeInTheDocument();

    // Die beiden Betraege, die der Nutzer sonst selbst rechnen muesste.
    expect(within(verdict).getByText("Nötig gegen das Minus")).toBeInTheDocument();
    expect(within(verdict).getByText("282,43 €")).toBeInTheDocument();
    expect(within(verdict).getByText("782,43 €")).toBeInTheDocument();

    // Ursachenkette: Ausloeser am 01.10., Wirkung am 22.10.
    expect(within(verdict).getByText("Miete + KFZ-Versicherung")).toBeInTheDocument();
    expect(within(verdict).getByText("01.10.")).toBeInTheDocument();
    expect(within(verdict).getByText("Konto unter null")).toBeInTheDocument();
  });

  it("zeigt die Datenqualitaet mit ihren Belegen", async () => {
    stubFetch();
    renderPage();

    const quality = await screen.findByTestId("forecast-quality");
    expect(within(quality).getByText(/eingeschränkt/)).toBeInTheDocument();
    expect(within(quality).getByText(/11 Verträge/)).toBeInTheDocument();
    expect(within(quality).getByText(/Tagesgeld nicht einbezogen/)).toBeInTheDocument();
  });

  it("gruppiert die naechsten Zahlungen nach Tagen und markiert schwere Tage", async () => {
    stubFetch();
    renderPage();

    const upcoming = await screen.findByTestId("forecast-upcoming");
    // Tagessumme, laufender Stand danach und die Markierung des schweren Tages.
    expect(within(upcoming).getByText("-1.500,00 €")).toBeInTheDocument();
    expect(within(upcoming).getAllByText("Danach verfügbar")).toHaveLength(2);
    expect(within(upcoming).getByText("1.600,98 €")).toBeInTheDocument();
    expect(within(upcoming).getByText(/▲ 01\.10\./)).toBeInTheDocument();
  });

  it("legt aus einer Vorlage Zeilen an und schickt sie an die Simulation", async () => {
    const requests = stubFetch();
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("forecast-verdict");
    await user.click(screen.getByRole("tab", { name: "Was wäre wenn" }));

    // Ohne Zeile passiert nichts - der Leerzustand ist ein eigener Entwurf.
    expect(screen.getByText("Keine Änderung aktiv.")).toBeInTheDocument();
    expect(screen.getByText(/Noch keine Änderung/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Vorlage wählen" }));
    await user.click(screen.getByRole("button", { name: /Gehaltserhöhung/ }));

    // Die Zeile braucht ein Ziel, bevor sie gerechnet werden darf.
    expect(await screen.findByText("Ziel auswählen.")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Ziel"), "c3");

    await waitFor(() => {
      expect(requests.some((r) => r.url.includes("/v1/forecast/simulate"))).toBe(true);
    });

    const request = requests.find((r) => r.url.includes("/v1/forecast/simulate"));
    const body = JSON.parse(String(request?.init?.body)) as {
      changes: {
        change_type: string;
        amount_mode: string;
        amount_minor: number;
        target_contract_id: string;
      }[];
    };
    expect(body.changes).toHaveLength(1);
    const change = body.changes[0];
    expect(change?.change_type).toBe("income_change");
    expect(change?.amount_mode).toBe("delta");
    expect(change?.amount_minor).toBe(20000);
    expect(change?.target_contract_id).toBe("c3");

    // Beitrag der einzelnen Aenderung, aus dem eigenen Rechenlauf des Servers.
    expect(await screen.findByText(/Verschiebt den Engpass um 4 Tage/)).toBeInTheDocument();
    expect(await screen.findByTestId("forecast-waterfall")).toBeInTheDocument();
  });

  it("setzt den Beginn über zwei Auswahlfelder und schickt den Monatsersten", async () => {
    const requests = stubFetch();
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("forecast-verdict");
    await user.click(screen.getByRole("tab", { name: "Was wäre wenn" }));
    await user.click(screen.getByRole("button", { name: "Änderung hinzufügen" }));

    // Kein Textfeld mehr: Firefox hat <input type="month"> als freies Feld
    // gerendert, in dem Werte wie "-012-01" entstehen konnten.
    const month = screen.getByRole("combobox", { name: "Beginn Neue Ausgabe Monat" });
    expect(within(month).getAllByRole("option")).toHaveLength(12);
    expect(within(month).getByRole("option", { name: "Dezember" })).toBeInTheDocument();

    await user.selectOptions(month, "12");

    await waitFor(() => {
      const last = [...requests].reverse().find((r) => r.url.includes("/v1/forecast/simulate"));
      const body = JSON.parse(String(last?.init?.body)) as { changes: { starts_on: string }[] };
      expect(body.changes[0]?.starts_on).toBe(`${startMonth.slice(0, 4)}-12-01`);
    });
  });

  it("schickt als Ende den letzten Tag des Monats", async () => {
    const requests = stubFetch();
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("forecast-verdict");
    await user.click(screen.getByRole("tab", { name: "Was wäre wenn" }));
    await user.click(screen.getByRole("button", { name: "Änderung hinzufügen" }));
    await user.click(screen.getByRole("button", { name: "Enddatum festlegen" }));

    // Ein Monatserster wuerde die Zahlung des gewaehlten Monats verschlucken:
    // der Rechenkern bricht bei "cursor > ends_on" ab.
    await waitFor(() => {
      const last = [...requests].reverse().find((r) => r.url.includes("/v1/forecast/simulate"));
      const body = JSON.parse(String(last?.init?.body)) as { changes: { ends_on: string }[] };
      expect(body.changes[0]?.ends_on).toBe(lastOfMonth(addMonths(startMonth, 12)));
    });
  });

  it("zeigt bei einem Serverfehler genau eine Meldung mit der Begründung des Servers", async () => {
    stubFetch({
      simulateError: {
        status: 422,
        body: { detail: [{ msg: "ends_on muss >= starts_on sein" }] },
      },
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("forecast-verdict");
    await user.click(screen.getByRole("tab", { name: "Was wäre wenn" }));
    await user.click(screen.getByRole("button", { name: "Änderung hinzufügen" }));

    expect(await screen.findByText("ends_on muss >= starts_on sein")).toBeInTheDocument();
    // Genau das war die Beschwerde: beide Meldungen standen gleichzeitig da.
    expect(screen.queryByText("Keine Änderung aktiv.")).toBeNull();
    expect(screen.queryByText("Simulation fehlgeschlagen.")).toBeNull();
    expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
  });

  it("rechnet nach einem geleerten Betragsfeld nicht mit dem alten Betrag weiter", async () => {
    const requests = stubFetch();
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("forecast-verdict");
    await user.click(screen.getByRole("tab", { name: "Was wäre wenn" }));
    await user.click(screen.getByRole("button", { name: "Änderung hinzufügen" }));
    await screen.findByTestId("forecast-waterfall");

    const before = requests.filter((r) => r.url.includes("/v1/forecast/simulate")).length;
    await user.clear(screen.getByLabelText("Betrag Neue Ausgabe"));

    // Die Zeile faellt still aus der Rechnung - kein Fehler, kein Serverlauf.
    expect(await screen.findByText("Betrag fehlt.")).toBeInTheDocument();
    expect(
      screen.getByText("Eine Änderung ist noch unvollständig — sie wird nicht mitgerechnet."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(requests.filter((r) => r.url.includes("/v1/forecast/simulate")).length).toBe(before);
  });

  it("bietet den Fälligkeitstag als Auswahl an, damit er nicht leer sein kann", async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("forecast-verdict");
    await user.click(screen.getByRole("tab", { name: "Was wäre wenn" }));
    await user.click(screen.getByRole("button", { name: "Änderung hinzufügen" }));

    const dueDay = screen.getByRole("combobox", { name: "Fälligkeitstag Neue Ausgabe" });
    const options = within(dueDay).getAllByRole("option");
    expect(options).toHaveLength(31);
    expect(options[30]).toHaveTextContent("31.");
  });

  it("eine Vorlage darf mehrere Zeilen erzeugen", async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();

    await screen.findByTestId("forecast-verdict");
    await user.click(screen.getByRole("tab", { name: "Was wäre wenn" }));
    await user.click(screen.getByRole("button", { name: "Vorlage wählen" }));
    await user.click(screen.getByRole("button", { name: /Auto anschaffen/ }));

    // Anzahlung, Rate und Versicherung - genau die Kombination, die man von
    // Hand vergisst.
    expect(screen.getByDisplayValue("Anzahlung Auto")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Finanzierungsrate")).toBeInTheDocument();
    expect(screen.getByDisplayValue("KFZ-Versicherung")).toBeInTheDocument();
  });

  it("zeigt die Einrichtung, solange die Vorschau nie konfiguriert wurde", async () => {
    stubFetch({ settingsUpdatedAt: null });
    const user = userEvent.setup();
    renderPage();

    const setup = await screen.findByTestId("forecast-setup");
    expect(within(setup).getByText("Vorschau einrichten")).toBeInTheDocument();
    expect(within(setup).getByLabelText(/Girokonto/)).toBeChecked();
    expect(within(setup).getByLabelText(/Tagesgeld/)).not.toBeChecked();

    // Bis zum letzten Schritt durchklicken: dort steht die Abnahme.
    for (let step = 0; step < 4; step += 1) {
      await user.click(within(setup).getByRole("button", { name: "Weiter" }));
    }
    expect(within(setup).getByText(/deiner regelmäßigen Zahlungsströme/)).toBeInTheDocument();
  });

  it("blockiert die Aussage, wenn keine Einnahme gepflegt ist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        const ok = (body: unknown) =>
          Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
        if (/\/v1\/me(?:\?|$)/.test(url)) return ok(meResponse);
        if (url.includes("/v1/forecast/upcoming")) return ok([]);
        if (url.includes("/v1/forecast/settings")) return ok(settingsResponse);
        if (url.includes("/v1/forecast"))
          return ok({
            ...forecastResponse,
            warnings: [{ code: "no_income_sources", level: "critical", context: {} }],
          });
        return ok([]);
      }),
    );
    renderPage();

    const verdict = await screen.findByTestId("forecast-verdict");
    expect(within(verdict).getByText("Grundlage fehlt")).toBeInTheDocument();
    expect(within(verdict).getByRole("link", { name: "Einnahmen anlegen" })).toHaveAttribute(
      "href",
      "/fixkosten?richtung=einnahmen",
    );
  });
});
