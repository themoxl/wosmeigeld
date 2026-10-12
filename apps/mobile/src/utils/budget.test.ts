import { formatMinorUnits, parseAmountToMinorUnits } from "./money";

describe("Budget money parsing and formatting", () => {
  it("formats minor units correctly for budget displays", () => {
    expect(formatMinorUnits(50000)).toBe("500,00 €");
    expect(formatMinorUnits(-1250)).toBe("-12,50 €");
    expect(formatMinorUnits(0)).toBe("0,00 €");
  });

  it("parses user input to minor units correctly for allocations", () => {
    expect(parseAmountToMinorUnits("500")).toBe(50000);
    expect(parseAmountToMinorUnits("500,50")).toBe(50050);
    expect(parseAmountToMinorUnits("1.200,00")).toBe(120000);
  });
});
