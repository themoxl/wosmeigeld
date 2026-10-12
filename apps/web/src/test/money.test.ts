import { describe, expect, it } from "vitest";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";

describe("parseAmountToMinorUnits", () => {
  it("parst deutsche Dezimalschreibweise mit Komma", () => {
    expect(parseAmountToMinorUnits("12,34")).toBe(1234);
  });

  it("parst englische Dezimalschreibweise mit Punkt", () => {
    expect(parseAmountToMinorUnits("12.34")).toBe(1234);
  });

  it("parst ganze Eurobetraege ohne Nachkommastellen", () => {
    expect(parseAmountToMinorUnits("1234")).toBe(123400);
  });

  it("parst negative Betraege (Ausgaben)", () => {
    expect(parseAmountToMinorUnits("-12,5")).toBe(-1250);
  });

  it("parst ein explizites Pluszeichen", () => {
    expect(parseAmountToMinorUnits("+12,34")).toBe(1234);
  });

  it("parst deutsche Tausendertrennzeichen mit Dezimalkomma", () => {
    expect(parseAmountToMinorUnits("1.234,56")).toBe(123456);
  });

  it("parst englische Tausendertrennzeichen mit Dezimalpunkt", () => {
    expect(parseAmountToMinorUnits("1,234.56")).toBe(123456);
  });

  it("rundet nicht ungenau wie parseFloat(...) * 100 es tun wuerde", () => {
    // 1,005 € entspricht exakt 100 Cent + 5 Zehntel - hier bewusst mit nur
    // einer Nachkommastelle, damit das Ergebnis eindeutig ist.
    expect(parseAmountToMinorUnits("0,1")).toBe(10);
    expect(parseAmountToMinorUnits("19,9")).toBe(1990);
  });

  it("liefert null fuer leere Eingabe", () => {
    expect(parseAmountToMinorUnits("")).toBeNull();
    expect(parseAmountToMinorUnits("   ")).toBeNull();
  });

  it("liefert null fuer nicht-numerische Eingabe", () => {
    expect(parseAmountToMinorUnits("abc")).toBeNull();
    expect(parseAmountToMinorUnits("12,34 €")).toBeNull();
  });

  it("liefert null fuer mehr als zwei Nachkommastellen", () => {
    expect(parseAmountToMinorUnits("12,345")).toBeNull();
  });

  it("behandelt fehlende Nachkommastellen nach dem Trennzeichen als volle Euro", () => {
    expect(parseAmountToMinorUnits("12,")).toBe(1200);
  });
});

describe("formatMinorUnitsAsEuro", () => {
  it("formatiert positive Betraege als Euro", () => {
    expect(formatMinorUnitsAsEuro(123456)).toContain("1.234,56");
  });

  it("formatiert negative Betraege mit Minuszeichen", () => {
    const formatted = formatMinorUnitsAsEuro(-500);
    expect(formatted).toContain("5,00");
    expect(formatted).toContain("-");
  });
});
