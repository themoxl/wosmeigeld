import { describe, expect, it } from "vitest";
import {
  describeBenefit,
  describeLoanTerms,
  loanRateOptions,
  loanTermVariants,
  resolveBenefitAmountMinor,
} from "../lib/fundingTerms";

// Die Werte in den Testfaellen sind die echten Katalogeintraege der
// mitgelieferten Programme - so faellt auf, wenn die Anzeige an genau den
// Formen scheitert, die im Katalog wirklich vorkommen.

/** Intl setzt vor das Waehrungszeichen ein geschuetztes Leerzeichen (U+00A0).
 *  Fuer die Anzeige ist das richtig, im Testliteral waere es unlesbar. */
function plain(value: string | undefined): string {
  return (value ?? "").replace(/\u00a0/g, " ");
}

describe("describeBenefit", () => {
  it("nennt den Höchstbetrag eines Festdarlehens", () => {
    // KfW 124
    const facts = describeBenefit({ type: "loan_fixed", amount_minor: 100_000_00 });
    expect(facts).toHaveLength(1);
    expect(facts[0]?.label).toBe("Höchstbetrag Darlehen");
    expect(plain(facts[0]?.value)).toBe("100.000,00 €");
  });

  it("schlüsselt eine Staffel nach Kinderzahl auf", () => {
    // Jung kauft Alt (308)
    const facts = describeBenefit({
      type: "loan_tiered",
      tier_field: "children_count",
      tiers: [
        { min: 1, max: 1, amount_minor: 140_000_00 },
        { min: 2, max: 2, amount_minor: 160_000_00 },
        { min: 3, amount_minor: 180_000_00 },
      ],
    });
    expect(facts).toHaveLength(3);
    expect(facts[0]?.label).toBe("Höchstbetrag bei 1 Kinder im Haushalt");
    expect(plain(facts[1]?.value)).toBe("160.000,00 €");
    expect(facts[2]?.label).toBe("Höchstbetrag bei ab 3 Kinder im Haushalt");
  });

  it("nennt Fördersatz, Deckel und die begünstigten Maßnahmen", () => {
    // Heizungstausch (BEG Einzelmaßnahmen)
    const facts = describeBenefit({
      type: "percent_of_costs",
      rate_bps: 3000,
      cap_minor: 9_000_00,
      eligible_funding_tags: ["heating", "renewable_energy"],
    });
    expect(facts[0]?.value).toBe("30 % der förderfähigen Kosten");
    expect(facts[0]?.hint).toContain("heating");
    expect(plain(facts[1]?.value)).toBe("9.000,00 €");
  });

  it("beschreibt ein kombiniertes BayernLabo-Paket rekursiv als Darlehen und Zuschuss", () => {
    const facts = describeBenefit({
      type: "combined",
      components: [
        { type: "loan_percent_of_costs", rate_bps: 3333 },
        {
          type: "combined",
          components: [
            { type: "per_unit", unit_field: "children_count", amount_per_unit_minor: 500_000 },
          ],
        },
      ],
    });
    expect(facts[0]).toMatchObject({
      label: "Darlehensanteil",
      value: "33,33 % der förderfähigen Kosten",
    });
    expect(facts[1]?.label).toBe("Kinderzuschuss je Kind");
    expect(plain(facts[1]?.value)).toBe("5.000,00 €");
  });

  it("erfindet nichts, wenn der Betrag fehlt oder der Typ unbekannt ist", () => {
    expect(describeBenefit({ type: "loan_fixed" })).toEqual([]);
    expect(describeBenefit({ type: "combined" })).toEqual([]);
    expect(describeBenefit(undefined)).toEqual([]);
  });
});

describe("describeLoanTerms", () => {
  it("gibt Laufzeit, Bindung und tilgungsfreie Zeit auch in Jahren an", () => {
    // Jung kauft Alt (308)
    const facts = describeLoanTerms({
      max_term_months: 420,
      fixed_rate_months: 120,
      max_repayment_free_months: 60,
      note: "Zins wird laufend angepasst.",
    });
    const byLabel = new Map(facts.map((f) => [f.label, f]));
    expect(byLabel.get("Längste Laufzeit")?.hint).toBe("35 Jahre");
    expect(byLabel.get("Sollzinsbindung")?.value).toBe("120 Monate");
    expect(byLabel.get("Längste tilgungsfreie Zeit")?.hint).toBe("5 Jahre");
    // Die Notiz ist kein Konditionswert und taucht hier nicht auf.
    expect(facts.some((f) => f.value.includes("laufend"))).toBe(false);
  });

  it("zeigt einen hinterlegten Zinssatz", () => {
    const facts = describeLoanTerms({ nominal_rate_bps_annual: 312 });
    expect(facts[0]).toEqual({ label: "Sollzins", value: "3,12 % p. a." });
  });

  it("bleibt leer, wenn der Katalog keine Konditionen nennt", () => {
    expect(describeLoanTerms({})).toEqual([]);
    expect(describeLoanTerms({ note: "nur ein Hinweis" })).toEqual([]);
    expect(describeLoanTerms(undefined)).toEqual([]);
  });
});

describe("loanTermVariants", () => {
  it("keeps every published rate coupled to its own range", () => {
    const terms = {
      rate_as_of: "2026-08-09",
      variants: [
        {
          id: "annuity_7_10",
          label: "Annuität 7–10 Jahre",
          repayment_type: "annuity",
          min_term_months: 84,
          max_term_months: 120,
          fixed_rate_months: 120,
          min_repayment_free_months: 12,
          max_repayment_free_months: 24,
          nominal_rate_bps_annual: 1,
          effective_rate_bps_annual: 1,
        },
        {
          id: "bullet_7_10",
          label: "Endfälliges Darlehen 7–10 Jahre",
          repayment_type: "bullet",
          min_term_months: 84,
          max_term_months: 120,
          fixed_rate_months: 120,
          min_repayment_free_months: 0,
          max_repayment_free_months: 0,
          nominal_rate_bps_annual: 69,
          effective_rate_bps_annual: 69,
        },
      ],
    };
    const variants = loanTermVariants(terms);
    expect(variants).toHaveLength(2);
    expect(variants[0]).toMatchObject({
      id: "annuity_7_10",
      maxTermMonths: 120,
      nominalRateBpsAnnual: 1,
    });
    expect(variants[1]).toMatchObject({
      repaymentType: "bullet",
      nominalRateBpsAnnual: 69,
    });
    expect(describeLoanTerms(terms)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: "Annuität 7–10 Jahre", value: "0,01 % Sollzins" }),
        expect.objectContaining({ label: "Endfälliges Darlehen 7–10 Jahre" }),
      ]),
    );
  });
});

describe("loanRateOptions", () => {
  it("hält BayernLabo-Zinsbindung, Sollzins und Effektivzins zusammen", () => {
    const terms = {
      rate_options: [
        {
          id: "fixed_10_years",
          label: "10 Jahre Zinsbindung",
          fixed_rate_months: 120,
          nominal_rate_bps_annual: 265,
          effective_rate_bps_annual: 272,
        },
      ],
    };
    expect(loanRateOptions(terms)[0]).toMatchObject({
      id: "fixed_10_years",
      fixedRateMonths: 120,
      nominalRateBpsAnnual: 265,
      effectiveRateBpsAnnual: 272,
    });
    expect(describeLoanTerms(terms)[0]).toMatchObject({
      label: "10 Jahre Zinsbindung",
      value: "2,65 % nominal",
      hint: "2,72 % effektiv",
    });
  });
});

describe("resolveBenefitAmountMinor", () => {
  const jungKauftAlt = {
    type: "loan_tiered",
    tier_field: "children_count",
    tiers: [
      { min: 1, max: 1, amount_minor: 140_000_00 },
      { min: 2, max: 2, amount_minor: 160_000_00 },
      { min: 3, amount_minor: 180_000_00 },
    ],
  };

  it("waehlt die Stufe passend zur Kinderzahl aus dem Foerderprofil", () => {
    expect(resolveBenefitAmountMinor(jungKauftAlt, { children_count: 2 })).toBe(160_000_00);
    expect(resolveBenefitAmountMinor(jungKauftAlt, { children_count: 1 })).toBe(140_000_00);
  });

  it("greift bei der offenen obersten Stufe auch fuer mehr Kinder", () => {
    expect(resolveBenefitAmountMinor(jungKauftAlt, { children_count: 5 })).toBe(180_000_00);
  });

  it("raet nicht, wenn das Merkmal im Profil fehlt oder darunter liegt", () => {
    expect(resolveBenefitAmountMinor(jungKauftAlt, {})).toBeNull();
    expect(resolveBenefitAmountMinor(jungKauftAlt, undefined)).toBeNull();
    expect(resolveBenefitAmountMinor(jungKauftAlt, { children_count: 0 })).toBeNull();
  });

  it("gibt bei einem Festbetrag schlicht diesen zurueck", () => {
    expect(
      resolveBenefitAmountMinor({ type: "loan_fixed", amount_minor: 100_000_00 }, undefined),
    ).toBe(100_000_00);
  });
});
