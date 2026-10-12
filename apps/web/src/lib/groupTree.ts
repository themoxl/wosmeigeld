import { dimensionLabel } from "./analytics";
import type { AnalyticsRow } from "./types";

// Aus den flachen Aggregatzeilen des Servers wird im Browser ein aufklappbarer
// Baum.
//
// Warum nicht serverseitig? Weil eine Abfrage mit mehreren Dimensionen ohnehin
// genau die Kombinationszeilen liefert, aus denen jede Zwischenebene exakt
// ableitbar ist. Eine zweite Abfrage je Ebene waere nicht nur langsamer,
// sondern koennte - bei einer zwischenzeitlich geaenderten Buchung - andere
// Zahlen liefern als die Ebene darunter. Der Baum hier ist per Konstruktion
// widerspruchsfrei: jede Elternzeile ist die Summe ihrer sichtbaren Kinder.
//
// Genau deshalb duerfen nur SUMMIERBARE Kennzahlen hochgerechnet werden.
// Ein Median oder ein Ø ueber Teilsummen ist nicht der Median oder das Ø der
// Grundmenge; solche Zellen bleiben auf Zwischenebenen leer, statt eine Zahl
// zu behaupten, die niemand nachrechnen kann.

export interface GroupNode {
  /** Stabiler Schluessel ueber den gesamten Pfad. */
  path: string;
  depth: number;
  dimKey: string;
  label: string;
  measures: Record<string, number | null>;
  children: GroupNode[];
  /** Die Aggregatzeile - nur auf der tiefsten Ebene gesetzt. */
  row: AnalyticsRow | null;
  /** Anzahl der Blaetter unterhalb dieses Knotens. */
  leafCount: number;
}

/**
 * Aggregate, deren Werte sich ueber Ebenen hinweg addieren lassen. Alles
 * andere wird auf Zwischenebenen nicht ausgewiesen.
 */
export const SUMMABLE_AGGREGATES = new Set(["sum", "abs_sum", "count", "avg_per_month"]);

export function isSummableMeasureKey(key: string): boolean {
  return [...SUMMABLE_AGGREGATES].some((aggregate) => key.startsWith(`${aggregate}_`));
}

export function buildGroupTree(
  rows: readonly AnalyticsRow[],
  dimKeys: readonly string[],
  measureKeys: readonly string[],
): GroupNode[] {
  if (dimKeys.length === 0) return [];

  const roots: GroupNode[] = [];
  const index = new Map<string, GroupNode>();

  for (const row of rows) {
    let parentPath = "";
    let siblings = roots;

    dimKeys.forEach((dimKey, depth) => {
      const rawValue = row[dimKey];
      const path = `${parentPath}/${dimKey}=${String(rawValue)}`;
      let node = index.get(path);
      if (!node) {
        node = {
          path,
          depth,
          dimKey,
          label: dimensionLabel(row, dimKey),
          measures: Object.fromEntries(measureKeys.map((key) => [key, null])),
          children: [],
          row: null,
          leafCount: 0,
        };
        index.set(path, node);
        siblings.push(node);
      }
      if (depth === dimKeys.length - 1) {
        node.row = row;
        node.leafCount = 1;
      }
      parentPath = path;
      siblings = node.children;
    });
  }

  for (const root of roots) {
    accumulate(root, measureKeys);
  }
  return roots;
}

function accumulate(node: GroupNode, measureKeys: readonly string[]): void {
  if (node.children.length === 0) {
    for (const key of measureKeys) {
      const value = node.row?.[key];
      node.measures[key] = typeof value === "number" ? value : null;
    }
    node.leafCount = 1;
    return;
  }

  for (const child of node.children) {
    accumulate(child, measureKeys);
  }

  node.leafCount = node.children.reduce((total, child) => total + child.leafCount, 0);
  for (const key of measureKeys) {
    if (!isSummableMeasureKey(key)) {
      node.measures[key] = null;
      continue;
    }
    let sum = 0;
    let seen = false;
    for (const child of node.children) {
      const value = child.measures[key];
      if (typeof value === "number") {
        sum += value;
        seen = true;
      }
    }
    node.measures[key] = seen ? sum : null;
  }
}

/** Die sichtbaren Knoten in Anzeigereihenfolge, abhaengig vom Aufklappzustand. */
export function flattenGroupTree(
  nodes: readonly GroupNode[],
  expanded: ReadonlySet<string>,
): GroupNode[] {
  const flat: GroupNode[] = [];
  const walk = (list: readonly GroupNode[]) => {
    for (const node of list) {
      flat.push(node);
      if (node.children.length > 0 && expanded.has(node.path)) {
        walk(node.children);
      }
    }
  };
  walk(nodes);
  return flat;
}

/** Alle Pfade eines Baums - fuer "alles aufklappen". */
export function allGroupPaths(nodes: readonly GroupNode[]): string[] {
  const paths: string[] = [];
  const walk = (list: readonly GroupNode[]) => {
    for (const node of list) {
      if (node.children.length > 0) {
        paths.push(node.path);
        walk(node.children);
      }
    }
  };
  walk(nodes);
  return paths;
}
