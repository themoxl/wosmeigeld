/// <reference types="jest" />
import { Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type {
  ForecastDayGroup,
  ForecastMetrics,
  ForecastQuality,
  ForecastResponse,
} from "../../api/types";
import { useForecast, useForecastUpcoming } from "../../hooks/useForecast";
import VorschauScreen from "../../../app/mehr/vorschau";

// Component-Tests fuer den lesenden Vorschau-Screen
// (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 5). Beide Daten-Hooks
// werden gemockt, damit der Screen ohne Auth-/Haushalts-Provider rendert.
// Der Schwerpunkt liegt auf den Zustaenden aus Plan-Abschnitt 8.3 - der
// Idealfall ist nur einer von sechs.

jest.mock("../../hooks/useForecast", () => ({
  useForecast: jest.fn(),
  useForecastUpcoming: jest.fn(),
  FORECAST_SCREEN_HORIZON_MONTHS: 6,
  FORECAST_DAILY_WINDOW_DAYS: 90,
}));

jest.mock("expo-router", () => ({
  Stack: { Screen: () => null },
  useRouter: () => ({ push: jest.fn() }),
}));

const mockUseForecast = useForecast as jest.Mock;
const mockUseForecastUpcoming = useForecastUpcoming as jest.Mock;

function makeMetrics(overrides: Partial<ForecastMetrics> = {}): ForecastMetrics {
  return {
    lowest_balance_minor: 120000,
    lowest_balance_on: "2026-09-30",
    first_negative_on: null,
    first_below_buffer_on: null,
    recovery_on: null,
    days_below_zero: 0,
    days_below_buffer: 0,
    needed_against_negative_minor: 0,
    needed_for_buffer_minor: 0,
    average_monthly_surplus_minor: 45000,
    income_monthly_minor: 320000,
    fixed_monthly_minor: 180000,
    variable_monthly_minor: 95000,
    savings_rate_percent: 14,
    fixed_cost_ratio_percent: 56,
    reserve_without_income_months: 3.4,
    balance_in_3_months_minor: 150000,
    balance_in_6_months_minor: 200000,
    balance_in_12_months_minor: 300000,
    balance_in_24_months_minor: 500000,
    ...overrides,
  };
}

function makeQuality(overrides: Partial<ForecastQuality> = {}): ForecastQuality {
  return {
    level: "good",
    last_booking_on: "2026-08-12",
    contract_count: 12,
    income_count: 2,
    variable_basis: "budget",
    excluded_account_names: [],
    contracts_ending_in_horizon: 0,
    ...overrides,
  };
}

function makeDaily(count: number): ForecastResponse["daily"] {
  const start = new Date(2026, 7, 15);
  return Array.from({ length: count }, (_, index) => {
    const day = new Date(start.getTime());
    day.setDate(day.getDate() + index);
    const iso = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(
      day.getDate(),
    ).padStart(2, "0")}`;
    const balance = 200000 - index * 1000;
    return {
      on: iso,
      opening_balance_minor: balance,
      variable_minor: 500,
      balance_minor: balance,
      is_event: false,
      flows: [],
    };
  });
}

function makeForecast(overrides: Partial<ForecastResponse> = {}): ForecastResponse {
  return {
    as_of: "2026-08-14",
    horizon_months: 6,
    granularity: "day",
    opening_liquidity_minor: 240000,
    bound_reserve_minor: 50000,
    safety_buffer_minor: 100000,
    daily: makeDaily(90),
    monthly: [
      {
        year: 2026,
        month: 9,
        income_minor: 320000,
        fixed_minor: -180000,
        variable_minor: -95000,
        net_minor: 45000,
        closing_balance_minor: 285000,
      },
      {
        year: 2026,
        month: 10,
        income_minor: 320000,
        fixed_minor: -312000,
        variable_minor: -95000,
        net_minor: -87000,
        closing_balance_minor: -18000,
      },
    ],
    metrics: makeMetrics(),
    cause: null,
    warnings: [],
    quality: makeQuality(),
    ...overrides,
  };
}

const UPCOMING: ForecastDayGroup[] = [
  {
    on: "2026-10-01",
    total_minor: -132000,
    balance_after_minor: -4500,
    is_heavy: true,
    flows: [
      { label: "Miete", amount_minor: -95000, source_kind: "contract", source_id: null },
      { label: "KFZ-Versicherung", amount_minor: -37000, source_kind: "contract", source_id: null },
    ],
  },
];

function query(overrides: Record<string, unknown> = {}) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: jest.fn(),
    ...overrides,
  };
}

function textsOf(tree: ReactTestRenderer): string[] {
  return tree.root.findAllByType(Text).map((node) => {
    const children = node.props.children;
    if (typeof children === "string" || typeof children === "number") return String(children);
    if (Array.isArray(children)) {
      return children
        .map((child) =>
          typeof child === "string" || typeof child === "number" ? String(child) : "",
        )
        .join("");
    }
    return "";
  });
}

let renderedTrees: ReactTestRenderer[] = [];

function renderScreen(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<VorschauScreen />);
  });
  renderedTrees.push(tree!);
  return tree!;
}

describe("VorschauScreen", () => {
  beforeEach(() => {
    mockUseForecast.mockReturnValue(query({ data: makeForecast() }));
    mockUseForecastUpcoming.mockReturnValue(query({ data: UPCOMING }));
  });

  afterEach(() => {
    for (const tree of renderedTrees) {
      act(() => {
        tree.unmount();
      });
    }
    renderedTrees = [];
    jest.resetAllMocks();
  });

  it("zeigt einen Ladezustand statt einer halben Kurve", () => {
    mockUseForecast.mockReturnValue(query({ isLoading: true }));

    const tree = renderScreen();
    expect(tree.root.findByProps({ testID: "vorschau-loading" })).toBeTruthy();
  });

  it("zeigt einen Fehlerzustand mit 'Erneut versuchen'", () => {
    const refetch = jest.fn();
    mockUseForecast.mockReturnValue(
      query({ isError: true, error: new Error("Server nicht erreichbar"), refetch }),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Vorschau konnte nicht berechnet werden");
    expect(texts).toContain("Server nicht erreichbar");

    const retry = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retry.props.onPress();
    });
    expect(refetch).toHaveBeenCalled();
  });

  it("sperrt die Zahlen, solange keine Einnahmequelle gepflegt ist", () => {
    mockUseForecast.mockReturnValue(
      query({
        data: makeForecast({
          quality: makeQuality({ level: "insufficient", income_count: 0 }),
          warnings: [{ code: "no_income_sources", level: "critical", context: {} }],
        }),
      }),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("Keine Einnahmequelle gepflegt");
    // Weder Kennzahlen noch Kurve, Zahlungen, Monatskarten oder Simulator.
    expect(texts).not.toContain("Kennzahlen");
    expect(texts).not.toContain("Monate");
    expect(texts).not.toContain("Nächste Zahlungen");
    expect(texts).not.toContain("Kontostands-Prognose");
    expect(tree.root.findAllByProps({ testID: "vorschau-verlauf" })).toHaveLength(0);
    expect(tree.root.findAllByProps({ testID: "vorschau-simulator-oeffnen" })).toHaveLength(0);
    // Die Berechnungsgrundlage bleibt - sie zeigt, was fehlt.
    expect(texts).toContain("So wird gerechnet");
  });

  it("meldet veraltete Kontodaten, zeigt die Zahlen aber weiter", () => {
    mockUseForecast.mockReturnValue(
      query({
        data: makeForecast({
          quality: makeQuality({ level: "insufficient", last_booking_on: "2026-07-02" }),
          warnings: [{ code: "stale_account_data", level: "watch", context: {} }],
        }),
      }),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("Kontodaten aktualisieren");
    expect(texts).toContain("Datenqualität: unzureichend");
    expect(texts).toContain("Kontodaten veraltet");
    expect(texts).toContain("Kennzahlen");
  });

  it("nennt bei einem heute schon negativen Konto den Ist-Befund", () => {
    mockUseForecast.mockReturnValue(
      query({
        data: makeForecast({
          opening_liquidity_minor: -12000,
          metrics: makeMetrics({
            first_negative_on: "2026-08-14",
            needed_against_negative_minor: 12000,
          }),
          warnings: [{ code: "below_zero", level: "critical", context: {} }],
        }),
      }),
    );

    const texts = textsOf(renderScreen());
    expect(texts).toContain("Dein Konto ist heute schon im Minus");
    expect(texts).toContain("Aktueller Stand");
  });

  it("zeigt im gesunden Fall eine eigene Aussage statt eines gruenen Hakens", () => {
    const texts = textsOf(renderScreen());
    expect(texts).toContain("Kein Engpass");
    expect(
      texts.some((text) => text.startsWith("Niedrigster Stand in den nächsten 90 Tagen")),
    ).toBe(true);
  });

  it("zeigt Prognose, Kennzahlen, Zahlungen und Monatskarten in dieser Reihenfolge", () => {
    const tree = renderScreen();
    const texts = textsOf(tree);

    const order = ["Kontostands-Prognose", "Kennzahlen", "Nächste Zahlungen", "Monate"].map(
      (title) => texts.indexOf(title),
    );
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);

    // Die Kurve ist fuer Vorlesehilfen stumm, deshalb traegt das umschliessende
    // Feld die Ansage.
    const chart = tree.root.findAllByProps({ testID: "vorschau-verlauf" })[0]!;
    expect(chart.props.accessibilityLabel).toContain("über 90 Tage");

    // Tiefstand und Pufferlinie stehen als Text daneben - Farbe allein traegt
    // die Aussage nie.
    expect(texts).toContain("Tiefstand am 12.11.");
    expect(texts).toContain("Sicherheitspuffer");

    // Monate als Karten: jeder Wert traegt eine eigene Beschriftung, statt in
    // einer sechsspaltigen Tabelle zu stehen.
    expect(texts).toContain("September 2026");
    expect(texts).toContain("Endstand");
    expect(texts).toContain("Danach verfügbar");
  });

  it("schneidet den Verlauf mit dem Horizont-Umschalter zu, ohne neu anzufragen", () => {
    const tree = renderScreen();
    const before = mockUseForecast.mock.calls.length;

    const shortTab = tree.root.findByProps({ accessibilityLabel: "Zeitraum 30 Tage" });
    act(() => {
      shortTab.props.onPress();
    });

    const chart = tree.root.findAllByProps({ testID: "vorschau-verlauf" })[0]!;
    expect(chart.props.accessibilityLabel).toContain("über 30 Tage");
    // 30 und 90 Tage sind zwei Ausschnitte derselben Reihe: derselbe Horizont,
    // dieselbe Granularitaet - also dieselbe Anfrage.
    expect(mockUseForecast.mock.calls[before]).toEqual([{ horizonMonths: 6, granularity: "day" }]);
  });

  it("holt fuer zwoelf Monate die Monatsreihe statt 365 Tageszeilen", () => {
    const tree = renderScreen();

    const longTab = tree.root.findByProps({ accessibilityLabel: "Zeitraum 12 Monate" });
    act(() => {
      longTab.props.onPress();
    });

    const lastCall = mockUseForecast.mock.calls[mockUseForecast.mock.calls.length - 1]!;
    expect(lastCall[0]).toEqual({ horizonMonths: 12, granularity: "month" });

    const chart = tree.root.findAllByProps({ testID: "vorschau-verlauf" })[0]!;
    expect(chart.props.accessibilityLabel).toContain("über 2 Monate");
  });

  it("zeigt die Ursachenkette nur, wenn ein Ausloesetag bekannt ist", () => {
    expect(textsOf(renderScreen())).not.toContain("Wie es dazu kommt");

    mockUseForecast.mockReturnValue(
      query({
        data: makeForecast({
          cause: {
            on: "2026-10-01",
            total_minor: -132000,
            flows: [
              { label: "Miete", amount_minor: -95000, source_kind: "contract", source_id: null },
            ],
          },
          metrics: makeMetrics({
            first_negative_on: "2026-10-22",
            needed_against_negative_minor: 24500,
            lowest_balance_minor: -18000,
            lowest_balance_on: "2026-10-28",
            recovery_on: "2026-11-01",
          }),
        }),
      }),
    );

    const texts = textsOf(renderScreen());
    expect(texts).toContain("Wie es dazu kommt");
    expect(texts).toContain("Miete");
    expect(texts).toContain("Konto unter null");
    expect(texts).toContain("Wieder im Plus");
  });

  it("klappt die Berechnungsgrundlage erst auf Wunsch auf", () => {
    const tree = renderScreen();
    expect(textsOf(tree)).not.toContain("Startsaldo");

    const toggle = tree.root.findByProps({ accessibilityLabel: "Berechnungsgrundlage anzeigen" });
    expect(toggle.props.accessibilityState).toEqual({ expanded: false });
    act(() => {
      toggle.props.onPress();
    });

    const texts = textsOf(tree);
    expect(texts).toContain("Startsaldo");
    expect(texts).toContain("2.400,00 €");
    expect(texts).toContain("Gebunden in Rücklagen");
    expect(texts).toContain("ausgewiesen, nicht abgezogen");
  });

  it("beantwortet 'Kann ich mir das leisten?' aus Tiefstand und Puffer", () => {
    const tree = renderScreen();

    // Der Banner steht auf dem Bildschirm, das Sheet erst nach dem Antippen.
    expect(textsOf(tree)).not.toContain("Unbedenklich");
    const banner = tree.root.findAllByProps({ testID: "vorschau-simulator-oeffnen" })[0]!;
    act(() => {
      banner.props.onPress();
    });

    // Tiefstand 1.200 EUR, Puffer 1.000 EUR, noch kein Wunschbetrag.
    expect(textsOf(tree)).toContain("Unbedenklich");

    const chip500 = tree.root.findAllByProps({ accessibilityLabel: "Wunschbetrag 500,00 €" })[0]!;
    act(() => {
      chip500.props.onPress();
    });
    let texts = textsOf(tree);
    expect(texts).toContain("Knapper Puffer");
    expect(texts).toContain("700,00 €");

    const chip2500 = tree.root.findAllByProps({
      accessibilityLabel: "Wunschbetrag 2.500,00 €",
    })[0]!;
    act(() => {
      chip2500.props.onPress();
    });
    texts = textsOf(tree);
    expect(texts).toContain("Engpass am 30.09.");
    expect(texts).toContain("-1.300,00 €");
  });

  it("gibt im Simulator keine Kaufempfehlung ab", () => {
    const tree = renderScreen();
    const banner = tree.root.findAllByProps({ testID: "vorschau-simulator-oeffnen" })[0]!;
    act(() => {
      banner.props.onPress();
    });

    // Der Showcase raet an dieser Stelle zu Ratenzahlung und einem spaeteren
    // Kaufdatum. Die App sagt, was passiert - entscheiden tut der Nutzer.
    const joined = textsOf(tree).join(" ");
    expect(joined).not.toContain("Ratenzahlung");
    expect(joined).not.toContain("Raten");
    expect(joined).not.toContain("Empfehlung");
  });

  it("verweist fuer Szenarien auf das Web statt einen Editor anzubieten", () => {
    const texts = textsOf(renderScreen());
    expect(texts.some((text) => text.includes("Was-wäre-wenn im Web bearbeiten"))).toBe(true);
    expect(texts).not.toContain("Was wäre wenn");
  });

  it("haelt den Zahlungsfehler lokal, ohne die uebrige Vorschau auszublenden", () => {
    const refetch = jest.fn();
    mockUseForecastUpcoming.mockReturnValue(query({ isError: true, refetch }));

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Zahlungen konnten nicht geladen werden");
    expect(texts).toContain("Kennzahlen");

    const retry = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retry.props.onPress();
    });
    expect(refetch).toHaveBeenCalled();
  });

  it("sagt ausdruecklich, wenn im Zeitraum keine Zahlung ansteht", () => {
    mockUseForecastUpcoming.mockReturnValue(query({ data: [] }));

    expect(textsOf(renderScreen())).toContain("Keine erwarteten Zahlungen im Zeitraum.");
  });
});
