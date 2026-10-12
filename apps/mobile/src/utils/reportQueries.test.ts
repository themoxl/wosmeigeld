/// <reference types="jest" />
import {
  categoryMonthlyQuery,
  drilldownRowsQuery,
  groupMonthlyQuery,
  payeeMonthlyQuery,
  reportBaseQuery,
  yearComparisonQuery,
  DRILLDOWN_ROW_LIMIT,
} from "./reportQueries";

// Der Zuschnitt ist der eigentliche Vertrag dieser Datei: Transfers aus, nur
// Budgetkonten, archivierte Kategorien drin. Weicht eine Abfrage davon ab,
// zeigt die App bei derselben Frage eine andere Summe als das Web - und das
// sieht wie ein Datenfehler aus, nicht wie ein Anzeigefehler.

describe("reportBaseQuery", () => {
  it("schliesst Transfers aus und beschraenkt auf Budgetkonten", () => {
    const query = reportBaseQuery(12);
    expect(query.include_transfers).toBe(false);
    expect(query.only_on_budget).toBe(true);
    expect(query.period).toEqual({ kind: "last_n_months", n: 12 });
  });

  it("behaelt archivierte Kategorien, damit die Vergangenheit vollstaendig bleibt", () => {
    expect(reportBaseQuery(24).include_archived_categories).toBe(true);
  });
});

describe("Registerabfragen", () => {
  it("gruppiert den Ueberblick nach Kategoriegruppe und Monat", () => {
    expect(groupMonthlyQuery(12).group_by).toEqual([
      { field: "category_group" },
      { field: "date", granularity: "month" },
    ]);
  });

  it("liefert fuer Kategorien zusaetzlich die Gruppe mit", () => {
    expect(categoryMonthlyQuery(12).group_by).toEqual([
      { field: "category_group" },
      { field: "category" },
      { field: "date", granularity: "month" },
    ]);
  });

  it("fragt Empfaenger ohne Kategoriebezug ab", () => {
    expect(payeeMonthlyQuery(24).group_by).toEqual([
      { field: "payee" },
      { field: "date", granularity: "month" },
    ]);
  });

  it("filtert ueberall auf Ausgaben", () => {
    for (const query of [groupMonthlyQuery(12), categoryMonthlyQuery(12), payeeMonthlyQuery(12)]) {
      expect(query.where).toEqual({
        condition: { field: "direction", op: "eq", value: "expense" },
      });
    }
  });

  it("misst Summe und Anzahl, damit hinter jeder Zahl eine Belegzahl steht", () => {
    expect(categoryMonthlyQuery(12).measures).toEqual([
      { field: "amount", aggregate: "sum" },
      { field: "amount", aggregate: "count" },
    ]);
  });

  it("nimmt fuer den Jahresvergleich einen festen Zeitraum statt der letzten N Monate", () => {
    const query = yearComparisonQuery("2026-01-01", "2026-07-31");
    expect(query.period).toEqual({
      kind: "custom",
      from_date: "2026-01-01",
      to_date: "2026-07-31",
    });
    expect(query.group_by).toEqual([{ field: "category" }]);
  });
});

describe("drilldownRowsQuery", () => {
  it("holt Einzelzeilen statt einer Aggregation - mit derselben Abgrenzung wie die Summe", () => {
    const query = drilldownRowsQuery({ from_date: "2026-07-01", to_date: "2026-07-31" });

    expect(query.grain).toBe("rows");
    expect(query.include_transfers).toBe(false);
    expect(query.only_on_budget).toBe(true);
    expect(query.limit).toBe(DRILLDOWN_ROW_LIMIT);
    expect(query.where).toBeNull();
  });

  it("filtert auf den angetippten Dimensionswert", () => {
    const query = drilldownRowsQuery({
      field: "category_group",
      value: "grp-1",
      from_date: "2026-07-01",
      to_date: "2026-07-31",
    });

    expect(query.where).toEqual({
      condition: { field: "category_group", op: "eq", value: "grp-1" },
    });
  });

  it("uebersetzt 'ohne Zuordnung' in einen is_null-Filter statt in einen leeren Wert", () => {
    const query = drilldownRowsQuery({
      field: "category",
      value: null,
      from_date: "2026-07-01",
      to_date: "2026-07-31",
    });

    expect(query.where).toEqual({
      condition: { field: "category", op: "is_null", value: null },
    });
  });

  it("verknuepft mehrere Bedingungen mit UND", () => {
    const query = drilldownRowsQuery({
      field: "category",
      value: "cat-1",
      direction: "expense",
      from_date: "2026-07-01",
      to_date: "2026-07-31",
    });

    expect(query.where).toEqual({
      all_of: [
        { condition: { field: "category", op: "eq", value: "cat-1" } },
        { condition: { field: "direction", op: "eq", value: "expense" } },
      ],
    });
  });

  it("nimmt fuer den Saldo beide Cashflow-Rollen, nicht nur eine", () => {
    const query = drilldownRowsQuery({
      cashflowRole: "net",
      from_date: "2026-07-01",
      to_date: "2026-07-31",
    });

    expect(query.where).toEqual({
      condition: { field: "cashflow_role", op: "in", value: ["income", "expense"] },
    });
  });
});
