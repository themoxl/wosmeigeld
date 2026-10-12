import {
  MAX_BULK_TRANSACTIONS,
  type BulkCategoryItem,
  type BulkCategoryResult,
  type BulkSkipReason,
} from "../api/categoryTypes";

// Reine Auswertung der Mehrfachzuordnung (docs/KATEGORISIERUNGSPLAN.md,
// "Manuelle Mehrfachzuordnung"). Der Plan verlangt sowohl vor dem Anwenden
// eine Vorschau als auch danach eine Rueckmeldung pro Buchung - beides
// benutzt dieselbe Zusammenfassung, deshalb liegt sie hier und nicht im
// Screen.

export { MAX_BULK_TRANSACTIONS };

/**
 * Maschinenlesbare Gruende des Servers in die Sprache der Nutzerin
 * uebersetzt. Ohne Umlaute, wie im uebrigen App-Text.
 */
export const BULK_SKIP_REASON_LABELS: Record<BulkSkipReason, string> = {
  split_transaction: "Split-Buchung",
  already_assigned: "bereits so zugeordnet",
  version_conflict: "zwischenzeitlich geändert",
  not_found: "nicht gefunden",
  archived_transaction: "gelöscht",
  category_inactive: "Kategorie archiviert",
};

export function describeBulkSkipReason(reason: BulkSkipReason | null | undefined): string {
  if (!reason) return "ohne Angabe";
  return BULK_SKIP_REASON_LABELS[reason] ?? reason;
}

export interface BulkResultSummary {
  applied: number;
  unchanged: number;
  skipped: number;
  conflict: number;
  total: number;
  /**
   * Eine Zeile je nicht angewendetem Grund, z. B. "3x Split-Buchung".
   * Angewendete Buchungen brauchen keine Begruendung.
   */
  reasonLines: string[];
  /** Einzeiler fuer Kopfzeile/Alert. */
  headline: string;
}

/**
 * Fasst ein Preview- oder Apply-Ergebnis zusammen.
 *
 * Bewusst NICHT aus den Zaehlern allein: bei einem alten Server oder einem
 * teilweise gefuellten Ergebnis muessen die Items die Wahrheit sein, sonst
 * meldet die App mehr Erfolge, als tatsaechlich geschrieben wurden. Die
 * Zaehler werden deshalb aus `items` neu gebildet und nur als Rueckfall
 * verwendet, wenn `items` fehlt.
 */
export function summarizeBulkResult(result: BulkCategoryResult): BulkResultSummary {
  const items: BulkCategoryItem[] = result.items ?? [];

  let applied = 0;
  let unchanged = 0;
  let skipped = 0;
  let conflict = 0;
  const reasonCounts = new Map<string, number>();

  for (const item of items) {
    switch (item.status) {
      case "applied":
        applied += 1;
        break;
      case "unchanged":
        unchanged += 1;
        break;
      case "skipped":
        skipped += 1;
        break;
      case "conflict":
        conflict += 1;
        break;
    }
    if (item.status !== "applied") {
      const label = describeBulkSkipReason(item.reason);
      reasonCounts.set(label, (reasonCounts.get(label) ?? 0) + 1);
    }
  }

  if (items.length === 0) {
    applied = result.applied;
    unchanged = result.unchanged;
    skipped = result.skipped;
    conflict = result.conflict;
  }

  const total = applied + unchanged + skipped + conflict;
  const reasonLines = [...reasonCounts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "de"))
    .map(([label, count]) => `${count}x ${label}`);

  const parts = [`${applied} geändert`];
  if (unchanged > 0) parts.push(`${unchanged} unverändert`);
  if (skipped > 0) parts.push(`${skipped} übersprungen`);
  if (conflict > 0) parts.push(`${conflict} im Konflikt`);

  return {
    applied,
    unchanged,
    skipped,
    conflict,
    total,
    reasonLines,
    headline: `${parts.join(", ")} (von ${total})`,
  };
}

/**
 * Beschriftung von `Alle auswaehlen`. Der Plan verlangt ausdruecklich, dass
 * der Geltungsbereich benannt wird - verdeckte Seiten oder Filtertreffer
 * duerfen nicht still mitgemeint sein.
 */
export function selectAllLabel(visibleCount: number): string {
  const capped = Math.min(visibleCount, MAX_BULK_TRANSACTIONS);
  return `Alle ${capped} sichtbaren auswählen`;
}

/**
 * Teilt eine Auswahl in den anwendbaren Teil und den Rest oberhalb der
 * 500er-Grenze. Der Rest wird nicht still verworfen, sondern gemeldet.
 */
export function capSelection<T>(ids: T[]): { applicable: T[]; droppedCount: number } {
  if (ids.length <= MAX_BULK_TRANSACTIONS) return { applicable: ids, droppedCount: 0 };
  return {
    applicable: ids.slice(0, MAX_BULK_TRANSACTIONS),
    droppedCount: ids.length - MAX_BULK_TRANSACTIONS,
  };
}
