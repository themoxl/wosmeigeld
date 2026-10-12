import type { SplitProvenance } from "../api/types";

// Klartext zur Herkunft einer Kategoriezuordnung
// (docs/ANALYSE-EXPLORER-PLAN.md, Baustein A).
//
// Bewusst als reine Funktionen ohne React: so ist die Formulierung ohne
// Renderer testbar, und Web wie Mobile koennen dieselben Regeln nachziehen.
// Rohe reason_code-Werte oder Modellprozente werden nie als Erklaerung
// ausgegeben (docs/KI-FINANZASSISTENT-PLAN.md, "Zielbild der Oberflaeche").

const FIELD_LABELS: Record<string, string> = {
  payee_raw: "der Zahlungsempfänger",
  purpose: "der Verwendungszweck",
  amount_min: "der Betrag mindestens",
  amount_max: "der Betrag höchstens",
  account: "das Konto",
};

export const SOURCE_LABELS: Record<SplitProvenance["category_source"], string> = {
  unset: "Ohne Kategorie",
  unknown: "Herkunft nicht erfasst",
  manual: "Von Hand zugeordnet",
  import: "Beim Import gewählt",
  rule: "Durch Regel zugeordnet",
  ai: "Von der KI vorgeschlagen",
  history: "Aus der Haushaltshistorie",
};

function formatMinorUnits(minor: number): string {
  return `${(minor / 100).toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} €`;
}

/** Der Name, unter dem die Regel gegriffen hat - notfalls der heutige. */
export function provenanceRuleName(split: SplitProvenance): string | null {
  return split.matched_on?.rule_name ?? split.rule_name ?? null;
}

/**
 * Ein Satz, der die Kategoriezuordnung erklaert - oder null, wenn es nichts
 * zu erklaeren gibt. Gibt nie eine Begruendung aus, die die Daten nicht
 * hergeben.
 */
export function provenanceExplanation(split: SplitProvenance): string | null {
  switch (split.category_source) {
    case "rule": {
      const name = provenanceRuleName(split);
      const criteria = split.matched_on?.criteria ?? [];
      const reasons = criteria.map((criterion) => {
        const label = FIELD_LABELS[criterion.field] ?? criterion.field;
        if (criterion.field === "amount_min" || criterion.field === "amount_max") {
          return `${label} ${formatMinorUnits(Number(criterion.pattern))} beträgt`;
        }
        if (criterion.field === "account") return `${label} übereinstimmt`;
        return `${label} „${criterion.pattern}“ enthält`;
      });

      let sentence = `Gesetzt durch die Regel „${name ?? "ohne Namen"}“`;
      if (split.rule_priority !== null) sentence += ` (Priorität ${split.rule_priority})`;
      if (reasons.length > 0) sentence += `, weil ${reasons.join(" und ")}`;
      sentence += ".";
      if (split.rule_is_active === false) sentence += " Diese Regel ist inzwischen deaktiviert.";
      if (split.rule_id === null) sentence += " Diese Regel wurde inzwischen gelöscht.";
      return sentence;
    }
    case "ai": {
      let sentence = "Vorschlag der lokalen KI";
      if (split.model_name) sentence += ` (Modell ${split.model_name})`;
      if (split.confidence !== null) {
        sentence += `, Sicherheit ${Math.round(split.confidence * 100)} %`;
      }
      return `${sentence}, von einer Person bestätigt.`;
    }
    case "import":
      return "Beim Übernehmen des Imports ausdrücklich gewählt.";
    case "manual":
      return "Von Hand in der App zugeordnet.";
    case "unknown":
      return (
        "Diese Zuordnung stammt aus der Zeit vor der Herkunftsaufzeichnung. " +
        "Ab jetzt wird bei jeder Änderung festgehalten, wodurch sie entstanden ist."
      );
    case "unset":
      return "Weder eine Regel noch die KI noch eine Person hat bisher eine Kategorie gesetzt.";
    default:
      return null;
  }
}
