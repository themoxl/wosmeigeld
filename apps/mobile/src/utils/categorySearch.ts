import type { Category, CategoryGroup } from "../api/categoryTypes";

// Reine Suchlogik der gemeinsamen Kategorieauswahl
// (docs/KATEGORISIERUNGSPLAN.md, "Eine gemeinsame, durchsuchbare
// Kategorieauswahl"). Bewusst ohne React/React-Native-Import: apps/mobile hat
// jest-expo, aber KEINE React-Native-Testing-Library - testbar ist deshalb nur
// pure Logik, und genau die liegt hier statt in der Sheet-Komponente.

/** Wie eine Kategorie im Picker angezeigt und durchsucht wird. */
export interface PickerCategory {
  id: string;
  name: string;
  groupId: string;
  groupName: string;
  /** `Gruppe · Kategorie` - Treffer zeigen immer den vollen Pfad. */
  path: string;
  synonyms: string[];
  isArchived: boolean;
  defaultType: string | null;
  icon: string | null;
  /** Vorberechneter Suchtext (normalisiert), damit nicht je Tastendruck neu normalisiert wird. */
  haystack: string;
}

export type PickerSectionKey = "none" | "selected" | "recent" | "group";

export interface PickerSection {
  key: string;
  kind: PickerSectionKey;
  title: string | null;
  items: PickerCategory[];
}

/** Trennzeichen des vollstaendigen Pfads, z. B. `Mobilitaet · OEPNV & Bahn`. */
const PATH_SEPARATOR = " · ";

const UMLAUT_MAP: Record<string, string> = {
  ä: "ae",
  ö: "oe",
  ü: "ue",
  ß: "ss",
  Ä: "ae",
  Ö: "oe",
  Ü: "ue",
  ẞ: "ss",
};

/**
 * Kombinierende Akzente (U+0300..U+036F), die nach normalize("NFD") als
 * eigene Zeichen hinter dem Grundbuchstaben stehen. Bewusst ueber
 * `new RegExp` mit Escapes statt als Literal: ein Literal mit unsichtbaren
 * kombinierenden Zeichen ueberlebt weder Code-Review noch Copy-Paste.
 */
const COMBINING_MARKS = new RegExp("[\\u0300-\\u036f]", "g");

/**
 * Normalisiert einen Suchtext so, dass `ä/ae`, `ö/oe`, `ü/ue` und `ß/ss`
 * gleichwertig behandelt werden (Plan: "Suchtexte behandeln zusaetzlich
 * ä/ae, ö/oe, ü/ue und ß/ss gleichwertig").
 *
 * Reihenfolge ist wichtig: erst die deutschen Sonderzeichen ausschreiben,
 * DANN die uebrigen Akzente per Unicode-Zerlegung entfernen. Andersherum
 * wuerde aus `ä` ein `a` und `Zaehler` faende `Zähler` nicht mehr.
 * Zusaetzlich werden Trennzeichen (`&`, `-`, `·`, `/`) zu Leerzeichen und
 * Mehrfachleerzeichen zusammengezogen, damit "oepnv bahn" auch
 * "OEPNV & Bahn" trifft.
 */
export function normalizeSearchText(value: string): string {
  let out = "";
  for (const char of value) {
    out += UMLAUT_MAP[char] ?? char;
  }
  return out
    .toLowerCase()
    .normalize("NFD")
    .replace(COMBINING_MARKS, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** `Gruppe · Kategorie`, der im Plan verbindliche Anzeigepfad. */
export function formatCategoryPath(groupName: string, categoryName: string): string {
  return `${groupName}${PATH_SEPARATOR}${categoryName}`;
}

function toPickerCategory(group: CategoryGroup, category: Category): PickerCategory {
  // Defensive Lesung: der persistierte Lesecache kann noch aus einer
  // App-Version ohne diese Felder stammen (siehe api/queryClient.ts).
  const synonyms = category.synonyms ?? [];
  const path = formatCategoryPath(group.name, category.name);
  return {
    id: category.id,
    name: category.name,
    groupId: group.id,
    groupName: group.name,
    path,
    synonyms,
    isArchived: category.is_archived,
    defaultType: category.default_type ?? null,
    icon: category.icon ?? null,
    haystack: normalizeSearchText(
      [group.name, category.name, ...synonyms, category.description ?? ""].join(" "),
    ),
  };
}

/** Alle Kategorien des Baums als flache, suchbare Liste (Gruppen- und Kategoriesortierung erhalten). */
export function flattenCategories(groups: CategoryGroup[]): PickerCategory[] {
  const sortedGroups = [...groups].sort((a, b) => a.sort_order - b.sort_order);
  const flat: PickerCategory[] = [];
  for (const group of sortedGroups) {
    const sorted = [...group.categories].sort(
      (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name, "de"),
    );
    for (const category of sorted) {
      flat.push(toPickerCategory(group, category));
    }
  }
  return flat;
}

/**
 * Trifft die Kategorie den (bereits normalisierten) Suchtext? Gesucht wird ab
 * dem ersten Zeichen ueber Gruppenname, Kategoriename und Synonyme. Mehrere
 * Suchwoerter muessen alle vorkommen, aber nicht in dieser Reihenfolge.
 */
export function categoryMatchesQuery(category: PickerCategory, normalizedQuery: string): boolean {
  if (normalizedQuery.length === 0) return true;
  return normalizedQuery.split(" ").every((token) => category.haystack.includes(token));
}

/**
 * Welche `default_type` passt zu Betrag/Richtung? Das priorisiert nur die
 * Reihenfolge - andere Kategorien werden NIE ausgeblendet, sonst waeren
 * Erstattungen und Korrekturbuchungen nicht mehr zuordenbar.
 */
export function preferredTypeForAmount(amountMinor: number | null | undefined): string | null {
  if (amountMinor === null || amountMinor === undefined || amountMinor === 0) return null;
  return amountMinor > 0 ? "income" : "expense";
}

export interface BuildPickerSectionsInput {
  groups: CategoryGroup[];
  /** Aktuell zugeordnete Kategorie (auch wenn archiviert). */
  value: string | null;
  /** Zuletzt verwendete Kategorien des Haushalts, neueste zuerst. */
  recentIds?: string[];
  /** Rohe Sucheingabe der Nutzerin. */
  query?: string;
  /** Betrag der Buchung in Cent - priorisiert passende Arten. */
  amountMinor?: number | null;
  /** Wie viele "zuletzt verwendet" hoechstens angezeigt werden. */
  recentLimit?: number;
}

export interface PickerModel {
  sections: PickerSection[];
  /** Alle Treffer ueber alle Abschnitte hinweg (ohne Dubletten). */
  matchCount: number;
  /** Die aktuell gewaehlte Kategorie, auch wenn sie archiviert ist. */
  selected: PickerCategory | null;
  /** true, wenn die aktuelle Zuordnung archiviert ist (nur lesbar, aenderbar auf aktiv). */
  selectedIsArchived: boolean;
}

/**
 * Baut die Abschnitte der Auswahl in der vom Plan vorgegebenen Reihenfolge:
 *
 *   1. `Ohne Kategorie`   (als eigener Abschnitt "none", ohne Eintraege - die
 *                          Sheet-Komponente rendert dafuer eine feste Zeile)
 *   2. aktuell gewaehlte Kategorie
 *   3. zuletzt verwendete Kategorien
 *   4. alle aktiven Kategorien, gruppiert und sortiert
 *
 * Archivierte Kategorien erscheinen NUR als aktuelle Zuordnung (Abschnitt 2),
 * nie in "zuletzt verwendet" oder in den Gruppen - sie sind nicht neu
 * waehlbar. Leere Gruppen erscheinen im Picker nicht.
 */
export function buildPickerSections(input: BuildPickerSectionsInput): PickerModel {
  const { groups, value, recentIds = [], query = "", amountMinor = null, recentLimit = 5 } = input;

  const all = flattenCategories(groups);
  const byId = new Map(all.map((category) => [category.id, category]));
  const normalizedQuery = normalizeSearchText(query);
  const preferredType = preferredTypeForAmount(amountMinor);

  const selected = value ? (byId.get(value) ?? null) : null;
  const matches = all.filter((category) => categoryMatchesQuery(category, normalizedQuery));
  const matchIds = new Set(matches.map((category) => category.id));

  const sections: PickerSection[] = [{ key: "none", kind: "none", title: null, items: [] }];

  if (selected && matchIds.has(selected.id)) {
    sections.push({
      key: "selected",
      kind: "selected",
      title: "Aktuell zugeordnet",
      items: [selected],
    });
  }

  const recent = recentIds
    .map((id) => byId.get(id))
    .filter(
      (category): category is PickerCategory =>
        category !== undefined &&
        !category.isArchived &&
        category.id !== selected?.id &&
        matchIds.has(category.id),
    )
    .slice(0, recentLimit);
  if (recent.length > 0) {
    sections.push({
      key: "recent",
      kind: "recent",
      title: "Zuletzt verwendet",
      items: recent,
    });
  }

  // Gruppenabschnitte: nur aktive Kategorien, und die passende Art zuerst.
  const grouped = new Map<string, PickerCategory[]>();
  for (const category of matches) {
    if (category.isArchived) continue;
    const bucket = grouped.get(category.groupId);
    if (bucket) bucket.push(category);
    else grouped.set(category.groupId, [category]);
  }
  for (const [groupId, items] of grouped) {
    const first = items[0];
    if (!first) continue;
    sections.push({
      key: `group:${groupId}`,
      kind: "group",
      title: first.groupName,
      items: preferredType ? sortByPreferredType(items, preferredType) : items,
    });
  }

  return {
    sections,
    matchCount: matches.filter((category) => !category.isArchived || category.id === value).length,
    selected,
    selectedIsArchived: selected?.isArchived ?? false,
  };
}

/** Stabile Sortierung: passende `default_type` nach vorn, sonst Reihenfolge unveraendert. */
function sortByPreferredType(items: PickerCategory[], preferredType: string): PickerCategory[] {
  const preferred: PickerCategory[] = [];
  const rest: PickerCategory[] = [];
  for (const item of items) {
    if (item.defaultType === preferredType) preferred.push(item);
    else rest.push(item);
  }
  return [...preferred, ...rest];
}

/**
 * Zuletzt verwendete Kategorien aus einer Buchungsliste ableiten (neueste
 * zuerst, ohne Dubletten). Split-Buchungen liefern keine eindeutige
 * Kategorie und werden uebersprungen.
 */
export function recentCategoryIdsFromTransactions(
  transactions: { category_id: string | null; is_split: boolean }[],
  limit = 5,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const transaction of transactions) {
    if (transaction.is_split) continue;
    const id = transaction.category_id;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
    if (out.length >= limit) break;
  }
  return out;
}

/** Anzeigename einer Kategorie-ID als voller Pfad; `null` -> "Ohne Kategorie". */
export function categoryPathById(groups: CategoryGroup[], categoryId: string | null): string {
  if (!categoryId) return "Ohne Kategorie";
  for (const group of groups) {
    for (const category of group.categories) {
      if (category.id === categoryId) return formatCategoryPath(group.name, category.name);
    }
  }
  return "Unbekannte Kategorie";
}
