/// <reference types="jest" />
import { MAX_BULK_TRANSACTIONS, type BulkCategoryResult } from "../api/categoryTypes";
import {
  capSelection,
  describeBulkSkipReason,
  selectAllLabel,
  summarizeBulkResult,
} from "./bulkCategory";

function result(overrides: Partial<BulkCategoryResult> = {}): BulkCategoryResult {
  return { applied: 0, unchanged: 0, skipped: 0, conflict: 0, items: [], ...overrides };
}

describe("summarizeBulkResult", () => {
  it("zaehlt jeden Status einzeln", () => {
    const summary = summarizeBulkResult(
      result({
        applied: 2,
        unchanged: 1,
        skipped: 1,
        conflict: 1,
        items: [
          { transaction_id: "t1", status: "applied" },
          { transaction_id: "t2", status: "applied" },
          { transaction_id: "t3", status: "unchanged", reason: "already_assigned" },
          { transaction_id: "t4", status: "skipped", reason: "split_transaction" },
          { transaction_id: "t5", status: "conflict", reason: "version_conflict" },
        ],
      }),
    );
    expect(summary).toMatchObject({ applied: 2, unchanged: 1, skipped: 1, conflict: 1, total: 5 });
    expect(summary.headline).toBe(
      "2 geändert, 1 unverändert, 1 übersprungen, 1 im Konflikt (von 5)",
    );
  });

  it("bildet die Zaehler aus den Items, nicht aus den (moeglicherweise falschen) Summen", () => {
    // Ein Konflikt darf die uebrigen gueltigen Aenderungen nicht blockieren -
    // und die App darf nicht mehr Erfolge melden, als die Items hergeben.
    const summary = summarizeBulkResult(
      result({
        applied: 99,
        items: [
          { transaction_id: "t1", status: "applied" },
          { transaction_id: "t2", status: "conflict", reason: "version_conflict" },
        ],
      }),
    );
    expect(summary.applied).toBe(1);
    expect(summary.conflict).toBe(1);
    expect(summary.total).toBe(2);
  });

  it("faellt ohne Items auf die Zaehler zurueck", () => {
    const summary = summarizeBulkResult(result({ applied: 3, skipped: 1 }));
    expect(summary).toMatchObject({ applied: 3, skipped: 1, total: 4 });
    expect(summary.reasonLines).toEqual([]);
  });

  it("gruppiert die Gruende und sortiert nach Haeufigkeit", () => {
    const summary = summarizeBulkResult(
      result({
        items: [
          { transaction_id: "t1", status: "skipped", reason: "split_transaction" },
          { transaction_id: "t2", status: "skipped", reason: "split_transaction" },
          { transaction_id: "t3", status: "conflict", reason: "version_conflict" },
          { transaction_id: "t4", status: "applied" },
        ],
      }),
    );
    expect(summary.reasonLines).toEqual(["2x Split-Buchung", "1x zwischenzeitlich geändert"]);
  });

  it("nennt einen fehlenden Grund ausdruecklich, statt ihn zu verschweigen", () => {
    const summary = summarizeBulkResult(
      result({ items: [{ transaction_id: "t1", status: "skipped" }] }),
    );
    expect(summary.reasonLines).toEqual(["1x ohne Angabe"]);
  });

  it("kommt mit einem leeren Ergebnis klar", () => {
    const summary = summarizeBulkResult(result());
    expect(summary.total).toBe(0);
    expect(summary.headline).toBe("0 geändert (von 0)");
  });
});

describe("describeBulkSkipReason", () => {
  it("uebersetzt alle bekannten Gruende", () => {
    expect(describeBulkSkipReason("category_inactive")).toBe("Kategorie archiviert");
    expect(describeBulkSkipReason("archived_transaction")).toBe("gelöscht");
  });

  it("bleibt bei null verstaendlich", () => {
    expect(describeBulkSkipReason(null)).toBe("ohne Angabe");
  });
});

describe("selectAllLabel", () => {
  it("benennt den Geltungsbereich ausdruecklich", () => {
    expect(selectAllLabel(84)).toBe("Alle 84 sichtbaren auswählen");
  });

  it("verspricht nie mehr als die Obergrenze", () => {
    expect(selectAllLabel(900)).toBe(`Alle ${MAX_BULK_TRANSACTIONS} sichtbaren auswählen`);
  });
});

describe("capSelection", () => {
  it("laesst eine Auswahl unterhalb der Grenze unveraendert", () => {
    expect(capSelection(["a", "b"])).toEqual({ applicable: ["a", "b"], droppedCount: 0 });
  });

  it("kappt bei 500 und meldet den Rest, statt ihn still zu verwerfen", () => {
    const ids = Array.from({ length: MAX_BULK_TRANSACTIONS + 7 }, (_, i) => `t${i}`);
    const { applicable, droppedCount } = capSelection(ids);
    expect(applicable).toHaveLength(MAX_BULK_TRANSACTIONS);
    expect(droppedCount).toBe(7);
  });
});
