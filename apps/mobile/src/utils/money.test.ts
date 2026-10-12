/// <reference types="jest" />
import {
  formatMinorUnits,
  formatMinorUnitsCompact,
  formatMinorUnitsForInput,
  parseAmountToMinorUnits,
} from "./money";

describe("parseAmountToMinorUnits", () => {
  it("parses a plain German comma amount", () => {
    expect(parseAmountToMinorUnits("12,34")).toBe(1234);
  });

  it("parses a plain dot amount as decimal separator", () => {
    expect(parseAmountToMinorUnits("12.34")).toBe(1234);
  });

  it("parses an integer euro amount without decimals", () => {
    expect(parseAmountToMinorUnits("12")).toBe(1200);
  });

  it("parses German thousands separator with comma decimal", () => {
    expect(parseAmountToMinorUnits("1.234,56")).toBe(123456);
  });

  it("parses English thousands separator with dot decimal", () => {
    expect(parseAmountToMinorUnits("1,234.56")).toBe(123456);
  });

  it("pads a single decimal digit", () => {
    expect(parseAmountToMinorUnits("0,5")).toBe(50);
  });

  it("rounds more than two decimal digits", () => {
    expect(parseAmountToMinorUnits("12,345")).toBe(1235);
    expect(parseAmountToMinorUnits("12,344")).toBe(1234);
  });

  it("keeps a negative sign", () => {
    expect(parseAmountToMinorUnits("-5,00")).toBe(-500);
  });

  it("tolerates surrounding whitespace", () => {
    expect(parseAmountToMinorUnits("  9,90  ")).toBe(990);
  });

  it("does not lose precision the way parseFloat * 100 would", () => {
    // parseFloat("1.15") * 100 === 114.99999999999999 in JS - die
    // string-basierte Umrechnung muss stattdessen exakt 115 liefern.
    expect(parseAmountToMinorUnits("1.15")).toBe(115);
  });

  it("throws for an empty string", () => {
    expect(() => parseAmountToMinorUnits("")).toThrow();
    expect(() => parseAmountToMinorUnits("   ")).toThrow();
  });

  it("throws for invalid characters", () => {
    expect(() => parseAmountToMinorUnits("12,34 EUR")).toThrow();
    expect(() => parseAmountToMinorUnits("abc")).toThrow();
  });
});

describe("formatMinorUnits", () => {
  it("formats a positive amount with the default EUR currency symbol", () => {
    expect(formatMinorUnits(1234)).toBe("12,34 €");
  });

  it("formats a negative amount with a leading minus", () => {
    expect(formatMinorUnits(-500)).toBe("-5,00 €");
  });

  it("pads single-digit cents", () => {
    expect(formatMinorUnits(105)).toBe("1,05 €");
  });

  it("groups thousands using German locale formatting", () => {
    expect(formatMinorUnits(123456)).toBe("1.234,56 €");
  });

  it("supports a different currency with its own symbol", () => {
    expect(formatMinorUnits(1000, "USD")).toBe("10,00 $");
  });

  it("falls back to the currency code when there is no common symbol", () => {
    expect(formatMinorUnits(1000, "CHF")).toBe("10,00 CHF");
  });
});

describe("formatMinorUnitsCompact", () => {
  it("keeps small amounts readable without decimals", () => {
    expect(formatMinorUnitsCompact(123456)).toBe("1.235 €");
  });

  it("shortens thousands from ten thousand upwards", () => {
    expect(formatMinorUnitsCompact(2845000)).toBe("28,5k €");
  });

  it("shortens millions", () => {
    expect(formatMinorUnitsCompact(120000000)).toBe("1,2 Mio €");
  });

  it("keeps the sign of a negative amount", () => {
    expect(formatMinorUnitsCompact(-2845000)).toBe("-28,5k €");
  });
});

describe("formatMinorUnitsForInput", () => {
  it("formats a positive amount without a currency suffix", () => {
    expect(formatMinorUnitsForInput(1234)).toBe("12,34");
  });

  it("formats a negative amount with a leading minus", () => {
    expect(formatMinorUnitsForInput(-500)).toBe("-5,00");
  });

  it("does not group thousands, so it round-trips through parseAmountToMinorUnits", () => {
    expect(formatMinorUnitsForInput(123456)).toBe("1234,56");
    expect(parseAmountToMinorUnits(formatMinorUnitsForInput(123456))).toBe(123456);
  });

  it("round-trips zero", () => {
    expect(formatMinorUnitsForInput(0)).toBe("0,00");
  });
});
