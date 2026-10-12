import { describe, expect, it } from "vitest";
import {
  allGroupPaths,
  buildGroupTree,
  flattenGroupTree,
  isSummableMeasureKey,
} from "../lib/groupTree";
import type { AnalyticsRow } from "../lib/types";

// Der aufklappbare Gruppenbaum des Explorers.
//
// Die entscheidende Zusage ist die Widerspruchsfreiheit: jede Elternzeile ist
// die Summe ihrer Kinder. Waere sie das nicht, koennte eine aufgeklappte
// Ebene ihrer eigenen Ueberschrift widersprechen.

const ROWS: AnalyticsRow[] = [
  {
    account: "acc-1",
    account_label: "Girokonto",
    category: "cat-1",
    category_label: "Lebensmittel",
    sum_amount: -3000,
    count_amount: 3,
  },
  {
    account: "acc-1",
    account_label: "Girokonto",
    category: "cat-2",
    category_label: "Restaurant",
    sum_amount: -1200,
    count_amount: 2,
  },
  {
    account: "acc-2",
    account_label: "Sparkonto",
    category: "cat-1",
    category_label: "Lebensmittel",
    sum_amount: -800,
    count_amount: 1,
  },
];

describe("Gruppenbaum", () => {
  it("schachtelt die Zeilen in der Reihenfolge der Achsen", () => {
    const tree = buildGroupTree(ROWS, ["account", "category"], ["sum_amount", "count_amount"]);

    expect(tree.map((node) => node.label)).toEqual(["Girokonto", "Sparkonto"]);
    expect(tree[0]?.children.map((node) => node.label)).toEqual(["Lebensmittel", "Restaurant"]);
  });

  it("bildet jede Elternzeile als Summe ihrer Kinder", () => {
    const tree = buildGroupTree(ROWS, ["account", "category"], ["sum_amount", "count_amount"]);

    expect(tree[0]?.measures.sum_amount).toBe(-4200);
    expect(tree[0]?.measures.count_amount).toBe(5);
    expect(tree[1]?.measures.sum_amount).toBe(-800);
    expect(tree[0]?.leafCount).toBe(2);
  });

  it("weist nicht summierbare Kennzahlen auf Zwischenebenen nicht aus", () => {
    // Der Median ueber Teilsummen ist nicht der Median der Grundmenge. Eine
    // Zahl, die niemand nachrechnen kann, ist schlechter als eine leere Zelle.
    const rows: AnalyticsRow[] = [
      { account: "acc-1", category: "cat-1", median_amount: -100 },
      { account: "acc-1", category: "cat-2", median_amount: -900 },
    ];
    const tree = buildGroupTree(rows, ["account", "category"], ["median_amount"]);

    expect(isSummableMeasureKey("median_amount")).toBe(false);
    expect(tree[0]?.measures.median_amount).toBeNull();
    expect(tree[0]?.children[0]?.measures.median_amount).toBe(-100);
  });

  it("benennt eine fehlende Zuordnung, statt sie leer zu lassen", () => {
    const tree = buildGroupTree(
      [{ category: null, sum_amount: -50 }],
      ["category"],
      ["sum_amount"],
    );
    expect(tree[0]?.label).toBe("Ohne Zuordnung");
  });

  it("zeigt Kinder erst, wenn ihre Ebene aufgeklappt ist", () => {
    const tree = buildGroupTree(ROWS, ["account", "category"], ["sum_amount"]);

    expect(flattenGroupTree(tree, new Set())).toHaveLength(2);

    const paths = allGroupPaths(tree);
    const open = flattenGroupTree(tree, new Set(paths));
    expect(open.map((node) => node.label)).toEqual([
      "Girokonto",
      "Lebensmittel",
      "Restaurant",
      "Sparkonto",
      "Lebensmittel",
    ]);
  });

  it("liefert ohne Achse keinen Baum", () => {
    expect(buildGroupTree(ROWS, [], ["sum_amount"])).toEqual([]);
  });
});
