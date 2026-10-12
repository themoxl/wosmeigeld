/// <reference types="jest" />
import { Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ForecastMetrics, ForecastQuality, ForecastResponse } from "../../api/types";
import { useForecast } from "../../hooks/useForecast";
import { darkTheme } from "../../theme";
import { buildTrendPoints, ForecastCard } from "../ForecastCard";

// useForecast greift ueber useAuth/useHousehold auf echten Kontext zu - fuer
// den Component-Test genuegt ein Modul-Mock mit festen Rueckgabewerten
// (Muster: FixedCostsRatioCard.test.tsx).
//
// Ohne ThemeProvider liefert useTheme() den Vorgabewert des Kontexts, also
// darkTheme - danach richten sich die Farbpruefungen hier.
jest.mock("../../hooks/useForecast", () => ({
  useForecast: jest.fn(),
  FORECAST_CARD_HORIZON_MONTHS: 3,
}));

const mockPush = jest.fn();
jest.mock("expo-router", () => ({
  useRouter: () => ({ push: mockPush }),
}));

const mockUseForecast = useForecast as jest.Mock;

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

function makeForecast(overrides: Partial<ForecastResponse> = {}): ForecastResponse {
  return {
    as_of: "2026-08-14",
    horizon_months: 3,
    granularity: "day",
    opening_liquidity_minor: 240000,
    bound_reserve_minor: 50000,
    safety_buffer_minor: 100000,
    daily: Array.from({ length: 90 }, (_, index) => ({
      on: `2026-08-${String(15 + (index % 15)).padStart(2, "0")}`,
      opening_balance_minor: 200000,
      variable_minor: 0,
      balance_minor: 200000,
      is_event: false,
      flows: [],
    })),
    monthly: [],
    metrics: makeMetrics(),
    cause: null,
    warnings: [],
    quality: makeQuality(),
    ...overrides,
  };
}

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

function renderCard(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<ForecastCard />);
  });
  renderedTrees.push(tree!);
  return tree!;
}

describe("ForecastCard", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseForecast.mockReturnValue(query());
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

  it("zeigt einen Ladehinweis, solange gerechnet wird", () => {
    mockUseForecast.mockReturnValue(query({ isLoading: true }));

    expect(textsOf(renderCard())).toContain("Wird berechnet …");
  });

  it("zeigt bei einem Fehler keinen halben Verlauf, sondern 'Erneut versuchen'", () => {
    const refetch = jest.fn();
    mockUseForecast.mockReturnValue(query({ isError: true, error: new Error("kaputt"), refetch }));

    const tree = renderCard();
    expect(textsOf(tree)).toContain("Vorschau konnte nicht berechnet werden.");

    const button = tree.root.findByProps({ accessibilityLabel: "Vorschau erneut berechnen" });
    act(() => {
      button.props.onPress();
    });
    expect(refetch).toHaveBeenCalled();
  });

  it("zeigt bei einem Engpass Tag und Fehlbetrag - und nur diese eine Aussage", () => {
    mockUseForecast.mockReturnValue(
      query({
        data: makeForecast({
          metrics: makeMetrics({
            first_negative_on: "2026-10-22",
            needed_against_negative_minor: 24500,
            first_below_buffer_on: "2026-10-15",
            needed_for_buffer_minor: 124500,
            lowest_balance_minor: -18000,
            lowest_balance_on: "2026-10-28",
          }),
        }),
      }),
    );

    const texts = textsOf(renderCard());
    expect(texts).toContain("Am 22.10. fehlen 245,00 €");
    // Die nachrangigen Aussagen erscheinen NICHT zusaetzlich.
    expect(texts.join(" ")).not.toContain("Unter Sicherheitspuffer");
    expect(texts.join(" ")).not.toContain("Niedrigster Stand");
  });

  it("fordert bei unzureichender Datenlage die Aktualisierung statt einer Prognose", () => {
    mockUseForecast.mockReturnValue(
      query({
        data: makeForecast({
          quality: makeQuality({ level: "insufficient", last_booking_on: "2026-07-02" }),
          warnings: [{ code: "stale_account_data", level: "watch", context: {} }],
          metrics: makeMetrics({
            first_negative_on: "2026-10-22",
            needed_against_negative_minor: 24500,
          }),
        }),
      }),
    );

    const texts = textsOf(renderCard());
    expect(texts).toContain("Kontodaten aktualisieren");
    expect(texts.join(" ")).not.toContain("Am 22.10. fehlen");
  });

  it("zeigt im gesunden Fall den niedrigsten Stand in Positivfarbe", () => {
    mockUseForecast.mockReturnValue(query({ data: makeForecast() }));

    const tree = renderCard();
    const headline = "Niedrigster Stand in den nächsten 90 Tagen: 1.200,00 €";
    expect(textsOf(tree)).toContain(headline);

    const node = tree.root.findAllByType(Text).find((entry) => entry.props.children === headline)!;
    const flatStyle = [node.props.style].flat();
    expect(flatStyle.some((entry) => entry && entry.color === darkTheme.positive)).toBe(true);
  });

  it("zeigt Verlaufskurve und Tiefstand als eigene Zeile", () => {
    mockUseForecast.mockReturnValue(query({ data: makeForecast() }));

    const tree = renderCard();
    // Kompakte Kurve statt Zahlenwand - Hoehe 90 laut Arbeitsauftrag. Der
    // testID sitzt am TrendChart UND an dessen Wurzel-View, deshalb [0].
    const chart = tree.root.findAllByProps({ testID: "heute-vorschau-verlauf" })[0]!;
    expect(chart.props.height).toBe(90);

    const texts = textsOf(tree);
    expect(texts).toContain("Tiefstand am 30.09.");
    // Der Betrag laeuft durch <Money>, steht also ein zweites Mal als reine
    // Zahl da - nicht nur eingebettet im Aussagesatz.
    expect(texts.filter((text) => text === "1.200,00 €")).toHaveLength(1);
  });

  it("fuehrt beim Antippen auf die Vorschau und sagt das Ziel an", () => {
    mockUseForecast.mockReturnValue(query({ data: makeForecast() }));

    const tree = renderCard();
    const pressable = tree.root.findAll(
      (node) =>
        typeof node.props.accessibilityLabel === "string" && node.props.onPress !== undefined,
    )[0]!;

    expect(pressable.props.accessibilityRole).toBe("button");
    expect(pressable.props.accessibilityLabel).toContain("Vorschau öffnen");
    // Der lange Vorlesetext nennt das Datum ausgeschrieben, nicht "30 Punkt 09".
    expect(pressable.props.accessibilityLabel).toContain("30. September 2026");

    act(() => {
      pressable.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith("/mehr/vorschau");
  });
});

describe("buildTrendPoints", () => {
  it("markiert den Tiefstand und den groessten Zufluss", () => {
    const points = buildTrendPoints([2000, 1500, 900, 4000, 3800]);

    expect(points.map((point) => point.x)).toEqual([0, 1, 2, 3, 4]);
    expect(points.find((point) => point.marker === "low")?.value).toBe(900);
    // "Groesster Zufluss" ist der groesste Anstieg zum Vortag, nicht der
    // hoechste Saldo - sonst waere der Gehaltseingang nie zu erkennen.
    expect(points.find((point) => point.marker === "high")?.value).toBe(4000);
  });

  it("markiert bei einer nur fallenden Reihe keinen Zufluss", () => {
    const points = buildTrendPoints([500, 400, 300]);

    expect(points.filter((point) => point.marker === "high")).toHaveLength(0);
    expect(points.filter((point) => point.marker === "low")).toHaveLength(1);
  });
});
