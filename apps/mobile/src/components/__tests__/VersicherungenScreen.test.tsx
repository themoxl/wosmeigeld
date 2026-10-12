/// <reference types="jest" />
import { Text as MockRNText, Text } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type {
  InsuranceLine,
  InsuranceLineDetail,
  InsurancePolicy,
  InsurancePolicyDetail,
  InsuranceSummary,
  PolicyDeadline,
  RiskField,
} from "../../api/types";
import { useConnectivity } from "../../hooks/useConnectivity";
import { useContracts } from "../../hooks/useContracts";
import {
  useCreateInsurancePolicy,
  useInsuranceContext,
  useInsuranceLines,
  useInsurancePolicies,
  useInsurancePolicy,
  useInsuranceSummary,
} from "../../hooks/useInsurance";
import { useNotificationResponseRouter } from "../../notifications/useNotificationResponseRouter";
import { TextField } from "../TextField";
// Die drei Bildschirme liegen als Expo-Router-Routen unter app/; der Test
// bleibt bewusst ausserhalb von app/, damit expo-router ihn nicht selbst als
// Route einsammelt (siehe require.context in expo-router/entry.js - dort gibt
// es keinen eingebauten Ausschluss fuer *.test.tsx).
import VersicherungenScreen from "../../../app/mehr/versicherungen";
import VersicherungDetailScreen from "../../../app/versicherung/[id]";
import VersicherungNeuScreen from "../../../app/versicherung/neu";

// Component-Tests fuer die lesende Fassung des Versicherungsmoduls auf Mobile
// (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7, Phase P2b).
//
// Geprueft wird, was diesen Bildschirmen ihre Berechtigung gibt, und nicht,
// dass sie rendern: die Liste muss den Bestand ordnen und je Zeile sagen,
// wofuer die Police da ist; das Detail muss am Unfallort die Nummer und die
// Hotline OBEN haben; das Formular darf nicht mehr als vier Felder verlangen;
// und ein angetippter Push muss auf genau der Police landen, um die es geht,
// ohne dass auf dem Weg dorthin eine Gesundheitsangabe auf dem
// Sperrbildschirm gestanden hat.

const mockPush = jest.fn();
const mockBack = jest.fn();
let mockSearchParams: Record<string, string> = { id: "pol-hausrat" };

jest.mock("expo-router", () => ({
  Stack: {
    Screen: (props: { options?: { title?: string } }) => (
      <MockRNText>{props.options?.title ?? ""}</MockRNText>
    ),
  },
  useRouter: () => ({ push: mockPush, back: mockBack }),
  useLocalSearchParams: () => mockSearchParams,
}));

// @expo/vector-icons laedt seine Schrift asynchron nach und ist in der reinen
// Node-Testumgebung nicht sinnvoll simulierbar (siehe ListRow.test.tsx). Der
// Alias "MockRNText" ist bewusst so benannt: Jests Mock-Factory darf laut
// babel-plugin-jest-hoist nur auf Variablen ausserhalb ihres Scopes zugreifen,
// deren Name mit "mock" beginnt.
jest.mock("@expo/vector-icons", () => ({
  Ionicons: (props: { name: string; color?: string; size?: number }) => (
    <MockRNText {...props}>{`icon:${props.name}`}</MockRNText>
  ),
}));

// Die Zuhoerer werden erst beim Rendern eingetragen, die Fabrik greift also
// nicht schon beim Laden des Moduls auf die Liste zu.
const mockNotificationListeners: ((response: unknown) => void)[] = [];

jest.mock("expo-notifications", () => ({
  addNotificationResponseReceivedListener: (listener: (response: unknown) => void) => {
    mockNotificationListeners.push(listener);
    return { remove: jest.fn() };
  },
}));

jest.mock("../../hooks/useConnectivity", () => ({ useConnectivity: jest.fn() }));
jest.mock("../../hooks/useContracts", () => ({ useContracts: jest.fn() }));

jest.mock("../../hooks/useInsurance", () => {
  // Die Gliederung nach Risikobereich ist Datenformung und gehoert zum
  // Prueffall - nur die Abfragen selbst werden ersetzt.
  const actual = jest.requireActual("../../hooks/useInsurance");
  return {
    ...actual,
    useInsuranceContext: jest.fn(),
    useInsurancePolicies: jest.fn(),
    useInsurancePolicy: jest.fn(),
    useInsuranceLines: jest.fn(),
    useInsuranceSummary: jest.fn(),
    useCreateInsurancePolicy: jest.fn(),
  };
});

const mockUseConnectivity = useConnectivity as jest.MockedFunction<typeof useConnectivity>;
const mockUseContracts = useContracts as jest.MockedFunction<typeof useContracts>;
const mockUseInsuranceContext = useInsuranceContext as jest.MockedFunction<
  typeof useInsuranceContext
>;
const mockUseInsurancePolicies = useInsurancePolicies as jest.MockedFunction<
  typeof useInsurancePolicies
>;
const mockUseInsurancePolicy = useInsurancePolicy as jest.MockedFunction<typeof useInsurancePolicy>;
const mockUseInsuranceLines = useInsuranceLines as jest.MockedFunction<typeof useInsuranceLines>;
const mockUseInsuranceSummary = useInsuranceSummary as jest.MockedFunction<
  typeof useInsuranceSummary
>;
const mockUseCreateInsurancePolicy = useCreateInsurancePolicy as jest.MockedFunction<
  typeof useCreateInsurancePolicy
>;

// ---------------------------------------------------------------------------
// Testdaten
// ---------------------------------------------------------------------------

function makeDeadline(overrides: Partial<PolicyDeadline> = {}): PolicyDeadline {
  return {
    anchor: "main_due",
    anchor_basis_kind: "statute",
    deadline_kind: "kuendigungsfrist",
    certainty: "confirmed",
    term_ends_on: "2026-12-31",
    deadline_legal_on: "2026-09-30",
    safe_service_on: "2026-09-28",
    recommended_action_on: "2026-09-21",
    days_left: 47,
    reason: "",
    ...overrides,
  };
}

function makePolicy(overrides: Partial<InsurancePolicy> = {}): InsurancePolicy {
  return {
    id: "pol-hausrat",
    line_key: "hausrat",
    line_label: "Hausratversicherung",
    line_purpose_short: "Ersetzt Hab und Gut nach Feuer, Einbruch, Leitungswasser oder Sturm.",
    risk_field: "property",
    label: null,
    insurer_name: "Beispiel Versicherung AG",
    policy_number: "HR-4711/02",
    customer_number: null,
    tariff_name: null,
    purpose_note: null,
    status: "active",
    starts_on: "2020-04-01",
    ends_on: null,
    main_due_on: "2026-12-31",
    main_due_source: "user_confirmed",
    main_due_confirmed_at: "2026-01-05T10:00:00.000Z",
    notice_anchor: "main_due",
    anchor_basis_kind: "statute",
    notice_period_months: 3,
    notice_terms_source: "user_confirmed",
    deadline_kind: "kuendigungsfrist",
    min_term_months: null,
    renewal_term_months: 12,
    waiting_period_days: null,
    sum_insured_minor: null,
    deductible_minor: null,
    broker_name: null,
    broker_phone: null,
    claims_phone: "0800 1122 3344",
    service_channel: "post",
    recipient_state: "BY",
    cancelled_on: null,
    cancellation_reason: null,
    dormant_until: null,
    paid_up_benefit_minor: null,
    dynamic_agreed: false,
    attrs: {},
    data_source: "manual",
    line_confirmed_at: null,
    line_deferred_at: null,
    note: null,
    premium: { kind: "none" },
    deadline: makeDeadline(),
    completeness_filled: 6,
    completeness_total: 8,
    next_task: null,
    version: 1,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

const HAUSRAT = makePolicy();

const KFZ = makePolicy({
  id: "pol-kfz",
  line_key: "kfz_haftpflicht",
  line_label: "Kfz-Haftpflichtversicherung",
  line_purpose_short: "Zahlt Schäden, die Sie mit dem Auto anderen zufügen.",
  risk_field: "mobility",
  insurer_name: "Zweite Versicherung AG",
  policy_number: "KFZ-9002",
  main_due_source: "catalog_suggested",
  notice_terms_source: "catalog_suggested",
  deadline: makeDeadline({ certainty: "assumed", days_left: 47 }),
});

const ZAHN = makePolicy({
  id: "pol-zahn",
  line_key: "zahnzusatz",
  line_label: "Zahnzusatzversicherung",
  line_purpose_short: "Übernimmt einen Teil dessen, was die Kasse beim Zahnersatz offenlässt.",
  risk_field: "health",
  insurer_name: "Dritte Versicherung AG",
  status: "cancelled",
  main_due_on: null,
  main_due_source: "not_computable",
  deadline: makeDeadline({
    certainty: "not_computable",
    deadline_legal_on: null,
    safe_service_on: null,
    recommended_action_on: null,
    days_left: null,
    reason: "Ohne Hauptfälligkeit lässt sich kein Kündigungstermin berechnen.",
  }),
});

/** Ein Risikobereich, den der Server kennt und diese App noch nicht. */
const UNBEKANNTES_FELD = makePolicy({
  id: "pol-cyber",
  line_key: "cyber",
  line_label: "Cyberversicherung",
  line_purpose_short: "Hilft nach einem Angriff auf Konten und Geräte.",
  risk_field: "cyber" as RiskField,
  insurer_name: "Vierte Versicherung AG",
});

function makeSummary(overrides: Partial<InsuranceSummary> = {}): InsuranceSummary {
  return {
    policy_count: 3,
    active_count: 2,
    yearly_in_fixed_costs_minor: 84000,
    yearly_paid_by_others_minor: 24000,
    paid_by_others_breakdown: { employer: 24000 },
    no_premium_count: 1,
    unknown_line_count: 1,
    deferred_line_count: 0,
    missing_main_due_count: 1,
    unconfirmed_main_due_count: 1,
    next_deadline_on: "2026-09-30",
    next_deadline_policy_id: "pol-hausrat",
    ...overrides,
  };
}

function makeLine(overrides: Partial<InsuranceLine> = {}): InsuranceLine {
  return {
    line_key: "hausrat",
    risk_field: "property",
    gdv_sparte: "030",
    label: "Hausratversicherung",
    purpose_short: "Ersetzt Hab und Gut nach Feuer, Einbruch, Leitungswasser oder Sturm.",
    purpose_long: "Ersetzt bewegliche Sachen im Haushalt nach Feuer, Einbruch, Leitungswasser.",
    typically_covers: ["Möbel", "Kleidung"],
    typically_not_covers: ["Das Gebäude selbst"],
    is_placeholder: false,
    need_tier: "basis",
    need_reason: null,
    source_url: null,
    source_checked_on: "2026-01-15",
    default_notice_anchor: "main_due",
    default_anchor_basis_kind: "statute",
    default_notice_period_months: 3,
    default_main_due_md: "01-01",
    default_min_term_months: 12,
    default_deadline_kind: "kuendigungsfrist",
    special_law: "",
    completeness_fields: [],
    covered_by_keys: [],
    requires_object_kind: null,
    sort_order: 10,
    ...overrides,
  };
}

const KATALOG: InsuranceLine[] = [
  makeLine(),
  makeLine({
    line_key: "kfz_haftpflicht",
    risk_field: "mobility",
    label: "Kfz-Haftpflichtversicherung",
    purpose_short: "Zahlt Schäden, die Sie mit dem Auto anderen zufügen.",
    sort_order: 20,
  }),
  // Der Platzhalter aus dem Katalog: er darf nicht doppelt erscheinen.
  makeLine({
    line_key: "unknown",
    risk_field: "other",
    label: "Noch nicht zugeordnet",
    is_placeholder: true,
    sort_order: 999,
  }),
];

const HAUSRAT_LINE_DETAIL: InsuranceLineDetail = {
  ...makeLine(),
  attr_schema: {},
};

function makeDetail(policy: InsurancePolicy): InsurancePolicyDetail {
  return { policy, line: HAUSRAT_LINE_DETAIL, completeness_missing: ["main_due_on"] };
}

// ---------------------------------------------------------------------------
// Werkzeug
// ---------------------------------------------------------------------------

function flattenChildren(children: unknown): string {
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(flattenChildren).join("");
  return "";
}

function textsOf(tree: ReactTestRenderer): string[] {
  return tree.root.findAllByType(Text).map((node) => flattenChildren(node.props.children));
}

function indexOfText(tree: ReactTestRenderer, needle: string | RegExp): number {
  return textsOf(tree).findIndex((text) =>
    typeof needle === "string" ? text === needle : needle.test(text),
  );
}

function queryResult<T>(data: T | undefined, overrides: Record<string, unknown> = {}) {
  return {
    data,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: jest.fn(),
    ...overrides,
  };
}

/** Ein Textfeld ueber seine Beschriftung ausfuellen. */
function fillField(tree: ReactTestRenderer, label: string, value: string): void {
  const field = tree.root.findAllByType(TextField).find((node) => node.props.label === label);
  if (!field) throw new Error(`Kein Textfeld mit der Beschriftung "${label}" gefunden.`);
  act(() => {
    field.props.onChangeText(value);
  });
}

/** Eine Zeile der OptionList ueber ihren sichtbaren Text antippen. */
function pressOption(tree: ReactTestRenderer, label: string): void {
  const option = tree.root
    .findAll(
      (node) =>
        node.props.accessibilityRole === "radio" && typeof node.props.onPress === "function",
    )
    .find((node) =>
      node.findAllByType(Text).some((text) => flattenChildren(text.props.children).includes(label)),
    );
  if (!option) throw new Error(`Keine Auswahlzeile mit dem Text "${label}" gefunden.`);
  act(() => {
    option.props.onPress();
  });
}

// SectionList/ScrollView (ueber VirtualizedList) planen beim Mounten echte
// setTimeout-Aufrufe fuer die Zellenberechnung. Ohne explizites unmount()
// ueberleben diese Timer das Testende und React protokolliert danach eine
// "act(...)"-Warnung, die den ganzen Jest-Lauf als fehlgeschlagen meldet.
let renderedTree: ReactTestRenderer | undefined;

function renderScreen(element: React.JSX.Element): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(element);
  });
  renderedTree = tree!;
  return tree!;
}

beforeEach(() => {
  mockPush.mockClear();
  mockBack.mockClear();
  mockNotificationListeners.length = 0;
  mockSearchParams = { id: "pol-hausrat" };
  mockUseConnectivity.mockReturnValue(true);
  mockUseInsuranceContext.mockReturnValue({
    accessToken: "token",
    householdId: "h-1",
    enabled: true,
    canEdit: true,
  });
  mockUseInsurancePolicies.mockReturnValue(
    queryResult([ZAHN, HAUSRAT, KFZ]) as unknown as ReturnType<typeof useInsurancePolicies>,
  );
  mockUseInsuranceSummary.mockReturnValue(
    queryResult(makeSummary()) as unknown as ReturnType<typeof useInsuranceSummary>,
  );
  mockUseInsurancePolicy.mockReturnValue(
    queryResult(makeDetail(HAUSRAT)) as unknown as ReturnType<typeof useInsurancePolicy>,
  );
  mockUseInsuranceLines.mockReturnValue(
    queryResult(KATALOG) as unknown as ReturnType<typeof useInsuranceLines>,
  );
  mockUseContracts.mockReturnValue(queryResult([]) as unknown as ReturnType<typeof useContracts>);
  mockUseCreateInsurancePolicy.mockReturnValue({
    mutate: jest.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCreateInsurancePolicy>);
});

afterEach(() => {
  act(() => {
    renderedTree?.unmount();
  });
  renderedTree = undefined;
  jest.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Liste
// ---------------------------------------------------------------------------

describe("VersicherungenScreen (Liste)", () => {
  it("gliedert den Bestand nach Risikobereich, in fester Reihenfolge", () => {
    const tree = renderScreen(<VersicherungenScreen />);

    const gesundheit = indexOfText(tree, "Gesundheit");
    const habUndGut = indexOfText(tree, "Hab und Gut");
    const mobilitaet = indexOfText(tree, "Mobilität");

    // Alltagsworte statt Branchenbegriffen, und die Reihenfolge kommt aus
    // RISK_FIELD_ORDER - nicht aus der Anzahl im Bestand, sonst saehe die
    // Liste in jedem Haushalt anders aus.
    expect(gesundheit).toBeGreaterThan(-1);
    expect(gesundheit).toBeLessThan(habUndGut);
    expect(habUndGut).toBeLessThan(mobilitaet);
    expect(textsOf(tree)).not.toContain("Sachversicherung");
  });

  it("zeigt je Zeile den Zwecktext der Sparte", () => {
    const tree = renderScreen(<VersicherungenScreen />);
    const texts = textsOf(tree);

    // "Hausratversicherung" allein beantwortet nicht, wofuer sie da ist.
    expect(texts).toContain("Ersetzt Hab und Gut nach Feuer, Einbruch, Leitungswasser oder Sturm.");
    expect(texts).toContain("Zahlt Schäden, die Sie mit dem Auto anderen zufügen.");
  });

  it("traegt ohne eigene Benennung Sparte UND Versicherer, damit zwei gleiche Sparten unterscheidbar bleiben", () => {
    const zweiteHausrat = makePolicy({
      id: "pol-hausrat-2",
      insurer_name: "Zweite Gesellschaft AG",
    });
    mockUseInsurancePolicies.mockReturnValue(
      queryResult([HAUSRAT, zweiteHausrat]) as unknown as ReturnType<typeof useInsurancePolicies>,
    );

    const texts = textsOf(renderScreen(<VersicherungenScreen />));

    expect(texts).toContain("Hausratversicherung · Beispiel Versicherung AG");
    expect(texts).toContain("Hausratversicherung · Zweite Gesellschaft AG");
  });

  it("stellt den Status vor den Zwecktext, zeigt 'aktiv' aber nicht an", () => {
    const texts = textsOf(renderScreen(<VersicherungenScreen />)).join(" | ");

    // Eine gekuendigte Police, die aussieht wie eine laufende, ist die
    // gefaehrlichste Zeile dieser Liste.
    expect(texts).toMatch(/gekündigt · Übernimmt einen Teil dessen/);
    expect(texts).not.toMatch(/aktiv · Ersetzt Hab und Gut/);
  });

  it("zeigt die Frist rechts in der Zeile - mit Countdown nur bei bestaetigtem Datum", () => {
    const texts = textsOf(renderScreen(<VersicherungenScreen />));

    // Hausrat: bestaetigt. Kfz: angenommen, gleiche Tageszahl an der Police.
    expect(texts).toContain("noch 47 Tage");
    expect(texts.filter((text) => text === "noch 47 Tage")).toHaveLength(1);
    expect(texts).toContain("angenommen");
    // Zahnzusatz: kein berechenbarer Termin.
    expect(texts).toContain("Angabe fehlt");
  });

  it("oeffnet beim Antippen einer Zeile das Policendetail", () => {
    const tree = renderScreen(<VersicherungenScreen />);
    const row = tree.root.find(
      (node) =>
        node.props.accessibilityRole === "link" &&
        typeof node.props.accessibilityLabel === "string" &&
        node.props.accessibilityLabel.startsWith("Kfz-Haftpflichtversicherung") &&
        typeof node.props.onPress === "function",
    );

    act(() => {
      row.props.onPress();
    });

    expect(mockPush).toHaveBeenCalledWith("/versicherung/pol-kfz");
  });

  it("verschluckt ein unbekanntes Risikofeld nicht, sondern stellt es ans Ende", () => {
    mockUseInsurancePolicies.mockReturnValue(
      queryResult([UNBEKANNTES_FELD, HAUSRAT]) as unknown as ReturnType<
        typeof useInsurancePolicies
      >,
    );

    const tree = renderScreen(<VersicherungenScreen />);
    const texts = textsOf(tree);

    // Genau die eine Police, die man am Schadenstag sucht, koennte sonst fehlen.
    expect(texts).toContain("Cyberversicherung · Vierte Versicherung AG");
    expect(indexOfText(tree, "Hab und Gut")).toBeLessThan(indexOfText(tree, "cyber"));
  });

  it("nennt die Kennzahlen samt ihrer Unschaerfe und addiert Fremdbeitraege nicht mit", () => {
    const tree = renderScreen(<VersicherungenScreen />);
    const texts = textsOf(tree).join(" | ");
    // Beschriftung und Betrag sind seit dem Designsystem zwei Textknoten -
    // <Money> setzt den Betrag in Monoschrift. Fuer die Frage, ob der Satz
    // zusammenhaengend lesbar ist, zaehlt nur, dass sie unmittelbar
    // aufeinander folgen; der Knotentrenner wird deshalb zum Leerzeichen.
    const readAsSentence = textsOf(tree).join(" ");

    expect(texts).toMatch(/3 Policen · 2 aktiv/);
    expect(readAsSentence).toMatch(/Jahresbeitrag in den Fixkosten: 840,00 €/);
    expect(readAsSentence).toMatch(
      /Von Dritten gezahlt: 240,00 € pro Jahr, nicht in den Fixkosten/,
    );
    expect(texts).toMatch(
      /Offen: 1 Police ohne Sparte · 2 Termine ohne bestätigte Hauptfälligkeit/,
    );
    // Die Summe aus beiden Toepfen waere die erste Zahl, die jemand dafuer haelt.
    expect(texts).not.toMatch(/1\.080,00 €/);
  });

  it("nennt offline die Folge, nicht den Zustand", () => {
    mockUseConnectivity.mockReturnValue(false);
    mockUseInsurancePolicies.mockReturnValue(
      queryResult(undefined) as unknown as ReturnType<typeof useInsurancePolicies>,
    );

    const texts = textsOf(renderScreen(<VersicherungenScreen />)).join(" | ");

    expect(texts).toMatch(/Versicherungsdaten liegen aus Datenschutzgründen nicht auf dem Gerät/);
  });

  it("behauptet offline NICHT, es sei nichts erfasst - die App kennt den Bestand dann nur nicht", () => {
    // Der Regelfall dieses Moduls ohne Netz: die Abfrage pausiert
    // (networkMode "online"), und zwischengespeichert wird bewusst nichts
    // (Plan 7.1). isLoading und isError sind dabei beide false, `data` ist
    // undefined - genau der Zustand, in dem ein Leerzustand mit "Noch keine
    // Versicherung erfasst" einem Haushalt mit zwoelf Policen widerspricht.
    mockUseConnectivity.mockReturnValue(false);
    mockUseInsurancePolicies.mockReturnValue(
      queryResult(undefined) as unknown as ReturnType<typeof useInsurancePolicies>,
    );

    const texts = textsOf(renderScreen(<VersicherungenScreen />)).join(" | ");

    expect(texts).not.toMatch(/Noch keine Versicherung erfasst/);
    expect(texts).toMatch(/Bestand offline nicht abrufbar/);
  });

  it("nennt den leeren Bestand beim Namen, wenn der Server ihn wirklich leer meldet", () => {
    mockUseInsurancePolicies.mockReturnValue(
      queryResult([]) as unknown as ReturnType<typeof useInsurancePolicies>,
    );

    const texts = textsOf(renderScreen(<VersicherungenScreen />)).join(" | ");

    expect(texts).toMatch(/Noch keine Versicherung erfasst/);
    expect(texts).not.toMatch(/Bestand offline nicht abrufbar/);
  });
});

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

describe("VersicherungDetailScreen", () => {
  it("stellt den Ernstfallblock nach oben - vor Zweck, Frist und Beitrag", () => {
    const tree = renderScreen(<VersicherungDetailScreen />);

    const ernstfall = indexOfText(tree, "Im Ernstfall");
    const zweck = indexOfText(tree, "Wofür ist diese Art von Versicherung da?");
    const frist = indexOfText(tree, "Frist");
    const beitrag = indexOfText(tree, "Beitrag");

    expect(ernstfall).toBeGreaterThan(-1);
    // Wer eine Police auf dem Telefon oeffnet, braucht dort keine Erklaerung
    // einer Sparte, sondern eine Telefonnummer.
    expect(ernstfall).toBeLessThan(zweck);
    expect(zweck).toBeLessThan(frist);
    expect(frist).toBeLessThan(beitrag);
  });

  it("enthaelt im Ernstfallblock Versicherer, Versicherungsschein und Schadenhotline", () => {
    const tree = renderScreen(<VersicherungDetailScreen />);
    const texts = textsOf(tree);

    expect(texts).toContain("Versicherer");
    expect(texts).toContain("Beispiel Versicherung AG");
    expect(texts).toContain("Versicherungsschein");
    expect(texts).toContain("HR-4711/02");
    expect(texts).toContain("Schaden melden");
    expect(texts).toContain("0800 1122 3344");

    // Alle drei stehen VOR dem Zwecktext, nicht bloss irgendwo auf der Seite.
    const zweck = indexOfText(tree, "Wofür ist diese Art von Versicherung da?");
    expect(indexOfText(tree, "Beispiel Versicherung AG")).toBeLessThan(zweck);
    expect(indexOfText(tree, "HR-4711/02")).toBeLessThan(zweck);
    expect(indexOfText(tree, "0800 1122 3344")).toBeLessThan(zweck);
  });

  it("macht die Schadenhotline waehlbar, auch mit Leerzeichen in der Nummer", () => {
    const tree = renderScreen(<VersicherungDetailScreen />);

    const call = tree.root.findByProps({
      accessibilityLabel: "Schaden melden 0800 1122 3344 anrufen",
    });
    expect(call).toBeDefined();
  });

  it("benennt fehlende Ernstfallangaben ausdruecklich statt sie wegzulassen", () => {
    mockUseInsurancePolicy.mockReturnValue(
      queryResult(
        makeDetail(makePolicy({ policy_number: null, claims_phone: null })),
      ) as unknown as ReturnType<typeof useInsurancePolicy>,
    );

    const texts = textsOf(renderScreen(<VersicherungDetailScreen />));

    // Eine leere Zeile sieht aus wie "nichts noetig", nicht wie "hier fehlt was".
    expect(texts).toContain("keine Nummer erfasst");
    expect(texts).toContain("keine Hotline hinterlegt");
  });

  it("stellt den Vorbehalt UEBER den Zwecktext, nicht als Fussnote darunter", () => {
    const tree = renderScreen(<VersicherungDetailScreen />);

    const caveat = indexOfText(tree, /Ihre konkrete Police kann abweichen/);
    const purposeLong = indexOfText(tree, HAUSRAT_LINE_DETAIL.purpose_long);

    expect(caveat).toBeGreaterThan(-1);
    expect(caveat).toBeLessThan(purposeLong);
  });

  it("benennt die Grenze zur Webansicht, statt den Nutzer suchen zu lassen", () => {
    const texts = textsOf(renderScreen(<VersicherungDetailScreen />)).join(" | ");

    expect(texts).toMatch(/Bearbeiten, Termine bestätigen, Deckungen und Beteiligte pflegen/);
  });
});

// ---------------------------------------------------------------------------
// Anlegen
// ---------------------------------------------------------------------------

/**
 * Die 24 weiteren Felder, die `InsurancePolicyCreate` erlaubt. Der Prueffall
 * lautet "genau vier Felder - nicht mehr", und der laesst sich nur so
 * schreiben: eine blosse Zaehlung wuerde gruen bleiben, wenn jemand ein Feld
 * gegen ein anderes tauscht.
 */
const NICHT_IM_FORMULAR = [
  "Bezeichnung",
  "Kundennummer",
  "Tarif",
  "Status",
  "Beginn",
  "Ende",
  "Hauptfälligkeit",
  "Kündigungsanker",
  "Kündigungsfrist",
  "Mindestlaufzeit",
  "Verlängerung",
  "Wartezeit",
  "Versicherungssumme",
  "Selbstbeteiligung",
  "Vermittler",
  "Schadenhotline",
  "Zustellweg",
  "Bundesland",
  "Dynamik",
  "Notiz",
];

describe("VersicherungNeuScreen", () => {
  it("verlangt genau vier Felder - nicht mehr", () => {
    const tree = renderScreen(<VersicherungNeuScreen />);
    const texts = textsOf(tree);

    // Zwei Textfelder, zwei Auswahlfelder: Sparte, Versicherer,
    // Policennummer, Geldfall.
    expect(tree.root.findAllByType(TextField).map((node) => node.props.label)).toEqual([
      "Versicherer",
      "Policennummer (optional)",
    ]);
    expect(texts).toContain("Sparte");
    expect(texts).toContain("Geldfall");

    for (const feld of NICHT_IM_FORMULAR) {
      expect(texts).not.toContain(feld);
    }
  });

  it("bietet 'Sparte noch offen' als ausdrueckliche Wahl statt als stillen Vorgabewert", () => {
    const tree = renderScreen(<VersicherungNeuScreen />);
    const texts = textsOf(tree);

    expect(texts).toContain("Sparte noch offen");
    // Der Platzhalter aus dem Katalog darf nicht zusaetzlich in seiner
    // Risikogruppe auftauchen.
    expect(texts).not.toContain("Noch nicht zugeordnet");
  });

  it("behandelt den Geldfall als EIN Feld: die Anschlussangaben folgen der Wahl", () => {
    const tree = renderScreen(<VersicherungNeuScreen />);

    expect(tree.root.findAllByType(TextField)).toHaveLength(2);

    pressOption(tree, "Jemand anderes zahlt");

    // "Betrag" ist kein fuenftes Feld, sondern die Konsequenz des vierten -
    // getrennte Felder "Betrag" und "Zahler" liessen sich widerspruechlich
    // ausfuellen, genau das lehnt der Server mit
    // insurance_premium_source_conflict ab.
    expect(tree.root.findAllByType(TextField).map((node) => node.props.label)).toEqual([
      "Versicherer",
      "Policennummer (optional)",
      "Betrag",
    ]);
    expect(textsOf(tree)).toContain("Wer zahlt");
  });

  it("sendet genau die vier Angaben ab", () => {
    const mutate = jest.fn();
    mockUseCreateInsurancePolicy.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateInsurancePolicy>);

    const tree = renderScreen(<VersicherungNeuScreen />);
    pressOption(tree, "Hausratversicherung");
    fillField(tree, "Versicherer", "Beispiel Versicherung AG");
    fillField(tree, "Policennummer (optional)", "HR-4711/02");
    pressOption(tree, "Kein Beitrag hinterlegt");
    act(() => {
      tree.root.findByProps({ accessibilityRole: "button" }).props.onPress();
    });

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0][0]).toEqual({
      lineKey: "hausrat",
      insurerName: "Beispiel Versicherung AG",
      policyNumber: "HR-4711/02",
      premium: { kind: "none" },
    });
  });

  it("besteht auf einer ausdruecklichen Sparte, statt still 'unknown' zu senden", () => {
    const mutate = jest.fn();
    mockUseCreateInsurancePolicy.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateInsurancePolicy>);

    const tree = renderScreen(<VersicherungNeuScreen />);
    fillField(tree, "Versicherer", "Beispiel Versicherung AG");
    act(() => {
      tree.root.findByProps({ accessibilityRole: "button" }).props.onPress();
    });

    expect(mutate).not.toHaveBeenCalled();
    expect(textsOf(tree).join(" | ")).toMatch(/Bitte eine Sparte wählen/);
  });

  it("sagt einer Rolle ohne Schreibrecht, dass sie hier nichts anlegen kann", () => {
    mockUseInsuranceContext.mockReturnValue({
      accessToken: "token",
      householdId: "h-1",
      enabled: true,
      canEdit: false,
    });

    const texts = textsOf(renderScreen(<VersicherungNeuScreen />));

    expect(texts).toContain("Zum Anlegen fehlen die Rechte in diesem Haushalt.");
    expect(texts).not.toContain("Geldfall");
  });
});

// ---------------------------------------------------------------------------
// Push
// ---------------------------------------------------------------------------

function PushHarness() {
  useNotificationResponseRouter();
  return null;
}

function deliverPush(data: Record<string, unknown>): void {
  renderScreen(<PushHarness />);
  act(() => {
    for (const listener of mockNotificationListeners) {
      listener({ notification: { request: { content: { data } } } });
    }
  });
}

describe("Push-Benachrichtigungen des Versicherungsmoduls", () => {
  it("fuehrt von insurance_deadline_ordinary in genau die betroffene Police", () => {
    deliverPush({ type: "insurance_deadline_ordinary", policy_id: "pol-kfz" });

    expect(mockPush).toHaveBeenCalledWith("/versicherung/pol-kfz");
    expect(mockPush).toHaveBeenCalledTimes(1);
  });

  it("fuehrt auch die zusammengefasste Rueckfrage auf die erste betroffene Police", () => {
    deliverPush({
      type: "insurance_main_due_question",
      policy_id: "pol-zahn",
      policy_count: 3,
    });

    expect(mockPush).toHaveBeenCalledWith("/versicherung/pol-zahn");
  });

  it("faengt eine Nachricht ohne verwertbare Policen-ID auf der Uebersicht auf", () => {
    // Geloeschte Police oder aeltere Nachricht: eine Detailseite zu einer
    // unbekannten ID waere eine Fehlermeldung.
    deliverPush({ type: "insurance_deadline_ordinary" });

    expect(mockPush).toHaveBeenCalledWith("/mehr/versicherungen");
  });

  it("laesst fremde Benachrichtigungstypen unberuehrt", () => {
    deliverPush({ type: "recurring_due", contract_id: "c-1" });

    expect(mockPush).toHaveBeenCalledWith("/vertrag/c-1");
    expect(mockPush).not.toHaveBeenCalledWith("/mehr/versicherungen");
  });

  it("akzeptiert die Nutzlast des Workers wortlos - sie traegt nur Typ, ID und Anzahl", () => {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});

    // Deckungsgleich mit build_push_messages in
    // services/worker/worker/notifications/insurance_alerts.py.
    deliverPush({ type: "insurance_main_due_question", policy_id: "pol-zahn", policy_count: 2 });

    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("schlaegt an, wenn Betrag, Person oder Versicherungsart mitkaemen", () => {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});

    deliverPush({
      type: "insurance_deadline_ordinary",
      policy_id: "pol-zahn",
      premium_minor: 4990,
      person_name: "Anna",
      line_label: "Zahnzusatzversicherung",
    });

    expect(warn).toHaveBeenCalledTimes(1);
    const logged = (warn.mock.calls[0] ?? []).join(" ");
    // Gemeldet werden die Feld-NAMEN. Eine Warnung, die den Betrag oder den
    // Namen mitschreibt, waere genau das Leck, das sie meldet.
    expect(logged).toMatch(/premium_minor/);
    expect(logged).toMatch(/person_name/);
    expect(logged).toMatch(/line_label/);
    expect(logged).not.toMatch(/4990/);
    expect(logged).not.toMatch(/Anna/);
    expect(logged).not.toMatch(/Zahnzusatzversicherung/);

    // Geroutet wird trotzdem, und zwar nur ueber die opake Policen-ID.
    expect(mockPush).toHaveBeenCalledWith("/versicherung/pol-zahn");
    warn.mockRestore();
  });
});
