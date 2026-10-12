import { describe, expect, it } from "vitest";
import {
  collectConditions,
  describeCondition,
  dimensionLabel,
  drilldownQuery,
  findCondition,
  parseFilterExpression,
  queryFromSearchParams,
  queryToSearchParams,
  removeCondition,
  upsertCondition,
} from "../lib/analytics";
import type { AnalyticsQuery } from "../lib/types";

// docs/ANALYSE-EXPLORER-PLAN.md, Bausteine B/C/D. Diese Datei prueft die
// reine Abfragelogik ohne Renderer - insbesondere die Drilldown-Ableitung,
// weil auf ihr die Zusage "jede Zahl ist rueckverfolgbar" beruht.

const BASE: AnalyticsQuery = {
  grain: "aggregate",
  group_by: [{ field: "category" }],
  measures: [{ field: "amount", aggregate: "sum" }],
  period: { kind: "last_n_months", n: 12 },
};

describe("Filterbaum", () => {
  it("setzt genau eine Bedingung je Feld", () => {
    let query = upsertCondition(BASE, { field: "account", op: "eq", value: "a" });
    query = upsertCondition(query, { field: "account", op: "eq", value: "b" });
    const conditions = collectConditions(query.where);
    expect(conditions).toHaveLength(1);
    expect(conditions[0]?.value).toBe("b");
  });

  it("behaelt andere Felder beim Ersetzen", () => {
    let query = upsertCondition(BASE, { field: "account", op: "eq", value: "a" });
    query = upsertCondition(query, { field: "category", op: "eq", value: "c" });
    expect(collectConditions(query.where)).toHaveLength(2);
    expect(findCondition(query, "account")?.value).toBe("a");
  });

  it("entfernt eine Bedingung vollstaendig", () => {
    let query = upsertCondition(BASE, { field: "account", op: "eq", value: "a" });
    query = removeCondition(query, "account");
    expect(query.where).toBeNull();
  });
});

describe("Drilldown", () => {
  it("leitet aus Segment und Aggregat die Zeilenabfrage ab", () => {
    const detail = drilldownQuery(BASE, { category: "cat-1", sum_amount: -4250 });
    expect(detail.grain).toBe("rows");
    expect(detail.group_by).toEqual([]);
    expect(detail.measures).toEqual([]);
    expect(findCondition(detail, "category")).toEqual({
      field: "category",
      op: "eq",
      value: "cat-1",
    });
  });

  it("uebernimmt die bestehenden Filter", () => {
    const filtered = upsertCondition(BASE, { field: "account", op: "eq", value: "acc-1" });
    const detail = drilldownQuery(filtered, { category: "cat-1", sum_amount: -1 });
    expect(findCondition(detail, "account")?.value).toBe("acc-1");
  });

  it("behandelt 'Ohne Kategorie' als is_null, nicht als Text 'null'", () => {
    const detail = drilldownQuery(BASE, { category: null, sum_amount: -999 });
    expect(findCondition(detail, "category")?.op).toBe("is_null");
  });

  it("klappt die zusammengefasste Restzeile nicht auf", () => {
    // Die Restzeile steht fuer viele Segmente - ein Drilldown darauf waere
    // eine Behauptung.
    const detail = drilldownQuery(BASE, { category: "__rest__", sum_amount: -100 });
    expect(findCondition(detail, "category")).toBeUndefined();
  });

  it("uebersetzt eine Monatsgruppierung in einen Zeitraum", () => {
    const monthly: AnalyticsQuery = {
      ...BASE,
      group_by: [{ field: "date", granularity: "month" }],
    };
    const detail = drilldownQuery(monthly, { "date:month": "2026-06-01", sum_amount: -1 });
    expect(detail.period).toEqual({
      kind: "custom",
      from_date: "2026-06-01",
      to_date: "2026-06-30",
    });
  });
});

describe("Mikrosyntax der Spaltenfilter", () => {
  it("versteht Vergleiche und rechnet Euro in Minor Units um", () => {
    expect(parseFilterExpression("amount", "num", ">100")).toEqual({
      field: "amount",
      op: "gt",
      value: 10000,
    });
  });

  it("versteht Bereiche", () => {
    expect(parseFilterExpression("amount", "num", "10..99")).toEqual({
      field: "amount",
      op: "between",
      value: [1000, 9900],
    });
  });

  it("versteht Negation und exakte Uebereinstimmung bei Text", () => {
    expect(parseFilterExpression("payee", "cat", "!REWE")).toEqual({
      field: "payee",
      op: "ne",
      value: "REWE",
    });
    expect(parseFilterExpression("payee", "cat", "=REWE")).toEqual({
      field: "payee",
      op: "eq",
      value: "REWE",
    });
  });

  it("faellt bei freiem Text auf enthaelt zurueck", () => {
    expect(parseFilterExpression("payee", "cat", "rewe")?.op).toBe("contains");
  });

  it("liefert null fuer leere Eingabe", () => {
    expect(parseFilterExpression("payee", "cat", "   ")).toBeNull();
  });
});

describe("URL-Zustand", () => {
  it("ueberlebt eine Rundreise durch die Suchparameter", () => {
    const query = upsertCondition(BASE, { field: "account", op: "in", value: ["a", "b"] });
    const restored = queryFromSearchParams(queryToSearchParams(query), BASE);
    expect(restored).toEqual(query);
  });

  it("faellt bei unlesbarer URL auf die Standardabfrage zurueck", () => {
    const params = new URLSearchParams({ q: "kein-base64!" });
    expect(queryFromSearchParams(params, BASE)).toEqual(BASE);
  });
});

describe("Beschriftung", () => {
  it("bevorzugt das aufgeloeste Label vor der ID", () => {
    expect(dimensionLabel({ category: "uuid", category_label: "Lebensmittel" }, "category")).toBe(
      "Lebensmittel",
    );
  });

  it("benennt eine fehlende Zuordnung, statt sie leer zu lassen", () => {
    expect(dimensionLabel({ category: null }, "category")).toBe("Ohne Zuordnung");
  });

  it("beschreibt Bedingungen lesbar", () => {
    expect(describeCondition({ field: "amount", op: "gt", value: 10000 }, "Betrag")).toBe(
      "Betrag > 10000",
    );
    expect(
      describeCondition({ field: "category", op: "in", value: ["a", "b", "c"] }, "Kategorie"),
    ).toBe("Kategorie ist 3 Werte");
    expect(describeCondition({ field: "category", op: "is_null", value: null }, "Kategorie")).toBe(
      "Kategorie ist leer",
    );
  });
});
