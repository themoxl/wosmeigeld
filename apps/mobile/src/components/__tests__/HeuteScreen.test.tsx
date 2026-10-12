/// <reference types="jest" />
import { Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type {
  Account,
  CategoryGroup,
  ForecastMetrics,
  ForecastQuality,
  ForecastResponse,
  Meal,
  MealWeek,
  ReviewInboxResponse,
  Transaction,
} from "../../api/types";
import { useAccounts } from "../../hooks/useAccounts";
import { useBudgetSummary } from "../../hooks/useBudget";
import { useCategories } from "../../hooks/useCategories";
import { useForecast, useForecastUpcoming } from "../../hooks/useForecast";
import { useMealWeeks, useMeals } from "../../hooks/useLebensmittel";
import { useReviewInbox } from "../../hooks/useReview";
import { useCreateTransaction } from "../../hooks/useTransactionMutations";
import { useTransactions } from "../../hooks/useTransactions";
import { AFTER_FIXED_COSTS_LABEL } from "../../utils/financeLabels";
import HeuteScreen from "../../../app/(tabs)/index";

// Component-Tests fuer die Reihenfolge/Kennzahl-Semantik auf Heute
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 4 und 12). Alle Daten-Hooks
// werden gemockt (wie in FixedCostsRatioCard.test.tsx), damit der Screen ohne
// echte Auth-/Haushalts-Provider gerendert werden kann.
//
// Seit der Liquiditaetsvorschau (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md,
// Stufe 5) traegt ForecastCard die eine Finanzbotschaft. useForecast.ts wird
// sowohl vom Screen selbst (nur fuer Refetch/Pull-to-refresh) als auch von
// der eingebetteten Karte aufgerufen - ein Modul-Mock deckt beide
// Aufrufstellen ab.

const mockPush = jest.fn();
jest.mock("expo-router", () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("../../hooks/useForecast", () => ({
  useForecast: jest.fn(),
  useForecastUpcoming: jest.fn(() => ({ data: [], isLoading: false })),
  FORECAST_CARD_HORIZON_MONTHS: 3,
}));
jest.mock("../../hooks/useBudget", () => ({
  useBudgetSummary: jest.fn(() => ({
    data: {
      total_planned_minor: 100000,
      total_spent_minor: 40000,
      total_remaining_minor: 60000,
    },
    isLoading: false,
  })),
}));
jest.mock("../../hooks/useTransactionMutations", () => ({
  useCreateTransaction: jest.fn(),
}));
jest.mock("../../hooks/useReview", () => ({ useReviewInbox: jest.fn() }));
jest.mock("../../hooks/useTransactions", () => ({ useTransactions: jest.fn() }));
jest.mock("../../hooks/useAccounts", () => ({ useAccounts: jest.fn() }));
jest.mock("../../hooks/useCategories", () => ({ useCategories: jest.fn() }));
jest.mock("../../hooks/useLebensmittel", () => ({
  useMealWeeks: jest.fn(),
  useMeals: jest.fn(),
}));

const mockUseForecast = useForecast as jest.Mock;
const mockUseForecastUpcoming = useForecastUpcoming as jest.Mock;
const mockUseBudgetSummary = useBudgetSummary as jest.Mock;
const mockUseCreateTransaction = useCreateTransaction as jest.Mock;
const mockUseReviewInbox = useReviewInbox as jest.Mock;
const mockUseTransactions = useTransactions as jest.Mock;
const mockUseAccounts = useAccounts as jest.Mock;
const mockUseCategories = useCategories as jest.Mock;
const mockUseMealWeeks = useMealWeeks as jest.Mock;
const mockUseMeals = useMeals as jest.Mock;

// today/tomorrow exakt wie in app/(tabs)/index.tsx berechnet, damit die
// Essensplan-Fixtures unabhaengig vom tatsaechlichen Testlaufdatum treffen.
const now = new Date();
const TODAY = now.toISOString().slice(0, 10);
const TOMORROW = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

function queryResult<T>(
  overrides: Partial<{
    data: T | undefined;
    isLoading: boolean;
    isError: boolean;
    error: unknown;
    isFetching: boolean;
    refetch: () => void;
  }> = {},
) {
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

function makeReviewInbox(overrides: Partial<ReviewInboxResponse> = {}): ReviewInboxResponse {
  return {
    total_count: 0,
    unclassified_open_count: 0,
    unclassified_total_count: 0,
    unclassified_offset: 0,
    unclassified_limit: 20,
    unclassified: [],
    possible_duplicates: [],
    transfer_candidates: [],
    recurring_suggestions: [],
    ...overrides,
  } as ReviewInboxResponse;
}

function makeTransaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: "t1",
    account_id: "acc-1",
    booked_date: "2026-08-05",
    value_date: null,
    amount_minor: -1200,
    currency: "EUR",
    payee_label: "Rewe",
    note: null,
    status: "booked",
    is_transfer: false,
    category_id: "cat-1",
    is_split: false,
    version: 1,
    is_archived: false,
    ...overrides,
  };
}

const ACCOUNTS = [{ id: "acc-1", name: "Girokonto" }] as unknown as Account[];
const CATEGORY_GROUPS = [
  {
    id: "grp-1",
    name: "Lebensmittel",
    sort_order: 1,
    categories: [
      {
        id: "cat-1",
        group_id: "grp-1",
        name: "Supermarkt",
        default_type: "expense",
        budget_bucket: null,
        is_archived: false,
      },
    ],
  },
] as unknown as CategoryGroup[];

function makeMealWeek(overrides: Partial<MealWeek> = {}): MealWeek {
  return {
    id: "week-1",
    week_start: "2026-08-03",
    budget_minor: 0,
    note: null,
    created_at: "2026-08-01T00:00:00Z",
    ...overrides,
  } as unknown as MealWeek;
}

function makeMeal(overrides: Partial<Meal> = {}): Meal {
  return {
    id: "meal-1",
    meal_week_id: "week-1",
    meal_date: TODAY,
    meal_type: "dinner",
    dish_name: "Linsensuppe",
    dish_id: null,
    servings: 2,
    estimated_cost_minor: null,
    note: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  } as unknown as Meal;
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

// Baseline: alles laedt erfolgreich, aber leer/neutral - jeder Test
// ueberschreibt gezielt die fuer ihn relevanten Mocks.
function applyDefaults() {
  mockUseForecast.mockReturnValue(queryResult<ForecastResponse>({ data: makeForecast() }));
  mockUseForecastUpcoming.mockReturnValue(queryResult({ data: [] }));
  mockUseBudgetSummary.mockReturnValue(
    queryResult({
      data: {
        total_planned_minor: 100000,
        total_spent_minor: 40000,
        total_remaining_minor: 60000,
      },
    }),
  );
  mockUseReviewInbox.mockReturnValue(queryResult<ReviewInboxResponse>({ data: makeReviewInbox() }));
  mockUseTransactions.mockReturnValue(queryResult<Transaction[]>({ data: [] }));
  mockUseAccounts.mockReturnValue(queryResult<Account[]>({ data: ACCOUNTS }));
  mockUseCategories.mockReturnValue(queryResult<CategoryGroup[]>({ data: CATEGORY_GROUPS }));
  mockUseMealWeeks.mockReturnValue(queryResult<MealWeek[]>({ data: [] }));
  mockUseMeals.mockReturnValue(queryResult<Meal[]>({ data: [] }));
  mockUseCreateTransaction.mockReturnValue({ mutateAsync: jest.fn(), isPending: false });
}

let renderedTrees: ReactTestRenderer[] = [];

function renderScreen(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<HeuteScreen />);
  });
  renderedTrees.push(tree!);
  return tree!;
}

describe("HeuteScreen", () => {
  beforeEach(() => {
    mockPush.mockClear();
    applyDefaults();
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

  it("zeigt weder Budgetrest noch ein generisches 'Verfuegbar'", () => {
    const tree = renderScreen();
    const allText = textsOf(tree).join(" ");

    expect(allText).not.toMatch(/Budgetrest/);
    expect(allText).not.toMatch(/^Verfügbar$/m);
    expect(allText).not.toContain("Aktuelles Budget");
    expect(allText).not.toContain("Noch möglich");
  });

  it("traegt genau eine Finanzbotschaft: die Vorschau, nicht mehr die Fixkostenquote", () => {
    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("Niedrigster Stand in den nächsten 90 Tagen: 1.200,00 €");
    // Die Fixkostenquote ist auf Heute ERSETZT worden, nicht ergaenzt (Plan
    // Abschnitt 9). Sie bleibt auf der Vertragsseite.
    expect(texts).not.toContain(AFTER_FIXED_COSTS_LABEL);
    expect(texts.join(" ")).not.toContain("Fixkostenquote");
  });

  it("zeigt bei einem Engpass Tag und Fehlbetrag und fuehrt auf die Vorschau", () => {
    mockUseForecast.mockReturnValue(
      queryResult({
        data: makeForecast({
          metrics: makeMetrics({
            first_negative_on: "2026-10-22",
            needed_against_negative_minor: 24500,
            lowest_balance_minor: -18000,
            lowest_balance_on: "2026-10-28",
          }),
          warnings: [{ code: "below_zero", level: "critical", context: {} }],
        }),
      }),
    );

    const tree = renderScreen();
    expect(textsOf(tree)).toContain("Am 22.10. fehlen 245,00 €");

    const card = tree.root.findAll(
      (node) =>
        typeof node.props.accessibilityLabel === "string" &&
        node.props.accessibilityLabel.includes("Vorschau öffnen"),
    )[0]!;
    act(() => {
      card.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith("/mehr/vorschau");
  });

  it("zeigt einen eigenen Ladehinweis fuer die Vorschau, ohne andere Bereiche auszublenden", () => {
    mockUseForecast.mockReturnValue(queryResult({ isLoading: true }));
    mockUseTransactions.mockReturnValue(queryResult({ data: [makeTransaction()] }));

    const texts = textsOf(renderScreen());
    expect(texts).toContain("Wird berechnet …");
    expect(texts).toContain("Rewe");
  });

  it("blendet den Review-Hinweis vollstaendig aus, wenn nichts offen ist", () => {
    mockUseReviewInbox.mockReturnValue(queryResult({ data: makeReviewInbox({ total_count: 0 }) }));

    const tree = renderScreen();

    expect(textsOf(tree).join(" ")).not.toMatch(/prüfen/);
  });

  it("zeigt eine kompakte Aktionszeile bei offenen Faellen und oeffnet die Review-Inbox", () => {
    mockUseReviewInbox.mockReturnValue(queryResult({ data: makeReviewInbox({ total_count: 3 }) }));

    const tree = renderScreen();
    expect(textsOf(tree)).toContain("3 Buchungen prüfen");

    const row = tree.root.findByProps({ accessibilityLabel: "3 Buchungen prüfen" });
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith("/mehr/review");
  });

  it("verwendet den Singular fuer genau einen offenen Fall", () => {
    mockUseReviewInbox.mockReturnValue(queryResult({ data: makeReviewInbox({ total_count: 1 }) }));

    const tree = renderScreen();

    expect(textsOf(tree)).toContain("1 Buchung prüfen");
  });

  it("zeigt einen Fehlerzustand mit 'Erneut versuchen' fuer die Review-Sektion, ohne andere Bereiche auszublenden", () => {
    const refetch = jest.fn();
    mockUseReviewInbox.mockReturnValue(
      queryResult({ isError: true, error: new Error("Netzwerkfehler"), refetch }),
    );
    mockUseTransactions.mockReturnValue(queryResult({ data: [makeTransaction()] }));

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Netzwerkfehler");
    expect(texts).toContain("Rewe");

    const retryButton = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retryButton.props.onPress();
    });
    expect(refetch).toHaveBeenCalled();
  });

  it("zeigt hoechstens drei antippbare letzte Buchungen mit 'Alle' in der Abschnittsueberschrift", () => {
    const transactions = [
      makeTransaction({ id: "t1", payee_label: "Rewe" }),
      makeTransaction({ id: "t2", payee_label: "Aldi" }),
      makeTransaction({ id: "t3", payee_label: "Netto" }),
      makeTransaction({ id: "t4", payee_label: "Miete" }),
    ];
    mockUseTransactions.mockReturnValue(queryResult({ data: transactions }));

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Rewe");
    expect(texts).toContain("Aldi");
    expect(texts).toContain("Netto");
    expect(texts).not.toContain("Miete");

    const rowPress = tree.root.findByProps({ accessibilityLabel: "Rewe öffnen" });
    act(() => {
      rowPress.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith("/buchung/t1");

    // Das Label heisst seit dem Redesign ausgeschrieben "Alle Buchungen" -
    // vorgelesen war das blosse "Alle" ohne den Kartenkopf nicht deutbar.
    const allLink = tree.root.findByProps({ accessibilityLabel: "Alle Buchungen" });
    act(() => {
      allLink.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith("/buchungen");
  });

  it("zeigt einen Ladehinweis fuer Buchungen unabhaengig von anderen Bereichen", () => {
    mockUseTransactions.mockReturnValue(queryResult({ isLoading: true }));

    const tree = renderScreen();

    expect(tree.root.findByProps({ testID: "heute-transactions-loading" })).toBeTruthy();
  });

  it("zeigt einen eigenen Leerzustand fuer 'noch keine Buchungen'", () => {
    mockUseTransactions.mockReturnValue(queryResult({ data: [] }));

    const tree = renderScreen();

    expect(textsOf(tree)).toContain("Noch keine Buchungen");
  });

  it("zeigt einen Fehlerzustand fuer Buchungen mit 'Erneut versuchen', der die anderen Bereiche nicht ausblendet", () => {
    const refetch = jest.fn();
    mockUseTransactions.mockReturnValue(
      queryResult({ isError: true, error: new Error("Serverfehler"), refetch }),
    );
    mockUseMeals.mockReturnValue(
      queryResult({ data: [makeMeal({ meal_date: TODAY, dish_name: "Linsensuppe" })] }),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Serverfehler");
    expect(texts).toContain("Linsensuppe");

    const retryButton = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retryButton.props.onPress();
    });
    expect(refetch).toHaveBeenCalled();
  });

  it("zeigt Essensplan fuer heute und morgen als Zwei-Spalten-Vorschau ohne weiteren Finanz-KPI", () => {
    mockUseMealWeeks.mockReturnValue(queryResult({ data: [makeMealWeek()] }));
    mockUseMeals.mockReturnValue(
      queryResult({
        data: [
          makeMeal({ id: "m1", meal_date: TODAY, dish_name: "Linsensuppe" }),
          makeMeal({ id: "m2", meal_date: TOMORROW, dish_name: "Ofengemüse" }),
        ],
      }),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Heute");
    expect(texts).toContain("Linsensuppe");
    expect(texts).toContain("Morgen");
    expect(texts).toContain("Ofengemüse");
  });

  it("zeigt einen neutralen Leerzustand, wenn fuer heute und morgen nichts geplant ist", () => {
    mockUseMealWeeks.mockReturnValue(queryResult({ data: [] }));
    mockUseMeals.mockReturnValue(queryResult({ data: [] }));

    const tree = renderScreen();

    expect(textsOf(tree)).toContain("Noch nichts geplant.");
  });

  it("zeigt einen Fehlerzustand fuer den Essensplan, ohne die anderen Bereiche auszublenden", () => {
    const refetchMealWeeks = jest.fn();
    mockUseMealWeeks.mockReturnValue(
      queryResult({
        isError: true,
        error: new Error("Essensplan nicht erreichbar"),
        refetch: refetchMealWeeks,
      }),
    );
    mockUseTransactions.mockReturnValue(queryResult({ data: [makeTransaction()] }));

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Essensplan nicht erreichbar");
    expect(texts).toContain("Rewe");

    const retryButton = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retryButton.props.onPress();
    });
    expect(refetchMealWeeks).toHaveBeenCalled();
  });
});
