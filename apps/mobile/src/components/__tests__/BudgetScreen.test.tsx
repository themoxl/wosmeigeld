/// <reference types="jest" />
import { Text } from "react-native";
import { act, create, type ReactTestRenderer, type ReactTestInstance } from "react-test-renderer";
import type {
  BudgetSummary,
  CategoryBudgetStatus,
  CategoryGroupBudgetStatus,
} from "../../api/types";
import { useBudgetSummary, useUpdateBudgetAllocations } from "../../hooks/useBudget";
import { useReservePlanningPreference } from "../../hooks/useReservePlanningPreference";
import { useReserveOverview } from "../../hooks/useReserves";
import BudgetScreen from "../../../app/(tabs)/budget";

// useBudgetSummary/useUpdateBudgetAllocations greifen ueber useAuth/useHousehold
// auf echten Kontext zu - fuer den Component-Test genuegt ein Modul-Mock mit
// festen Rueckgabewerten je Test (analog FixedCostsRatioCard.test.tsx).
jest.mock("../../hooks/useBudget");
jest.mock("../../hooks/useReservePlanningPreference");
jest.mock("../../hooks/useReserves");
// Bewusst einfache Funktionen statt jest.fn(() => ...): das
// jest.resetAllMocks() in afterEach() wuerde die Implementierung einer
// jest.fn() nach dem ersten Test entfernen, und die Hooks liefen fuer alle
// weiteren Tests auf undefined.
jest.mock("../../hooks/useAccounts", () => ({
  useAccounts: () => ({ data: [] }),
}));
// Das Drilldown-Sheet fragt die Buchungen der Kategorie seit dem
// Monatsfilter ueber useTransactionsPage() ab, nicht mehr ueber
// useTransactions().
jest.mock("../../hooks/useTransactions", () => ({
  useTransactions: () => ({ data: [] }),
  useTransactionsPage: () => ({
    items: [],
    isLoading: false,
    isError: false,
    refetch: () => undefined,
  }),
}));

const mockPush = jest.fn();
jest.mock("expo-router", () => ({
  useRouter: () => ({ push: mockPush }),
}));

const mockUseBudgetSummary = useBudgetSummary as jest.MockedFunction<typeof useBudgetSummary>;
const mockUseUpdateBudgetAllocations = useUpdateBudgetAllocations as jest.MockedFunction<
  typeof useUpdateBudgetAllocations
>;
const mockUseReservePlanningPreference = useReservePlanningPreference as jest.MockedFunction<
  typeof useReservePlanningPreference
>;
const mockUseReserveOverview = useReserveOverview as jest.MockedFunction<typeof useReserveOverview>;

function makeCategory(overrides: Partial<CategoryBudgetStatus> = {}): CategoryBudgetStatus {
  return {
    category_id: "cat-1",
    category_name: "Kategorie",
    group_id: "group-1",
    group_name: "Gruppe",
    budget_bucket: null,
    default_type: "expense",
    rollover_rule: "none",
    is_archived: false,
    planned_minor: 10000,
    rollover_minor: 0,
    spent_minor: 5000,
    income_actual_minor: 0,
    remaining_minor: 5000,
    percent_used: 50,
    prev_month_actual_minor: 0,
    contract_forecast_minor: 0,
    contract_forecast_sources: [],
    has_activity_ever: true,
    ...overrides,
  };
}

function makeGroups(): CategoryGroupBudgetStatus[] {
  return [
    {
      group_id: "g-income",
      group_name: "Einkommen",
      sort_order: 1,
      total_planned_minor: 0,
      total_rollover_minor: 0,
      total_spent_minor: 0,
      total_remaining_minor: 0,
      total_income_actual_minor: 200000,
      // Der Gehaltsplan zaehlt getrennt: total_planned_minor bleibt 0, weil
      // eine geplante Einnahme kein Budgettopf ist.
      total_planned_income_minor: 250000,
      total_rollover_income_minor: 0,
      total_income_remaining_minor: 50000,
      categories: [
        makeCategory({
          category_id: "cat-income",
          category_name: "Gehalt",
          group_id: "g-income",
          group_name: "Einkommen",
          default_type: "income",
          planned_minor: 250000,
          rollover_minor: 0,
          spent_minor: 0,
          income_actual_minor: 200000,
          remaining_minor: 50000,
        }),
      ],
    },
    {
      group_id: "g-fix",
      group_name: "Fixkosten",
      sort_order: 2,
      total_planned_minor: 30000,
      total_rollover_minor: 2000,
      total_spent_minor: 25000,
      total_remaining_minor: 7000,
      total_income_actual_minor: 0,
      total_planned_income_minor: 0,
      total_rollover_income_minor: 0,
      total_income_remaining_minor: 0,
      categories: [
        makeCategory({
          category_id: "cat-miete",
          category_name: "Miete",
          group_id: "g-fix",
          group_name: "Fixkosten",
          planned_minor: 30000,
          rollover_minor: 2000,
          spent_minor: 25000,
          remaining_minor: 7000,
          budget_bucket: "needs",
        }),
      ],
    },
    {
      group_id: "g-freizeit",
      group_name: "Freizeit",
      sort_order: 3,
      total_planned_minor: 5000,
      total_rollover_minor: 0,
      total_spent_minor: -1000,
      total_remaining_minor: 6000,
      total_income_actual_minor: 0,
      total_planned_income_minor: 0,
      total_rollover_income_minor: 0,
      total_income_remaining_minor: 0,
      categories: [
        makeCategory({
          category_id: "cat-hobby",
          category_name: "Hobby",
          group_id: "g-freizeit",
          group_name: "Freizeit",
          planned_minor: 5000,
          rollover_minor: 0,
          spent_minor: -1000,
          remaining_minor: 6000,
          budget_bucket: "wants",
        }),
        makeCategory({
          category_id: "cat-selten",
          category_name: "Selten genutzt",
          group_id: "g-freizeit",
          group_name: "Freizeit",
          planned_minor: 0,
          rollover_minor: 0,
          spent_minor: 0,
          income_actual_minor: 0,
          remaining_minor: 0,
          contract_forecast_minor: 0,
          has_activity_ever: false,
        }),
      ],
    },
  ];
}

function makeBudget(overrides: Partial<BudgetSummary> = {}): BudgetSummary {
  return {
    budget_id: "b1",
    year: 2026,
    month: 8,
    status: "open",
    method: "tracking",
    closed_at: null,
    total_income_minor: 250000,
    total_planned_minor: 35000,
    total_rollover_minor: 2000,
    total_spent_minor: 41250,
    total_remaining_minor: 8750,
    total_planned_income_minor: 250000,
    total_rollover_income_minor: 0,
    total_income_remaining_minor: 50000,
    uncategorized_spent_minor: 4210,
    bucket_classified_spent_minor: 0,
    savings_rate_percent: 0,
    bucket_needs_minor: 0,
    bucket_wants_minor: 0,
    bucket_savings_minor: 0,
    bucket_unassigned_spent_minor: 0,
    bucket_needs_planned_minor: 0,
    bucket_wants_planned_minor: 0,
    bucket_savings_planned_minor: 0,
    bucket_unassigned_planned_minor: 0,
    bucket_needs_percent: 0,
    bucket_wants_percent: 0,
    bucket_savings_percent: 0,
    surplus_before_goal_reserves_minor: 215000,
    goal_reserves_monthly_minor: 0,
    free_after_goal_reserves_minor: 215000,
    goal_reserve_count: 0,
    planning_expected_income_minor: 250000,
    planning_income_basis: "actual",
    regular_fixed_costs_monthly_minor: 0,
    fixed_cost_reserves_monthly_minor: 0,
    fixed_cost_reserve_count: 0,
    fixed_cost_reserve_items: [],
    variable_planned_budget_minor: 35000,
    variable_planned_excluded_contract_minor: 0,
    variable_planned_excluded_goal_savings_minor: 0,
    total_calculational_reserves_minor: 0,
    planning_truly_free_minor: 215000,
    fixed_costs_monthly_minor: 0,
    fixed_costs_income_monthly_minor: 0,
    income_basis_minor: 250000,
    available_after_fixed_minor: 0,
    fixed_costs_income_basis: "actual",
    fixed_costs_ratio_percent: 0,
    fixed_costs_contract_count: 0,
    groups: makeGroups(),
    ...overrides,
  };
}

function summaryQuery(
  overrides: Partial<ReturnType<typeof useBudgetSummary>> = {},
): ReturnType<typeof useBudgetSummary> {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: jest.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useBudgetSummary>;
}

function mutationResult(
  overrides: Partial<ReturnType<typeof useUpdateBudgetAllocations>> = {},
): ReturnType<typeof useUpdateBudgetAllocations> {
  return {
    mutateAsync: jest.fn().mockResolvedValue(undefined),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useUpdateBudgetAllocations>;
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

// SectionList/VirtualizedList planen einen internen `setTimeout` fuer
// `_updateCellsToRender`, der nach Testende noch feuern und "Cannot log
// after tests are done" ausloesen kann. Jeder gerenderte Baum wird deshalb
// zentral verfolgt und in afterEach() unmounted (raeumt VirtualizedLists
// eigenen Timer in componentWillUnmount() auf), statt das je Testfall selbst
// zu wiederholen.
let renderedTrees: ReactTestRenderer[] = [];

function renderScreen(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<BudgetScreen />);
  });
  renderedTrees.push(tree!);
  return tree!;
}

/**
 * Findet die OptionList-Zeile ("radio") mit dem gegebenen sichtbaren Label.
 * OptionList.tsx setzt kein eigenes accessibilityLabel je Option - der
 * Weg fuehrt deshalb ueber den sichtbaren Text nach oben zum naechsten
 * Vorfahren mit `accessibilityRole="radio"` UND einer eigenen `onPress`-
 * Funktion (der Zeilen-Pressable selbst statt einer seiner intern
 * gerenderten Host-Views, die je nach RN-Version dieselben Props spiegeln
 * koennen).
 */
function findRadioRowByLabel(tree: ReactTestRenderer, label: string): ReactTestInstance {
  const textNode = tree.root.find((node) => node.type === Text && node.props.children === label);
  let current: ReactTestInstance | null = textNode.parent;
  while (
    current &&
    !(current.props.accessibilityRole === "radio" && typeof current.props.onPress === "function")
  ) {
    current = current.parent;
  }
  if (!current) {
    throw new Error(`Radio-Zeile fuer "${label}" nicht gefunden.`);
  }
  return current;
}

describe("BudgetScreen", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseUpdateBudgetAllocations.mockReturnValue(mutationResult());
    mockUseReservePlanningPreference.mockReturnValue({
      includeInPlan: false,
      setIncludeInPlan: jest.fn(),
      isHydrated: true,
    });
    mockUseReserveOverview.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: jest.fn(),
    } as unknown as ReturnType<typeof useReserveOverview>);
  });

  afterEach(() => {
    for (const tree of renderedTrees) {
      act(() => {
        tree.unmount();
      });
    }
    renderedTrees = [];
  });

  afterEach(() => {
    jest.resetAllMocks();
  });

  it("zeigt einen Ladehinweis, solange das Budget laedt", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ isLoading: true }));

    const tree = renderScreen();

    expect(tree.root.findByProps({ accessibilityLabel: "Wird geladen" })).toBeTruthy();
  });

  it("zeigt einen Fehlerzustand mit 'Erneut versuchen' statt einer rohen Meldung", () => {
    const refetch = jest.fn();
    mockUseBudgetSummary.mockReturnValue(
      summaryQuery({ isError: true, error: new Error("Netzwerkfehler"), refetch }),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Netzwerkfehler");

    const retryButton = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retryButton.props.onPress();
    });
    expect(refetch).toHaveBeenCalled();
  });

  it("zeigt einen eigenen Leerzustand, wenn fuer den Monat kein Budget existiert", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: undefined }));

    const tree = renderScreen();

    expect(textsOf(tree)).toContain("Kein Budget für diesen Monat");
  });

  it("zeigt weder den 50/30/20-Block noch NEEDS/WANTS/SAVINGS-Tags in der primaeren Ansicht", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));

    const tree = renderScreen();
    const allText = textsOf(tree).join(" ");

    expect(allText).not.toMatch(/50\/30\/20/);
    expect(allText).not.toMatch(/NEEDS/);
    expect(allText).not.toMatch(/WANTS/);
    expect(allText).not.toMatch(/SAVINGS/);
    expect(allText).not.toContain("Bearbeiten");
  });

  it("zeigt die kompakte Zusammenfassung mit Plan + Uebertrag und Budgetrest", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));

    const tree = renderScreen();
    const texts = textsOf(tree);

    // Der Satz "412,50 € netto ausgegeben von 370,00 € Plan + Übertrag" steht
    // seit dem Redesign nicht mehr als eine Textzeile da, sondern als
    // Beschriftung + Betrag nebeneinander. Inhaltlich muss beides weiter
    // vorkommen: total_planned_minor(35000) + total_rollover_minor(2000) = 37000.
    expect(texts).toContain("Netto ausgegeben");
    expect(texts).toContain("412,50 €");
    expect(texts).toContain("Plan + Übertrag");
    expect(texts).toContain("370,00 €");
    expect(texts).toContain("Budgetrest");
    expect(texts).toContain("87,50 €");
  });

  it("zeigt bei negativem Nettoverbrauch einen Erstattungsueberschuss ohne Minuszeichen", () => {
    mockUseBudgetSummary.mockReturnValue(
      summaryQuery({
        data: makeBudget({ total_spent_minor: -3660, total_remaining_minor: 40660 }),
      }),
    );

    const tree = renderScreen();
    const texts = textsOf(tree);

    // Kern der Zusicherung: der Ueberschuss erscheint OHNE Minuszeichen und
    // wird als solcher benannt, statt als negative Ausgabe dazustehen.
    expect(texts).toContain("Mehr erstattet als ausgegeben");
    expect(texts).toContain("36,60 €");
    expect(texts.join(" ")).not.toMatch(/-36,60/);
  });

  it("zeigt die unkategorisierte Warnzeile mit Betrag und oeffnet beim Antippen die vorgefilterte Buchungsliste", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));

    const tree = renderScreen();
    expect(textsOf(tree)).toContain("42,10 €");

    const link = tree.root.findByProps({
      accessibilityLabel:
        "42,10 € Ausgaben ohne Kategorie. Öffnet die Buchungsliste, gefiltert auf ohne Kategorie.",
    });
    act(() => {
      link.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith("/buchungen?uncategorized=true");
  });

  it("blendet die unkategorisierte Warnzeile aus, wenn nichts unkategorisiert ist", () => {
    mockUseBudgetSummary.mockReturnValue(
      summaryQuery({ data: makeBudget({ uncategorized_spent_minor: 0 }) }),
    );

    const tree = renderScreen();
    expect(textsOf(tree)).not.toContain("Ausgaben ohne Kategorie · Buchungen ansehen");
  });

  it("zeigt Ausgabengruppen als flache Zeilen mit Ist / Plan + Uebertrag und Kategoriezeilen darunter", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));

    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("Fixkosten");
    // Gruppenkopf: Ist gegen Plan + Uebertrag, jetzt als Beschriftung + Betrag.
    // total_spent_minor 25000 / (30000 + 2000 Uebertrag) = 32000
    expect(texts).toContain("Ist");
    expect(texts).toContain("250,00 €");
    expect(texts).toContain("Plan + Übertrag");
    expect(texts).toContain("320,00 €");

    // Der zusammenhaengende Satz ist nicht verschwunden, sondern in die
    // Vorlesehilfe gewandert - dort steht er weiter vollstaendig.
    const row = tree.root.findByProps({
      accessibilityLabel: "Miete, 250,00 € von 320,00 € ausgegeben, Budgetrest 70,00 €",
    });
    expect(row).toBeTruthy();
  });

  it("zeigt bei negativem Gruppen-Ist den Erstattungsueberschuss statt eines negativen Werts", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));

    const tree = renderScreen();
    const texts = textsOf(tree);

    // Kein negativer Ist-Wert, sondern die Benennung als Ueberschuss ...
    expect(texts).toContain("Mehr erstattet");
    expect(texts).toContain("10,00 €");
    expect(texts.join(" ")).not.toMatch(/-10,00/);
    // ... und der Plan der Gruppe steht unveraendert daneben.
    expect(texts).toContain("Plan");
    expect(texts).toContain("50,00 €");
  });

  it("zeigt Einnahmekategorien mit 'eingegangen'/'Noch erwartet' statt eines Ausgabenbalkens", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));

    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("Gehalt");
    // Einnahmen werden als "Eingegangen / Erwartet" gegenuebergestellt, nicht
    // als Ausgabensatz - der vollstaendige Satz steht in der Vorlesehilfe.
    expect(texts).toContain("Eingegangen");
    expect(texts).toContain("2.000,00 €");
    expect(texts).toContain("Erwartet");
    expect(texts).toContain("2.500,00 €");
    expect(texts).toContain("Noch erwartet");
    expect(texts).toContain("500,00 €");

    const incomeRow = tree.root.findByProps({
      accessibilityLabel: "Gehalt, 2.000,00 € von 2.500,00 € eingegangen, Noch erwartet 500,00 €",
    });
    const progressBars = incomeRow.findAll(
      (node) => node.props.accessibilityRole === "progressbar",
    );
    expect(progressBars).toHaveLength(0);
  });

  it("blendet Kategorien ohne Bewegung standardmaessig aus und zeigt sie nach Wechsel auf 'Alle'", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));

    const tree = renderScreen();
    expect(textsOf(tree)).not.toContain("Selten genutzt");
    expect(textsOf(tree)).toContain("1 ausgeblendet");

    const filterTrigger = tree.root.findByProps({
      accessibilityLabel: "Anzeigen: Dieser Monat. Öffnet die Auswahl des Anzeigezeitraums.",
    });
    act(() => {
      filterTrigger.props.onPress();
    });

    // OptionList setzt kein eigenes accessibilityLabel je Option - die
    // "Alle"-Zeile wird deshalb ueber ihren sichtbaren Text gefunden und von
    // dort zum naechsten Vorfahren mit accessibilityRole="radio" UND einer
    // eigenen onPress-Funktion aufgeloest (der Zeilen-Pressable selbst,
    // nicht seine intern gerenderten Host-Views).
    const allOptionRow = findRadioRowByLabel(tree, "Alle");
    act(() => {
      allOptionRow.props.onPress();
    });

    expect(textsOf(tree)).toContain("Selten genutzt");
  });

  it("oeffnet den Planbetrag-Editor beim Antippen einer Ausgabenkategorie, vorbelegt mit dem Planbetrag", async () => {
    const mutateAsync = jest.fn().mockResolvedValue(undefined);
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget() }));
    mockUseUpdateBudgetAllocations.mockReturnValue(mutationResult({ mutateAsync }));

    const tree = renderScreen();
    const row = tree.root.findByProps({
      accessibilityLabel: "Miete, 250,00 € von 320,00 € ausgegeben, Budgetrest 70,00 €",
    });
    expect(row.props.accessibilityHint).toBe("Öffnet den Planbetrag zum Bearbeiten");

    act(() => {
      row.props.onPress();
    });

    // Das Sheet nennt seit dem Monatsfilter auch den angezeigten Monat -
    // geprueft wird deshalb der Anfang der Zeile, nicht die ganze Zeichenkette
    // (der Monatsname haengt am Systemdatum).
    expect(textsOf(tree).some((text) => text.startsWith("Kategorie: Miete · "))).toBe(true);

    const input = tree.root.findByProps({ label: "Neuer Planbetrag (EUR)" });
    expect(input.props.value).toBe("300,00"); // planned_minor 30000 -> "300,00"

    const saveButton = tree.root.findByProps({ label: "Speichern" });
    await act(async () => {
      saveButton.props.onPress();
    });

    expect(mutateAsync).toHaveBeenCalled();
  });

  it("markiert geschlossene Monate sichtbar als schreibgeschuetzt und deaktiviert die Kategoriezeilen", () => {
    mockUseBudgetSummary.mockReturnValue(summaryQuery({ data: makeBudget({ status: "closed" }) }));

    const tree = renderScreen();
    expect(textsOf(tree)).toContain("Dieser Monat ist abgeschlossen und schreibgeschützt.");

    const row = tree.root.findByProps({
      accessibilityLabel: "Miete, 250,00 € von 320,00 € ausgegeben, Budgetrest 70,00 €",
    });
    expect(row.props.onPress).toBeUndefined();
    expect(row.props.accessibilityState).toEqual({ disabled: true });
    expect(row.props.accessibilityHint).toBeUndefined();
  });
});
