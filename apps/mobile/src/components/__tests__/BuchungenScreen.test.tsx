/// <reference types="jest" />
import { SectionList, Text, TextInput } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { Transaction } from "../../api/types";
import { PrimaryButton } from "../../components/PrimaryButton";
import { useAccounts } from "../../hooks/useAccounts";
import {
  useBulkCategoryApply,
  useBulkCategoryPreview,
  useCategoryPermissions,
  useCreateCategory,
  useQueueBulkCategory,
  useRecentCategoryIds,
} from "../../hooks/useCategoryCatalog";
import { useCategories } from "../../hooks/useCategories";
import { useConnectivity } from "../../hooks/useConnectivity";
import { useDeleteTransaction } from "../../hooks/useTransactionMutations";
import { useTransactionsPage } from "../../hooks/useTransactions";
import { formatMonthLabel } from "../../utils/dates";
import BuchungenScreen from "../../../app/(tabs)/buchungen";

// Component-Tests fuer Loading/Error/Empty/Filter-Empty/Populated
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 5 und 12). Alle Daten-Hooks
// werden gemockt (wie in ../../components/__tests__/FixedCostsRatioCard.test.tsx),
// damit der Screen ohne echte Auth-/Haushalts-Provider gerendert werden kann.
//
// jest.mock(...) wird von Jest/Babel unabhaengig von seiner Position im Modul
// vor alle Imports gehoben - die Aufrufe stehen deshalb bewusst NACH den
// Imports oben, damit die typisierten Hook-Referenzen (fuer
// `as jest.MockedFunction<...>` unten) direkt aus einem einzigen Importblock
// kommen und der Linter (import/first) nicht zwei Bloecke verlangt.

const mockRouterPush = jest.fn();

jest.mock("expo-router", () => ({
  useRouter: () => ({ push: mockRouterPush, replace: jest.fn(), back: jest.fn() }),
}));

jest.mock("../../hooks/useTransactions", () => ({
  useTransactionsPage: jest.fn(),
}));
jest.mock("../../hooks/useAccounts", () => ({ useAccounts: jest.fn() }));
jest.mock("../../hooks/useCategories", () => ({ useCategories: jest.fn() }));
jest.mock("../../hooks/useConnectivity", () => ({ useConnectivity: jest.fn() }));
jest.mock("../../hooks/useTransactionMutations", () => ({
  useDeleteTransaction: jest.fn(),
}));
// CategoryPickerModal (immer gemountet, auch waehrend `visible={false}`) haengt
// transitiv an denselben Hooks - ein einziger Modul-Mock deckt sowohl die
// direkten Aufrufstellen in buchungen.tsx als auch die von
// CategorySearchSheet ab.
jest.mock("../../hooks/useCategoryCatalog", () => ({
  useBulkCategoryApply: jest.fn(),
  useBulkCategoryPreview: jest.fn(),
  useQueueBulkCategory: jest.fn(),
  useRecentCategoryIds: jest.fn(),
  useCategoryPermissions: jest.fn(),
  useCreateCategory: jest.fn(),
}));

const mockUseTransactionsPage = useTransactionsPage as jest.MockedFunction<
  typeof useTransactionsPage
>;
const mockUseAccounts = useAccounts as jest.Mock;
const mockUseCategories = useCategories as jest.Mock;
const mockUseConnectivity = useConnectivity as jest.Mock;
const mockUseDeleteTransaction = useDeleteTransaction as jest.Mock;
const mockUseBulkCategoryApply = useBulkCategoryApply as jest.Mock;
const mockUseBulkCategoryPreview = useBulkCategoryPreview as jest.Mock;
const mockUseQueueBulkCategory = useQueueBulkCategory as jest.Mock;
const mockUseRecentCategoryIds = useRecentCategoryIds as jest.Mock;
const mockUseCategoryPermissions = useCategoryPermissions as jest.Mock;
const mockUseCreateCategory = useCreateCategory as jest.Mock;

const ACCOUNTS = [{ id: "acc-1", name: "Girokonto" }];
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
];

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

function pageQuery(overrides: Partial<ReturnType<typeof useTransactionsPage>> = {}) {
  return {
    items: [],
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    isFetchingNextPage: false,
    hasNextPage: false,
    fetchNextPage: jest.fn(),
    refetch: jest.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useTransactionsPage>;
}

async function renderScreen(): Promise<ReactTestRenderer> {
  let tree!: ReactTestRenderer;
  // @expo/vector-icons (in SearchField) laedt seine Schriftart asynchron -
  // siehe derselbe Kommentar in SearchField.test.tsx.
  await act(async () => {
    tree = create(<BuchungenScreen />);
  });
  return tree;
}

/** Findet eine OptionList-Zeile (accessibilityRole="radio") anhand ihres sichtbaren Textes. */
function findOptionRow(tree: ReactTestRenderer, label: string) {
  const row = tree.root
    .findAllByProps({ accessibilityRole: "radio" })
    .find((node) => node.findAllByType(Text).some((t) => t.props.children === label));
  if (!row) throw new Error(`Option "${label}" nicht gefunden`);
  return row;
}

function findPrimaryButton(tree: ReactTestRenderer, label: string) {
  const button = tree.root.findAllByType(PrimaryButton).find((node) => node.props.label === label);
  if (!button) throw new Error(`PrimaryButton "${label}" nicht gefunden`);
  return button;
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

describe("BuchungenScreen", () => {
  beforeEach(() => {
    mockUseAccounts.mockReturnValue({ data: ACCOUNTS });
    mockUseCategories.mockReturnValue({ data: CATEGORY_GROUPS });
    mockUseConnectivity.mockReturnValue(true);
    mockUseDeleteTransaction.mockReturnValue({ mutateAsync: jest.fn() });
    mockUseBulkCategoryApply.mockReturnValue({ mutateAsync: jest.fn() });
    mockUseBulkCategoryPreview.mockReturnValue({ mutateAsync: jest.fn() });
    mockUseQueueBulkCategory.mockReturnValue({ mutateAsync: jest.fn() });
    mockUseRecentCategoryIds.mockReturnValue([]);
    mockUseCategoryPermissions.mockReturnValue({ role: "owner", canManage: true, isOwner: true });
    mockUseCreateCategory.mockReturnValue({ isPending: false, mutateAsync: jest.fn() });
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  it("zeigt den Ladezustand, solange die erste Seite laedt", async () => {
    mockUseTransactionsPage.mockReturnValue(pageQuery({ isLoading: true }));

    const tree = await renderScreen();

    expect(tree.root.findByProps({ testID: "buchungen-loading" })).toBeTruthy();
    expect(tree.root.findAllByType(SectionList)).toHaveLength(0);
  });

  it("zeigt einen Fehlerzustand mit Retry-Control", async () => {
    const refetch = jest.fn();
    mockUseTransactionsPage.mockReturnValue(
      pageQuery({ isError: true, error: new Error("Netzwerkfehler"), refetch }),
    );

    const tree = await renderScreen();

    expect(tree.root.findByProps({ testID: "buchungen-error" })).toBeTruthy();
    expect(textsOf(tree)).toContain("Netzwerkfehler");

    const retryButton = tree.root.findByProps({ accessibilityLabel: "Erneut versuchen" });
    act(() => {
      retryButton.props.onPress();
    });
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("zeigt 'Keine Buchungen im <Monat>' und einen Import-Einstieg statt manueller Neuanlage", async () => {
    mockUseTransactionsPage.mockReturnValue(pageQuery());

    const tree = await renderScreen();
    const texts = textsOf(tree);
    const monthLabel = formatMonthLabel(new Date());

    expect(texts).toContain(`Keine Buchungen im ${monthLabel}`);
    // Der alte Empty-State-Hinweis auf manuelle Erfassung ("+ Neue Buchung")
    // darf nicht wiederkehren - bewusst als exakter Substring statt einer
    // groben Wortsuche, sonst schluege das auf den erlaubten Fliesstext
    // "Neue Buchungen kommen ueber den Kontoauszug-Import" fehlerhaft an.
    expect(texts.some((t) => t.includes("+ Neue Buchung"))).toBe(false);

    const importButton = findPrimaryButton(tree, "Kontoauszug importieren");
    act(() => {
      importButton.props.onPress();
    });
    expect(mockRouterPush).toHaveBeenCalledWith("/mehr/import");
  });

  it("unterscheidet 'Keine Treffer fuer diese Filter' von der leeren Ausgangsliste", async () => {
    jest.useFakeTimers();
    mockUseTransactionsPage.mockReturnValue(pageQuery());

    const tree = await renderScreen();
    const searchInput = tree.root.findByType(TextInput);

    act(() => {
      searchInput.props.onChangeText("Rewe");
    });
    act(() => {
      jest.advanceTimersByTime(300);
    });

    expect(tree.root.findByProps({ testID: "buchungen-filter-empty" })).toBeTruthy();
    expect(textsOf(tree)).toContain("Keine Treffer für diese Filter");
  });

  it("zeigt geladene Buchungen gegliedert und ohne jeden Weg zur manuellen Neuanlage", async () => {
    const fetchNextPage = jest.fn();
    mockUseTransactionsPage.mockReturnValue(
      pageQuery({
        items: [
          makeTransaction({ id: "t1", payee_label: "Rewe", amount_minor: -1200 }),
          makeTransaction({ id: "t2", payee_label: "Aldi", amount_minor: -500 }),
        ],
        hasNextPage: true,
        fetchNextPage,
      }),
    );

    const tree = await renderScreen();
    const texts = textsOf(tree);

    expect(texts).toContain("Rewe");
    expect(texts).toContain("Aldi");

    // Mindestens eine Tagesueberschrift (SectionHeader, accessibilityRole="header").
    expect(tree.root.findAllByProps({ accessibilityRole: "header" }).length).toBeGreaterThan(0);

    // Kein sichtbarer ODER im Accessibility-Tree verstecker Hinweis auf
    // manuelle Buchungsanlage (Abnahme: "kein Weg mehr ... manuell anzulegen").
    const mentionsManualEntry = tree.root
      .findAll(() => true)
      .some((node) => {
        const label = node.props.accessibilityLabel;
        return typeof label === "string" && label.includes("Neue Buchung");
      });
    expect(mentionsManualEntry).toBe(false);
    expect(texts.some((t) => t.includes("+ Neue Buchung"))).toBe(false);

    const sectionList = tree.root.findByType(SectionList);
    act(() => {
      sectionList.props.onEndReached();
    });
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

  it("aktualisiert den Filterzaehler und leert alle Filter beim Zuruecksetzen", async () => {
    mockUseTransactionsPage.mockReturnValue(pageQuery());

    const tree = await renderScreen();

    const filterButton = tree.root.findByProps({ accessibilityLabel: "Filter" });
    act(() => {
      filterButton.props.onPress();
    });

    // Richtung auf "Nur Einnahmen" stellen und anwenden.
    const incomeOption = findOptionRow(tree, "Nur Einnahmen");
    act(() => {
      incomeOption.props.onPress();
    });
    act(() => {
      findPrimaryButton(tree, "Anwenden").props.onPress();
    });

    expect(tree.root.findByProps({ accessibilityLabel: "Filter, 1 aktiv" })).toBeTruthy();
    expect(textsOf(tree)).toContain("Nur Einnahmen");

    const chipRemove = tree.root.findByProps({
      accessibilityLabel: "Nur Einnahmen, Filter entfernen",
    });
    act(() => {
      chipRemove.props.onPress();
    });

    expect(tree.root.findByProps({ accessibilityLabel: "Filter" })).toBeTruthy();
  });
});
