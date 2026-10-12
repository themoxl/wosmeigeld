import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NeedsResults, type NeedsResultsProps } from "../components/insurance/NeedsResults";
import {
  DoubleInsurance,
  type DoubleInsuranceProps,
} from "../components/insurance/DoubleInsurance";
import type {
  DuplicateFinding,
  InsurancePolicy,
  NeedsCheckResult,
  NeedsFinding,
  PolicyDeadline,
  PolicyPremium,
} from "../lib/insuranceApi";

/**
 * Die Befundseite des Bedarfsabgleichs (Plan 10.2 bis 10.4, P6).
 *
 * Geprueft wird hier nicht, ob die Regeln richtig rechnen - das tut der
 * Rechenkern und seine eigene Suite. Geprueft wird, ob die Oberflaeche einen
 * fertigen Befund so zeigt, wie ihn die vier Leitsaetze der Phase verlangen:
 * mit seiner Regel, seiner Quelle und seinen Angaben; ohne Note, Prozent und
 * Ampel; mit fehlenden Angaben als EIGENEM Ergebnis statt als Luecke; und mit
 * der Doppelung als Frage statt als Feststellung.
 *
 * Die Doppelversicherung steht mit in dieser Datei und nicht in einer eigenen:
 * sie ist derselbe Bildschirmabschnitt und derselbe Leitsatz ("das Werkzeug
 * behauptet nur, was die Datenlage traegt"). Getrennt gepruefte Nachbarn haetten
 * dieselben Attrappen zweimal.
 */

vi.mock("../hooks/useProfile", async () => {
  const { formatDate, formatDateTime } = await import("../lib/dates");
  return {
    useFormatDate: () => ({
      dateFormat: "de" as const,
      formatDate: (value: string | Date) => formatDate(value, "de"),
      formatDateTime: (value: string | Date) => formatDateTime(value, "de"),
    }),
  };
});

// ---------------------------------------------------------------------------
// Attrappen
// ---------------------------------------------------------------------------

function beitrag(overrides: Partial<PolicyPremium> = {}): PolicyPremium {
  return {
    kind: "contract",
    contract_id: "contract-1",
    contract_name: "Hausrat Allianz",
    contract_amount_minor: 1240,
    contract_frequency: "monthly",
    external_premium_minor: null,
    external_frequency: null,
    premium_payer: null,
    premium_payer_note: null,
    premium_share_bp: 10000,
    yearly_in_fixed_costs_minor: 14880,
    ...overrides,
  };
}

function frist(overrides: Partial<PolicyDeadline> = {}): PolicyDeadline {
  return {
    anchor: "term_end",
    anchor_basis_kind: "statute",
    deadline_kind: "kuendigungsfrist",
    certainty: "confirmed",
    term_ends_on: "2026-12-31",
    deadline_legal_on: "2026-09-30",
    safe_service_on: "2026-09-27",
    recommended_action_on: "2026-09-20",
    days_left: 47,
    reason: "Drei Monate zum Ablauf des Versicherungsjahres.",
    ...overrides,
  };
}

function police(overrides: Partial<InsurancePolicy> = {}): InsurancePolicy {
  return {
    id: "p-hausrat",
    line_key: "household_contents",
    line_label: "Hausrat",
    line_purpose_short: "Ersetzt die Einrichtung nach Feuer, Leitungswasser oder Einbruch.",
    risk_field: "property",
    label: "Hausrat Allianz",
    insurer_name: "Allianz",
    policy_number: "HR-4711",
    customer_number: null,
    tariff_name: null,
    purpose_note: null,
    status: "active",
    starts_on: "2019-10-01",
    ends_on: null,
    main_due_on: "2026-10-01",
    main_due_source: "user_confirmed",
    main_due_confirmed_at: "2026-08-01T09:00:00Z",
    notice_anchor: "term_end",
    anchor_basis_kind: "statute",
    notice_period_months: 3,
    notice_terms_source: "user_confirmed",
    deadline_kind: "kuendigungsfrist",
    min_term_months: 12,
    renewal_term_months: 12,
    waiting_period_days: null,
    sum_insured_minor: null,
    deductible_minor: null,
    broker_name: null,
    broker_phone: null,
    claims_phone: null,
    service_channel: "post",
    recipient_state: null,
    cancelled_on: null,
    cancellation_reason: null,
    dormant_until: null,
    paid_up_benefit_minor: null,
    dynamic_agreed: false,
    attrs: {},
    data_source: "from_contract",
    line_confirmed_at: "2026-08-01T09:00:00Z",
    line_deferred_at: null,
    note: null,
    premium: beitrag(),
    deadline: frist(),
    completeness_filled: 7,
    completeness_total: 9,
    next_task: null,
    version: 1,
    created_at: "2026-08-01T09:00:00Z",
    updated_at: "2026-08-01T09:00:00Z",
    ...overrides,
  };
}

/**
 * Ein Befund, wie ihn `GET /v1/insurance/needs/check` liefert.
 *
 * Alle Saetze stehen hier als Serverdaten und nicht als Erwartung an ein
 * Textbaustein-Repertoire der Oberflaeche - genau das ist die Aussage von
 * Leitsatz 1: formuliert wird im Regelsatz, nicht im JSX.
 */
function befund(overrides: Partial<NeedsFinding> = {}): NeedsFinding {
  return {
    rule_key: "R11",
    risk_field: "property",
    line_key: "elemental",
    line_label: "Elementarschaden",
    outcome: "gap",
    priority: "S1",
    need_tier: "S1",
    headline: "Elementarschutz fehlt",
    rationale:
      "Weil Sie Eigentum besitzen: Überschwemmung, Starkregen und Erdrutsch sind weder in der " +
      "Wohngebäude- noch in der Hausratversicherung enthalten.",
    triggered_by: { housing_form: "Eigentum, selbst bewohnt", living_area_sqm: 140 },
    source_url: "https://www.gdv.de/elementarschaden",
    source_checked_on: "2026-08-05",
    matched_policy_ids: [],
    gap_value_minor: null,
    target_value_minor: null,
    person_id: null,
    person_name: null,
    decision: null,
    ...overrides,
  };
}

function ergebnis(
  findings: NeedsFinding[],
  overrides: Partial<NeedsCheckResult> = {},
): NeedsCheckResult {
  return {
    findings,
    disclaimer:
      "Dieser Abgleich prüft feste Regeln gegen Ihre Angaben. Er ist keine Beratung und ersetzt " +
      "keine Prüfung Ihrer Vertragsbedingungen.",
    checked_on: "2026-08-14",
    ...overrides,
  };
}

function renderBefunde(props: Partial<NeedsResultsProps> = {}) {
  const onDecide = vi.fn();
  const onClearDecision = vi.fn();
  const onOpenPolicy = vi.fn();
  const alle: NeedsResultsProps = {
    result: ergebnis([befund()]),
    policies: [police()],
    loading: false,
    error: null,
    canEdit: true,
    busyRuleKey: null,
    actionError: null,
    onDecide,
    onClearDecision,
    onOpenPolicy,
    ...props,
  };
  const view = render(<NeedsResults {...alle} />);
  return {
    ...view,
    onDecide,
    onClearDecision,
    onOpenPolicy,
    /** Denselben Bildschirm mit neuen Serverdaten zeigen - so, wie es die Seite
     *  nach dem Neurechnen tut. */
    zeige: (patch: Partial<NeedsResultsProps>) =>
      view.rerender(<NeedsResults {...alle} {...patch} />),
  };
}

function renderDoppelung(props: Partial<DoubleInsuranceProps> = {}) {
  const onOpenPolicy = vi.fn();
  const alle: DoubleInsuranceProps = {
    findings: [],
    policies: [],
    loading: false,
    error: null,
    onOpenPolicy,
    ...props,
  };
  const view = render(<DoubleInsurance {...alle} />);
  return { ...view, onOpenPolicy };
}

/** Der Kasten einer Ergebnisgruppe - ueber ihre Ueberschrift gegriffen. Sie
 *  steht bei den offenen Gruppen im `h3`, bei den eingeklappten im `summary`;
 *  der gemeinsame Rahmen traegt beide Male dieselbe Klasse. */
function gruppe(titel: string): HTMLElement {
  const kopf = screen.getByText(titel);
  const rahmen = kopf.closest(".insurance-finding-group");
  if (!rahmen) throw new Error(`Gruppe "${titel}" hat keinen Rahmen`);
  return rahmen as HTMLElement;
}

function befundKarte(headline: string): HTMLElement {
  return screen.getByText(headline).closest("li.insurance-finding") as HTMLElement;
}

// ---------------------------------------------------------------------------
// Pflichtfall 2: der Befund traegt seine Herkunft bei sich
// ---------------------------------------------------------------------------

describe("NeedsResults – ein Befund nennt Regel, Begründung, Quelle und Grundlage", () => {
  it("zeigt Begruendungssatz, Regelschluessel, Quelle und Pruefdatum an derselben Karte", () => {
    renderBefunde({ result: ergebnis([befund()]) });

    const karte = befundKarte("Elementarschutz fehlt");

    // Der Begruendungssatz - woertlich aus dem Regelsatz, nicht umformuliert.
    expect(
      within(karte).getByText(/Weil Sie Eigentum besitzen: Überschwemmung, Starkregen/),
    ).toBeInTheDocument();

    // Die Regel, die den Befund erzeugt hat.
    expect(within(karte).getByText("Regel R11")).toBeInTheDocument();

    // Die Quelle als anklickbarer Beleg, nicht als bloss genannter Name.
    const quelle = within(karte).getByRole("link", {
      name: "https://www.gdv.de/elementarschaden",
    });
    expect(quelle).toHaveAttribute("href", "https://www.gdv.de/elementarschaden");

    // Das Pruefdatum der Quelle - ohne es waere die Quelle eine Behauptung
    // ueber einen unbekannten Stand.
    expect(within(karte).getByText(/geprüft am 05\.08\.2026/)).toBeInTheDocument();
  });

  it("nennt die Angaben, auf denen der Befund beruht, im Klartext", () => {
    renderBefunde({ result: ergebnis([befund()]) });

    const karte = befundKarte("Elementarschutz fehlt");
    expect(within(karte).getByText("Worauf dieser Befund beruht")).toBeInTheDocument();

    const wohnform = within(karte).getByText("Wohnform").closest(".insurance-finding-fact");
    expect(wohnform).not.toBeNull();
    expect(
      within(wohnform as HTMLElement).getByText("Eigentum, selbst bewohnt"),
    ).toBeInTheDocument();

    const flaeche = within(karte).getByText("Wohnfläche").closest(".insurance-finding-fact");
    expect(within(flaeche as HTMLElement).getByText("140")).toBeInTheDocument();

    // Ohne herangezogene Police sagt die Karte das - und nennt die Sparte, die
    // fehlt. Nicht, wo man sie kauft (Leitsatz 3).
    expect(
      within(karte).getByText(/Keine Police herangezogen – erfasst ist keine der Sparte/),
    ).toBeInTheDocument();
  });

  it("macht aus einer fehlenden Angabe in der Grundlage kein „nein“", () => {
    renderBefunde({
      result: ergebnis([
        befund({
          rule_key: "R13",
          outcome: "unclear",
          headline: "Öltank ungeklärt",
          triggered_by: { has_oil_tank: null, housing_form: "Eigentum, selbst bewohnt" },
        }),
      ]),
    });

    const karte = befundKarte("Öltank ungeklärt");
    const oeltank = within(karte).getByText("Öltank").closest(".insurance-finding-fact");
    expect(within(oeltank as HTMLElement).getByText("keine Angabe")).toBeInTheDocument();
    expect(within(oeltank as HTMLElement).queryByText("nein")).toBeNull();
  });

  it("führt die herangezogene Police namentlich und in ihr Detail", async () => {
    const nutzer = userEvent.setup();
    const { onOpenPolicy } = renderBefunde({
      policies: [police({ id: "p-hausrat", label: "Hausrat Allianz" })],
      result: ergebnis([
        befund({
          rule_key: "R15a",
          outcome: "underinsured",
          headline: "Hausratsumme unter dem geprüften Wert",
          matched_policy_ids: ["p-hausrat"],
          target_value_minor: 9100000,
          gap_value_minor: 2100000,
        }),
      ]),
    });

    const karte = befundKarte("Hausratsumme unter dem geprüften Wert");
    await nutzer.click(within(karte).getByRole("button", { name: "Hausrat Allianz" }));
    expect(onOpenPolicy).toHaveBeenCalledWith("p-hausrat");

    // Betraege nur so, wie der Regelsatz sie liefert - beide Zahlen stehen im
    // Befund, keine wird hier gerechnet.
    expect(within(karte).getByText(/Geprüft gegen 91\.000,00/)).toBeInTheDocument();
    expect(within(karte).getByText(/es fehlen 21\.000,00/)).toBeInTheDocument();
  });

  it("zeigt den Hinweis aus dem Antwortkörper, nicht aus dem JSX", () => {
    renderBefunde({
      result: ergebnis([befund()], { disclaimer: "Hinweis aus dem API-Body." }),
    });
    expect(screen.getByText("Hinweis aus dem API-Body.")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Pflichtfall 3: kein Score, kein Prozentwert, keine Gesamtampel
// ---------------------------------------------------------------------------

describe("NeedsResults – kein Score, kein Prozentwert, keine Gesamtampel", () => {
  /** Ein voller Bildschirm: jeder der sieben Ergebniszustaende einmal. Wenn
   *  irgendwo eine Note entstuende, dann hier. */
  const alleZustaende = ergebnis([
    befund({ rule_key: "R11", outcome: "gap", headline: "Elementarschutz fehlt" }),
    befund({
      rule_key: "R2a",
      outcome: "underinsured",
      headline: "Deckungssumme unter dem geprüften Wert",
      matched_policy_ids: ["p-hausrat"],
      target_value_minor: 1000000000,
      gap_value_minor: 500000000,
    }),
    befund({ rule_key: "R13", outcome: "unclear", headline: "Öltank ungeklärt" }),
    befund({ rule_key: "R2", outcome: "fulfilled", headline: "Privathaftpflicht besteht" }),
    befund({
      rule_key: "R17",
      outcome: "dismissed",
      headline: "Rechtsschutz weggelegt",
      decision: {
        decision: "dismissed",
        policy_id: null,
        reason: "self_insured",
        reason_note: null,
        remind_on: null,
        decided_at: "2026-08-10",
      },
    }),
    befund({
      rule_key: "R9a",
      outcome: "overinsured",
      headline: "Vollkasko bei altem Fahrzeug",
      matched_policy_ids: ["p-hausrat"],
    }),
    befund({ rule_key: "R3", outcome: "not_applicable", headline: "Kinder unter sieben" }),
  ]);

  it("enthält im gesamten gerenderten Text keine Note, keine Quote und kein Prozent", () => {
    const { container } = renderBefunde({ result: alleZustaende });
    const text = container.textContent ?? "";

    // Wortformen, die eine Gesamtaussage ueber den Nutzer treffen wuerden.
    for (const wort of [/\bScore\b/i, /\bPunkte?\b/i, /\bNote\b/i, /\bSterne?\b/i, /\bAmpel\b/i]) {
      expect(text, `verbotenes Wort ${String(wort)} im Befundtext`).not.toMatch(wort);
    }

    // Prozent und Quote in jeder Schreibweise.
    expect(text).not.toMatch(/%/);
    expect(text).not.toMatch(/\bProzent\b/i);
    expect(text).not.toMatch(/\d+\s*(?:von|\/)\s*\d+/);

    // Plan 10.2: „fulfilled" heisst im Text nie „abgesichert".
    expect(text).not.toMatch(/abgesichert/i);
  });

  it("zählt die Befunde einer Gruppe nicht", () => {
    renderBefunde({ result: alleZustaende });

    for (const titel of [
      "Wofür kein Schutz erfasst ist",
      "Wofür eine Angabe fehlt",
      "Was die Regeln als vorhanden gefunden haben",
      "Weggelegtes und Nichtzutreffendes",
    ]) {
      // Die Ueberschrift selbst, nicht der Gruppeninhalt: eine Zahl darin waere
      // der erste Schritt zur Note.
      expect(screen.getByText(titel).textContent ?? "").not.toMatch(/\d/);
    }
  });

  it("gibt keinem Ergebniszustand eine eigene Farbe", () => {
    const { container } = renderBefunde({ result: alleZustaende });

    // Sieben Zustaende, sieben Karten - und genau EINE Klassenbelegung. Eine
    // Farbskala ueber die Zustaende waere die Ampel, nur gemalt.
    const karten = Array.from(container.querySelectorAll("li.insurance-finding"));
    expect(karten).toHaveLength(7);
    expect(new Set(karten.map((karte) => karte.className)).size).toBe(1);

    const marken = Array.from(container.querySelectorAll(".insurance-finding-outcome"));
    expect(marken).toHaveLength(7);
    expect(new Set(marken.map((marke) => marke.className)).size).toBe(1);

    // Und kein Messwerk irgendeiner Art.
    expect(container.querySelector("progress")).toBeNull();
    expect(container.querySelector("meter")).toBeNull();
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
    expect(container.querySelector('[role="meter"]')).toBeNull();
    expect(
      container.querySelector('[class*="score"], [class*="gauge"], [class*="ampel"]'),
    ).toBeNull();
  });

  it("nennt den Zustand als Wort", () => {
    renderBefunde({ result: alleZustaende });
    expect(
      within(befundKarte("Elementarschutz fehlt")).getByText("kein Schutz erfasst"),
    ).toBeInTheDocument();
    expect(
      within(befundKarte("Privathaftpflicht besteht")).getByText(
        "Police vorhanden, geprüfter Wert erreicht",
      ),
    ).toBeInTheDocument();
  });

  it("empfiehlt nichts und nennt keinen Anbieter", () => {
    const { container } = renderBefunde({ result: alleZustaende });
    const text = container.textContent ?? "";

    // Verbformen: das Kopfzeilenversprechen „keine Empfehlung" ist die
    // Verneinung und darf stehen bleiben, ein Rat nicht.
    for (const wort of [
      /\bempfehlen\b/i,
      /\bempfiehlt\b/i,
      /\bempfohlen\b/i,
      /Tarifvergleich/i,
      /\bAnbieter\b/i,
      /\bwechseln Sie\b/i,
      /\bkündigen Sie\b/i,
    ]) {
      expect(text, `Beratung im Befundtext: ${String(wort)}`).not.toMatch(wort);
    }
  });

  it("stellt „Police vorhanden, Regel greift nicht“ nicht zu den Lücken", () => {
    renderBefunde({ result: alleZustaende });

    // `overinsured` weit oben und in Signalfarbe waere die Aufforderung zu
    // kuendigen - also Beratung (Leitsatz 3).
    expect(
      within(gruppe("Wofür kein Schutz erfasst ist")).queryByText("Vollkasko bei altem Fahrzeug"),
    ).toBeNull();
    expect(
      within(gruppe("Weggelegtes und Nichtzutreffendes")).getByText("Vollkasko bei altem Fahrzeug"),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Pflichtfall 4: fehlende Angaben sind ein eigenes Ergebnis
// ---------------------------------------------------------------------------

describe("NeedsResults – fehlende Angaben stehen nicht bei den Lücken", () => {
  it("trennt „Angabe fehlt“ von „kein Schutz erfasst“ in zwei Gruppen", () => {
    renderBefunde({
      result: ergebnis([
        befund({ rule_key: "R11", outcome: "gap", headline: "Elementarschutz fehlt" }),
        befund({
          rule_key: "R13",
          outcome: "unclear",
          headline: "Gewässerschaden ungeklärt",
          triggered_by: { has_oil_tank: null },
        }),
      ]),
    });

    const luecken = gruppe("Wofür kein Schutz erfasst ist");
    const offen = gruppe("Wofür eine Angabe fehlt");
    expect(luecken).not.toBe(offen);

    expect(within(luecken).getByText("Elementarschutz fehlt")).toBeInTheDocument();
    expect(within(luecken).queryByText("Gewässerschaden ungeklärt")).toBeNull();

    expect(within(offen).getByText("Gewässerschaden ungeklärt")).toBeInTheDocument();
    expect(within(offen).queryByText("Elementarschutz fehlt")).toBeNull();

    // Und die Gruppe sagt ausdruecklich, dass das kein Fehler ist.
    expect(within(offen).getByText(/Das ist ein Ergebnis und kein Fehler/)).toBeInTheDocument();
  });

  it("stuft die Aussage zurück, solange keine einzige Police erfasst ist", () => {
    renderBefunde({ policies: [], result: ergebnis([befund()]) });

    // Plan 10.2: ein vollstaendig rotes Bild bei leerer Erfassung ist so falsch
    // wie ein gruenes.
    expect(
      within(gruppe("Wofür kein Schutz erfasst ist")).getByText(
        /keine Lücke, sondern der Stand der Erfassung/,
      ),
    ).toBeInTheDocument();
  });

  it("behauptet bei leerer Befundliste nicht, es sei alles in Ordnung", () => {
    renderBefunde({ result: ergebnis([]) });
    expect(screen.getByText(/Das heißt nicht „alles in Ordnung“/)).toBeInTheDocument();
  });

  it("schweigt vollständig, solange der Endpunkt fehlt", () => {
    const { container } = renderBefunde({ result: null, unavailable: true, error: "404" });
    expect(container).toBeEmptyDOMElement();
  });
});

// ---------------------------------------------------------------------------
// Pflichtfall 5: die Entscheidung des Nutzers verschiebt den Befund
// ---------------------------------------------------------------------------

describe("NeedsResults – „Trifft auf mich nicht zu“", () => {
  it("fragt nach dem Grund und meldet die Entscheidung regelweit", async () => {
    const nutzer = userEvent.setup();
    const { onDecide } = renderBefunde({
      result: ergebnis([befund({ rule_key: "R17", headline: "Rechtsschutz fehlt" })]),
    });

    const karte = befundKarte("Rechtsschutz fehlt");
    await nutzer.click(within(karte).getByRole("button", { name: "Trifft auf mich nicht zu" }));

    const grund = within(karte).getByLabelText("Warum trifft das nicht zu?");
    expect(grund).toHaveValue("not_applicable");

    await nutzer.click(within(karte).getByRole("button", { name: "Weglegen" }));

    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith("R17", {
      decision: "dismissed",
      // Der Befund zeigt auf eine FEHLENDE Police - die Entscheidung gilt der
      // Regel, nicht einem Vertrag.
      policy_id: null,
      reason: "not_applicable",
      reason_note: null,
      remind_on: null,
    });
  });

  it("verschiebt den Befund sichtbar in die verworfene Gruppe", async () => {
    const nutzer = userEvent.setup();
    const { zeige } = renderBefunde({
      result: ergebnis([befund({ rule_key: "R17", headline: "Rechtsschutz fehlt" })]),
    });

    expect(
      within(gruppe("Wofür kein Schutz erfasst ist")).getByText("Rechtsschutz fehlt"),
    ).toBeInTheDocument();

    await nutzer.click(
      within(befundKarte("Rechtsschutz fehlt")).getByRole("button", {
        name: "Trifft auf mich nicht zu",
      }),
    );
    await nutzer.click(
      within(befundKarte("Rechtsschutz fehlt")).getByRole("button", { name: "Weglegen" }),
    );

    // So kommt der Befund nach dem Neurechnen vom Server zurueck.
    zeige({
      result: ergebnis([
        befund({
          rule_key: "R17",
          headline: "Rechtsschutz fehlt",
          outcome: "dismissed",
          decision: {
            decision: "dismissed",
            policy_id: null,
            reason: "not_applicable",
            reason_note: null,
            remind_on: null,
            decided_at: "2026-08-14",
          },
        }),
      ]),
    });

    // Die Luecken-Gruppe ist leer und damit ganz verschwunden.
    expect(screen.queryByText("Wofür kein Schutz erfasst ist")).toBeNull();

    const weggelegt = gruppe("Weggelegtes und Nichtzutreffendes");
    expect(within(weggelegt).getByText("Rechtsschutz fehlt")).toBeInTheDocument();
    expect(within(weggelegt).getByText(/Von Ihnen weggelegt/)).toBeInTheDocument();
    expect(
      within(weggelegt).getByRole("button", { name: "Entscheidung zurücknehmen" }),
    ).toBeInTheDocument();
  });

  it("macht aus einem Wiedervorlagedatum eine Wiedervorlage statt eines Weglegens", async () => {
    const nutzer = userEvent.setup();
    const { onDecide } = renderBefunde({
      result: ergebnis([befund({ rule_key: "R17", headline: "Rechtsschutz fehlt" })]),
    });

    const karte = befundKarte("Rechtsschutz fehlt");
    await nutzer.click(within(karte).getByRole("button", { name: "Trifft auf mich nicht zu" }));
    await nutzer.selectOptions(
      within(karte).getByLabelText("Warum trifft das nicht zu?"),
      "planned",
    );
    await nutzer.type(within(karte).getByLabelText("Notiz (freiwillig)"), "erst nach dem Umzug");
    fireEvent.change(within(karte).getByLabelText("Wieder vorlegen am"), {
      target: { value: "2027-01-31" },
    });
    await nutzer.click(within(karte).getByRole("button", { name: "Weglegen" }));

    expect(onDecide).toHaveBeenCalledWith("R17", {
      decision: "snoozed",
      policy_id: null,
      reason: "planned",
      reason_note: "erst nach dem Umzug",
      remind_on: "2027-01-31",
    });
  });

  it("hängt die Entscheidung an die Police, wo der Befund auf genau eine zeigt", async () => {
    const nutzer = userEvent.setup();
    const { onDecide } = renderBefunde({
      result: ergebnis([
        befund({
          rule_key: "R2a",
          outcome: "underinsured",
          headline: "Deckungssumme unter dem geprüften Wert",
          matched_policy_ids: ["p-hausrat"],
        }),
      ]),
    });

    const karte = befundKarte("Deckungssumme unter dem geprüften Wert");
    await nutzer.click(within(karte).getByRole("button", { name: "Stimmt, kümmere ich mich" }));

    expect(onDecide).toHaveBeenCalledWith("R2a", {
      decision: "accepted",
      policy_id: "p-hausrat",
      reason: "planned",
    });
  });

  it("bietet zu einer ohnehin nicht zutreffenden Regel keine Antwort an", () => {
    renderBefunde({
      result: ergebnis([
        befund({ rule_key: "R3", outcome: "not_applicable", headline: "Kinder unter sieben" }),
      ]),
    });

    const karte = befundKarte("Kinder unter sieben");
    expect(within(karte).queryByRole("button", { name: "Trifft auf mich nicht zu" })).toBeNull();
    expect(within(karte).queryByRole("button", { name: "Stimmt, kümmere ich mich" })).toBeNull();
  });

  it("zeigt einem Lesenden keine Schaltflächen", () => {
    renderBefunde({ canEdit: false, result: ergebnis([befund()]) });
    const karte = befundKarte("Elementarschutz fehlt");
    expect(within(karte).queryByRole("button", { name: /kümmere|Trifft auf mich/ })).toBeNull();
  });

  it("sperrt beim Speichern nur die betroffene Regel", () => {
    renderBefunde({
      busyRuleKey: "R11",
      result: ergebnis([
        befund({ rule_key: "R11", headline: "Elementarschutz fehlt" }),
        befund({ rule_key: "R17", headline: "Rechtsschutz fehlt" }),
      ]),
    });

    expect(
      within(befundKarte("Elementarschutz fehlt")).getByRole("button", {
        name: "Stimmt, kümmere ich mich",
      }),
    ).toBeDisabled();
    expect(
      within(befundKarte("Rechtsschutz fehlt")).getByRole("button", {
        name: "Stimmt, kümmere ich mich",
      }),
    ).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------
// Pflichtfall 6: Doppelversicherung als Frage
// ---------------------------------------------------------------------------

function doppelung(overrides: Partial<DuplicateFinding> = {}): DuplicateFinding {
  return {
    kind: "contained_in",
    policy_id: "p-glas",
    other_policy_id: "p-hausrat",
    policy_label: "Glasversicherung Provinzial",
    other_policy_label: "Hausrat Allianz",
    line_key: "glass",
    line_label: "Glasversicherung",
    coverage_key: "glass",
    coverage_label: "Glasbruch",
    yearly_minor: 8400,
    rationale:
      "Glasbruch ist im Katalog als Baustein der Hausratversicherung geführt und bei Ihrer " +
      "Hausratpolice als erfasst hinterlegt.",
    certainty: "established",
    ...overrides,
  };
}

describe("DoubleInsurance – als Frage, mit beiden Policen", () => {
  it("formuliert die Doppelung im Konjunktiv und nicht als Feststellung", () => {
    const { container } = renderDoppelung({ findings: [doppelung()] });

    expect(
      screen.getByRole("heading", { name: "Könnte doppelt versichert sein" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Glasbruch könnte doppelt versichert sein")).toBeInTheDocument();

    const text = container.textContent ?? "";
    expect(text).toMatch(/könnte/);
    // Keine Feststellung ueber Papier, das dieses Werkzeug nie gesehen hat.
    expect(text).not.toMatch(/ist doppelt versichert/);
    expect(text).not.toMatch(/\bsind doppelt\b/);
  });

  it("nennt beide Policen namentlich und führt in beide Details", async () => {
    const nutzer = userEvent.setup();
    const { onOpenPolicy } = renderDoppelung({ findings: [doppelung()] });

    const karte = screen
      .getByText("Glasbruch könnte doppelt versichert sein")
      .closest("li.insurance-duplicate") as HTMLElement;

    await nutzer.click(within(karte).getByRole("button", { name: "Glasversicherung Provinzial" }));
    await nutzer.click(within(karte).getByRole("button", { name: "Hausrat Allianz" }));

    expect(onOpenPolicy).toHaveBeenNthCalledWith(1, "p-glas");
    expect(onOpenPolicy).toHaveBeenNthCalledWith(2, "p-hausrat");
  });

  it("fällt auf den Policennamen aus dem Bestand zurück, wenn der Server keinen mitgibt", () => {
    renderDoppelung({
      policies: [police({ id: "p-hausrat", label: "Hausrat Allianz" })],
      findings: [
        doppelung({
          policy_id: "p-hausrat",
          other_policy_id: "p-glas",
          policy_label: null,
          other_policy_label: null,
        }),
      ],
    });

    expect(screen.getByRole("button", { name: "Hausrat Allianz" })).toBeInTheDocument();
    // Die unbekannte Police bekommt einen Platzhalter statt einer UUID.
    expect(screen.getByRole("button", { name: "Police ohne Namen" })).toBeInTheDocument();
  });

  it("zeigt die Rechtsfolge und keinen Rat", () => {
    const { container } = renderDoppelung({ findings: [doppelung()] });
    const text = container.textContent ?? "";

    expect(text).toMatch(/doppelte Beiträge bringen kein doppeltes Geld/);
    expect(text).toMatch(/der später geschlossene Vertrag fristlos kündbar/);
    // Der betroffene Beitrag ist ausdruecklich keine Ersparnis.
    expect(screen.getByText(/die Zahl ist keine Ersparnis/)).toBeInTheDocument();

    for (const wort of [
      /\bkündigen Sie\b/i,
      /\bempfehlen\b/i,
      /Tarifvergleich/i,
      /\bsparen Sie\b/i,
    ]) {
      expect(text, `Rat statt Rechtsfolge: ${String(wort)}`).not.toMatch(wort);
    }
  });

  it("macht aus einer unbelegten Vermutung eine Aufgabe statt eines Befundes", () => {
    renderDoppelung({
      policies: [police({ id: "p-hausrat", label: "Hausrat Allianz" })],
      findings: [
        doppelung({
          certainty: "unclear",
          coverage_label: "Fahrraddiebstahl",
          missing_coverage_policy_ids: ["p-hausrat"],
        }),
      ],
    });

    // Plan 10.4: ein Befund, der sich selbst entwertet, ist schlechter als
    // keiner - deshalb steht hier die Aufgabe.
    const aufgabe = screen
      .getByRole("heading", { name: "Dafür fehlen noch Angaben" })
      .closest(".insurance-duplicate-pending") as HTMLElement;

    expect(
      within(aufgabe).getByText("Fahrraddiebstahl könnte doppelt versichert sein"),
    ).toBeInTheDocument();
    expect(within(aufgabe).getByText(/Bausteine erfassen bei:/)).toBeInTheDocument();
    expect(within(aufgabe).getByRole("button", { name: "Hausrat Allianz" })).toBeInTheDocument();
  });

  it("behauptet ohne Befund keine Prüfung", () => {
    const { container } = renderDoppelung({ findings: [] });
    expect(container).toBeEmptyDOMElement();
  });
});

/**
 * Die vier Fehler, die bei der Abnahme gefunden und behoben wurden.
 *
 * Sie stehen zusammen, weil sie dieselbe Ursache haben: die Oberflaeche liess
 * Felder liegen, die der Server ausdruecklich fuer sie fuellt, und formulierte
 * stattdessen selbst. Genau daraus entstanden Saetze, die nicht stimmten.
 */
describe("Befunde – die Korrekturen der Abnahme", () => {
  it("erklärt eine abgelaufene Wiedervorlage im Perfekt und lässt wieder antworten", () => {
    renderBefunde({
      result: ergebnis([
        befund({
          rule_key: "R17",
          outcome: "gap",
          decision: {
            decision: "snoozed",
            policy_id: null,
            reason: "planned",
            reason_note: null,
            remind_on: "2026-01-31",
            decided_at: "2025-12-01T10:00:00Z",
            expired: true,
          },
        }),
      ]),
    });

    // Der Befund ist zurueck - der Satz darf keine Rueckkehr in der Zukunft
    // versprechen, die laengst eingetreten ist.
    expect(screen.queryByText(/Kommt am 31\.01\.2026 zurück/)).toBeNull();
    expect(screen.getByText(/Der Tag ist erreicht/)).toBeInTheDocument();
    expect(screen.getByText(/Sie hatten das vertagt/)).toBeInTheDocument();

    // Und beide Antworten stehen wieder offen: ein vertagter Befund muss
    // beantwortbar bleiben, nicht nur löschbar.
    expect(screen.getByRole("button", { name: "Stimmt, kümmere ich mich" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trifft auf mich nicht zu" })).toBeInTheDocument();
  });

  it("nennt eine vertagte Entscheidung nicht „weggelegt“", () => {
    renderBefunde({
      result: ergebnis([
        befund({
          decision: {
            decision: "snoozed",
            policy_id: null,
            reason: "planned",
            reason_note: null,
            remind_on: "2027-01-31",
            decided_at: "2026-08-01T10:00:00Z",
            expired: false,
          },
        }),
      ]),
    });

    expect(screen.getByText(/Von Ihnen vertagt/)).toBeInTheDocument();
    expect(screen.queryByText(/Von Ihnen weggelegt/)).toBeNull();
    expect(screen.getByText(/Kommt am 31\.01\.2027 zurück/)).toBeInTheDocument();
  });

  it("zeigt neben der Regelbegründung auch die Ergebnisbegründung des Servers", () => {
    renderBefunde({
      result: ergebnis([
        befund({ outcome_reason: "Keine laufende Police dieser Sparte erfasst." }),
      ]),
    });

    // Zwei verschiedene Saetze: der eine sagt, wofuer die Sparte da ist, der
    // andere, was am Bestand DIESES Haushalts gefunden wurde.
    expect(screen.getByText(/Weil Sie Eigentum besitzen/)).toBeInTheDocument();
    expect(screen.getByText("Keine laufende Police dieser Sparte erfasst.")).toBeInTheDocument();
  });

  it("sagt den Ergebnissatz nicht zweimal, wenn der Dienst die Begründung ersetzt hat", () => {
    const satz = "Diese Regel wurde nicht geprüft, weil eine Angabe fehlt: Rücklage.";
    renderBefunde({
      result: ergebnis([befund({ outcome: "unclear", rationale: satz, outcome_reason: satz })]),
    });
    expect(screen.getAllByText(satz)).toHaveLength(1);
  });

  it("übernimmt bei der Doppelung die Aufgabe des Servers statt einer eigenen Fassung", () => {
    renderDoppelung({
      findings: [
        doppelung({
          certainty: "unclear",
          hint: "Bausteine der Police Hausrat Allianz erfassen.",
        }),
      ],
    });
    expect(screen.getByText("Bausteine der Police Hausrat Allianz erfassen.")).toBeInTheDocument();
  });
});
