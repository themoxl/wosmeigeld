/// <reference types="jest" />
import { Text as MockRNText } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { CategoryGroup } from "../../api/categoryTypes";
import type { Contract, ContractSummary, Person } from "../../api/types";
import { useCategories } from "../../hooks/useCategories";
import {
  useContractContext,
  useContracts,
  useContractSummary,
  useDeleteContract,
  useSetContractStatus,
} from "../../hooks/useContracts";
import { usePeople } from "../../hooks/usePeople";
// Der Screen selbst lebt in app/(tabs)/vertraege.tsx (Expo-Router-Route);
// dieser Test bleibt bewusst ausserhalb von app/, damit expo-router die
// Testdatei nicht selbst als Route einsammelt (siehe require.context in
// expo-router/entry.js - dort gibt es keinen eingebauten Ausschluss fuer
// *.test.tsx).
import VertraegeScreen from "../../../app/(tabs)/vertraege";

// Vertragsuebersicht als SectionList (Redesign-Plan Abschnitt 7). Die reine
// Gruppier-/Sortierlogik ist bereits in hooks/useContracts.test.ts
// abgedeckt - hier geht es um das Zusammenspiel: Segmented Control zuerst,
// dann der kompakte Zusammenfassungsblock, dann die nach Kategorien
// gegliederte Liste, plus die abgesicherten Aktionen je Zeile.

jest.mock("expo-router", () => ({
  Stack: {
    Screen: (props: { options?: { title?: string } }) => (
      <MockRNText>{props.options?.title ?? ""}</MockRNText>
    ),
  },
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("@expo/vector-icons", () => ({
  Ionicons: (props: { name: string; color?: string; size?: number }) => (
    <MockRNText {...props}>{`icon:${props.name}`}</MockRNText>
  ),
}));

// FixedCostsRatioCard laedt selbst useOverviewReport/useBudgetSummary (siehe
// deren eigenen Component-Test) - fuer den Screen-Test genuegt ein
// Platzhalter, der nur sichtbar macht, OB die Karte gerendert wurde.
jest.mock("../FixedCostsRatioCard", () => ({
  FixedCostsRatioCard: () => <MockRNText>FixedCostsRatioCard-Platzhalter</MockRNText>,
}));

jest.mock("../../hooks/useCategories");
jest.mock("../../hooks/usePeople");

// Das Preissprung-Banner liest die Review-Inbox (GET /v1/review) und braucht
// dafuer Auth- und Haushaltskontext. Fuer diesen Screen-Test genuegt eine
// leere Inbox - der Bannerfall selbst haengt an app/mehr/review.tsx.
jest.mock("../../hooks/useReview", () => ({
  useReviewInbox: () => ({ data: undefined }),
}));

jest.mock("../../hooks/useContracts", () => {
  const actual = jest.requireActual("../../hooks/useContracts");
  return {
    ...actual,
    useContractContext: jest.fn(),
    useContracts: jest.fn(),
    useContractSummary: jest.fn(),
    useSetContractStatus: jest.fn(),
    useDeleteContract: jest.fn(),
  };
});

const mockPush = jest.fn();

const mockUseCategories = useCategories as jest.MockedFunction<typeof useCategories>;
const mockUseContractContext = useContractContext as jest.MockedFunction<typeof useContractContext>;
const mockUseContracts = useContracts as jest.MockedFunction<typeof useContracts>;
const mockUseContractSummary = useContractSummary as jest.MockedFunction<typeof useContractSummary>;
const mockUseSetContractStatus = useSetContractStatus as jest.MockedFunction<
  typeof useSetContractStatus
>;
const mockUseDeleteContract = useDeleteContract as jest.MockedFunction<typeof useDeleteContract>;
const mockUsePeople = usePeople as jest.MockedFunction<typeof usePeople>;

function makeContract(overrides: Partial<Contract> = {}): Contract {
  return {
    id: "c-1",
    household_id: "h-1",
    name: "Vertrag",
    provider_label: null,
    contract_type: "subscription",
    payee_id: null,
    account_id: null,
    category_id: null,
    contract_key: null,
    contract_key_source: null,
    direction: "expense",
    amount_minor: 1000,
    frequency: "monthly",
    due_rule: "day_of_month",
    due_day: 1,
    next_due_date: "2026-08-15",
    first_seen_date: null,
    last_seen_date: null,
    tolerance_days: 3,
    tolerance_amount_minor: 0,
    reserve_enabled: false,
    reserve_started_on: null,
    reserve_starting_amount_minor: 0,
    status: "active",
    cancelled_on: null,
    ends_on: null,
    min_term_months: null,
    cancellation_period_days: null,
    starts_on: null,
    renewal_term_months: null,
    provider_template_key: null,
    installments_total: null,
    installments_paid: 0,
    source: "manual",
    detection_confidence: null,
    missed_periods: 0,
    last_matched_at: null,
    note: null,
    income_kind: null,
    person_id: null,
    version: 1,
    created_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

function makePerson(id: string, name: string, sortOrder: number): Person {
  return {
    id,
    household_id: "h-1",
    name,
    member_user_id: null,
    sort_order: sortOrder,
    is_active: true,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
  };
}

const WOHNEN: CategoryGroup = {
  id: "g-wohnen",
  name: "Wohnen",
  sort_order: 1,
  categories: [
    {
      id: "cat-miete",
      group_id: "g-wohnen",
      name: "Miete",
      default_type: null,
      budget_bucket: null,
      is_archived: false,
      sort_order: 0,
    },
  ],
};

const VERSICHERUNGEN: CategoryGroup = {
  id: "g-versicherungen",
  name: "Versicherungen",
  sort_order: 2,
  categories: [
    {
      id: "cat-auto",
      group_id: "g-versicherungen",
      name: "Auto",
      default_type: null,
      budget_bucket: null,
      is_archived: false,
      sort_order: 0,
    },
  ],
};

const MIETE_VERTRAG = makeContract({
  id: "miete",
  name: "Miete Wohnung",
  category_id: "cat-miete",
  amount_minor: 90000,
  frequency: "monthly",
  next_due_date: "2026-09-01",
  status: "active",
});

const AUTO_VERTRAG = makeContract({
  id: "vers",
  name: "Autoversicherung",
  category_id: "cat-auto",
  amount_minor: 45000,
  frequency: "yearly",
  next_due_date: "2026-10-01",
  status: "paused",
});

const KUENFTIGER_VERTRAG = makeContract({
  id: "neu",
  name: "Neuer Streamingdienst",
  category_id: null,
  amount_minor: 1200,
  frequency: "monthly",
  next_due_date: "2099-01-01",
  starts_on: "2099-01-01",
  status: "active",
});

const MAX = makePerson("p-max", "Max", 0);
const ANNA = makePerson("p-anna", "Anna", 1);

const GEHALT_MAX = makeContract({
  id: "gehalt-max",
  name: "Gehalt Max",
  direction: "income",
  contract_type: "income",
  income_kind: "salary",
  person_id: "p-max",
  amount_minor: 300000,
  next_due_date: "2026-08-28",
});

const MIETEINNAHME_ANNA = makeContract({
  id: "miete-anna",
  name: "Ferienwohnung",
  direction: "income",
  contract_type: "income",
  income_kind: "rental",
  person_id: "p-anna",
  amount_minor: 50000,
  next_due_date: "2026-08-05",
});

const NEBENJOB_OHNE_ZUORDNUNG = makeContract({
  id: "nebenjob",
  name: "Nebenjob",
  direction: "income",
  contract_type: "income",
  income_kind: null,
  person_id: null,
  amount_minor: 40000,
  next_due_date: "2026-08-10",
});

function contractsQuery(
  data: Contract[],
  overrides: Partial<ReturnType<typeof useContracts>> = {},
) {
  return {
    data,
    isLoading: false,
    isFetching: false,
    refetch: jest.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useContracts>;
}

function summaryQuery(data: Partial<ContractSummary>) {
  return { data, isLoading: false } as unknown as ReturnType<typeof useContractSummary>;
}

function textsOf(tree: ReactTestRenderer): string[] {
  return tree.root.findAllByType(MockRNText).map((node) => {
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

// SectionList (ueber VirtualizedList) plant beim Mounten einen echten
// setTimeout fuer die Zellenberechnung. Ohne explizites unmount() ueberlebt
// dieser Timer das Testende und React protokolliert nach Testschluss eine
// "act(...)"-Warnung, die den gesamten Jest-Lauf als fehlgeschlagen meldet -
// componentWillUnmount() von VirtualizedList raeumt den Timer zuverlaessig
// auf (siehe clearTimeout(this._updateCellsToRenderTimeoutID) dort).
let renderedTree: ReactTestRenderer | undefined;

function renderScreen(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<VertraegeScreen />);
  });
  renderedTree = tree!;
  return tree!;
}

describe("VertraegeScreen", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockUseContractContext.mockReturnValue({
      accessToken: "token",
      householdId: "h-1",
      enabled: true,
      canEdit: true,
    });
    mockUseCategories.mockReturnValue({
      data: [WOHNEN, VERSICHERUNGEN],
    } as unknown as ReturnType<typeof useCategories>);
    mockUsePeople.mockReturnValue({
      data: [MAX, ANNA],
    } as unknown as ReturnType<typeof usePeople>);
    mockUseContracts.mockReturnValue(
      contractsQuery([
        MIETE_VERTRAG,
        AUTO_VERTRAG,
        KUENFTIGER_VERTRAG,
        GEHALT_MAX,
        MIETEINNAHME_ANNA,
        NEBENJOB_OHNE_ZUORDNUNG,
      ]),
    );
    mockUseContractSummary.mockReturnValue(
      summaryQuery({
        monthly_expense_minor: 135700,
        yearly_expense_minor: 1500000,
        monthly_income_minor: 250000,
        yearly_income_minor: 3000000,
      }),
    );
    mockUseSetContractStatus.mockReturnValue({
      mutate: jest.fn(),
    } as unknown as ReturnType<typeof useSetContractStatus>);
    mockUseDeleteContract.mockReturnValue({
      mutate: jest.fn(),
    } as unknown as ReturnType<typeof useDeleteContract>);
  });

  afterEach(() => {
    act(() => {
      renderedTree?.unmount();
    });
    renderedTree = undefined;
    jest.clearAllMocks();
  });

  // Die Ueberschrift steht seit dem Redesign im Inhalt (ScreenHeader) und
  // nicht mehr in der nativen Titelleiste - die Tab-Bildschirme haben keine.
  it('zeigt den Screen-Titel als "Verträge & Abos" mit echtem Umlaut', () => {
    const tree = renderScreen();
    expect(textsOf(tree)).toContain("Verträge & Abos");
  });

  it("zeigt Monats- und Jahressumme sowie die Fixkostenquote-Karte im Ausgaben-Tab", () => {
    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("1.357,00 €");
    expect(texts).toContain("15.000,00 €");
    expect(texts).toContain("FixedCostsRatioCard-Platzhalter");
  });

  it("blendet im Einnahmen-Tab die Fixkostenquote aus und zeigt nur die Summen", () => {
    const tree = renderScreen();

    const incomeTab = tree.root.findByProps({ accessibilityLabel: "Einnahmen" });
    act(() => {
      incomeTab.props.onPress();
    });

    const texts = textsOf(tree);
    expect(texts).toContain("2.500,00 €");
    expect(texts).toContain("30.000,00 €");
    expect(texts).not.toContain("FixedCostsRatioCard-Platzhalter");
  });

  it("gliedert die Einnahmen nach Person, mit 'Ohne Person' zuletzt", () => {
    const tree = renderScreen();
    act(() => {
      tree.root.findByProps({ accessibilityLabel: "Einnahmen" }).props.onPress();
    });

    const texts = textsOf(tree);
    const max = texts.indexOf("Max");
    const anna = texts.indexOf("Anna");
    const ohnePerson = texts.indexOf("Ohne Person");

    // Reihenfolge aus sort_order der Personenliste, nicht alphabetisch.
    expect(max).toBeGreaterThan(-1);
    expect(max).toBeLessThan(anna);
    expect(anna).toBeLessThan(ohnePerson);
    // Die Kategoriegliederung der Ausgabenseite darf hier nicht auftauchen.
    expect(texts).not.toContain("Ohne Kategorie");
  });

  it("zeigt die Einnahmeart in der Zeile und benennt eine fehlende Art ausdruecklich", () => {
    const tree = renderScreen();
    act(() => {
      tree.root.findByProps({ accessibilityLabel: "Einnahmen" }).props.onPress();
    });

    const texts = textsOf(tree).join(" | ");
    expect(texts).toMatch(/Gehalt • Monatlich/);
    expect(texts).toMatch(/Vermietung • Monatlich/);
    // Bestand ohne gepflegte Art: sichtbare offene Aufgabe statt stiller Luecke.
    expect(texts).toMatch(/Ohne Art • Monatlich/);
  });

  it("gliedert die Vertragsliste nach Kategorien, mit 'Ohne Kategorie' zuletzt", () => {
    const tree = renderScreen();
    const texts = textsOf(tree);

    const miete = texts.indexOf("Miete");
    const auto = texts.indexOf("Auto");
    const ohneKategorie = texts.indexOf("Ohne Kategorie");

    expect(miete).toBeGreaterThan(-1);
    expect(auto).toBeGreaterThan(-1);
    expect(ohneKategorie).toBeGreaterThan(-1);
    expect(miete).toBeLessThan(auto);
    expect(auto).toBeLessThan(ohneKategorie);
  });

  it("zeigt einen Textbadge fuer nicht aktive Vertraege, aber nicht fuer aktive", () => {
    const tree = renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("Pausiert");
    expect(texts).not.toContain("Aktiv");
  });

  // Die Fussnote rechts steht in der schmalen Betragsspalte und nutzt deshalb
  // das kurze Tagesdatum; die Jahreszahl kommt nur dazu, wenn das Datum nicht
  // im laufenden Jahr liegt - genau wie hier.
  it('zeigt "Beginnt ..." statt der naechsten Faelligkeit fuer einen kuenftigen Vertrag', () => {
    const tree = renderScreen();
    const texts = textsOf(tree).join(" | ");

    expect(texts).toMatch(/Beginnt 01\.01\.2099/);
    expect(texts).not.toMatch(/Nächste 01\.01\.2099/);
  });

  it("oeffnet beim Antippen der ganzen Zeile das Vertragsdetail", () => {
    const tree = renderScreen();
    const row = tree.root.findByProps({ accessibilityLabel: "Miete Wohnung öffnen" });

    act(() => {
      row.props.onPress();
    });

    expect(mockPush).toHaveBeenCalledWith("/vertrag/miete");
  });

  it("oeffnet ueber das Overflow-Icon das Aktionsmenue, ohne das Detail zu oeffnen", () => {
    const tree = renderScreen();
    const overflowButton = tree.root.findByProps({
      accessibilityLabel: "Aktionen für Miete Wohnung",
    });

    act(() => {
      overflowButton.props.onPress();
    });

    expect(mockPush).not.toHaveBeenCalled();
    expect(textsOf(tree)).toContain("Bearbeiten");
  });

  it("zeigt den Leerzustand, wenn im gewaehlten Tab keine Vertraege vorhanden sind", () => {
    mockUseContracts.mockReturnValue(contractsQuery([]));
    const tree = renderScreen();

    expect(textsOf(tree)).toContain("Keine Verträge");
  });
});
