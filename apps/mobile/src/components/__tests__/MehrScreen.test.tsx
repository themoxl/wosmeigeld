/// <reference types="jest" />
import { Text, Text as MockRNText } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ReviewInboxResponse } from "../../api/types";
import { useAuth } from "../../auth/AuthProvider";
// Seit der Umstellung auf das Handy-Designsystem baut der Mehr-Tab aus
// <Card> + <DataRow> (docs/MOBILE-DESIGNSYSTEM.md). Die Zeile traegt keinen href mehr,
// sondern navigiert ueber onPress.
import { Badge, Card, DataRow } from "../../components/ui";
import { isHostElementOfType } from "../../components/testUtils";
import { useReviewInbox } from "../../hooks/useReview";
import { useQueueConflicts } from "../../hooks/useTransactionQueue";
import MehrScreen from "../../../app/(tabs)/mehr";

// Component-Tests fuer die neue Informationsarchitektur auf Mehr
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 8 und 12): genau vier
// verstaendliche Gruppen, Badge auf "Buchungen pruefen" ueber denselben
// Review-Inbox-Datenstand wie Heute, Sync-Konflikt-Hinweis nur bei
// vorhandenen Konflikten und ein ruhiger Accountblock im Footer.

const mockPush = jest.fn();
jest.mock("expo-router", () => ({
  useRouter: () => ({ push: mockPush }),
}));

jest.mock("../../auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("../../hooks/useReview", () => ({ useReviewInbox: jest.fn() }));
jest.mock("../../hooks/useTransactionQueue", () => ({ useQueueConflicts: jest.fn() }));

// @expo/vector-icons laedt seine Schrift asynchron nach und ist in der
// reinen Node-Testumgebung nicht sinnvoll simulierbar (siehe ListRow.test.tsx).
// Der Alias "MockRNText" ist bewusst so benannt: Jests Mock-Factory darf laut
// babel-plugin-jest-hoist nur auf Variablen ausserhalb ihres Scopes
// zugreifen, deren Name mit "mock" beginnt.
jest.mock("@expo/vector-icons", () => ({
  Ionicons: (props: { name: string; color?: string; size?: number }) => (
    <MockRNText {...props}>{`icon:${props.name}`}</MockRNText>
  ),
}));

const mockUseAuth = useAuth as jest.Mock;
const mockUseReviewInbox = useReviewInbox as jest.Mock;
const mockUseQueueConflicts = useQueueConflicts as jest.Mock;

function queryResult<T>(
  overrides: Partial<{ data: T | undefined; isLoading: boolean; isError: boolean }> = {},
) {
  return { data: undefined, isLoading: false, isError: false, ...overrides };
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

function textsOf(tree: ReactTestRenderer): unknown[] {
  return tree.root.findAllByType(Text).map((node) => node.props.children);
}

function applyDefaults() {
  mockUseAuth.mockReturnValue({ signOut: jest.fn(), userEmail: "max@example.com" });
  mockUseReviewInbox.mockReturnValue(queryResult({ data: makeReviewInbox() }));
  mockUseQueueConflicts.mockReturnValue([]);
}

let renderedTrees: ReactTestRenderer[] = [];

function renderScreen(): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(<MehrScreen />);
  });
  renderedTrees.push(tree!);
  return tree!;
}

/** Die Menuezeile zu einer Route: einmal antippen und schauen, wohin es geht. */
function routeOf(row: { props: { onPress?: () => void } }): string | undefined {
  mockPush.mockClear();
  act(() => {
    row.props.onPress?.();
  });
  return mockPush.mock.calls[0]?.[0] as string | undefined;
}

function menuRows(tree: ReactTestRenderer) {
  return tree.root.findAllByType(DataRow);
}

function rowForRoute(tree: ReactTestRenderer, href: string) {
  const row = menuRows(tree).find((node) => routeOf(node) === href);
  if (!row) throw new Error(`Keine Menuezeile fuer ${href} gefunden`);
  return row;
}

const EXPECTED_HREFS = [
  // Die Buchungsliste hat seit dem Redesign keinen eigenen Tab mehr und steht
  // deshalb hier ganz oben (app/(tabs)/_layout.tsx).
  "/buchungen",
  "/mehr/vorschau",
  // "Berichte" steht bewusst NICHT mehr in dieser Liste: der Bildschirm ist
  // seit dem Redesign ein Bottom-Tab (docs/MOBILE-DESIGNSYSTEM.md, Abschnitt 6).
  "/mehr/konten",
  // Versicherungen steht bewusst neben "Konten" und nicht bei den
  // Buchungswerkzeugen: beides sind Verzeichnisse, die nachgeschlagen statt
  // bearbeitet werden (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7.1).
  "/mehr/versicherungen",
  "/mehr/kategorien",
  "/mehr/import",
  "/mehr/review",
  "/mehr/sparziele",
  "/mehr/vorhaben",
  "/mehr/vermoegen",
  "/mehr/essensplan",
  "/mehr/einkaufsliste",
  "/mehr/einstellungen",
  "/mehr/status",
];

describe("MehrScreen", () => {
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

  it("zeigt genau vier verstaendliche Gruppen ueber accessibilityRole='header'", () => {
    const tree = renderScreen();
    // Nur Host-Text-Knoten zaehlen: die zusammengesetzte <Text>-Komponente
    // reicht accessibilityRole/children unveraendert an ihr Host-Element
    // durch, eine lose Props-Suche ueber den ganzen Baum wuerde also jede
    // Kopfzeile doppelt zaehlen (siehe testUtils.isHostElementOfType).
    const headers = tree.root.findAll(
      (node) =>
        isHostElementOfType(node, "Text") &&
        node.props.accessibilityRole === "header" &&
        typeof node.props.children === "string",
    );
    expect(headers.map((node) => node.props.children)).toEqual([
      "Finanzen verwalten",
      "Ziele & Vermögen",
      "Alltag",
      "App",
    ]);
  });

  it("zeigt jeden Eintrag genau einmal, ohne doppelte oder verwaiste Routen", () => {
    const tree = renderScreen();
    // Geprueft wird das TATSAECHLICHE Navigationsziel: jede Zeile wird einmal
    // ausgeloest und der Aufruf an den Router mitgeschrieben. Das ist strenger
    // als der frueher abgelesene href-Prop, weil eine Zeile ohne funktionierende
    // Navigation dabei auffliegt.
    const hrefs = menuRows(tree).map((row) => routeOf(row));

    expect(hrefs).toEqual(EXPECTED_HREFS);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });

  it("meldet jede Menuezeile als Link, damit ein Ortswechsel angesagt wird", () => {
    const tree = renderScreen();
    const roles = menuRows(tree).map((row) => row.props.accessibilityRole);

    expect(roles).toEqual(EXPECTED_HREFS.map(() => "link"));
  });

  it("zeigt keine rohen '>'-Zeichen, sondern echte Chevron-Icons", () => {
    const tree = renderScreen();
    expect(textsOf(tree)).not.toContain(">");
    // Nur Host-Text-Knoten zaehlen (siehe Kommentar oben) - der gemockte
    // Ionicons-Wrapper reicht seine Children ebenfalls an ein Host-Text-
    // Element durch.
    const chevrons = tree.root.findAll(
      (node) => isHostElementOfType(node, "Text") && node.props.children === "icon:chevron-forward",
    );
    // 14 Navigationszeilen; die Sync-Konflikt-Zeile ist bei 0 Konflikten ausgeblendet.
    expect(chevrons.length).toBe(EXPECTED_HREFS.length);
  });

  it("Einkaufsliste ist kein eingerueckter Unterpunkt mehr", () => {
    const tree = renderScreen();
    const einkaufsliste = rowForRoute(tree, "/mehr/einkaufsliste");
    const essensplan = rowForRoute(tree, "/mehr/essensplan");

    // Gleiche Gruppe, gleiche Bauform: beide Zeilen bringen ihre Symbolkachel
    // selbst mit und keine von beiden bekommt einen zusaetzlichen linken
    // Einzug ueber ein abweichendes leading-Element.
    expect(einkaufsliste.props.leading).toBeUndefined();
    expect(essensplan.props.leading).toBeUndefined();
    expect(einkaufsliste.props.avatarIcon).toBe("cart-outline");
  });

  it("zeigt kein Badge auf 'Buchungen pruefen', wenn keine offenen Faelle vorliegen", () => {
    mockUseReviewInbox.mockReturnValue(queryResult({ data: makeReviewInbox({ total_count: 0 }) }));

    const tree = renderScreen();
    const reviewRow = rowForRoute(tree, "/mehr/review");
    expect(reviewRow.props.trailing).toBeUndefined();
    expect(reviewRow.props.accessibilityLabel).toBe("Buchungen prüfen");
  });

  it("zeigt ein Badge mit der Anzahl offener Faelle auf 'Buchungen pruefen'", () => {
    mockUseReviewInbox.mockReturnValue(queryResult({ data: makeReviewInbox({ total_count: 4 }) }));

    const tree = renderScreen();
    const reviewRow = rowForRoute(tree, "/mehr/review");

    // Die Zahl steht sichtbar als Pille in der Zeile ...
    const badge = reviewRow.findByType(Badge);
    expect(badge.props.label).toBe("4 offen");
    expect(badge.props.tone).toBe("warning");
    // ... und Farbe und Position sind nie alleiniger Bedeutungstraeger.
    expect(reviewRow.props.accessibilityLabel).toBe("Buchungen prüfen, 4 offen");
  });

  it("fasst jede Gruppe in genau eine Flaeche mit durchlaufenden Trennlinien", () => {
    const tree = renderScreen();
    // Nur die Karten, die Menuezeilen tragen - die Abmeldekarte im Fuss und
    // ein moegliches Konfliktbanner sind ebenfalls <Card>.
    const groups = tree.root
      .findAllByType(Card)
      .filter((card) => card.findAllByType(DataRow).length > 0);

    expect(groups).toHaveLength(4);
    expect(groups.map((group) => group.findAllByType(DataRow).length)).toEqual([7, 3, 2, 2]);

    // n Zeilen ergeben n-1 Trennlinien: die letzte Zeile einer Gruppe traegt
    // keine, damit am Kartenrand keine doppelte Kante steht.
    const withDivider = menuRows(tree).filter((row) => row.props.divider === true);
    expect(withDivider).toHaveLength(EXPECTED_HREFS.length - groups.length);
  });

  it("zeigt kein Badge, solange die Review-Inbox noch laedt", () => {
    mockUseReviewInbox.mockReturnValue(queryResult({ isLoading: true }));

    const tree = renderScreen();
    expect(rowForRoute(tree, "/mehr/review").props.trailing).toBeUndefined();
  });

  it("blendet den Sync-Konflikt-Hinweis vollstaendig aus, wenn keine Konflikte vorliegen", () => {
    mockUseQueueConflicts.mockReturnValue([]);

    const tree = renderScreen();
    expect(textsOf(tree).join(" ")).not.toMatch(/Sync-Konflikt/);
  });

  it("zeigt eine kompakte korallfarbene Hinweiszeile bei mehreren Sync-Konflikten und oeffnet sie", () => {
    mockUseQueueConflicts.mockReturnValue([
      { id: "1", status: "conflict" },
      { id: "2", status: "conflict" },
    ]);

    const tree = renderScreen();
    expect(textsOf(tree)).toContain("2 Sync-Konflikte lösen");

    const row = tree.root.findByProps({ accessibilityLabel: "2 Sync-Konflikte lösen" });
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith("/mehr/sync-konflikte");
  });

  it("verwendet den Singular fuer genau einen Sync-Konflikt", () => {
    mockUseQueueConflicts.mockReturnValue([{ id: "1", status: "conflict" }]);

    const tree = renderScreen();
    expect(textsOf(tree)).toContain("1 Sync-Konflikt lösen");
  });

  it("zeigt einen ruhigen Accountblock mit 'Angemeldet als', E-Mail und separatem Abmelden-Button", () => {
    const signOut = jest.fn();
    mockUseAuth.mockReturnValue({ signOut, userEmail: "max@example.com" });

    const tree = renderScreen();
    const texts = textsOf(tree);
    expect(texts).toContain("Angemeldet als");
    expect(texts).toContain("max@example.com");
    expect(texts).toContain("Abmelden");

    // Keine offenen Sync-Konflikte in diesem Test: die einzige Pressable mit
    // accessibilityRole="button" ist der destruktive Abmelden-Button.
    const logoutButton = tree.root.findByProps({ accessibilityRole: "button" });
    act(() => {
      logoutButton.props.onPress();
    });
    expect(signOut).toHaveBeenCalled();
  });
});
