/// <reference types="jest" />
import { Text, Text as MockRNText } from "react-native";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import type { AnalyticsResult, CashflowTrendResponse } from "../../api/types";
import {
  useCashflowTrend,
  useFixedCostsTrend,
  useProjectReports,
  useReportQuery,
} from "../../hooks/useReports";
import BerichteScreen from "../../../app/(tabs)/berichte";

// Die Hooks greifen ueber useAuth/useHousehold auf echten Kontext zu - fuer
// den Component-Test genuegt ein Modul-Mock mit festen Rueckgabewerten
// (analog BudgetScreen.test.tsx).
jest.mock("../../hooks/useReports", () => {
  const actual = jest.requireActual("../../hooks/useReports");
  return {
    ...actual,
    useCashflowTrend: jest.fn(),
    useFixedCostsTrend: jest.fn(),
    useProjectReports: jest.fn(),
    useReportQuery: jest.fn(),
  };
});

// Die Leitkarte laedt ihre eigene Budgetzusammenfassung ueber react-query und
// braucht dafuer Auth-, Haushalts- und QueryClient-Kontext (siehe
// VertraegeScreen.test.tsx, das denselben Platzhalter setzt).
jest.mock("../FixedCostsRatioCard", () => ({
  FixedCostsRatioCard: () => <MockRNText>Kostenstruktur-Platzhalter</MockRNText>,
}));

// @expo/vector-icons laedt seine Schrift asynchron nach und ist in der reinen
// Node-Testumgebung nicht sinnvoll simulierbar.
jest.mock("@expo/vector-icons", () => ({
  Ionicons: (props: { name: string; color?: string; size?: number }) => (
    <MockRNText {...props}>{`icon:${props.name}`}</MockRNText>
  ),
}));

const mockPush = jest.fn();
jest.mock("expo-router", () => ({
  useRouter: () => ({ push: mockPush }),
}));

const mockUseCashflowTrend = useCashflowTrend as jest.Mock;
const mockUseFixedCostsTrend = useFixedCostsTrend as jest.Mock;
const mockUseProjectReports = useProjectReports as jest.Mock;
const mockUseReportQuery = useReportQuery as jest.Mock;

function queryResult<T>(
  overrides: Partial<{
    data: T;
    isLoading: boolean;
    isError: boolean;
    isFetching: boolean;
    refetch: () => unknown;
  }> = {},
) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: jest.fn(),
    ...overrides,
  };
}

const TREND: CashflowTrendResponse = {
  months: [
    { year: 2026, month: 6, income_minor: 300000, expense_minor: 250000, net_minor: 50000 },
    { year: 2026, month: 7, income_minor: 320000, expense_minor: 240000, net_minor: 80000 },
  ],
};

function aggregateResult(rows: Record<string, string | number | null>[]): AnalyticsResult {
  return {
    grain: "aggregate",
    columns: [],
    rows,
    total_row: null,
    row_count: rows.length,
    truncated: false,
    applied_limits: [],
    next_cursor: null,
    query_id: "test",
    period_from: "2026-06-01",
    period_to: "2026-07-31",
    months_covered: 2,
  };
}

const GROUPS = aggregateResult([
  {
    category_group: "grp-1",
    category_group_label: "Haushalt",
    "date:month": "2026-07-01",
    sum_amount: -90000,
    count_amount: 12,
  },
]);

// Der Kategorienring vergleicht den gewaehlten Monat mit seinem Vormonat.
// Beide Monate haengen am echten Kalender, weil der Screen ohne feste
// Zeitangabe rendert - deshalb hier aus dem heutigen Datum abgeleitet.
const NOW = new Date();
const MONTH_LABEL = new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric" }).format(
  NOW,
);

function monthStart(offset: number): string {
  const date = new Date(NOW.getFullYear(), NOW.getMonth() + offset, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-01`;
}

function monthEnd(offset: number): string {
  const date = new Date(NOW.getFullYear(), NOW.getMonth() + offset + 1, 0);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

const MONTH_GROUPS: AnalyticsResult = {
  ...aggregateResult([
    {
      category_group: "grp-1",
      category_group_label: "Wohnen",
      "date:month": monthStart(-1),
      sum_amount: -100000,
      count_amount: 4,
    },
    {
      category_group: "grp-1",
      category_group_label: "Wohnen",
      "date:month": monthStart(0),
      sum_amount: -120000,
      count_amount: 5,
    },
  ]),
  period_from: monthStart(-1),
  period_to: monthEnd(0),
};

let renderedTrees: ReactTestRenderer[] = [];

function renderScreen(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<BerichteScreen />);
  });
  renderedTrees.push(tree!);
  return tree!;
}

function textsOf(tree: ReactTestRenderer): string[] {
  return tree.root
    .findAllByType(Text)
    .map((node) => node.props.children)
    .filter((value): value is string => typeof value === "string");
}

/**
 * Pressable reicht accessibilityRole und -Label bis auf sein Host-View durch;
 * eine lose Props-Suche faende denselben Knopf deshalb dreifach. `onPress`
 * bleibt dagegen bei der Pressable-Komponente - also genau ein Treffer je
 * Knopf, und zugleich der einzige Knoten, ueber den sich das Antippen
 * ausloesen laesst.
 */
function pressablesWithRole(tree: ReactTestRenderer, role: string): ReactTestInstance[] {
  return tree.root.findAll(
    (node) => typeof node.props.onPress === "function" && node.props.accessibilityRole === role,
  );
}

function pressableLabelled(tree: ReactTestRenderer, label: string): ReactTestInstance | undefined {
  return tree.root
    .findAll(
      (node) => typeof node.props.onPress === "function" && node.props.accessibilityLabel === label,
    )
    .at(0);
}

/**
 * Die Register sind seit dem Redesign Auswahlpillen (<Chip>) und keine
 * eigenen Tabs mehr - erkennbar an `accessibilityState.selected`. Auf
 * `accessibilityState !== undefined` allein darf nicht gefiltert werden:
 * seit <IconButton> seinen Sperrzustand ansagt, tragen auch der
 * Privat-Modus-Knopf und die Monatspfeile ein accessibilityState.
 */
function registerChips(tree: ReactTestRenderer): ReactTestInstance[] {
  return pressablesWithRole(tree, "button").filter(
    (node) => node.props.accessibilityState?.selected !== undefined,
  );
}

function registerNamed(tree: ReactTestRenderer, label: string): ReactTestInstance {
  const chip = registerChips(tree).find((node) => node.props.accessibilityLabel === label);
  if (!chip) throw new Error(`Register "${label}" nicht gefunden`);
  return chip;
}

describe("BerichteScreen", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseCashflowTrend.mockReturnValue(queryResult({ data: TREND }));
    mockUseFixedCostsTrend.mockReturnValue(queryResult());
    mockUseProjectReports.mockReturnValue(queryResult());
    mockUseReportQuery.mockImplementation((label: string) =>
      label === "groups" ? queryResult({ data: GROUPS }) : queryResult(),
    );
  });

  afterEach(() => {
    for (const tree of renderedTrees) {
      act(() => {
        tree.unmount();
      });
    }
    renderedTrees = [];
    jest.clearAllMocks();
  });

  it("bietet dieselben sechs Register wie die Weboberflaeche an", () => {
    const tree = renderScreen();
    const labels = registerChips(tree).map((node) => node.props.accessibilityLabel as string);

    expect(labels).toEqual([
      "Überblick",
      "Kategorien",
      "Empfänger",
      "Jahresvergleich",
      "Fixkosten",
      "Vorhaben",
    ]);
  });

  it("startet in der Monatsansicht und nennt den Zuschnitt darueber", () => {
    const tree = renderScreen();
    const scope = tree.root.findByProps({ testID: "berichte-zuschnitt" });
    expect(scope.props.children).toBe(`${MONTH_LABEL} · Transfers aus · nur Budgetkonten`);
  });

  it("zeigt den juengsten abgeschlossenen Monat als Entscheidungsmonat", () => {
    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("ENTSCHEIDUNGSMONAT");
    expect(texts).toContain("Juli 2026");
  });

  it("stellt die Leitkarte Kostenstruktur ueber die Auswertungen", () => {
    const tree = renderScreen();
    expect(textsOf(tree)).toContain("Kostenstruktur-Platzhalter");
  });

  it("laedt nur das sichtbare Register, nicht alle sechs auf einmal", () => {
    renderScreen();

    // Beim Oeffnen ist "Überblick" aktiv: die Gruppenabfrage laeuft, die
    // Register Kategorien/Empfaenger bleiben abgeschaltet.
    const calls = mockUseReportQuery.mock.calls;
    const enabledByLabel = new Map(calls.map((call) => [call[0] as string, call[2] as boolean]));

    expect(enabledByLabel.get("groups")).toBe(true);
    expect(enabledByLabel.get("categories")).toBe(false);
    expect(enabledByLabel.get("payees")).toBe(false);
    // Verlaufsreihen bleiben in der Monatsansicht bei zwoelf Monaten.
    expect(mockUseFixedCostsTrend).toHaveBeenCalledWith(12, false);
    expect(mockUseProjectReports).toHaveBeenCalledWith(false);
  });

  it("schaltet beim Wechsel auf ein anderes Register dessen Abfrage frei", () => {
    const tree = renderScreen();

    act(() => {
      registerNamed(tree, "Kategorien").props.onPress();
    });

    const lastCategoriesCall = mockUseReportQuery.mock.calls
      .filter((call) => call[0] === "categories")
      .at(-1);
    expect(lastCategoriesCall?.[2]).toBe(true);
  });

  it("wechselt den Zeitraum und fragt damit neu an", () => {
    const tree = renderScreen();
    const segment = pressableLabelled(tree, "Zeitraum 6 Monate");

    act(() => {
      segment?.props.onPress();
    });

    expect(mockUseCashflowTrend).toHaveBeenLastCalledWith(6);
    const scope = tree.root.findByProps({ testID: "berichte-zuschnitt" });
    expect(scope.props.children).toBe("Letzte 6 Monate · Transfers aus · nur Budgetkonten");
  });

  it("blendet den Zeitraumumschalter aus, wo er nichts steuert", () => {
    const tree = renderScreen();

    act(() => {
      registerNamed(tree, "Vorhaben").props.onPress();
    });

    expect(pressableLabelled(tree, "Zeitraum 12 Monate")).toBeUndefined();
  });

  it("zeigt den Monatsnavigator nur in der Monatsansicht", () => {
    const tree = renderScreen();
    expect(pressableLabelled(tree, `Monat wählen, aktuell ${MONTH_LABEL}`)).toBeTruthy();

    act(() => {
      pressableLabelled(tree, "Zeitraum 12 Monate")?.props.onPress();
    });

    expect(pressableLabelled(tree, `Monat wählen, aktuell ${MONTH_LABEL}`)).toBeUndefined();
  });

  it("vergleicht im Kategorienring den gewaehlten Monat mit dem Vormonat", () => {
    mockUseReportQuery.mockImplementation((label: string) =>
      label === "groups" ? queryResult({ data: MONTH_GROUPS }) : queryResult(),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("KATEGORIEN-VERTEILUNG");
    expect(texts).toContain("Wohnen");
    // 1.200 EUR gegen 1.000 EUR im Vormonat - mehr ausgegeben.
    expect(texts).toContain("+20 % zum Vormonat");
  });

  it("laesst die Vormonatsfussnote weg, wo es keinen Vormonat zu vergleichen gibt", () => {
    const tree = renderScreen();

    act(() => {
      pressableLabelled(tree, "Zeitraum 12 Monate")?.props.onPress();
    });

    const texts = textsOf(tree);
    expect(texts).toContain("Haushalt");
    // Der Zeitraum umfasst mehrere Monate: eine Prozentangabe "zum Vormonat"
    // waere hier nicht belegbar. (Die Kennzahlen des Entscheidungsmonats
    // vergleichen weiterhin mit dem Vormonat - aber in Euro, nicht in Prozent.)
    expect(texts.some((value) => /^[+-]\d+ % zum Vormonat$/.test(value))).toBe(false);
  });

  it("fuehrt von einer Monatszahl zu den Buchungen dahinter", () => {
    const tree = renderScreen();
    const monthRow = pressablesWithRole(tree, "button").find(
      (node) =>
        typeof node.props.accessibilityLabel === "string" &&
        node.props.accessibilityLabel.startsWith("Juli 2026"),
    );

    act(() => {
      monthRow?.props.onPress();
    });

    // Der Drilldown stellt eine eigene Zeilenabfrage - erkennbar am Label.
    const drilldownCall = mockUseReportQuery.mock.calls.find((call) => call[0] === "drilldown");
    expect(drilldownCall?.[1]).toMatchObject({
      grain: "rows",
      include_transfers: false,
      only_on_budget: true,
      period: { kind: "custom", from_date: "2026-07-01", to_date: "2026-07-31" },
    });
  });

  it("zeigt einen Leerzustand statt Nullen, wenn keine Buchungen vorliegen", () => {
    mockUseCashflowTrend.mockReturnValue(queryResult({ data: { months: [] } }));

    const tree = renderScreen();
    expect(textsOf(tree)).toContain("Noch keine Buchungen im Zeitraum");
  });

  it("bietet bei einem Fehler einen Wiederholversuch an, statt leer zu bleiben", () => {
    const refetch = jest.fn();
    mockUseCashflowTrend.mockReturnValue(queryResult({ isError: true, refetch }));

    const tree = renderScreen();

    expect(tree.root.findByProps({ testID: "berichte-error" })).toBeTruthy();

    const retry = pressableLabelled(tree, "Erneut versuchen");
    expect(retry).toBeTruthy();

    act(() => {
      retry?.props.onPress();
    });
    expect(refetch).toHaveBeenCalled();
  });
});
