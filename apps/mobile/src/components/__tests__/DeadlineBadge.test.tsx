/// <reference types="jest" />
import type { ReactElement } from "react";
import { StyleSheet, Text, type TextStyle, type ViewStyle } from "react-native";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { InsurancePolicy, PolicyDeadline } from "../../api/types";
import { DateWithSource, DeadlineBadge, deadlineAccessibilityLabel } from "../DeadlineBadge";
import { isHostElementOfType } from "../testUtils";

// Component-Tests fuer den EINZIGEN Renderpfad eines Fristdatums
// (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 2.4 und 7).
//
// Der teuerste Fehler dieses Moduls ist eine Tageszahl unter einem Datum, das
// niemand geprueft hat: sie sieht aus wie eine Zusage und ist eine Schaetzung.
// Geprueft wird deshalb nicht "die Komponente rendert", sondern die drei
// Zustaende aus der Tabelle im Kopf von DeadlineBadge.tsx - und zwar ueber
// sichtbaren Text, nicht ueber Props.
//
// Die Komponente braucht keine Attrappen: sie liest nur ihr Policenobjekt, das
// Theme hat einen Standardwert (lightTheme in ../../theme) und die Vokabulare
// stehen als Konstanten in ../../api/insurance.

function makeDeadline(overrides: Partial<PolicyDeadline> = {}): PolicyDeadline {
  return {
    anchor: "main_due",
    anchor_basis_kind: "statute",
    deadline_kind: "kuendigungsfrist",
    certainty: "confirmed",
    term_ends_on: "2026-12-31",
    deadline_legal_on: "2026-09-30",
    safe_service_on: "2026-09-28",
    recommended_action_on: "2026-09-21",
    days_left: 47,
    reason: "",
    ...overrides,
  };
}

function makePolicy(overrides: Partial<InsurancePolicy> = {}): InsurancePolicy {
  return {
    id: "pol-hausrat",
    line_key: "hausrat",
    line_label: "Hausratversicherung",
    line_purpose_short: "Ersetzt Hab und Gut nach Feuer, Einbruch, Leitungswasser oder Sturm.",
    risk_field: "property",
    label: null,
    insurer_name: "Beispiel Versicherung AG",
    policy_number: "HR-4711/02",
    customer_number: null,
    tariff_name: null,
    purpose_note: null,
    status: "active",
    starts_on: "2020-04-01",
    ends_on: null,
    main_due_on: "2026-12-31",
    main_due_source: "user_confirmed",
    main_due_confirmed_at: "2026-01-05T10:00:00.000Z",
    notice_anchor: "main_due",
    anchor_basis_kind: "statute",
    notice_period_months: 3,
    notice_terms_source: "user_confirmed",
    deadline_kind: "kuendigungsfrist",
    min_term_months: null,
    renewal_term_months: 12,
    waiting_period_days: null,
    sum_insured_minor: null,
    deductible_minor: null,
    broker_name: null,
    broker_phone: null,
    claims_phone: null,
    service_channel: "post",
    recipient_state: "BY",
    cancelled_on: null,
    cancellation_reason: null,
    dormant_until: null,
    paid_up_benefit_minor: null,
    dynamic_agreed: false,
    attrs: {},
    data_source: "manual",
    line_confirmed_at: null,
    line_deferred_at: null,
    note: null,
    premium: { kind: "none" },
    deadline: makeDeadline(),
    completeness_filled: 6,
    completeness_total: 8,
    next_task: null,
    version: 1,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

/**
 * Dieselbe Police zweimal, unterschieden NUR durch `certainty` - und mit
 * derselben Tageszahl. Ohne das identische `days_left` bewiese ein fehlender
 * Countdown nur, dass keine Zahl da war; die Regel lautet aber, dass die Zahl
 * bei einem angenommenen Datum bewusst verschwiegen wird.
 */
const BESTAETIGT = makePolicy({
  deadline: makeDeadline({ certainty: "confirmed", days_left: 47 }),
});

const ANGENOMMEN = makePolicy({
  main_due_source: "catalog_suggested",
  notice_terms_source: "catalog_suggested",
  deadline: makeDeadline({ certainty: "assumed", days_left: 47 }),
});

let renderedTree: ReactTestRenderer | undefined;

function render(element: ReactElement): ReactTestRenderer {
  let tree: ReactTestRenderer;
  act(() => {
    tree = create(element);
  });
  renderedTree = tree!;
  return tree!;
}

function flattenChildren(children: unknown): string {
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(flattenChildren).join("");
  return "";
}

function textsOf(tree: ReactTestRenderer): string[] {
  return tree.root.findAllByType(Text).map((node) => flattenChildren(node.props.children));
}

function textNode(tree: ReactTestRenderer, value: string) {
  const node = tree.root
    .findAllByType(Text)
    .find((candidate) => flattenChildren(candidate.props.children) === value);
  if (!node) throw new Error(`Kein Textknoten mit dem Inhalt "${value}" gefunden.`);
  return node;
}

function fontWeightOf(tree: ReactTestRenderer, value: string): TextStyle["fontWeight"] {
  return (StyleSheet.flatten(textNode(tree, value).props.style) as TextStyle).fontWeight;
}

/**
 * Die Markierung links vom Datum als Wort. Sie ist das Merkmal, das auch in
 * Graustufen und bei Sonnenlicht traegt - deshalb wird ihre FORM geprueft
 * (voller Punkt / offener Ring / Strich) und nicht ihre Farbe.
 */
function markersOf(tree: ReactTestRenderer): string[] {
  return tree.root
    .findAll((node) => {
      if (!isHostElementOfType(node, "View")) return false;
      const style = StyleSheet.flatten(node.props.style) as ViewStyle | undefined;
      return style?.width === 8;
    })
    .map((node) => {
      const style = StyleSheet.flatten(node.props.style) as ViewStyle;
      if (style.height === 2) return "strich";
      return style.borderWidth ? "ring" : "punkt";
    });
}

afterEach(() => {
  act(() => {
    renderedTree?.unmount();
  });
  renderedTree = undefined;
});

describe("DeadlineBadge - bestaetigt gegen angenommen", () => {
  it("zeigt bei bestaetigter Frist das Datum, die Tageszahl und das Wort 'bestätigt'", () => {
    const texts = textsOf(render(<DeadlineBadge policy={BESTAETIGT} />));

    expect(texts).toContain("30.09.2026");
    expect(texts).toContain("noch 47 Tage");
    expect(texts).toContain("bestätigt");
  });

  it("zeigt bei angenommener Frist dasselbe Datum, aber KEINEN Countdown", () => {
    const texts = textsOf(render(<DeadlineBadge policy={ANGENOMMEN} />));

    expect(texts).toContain("30.09.2026");
    // Die Tageszahl liegt an der Police an (days_left: 47, identisch zur
    // bestaetigten Fassung) - sie wird bewusst verschwiegen, nicht vermisst.
    expect(texts).not.toContain("noch 47 Tage");
    expect(texts.join(" | ")).not.toMatch(/noch \d+ Tage|heute|morgen|vorbei/);
    // Das Wort tritt an die Stelle des Countdowns, es ist nicht seine Fussnote.
    expect(texts).toContain("angenommen");
  });

  it("unterscheidet beide Faelle in drei Merkmalen, nicht nur in der Farbe", () => {
    const confirmed = render(<DeadlineBadge policy={BESTAETIGT} />);
    const confirmedWeight = fontWeightOf(confirmed, "30.09.2026");
    const confirmedMarkers = markersOf(confirmed);
    const confirmedTexts = textsOf(confirmed);
    act(() => {
      confirmed.unmount();
    });

    const assumed = render(<DeadlineBadge policy={ANGENOMMEN} />);
    const assumedWeight = fontWeightOf(assumed, "30.09.2026");

    // (1) Schriftgewicht, (2) Markierungsform, (3) Wortlaut.
    expect(confirmedWeight).toBe("700");
    expect(assumedWeight).toBe("400");
    expect(confirmedMarkers).toEqual(["punkt"]);
    expect(markersOf(assumed)).toEqual(["ring"]);
    expect(confirmedTexts).toContain("bestätigt");
    expect(textsOf(assumed)).toContain("angenommen");
  });

  it("sagt einer Vorlesehilfe die Herkunft an, damit eine geratene Frist nicht wie eine geprüfte klingt", () => {
    // Schriftgewicht und Punktform existieren fuer eine Vorlesehilfe nicht.
    expect(deadlineAccessibilityLabel(BESTAETIGT)).toBe(
      "Nächste Frist 30.09.2026, bestätigt, noch 47 Tage",
    );
    expect(deadlineAccessibilityLabel(ANGENOMMEN)).toBe(
      "Nächste Frist 30.09.2026, angenommen, kein Countdown",
    );
  });

  it("begruendet in der Fristenkarte, warum unter dem angenommenen Datum keine Tageszahl steht", () => {
    const assumed = textsOf(render(<DeadlineBadge policy={ANGENOMMEN} variant="full" />)).join(
      " | ",
    );

    expect(assumed).toMatch(/Kein Countdown, solange das Datum angenommen ist\./);
    // Was genau unsicher ist, entscheidet, was im Web zu bestaetigen waere.
    expect(assumed).toMatch(/Hauptfälligkeit nicht bestätigt/);
    expect(assumed).toMatch(/Fristlänge aus dem Katalog/);
  });

  it("laesst diesen Satz bei einer bestaetigten Frist weg", () => {
    const confirmed = textsOf(render(<DeadlineBadge policy={BESTAETIGT} variant="full" />)).join(
      " | ",
    );

    expect(confirmed).not.toMatch(/Kein Countdown/);
    expect(confirmed).toMatch(/bestätigt \(manuell\)/);
  });

  it("zeigt in der Fristenkarte alle drei Daten samt Feiertagsgrund", () => {
    const policy = makePolicy({
      deadline: makeDeadline({ safe_service_holiday: "Tag der Deutschen Einheit" }),
    });
    const texts = textsOf(render(<DeadlineBadge policy={policy} variant="full" />)).join(" | ");

    expect(texts).toMatch(/Kündigungsfrist/);
    expect(texts).toMatch(
      /Sicher zugestellt bis 28\.09\.2026 \(verschoben wegen Tag der Deutschen Einheit\)/,
    );
    expect(texts).toMatch(/Empfohlen zu handeln bis 21\.09\.2026/);
    expect(texts).toMatch(/Wirksam zum 31\.12\.2026/);
    expect(texts).toMatch(/zur Hauptfälligkeit · Grundlage: Gesetz/);
  });
});

describe("DeadlineBadge - nicht berechenbare Frist", () => {
  const OHNE_FRIST = makePolicy({
    main_due_on: null,
    main_due_source: "not_computable",
    deadline: makeDeadline({
      certainty: "not_computable",
      deadline_legal_on: null,
      safe_service_on: null,
      recommended_action_on: null,
      days_left: null,
      reason: "Ohne Hauptfälligkeit lässt sich kein Kündigungstermin berechnen.",
    }),
  });

  it("zeigt 'Angabe fehlt' statt eines leeren Feldes", () => {
    const tree = render(<DeadlineBadge policy={OHNE_FRIST} />);
    const texts = textsOf(tree);

    expect(texts).toContain("Angabe fehlt");
    // Ein leeres Feld waere die eigentliche Gefahr: es sieht aus wie "nichts zu
    // tun" statt wie "hier fehlt eine Angabe".
    expect(texts.filter((text) => text.trim().length > 0)).not.toHaveLength(0);
    expect(markersOf(tree)).toEqual(["strich"]);
  });

  it("bleibt bei 'Angabe fehlt', auch wenn ein Datum mitkommt - die Sicherheit schlaegt das Datum", () => {
    const widerspruch = makePolicy({
      deadline: makeDeadline({ certainty: "not_computable", deadline_legal_on: "2026-09-30" }),
    });
    const texts = textsOf(render(<DeadlineBadge policy={widerspruch} />));

    expect(texts).toContain("Angabe fehlt");
    expect(texts).not.toContain("30.09.2026");
  });

  it("faellt ohne rechtliches Fristende NICHT auf das Vertragsende zurueck", () => {
    // term_ends_on (31.12.2026) ist gesetzt, deadline_legal_on nicht. Beide
    // koennen Monate auseinanderliegen - das naechstbeste Datum waere hier die
    // falsche Auskunft.
    const ohneLegal = makePolicy({
      deadline: makeDeadline({ certainty: "confirmed", deadline_legal_on: null }),
    });
    const texts = textsOf(render(<DeadlineBadge policy={ohneLegal} />));

    expect(texts).toContain("Angabe fehlt");
    expect(texts).not.toContain("31.12.2026");
    expect(texts).not.toContain("noch 47 Tage");
  });

  it("nennt in der Fristenkarte die Angabe, die fehlt", () => {
    const texts = textsOf(render(<DeadlineBadge policy={OHNE_FRIST} variant="full" />)).join(" | ");

    expect(texts).toMatch(/Ohne Hauptfälligkeit lässt sich kein Kündigungstermin berechnen\./);
  });

  it("sagt einer Vorlesehilfe 'Angabe fehlt, nicht berechenbar' an", () => {
    expect(deadlineAccessibilityLabel(OHNE_FRIST)).toBe(
      "Nächste Frist: Angabe fehlt, nicht berechenbar",
    );
  });
});

describe("DeadlineBadge - Wortlaut des Countdowns", () => {
  function countdownText(daysLeft: number): string[] {
    const policy = makePolicy({
      deadline: makeDeadline({ certainty: "confirmed", days_left: daysLeft }),
    });
    return textsOf(render(<DeadlineBadge policy={policy} />));
  }

  it("sagt 'heute' und 'morgen' statt einer nackten Null oder Eins", () => {
    expect(countdownText(0)).toContain("heute");
    act(() => {
      renderedTree?.unmount();
    });
    expect(countdownText(1)).toContain("morgen");
  });

  it("verschweigt eine verstrichene Frist nicht", () => {
    // Eine negative Tageszahl auszublenden hiesse, den Nutzer im Glauben zu
    // lassen, es sei noch Zeit.
    expect(countdownText(-3)).toContain("seit 3 Tagen vorbei");
    act(() => {
      renderedTree?.unmount();
    });
    expect(countdownText(-1)).toContain("seit 1 Tag vorbei");
  });
});

describe("DateWithSource", () => {
  it("zeichnet eine bestaetigte Hauptfaelligkeit anders aus als eine angenommene", () => {
    const confirmed = render(
      <DateWithSource label="Hauptfälligkeit" date="2026-12-31" source="user_confirmed" />,
    );
    const confirmedTexts = textsOf(confirmed);
    const confirmedWeight = fontWeightOf(confirmed, "31.12.2026");
    act(() => {
      confirmed.unmount();
    });

    const assumed = render(
      <DateWithSource label="Hauptfälligkeit" date="2026-12-31" source="catalog_suggested" />,
    );

    expect(confirmedTexts).toContain("bestätigt (manuell)");
    expect(textsOf(assumed)).toContain("angenommen (Katalog)");
    expect(confirmedWeight).toBe("700");
    expect(fontWeightOf(assumed, "31.12.2026")).toBe("400");
  });

  it("zeigt ohne Datum 'Angabe fehlt' statt einer leeren Zeile", () => {
    const texts = textsOf(
      render(<DateWithSource label="Hauptfälligkeit" date={null} source="not_computable" />),
    );

    expect(texts).toContain("Hauptfälligkeit");
    expect(texts).toContain("Angabe fehlt");
  });
});
