import { describe, expect, it } from "vitest";
import {
  buildCondition,
  conditionInputs,
  conditionValueKeys,
  matchesCondition,
  NULL_VALUE_KEY,
  operatorsFor,
  valueKeysToCondition,
} from "../lib/gridFilters";

// Die Spaltenfilter (docs/ANALYSE-EXPLORER-PLAN.md, C.1).
//
// Der Explorer schickt die hier gebaute Bedingung an den Server, das
// Buchungsregister wertet sie im Browser aus. Genau deshalb wird beides in
// derselben Datei geprueft: waeren `buildCondition` und `matchesCondition`
// nicht deckungsgleich, wuerde derselbe Filter an den beiden Stellen
// verschiedene Mengen zeigen - und niemand koennte sagen, welche stimmt.

describe("Operatorangebot", () => {
  it("bietet numerische Vergleiche nur auf Zahlenspalten an", () => {
    const numeric = operatorsFor("num").map((entry) => entry.op);
    expect(numeric).toContain("between");
    expect(numeric).toContain("gte");
    expect(numeric).not.toContain("contains");

    const text = operatorsFor("cat").map((entry) => entry.op);
    expect(text).toContain("contains");
    expect(text).toContain("starts_with");
    expect(text).not.toContain("between");
  });

  it("kennt fuer jede Spaltenart einen wertlosen Leer-Operator", () => {
    for (const kind of ["num", "cat", "date", "bool"] as const) {
      expect(operatorsFor(kind).map((entry) => entry.op)).toContain("is_null");
    }
  });
});

describe("Bedingung aus der Eingabemaske", () => {
  it("rechnet Geldeingaben in Minor Units um", () => {
    expect(buildCondition("amount", "num", true, "gte", ["1.234,56", ""])).toEqual({
      field: "amount",
      op: "gte",
      value: 123456,
    });
  });

  it("dreht einen verkehrt herum eingegebenen Bereich um", () => {
    expect(buildCondition("amount", "num", true, "between", ["99", "10"])).toEqual({
      field: "amount",
      op: "between",
      value: [1000, 9900],
    });
  });

  it("liefert null statt einer 0, solange die Eingabe unvollstaendig ist", () => {
    // Ein Filter, der bei leerer Eingabe auf 0 greift, blendet stillschweigend
    // fast alles aus.
    expect(buildCondition("amount", "num", true, "gt", ["", ""])).toBeNull();
    expect(buildCondition("amount", "num", true, "between", ["10", ""])).toBeNull();
    expect(buildCondition("payee", "cat", false, "contains", ["  ", ""])).toBeNull();
  });

  it("braucht fuer Leer-Operatoren keinen Wert", () => {
    expect(buildCondition("category", "cat", false, "is_null", ["", ""])).toEqual({
      field: "category",
      op: "is_null",
      value: null,
    });
  });

  it("findet eine gesetzte Bedingung unveraendert wieder", () => {
    const condition = buildCondition("amount", "num", true, "between", ["10", "99"]);
    expect(conditionInputs(condition, "num", true)).toEqual(["10,00", "99,00"]);
  });
});

describe("Werteauswahl", () => {
  it("behandelt 'ohne Angabe' als echten Wert, nicht als fehlenden", () => {
    const condition = valueKeysToCondition("category", "in", ["cat-1", NULL_VALUE_KEY]);
    expect(condition).toEqual({ field: "category", op: "in", value: ["cat-1", null] });
    expect(conditionValueKeys(condition)).toEqual(["cat-1", NULL_VALUE_KEY]);
  });

  it("entfernt den Filter, wenn kein Wert mehr ausgewaehlt ist", () => {
    expect(valueKeysToCondition("category", "in", [])).toBeNull();
  });
});

describe("Auswertung im Browser", () => {
  it("vergleicht Text ohne Ruecksicht auf Gross- und Kleinschreibung", () => {
    const condition = { field: "payee", op: "contains" as const, value: "REWE" };
    expect(matchesCondition(condition, "Rewe Markt")).toBe(true);
    expect(matchesCondition(condition, "Edeka")).toBe(false);
  });

  it("trifft mit Leer-Operatoren genau die fehlenden Werte", () => {
    expect(matchesCondition({ field: "category", op: "is_null", value: null }, null)).toBe(true);
    expect(matchesCondition({ field: "category", op: "is_null", value: null }, "")).toBe(true);
    expect(matchesCondition({ field: "category", op: "is_null", value: null }, "Miete")).toBe(
      false,
    );
    expect(matchesCondition({ field: "category", op: "is_not_null", value: null }, "Miete")).toBe(
      true,
    );
  });

  it("laesst einen fehlenden Wert an keinem inhaltlichen Vergleich teilnehmen", () => {
    // Sonst waere "Kategorie ist nicht Miete" auch fuer unkategorisierte
    // Buchungen wahr - eine Aussage, die niemand gemeint hat.
    expect(matchesCondition({ field: "category", op: "ne", value: "Miete" }, null)).toBe(false);
  });

  it("vergleicht Betraege numerisch, nicht als Text", () => {
    expect(matchesCondition({ field: "amount", op: "gt", value: -1000 }, -500)).toBe(true);
    expect(matchesCondition({ field: "amount", op: "gt", value: -1000 }, -2000)).toBe(false);
    expect(matchesCondition({ field: "amount", op: "between", value: [-2000, -500] }, -1000)).toBe(
      true,
    );
  });

  it("vergleicht ISO-Datumsangaben ueber ihre Textordnung", () => {
    const condition = {
      field: "date",
      op: "between" as const,
      value: ["2026-07-01", "2026-07-31"],
    };
    expect(matchesCondition(condition, "2026-07-15")).toBe(true);
    expect(matchesCondition(condition, "2026-08-01")).toBe(false);
  });

  it("wertet Mehrfachauswahl ein- und ausschliessend aus", () => {
    const values = { field: "status", op: "in" as const, value: ["Gebucht", "Vorgemerkt"] };
    expect(matchesCondition(values, "Gebucht")).toBe(true);
    expect(matchesCondition(values, "Abgestimmt")).toBe(false);
    expect(matchesCondition({ ...values, op: "not_in" }, "Abgestimmt")).toBe(true);
  });

  it("versteht Merkmale als ja/nein", () => {
    expect(matchesCondition({ field: "transfer", op: "eq", value: true }, true)).toBe(true);
    expect(matchesCondition({ field: "transfer", op: "eq", value: false }, true)).toBe(false);
  });
});
