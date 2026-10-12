import { describe, expect, it } from "vitest";
import { calculateLoanPreview } from "../lib/loanPreview";

// Ueberschlagsrechnung der Foerder-Vorschau. Die Zahlen sind von Hand
// nachgerechnet, damit ein Denkfehler in der Formel auffaellt und nicht bloss
// eine Abweichung zur vorherigen Fassung.

describe("calculateLoanPreview", () => {
  it("rechnet die Annuitaet aus Zins plus Anfangstilgung", () => {
    // 150.000 EUR zu 3 % Zins und 2 % Tilgung: 5 % von 150.000 = 7.500 im Jahr,
    // also 625 EUR im Monat.
    const preview = calculateLoanPreview({
      principalMinor: 150_000_00,
      nominalRateBpsAnnual: 300,
      initialRepaymentRateBps: 200,
      repaymentFreeMonths: 0,
      fixedRateMonths: 120,
      followUpRateBpsAnnual: 300,
    });
    expect(preview.paymentMinor).toBe(625_00);
    expect(preview.yearlyPaymentMinor).toBe(7_500_00);
    expect(preview.paymentDuringRepaymentFreeMinor).toBe(0);
  });

  it("zahlt waehrend der tilgungsfreien Zeit nur Zinsen und tilgt nichts", () => {
    const preview = calculateLoanPreview({
      principalMinor: 150_000_00,
      nominalRateBpsAnnual: 300,
      initialRepaymentRateBps: 200,
      repaymentFreeMonths: 60,
      fixedRateMonths: 60,
      followUpRateBpsAnnual: 300,
    });
    // 3 % von 150.000 = 4.500 im Jahr = 375 EUR im Monat.
    expect(preview.paymentDuringRepaymentFreeMinor).toBe(375_00);
    // Nach fuenf tilgungsfreien Jahren steht die Restschuld unveraendert.
    expect(preview.balanceAtFixedEndMinor).toBe(150_000_00);
    expect(preview.interestUntilFixedEndMinor).toBe(60 * 375_00);
  });

  it("laesst die Restschuld nach der Zinsbindung stehen", () => {
    const preview = calculateLoanPreview({
      principalMinor: 100_000_00,
      nominalRateBpsAnnual: 350,
      initialRepaymentRateBps: 200,
      repaymentFreeMonths: 0,
      fixedRateMonths: 120,
      followUpRateBpsAnnual: 500,
    });
    expect(preview.balanceAtFixedEndMinor).toBeGreaterThan(0);
    expect(preview.balanceAtFixedEndMinor).toBeLessThan(100_000_00);
    expect(preview.payoffMonth).not.toBe(1);
  });

  it("meldet den Monat der Volltilgung, wenn sie im Horizont liegt", () => {
    const preview = calculateLoanPreview({
      principalMinor: 10_000_00,
      nominalRateBpsAnnual: 100,
      initialRepaymentRateBps: 5_000,
      repaymentFreeMonths: 0,
      fixedRateMonths: 120,
      followUpRateBpsAnnual: 100,
    });
    expect(preview.payoffMonth).not.toBeNull();
    expect(preview.balanceAtFixedEndMinor).toBe(0);
  });

  it("ein hoeherer Anschlusszins erhoeht die Gesamtzinsen", () => {
    const base = {
      principalMinor: 200_000_00,
      nominalRateBpsAnnual: 300,
      initialRepaymentRateBps: 200,
      repaymentFreeMonths: 0,
      fixedRateMonths: 120,
    };
    const guenstig = calculateLoanPreview({ ...base, followUpRateBpsAnnual: 300 });
    const teuer = calculateLoanPreview({ ...base, followUpRateBpsAnnual: 600 });
    expect(teuer.totalInterestMinor).toBeGreaterThan(guenstig.totalInterestMinor);
    // Bis zum Ende der Bindung darf der Anschlusszins nichts aendern.
    expect(teuer.interestUntilFixedEndMinor).toBe(guenstig.interestUntilFixedEndMinor);
    expect(teuer.balanceAtFixedEndMinor).toBe(guenstig.balanceAtFixedEndMinor);
  });
});
