import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PolicyList } from "../components/insurance/PolicyList";
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

/** Zeile ueber die Beschriftung des Oeffnen-Knopfes greifen: sie ist die
 *  einzige Angabe, die Sparte und Versicherer zusammen fuehrt. */
function zeile(lineLabel: string, insurer: string): HTMLElement {
  const button = screen.getByRole("button", { name: `${lineLabel} bei ${insurer} öffnen` });
  return button.closest("tr") as HTMLElement;
}

function renderListe(policies: InsurancePolicy[], onSelectPolicy = vi.fn()) {
  render(
    <PolicyList policies={policies} selectedPolicyId={null} onSelectPolicy={onSelectPolicy} />,
  );
  return onSelectPolicy;
}

describe("PolicyList", () => {
  it("laesst ein angenommenes Datum nie aussehen wie ein bestaetigtes", () => {
    renderListe([
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
        line_purpose_short: "Zahlt Schaeden, die mit dem Fahrzeug bei anderen entstehen.",
        insurer_name: "HUK",
        main_due_source: "catalog_suggested",
        notice_terms_source: "catalog_suggested",
        deadline: frist({ certainty: "assumed", days_left: 47 }),
      }),
    ]);

    // Beide Policen tragen dasselbe Datum und dieselbe Tageszahl. Der
    // Unterschied darf also nur aus der Herkunft stammen und nicht daraus,
    // dass der einen Police schlicht Daten fehlen.
    const bestaetigt = zeile("Hausrat", "Allianz");
    expect(within(bestaetigt).getByText("30.09.2026")).toBeInTheDocument();
    expect(within(bestaetigt).getByText("bestätigt (manuell)")).toBeInTheDocument();
    expect(within(bestaetigt).getByText("noch 47 Tage")).toBeInTheDocument();
    expect(within(bestaetigt).queryByText(/angenommen/)).not.toBeInTheDocument();

    const angenommen = zeile("Kfz-Haftpflicht", "HUK");
    expect(within(angenommen).getByText("30.09.2026")).toBeInTheDocument();
    expect(within(angenommen).getByText(/^angenommen \(Katalog\)/)).toBeInTheDocument();
    expect(within(angenommen).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
    expect(
      within(angenommen).getByText("kein Countdown, solange das Datum angenommen ist"),
    ).toBeInTheDocument();
  });

  it("nennt bei der angenommenen Frist auch, was zu bestaetigen waere", () => {
    renderListe([
      police({
        main_due_source: "catalog_suggested",
        notice_terms_source: "derived_from_days",
        deadline: frist({ certainty: "assumed" }),
      }),
    ]);

    const zelle = within(zeile("Hausrat", "Allianz")).getByText(/^angenommen \(Katalog\)/);
    expect(zelle).toHaveTextContent("Hauptfälligkeit nicht bestätigt");
    expect(zelle).toHaveTextContent("Frist aus Kündigungstagen umgerechnet");
  });

  it("zeigt eine abgelaufene bestaetigte Frist als vergangene Zeit", () => {
    renderListe([police({ deadline: frist({ certainty: "confirmed", days_left: -3 }) })]);
    expect(
      within(zeile("Hausrat", "Allianz")).getByText("seit 3 Tagen vorbei"),
    ).toBeInTheDocument();
  });

  it("sagt bei einer nicht berechenbaren Frist, dass sie es ist, statt das Feld leer zu lassen", () => {
    renderListe([
      police({
        main_due_on: null,
        main_due_source: "not_computable",
        deadline: frist({
          certainty: "not_computable",
          term_ends_on: null,
          deadline_legal_on: null,
          safe_service_on: null,
          recommended_action_on: null,
          days_left: null,
          reason: "Hauptfälligkeit fehlt – ohne sie gibt es keinen Kündigungstermin.",
        }),
      }),
      police({
        id: "p-ohne-grund",
        line_key: "rechtsschutz",
        line_label: "Rechtsschutz",
        line_purpose_short: "Traegt Anwalts- und Gerichtskosten im Streitfall.",
        insurer_name: "ARAG",
        deadline: frist({
          certainty: "not_computable",
          term_ends_on: null,
          deadline_legal_on: null,
          days_left: null,
          reason: "",
        }),
      }),
    ]);

    const mitGrund = zeile("Hausrat", "Allianz");
    expect(within(mitGrund).getByText("nicht berechenbar")).toBeInTheDocument();
    expect(
      within(mitGrund).getByText(
        "Hauptfälligkeit fehlt – ohne sie gibt es keinen Kündigungstermin.",
      ),
    ).toBeInTheDocument();
    expect(within(mitGrund).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();

    // Auch ohne Begruendung des Rechners bleibt die Zelle eine Aussage.
    const ohneGrund = zeile("Rechtsschutz", "ARAG");
    expect(within(ohneGrund).getByText("nicht berechenbar")).toBeInTheDocument();
    expect(within(ohneGrund).getByText("Angabe fehlt")).toBeInTheDocument();
  });

  it("setzt keinen Countdown unter ein Datum, das nicht das Fristende ist", () => {
    // Der Countdown zaehlt auf das Handlungsdatum, das aus dem rechtlichen
    // Fristende folgt. Fiele die Anzeige beim fehlenden Fristende auf das
    // Vertragsende zurueck, stuende die Tageszahl unter einem Datum, zu dem
    // sie nicht gehoert - beide koennen Monate auseinanderliegen.
    renderListe([
      police({
        deadline: frist({
          certainty: "confirmed",
          deadline_legal_on: null,
          term_ends_on: "2026-12-31",
          days_left: 47,
        }),
      }),
    ]);

    const zelle = zeile("Hausrat", "Allianz");
    expect(within(zelle).getByText("nicht berechenbar")).toBeInTheDocument();
    expect(within(zelle).queryByText("31.12.2026")).not.toBeInTheDocument();
    expect(within(zelle).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
  });

  it("stellt zu jeder Police den Zweck ihrer Sparte", () => {
    renderListe([
      police(),
      police({
        id: "p-bu",
        line_key: "berufsunfaehigkeit",
        line_label: "Berufsunfähigkeit",
        line_purpose_short:
          "Zahlt eine Rente, wenn der eigene Beruf nicht mehr ausgeübt werden kann.",
        insurer_name: "Alte Leipziger",
      }),
    ]);

    expect(
      within(zeile("Hausrat", "Allianz")).getByText(
        "Ersetzt die Einrichtung nach Feuer, Leitungswasser oder Einbruch.",
      ),
    ).toBeInTheDocument();
    expect(
      within(zeile("Berufsunfähigkeit", "Alte Leipziger")).getByText(
        "Zahlt eine Rente, wenn der eigene Beruf nicht mehr ausgeübt werden kann.",
      ),
    ).toBeInTheDocument();
  });

  it("zeigt zu jedem Beitrag, woher er kommt – und dass ein fremder nicht in die Fixkosten zaehlt", () => {
    renderListe([
      police(),
      police({
        id: "p-kranken",
        line_key: "krankenversicherung",
        line_label: "Krankenversicherung",
        line_purpose_short: "Traegt Behandlungskosten nach dem gewaehlten Tarif.",
        insurer_name: "Barmenia",
        premium: beitrag({
          kind: "external",
          contract_id: null,
          contract_name: null,
          contract_amount_minor: null,
          contract_frequency: null,
          external_premium_minor: 8900,
          external_frequency: "monthly",
          premium_payer: "employer",
          yearly_in_fixed_costs_minor: null,
        }),
      }),
      police({
        id: "p-ohne-beitrag",
        line_key: "privathaftpflicht",
        line_label: "Privathaftpflicht",
        line_purpose_short: "Zahlt Schaeden, die man anderen zufuegt.",
        insurer_name: "HDI",
        premium: beitrag({
          kind: "none",
          contract_id: null,
          contract_name: null,
          contract_amount_minor: null,
          contract_frequency: null,
          premium_share_bp: 0,
          yearly_in_fixed_costs_minor: null,
        }),
      }),
    ]);

    // (a) aus dem Vertrag - der Betrag lebt dort und zaehlt dort in die Fixkosten.
    const ausVertrag = zeile("Hausrat", "Allianz");
    expect(within(ausVertrag).getByText("12,40 €")).toBeInTheDocument();
    expect(
      within(ausVertrag).getByText(/monatlich · aus Vertrag „Hausrat Allianz“/),
    ).toBeInTheDocument();

    // (b) ein Dritter zahlt - der Betrag ist eine Information und ausdruecklich
    // kein Bestandteil der Fixkosten.
    const fremd = zeile("Krankenversicherung", "Barmenia");
    expect(within(fremd).getByText("89,00 €")).toBeInTheDocument();
    const herkunft = within(fremd).getByText(/zahlt der Arbeitgeber/);
    expect(herkunft).toHaveTextContent("nicht in den Fixkosten");
    expect(within(fremd).queryByText(/aus Vertrag/)).not.toBeInTheDocument();

    // (c) gar kein Beitrag - auch das wird gesagt, nicht verschwiegen.
    const ohne = zeile("Privathaftpflicht", "HDI");
    expect(within(ohne).getByText("kein Beitrag hinterlegt")).toBeInTheDocument();
  });

  it("nennt die naechste Aufgabe der Police, sonst ausdruecklich nichts Offenes", () => {
    renderListe([
      police({ next_task: "Hauptfälligkeit bestätigen" }),
      police({
        id: "p-fertig",
        line_key: "rechtsschutz",
        line_label: "Rechtsschutz",
        insurer_name: "ARAG",
        next_task: null,
      }),
    ]);

    expect(
      within(zeile("Hausrat", "Allianz")).getByText("Hauptfälligkeit bestätigen"),
    ).toBeInTheDocument();
    expect(within(zeile("Rechtsschutz", "ARAG")).getByText("nichts offen")).toBeInTheDocument();
  });

  it("weist einen Status abseits von aktiv an der Sparte aus", () => {
    renderListe([police({ status: "cancelled", cancelled_on: "2026-07-01" })]);
    expect(within(zeile("Hausrat", "Allianz")).getByText("gekündigt")).toBeInTheDocument();
  });

  it("sagt ohne Bestand, was hier stehen wird, statt nur leer zu sein", () => {
    renderListe([]);
    expect(screen.getByText("Noch keine Police erfasst")).toBeInTheDocument();
    expect(screen.getByText(/Sobald ein Versicherungsvertrag übernommen ist/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("blendet mit Suche und Statusfilter nichts stillschweigend aus", async () => {
    renderListe([
      police(),
      police({
        id: "p-kfz",
        line_key: "kfz_haftpflicht",
        line_label: "Kfz-Haftpflicht",
        insurer_name: "HUK",
        status: "cancelled",
      }),
    ]);
    // Voreinstellung ist "alle": eine gerade uebernommene, gekuendigte oder
    // beendete Police darf beim ersten Besuch nicht unsichtbar sein.
    expect(screen.getByText("2 von 2")).toBeInTheDocument();

    await userEvent.type(screen.getByRole("searchbox"), "HUK");
    expect(screen.getByText("1 von 2")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Hausrat bei Allianz öffnen" }),
    ).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Status"), "active");
    expect(screen.getByText("Kein Treffer")).toBeInTheDocument();
    expect(screen.getByText(/2 Policen sind erfasst/)).toBeInTheDocument();
  });

  it("oeffnet die angeklickte Police", async () => {
    const onSelectPolicy = renderListe([police(), police({ id: "p-zwei", insurer_name: "HUK" })]);
    await userEvent.click(screen.getByRole("button", { name: "Hausrat bei HUK öffnen" }));
    expect(onSelectPolicy).toHaveBeenCalledWith("p-zwei");
  });
});
