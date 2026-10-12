/// <reference types="jest" />
import { Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { BudgetSummary, OverviewReportResponse } from "../../api/types";
import { useBudgetSummary, useOverviewReport } from "../../hooks/useBudget";
// Handy-Palette statt Web-Tokens (siehe src/theme/palette.ts); ohne
// ThemeProvider gilt der Vorgabewert des Kontexts, also darkTheme.
import { darkTheme } from "../../theme";
import { formatMonthLabel } from "../../utils/dates";
import { AFTER_FIXED_COSTS_LABEL } from "../../utils/financeLabels";
import { FixedCostsRatioCard } from "../FixedCostsRatioCard";
import { isHostElementOfType } from "../testUtils";
import { Money } from "../ui";

// useOverviewReport/useBudgetSummary greifen ueber useAuth/useHousehold auf
// echten Kontext zu - fuer den Component-Test genuegt ein Modul-Mock mit
// festen Rueckgabewerten je Test.
jest.mock("../../hooks/useBudget");

const mockUseOverviewReport = useOverviewReport as jest.MockedFunction<typeof useOverviewReport>;
const mockUseBudgetSummary = useBudgetSummary as jest.MockedFunction<typeof useBudgetSummary>;

function makeReport(overrides: Partial<OverviewReportResponse> = {}): OverviewReportResponse {
  return {
    current_year: 2026,
    current_month: 8,
    total_income_minor: 250000,
    total_expense_minor: 120000,
    net_savings_minor: 130000,
    savings_rate_percent: 52,
    budget_remaining_minor: 30000,
    open_review_count: 0,
    fixed_costs_monthly_minor: 87500,
    available_after_fixed_minor: 162500,
    fixed_costs_ratio_percent: 35,
    upcoming_due_count: 0,
    top_expense_categories: [],
    cashflow_last_6_months: [],
    ...overrides,
  };
}

function makeSummary(overrides: Partial<BudgetSummary> = {}): BudgetSummary {
  return {
    budget_id: "b1",
    year: 2026,
    month: 8,
    status: "open",
    method: "tracking",
    closed_at: null,
    total_income_minor: 250000,
    total_planned_minor: 200000,
    total_rollover_minor: 0,
    total_spent_minor: 120000,
    total_remaining_minor: 80000,
    total_planned_income_minor: 0,
    total_rollover_income_minor: 0,
    total_income_remaining_minor: 0,
    uncategorized_spent_minor: 0,
    savings_rate_percent: 52,
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
    surplus_before_goal_reserves_minor: 162500,
    goal_reserves_monthly_minor: 0,
    free_after_goal_reserves_minor: 162500,
    goal_reserve_count: 0,
    planning_expected_income_minor: 250000,
    planning_income_basis: "actual",
    regular_fixed_costs_monthly_minor: 87500,
    fixed_cost_reserves_monthly_minor: 0,
    fixed_cost_reserve_count: 0,
    fixed_cost_reserve_items: [],
    variable_planned_budget_minor: 0,
    variable_planned_excluded_contract_minor: 0,
    variable_planned_excluded_goal_savings_minor: 0,
    total_calculational_reserves_minor: 0,
    planning_truly_free_minor: 162500,
    fixed_costs_monthly_minor: 87500,
    fixed_costs_income_monthly_minor: 0,
    income_basis_minor: 250000,
    available_after_fixed_minor: 162500,
    fixed_costs_income_basis: "actual",
    fixed_costs_ratio_percent: 35,
    fixed_costs_contract_count: 4,
    groups: [],
    ...overrides,
  };
}

function overviewQuery(data: OverviewReportResponse | undefined, isLoading = false) {
  return { data, isLoading } as unknown as ReturnType<typeof useOverviewReport>;
}

function summaryQuery(data: BudgetSummary | undefined, isLoading = false) {
  return { data, isLoading } as unknown as ReturnType<typeof useBudgetSummary>;
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

function renderCard(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<FixedCostsRatioCard />);
  });
  return tree!;
}

describe("FixedCostsRatioCard", () => {
  afterEach(() => {
    jest.resetAllMocks();
  });

  it("zeigt einen Ladehinweis, solange der Uebersichtsbericht laedt", () => {
    mockUseOverviewReport.mockReturnValue(overviewQuery(undefined, true));
    mockUseBudgetSummary.mockReturnValue(summaryQuery(undefined, true));

    const tree = renderCard();

    // Der Ladezustand ist seit dem Redesign ein Platzhaltergeruest statt einer
    // Textzeile; angesagt wird er als eine Flaeche mit dem Sammel-Label
    // "Wird geladen", statt jede Platzhalterzeile einzeln vorzulesen.
    const loading = tree.root.find(
      (node) => isHostElementOfType(node, "View") && node.props.testID === "kostenstruktur-zustand",
    );
    expect(loading.props.accessibilityLabel).toBe("Wird geladen");
    expect(loading.props.accessible).toBe(true);
  });

  it("zeigt einen neutralen Hinweis, wenn kein Bericht verfuegbar ist", () => {
    mockUseOverviewReport.mockReturnValue(overviewQuery(undefined, false));
    mockUseBudgetSummary.mockReturnValue(summaryQuery(undefined, false));

    const tree = renderCard();

    // Statt des generischen "Derzeit nicht verfügbar." nennt die Karte jetzt
    // den betroffenen Monat - dieselbe Aussage, nur konkret. Der Monatsname
    // wird aus demselben Helfer abgeleitet wie in der Karte, damit der Test
    // nicht am Monatswechsel zerbricht.
    const monthLabel = formatMonthLabel(new Date());
    expect(textsOf(tree)).toContain(`Für ${monthLabel} liegen keine Zahlen vor`);
  });

  it("zeigt Prozentwert, Statuswort, Balken und Nach-Fixkosten-Betrag im Normalfall", () => {
    mockUseOverviewReport.mockReturnValue(overviewQuery(makeReport()));
    mockUseBudgetSummary.mockReturnValue(summaryQuery(makeSummary()));

    const tree = renderCard();
    const texts = textsOf(tree);

    expect(texts).toContain("35,0 %");
    expect(texts).toContain("Gut");
    expect(texts).toContain(AFTER_FIXED_COSTS_LABEL);
    expect(texts).toContain("1.625,00 €");
    expect(texts).toContain("auf Basis gebuchter Einnahmen");

    const bar = tree.root.find((node) => node.props.accessibilityRole === "progressbar");
    // `text` kam mit dem Designsystem dazu: Vorlesehilfen sprechen den
    // ausgeschriebenen Satz statt einer nackten Zahl.
    expect(bar.props.accessibilityValue).toEqual({
      min: 0,
      max: 100,
      now: 35,
      text: "35 Prozent",
    });
    expect(bar.props.accessibilityLabel).toBe("Fixkostenquote: 35 Prozent");

    // Der lange Schwellen-Erklaertext aus der Vorgaengerversion darf nicht
    // mehr vorkommen.
    expect(texts.join(" ")).not.toMatch(/50 bis 70/);
  });

  it("zeigt einen negativen Nach-Fixkosten-Betrag in Warnfarbe statt eines Strichs", () => {
    mockUseOverviewReport.mockReturnValue(
      overviewQuery(
        makeReport({
          fixed_costs_ratio_percent: 82,
          fixed_costs_monthly_minor: 205000,
          available_after_fixed_minor: -5000,
        }),
      ),
    );
    mockUseBudgetSummary.mockReturnValue(
      summaryQuery(
        makeSummary({
          fixed_costs_ratio_percent: 82,
          fixed_costs_monthly_minor: 205000,
          available_after_fixed_minor: -5000,
          fixed_costs_income_basis: "actual",
        }),
      ),
    );

    const tree = renderCard();
    const texts = textsOf(tree);

    expect(texts).toContain("Kritisch");
    expect(texts).toContain("-50,00 €");

    const amountNode = tree.root
      .findAllByType(Text)
      .find((node) => node.props.children === "-50,00 €")!;
    const flatStyle = [amountNode.props.style].flat();
    expect(flatStyle.some((entry) => entry && entry.color === darkTheme.warning)).toBe(true);
  });

  it("zeigt einen Strich statt 0 € bei fehlender Einkommensbasis", () => {
    mockUseOverviewReport.mockReturnValue(
      overviewQuery(
        makeReport({
          fixed_costs_ratio_percent: 0,
          fixed_costs_monthly_minor: 45000,
          available_after_fixed_minor: -45000,
        }),
      ),
    );
    mockUseBudgetSummary.mockReturnValue(
      summaryQuery(
        makeSummary({
          total_income_minor: 0,
          fixed_costs_ratio_percent: 0,
          fixed_costs_monthly_minor: 45000,
          fixed_costs_income_monthly_minor: 0,
          available_after_fixed_minor: -45000,
          fixed_costs_income_basis: "contract",
        }),
      ),
    );

    const tree = renderCard();
    const texts = textsOf(tree);

    expect(texts).toContain("—");

    // Positionsgenau statt ueber den gesamten Kartentext: "-450,00 €" steht in
    // dieser Lage voellig zu Recht in der FIXKOSTEN-Kachel, und "0,00 €" bei
    // EINNAHMEN ist eine echte Zahl ("nichts gebucht"). Geprueft wird deshalb
    // genau die Zeile "Nach Fixkosten ..." - dort darf KEIN errechneter Betrag
    // stehen, sondern nur der Strich.
    const rowsWithLabel = tree.root
      .findAll((node) => isHostElementOfType(node, "View"))
      .filter((row) =>
        row.findAllByType(Text).some((node) => node.props.children === AFTER_FIXED_COSTS_LABEL),
      );
    const afterFixedRow = rowsWithLabel[rowsWithLabel.length - 1]!;
    expect(afterFixedRow.findAllByType(Money)).toHaveLength(0);

    const amountNode = tree.root
      .findAllByType(Text)
      .find(
        (node) =>
          node.props.children === "—" &&
          node.props.accessibilityLabel === "Kein Betrag ermittelbar",
      );
    expect(amountNode).toBeDefined();
  });

  it('zeigt "Schätzung" bei Vertragsfallback der Einkommensbasis', () => {
    mockUseOverviewReport.mockReturnValue(overviewQuery(makeReport()));
    mockUseBudgetSummary.mockReturnValue(
      summaryQuery(
        makeSummary({
          total_income_minor: 0,
          fixed_costs_income_basis: "contract",
          fixed_costs_income_monthly_minor: 240000,
        }),
      ),
    );

    const tree = renderCard();

    expect(textsOf(tree)).toContain("Schätzung aus Einnahmeverträgen");
  });
});
