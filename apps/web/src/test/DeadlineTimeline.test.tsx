import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DeadlineTimeline } from "../components/insurance/DeadlineTimeline";
import type { InsurancePolicy, PolicyDeadline, PolicyPremium } from "../lib/insuranceApi";

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

/**
 * Ankertag aller Tests: 14. August 2026.
 *
 * Fest verdrahtet, weil das Fenster der Achse am Monatsersten beginnt und
 * zwoelf Monate weit reicht. Mit dem echten Heute laege dasselbe Testdatum je
 * nach Laufmonat einmal auf der Achse und einmal darunter - der Test wuerde
 * dann nicht die Komponente pruefen, sondern den Kalender.
 */
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

/** Eine Frist, an der der Rechenkern gescheitert ist. Alle abgeleiteten Daten
 *  fallen mit weg - sonst pruefte der Test einen Zustand, den es nicht gibt. */
function ohneFrist(overrides: Partial<PolicyDeadline> = {}): PolicyDeadline {
  return frist({
    certainty: "not_computable",
    term_ends_on: null,
    deadline_legal_on: null,
    safe_service_on: null,
    recommended_action_on: null,
    days_left: null,
    reason: "Hauptfälligkeit fehlt – ohne sie gibt es keinen Kündigungstermin.",
    ...overrides,
  });
}

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

function renderLeiste(
  policies: InsurancePolicy[],
  options: { selectedPolicyId?: string | null; interaktiv?: boolean } = {},
) {
  const onSelectPolicy = vi.fn();
  const view = render(
    <DeadlineTimeline
      policies={policies}
      selectedPolicyId={options.selectedPolicyId ?? null}
      onSelectPolicy={options.interaktiv === false ? undefined : onSelectPolicy}
      today={HEUTE}
    />,
  );
  return { ...view, onSelectPolicy };
}

/** Die Marke einer Police ueber ihren Barrierenamen greifen - er ist die
 *  einzige Angabe, die Name, Datum und Sicherheit zusammenfuehrt. */
function marke(name: RegExp): HTMLElement {
  return screen.getByRole("button", { name });
}

function stielhoehe(mark: HTMLElement): string {
  return (mark.querySelector(".insurance-tl-pin") as HTMLElement).style.height;
}

/** Die Hinweiszeile unter der Achse. Sie steht als <p> um ein <strong>; der
 *  Text der Zaehlung liegt im <strong>, die Namen daneben. */
function hinweis(muster: RegExp): HTMLElement {
  return screen.getByText(muster).closest("p") as HTMLElement;
}

describe("DeadlineTimeline – bestätigt gegen angenommen", () => {
  it("laesst eine angenommene Frist auf der Achse nie aussehen wie eine bestaetigte", () => {
    renderLeiste([
      police({
        id: "p-bestaetigt",
        main_due_source: "user_confirmed",
        notice_terms_source: "user_confirmed",
        deadline: frist({ certainty: "confirmed", days_left: 47 }),
      }),
      police({
        id: "p-angenommen",
        line_key: "kfz_haftpflicht",
        line_label: "Kfz-Haftpflicht",
        insurer_name: "HUK",
        main_due_source: "catalog_suggested",
        notice_terms_source: "catalog_suggested",
        // Derselbe Termin, dieselbe Tageszahl vom Server. Der Unterschied darf
        // also nur aus der Herkunft stammen und nicht daraus, dass der einen
        // Police schlicht Daten fehlen.
        deadline: frist({ certainty: "assumed", days_left: 47 }),
      }),
    ]);

    const bestaetigt = marke(/^Hausrat, 30\.09\.2026, bestätigt$/);
    const angenommen = marke(/^Kfz-Haftpflicht, 30\.09\.2026, angenommen – kein Countdown$/);

    // Unterschieden wird in der FORM, nicht nur in der Farbe: eigene Klasse,
    // Fragezeichen am Namen - und der Countdown haengt allein am bestaetigten
    // Termin (Leitsatz 1).
    expect(bestaetigt).toHaveClass("insurance-tl-mark--confirmed");
    expect(angenommen).toHaveClass("insurance-tl-mark--assumed");
    expect(within(bestaetigt).getByText("Hausrat")).toBeInTheDocument();
    expect(within(angenommen).getByText("Kfz-Haftpflicht ?")).toBeInTheDocument();
    expect(within(bestaetigt).getByText("noch 47 Tage")).toBeInTheDocument();
    expect(within(angenommen).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
    expect(within(angenommen).queryByText(/Tage/)).not.toBeInTheDocument();
  });

  it("sagt im Kopf und in der Legende, was die blasse Marke bedeutet", () => {
    renderLeiste([police({ deadline: frist({ certainty: "assumed" }) })]);
    expect(
      screen.getByText("Angenommene Termine stehen blass und ohne Countdown"),
    ).toBeInTheDocument();
    expect(screen.getByText("angenommen – Fragezeichen statt Countdown")).toBeInTheDocument();
    expect(screen.getByText("bestätigte Frist, mit Countdown")).toBeInTheDocument();
  });

  it("faerbt den Countdown nach Restzeit und benennt eine verstrichene Frist", () => {
    renderLeiste([
      police({ id: "p-nah", deadline: frist({ days_left: 12 }) }),
      police({
        id: "p-fern",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        deadline: frist({ deadline_legal_on: "2027-03-31", days_left: 229 }),
      }),
      police({
        id: "p-vorbei",
        line_label: "Zahnzusatz",
        insurer_name: "DKV",
        deadline: frist({ deadline_legal_on: "2026-08-03", days_left: -11 }),
      }),
    ]);

    expect(within(marke(/^Hausrat,/)).getByText("noch 12 Tage")).toHaveClass(
      "insurance-tl-days--warning",
    );
    expect(within(marke(/^Rechtsschutz,/)).getByText("noch 229 Tage")).toHaveClass(
      "insurance-tl-days--neutral",
    );
    // Eine verstrichene Frist zu verschweigen hiesse, den Nutzer im Glauben zu
    // lassen, es sei noch Zeit.
    expect(within(marke(/^Zahnzusatz,/)).getByText("seit 11 Tagen vorbei")).toBeInTheDocument();
  });
});

describe("DeadlineTimeline – was nicht auf die Achse gehört", () => {
  it("stellt Policen ohne berechenbare Frist unter die Achse, mit Anzahl statt Platz", () => {
    renderLeiste([
      police({ id: "p-hausrat" }),
      police({
        id: "p-ohne-1",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        main_due_on: null,
        main_due_source: "not_computable",
        deadline: ohneFrist(),
      }),
      police({
        id: "p-ohne-2",
        line_label: "Privathaftpflicht",
        insurer_name: "HDI",
        main_due_on: null,
        main_due_source: "not_computable",
        deadline: ohneFrist(),
      }),
      police({
        id: "p-ohne-3",
        line_label: "Zahnzusatz",
        insurer_name: "DKV",
        main_due_on: null,
        main_due_source: "not_computable",
        deadline: ohneFrist(),
      }),
    ]);

    // Ein geschaetzter Platz auf der Achse waere ein erfundener Termin - die
    // drei stehen deshalb NICHT dort.
    expect(marke(/^Hausrat,/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Rechtsschutz,/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Privathaftpflicht,/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Zahnzusatz,/ })).not.toBeInTheDocument();

    const zeile = hinweis(/3 Policen ohne berechenbare Frist/);
    expect(zeile).toHaveTextContent("Rechtsschutz");
    expect(zeile).toHaveTextContent("Privathaftpflicht");
    expect(zeile).toHaveTextContent("Zahnzusatz");
    expect(zeile).toHaveTextContent("Sie stehen nicht auf der Achse, weil eine Angabe fehlt");
  });

  it("haelt eine nicht berechenbare Frist auch dann von der Achse fern, wenn ein Datum mitkommt", () => {
    // Der schaerfere Fall: der Rechner meldet "nicht berechenbar", liefert
    // aber trotzdem ein Datum. Entscheidet die Achse dann am Datum statt an
    // der Sicherheit, bekommt eine Police einen Platz, den ihr Ergebnis
    // ausdruecklich nicht traegt.
    renderLeiste([
      police({ id: "p-hausrat" }),
      police({
        id: "p-unberechenbar",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        deadline: frist({
          certainty: "not_computable",
          deadline_legal_on: "2026-09-30",
          days_left: null,
        }),
      }),
    ]);

    expect(screen.queryByRole("button", { name: /^Rechtsschutz,/ })).not.toBeInTheDocument();
    expect(hinweis(/1 Police ohne berechenbare Frist/)).toHaveTextContent("Rechtsschutz");
  });

  it("zaehlt eine einzelne fehlende Frist im Singular", () => {
    renderLeiste([
      police({ id: "p-hausrat" }),
      police({
        id: "p-ohne",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        deadline: ohneFrist(),
      }),
    ]);
    const zeile = hinweis(/1 Police ohne berechenbare Frist/);
    expect(zeile).toHaveTextContent("Sie steht nicht auf der Achse");
  });

  it("meldet auch eine Frist ohne Fristende als nicht berechenbar, nicht als Vertragsende", () => {
    // Der Rechner haelt die Frist fuer bestaetigt, liefert aber kein
    // rechtliches Fristende. Faellt die Achse dann auf `term_ends_on` zurueck,
    // stuende die Marke unter einem Datum, zu dem sie nicht gehoert.
    renderLeiste([
      police({
        deadline: frist({
          certainty: "confirmed",
          deadline_legal_on: null,
          term_ends_on: "2026-12-31",
        }),
      }),
    ]);
    expect(screen.queryByRole("button", { name: /^Hausrat,/ })).not.toBeInTheDocument();
    expect(hinweis(/1 Police ohne berechenbare Frist/)).toHaveTextContent("Hausrat");
  });

  it("zaehlt eine beendete Police nicht als fehlende Frist", () => {
    // An einer beendeten Police ist nichts mehr offen; sie als "ohne
    // berechenbare Frist" zu melden waere keine Luecke, sondern eine
    // Falschmeldung.
    renderLeiste([
      police({ id: "p-hausrat" }),
      police({
        id: "p-beendet",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        status: "ended",
        deadline: ohneFrist(),
      }),
      police({
        id: "p-gekuendigt",
        line_label: "Zahnzusatz",
        insurer_name: "DKV",
        status: "cancelled",
        deadline: frist(),
      }),
    ]);

    expect(screen.queryByText(/ohne berechenbare Frist/)).not.toBeInTheDocument();
    // Auch die gekuendigte Police mit gueltigem Datum bleibt von der Achse weg.
    expect(screen.queryByRole("button", { name: /^Zahnzusatz,/ })).not.toBeInTheDocument();
    expect(marke(/^Hausrat,/)).toBeInTheDocument();
  });

  it("nimmt ruhende und beitragsfreie Policen auf die Achse", () => {
    renderLeiste([
      police({ id: "p-ruhend", status: "suspended" }),
      police({
        id: "p-beitragsfrei",
        line_label: "Lebensversicherung",
        insurer_name: "Alte Leipziger",
        status: "paid_up",
      }),
    ]);
    expect(marke(/^Hausrat,/)).toBeInTheDocument();
    expect(marke(/^Lebensversicherung,/)).toBeInTheDocument();
  });

  it("trennt Termine jenseits der zwoelf Monate von den fehlenden", () => {
    renderLeiste([
      police({ id: "p-hausrat" }),
      police({
        id: "p-spaet",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        deadline: frist({ deadline_legal_on: "2028-01-15", days_left: 519 }),
      }),
    ]);

    expect(screen.queryByRole("button", { name: /^Rechtsschutz,/ })).not.toBeInTheDocument();
    expect(hinweis(/Ein weiterer Termin liegt nach diesen zwölf Monaten/)).toHaveTextContent(
      "Rechtsschutz",
    );
    expect(screen.queryByText(/ohne berechenbare Frist/)).not.toBeInTheDocument();
  });

  it("meldet einen verstrichenen Termin als verstrichen und nicht als spaeteren", () => {
    // Dieselbe verpasste Frist bekam je nach Tag im Monat zwei gegenlaeufige
    // Auskuenfte: auf der Achse "seit Tagen vorbei", nach dem Monatswechsel
    // aber "ausserhalb dieser zwoelf Monate" - was nach *spaeter* klingt.
    renderLeiste([
      police({ id: "p-hausrat" }),
      police({
        id: "p-vorbei",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        deadline: frist({ deadline_legal_on: "2026-05-04", days_left: -102 }),
      }),
    ]);

    expect(screen.queryByRole("button", { name: /^Rechtsschutz,/ })).not.toBeInTheDocument();
    expect(hinweis(/Ein Termin ist bereits verstrichen/)).toHaveTextContent("Rechtsschutz");
    expect(screen.queryByText(/nach diesen zwölf Monaten/)).not.toBeInTheDocument();
    // Und er ist keine fehlende Angabe - die Frist ist berechenbar, nur vorbei.
    expect(screen.queryByText(/ohne berechenbare Frist/)).not.toBeInTheDocument();
  });

  it("kappt lange Namenslisten, statt zwanzig Namen in einen Satz zu setzen", () => {
    const ohne = Array.from({ length: 7 }, (_, index) =>
      police({
        id: `p-ohne-${index}`,
        line_label: `Sparte ${index + 1}`,
        insurer_name: "Beispiel",
        deadline: ohneFrist(),
      }),
    );
    renderLeiste([police({ id: "p-hausrat" }), ...ohne]);

    const zeile = hinweis(/7 Policen ohne berechenbare Frist/);
    expect(zeile).toHaveTextContent("Sparte 6 und 1 weitere");
    expect(zeile).not.toHaveTextContent("Sparte 7");
  });

  it("sagt bei leerer Achse, dass kein Termin da ist, statt nur leer zu sein", () => {
    renderLeiste([police({ deadline: ohneFrist() })]);
    expect(
      screen.getByText("In den nächsten zwölf Monaten liegt kein berechenbarer Termin."),
    ).toBeInTheDocument();
  });

  it("verschwindet ohne jede Police ganz", () => {
    // Eine leere Achse behauptete, es gaebe etwas zu sehen; die leere Liste
    // ueber ihr hat das schon gesagt.
    const { container } = renderLeiste([]);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("DeadlineTimeline – Achse und Bedienung", () => {
  it("laesst zwei Termine im selben Monat zwei Marken bleiben", () => {
    renderLeiste([
      police({ id: "p-eins", deadline: frist({ deadline_legal_on: "2026-11-05", days_left: 83 }) }),
      police({
        id: "p-zwei",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        deadline: frist({ deadline_legal_on: "2026-11-20", days_left: 98 }),
      }),
    ]);

    const erste = marke(/^Hausrat, 05\.11\.2026/);
    const zweite = marke(/^Rechtsschutz, 20\.11\.2026/);

    // Der Platz kommt aus dem Tagesanteil, nicht aus einem Monatsraster -
    // sonst laegen beide Marken uebereinander.
    expect(erste.style.left).not.toBe(zweite.style.left);
    // Und wo die Beschriftungen kollidieren, waechst die Achse in die Hoehe,
    // statt eine Marke zu verstecken: der Stiel der zweiten reicht weiter.
    expect(stielhoehe(erste)).not.toBe(stielhoehe(zweite));
  });

  it("beschriftet die Achse mit zwoelf Monaten und dem Jahr nur, wo es wechselt", () => {
    renderLeiste([police()]);
    const achse = screen.getByRole("region", { name: "Fristenleiste über zwölf Monate" });

    expect(achse.querySelectorAll(".insurance-tl-month")).toHaveLength(12);
    // August 2026 als Fensterbeginn, Januar 2027 als Jahreswechsel - sonst
    // stuenden zwoelf Jahreszahlen nebeneinander.
    expect(within(achse).getByText("2026")).toBeInTheDocument();
    expect(within(achse).getByText("2027")).toBeInTheDocument();
    expect(within(achse).getAllByText(/^20\d\d$/)).toHaveLength(2);
  });

  it("meldet die angeklickte Police und zeigt die offene Herleitung an der Marke", async () => {
    const { onSelectPolicy } = renderLeiste([police()], { selectedPolicyId: "p-hausrat" });
    const mark = marke(/^Hausrat,/);

    expect(mark).toHaveAttribute("aria-pressed", "true");
    expect(mark).toHaveClass("insurance-tl-mark--selected");

    await userEvent.click(mark);
    expect(onSelectPolicy).toHaveBeenCalledWith("p-hausrat");
  });

  it("laesst ohne Empfaenger keine Schaltflaeche stehen, die nichts tut", () => {
    renderLeiste([police()], { interaktiv: false });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    // Lesbar bleibt die Marke trotzdem.
    expect(screen.getByText("Hausrat")).toBeInTheDocument();
  });

  it("nennt am Rand der Marke Datum und Herkunft der Frist", () => {
    renderLeiste([
      police({
        main_due_source: "catalog_suggested",
        notice_terms_source: "derived_from_days",
        deadline: frist({ certainty: "assumed" }),
      }),
    ]);
    expect(marke(/^Hausrat,/)).toHaveAttribute(
      "title",
      "30.09.2026 · angenommen (Katalog) · Hauptfälligkeit nicht bestätigt · Frist aus Kündigungstagen umgerechnet",
    );
  });
});
