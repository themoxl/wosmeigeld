import type { SplitProvenance } from "../api/types";
import { provenanceExplanation, provenanceRuleName } from "./provenanceText";

// docs/ANALYSE-EXPLORER-PLAN.md, Baustein A: die Oberflaeche muss aus den
// Herkunftsfeldern einen lesbaren Satz machen - und darf nichts behaupten,
// was die Daten nicht hergeben.

const base: SplitProvenance = {
  split_id: "split-1",
  amount_minor: -4250,
  category_id: "cat-1",
  category_name: "Lebensmittel",
  category_group_name: "Lebensmittel & Gastronomie",
  category_is_archived: false,
  category_source: "unset",
  rule_id: null,
  rule_name: null,
  rule_priority: null,
  rule_is_active: null,
  matched_on: null,
  decision_id: null,
  model_name: null,
  reason_code: null,
  confidence: null,
  categorized_at: null,
  categorized_by: null,
};

function split(overrides: Partial<SplitProvenance>): SplitProvenance {
  return { ...base, ...overrides };
}

describe("provenanceExplanation", () => {
  it("nennt Regel, Prioritaet und das getroffene Kriterium", () => {
    const text = provenanceExplanation(
      split({
        category_source: "rule",
        rule_id: "rule-1",
        rule_name: "REWE ist Supermarkt",
        rule_priority: 100,
        rule_is_active: true,
        matched_on: {
          rule_name: "REWE ist Supermarkt",
          criteria: [{ field: "purpose", pattern: "REWE" }],
        },
      }),
    );

    expect(text).toContain("REWE ist Supermarkt");
    expect(text).toContain("Priorität 100");
    expect(text).toContain("weil der Verwendungszweck");
    expect(text).toContain("REWE");
  });

  it("verkettet mehrere Kriterien mit 'und'", () => {
    const text = provenanceExplanation(
      split({
        category_source: "rule",
        rule_id: "rule-1",
        rule_priority: 0,
        rule_is_active: true,
        matched_on: {
          rule_name: "Grosse Lastschriften",
          criteria: [
            { field: "payee_raw", pattern: "STADTWERKE" },
            { field: "amount_min", pattern: "-50000" },
          ],
        },
      }),
    );

    expect(text).toContain("der Zahlungsempfänger");
    expect(text).toContain(" und ");
    expect(text).toContain("-500,00 €");
  });

  it("weist auf eine geloeschte Regel hin, statt den Namen zu verschweigen", () => {
    const text = provenanceExplanation(
      split({
        category_source: "rule",
        rule_id: null,
        rule_name: null,
        rule_priority: null,
        matched_on: { rule_name: "Geloeschte Regel", criteria: [] },
      }),
    );

    expect(text).toContain("Geloeschte Regel");
    expect(text).toContain("inzwischen gelöscht");
  });

  it("gibt bei der KI Modell und Sicherheit aus, aber keinen reason_code", () => {
    const text = provenanceExplanation(
      split({
        category_source: "ai",
        decision_id: "dec-1",
        model_name: "qwen3-14b",
        reason_code: "merchant_and_context_match",
        confidence: 0.87,
      }),
    );

    expect(text).toContain("qwen3-14b");
    expect(text).toContain("87 %");
    expect(text).not.toContain("merchant_and_context_match");
  });

  it("behauptet fuer Altbestand keine Herkunft", () => {
    const text = provenanceExplanation(split({ category_source: "unknown" }));
    expect(text).toContain("vor der Herkunftsaufzeichnung");
  });

  it("sagt bei fehlender Kategorie, dass niemand zugeordnet hat", () => {
    const text = provenanceExplanation(
      split({ category_source: "unset", category_id: null, category_name: null }),
    );
    expect(text).toContain("Weder eine Regel");
  });
});

describe("provenanceRuleName", () => {
  it("bevorzugt den eingefrorenen Namen vor dem heutigen", () => {
    const name = provenanceRuleName(
      split({
        category_source: "rule",
        rule_name: "Heute umbenannt",
        matched_on: { rule_name: "Damals so genannt", criteria: [] },
      }),
    );
    expect(name).toBe("Damals so genannt");
  });

  it("faellt auf den heutigen Namen zurueck, wenn nichts eingefroren ist", () => {
    const name = provenanceRuleName(
      split({ category_source: "rule", rule_name: "Nur heute bekannt" }),
    );
    expect(name).toBe("Nur heute bekannt");
  });
});
