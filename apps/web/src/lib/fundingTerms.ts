import { formatMinorUnitsAsEuro } from "./money";

// Macht die im Foerderkatalog hinterlegten Konditionen lesbar.
//
// `benefit_json` ist eine kleine DSL mit mehreren Betragstypen (Festbetrag,
// gestaffelt nach Kinderzahl, Prozent der Kosten, je Einheit, kombiniert),
// `loan_terms_json` ein flaches Objekt mit Darlehenskonditionen. Beides steht
// im Katalog, war aber nirgends sichtbar - wer ein Programm auswaehlte, sah
// nicht, welche Hoechstbetraege und Fristen dahinterstehen.
//
// Beide Funktionen geben bewusst nur wieder, was WIRKLICH hinterlegt ist.
// Fehlt ein Wert, erscheint er gar nicht, statt mit einer plausiblen Zahl
// gefuellt zu werden: eine erfundene Kondition sieht aus wie eine Zusage.

export interface CatalogFact {
  label: string;
  value: string;
  /** Zusatz, etwa woraus sich eine Staffel ergibt. */
  hint?: string;
}

export interface LoanTermVariant {
  id: string;
  label: string;
  repaymentType: "annuity" | "bullet";
  minTermMonths: number;
  maxTermMonths: number;
  fixedRateMonths: number;
  minRepaymentFreeMonths: number;
  maxRepaymentFreeMonths: number;
  nominalRateBpsAnnual: number;
  effectiveRateBpsAnnual: number;
}

export interface LoanRateOption {
  id: string;
  label: string;
  fixedRateMonths: number;
  nominalRateBpsAnnual: number;
  effectiveRateBpsAnnual: number;
}

/** Alternative Zinsbindungen ohne erfundene Gesamtlaufzeit (z. B. BayernLabo). */
export function loanRateOptions(terms: Record<string, unknown> | undefined): LoanRateOption[] {
  const rawOptions = terms?.["rate_options"];
  if (!Array.isArray(rawOptions)) return [];
  return rawOptions.flatMap((raw) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as Record<string, unknown>;
    if (
      typeof item.id !== "string" ||
      typeof item.label !== "string" ||
      typeof item.fixed_rate_months !== "number" ||
      typeof item.nominal_rate_bps_annual !== "number" ||
      typeof item.effective_rate_bps_annual !== "number"
    ) {
      return [];
    }
    return [
      {
        id: item.id,
        label: item.label,
        fixedRateMonths: item.fixed_rate_months,
        nominalRateBpsAnnual: item.nominal_rate_bps_annual,
        effectiveRateBpsAnnual: item.effective_rate_bps_annual,
      },
    ];
  });
}

/** Liest die validierten Konditionsvarianten aus dem offenen JSONB-Feld. */
export function loanTermVariants(terms: Record<string, unknown> | undefined): LoanTermVariant[] {
  const rawVariants = terms?.["variants"];
  if (!Array.isArray(rawVariants)) return [];
  return rawVariants.flatMap((raw) => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as Record<string, unknown>;
    const repaymentType = item.repayment_type;
    const id = item.id;
    const label = item.label;
    const values = [
      item.min_term_months,
      item.max_term_months,
      item.fixed_rate_months,
      item.min_repayment_free_months,
      item.max_repayment_free_months,
      item.nominal_rate_bps_annual,
      item.effective_rate_bps_annual,
    ];
    if (
      typeof id !== "string" ||
      typeof label !== "string" ||
      (repaymentType !== "annuity" && repaymentType !== "bullet") ||
      values.some((value) => typeof value !== "number" || !Number.isFinite(value))
    ) {
      return [];
    }
    return [
      {
        id,
        label,
        repaymentType,
        minTermMonths: item.min_term_months as number,
        maxTermMonths: item.max_term_months as number,
        fixedRateMonths: item.fixed_rate_months as number,
        minRepaymentFreeMonths: item.min_repayment_free_months as number,
        maxRepaymentFreeMonths: item.max_repayment_free_months as number,
        nominalRateBpsAnnual: item.nominal_rate_bps_annual as number,
        effectiveRateBpsAnnual: item.effective_rate_bps_annual as number,
      },
    ];
  });
}

function num(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const TIER_FIELD_LABELS: Record<string, string> = {
  children_count: "Kinder im Haushalt",
  household_size: "Haushaltsgröße",
};

/** Beschreibt den Foerderbetrag; leer, wenn der Katalog keinen nennt. */
export function describeBenefit(benefit: Record<string, unknown> | undefined): CatalogFact[] {
  if (!benefit) return [];
  const type = benefit["type"];

  if (type === "loan_fixed" || type === "fixed_grant") {
    const amount = num(benefit, "amount_minor");
    if (amount === null) return [];
    return [
      {
        label: type === "loan_fixed" ? "Höchstbetrag Darlehen" : "Zuschuss",
        value: formatMinorUnitsAsEuro(amount),
      },
    ];
  }

  if (type === "loan_tiered") {
    const tiers = Array.isArray(benefit["tiers"]) ? (benefit["tiers"] as unknown[]) : [];
    const field = typeof benefit["tier_field"] === "string" ? benefit["tier_field"] : null;
    const rows: CatalogFact[] = [];
    for (const raw of tiers) {
      if (typeof raw !== "object" || raw === null) continue;
      const tier = raw as Record<string, unknown>;
      const amount = num(tier, "amount_minor");
      if (amount === null) continue;
      const min = num(tier, "min");
      const max = num(tier, "max");
      let range: string;
      if (min !== null && max !== null) {
        range = min === max ? `${min}` : `${min}–${max}`;
      } else if (min !== null) {
        range = `ab ${min}`;
      } else if (max !== null) {
        range = `bis ${max}`;
      } else {
        range = "—";
      }
      rows.push({
        label:
          `Höchstbetrag bei ${range} ${field ? (TIER_FIELD_LABELS[field] ?? field) : ""}`.trim(),
        value: formatMinorUnitsAsEuro(amount),
      });
    }
    return rows;
  }

  if (type === "percent_of_costs" || type === "loan_percent_of_costs") {
    const rate = num(benefit, "rate_bps");
    const cap = num(benefit, "cap_minor");
    if (rate === null) return [];
    const tags = Array.isArray(benefit["eligible_funding_tags"])
      ? (benefit["eligible_funding_tags"] as unknown[]).filter((t) => typeof t === "string")
      : [];
    return [
      {
        label: type === "loan_percent_of_costs" ? "Darlehensanteil" : "Fördersatz",
        value: `${(rate / 100).toLocaleString("de-DE")} % der förderfähigen Kosten`,
        hint: tags.length > 0 ? `Gilt für: ${tags.join(", ")}` : undefined,
      },
      ...(cap !== null ? [{ label: "Höchstbetrag", value: formatMinorUnitsAsEuro(cap) }] : []),
    ];
  }

  if (type === "per_unit") {
    const perUnit = num(benefit, "amount_per_unit_minor");
    if (perUnit === null) return [];
    const unit = benefit["unit_field"];
    return [
      {
        label: unit === "children_count" ? "Kinderzuschuss je Kind" : "Zuschuss je Einheit",
        value: formatMinorUnitsAsEuro(perUnit),
      },
    ];
  }

  if (type === "combined") {
    const components = Array.isArray(benefit["components"])
      ? (benefit["components"] as unknown[])
      : [];
    return components.flatMap((component) =>
      component && typeof component === "object"
        ? describeBenefit(component as Record<string, unknown>)
        : [],
    );
  }

  return [];
}

/**
 * Loest den fuer diesen Haushalt geltenden Betrag auf.
 *
 * Bei einer Staffel (`loan_tiered`) entscheidet ein Merkmal aus dem
 * Foerderprofil, etwa die Kinderzahl - "Jung kauft Alt" zahlt bei zwei Kindern
 * mehr als bei einem. Fehlt das Merkmal im Profil, wird bewusst NICHTS
 * geraten: eine willkuerlich gewaehlte Stufe waere eine erfundene Zusage.
 */
export function resolveBenefitAmountMinor(
  benefit: Record<string, unknown> | undefined,
  facts: Record<string, unknown> | undefined,
): number | null {
  if (!benefit) return null;
  const type = benefit["type"];

  if (type === "loan_fixed" || type === "fixed_grant") {
    return num(benefit, "amount_minor");
  }

  if (type === "loan_tiered") {
    const field = typeof benefit["tier_field"] === "string" ? benefit["tier_field"] : null;
    if (!field || !facts) return null;
    const actual = facts[field];
    if (typeof actual !== "number" || !Number.isFinite(actual)) return null;
    const tiers = Array.isArray(benefit["tiers"]) ? (benefit["tiers"] as unknown[]) : [];
    for (const raw of tiers) {
      if (typeof raw !== "object" || raw === null) continue;
      const tier = raw as Record<string, unknown>;
      const min = num(tier, "min");
      const max = num(tier, "max");
      if (min !== null && actual < min) continue;
      if (max !== null && actual > max) continue;
      return num(tier, "amount_minor");
    }
  }

  return null;
}

/** Beschreibt die Darlehenskonditionen; leer, wenn nichts hinterlegt ist. */
export function describeLoanTerms(terms: Record<string, unknown> | undefined): CatalogFact[] {
  if (!terms) return [];
  const rateOptions = loanRateOptions(terms);
  if (rateOptions.length > 0) {
    const asOf = typeof terms["rate_as_of"] === "string" ? terms["rate_as_of"] : null;
    return [
      ...rateOptions.map((option) => ({
        label: option.label,
        value: `${(option.nominalRateBpsAnnual / 100).toLocaleString("de-DE", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })} % nominal`,
        hint: `${(option.effectiveRateBpsAnnual / 100).toLocaleString("de-DE", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })} % effektiv`,
      })),
      ...(asOf ? [{ label: "Konditionsstand", value: asOf }] : []),
    ];
  }
  const variants = loanTermVariants(terms);
  if (variants.length > 0) {
    return variants.map((variant) => ({
      label: variant.label,
      value: `${(variant.nominalRateBpsAnnual / 100).toLocaleString("de-DE", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })} % Sollzins`,
      hint:
        variant.repaymentType === "bullet"
          ? "Nur Zinsen während der Laufzeit, Gesamttilgung am Ende"
          : `${variant.minRepaymentFreeMonths / 12}–${variant.maxRepaymentFreeMonths / 12} tilgungsfreie Jahre`,
    }));
  }
  const facts: CatalogFact[] = [];

  const rate = num(terms, "nominal_rate_bps_annual");
  if (rate !== null) {
    facts.push({ label: "Sollzins", value: `${(rate / 100).toLocaleString("de-DE")} % p. a.` });
  }
  const subsidized = num(terms, "subsidized_rate_bps_annual");
  const subsidizedMonths = num(terms, "subsidized_months");
  if (subsidized !== null) {
    facts.push({
      label: "Verbilligter Zins",
      value: `${(subsidized / 100).toLocaleString("de-DE")} % p. a.`,
      hint: subsidizedMonths !== null ? `für ${subsidizedMonths} Monate` : undefined,
    });
  }
  const term = num(terms, "max_term_months");
  if (term !== null) {
    facts.push({
      label: "Längste Laufzeit",
      value: `${term} Monate`,
      hint: `${Math.round(term / 12)} Jahre`,
    });
  }
  const fixed = num(terms, "fixed_rate_months");
  if (fixed !== null) {
    facts.push({
      label: "Sollzinsbindung",
      value: `${fixed} Monate`,
      hint: `${Math.round(fixed / 12)} Jahre`,
    });
  }
  const free = num(terms, "max_repayment_free_months");
  if (free !== null) {
    facts.push({
      label: "Längste tilgungsfreie Zeit",
      value: `${free} Monate`,
      hint: `${Math.round(free / 12)} Jahre`,
    });
  }
  const repayment = num(terms, "initial_repayment_rate_bps");
  if (repayment !== null) {
    facts.push({
      label: "Anfängliche Tilgung",
      value: `${(repayment / 100).toLocaleString("de-DE")} %`,
    });
  }
  return facts;
}
