import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { InsurancePage, type InsurancePageProps } from "../pages/InsurancePage";
import type {
  BackfillPreview,
  InsurancePolicy,
  InsuranceSummary,
  InsuranceTask,
  PolicyDeadline,
  PolicyPremium,
} from "../lib/insuranceApi";

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
    claims_phone: "0800 1234567",
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

function aufgabe(overrides: Partial<InsuranceTask> = {}): InsuranceTask {
  return {
    kind: "deadline_approaching",
    policy_id: "p-hausrat",
    policy_label: "Hausrat · Allianz",
    what: "Kündigungsfrist läuft ab",
    why: "Nach dem Fristende verlängert sich der Vertrag um ein weiteres Jahr.",
    due_on: "2026-09-30",
    days_left: 12,
    ...overrides,
  };
}

function zusammenfassung(overrides: Partial<InsuranceSummary> = {}): InsuranceSummary {
  return {
    policy_count: 1,
    active_count: 1,
    yearly_in_fixed_costs_minor: 14880,
    yearly_paid_by_others_minor: 0,
    paid_by_others_breakdown: {},
    no_premium_count: 0,
    unknown_line_count: 0,
    deferred_line_count: 0,
    missing_main_due_count: 0,
    unconfirmed_main_due_count: 0,
    next_deadline_on: "2026-09-30",
    next_deadline_policy_id: "p-hausrat",
    ...overrides,
  };
}

function vorschau(overrides: Partial<BackfillPreview> = {}): BackfillPreview {
  return {
    items: [
      {
        contract_id: "contract-2",
        contract_name: "Kfz HUK",
        provider_label: "HUK-COBURG",
        amount_minor: 4100,
        frequency: "monthly",
        suggested_line_key: "kfz_haftpflicht",
        suggested_line_label: "Kfz-Haftpflicht",
        reason: "Kategorie „Versicherung“ und der Zahlungsempfänger deuten auf Kfz.",
      },
      {
        contract_id: "contract-3",
        contract_name: "Sammelposten Versicherung",
        provider_label: null,
        amount_minor: 2500,
        frequency: "monthly",
        suggested_line_key: "unknown",
        suggested_line_label: "Sparte unbekannt",
        reason: "Nur die Kategorie passt – der Name lässt keine Sparte erkennen.",
      },
    ],
    already_linked_count: 3,
    ...overrides,
  };
}

function renderSeite(overrides: Partial<InsurancePageProps> = {}) {
  const props: InsurancePageProps = {
    policies: [],
    tasks: [],
    summary: null,
    backfillPreview: null,
    lines: [],
    loading: false,
    error: null,
    onReload: vi.fn(),
    canEdit: true,
    selectedPolicyId: null,
    onSelectPolicy: vi.fn(),
    purpose: null,
    purposeLoading: false,
    purposeError: null,
    actionBusy: false,
    actionError: null,
    backfillBusy: false,
    backfillError: null,
    onRunBackfill: vi.fn(),
    onConfirmMainDue: vi.fn(),
    onConfirmNoticeTerms: vi.fn(),
    onConfirmLine: vi.fn(),
    ...overrides,
  };
  render(
    <MemoryRouter>
      <InsurancePage {...props} />
    </MemoryRouter>,
  );
  return props;
}

describe("InsurancePage – Jetzt wichtig", () => {
  it("nennt je Aufgabe Sachverhalt, Police und Grund", () => {
    renderSeite({
      policies: [police()],
      tasks: [
        aufgabe(),
        aufgabe({
          kind: "unknown_line",
          policy_id: "p-offen",
          policy_label: "Sammelposten · unbekannt",
          what: "Sparte noch offen",
          why: "Ohne Sparte gibt es keinen Zwecktext und keine Fristvorbelegung.",
          due_on: null,
          days_left: null,
        }),
      ],
    });

    expect(screen.getByRole("heading", { name: "Jetzt wichtig" })).toBeInTheDocument();
    expect(screen.getByText("2 Sachen gehen ohne dich nicht weiter")).toBeInTheDocument();
    expect(screen.getByText("Kündigungsfrist läuft ab")).toBeInTheDocument();
    expect(screen.getByText("Hausrat · Allianz")).toBeInTheDocument();
    expect(
      screen.getByText("Nach dem Fristende verlängert sich der Vertrag um ein weiteres Jahr."),
    ).toBeInTheDocument();
    expect(screen.getByText("Sparte noch offen")).toBeInTheDocument();
    expect(
      screen.getByText("Ohne Sparte gibt es keinen Zwecktext und keine Fristvorbelegung."),
    ).toBeInTheDocument();
  });

  it("gibt nur der bestaetigten Frist einen Countdown, der angenommenen das Wort", () => {
    renderSeite({
      policies: [police()],
      tasks: [
        aufgabe({ due_on: "2026-09-30", days_left: 12 }),
        aufgabe({
          kind: "unconfirmed_main_due",
          policy_id: "p-angenommen",
          policy_label: "Kfz-Haftpflicht · HUK",
          what: "Hauptfälligkeit bestätigen",
          why: "Solange das Datum angenommen ist, gibt es keine Erinnerung.",
          due_on: "2026-11-01",
          // Der Server liefert hier bewusst keine Tageszahl - und selbst wenn,
          // duerfte sie nicht als Countdown erscheinen.
          days_left: null,
        }),
      ],
    });

    const eintraege = within(screen.getByRole("list")).getAllByRole("listitem");
    const dringend = eintraege[0] as HTMLElement;
    const unbestaetigt = eintraege[1] as HTMLElement;
    expect(within(dringend).getByText("noch 12 Tage")).toBeInTheDocument();
    expect(within(unbestaetigt).getByText("angenommen")).toBeInTheDocument();
    expect(within(unbestaetigt).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
  });

  it("bestaetigt das vorgeschlagene Datum mit einem Klick, ohne Formular", async () => {
    const props = renderSeite({
      policies: [police()],
      tasks: [
        aufgabe({
          kind: "unconfirmed_main_due",
          policy_id: "p-angenommen",
          what: "Hauptfälligkeit bestätigen",
          due_on: "2026-11-01",
          days_left: null,
        }),
      ],
    });

    await userEvent.click(screen.getByRole("button", { name: "Datum stimmt" }));
    expect(props.onConfirmMainDue).toHaveBeenCalledWith("p-angenommen");
  });

  it("bietet 'Datum stimmt' nur bei einer angenommenen Hauptfaelligkeit an", () => {
    renderSeite({ policies: [police()], tasks: [aufgabe()] });
    expect(screen.queryByRole("button", { name: "Datum stimmt" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Frist ansehen" })).toBeInTheDocument();
  });

  it("laesst ohne Schreibrecht keine Schaltflaeche stehen, die nichts tut", () => {
    renderSeite({
      canEdit: false,
      policies: [police()],
      backfillPreview: vorschau(),
      tasks: [aufgabe({ kind: "unconfirmed_main_due", days_left: null })],
    });
    expect(screen.queryByRole("button", { name: "Datum stimmt" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /übernehmen/ })).not.toBeInTheDocument();
    // Die Aufgabe selbst bleibt sichtbar - lesen darf man sie.
    expect(screen.getByRole("heading", { name: "Jetzt wichtig" })).toBeInTheDocument();
  });

  it("verschwindet ohne Aufgaben ganz und ueberlaesst der Liste die Aussage", () => {
    // Plan 2.2 legt fest: leer heisst, der Block verschwindet - ein
    // "alles in Ordnung"-Kasten wuerde eine Prueftiefe behaupten, die es nicht
    // gibt. Die Aussage "hier ist nichts offen" faellt deshalb je Police in der
    // Liste, nicht als Sammelentwarnung im Kopf der Seite.
    renderSeite({ policies: [police({ next_task: null })], tasks: [] });

    expect(screen.queryByRole("heading", { name: "Jetzt wichtig" })).not.toBeInTheDocument();
    expect(screen.queryByText(/alles in Ordnung/i)).not.toBeInTheDocument();
    expect(screen.getByText("nichts offen")).toBeInTheDocument();
  });

  it("sagt ohne jede Police, was hier entstehen wird", () => {
    renderSeite({ policies: [], tasks: [] });
    expect(screen.getByText("Noch keine Police erfasst")).toBeInTheDocument();
    expect(screen.getByText(/Sobald ein Versicherungsvertrag übernommen ist/)).toBeInTheDocument();
  });

  it("laesst auch ohne Schreibrecht den Weg zur Police offen", async () => {
    const props = renderSeite({
      canEdit: false,
      policies: [police()],
      tasks: [
        aufgabe({
          kind: "missing_main_due",
          what: "Hauptfälligkeit erfassen",
          due_on: null,
          days_left: null,
        }),
      ],
    });

    // Nur die Bestaetigung haengt an der Rolle. Haengt der rein lesende Weg
    // zur Police mit dran, sieht ein Viewer die Aufgabe, kann ihr aber nicht
    // nachgehen und muss die Police von Hand in der Liste wiederfinden.
    expect(
      screen.queryByRole("button", { name: "Hauptfälligkeit erfassen" }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Police öffnen" }));
    expect(props.onSelectPolicy).toHaveBeenCalledWith("p-hausrat");
  });

  it("behauptet eine Annahme nur dort, wo sie belegt ist", () => {
    renderSeite({
      policies: [police()],
      tasks: [
        aufgabe({
          kind: "missing_premium_source",
          policy_id: "p-beitrag",
          what: "Beitrag klären",
          why: "Ohne Beitragsquelle fehlt die Police in der Jahressumme.",
          due_on: "2026-11-01",
          days_left: null,
        }),
      ],
    });

    const eintrag = within(screen.getByRole("list")).getAllByRole("listitem")[0] as HTMLElement;
    expect(within(eintrag).getByText("01.11.2026")).toBeInTheDocument();
    // Ueber die Sicherheit dieses Datums ist nichts bekannt: weder ein
    // Countdown noch das Wort "angenommen" waeren hier belegt. Frueher bekam
    // jede Aufgabe ausser "Frist laeuft ab" pauschal "angenommen".
    expect(within(eintrag).queryByText("angenommen")).not.toBeInTheDocument();
    expect(within(eintrag).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
  });
});

describe("InsurancePage – Bestand übernehmen", () => {
  it("zeigt erst die Vorschau und legt erst auf Klick etwas an", async () => {
    const props = renderSeite({
      policies: [police()],
      summary: zusammenfassung({ unknown_line_count: 1 }),
      backfillPreview: vorschau(),
    });

    const block = screen.getByRole("region", { name: "Vorschau" });
    expect(within(block).getByText("Kfz HUK")).toBeInTheDocument();
    expect(within(block).getByText("HUK-COBURG")).toBeInTheDocument();
    expect(within(block).getByText("Kfz-Haftpflicht")).toBeInTheDocument();
    expect(
      within(block).getByText("Kategorie „Versicherung“ und der Zahlungsempfänger deuten auf Kfz."),
    ).toBeInTheDocument();
    // Wo der Vorschlag nicht traegt, steht das da - kein geratener Eintrag.
    expect(
      within(block).getByText("Sparte noch offen – wird nach der Übernahme zugeordnet"),
    ).toBeInTheDocument();
    // Der Beitrag wandert nicht mit; die Uebernahme bewegt kein Geld.
    expect(within(block).getAllByText(/bleibt am Vertrag/).length).toBe(2);

    // Bis hierher ist nichts angelegt worden.
    expect(props.onRunBackfill).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "2 Verträge übernehmen" }));
    expect(props.onRunBackfill).toHaveBeenCalledTimes(1);
  });

  it("misst den Fortschritt an einsatzbereiten Policen, nicht an angelegten", () => {
    renderSeite({
      // Einsatzbereit ist nur die erste: bei der zweiten ist die Sparte offen.
      policies: [
        police(),
        police({ id: "p-offen", line_key: "unknown", line_label: "Sparte unbekannt" }),
      ],
      summary: zusammenfassung({ policy_count: 2, unknown_line_count: 1 }),
      backfillPreview: vorschau(),
    });

    // 2 Policen + 2 Verträge in der Vorschau = 4, davon 1 einsatzbereit.
    expect(screen.getByRole("img", { name: "1 von 4 Policen einsatzbereit" })).toBeInTheDocument();
    expect(screen.getByText("2 Verträge noch ohne Police")).toBeInTheDocument();
    expect(screen.getByText("3 Verträge haben bereits eine Police")).toBeInTheDocument();
    expect(screen.getByText("1 ohne Sparte")).toBeInTheDocument();
  });

  it("zaehlt einen bereits verknuepften Vertrag im Singular", () => {
    renderSeite({
      policies: [police()],
      summary: zusammenfassung(),
      backfillPreview: vorschau({ already_linked_count: 1 }),
    });
    expect(screen.getByText("1 Vertrag hat bereits eine Police")).toBeInTheDocument();
  });

  it("verschwindet, sobald nichts mehr zu uebernehmen ist", () => {
    renderSeite({
      policies: [police()],
      summary: zusammenfassung(),
      backfillPreview: { items: [], already_linked_count: 3 },
    });
    expect(screen.queryByRole("heading", { name: "Bestand übernehmen" })).not.toBeInTheDocument();
  });
});

describe("InsurancePage – Kennzahlenleiste", () => {
  it("traegt fremd gezahlte Beitraege ausdruecklich ausserhalb der Fixkosten aus", () => {
    renderSeite({
      policies: [police()],
      summary: zusammenfassung({ yearly_paid_by_others_minor: 106800 }),
    });
    expect(
      screen.getByText(/1\.068,00 €\/Jahr zahlen Dritte \(nicht in den Fixkosten\)/),
    ).toBeInTheDocument();
  });
});

describe("InsurancePage – Policendetail", () => {
  const geoeffnet = { selectedPolicyId: "p-hausrat", policies: [police()] };

  it("stellt den Ernstfallblock an den Anfang", () => {
    renderSeite(geoeffnet);
    const dialog = screen.getByRole("dialog");
    const ueberschriften = within(dialog).getAllByRole("heading");

    // [0] ist der Titel des Dialogs selbst - der erste Abschnitt danach ist
    // der Ernstfall, nicht die Erklaerung der Sparte (Plan 2.3).
    expect(ueberschriften[0]).toHaveTextContent("Hausrat · Allianz");
    expect(ueberschriften[1]).toHaveTextContent("Im Ernstfall");
    expect(ueberschriften[2]).toHaveTextContent("Wofür ist diese Art von Versicherung da?");
  });

  it("nennt im Ernstfall Versicherer, Policennummer und Schadenhotline", () => {
    renderSeite(geoeffnet);
    const ernstfall = screen
      .getByRole("heading", { name: "Im Ernstfall" })
      .closest("section") as HTMLElement;

    expect(within(ernstfall).getByText("Versicherer")).toBeInTheDocument();
    expect(within(ernstfall).getByText("Allianz")).toBeInTheDocument();
    expect(within(ernstfall).getByText("Policennummer")).toBeInTheDocument();
    expect(within(ernstfall).getByText("HR-4711")).toBeInTheDocument();
    expect(within(ernstfall).getByText("Schaden melden")).toBeInTheDocument();
    // Auf dem Telefon ist die Nummer waehlbar, nicht nur lesbar.
    expect(within(ernstfall).getByRole("link", { name: "0800 1234567" })).toHaveAttribute(
      "href",
      "tel:08001234567",
    );
    expect(within(ernstfall).getByText("aktiv")).toBeInTheDocument();
  });

  it("verschweigt eine fehlende Hotline nicht", () => {
    renderSeite({
      selectedPolicyId: "p-hausrat",
      policies: [police({ claims_phone: null, policy_number: null })],
    });
    const ernstfall = screen
      .getByRole("heading", { name: "Im Ernstfall" })
      .closest("section") as HTMLElement;
    expect(within(ernstfall).getByText("keine Hotline hinterlegt")).toBeInTheDocument();
    expect(within(ernstfall).getByText("nicht erfasst")).toBeInTheDocument();
  });

  it("zeigt den naechsten Termin im Ernstfallblock mit derselben Herkunft wie in der Liste", () => {
    renderSeite({
      selectedPolicyId: "p-hausrat",
      policies: [
        police({
          main_due_source: "catalog_suggested",
          notice_terms_source: "catalog_suggested",
          deadline: frist({ certainty: "assumed", days_left: 12 }),
        }),
      ],
    });
    const ernstfall = screen
      .getByRole("heading", { name: "Im Ernstfall" })
      .closest("section") as HTMLElement;

    expect(within(ernstfall).getByText(/^angenommen \(Katalog\)/)).toBeInTheDocument();
    expect(within(ernstfall).queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
    expect(
      within(ernstfall).getByText("kein Countdown, solange das Datum angenommen ist"),
    ).toBeInTheDocument();
  });

  it("belegt die Frist mit ihrer Herleitung statt mit einem nackten Datum", () => {
    renderSeite({
      selectedPolicyId: "p-hausrat",
      policies: [
        police({
          main_due_source: "catalog_suggested",
          notice_terms_source: "derived_from_days",
          deadline: frist({ certainty: "assumed" }),
        }),
      ],
    });
    const dialog = screen.getByRole("dialog");

    expect(within(dialog).getByText("Hauptfälligkeit")).toBeInTheDocument();
    expect(within(dialog).getAllByText("angenommen (Katalog)").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("aus Kündigungstagen umgerechnet")).toBeInTheDocument();
    expect(within(dialog).getByText("zum Ablauf des Versicherungsjahres")).toBeInTheDocument();
    expect(within(dialog).getByText("Grundlage: Gesetz")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Drei Monate zum Ablauf des Versicherungsjahres."),
    ).toBeInTheDocument();
  });

  it("fuehrt den Beitrag zum Vertrag, statt ihn an der Police zu behaupten", () => {
    renderSeite(geoeffnet);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Der Beitrag lebt im Vertrag/)).toHaveTextContent(
      "„Hausrat Allianz“",
    );
    expect(within(dialog).getByText("zählt in die Fixkosten")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /Zum Vertrag/ })).toHaveAttribute(
      "href",
      "/fixkosten?contract_id=contract-1",
    );
  });

  it("haelt einen fremd gezahlten Beitrag aus den Fixkosten heraus", () => {
    renderSeite({
      selectedPolicyId: "p-hausrat",
      policies: [
        police({
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
      ],
    });
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("zahlt der Arbeitgeber")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/zählt ausdrücklich nicht in die Fixkosten des Haushalts/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("zählt in die Fixkosten")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: /Zum Vertrag/ })).not.toBeInTheDocument();
  });

  it("sagt beim dritten Geldfall, wohin ein selbst gezahlter Beitrag gehoert", () => {
    renderSeite({
      selectedPolicyId: "p-hausrat",
      policies: [
        police({
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
      ],
    });
    expect(
      within(screen.getByRole("dialog")).getByText(/Kein Beitrag hinterlegt/),
    ).toHaveTextContent("Ein selbst gezahlter Beitrag gehört an einen Vertrag");
  });

  it("oeffnet die Police aus der Liste heraus", async () => {
    const props = renderSeite({ policies: [police()] });
    await userEvent.click(screen.getByRole("button", { name: "Hausrat bei Allianz öffnen" }));
    expect(props.onSelectPolicy).toHaveBeenCalledWith("p-hausrat");
  });

  it("meldet einen Fehler der Aktion genau einmal, nicht zweimal", () => {
    renderSeite({
      ...geoeffnet,
      actionError: "Die Bestätigung ist fehlgeschlagen.",
    });

    // Der Fehler gehoert an die Aktion, die ihn ausgeloest hat - also ins
    // Detail. Stuende er zusaetzlich auf der Seite, laese ein Screenreader
    // zwei gleichlautende Meldungen vor.
    expect(screen.getAllByText("Die Bestätigung ist fehlgeschlagen.")).toHaveLength(1);
    expect(
      within(screen.getByRole("dialog")).getByText("Die Bestätigung ist fehlgeschlagen."),
    ).toBeInTheDocument();
  });

  it("meldet den Fehler auf der Seite, solange kein Detail offen ist", () => {
    // Die Gegenprobe: aus "Jetzt wichtig" heraus laesst sich bestaetigen, ohne
    // dass ein Detail offen ist. Verschwaende die Seitenmeldung ganz, bliebe
    // dieser Fehlschlag unsichtbar.
    renderSeite({
      policies: [police()],
      tasks: [aufgabe({ kind: "unconfirmed_main_due", days_left: null })],
      actionError: "Die Bestätigung ist fehlgeschlagen.",
    });
    expect(screen.getAllByText("Die Bestätigung ist fehlgeschlagen.")).toHaveLength(1);
  });
});
