import type { AnalyticsColumn } from "../../lib/types";

// Gemeinsame Spaltenbeschreibung fuer beide Tabellen (Explorer und
// Buchungsregister).
//
// Bewusst nahe am Feldkatalog des Servers (`FieldMeta`): der Explorer
// uebernimmt ihn eins zu eins, das Register beschreibt seine Spalten von Hand
// in derselben Form. Dadurch verhalten sich Sortierung, Filter und
// Gruppierung an beiden Stellen gleich, obwohl die eine Tabelle serverseitig
// und die andere im Browser filtert.

export interface GridColumn {
  /** Feldschluessel - identisch mit dem Schluessel im Filterbaum. */
  key: string;
  label: string;
  kind: AnalyticsColumn["kind"];
  isMoney?: boolean;
  sortable?: boolean;
  filterable?: boolean;
  groupable?: boolean;
  /** Ob das Menue eine Werteliste (Facetten) anbietet. */
  hasValues?: boolean;
  /**
   * Grund, warum die Spalte nicht sortierbar ist. Wird im Menue angezeigt -
   * ein grundlos fehlender Menuepunkt liest sich wie ein Fehler.
   */
  sortHint?: string;
}

/** Ein Eintrag der Werteliste. `null` ist ein echter Wert ("ohne Angabe"). */
export interface GridValueOption {
  value: string | null;
  label: string;
  count?: number;
}

export type SortDirection = "asc" | "desc";
