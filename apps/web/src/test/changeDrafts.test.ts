import { describe, expect, it } from "vitest";
import {
  CHANGE_TEMPLATES,
  addMonths,
  draftIssue,
  emptyDraft,
  isIsoDate,
  isMonthValue,
  lastOfMonth,
  normalizeForType,
  syncAmountFromText,
  typeSpec,
  validateDraft,
  withAmount,
} from "../components/forecast/changeDrafts";

// Reine Regeln des Aenderungsentwurfs - ohne Renderer, weil sie ohne Renderer
// gelten. Die Oberflaeche liest sie nur ab.

describe("changeDrafts", () => {
  it("räumt beim Typwechsel auf, was nicht mehr passt", () => {
    const start = emptyDraft("2026-10");
    const withTarget = {
      ...start.change,
      change_type: "expense_change" as const,
      target_contract_id: "c1",
    };

    // Ein Typ ohne Ziel darf keinen Zielvertrag mitschleppen - der Server
    // lehnt das sonst ab.
    const toAdd = normalizeForType(withTarget, "expense_add");
    expect(toAdd.target_contract_id).toBeNull();

    // Beenden hat keinen Betrag.
    const toEnd = normalizeForType(withTarget, "expense_end");
    expect(toEnd.amount_mode).toBeNull();
    expect(toEnd.amount_minor).toBeNull();

    // Einmalzahlung erzwingt den Rhythmus und verbietet ein Ende.
    const toOnce = normalizeForType({ ...start.change, ends_on: "2027-01-01" }, "one_off");
    expect(toOnce.frequency).toBe("once");
    expect(toOnce.ends_on).toBeNull();

    // Zurueck auf einen laufenden Typ: "einmalig" ist dort kein gültiger Wert.
    expect(normalizeForType(toOnce, "expense_add").frequency).toBe("monthly");
  });

  it("leitet das Vorzeichen aus dem Typ ab, nicht aus der Eingabe", () => {
    const base = emptyDraft("2026-10").change;
    expect(withAmount(base, 28000).amount_minor).toBe(-28000);
    expect(withAmount({ ...base, change_type: "income_add" }, 28000).amount_minor).toBe(28000);
    // Auch ein negativ getippter Betrag landet auf der richtigen Seite.
    expect(withAmount(base, -28000).amount_minor).toBe(-28000);
  });

  it("meldet fehlende Angaben, bevor der Server sie ablehnt", () => {
    const draft = emptyDraft("2026-10");
    expect(validateDraft(draft)).toBeNull();

    expect(
      validateDraft({
        ...draft,
        change: { ...draft.change, change_type: "expense_change", target_contract_id: null },
      }),
    ).toBe("Ziel auswählen.");

    expect(validateDraft({ ...draft, change: { ...draft.change, label: "  " } })).toBe(
      "Bezeichnung fehlt.",
    );

    expect(
      validateDraft({
        ...draft,
        change: { ...draft.change, ends_on: "2026-09-01" },
      }),
    ).toBe("Ende liegt vor dem Beginn.");

    expect(validateDraft({ ...draft, change: { ...draft.change, frequency: "once" } })).toBe(
      "„Einmalig“ gibt es nur bei einer Einmalzahlung.",
    );
  });

  it("liefert für jede Vorlage gültige Zeilen", () => {
    for (const template of CHANGE_TEMPLATES) {
      const drafts = template.build("2026-10");
      expect(drafts.length).toBeGreaterThan(0);
      for (const draft of drafts) {
        const spec = typeSpec(draft.change.change_type);
        // Vorlagen ohne Ziel müssen ohne weitere Eingabe rechenbar sein;
        // Vorlagen mit Ziel dürfen genau daran hängen.
        expect(validateDraft(draft)).toBe(spec.needsTarget ? "Ziel auswählen." : null);
        expect(draft.change.starts_on).toBe("2026-10-01");
      }
    }
  });

  it("vergibt für jede Zeile eine eigene Kennung", () => {
    const ids = CHANGE_TEMPLATES.flatMap((t) => t.build("2026-10")).map((d) => d.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("rechnet Monate über die Jahresgrenze", () => {
    expect(addMonths("2026-12", 1)).toBe("2027-01");
    expect(addMonths("2026-01", -1)).toBe("2025-12");
    expect(addMonths("2026-06", 18)).toBe("2027-12");
    expect(addMonths("2026-09", 0)).toBe("2026-09");
  });

  it("trifft das Monatsende auch im Schaltjahr", () => {
    expect(lastOfMonth("2027-10")).toBe("2027-10-31");
    expect(lastOfMonth("2026-02")).toBe("2026-02-28");
    expect(lastOfMonth("2028-02")).toBe("2028-02-29");
    expect(lastOfMonth("2026-04")).toBe("2026-04-30");
  });

  it("weist die im Browser beobachteten Müllwerte ab", () => {
    for (const bad of ["-012-01", "20-0---", "2026-9", "2026-13", "2026-00", ""]) {
      expect(isMonthValue(bad)).toBe(false);
    }
    expect(isMonthValue("2026-01")).toBe(true);
    expect(isMonthValue("2026-12")).toBe(true);

    expect(isIsoDate("-012-01")).toBe(false);
    expect(isIsoDate("2026-02-30")).toBe(false);
    expect(isIsoDate("2026-13-01")).toBe(false);
    expect(isIsoDate("2028-02-29")).toBe(true);
  });

  it("trennt fehlende Angaben von echten Widersprüchen", () => {
    const draft = emptyDraft("2026-10");
    expect(draftIssue(draft)).toBeNull();

    expect(draftIssue({ ...draft, change: { ...draft.change, amount_minor: null } })).toEqual({
      kind: "fehlt",
      message: "Betrag fehlt.",
    });

    // 0 lehnt der Server ausdruecklich ab - im Feld steht aber etwas.
    expect(draftIssue({ ...draft, change: { ...draft.change, amount_minor: 0 } })).toEqual({
      kind: "widerspruch",
      message: "Betrag darf nicht 0 sein.",
    });

    expect(draftIssue({ ...draft, change: { ...draft.change, label: "  " } })?.kind).toBe("fehlt");
    expect(draftIssue({ ...draft, change: { ...draft.change, ends_on: "2026-09-30" } })?.kind).toBe(
      "widerspruch",
    );
  });

  it("lehnt ein kaputtes Datum ab, bevor es an den Server geht", () => {
    const draft = emptyDraft("2026-10");
    expect(validateDraft({ ...draft, change: { ...draft.change, starts_on: "-012-01" } })).toBe(
      "Beginn ist kein gültiger Monat.",
    );
    expect(validateDraft({ ...draft, change: { ...draft.change, starts_on: "0026-01-01" } })).toBe(
      "Beginn liegt außerhalb des Zeitraums.",
    );
    expect(validateDraft({ ...draft, change: { ...draft.change, ends_on: "20-0---" } })).toBe(
      "Ende ist kein gültiger Monat.",
    );
  });

  it("löscht income_kind beim Wechsel auf einen anderen Typ", () => {
    const income = normalizeForType(emptyDraft("2026-10").change, "income_add");
    expect(income.income_kind).toBe("salary");

    // Der Server erlaubt income_kind nur bei income_add - blieb es haengen,
    // scheiterte die ganze Simulation nach einem reinen Typwechsel.
    const changed = normalizeForType(income, "income_change");
    expect(changed.income_kind).toBeNull();
    expect(changed.person_id).toBeNull();
  });

  it("liest den Feldtext nach jedem Wechsel neu ein", () => {
    const base = emptyDraft("2026-10").change;

    const percent = syncAmountFromText({ ...base, amount_mode: "percent" }, "10,5");
    expect(percent.amount_percent).toBe(10.5);
    expect(percent.amount_minor).toBeNull();

    const absolute = syncAmountFromText({ ...percent, amount_mode: "absolute" }, "1.450,00");
    expect(absolute.amount_minor).toBe(-145000);
    expect(absolute.amount_percent).toBeNull();

    // Ein leeres Feld heisst "kein Betrag" - nicht "nimm den alten weiter".
    expect(syncAmountFromText(absolute, "").amount_minor).toBeNull();
    expect(syncAmountFromText({ ...base, amount_mode: "percent" }, "").amount_percent).toBeNull();
  });
});
