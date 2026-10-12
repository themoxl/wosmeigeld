/// <reference types="jest" />
import type { CategoryGroup } from "../api/categoryTypes";
import type { Contract, Person } from "../api/types";
import {
  groupContractsByCategory,
  groupContractsByPerson,
  rateFixedCostsRatio,
  UNASSIGNED_PERSON_SECTION_KEY,
  UNASSIGNED_PERSON_SECTION_LABEL,
  UNCATEGORIZED_SECTION_KEY,
  UNCATEGORIZED_SECTION_LABEL,
} from "./useContracts";

// Kategoriegliederung der Vertragsliste (Redesign-Plan Abschnitt 7,
// "FlatList wird zu SectionList ... Sections pro konkreter Kategorie").
// Nur die reine Sortier-/Gruppierlogik wird hier getestet - das Rendering
// steht in src/components/__tests__/VertraegeScreen.test.tsx.

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

function makePerson(id: string, name: string, sortOrder: number, isActive = true): Person {
  return {
    id,
    household_id: "h-1",
    name,
    member_user_id: null,
    sort_order: sortOrder,
    is_active: isActive,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
  };
}

function makeCategory(id: string, name: string, sortOrder: number) {
  return {
    id,
    group_id: "",
    name,
    default_type: null,
    budget_bucket: null,
    is_archived: false,
    sort_order: sortOrder,
  };
}

const MOBILITAET: CategoryGroup = {
  id: "g-mobilitaet",
  name: "Mobilität",
  sort_order: 2,
  categories: [
    { ...makeCategory("cat-auto", "Auto", 1), group_id: "g-mobilitaet" },
    { ...makeCategory("cat-oepnv", "ÖPNV", 0), group_id: "g-mobilitaet" },
  ],
};

const WOHNEN: CategoryGroup = {
  id: "g-wohnen",
  name: "Wohnen",
  sort_order: 1,
  categories: [{ ...makeCategory("cat-miete", "Miete", 0), group_id: "g-wohnen" }],
};

describe("groupContractsByCategory", () => {
  it("ordnet Sections nach Kategoriegruppen- dann Kategorie-Sortierung", () => {
    const contracts = [
      makeContract({ id: "auto", name: "KFZ-Versicherung", category_id: "cat-auto" }),
      makeContract({ id: "miete", name: "Miete", category_id: "cat-miete" }),
      makeContract({ id: "oepnv", name: "Deutschlandticket", category_id: "cat-oepnv" }),
    ];

    const sections = groupContractsByCategory(contracts, [MOBILITAET, WOHNEN]);

    // Wohnen (Gruppenordnung 1) vor Mobilitaet (2); innerhalb Mobilitaet
    // OEPNV (Kategorieordnung 0) vor Auto (1).
    expect(sections.map((section) => section.title)).toEqual(["Miete", "ÖPNV", "Auto"]);
  });

  it("sammelt Vertraege ohne Kategorie und mit unbekannter Kategorie-ID gemeinsam als letzte Section", () => {
    const contracts = [
      makeContract({ id: "keine", name: "Ohne Zuordnung", category_id: null }),
      makeContract({ id: "miete", name: "Miete", category_id: "cat-miete" }),
      makeContract({
        id: "unbekannt",
        name: "Archivierte Kategorie",
        category_id: "cat-geloescht",
      }),
    ];

    const sections = groupContractsByCategory(contracts, [WOHNEN]);

    expect(sections.map((section) => section.key)).toEqual([
      "cat-miete",
      UNCATEGORIZED_SECTION_KEY,
    ]);
    const last = sections[sections.length - 1]!;
    expect(last.title).toBe(UNCATEGORIZED_SECTION_LABEL);
    // Gleiche naechste Faelligkeit in beiden Vertraegen -> Tie-Breaker Name:
    // "Archivierte Kategorie" vor "Ohne Zuordnung".
    expect(last.data.map((contract) => contract.id)).toEqual(["unbekannt", "keine"]);
  });

  it("sortiert innerhalb einer Section nach naechster Faelligkeit, dann nach Name", () => {
    const contracts = [
      makeContract({
        id: "spaet",
        name: "Z-Versicherung",
        category_id: "cat-auto",
        next_due_date: "2026-09-01",
      }),
      makeContract({
        id: "frueh-b",
        name: "Bahncard",
        category_id: "cat-auto",
        next_due_date: "2026-08-01",
      }),
      makeContract({
        id: "frueh-a",
        name: "Autoversicherung",
        category_id: "cat-auto",
        next_due_date: "2026-08-01",
      }),
    ];

    const sections = groupContractsByCategory(contracts, [MOBILITAET]);
    const autoSection = sections.find((section) => section.key === "cat-auto")!;

    expect(autoSection.data.map((contract) => contract.id)).toEqual([
      "frueh-a",
      "frueh-b",
      "spaet",
    ]);
  });

  it("liefert keine Sections, wenn keine Vertraege vorhanden sind", () => {
    expect(groupContractsByCategory([], [WOHNEN, MOBILITAET])).toEqual([]);
  });

  it("gruppiert mehrere Vertraege innerhalb derselben Kategorie ohne Dubletten und ohne Vertraege zu verlieren", () => {
    const contracts = [
      makeContract({ id: "1", name: "Strom", category_id: "cat-miete" }),
      makeContract({ id: "2", name: "Wasser", category_id: "cat-miete" }),
      makeContract({ id: "3", name: "Sonstiges", category_id: null }),
    ];

    const sections = groupContractsByCategory(contracts, [WOHNEN]);
    const allIds = sections.flatMap((section) => section.data.map((contract) => contract.id));

    expect(allIds.sort()).toEqual(["1", "2", "3"]);
    expect(sections).toHaveLength(2);
  });
});

// Personengliederung der Einnahmenliste (Liquiditaetsplan Stufe 6). Dieselbe
// Trennung wie oben: hier nur Sortier- und Gruppierlogik, das Rendering steht
// in src/components/__tests__/VertraegeScreen.test.tsx.
describe("groupContractsByPerson", () => {
  const MAX = makePerson("p-max", "Max", 0);
  const ANNA = makePerson("p-anna", "Anna", 1);

  it("ordnet Sections nach der Reihenfolge der Personenliste, nicht nach dem Namen", () => {
    const contracts = [
      makeContract({ id: "anna", name: "Gehalt Anna", direction: "income", person_id: "p-anna" }),
      makeContract({ id: "max", name: "Gehalt Max", direction: "income", person_id: "p-max" }),
    ];

    const sections = groupContractsByPerson(contracts, [MAX, ANNA]);

    // sort_order 0 vor 1 - alphabetisch stuende Anna vorn.
    expect(sections.map((section) => section.title)).toEqual(["Max", "Anna"]);
  });

  it("sammelt Einnahmen ohne und mit unbekannter Person gemeinsam als letzte Section", () => {
    const contracts = [
      makeContract({ id: "ohne", name: "Nebenjob", direction: "income", person_id: null }),
      makeContract({ id: "max", name: "Gehalt Max", direction: "income", person_id: "p-max" }),
      makeContract({
        id: "unbekannt",
        name: "Alte Einnahme",
        direction: "income",
        person_id: "p-geloescht",
      }),
    ];

    const sections = groupContractsByPerson(contracts, [MAX]);

    expect(sections.map((section) => section.key)).toEqual([
      "p-max",
      UNASSIGNED_PERSON_SECTION_KEY,
    ]);
    const last = sections[sections.length - 1]!;
    expect(last.title).toBe(UNASSIGNED_PERSON_SECTION_LABEL);
    // Gleiche Faelligkeit -> Tie-Breaker Name.
    expect(last.data.map((contract) => contract.id)).toEqual(["unbekannt", "ohne"]);
  });

  it("behaelt eine geloeschte Person mit ihrem Namen als eigene Section", () => {
    // Loeschen ist serverseitig ein Soft-Delete: der Vertrag behaelt seine
    // person_id. Landete er unter "Ohne Person", waere das schlicht falsch.
    const geloescht = makePerson("p-alt", "Ehemalige", 2, false);
    const contracts = [
      makeContract({ id: "alt", name: "Unterhalt", direction: "income", person_id: "p-alt" }),
    ];

    const sections = groupContractsByPerson(contracts, [MAX, geloescht]);

    expect(sections.map((section) => section.title)).toEqual(["Ehemalige"]);
  });

  it("sortiert innerhalb einer Person nach naechster Faelligkeit, dann nach Name", () => {
    const contracts = [
      makeContract({
        id: "spaet",
        name: "Bonus",
        direction: "income",
        person_id: "p-max",
        next_due_date: "2026-09-01",
      }),
      makeContract({
        id: "frueh-b",
        name: "Nebenjob",
        direction: "income",
        person_id: "p-max",
        next_due_date: "2026-08-01",
      }),
      makeContract({
        id: "frueh-a",
        name: "Gehalt",
        direction: "income",
        person_id: "p-max",
        next_due_date: "2026-08-01",
      }),
    ];

    const sections = groupContractsByPerson(contracts, [MAX]);

    expect(sections[0]!.data.map((contract) => contract.id)).toEqual([
      "frueh-a",
      "frueh-b",
      "spaet",
    ]);
  });

  it("liefert keine Sections ohne Vertraege und verliert keinen Vertrag", () => {
    expect(groupContractsByPerson([], [MAX, ANNA])).toEqual([]);

    const contracts = [
      makeContract({ id: "1", direction: "income", person_id: "p-max" }),
      makeContract({ id: "2", direction: "income", person_id: "p-max" }),
      makeContract({ id: "3", direction: "income", person_id: null }),
    ];
    const sections = groupContractsByPerson(contracts, [MAX]);
    expect(sections.flatMap((section) => section.data.map((c) => c.id)).sort()).toEqual([
      "1",
      "2",
      "3",
    ]);
    expect(sections).toHaveLength(2);
  });
});

// rateFixedCostsRatio ist bereits ueber FixedCostsRatioCard.test.tsx indirekt
// abgedeckt; hier nur ein direkter Smoke-Test, da die Funktion jetzt aus
// demselben Modul wie die neue Gruppierlogik importiert wird.
describe("rateFixedCostsRatio", () => {
  it("meldet 'Keine Vertraege' ohne Fixkosten", () => {
    expect(rateFixedCostsRatio(0, 0)).toEqual({ tone: "neutral", label: "Keine Verträge" });
  });
});
