import { dimensionKey } from "./analytics";
import type { GridColumn } from "../components/datagrid/types";
import type { DimensionRef, FieldCatalog, FieldMeta } from "./types";

// Uebersetzung zwischen dem Feldkatalog des Servers und der Spaltenbeschreibung
// der Tabelle. Eine Stelle, damit Explorer-Kopfmenue, Filterauswahl und
// Gruppierungsleiste dieselbe Vorstellung davon haben, was ein Feld kann.

/**
 * Zeilenabfragen sortiert der Server ausschliesslich nach Datum oder Betrag
 * (`_compile_row_order`): jede andere Sortierung braeuchte einen zweiten
 * eindeutigen Schluessel, sonst waere die Keyset-Paginierung nicht stabil und
 * einzelne Zeilen verschwaenden zwischen zwei Seiten.
 */
export const ROW_SORTABLE_FIELDS = new Set(["date", "amount"]);

export const ROW_SORT_HINT =
  "Zeilen lassen sich nach Datum oder Betrag sortieren – andere Spalten erst nach einer Gruppierung.";

export function gridColumnFromField(field: FieldMeta): GridColumn {
  return {
    key: field.key,
    label: field.label,
    kind: field.kind,
    isMoney: field.is_money,
    filterable: field.filterable,
    groupable: field.groupable,
    // Facetten gibt es nur fuer gruppierbare Felder - fuer alles andere
    // lehnt `run_facets` die Anfrage ausdruecklich ab.
    hasValues: field.groupable && field.kind !== "num" && field.kind !== "date",
    sortable: ROW_SORTABLE_FIELDS.has(field.key),
    sortHint: ROW_SORT_HINT,
  };
}

const GRANULARITY_LABELS: Record<string, string> = {
  day: "Tag",
  week: "Woche",
  month: "Monat",
  quarter: "Quartal",
  year: "Jahr",
};

const GRANULARITIES = ["month", "quarter", "year", "week", "day"] as const;

/** Ein waehlbarer Eintrag der Gruppierungsleiste. */
export interface GroupOption {
  key: string;
  label: string;
  dimension: DimensionRef;
}

/**
 * Alle gruppierbaren Achsen. Datumsfelder erscheinen einmal je Koernung -
 * "nach Tag" und "nach Jahr" sind verschiedene Fragen, nicht dieselbe mit
 * einer Einstellung daneben.
 */
export function groupOptions(catalog: FieldCatalog | undefined): GroupOption[] {
  const options: GroupOption[] = [];
  for (const field of catalog?.fields ?? []) {
    if (!field.groupable) continue;
    if (field.kind === "date") {
      for (const granularity of GRANULARITIES) {
        const dimension: DimensionRef = { field: field.key, granularity };
        options.push({
          key: dimensionKey(dimension),
          label: `${field.label} (${GRANULARITY_LABELS[granularity]})`,
          dimension,
        });
      }
      continue;
    }
    options.push({ key: field.key, label: field.label, dimension: { field: field.key } });
  }
  return options;
}

/** `"date:month"` -> `{ field: "date", granularity: "month" }`. */
export function parseDimensionKey(key: string): DimensionRef {
  const [field, granularity] = key.split(":");
  return granularity
    ? { field: field ?? key, granularity: granularity as DimensionRef["granularity"] }
    : { field: field ?? key };
}
