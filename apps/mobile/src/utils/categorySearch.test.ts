/// <reference types="jest" />
import type { Category, CategoryGroup } from "../api/categoryTypes";
import {
  buildPickerSections,
  categoryMatchesQuery,
  categoryPathById,
  flattenCategories,
  formatCategoryPath,
  normalizeSearchText,
  preferredTypeForAmount,
  recentCategoryIdsFromTransactions,
} from "./categorySearch";

function category(overrides: Partial<Category> & Pick<Category, "id" | "name">): Category {
  return {
    group_id: "g1",
    default_type: "expense",
    budget_bucket: null,
    is_archived: false,
    sort_order: 0,
    synonyms: [],
    ...overrides,
  };
}

const MOBILITAET: CategoryGroup = {
  id: "g-mob",
  name: "Mobilität",
  sort_order: 1,
  categories: [
    category({
      id: "c-oepnv",
      group_id: "g-mob",
      name: "ÖPNV & Bahn",
      sort_order: 0,
      synonyms: ["Deutschlandticket", "BVG"],
    }),
    category({ id: "c-tanken", group_id: "g-mob", name: "Tanken & Laden", sort_order: 1 }),
  ],
};

const EINNAHMEN: CategoryGroup = {
  id: "g-ein",
  name: "Einnahmen",
  sort_order: 0,
  categories: [
    category({
      id: "c-gehalt",
      group_id: "g-ein",
      name: "Gehalt & Lohn",
      default_type: "income",
      sort_order: 0,
    }),
  ],
};

const ALT: CategoryGroup = {
  id: "g-alt",
  name: "Altlasten",
  sort_order: 2,
  categories: [
    category({ id: "c-freizeit", group_id: "g-alt", name: "Freizeit", is_archived: true }),
  ],
};

const GROUPS = [MOBILITAET, EINNAHMEN, ALT];

describe("normalizeSearchText", () => {
  it("behandelt Umlaute und ihre Umschrift gleichwertig", () => {
    expect(normalizeSearchText("ÖPNV")).toBe(normalizeSearchText("oepnv"));
    expect(normalizeSearchText("Mobilität")).toBe(normalizeSearchText("Mobilitaet"));
    expect(normalizeSearchText("Fuß")).toBe(normalizeSearchText("fuss"));
    expect(normalizeSearchText("Grüße")).toBe(normalizeSearchText("gruesse"));
  });

  it("macht aus einem Umlaut nicht versehentlich den nackten Vokal", () => {
    // Wuerde erst NFD zerlegt und dann alle Akzente entfernt, waere "ä" -> "a"
    // und die deutsche Umschrift "ae" wuerde nicht mehr treffen.
    expect(normalizeSearchText("Zähler")).toBe("zaehler");
  });

  it("entfernt uebrige Akzente anderer Sprachen", () => {
    expect(normalizeSearchText("Café")).toBe("cafe");
  });

  it("ersetzt Trennzeichen durch genau ein Leerzeichen", () => {
    expect(normalizeSearchText("  ÖPNV  &   Bahn ")).toBe("oepnv bahn");
    expect(normalizeSearchText("Mobilität · ÖPNV & Bahn")).toBe("mobilitaet oepnv bahn");
  });
});

describe("formatCategoryPath / categoryPathById", () => {
  it("zeigt immer den vollstaendigen Pfad", () => {
    expect(formatCategoryPath("Mobilität", "ÖPNV & Bahn")).toBe("Mobilität · ÖPNV & Bahn");
    expect(categoryPathById(GROUPS, "c-oepnv")).toBe("Mobilität · ÖPNV & Bahn");
  });

  it("faellt fuer null und unbekannte IDs verstaendlich zurueck", () => {
    expect(categoryPathById(GROUPS, null)).toBe("Ohne Kategorie");
    expect(categoryPathById(GROUPS, "gibt-es-nicht")).toBe("Unbekannte Kategorie");
  });
});

describe("flattenCategories", () => {
  it("sortiert Gruppen und Kategorien nach sort_order", () => {
    expect(flattenCategories(GROUPS).map((c) => c.id)).toEqual([
      "c-gehalt",
      "c-oepnv",
      "c-tanken",
      "c-freizeit",
    ]);
  });

  it("liest fehlende additive Felder defensiv (alter Cache ohne synonyms)", () => {
    const legacy: CategoryGroup = {
      id: "g",
      name: "Alt",
      sort_order: 0,
      // Bewusst ohne synonyms/sort_order/icon - so sieht ein persistierter
      // Cache aus, der vor der Kategorienverwaltung geschrieben wurde.
      categories: [
        {
          id: "c",
          group_id: "g",
          name: "Sonstiges",
          default_type: null,
          budget_bucket: null,
          is_archived: false,
        },
      ],
    };
    const [flat] = flattenCategories([legacy]);
    expect(flat?.synonyms).toEqual([]);
    expect(flat?.icon).toBeNull();
  });
});

describe("categoryMatchesQuery", () => {
  const [oepnv] = flattenCategories([MOBILITAET]);

  it("trifft ab dem ersten Zeichen", () => {
    expect(oepnv && categoryMatchesQuery(oepnv, normalizeSearchText("ö"))).toBe(true);
  });

  it("trifft ueber die Umschrift", () => {
    expect(oepnv && categoryMatchesQuery(oepnv, normalizeSearchText("oepnv"))).toBe(true);
  });

  it("trifft ueber Synonyme", () => {
    expect(oepnv && categoryMatchesQuery(oepnv, normalizeSearchText("deutschlandticket"))).toBe(
      true,
    );
  });

  it("trifft ueber den Gruppennamen", () => {
    expect(oepnv && categoryMatchesQuery(oepnv, normalizeSearchText("mobilitaet"))).toBe(true);
  });

  it("verlangt alle Suchwoerter, aber nicht deren Reihenfolge", () => {
    expect(oepnv && categoryMatchesQuery(oepnv, normalizeSearchText("bahn oepnv"))).toBe(true);
    expect(oepnv && categoryMatchesQuery(oepnv, normalizeSearchText("bahn taxi"))).toBe(false);
  });

  it("leere Suche trifft alles", () => {
    expect(oepnv && categoryMatchesQuery(oepnv, "")).toBe(true);
  });
});

describe("preferredTypeForAmount", () => {
  it("leitet die Richtung aus dem Vorzeichen ab", () => {
    expect(preferredTypeForAmount(-1500)).toBe("expense");
    expect(preferredTypeForAmount(1500)).toBe("income");
  });

  it("priorisiert nichts ohne Betrag", () => {
    expect(preferredTypeForAmount(null)).toBeNull();
    expect(preferredTypeForAmount(undefined)).toBeNull();
    expect(preferredTypeForAmount(0)).toBeNull();
  });
});

describe("buildPickerSections", () => {
  it("haelt die Reihenfolge Ohne Kategorie -> gewaehlt -> zuletzt -> Gruppen ein", () => {
    const model = buildPickerSections({
      groups: GROUPS,
      value: "c-tanken",
      recentIds: ["c-gehalt"],
    });
    expect(model.sections.map((s) => s.kind)).toEqual([
      "none",
      "selected",
      "recent",
      "group",
      "group",
    ]);
    expect(model.sections[1]?.items[0]?.id).toBe("c-tanken");
    expect(model.sections[2]?.items[0]?.id).toBe("c-gehalt");
  });

  it("zeigt keine leeren Gruppen", () => {
    const leer: CategoryGroup = { id: "g-leer", name: "Leer", sort_order: 9, categories: [] };
    const model = buildPickerSections({ groups: [...GROUPS, leer], value: null });
    expect(model.sections.some((s) => s.title === "Leer")).toBe(false);
  });

  it("blendet archivierte Kategorien aus den Gruppen aus", () => {
    const model = buildPickerSections({ groups: GROUPS, value: null });
    const groupIds = model.sections
      .flatMap((s) => (s.kind === "group" ? s.items : []))
      .map((c) => c.id);
    expect(groupIds).not.toContain("c-freizeit");
  });

  it("zeigt eine archivierte aktuelle Zuordnung weiterhin als gewaehlt an", () => {
    const model = buildPickerSections({ groups: GROUPS, value: "c-freizeit" });
    expect(model.selectedIsArchived).toBe(true);
    expect(model.sections[1]?.kind).toBe("selected");
    expect(model.sections[1]?.items[0]?.id).toBe("c-freizeit");
  });

  it("nimmt archivierte Kategorien nicht in 'zuletzt verwendet' auf", () => {
    const model = buildPickerSections({
      groups: GROUPS,
      value: null,
      recentIds: ["c-freizeit", "c-oepnv"],
    });
    const recent = model.sections.find((s) => s.kind === "recent");
    expect(recent?.items.map((c) => c.id)).toEqual(["c-oepnv"]);
  });

  it("dupliziert die gewaehlte Kategorie nicht in 'zuletzt verwendet'", () => {
    const model = buildPickerSections({
      groups: GROUPS,
      value: "c-oepnv",
      recentIds: ["c-oepnv", "c-tanken"],
    });
    const recent = model.sections.find((s) => s.kind === "recent");
    expect(recent?.items.map((c) => c.id)).toEqual(["c-tanken"]);
  });

  it("filtert alle Abschnitte ueber die Suche", () => {
    const model = buildPickerSections({
      groups: GROUPS,
      value: "c-tanken",
      recentIds: ["c-gehalt"],
      query: "bahn",
    });
    expect(model.matchCount).toBe(1);
    expect(model.sections.map((s) => s.kind)).toEqual(["none", "group"]);
    expect(model.sections[1]?.items.map((c) => c.id)).toEqual(["c-oepnv"]);
  });

  it("meldet eine erfolglose Suche ueber matchCount 0", () => {
    const model = buildPickerSections({ groups: GROUPS, value: null, query: "Hufschmied" });
    expect(model.matchCount).toBe(0);
    expect(model.sections.filter((s) => s.kind === "group")).toHaveLength(0);
  });

  it("priorisiert die passende Art, versteckt aber keine andere", () => {
    const gemischt: CategoryGroup = {
      id: "g-mix",
      name: "Gemischt",
      sort_order: 0,
      categories: [
        category({ id: "c-aus", group_id: "g-mix", name: "Ausgabe", sort_order: 0 }),
        category({
          id: "c-ein",
          group_id: "g-mix",
          name: "Einnahme",
          default_type: "income",
          sort_order: 1,
        }),
      ],
    };
    const model = buildPickerSections({ groups: [gemischt], value: null, amountMinor: 2500 });
    const items = model.sections.find((s) => s.kind === "group")?.items ?? [];
    expect(items.map((c) => c.id)).toEqual(["c-ein", "c-aus"]);
    expect(items).toHaveLength(2);
  });

  it("begrenzt 'zuletzt verwendet'", () => {
    const model = buildPickerSections({
      groups: GROUPS,
      value: null,
      recentIds: ["c-gehalt", "c-oepnv", "c-tanken"],
      recentLimit: 2,
    });
    expect(model.sections.find((s) => s.kind === "recent")?.items).toHaveLength(2);
  });
});

describe("recentCategoryIdsFromTransactions", () => {
  it("liefert die zuletzt verwendeten Kategorien ohne Dubletten", () => {
    const ids = recentCategoryIdsFromTransactions([
      { category_id: "a", is_split: false },
      { category_id: "b", is_split: false },
      { category_id: "a", is_split: false },
      { category_id: null, is_split: false },
      { category_id: "c", is_split: false },
    ]);
    expect(ids).toEqual(["a", "b", "c"]);
  });

  it("ueberspringt Split-Buchungen - deren Kategorie ist nicht eindeutig", () => {
    const ids = recentCategoryIdsFromTransactions([
      { category_id: "split", is_split: true },
      { category_id: "a", is_split: false },
    ]);
    expect(ids).toEqual(["a"]);
  });

  it("respektiert das Limit", () => {
    const ids = recentCategoryIdsFromTransactions(
      [
        { category_id: "a", is_split: false },
        { category_id: "b", is_split: false },
        { category_id: "c", is_split: false },
      ],
      2,
    );
    expect(ids).toEqual(["a", "b"]);
  });
});
