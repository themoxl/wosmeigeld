import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  PremiumHistory,
  SpecialTerminationBand,
  type PremiumRegisterData,
} from "../components/insurance/PremiumHistory";
import {
  openSpecialTermination,
  type InsurancePolicy,
  type PolicyDeadline,
  type PolicyPremium,
  type PremiumHistoryStep,
  type PremiumNotice,
  type SpecialTerminationWindow,
} from "../lib/insuranceApi";

// Diagramme zeichnen auf Canvas und sind in jsdom nicht pruefbar - `Chart`
// faellt dort auf seine Tabelle zurueck (components/charts/Chart.tsx). Genau
// diese Tabelle ist hier die Testoberflaeche: sie traegt dieselben Stufen wie
// die Treppe, weil beide aus `orderedPremiumSteps` kommen.

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

const HEUTE = new Date(2026, 7, 14);

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

function beitrag(overrides: Partial<PolicyPremium> = {}): PolicyPremium {
  return {
    kind: "contract",
    contract_id: "contract-1",
    contract_name: "Hausrat Allianz",
    contract_amount_minor: 1000,
    contract_frequency: "monthly",
    external_premium_minor: null,
    external_frequency: null,
    premium_payer: null,
    premium_payer_note: null,
    premium_share_bp: 10000,
    yearly_in_fixed_costs_minor: 12000,
    ...overrides,
  };
}

function police(overrides: Partial<InsurancePolicy> = {}): InsurancePolicy {
  return {
    id: "p-hausrat",
    line_key: "hausrat",
    line_label: "Hausrat",
    line_purpose_short: "Ersetzt die Einrichtung nach Feuer, Leitungswasser oder Einbruch.",
    risk_field: "property",
    label: null,
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

function stufe(overrides: Partial<PremiumHistoryStep> = {}): PremiumHistoryStep {
  return {
    policy_id: "p-hausrat",
    insurer_name: "Allianz",
    valid_from: "2019-10-01",
    valid_to: null,
    amount_minor: 1000,
    frequency: "monthly",
    yearly_minor: 12000,
    yearly_delta_minor: null,
    source: "contract",
    change_reason: null,
    notice_id: null,
    received_on: null,
    with_benefit_increase: null,
    notice_reason: null,
    is_switch_point: false,
    ...overrides,
  };
}

/** Drei Stufen: 120 -> 144 (+20 %) -> 158,40 Euro im Jahr (+10 %). */
function dreiStufen(): PremiumHistoryStep[] {
  return [
    stufe({ valid_from: "2019-10-01", valid_to: "2023-01-01", amount_minor: 1000 }),
    stufe({
      valid_from: "2023-01-01",
      valid_to: "2025-01-01",
      amount_minor: 1200,
      yearly_minor: 14400,
      yearly_delta_minor: 2400,
      source: "premium_notice",
      notice_id: "n-alt",
      notice_reason: "claims_cost",
      with_benefit_increase: false,
    }),
    stufe({
      valid_from: "2025-01-01",
      amount_minor: 1320,
      yearly_minor: 15840,
      yearly_delta_minor: 1440,
      source: "premium_notice",
      notice_id: "n-neu",
      notice_reason: "index",
      with_benefit_increase: false,
    }),
  ];
}

function fenster(overrides: Partial<SpecialTerminationWindow> = {}): SpecialTerminationWindow {
  return {
    state: "open",
    deadline_kind: "kuendigungsfrist",
    deadline_legal_on: "2026-09-30",
    safe_service_on: "2026-09-25",
    recommended_action_on: "2026-09-18",
    earliest_effect_on: "2026-11-01",
    days_left: 35,
    basis: "§ 40 Abs. 1 S. 1 VVG, Zugang 30.08.2026",
    basis_kind: "statute",
    ...overrides,
  };
}

function schreiben(overrides: Partial<PremiumNotice> = {}): PremiumNotice {
  return {
    id: "n-1",
    policy_id: "p-hausrat",
    received_on: "2026-08-30",
    effective_on: "2026-11-01",
    old_amount_minor: 1200,
    new_amount_minor: 1320,
    comparison_amount_minor: null,
    frequency: "monthly",
    with_benefit_increase: false,
    reason: "claims_cost",
    delta_minor: 120,
    delta_bp: 1000,
    yearly_old_minor: 14400,
    yearly_new_minor: 15840,
    special_termination: fenster(),
    amount_history_id: null,
    confirmed_at: null,
    acknowledged_at: null,
    version: 1,
    created_at: "2026-08-30T09:00:00Z",
    updated_at: "2026-08-30T09:00:00Z",
    ...overrides,
  };
}

function register(overrides: Partial<PremiumRegisterData> = {}): PremiumRegisterData {
  return {
    notices: [],
    steps: dreiStufen(),
    totalIncreaseBp: 3200,
    loading: false,
    error: null,
    busy: false,
    actionError: null,
    canEdit: true,
    successorCandidates: [],
    onCreateNotice: vi.fn(),
    onConfirmNotice: vi.fn(),
    onAckNotice: vi.fn(),
    onReplaceInsurer: vi.fn(),
    ...overrides,
  };
}

let wurzel: HTMLElement;

function renderRegister(
  overrides: Partial<PremiumRegisterData> = {},
  policyOverrides: Partial<InsurancePolicy> = {},
) {
  const data = register(overrides);
  const { container } = render(
    <PremiumHistory policy={police(policyOverrides)} data={data} today={HEUTE} />,
  );
  wurzel = container as HTMLElement;
  return data;
}

/** Die drei Abschnitte ueber ihre Ueberschrift greifen - die Tabellen sehen
 *  einander zu aehnlich, um sie ueber die Rolle zu unterscheiden. */
function abschnitt(id: string): HTMLElement {
  return wurzel.querySelector(`section[aria-labelledby="${id}"]`) as HTMLElement;
}

function zeilen(id: string): HTMLElement[] {
  // Ohne die Kopfzeile: sie steht in `getAllByRole("row")` mit drin.
  return within(abschnitt(id)).getAllByRole("row").slice(1);
}

describe("PremiumHistory – die Treppe", () => {
  it("zeigt jede Stufe mit ihrem Datum und ihrem Betrag", () => {
    renderRegister();

    const stufen = zeilen("insurance-premium-steps");
    expect(stufen).toHaveLength(3);

    // Aelteste zuerst: wer die Reihenfolge dreht, laesst den aeltesten Beitrag
    // wie den heutigen aussehen.
    expect(stufen[0]).toHaveTextContent("01.10.2019");
    expect(stufen[0]).toHaveTextContent("120,00 €");
    expect(stufen[0]).toHaveTextContent("10,00 € · monatlich");
    expect(stufen[1]).toHaveTextContent("01.01.2023");
    expect(stufen[1]).toHaveTextContent("144,00 €");
    expect(stufen[2]).toHaveTextContent("01.01.2025");
    expect(stufen[2]).toHaveTextContent("158,40 €");
    expect(stufen[2]).toHaveTextContent("13,20 € · monatlich");
  });

  it("nennt die Treppe in der Bildunterschrift beim Namen, samt Zahl der Stufen", () => {
    renderRegister();
    expect(
      screen.getByText("Jahresbeitrag von Hausrat als Treppe: 3 Stufen seit 01.10.2019."),
    ).toBeInTheDocument();
  });

  it("vergleicht Jahreswerte und sagt das dazu, samt Gesamtsteigerung", () => {
    // Sonst saehe eine Umstellung von monatlich auf jaehrlich wie eine
    // Erhoehung aus.
    renderRegister();
    expect(screen.getByText(/Jede Stufe hält, bis die nächste beginnt/)).toHaveTextContent(
      "gezeichnet wird der auf ein Jahr umgerechnete Beitrag, damit ein Wechsel der Zahlweise nicht wie eine Erhöhung aussieht.",
    );
    expect(screen.getByText(/Jede Stufe hält/)).toHaveTextContent(
      "Über den gezeigten Zeitraum: +32,0 %.",
    );
  });

  it("liest eine Steigerung als Prozentwert ab, neueste Änderung zuerst", () => {
    renderRegister();

    const aenderungen = zeilen("insurance-premium-changes");
    expect(aenderungen).toHaveLength(2);

    expect(aenderungen[0]).toHaveTextContent("01.01.2025");
    expect(aenderungen[0]).toHaveTextContent("144,00 €");
    expect(aenderungen[0]).toHaveTextContent("158,40 €");
    expect(aenderungen[0]).toHaveTextContent("+10,0 %");
    expect(aenderungen[0]).toHaveTextContent("Anpassung an einen Index");

    expect(aenderungen[1]).toHaveTextContent("01.01.2023");
    expect(aenderungen[1]).toHaveTextContent("+20,0 %");
    expect(aenderungen[1]).toHaveTextContent("gestiegene Schadenkosten");

    // Die Quote steht am Prozentwert, nicht an der Zeile: eine Ampel an der
    // ganzen Zeile faerbte auch das Datum.
    const quote = (aenderungen[0] as HTMLElement).querySelector(".insurance-delta") as HTMLElement;
    expect(quote).toHaveTextContent("+10,0 %");
    expect(quote).toHaveClass("insurance-delta--up");
  });

  it("laesst eine Senkung mit in der Tabelle stehen, mit Minus statt Plus", () => {
    // Die Kfz-Rueckstufung ist eine Stufe nach unten. Fehlte sie, hielte man
    // die Treppe darueber fuer falsch.
    renderRegister({
      steps: [
        stufe({ valid_from: "2024-01-01", yearly_minor: 60000, amount_minor: 60000 }),
        stufe({
          valid_from: "2025-01-01",
          yearly_minor: 54000,
          amount_minor: 54000,
          source: "premium_notice",
          notice_id: "n-sf",
          notice_reason: "sf_class",
        }),
      ],
    });

    const aenderungen = zeilen("insurance-premium-changes");
    expect(aenderungen).toHaveLength(1);
    expect(aenderungen[0]).toHaveTextContent("-10,0 %");
    expect((aenderungen[0] as HTMLElement).querySelector(".insurance-delta")).toHaveClass(
      "insurance-delta--down",
    );
    expect(screen.getByText(/Senkungen stehen mit in der Tabelle/)).toHaveTextContent(
      "Sie löst nur kein Sonderkündigungsrecht aus.",
    );
  });

  it("sagt 'keine Quote', wo es keine gibt - und faerbt trotzdem richtig", () => {
    // Eine beitragsfrei gestellte Police, die wieder Beitrag kostet: von 0,00
    // Euro aus hat der Anstieg keinen Prozentwert. Frueher stand dort "0,0 %"
    // - eine Zahl, die den beiden Betraegen daneben widerspricht - und die
    // Farbe kam aus derselben Null, faerbte den Anstieg also als Senkung.
    renderRegister({
      steps: [
        stufe({ valid_from: "2024-01-01", amount_minor: 0, yearly_minor: 0 }),
        stufe({ valid_from: "2025-01-01", amount_minor: 1320, yearly_minor: 15840 }),
      ],
      totalIncreaseBp: null,
    });

    const aenderungen = zeilen("insurance-premium-changes");
    expect(aenderungen).toHaveLength(1);
    const quote = (aenderungen[0] as HTMLElement).querySelector(".insurance-delta") as HTMLElement;
    expect(quote).toHaveTextContent("keine Quote");
    expect(quote).not.toHaveTextContent("0,0 %");
    expect(quote).toHaveClass("insurance-delta--up");
    // Die beiden Betraege bleiben die Aussage: 0,00 -> 158,40 Euro im Jahr.
    expect(aenderungen[0]).toHaveTextContent("0,00 €");
    expect(aenderungen[0]).toHaveTextContent("158,40 €");
  });

  it("faerbt eine auf 0 bp gerundete Erhoehung als Erhoehung", () => {
    // 2.400,00 -> 2.400,05 Euro im Jahr rundet auf 0 Basispunkte. Haengt die
    // Farbe an der Quote statt an den Betraegen, erscheint der Anstieg gruen
    // als Senkung.
    renderRegister({
      steps: [
        stufe({ valid_from: "2024-01-01", amount_minor: 240000, yearly_minor: 240000 }),
        stufe({ valid_from: "2025-01-01", amount_minor: 240005, yearly_minor: 240005 }),
      ],
      totalIncreaseBp: 0,
    });

    const quote = (zeilen("insurance-premium-changes")[0] as HTMLElement).querySelector(
      ".insurance-delta",
    ) as HTMLElement;
    expect(quote).toHaveTextContent("0,0 %");
    expect(quote).toHaveClass("insurance-delta--up");
  });

  it("liest einen Wechselpunkt als Wechsel und nicht als Erhoehung", () => {
    renderRegister({
      steps: [
        stufe({ valid_from: "2023-01-01", yearly_minor: 14400 }),
        stufe({
          valid_from: "2026-01-01",
          insurer_name: "HUK-COBURG",
          yearly_minor: 11400,
          amount_minor: 950,
          is_switch_point: true,
          source: "contract",
        }),
      ],
    });

    const aenderungen = zeilen("insurance-premium-changes");
    expect(aenderungen[0]).toHaveTextContent("Wechsel zu HUK-COBURG");
    expect(aenderungen[0]).not.toHaveTextContent("aus dem Vertrag (Preisstufe)");
  });

  it("sagt bei einer Police ohne Stufen eine Aussage, statt ein leeres Diagramm zu zeichnen", () => {
    renderRegister({ steps: [], totalIncreaseBp: null });

    expect(
      screen.getByText(/Für diese Police ist noch keine Beitragsstufe bekannt/),
    ).toHaveTextContent("eine einzelne Stufe ist noch keine Treppe.");
    // Kein Diagramm, keine leere Tabelle, keine gerechnete Aussage.
    expect(wurzel.querySelector("figure.chart")).toBeNull();
    expect(within(abschnitt("insurance-premium-steps")).queryByRole("table")).toBeNull();
    expect(screen.queryByText(/Über den gezeigten Zeitraum/)).not.toBeInTheDocument();
    expect(screen.getByText("Zwischen den bekannten Stufen liegt keine Änderung.")).toBeVisible();
  });

  it("unterscheidet 'noch nichts geladen' von 'nichts vorhanden'", () => {
    renderRegister({ steps: [], loading: true, totalIncreaseBp: null });
    expect(screen.getByText("Wird geladen …")).toBeInTheDocument();
    expect(screen.queryByText(/noch keine Beitragsstufe bekannt/)).not.toBeInTheDocument();
  });

  it("stellt eine Stufe ohne Datum in die Tabelle, aber nicht auf die Zeitachse", () => {
    renderRegister({ steps: [stufe({ valid_from: null })], totalIncreaseBp: null });

    expect(wurzel.querySelector("figure.chart")).toBeNull();
    const stufen = zeilen("insurance-premium-steps");
    expect(stufen).toHaveLength(1);
    expect(stufen[0]).toHaveTextContent("seit Beginn, Datum unbekannt");
  });

  it("erklaert, warum ein eben erfasstes Schreiben noch keine Stufe ist", () => {
    renderRegister({ notices: [schreiben({ confirmed_at: null })] });
    expect(
      screen.getByText(
        "Ein erfasstes Anpassungsschreiben ist noch nicht übernommen und steht deshalb nicht als Stufe in der Treppe.",
      ),
    ).toBeInTheDocument();
  });

  it("schweigt darueber, sobald der Betrag uebernommen ist", () => {
    renderRegister({ notices: [schreiben({ confirmed_at: "2026-09-02T09:00:00Z" })] });
    expect(screen.queryByText(/noch nicht übernommen und steht deshalb nicht/)).toBeNull();
  });
});

describe("PremiumHistory – die erfassten Schreiben", () => {
  it("nennt Zugang, Wirksamwerden und den Zustand des Fensters", () => {
    renderRegister({ notices: [schreiben()] });

    const liste = abschnitt("insurance-premium-notices");
    expect(liste).toHaveTextContent("12,00 € → 13,20 € · monatlich · +10,0 %");
    expect(liste).toHaveTextContent("erhalten am 30.08.2026 · gilt ab 01.11.2026");
    expect(liste).toHaveTextContent("Fenster läuft bis 30.09.2026.");
    expect(liste).toHaveTextContent("Betrag noch nicht übernommen.");
  });

  it("nennt ein verfallenes Fenster beim Namen, statt es zu leeren", () => {
    renderRegister({
      notices: [schreiben({ special_termination: fenster({ state: "expired", days_left: null }) })],
    });
    expect(abschnitt("insurance-premium-notices")).toHaveTextContent(
      "Fenster verfallen am 30.09.2026.",
    );
  });

  it("uebernimmt den Betrag ueber die Vertragsseite und sagt das auch", () => {
    const data = renderRegister({ notices: [schreiben()] });
    expect(
      screen.getByText(/„Betrag übernehmen“ schreibt den neuen Betrag über die Vertragsseite fort/),
    ).toHaveTextContent("Die Police führt keinen zweiten Betrag.");
    expect(data.onConfirmNotice).not.toHaveBeenCalled();
  });

  it("laesst ohne Schreibrecht keine Schaltflaeche stehen, die nichts tut", () => {
    renderRegister({ notices: [schreiben()], canEdit: false });
    expect(screen.queryByRole("button", { name: "Betrag übernehmen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Beitragsanpassung erfassen" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Sonderkuendigungsband
// ---------------------------------------------------------------------------

/** Genauso verdrahtet wie in `PolicyDetail`: welches Schreiben ein Band
 *  bekommt, entscheidet `openSpecialTermination` - nicht das Band selbst. */
function renderBand(policy: InsurancePolicy, notices: PremiumNotice[]) {
  const onAck = vi.fn();
  const { container } = render(
    <SpecialTerminationBand
      policy={policy}
      notice={openSpecialTermination(policy, notices)}
      busy={false}
      canEdit
      onAck={onAck}
    />,
  );
  return { container, onAck };
}

function datum(bezeichnung: string): HTMLElement {
  return screen.getByText(bezeichnung).parentElement as HTMLElement;
}

describe("SpecialTerminationBand", () => {
  it("erscheint bei laufendem Fenster und nennt den letzten Tag", () => {
    renderBand(police(), [schreiben()]);

    expect(screen.getByRole("note")).toHaveTextContent("Sonderkündigungsrecht läuft");
    // Der letzte Tag, an dem gekuendigt werden kann - und daneben der Tag, bis
    // zu dem man handeln sollte. Ein einziges Feld "Fristende" koennte die
    // beiden nicht auseinanderhalten.
    expect(datum("Rechtliches Fristende")).toHaveTextContent("30.09.2026");
    expect(datum("Empfohlen absenden bis")).toHaveTextContent("18.09.2026");
    expect(datum("Kündigung wirkt frühestens zum")).toHaveTextContent("01.11.2026");
    expect(screen.getByText(/Der letzte sichere Zustelltag ist/)).toHaveTextContent("25.09.2026");
  });

  it("uebernimmt den Countdown des Servers, statt ihn nachzurechnen", () => {
    // Dieselbe Tageszahl steuert den Push. Zwei Rechnungen ergaeben zwei
    // Zahlen fuer dieselbe Frist.
    renderBand(police(), [schreiben()]);
    expect(datum("Empfohlen absenden bis")).toHaveTextContent("noch 35 Tage");
  });

  it("nennt den Anlass und den Zugang, damit das Band nicht aus dem Nichts kommt", () => {
    renderBand(police(), [schreiben()]);
    const band = screen.getByRole("note");
    expect(band).toHaveTextContent("12,00 € → 13,20 € (monatlich) · +10,0 % ohne Mehrleistung");
    expect(band).toHaveTextContent("gestiegene Schadenkosten");
    expect(band).toHaveTextContent("Schreiben zugegangen am 30.08.2026.");
    expect(band).toHaveTextContent(
      "Grundlage: § 40 Abs. 1 S. 1 VVG, Zugang 30.08.2026 (gesetzlich)",
    );
  });

  it("erscheint NICHT bei einer vereinbarten Dynamik", () => {
    // Leitsatz 3: eine planmaessige Dynamikstufe ist keine Erhoehung im Sinne
    // des § 40 VVG. Ein Band waere hier ein Fehlalarm.
    const { container } = renderBand(police({ dynamic_agreed: true }), [schreiben()]);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("unterdrueckt bei vereinbarter Dynamik den Alarm, nicht die Angabe", () => {
    // Der Server liefert fuer eine dynamische Police `state: "none"`; das
    // Schreiben bleibt im Register sichtbar und sagt, warum es kein Fenster
    // gibt.
    renderRegister(
      {
        notices: [
          schreiben({
            special_termination: fenster({
              state: "none",
              deadline_legal_on: null,
              safe_service_on: null,
              recommended_action_on: null,
              days_left: null,
              basis: "Vereinbarte Dynamik – keine Erhöhung im Sinne des § 40 VVG.",
            }),
          }),
        ],
      },
      { dynamic_agreed: true },
    );

    expect(abschnitt("insurance-premium-notices")).toHaveTextContent(
      "Vereinbarte Dynamik – planmäßige Stufe, kein Sonderkündigungsrecht.",
    );
    expect(screen.queryByText("Zur Kenntnis genommen")).toBeNull();
  });

  it("erscheint nicht mehr, sobald das Fenster verfallen ist", () => {
    const { container } = renderBand(police(), [
      schreiben({ special_termination: fenster({ state: "expired", days_left: null }) }),
    ]);
    expect(container).toBeEmptyDOMElement();
  });

  it("erscheint nicht mehr, sobald der Nutzer es zur Kenntnis genommen hat", () => {
    const { container } = renderBand(police(), [
      schreiben({ acknowledged_at: "2026-09-01T09:00:00Z" }),
    ]);
    expect(container).toBeEmptyDOMElement();
  });

  it("zeigt bei zwei laufenden Fenstern das knappste", () => {
    renderBand(police(), [
      schreiben({
        id: "n-spaet",
        special_termination: fenster({ deadline_legal_on: "2026-11-30" }),
      }),
      schreiben({
        id: "n-knapp",
        special_termination: fenster({
          deadline_legal_on: "2026-09-30",
          recommended_action_on: "2026-09-18",
        }),
      }),
    ]);
    expect(datum("Rechtliches Fristende")).toHaveTextContent("30.09.2026");
  });

  it("beendet mit 'Zur Kenntnis genommen' die Erinnerung, nicht die Frist", async () => {
    const { onAck } = renderBand(police(), [schreiben()]);
    expect(screen.getByRole("note")).toHaveTextContent(
      "beendet die Erinnerungen zu dieser Frist, nicht die Frist selbst.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Zur Kenntnis genommen" }));
    expect(onAck).toHaveBeenCalledWith("n-1");
  });

  it("nennt bei fehlenden Serverdaten 'nicht berechenbar' statt eines leeren Feldes", () => {
    renderBand(police(), [
      schreiben({
        special_termination: fenster({
          deadline_legal_on: null,
          safe_service_on: null,
          recommended_action_on: null,
          earliest_effect_on: null,
        }),
      }),
    ]);

    expect(datum("Rechtliches Fristende")).toHaveTextContent("nicht berechenbar");
    expect(datum("Empfohlen absenden bis")).toHaveTextContent("nicht berechenbar");
    // Das fruehestes Wirkdatum faellt auf das Wirksamwerden zurueck - das steht
    // am Schreiben und ist keine Rechnung.
    expect(datum("Kündigung wirkt frühestens zum")).toHaveTextContent("01.11.2026");
  });
});
