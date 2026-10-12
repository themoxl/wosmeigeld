import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PremiumNoticeModal } from "../components/insurance/PremiumNoticeModal";
import type { InsurancePolicy, PolicyDeadline, PolicyPremium } from "../lib/insuranceApi";

// Der Dialog haelt zwei Datumsfelder auseinander, die im Brief nebeneinander
// stehen und Verschiedenes bedeuten. Genau das pruefen diese Tests: nicht die
// Rechnung - die macht der Server -, sondern ob der Nutzer erkennt, WELCHES
// der beiden Daten die Frist ausloest.

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

function renderModal(overrides: Partial<InsurancePolicy> = {}, props: { busy?: boolean } = {}) {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(
    <PremiumNoticeModal
      policy={police(overrides)}
      busy={props.busy ?? false}
      error={null}
      onSubmit={onSubmit}
      onClose={onClose}
    />,
  );
  return { onSubmit, onClose };
}

const ZUGANG = /Schreiben erhalten am/;
const WIRKUNG = /Neuer Beitrag gilt ab/;

/** Das Feld samt Marke und Hilfetext - der Unterschied der beiden Daten steht
 *  nicht im Label allein, sondern im Kasten drumherum. */
function feld(label: RegExp): HTMLElement {
  return screen.getByLabelText(label).closest(".insurance-form-field") as HTMLElement;
}

function setzeDatum(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

/** Fuellt das Formular so aus, dass es abgeschickt werden kann. Der bisherige
 *  Beitrag und die Zahlweise sind aus der Police vorbelegt. */
async function fuelleAus(neuerBetrag = "13,20") {
  setzeDatum(ZUGANG, "2026-08-30");
  setzeDatum(WIRKUNG, "2026-11-01");
  await userEvent.type(screen.getByLabelText("Neuer Beitrag"), neuerBetrag);
}

describe("PremiumNoticeModal – die beiden Daten", () => {
  it("markiert nur den Zugang als Fristanker und das Wirkdatum ausdruecklich nicht", () => {
    renderModal();

    const zugang = feld(ZUGANG);
    const wirkung = feld(WIRKUNG);

    expect(within(zugang).getByText("Fristanker")).toBeInTheDocument();
    expect(within(wirkung).getByText("kein Fristanker")).toBeInTheDocument();
    // Die Marke des einen Feldes darf nicht im anderen stehen - sonst haetten
    // beide dieselbe Aussage.
    expect(within(wirkung).queryByText("Fristanker")).toBeNull();
    expect(within(zugang).queryByText("kein Fristanker")).toBeNull();

    // Zwei verschiedene Eingaben, nicht zweimal dieselbe.
    expect(screen.getByLabelText(ZUGANG)).toHaveAttribute("id", "premium-notice-received");
    expect(screen.getByLabelText(WIRKUNG)).toHaveAttribute("id", "premium-notice-effective");
    expect(screen.getByLabelText(ZUGANG)).toHaveAttribute("type", "date");
    expect(screen.getByLabelText(WIRKUNG)).toHaveAttribute("type", "date");
  });

  it("nennt im Hilfetext, welches der beiden Daten die Frist ausloest", () => {
    renderModal();

    const zugang = feld(ZUGANG);
    expect(zugang).toHaveTextContent("§ 40 Abs. 1 S. 1 VVG lässt die Frist ab Zugang laufen");
    expect(zugang).toHaveTextContent("Der Tag, an dem der Brief bei Ihnen ankam");
    // Der haeufigste Fehler beim Ausfuellen steht ausdruecklich dabei.
    expect(zugang).toHaveTextContent("nicht das Datum auf dem Briefkopf");
    expect(zugang).toHaveTextContent("ein Monat, in der privaten Kranken- und Pflegeversicherung");

    const wirkung = feld(WIRKUNG);
    expect(wirkung).toHaveTextContent("Er verlängert die Frist nicht.");
    expect(wirkung).toHaveTextContent(
      "Er ist aber das früheste Datum, zu dem eine Kündigung wirkt",
    );
    // Die Fristaussage darf nur an EINEM der beiden Felder stehen.
    expect(wirkung).not.toHaveTextContent("lässt die Frist ab Zugang laufen");
  });

  it("stellt die beiden Daten nebeneinander, nicht untereinander", () => {
    // Untereinander liest niemand den zweiten Hilfetext - dann waeren zwei
    // Datumsfelder mit gleich aussehenden Beschriftungen uebrig.
    const { container } = render(
      <PremiumNoticeModal
        policy={police()}
        busy={false}
        error={null}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const paar = container.querySelector(".insurance-date-pair") as HTMLElement;
    expect(within(paar).getByLabelText(ZUGANG)).toBeInTheDocument();
    expect(within(paar).getByLabelText(WIRKUNG)).toBeInTheDocument();
  });

  it("weist vertauschte Daten zurueck, statt eine Frist daraus zu rechnen", async () => {
    const { onSubmit } = renderModal();

    setzeDatum(ZUGANG, "2026-11-01");
    setzeDatum(WIRKUNG, "2026-08-30");
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "13,20");
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(
      screen.getByText(
        "Der neue Beitrag kann nicht vor dem Schreiben gelten. Prüfen Sie, ob die beiden Daten vertauscht sind.",
      ),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rechnet die Frist nicht selbst, sondern sagt, wer sie rechnet", () => {
    renderModal();
    expect(
      screen.getByText(/Fristende, letzter sicherer Zustelltag und empfohlenes Handlungsdatum/),
    ).toHaveTextContent("samt Feiertagen des Bundeslands, in das die Kündigung geht.");
    // Kein gerechnetes Datum im Dialog - weder Fristende noch Countdown.
    expect(screen.queryByText(/noch \d+ Tage/)).toBeNull();
    expect(screen.queryByText(/Rechtliches Fristende/)).toBeNull();
  });
});

describe("PremiumNoticeModal – Betraege", () => {
  it("belegt den bisherigen Beitrag und die Zahlweise aus der Police vor", () => {
    renderModal();
    expect(screen.getByLabelText("Bisheriger Beitrag")).toHaveValue("10,00");
    expect(screen.getByLabelText("Zahlweise beider Beträge")).toHaveValue("monthly");
  });

  it("belegt nichts vor, wo die Police nur einen Teil des Vertrags traegt", () => {
    // Sonst bestaetigte der Nutzer ungeprueft eine Zahl, die anschliessend als
    // Preisstufe im Vertrag landet.
    renderModal({ premium: beitrag({ premium_share_bp: 5000 }) });
    expect(screen.getByLabelText("Bisheriger Beitrag")).toHaveValue("");
  });

  it("zeigt die Aenderung als Prozentwert, sobald beide Betraege dastehen", async () => {
    renderModal();
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "12,00");
    expect(screen.getByText(/^Änderung:/)).toHaveTextContent("+20,0 %");
    expect(screen.getByText(/^Änderung:/)).toHaveTextContent("10,00 € → 12,00 € · monatlich");
  });

  it("gibt die Betraege als ganzzahlige Minor Units weiter", async () => {
    const { onSubmit } = renderModal();
    await fuelleAus();
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({
      received_on: "2026-08-30",
      effective_on: "2026-11-01",
      old_amount_minor: 1000,
      new_amount_minor: 1320,
      comparison_amount_minor: null,
      frequency: "monthly",
      with_benefit_increase: false,
      reason: null,
    });
  });

  it("nimmt Anlass und Vergleichsbeitrag mit, wo sie im Schreiben stehen", async () => {
    const { onSubmit } = renderModal({ line_key: "motor_liability" });

    setzeDatum(ZUGANG, "2026-08-30");
    setzeDatum(WIRKUNG, "2026-11-01");
    await userEvent.clear(screen.getByLabelText("Bisheriger Beitrag"));
    await userEvent.type(screen.getByLabelText("Bisheriger Beitrag"), "480,00");
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "512,00");
    await userEvent.type(screen.getByLabelText("Vergleichsbeitrag"), "455,00");
    await userEvent.selectOptions(screen.getByLabelText("Anlass laut Schreiben"), "regional_class");
    await userEvent.selectOptions(screen.getByLabelText("Zahlweise beider Beträge"), "yearly");
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(onSubmit).toHaveBeenCalledWith({
      received_on: "2026-08-30",
      effective_on: "2026-11-01",
      old_amount_minor: 48000,
      new_amount_minor: 51200,
      comparison_amount_minor: 45500,
      frequency: "yearly",
      with_benefit_increase: false,
      reason: "regional_class",
    });
  });

  it("meldet einen unlesbaren Betrag, statt ihn zu verschlucken", async () => {
    const { onSubmit } = renderModal();

    setzeDatum(ZUGANG, "2026-08-30");
    setzeDatum(WIRKUNG, "2026-11-01");
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "dreizehn");
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(screen.getByText("Der neue Beitrag fehlt oder ist nicht lesbar.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("bietet keine Zahlweise an, die nicht angepasst werden kann", () => {
    renderModal();
    const zahlweise = screen.getByLabelText("Zahlweise beider Beträge");
    expect(within(zahlweise).queryByText("einmalig")).toBeNull();
    expect(within(zahlweise).queryByText("wöchentlich")).toBeNull();
    expect(within(zahlweise).getByText("vierteljährlich")).toBeInTheDocument();
  });
});

describe("PremiumNoticeModal – der Vergleichsbeitrag", () => {
  it("steht auf einer Kfz-Police offen", () => {
    renderModal({ line_key: "motor_liability" });
    expect(screen.getByLabelText("Vergleichsbeitrag")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vergleichsbeitrag ausblenden" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("bleibt sonst hinter einer Klappe, laesst sich aber oeffnen", async () => {
    renderModal();
    expect(screen.queryByLabelText("Vergleichsbeitrag")).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "Auf der Rechnung steht ein Vergleichsbeitrag" }),
    );
    expect(screen.getByLabelText("Vergleichsbeitrag")).toBeInTheDocument();
  });
});

describe("PremiumNoticeModal – die Vorschau sagt nur, was ein Fenster ausschliesst", () => {
  it("schweigt, solange die Angaben ein Fenster zulassen", async () => {
    renderModal();
    await fuelleAus();
    expect(screen.queryByText(/kein Sonderkündigungsrecht/)).toBeNull();
    expect(screen.queryByText(/§ 40 VVG greift nur/)).toBeNull();
  });

  it("nennt bei vereinbarter Dynamik den Grund und laesst das Erfassen trotzdem zu", async () => {
    // Leitsatz 3: eine planmaessige Dynamikstufe ist keine Erhoehung im Sinne
    // des § 40 VVG - erfasst wird sie trotzdem.
    const { onSubmit } = renderModal({ dynamic_agreed: true });

    expect(screen.getByText(/Diese Police hat eine vereinbarte Dynamik/)).toHaveTextContent(
      "erfassen können Sie das Schreiben trotzdem.",
    );

    await fuelleAus();
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("nennt die Leistungserweiterung als Ausschlussgrund und gibt sie weiter", async () => {
    const { onSubmit } = renderModal();
    await fuelleAus();
    await userEvent.click(screen.getByLabelText("Mit entsprechender Leistungserweiterung"));

    expect(
      screen.getByText(/Mit entsprechender Leistungserweiterung entsteht kein/),
    ).toHaveTextContent("§ 40 VVG greift nur bei einer Erhöhung ohne Mehrleistung.");

    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ with_benefit_increase: true }));
  });

  it("sagt bei einer Senkung, dass daraus kein Recht entsteht", async () => {
    renderModal();
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "9,00");
    expect(screen.getByText(/Der Beitrag steigt nicht/)).toHaveTextContent(
      "Eine Senkung oder ein gleichbleibender Beitrag löst kein Sonderkündigungsrecht aus.",
    );
  });

  it("misst auf einer Kfz-Police gegen den Vergleichsbeitrag, nicht gegen den Vorjahresbeitrag", async () => {
    // Der Kern des Kfz-Falls: die Rechnung sinkt gegenueber dem Vorjahr, das
    // Recht besteht trotzdem - und umgekehrt.
    renderModal({ line_key: "motor_liability" });

    await userEvent.clear(screen.getByLabelText("Bisheriger Beitrag"));
    await userEvent.type(screen.getByLabelText("Bisheriger Beitrag"), "600,00");
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "540,00");
    await userEvent.type(screen.getByLabelText("Vergleichsbeitrag"), "500,00");

    // Gegenueber dem Vorjahr eine Senkung - trotzdem kein Ausschluss, weil der
    // neue Beitrag ueber dem Vergleichsbeitrag liegt.
    expect(screen.queryByText(/Der Beitrag steigt nicht/)).toBeNull();
    expect(screen.queryByText(/liegt nicht über dem Vergleichsbeitrag/)).toBeNull();

    await userEvent.clear(screen.getByLabelText("Vergleichsbeitrag"));
    await userEvent.type(screen.getByLabelText("Vergleichsbeitrag"), "560,00");
    expect(
      screen.getByText(/Der neue Beitrag liegt nicht über dem Vergleichsbeitrag/),
    ).toHaveTextContent("Dann bleibt es bei der Hochstufung");
  });
});

describe("PremiumNoticeModal – nichts entscheidet unsichtbar", () => {
  it("vergisst einen zugeklappten Vergleichsbeitrag, statt ihn mitzusenden", async () => {
    // Der Server nimmt `comparison_amount_minor` als Massstab. Ein zugeklappt
    // stehengebliebener Betrag ueber dem neuen Beitrag loeschte das
    // Sonderkuendigungsfenster nach § 40 VVG - durch einen Wert, den der Nutzer
    // auf dem Bildschirm nicht mehr sieht.
    const { onSubmit } = renderModal();

    await userEvent.click(
      screen.getByRole("button", { name: "Auf der Rechnung steht ein Vergleichsbeitrag" }),
    );
    await userEvent.type(screen.getByLabelText("Vergleichsbeitrag"), "99,00");
    await userEvent.click(screen.getByRole("button", { name: "Vergleichsbeitrag ausblenden" }));
    expect(screen.queryByLabelText("Vergleichsbeitrag")).toBeNull();

    await fuelleAus();
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ comparison_amount_minor: null }),
    );

    // Wer die Klappe wieder oeffnet, findet ein leeres Feld - der zugeklappte
    // Zustand behauptet also dasselbe wie das Feld darunter.
    await userEvent.click(
      screen.getByRole("button", { name: "Auf der Rechnung steht ein Vergleichsbeitrag" }),
    );
    expect(screen.getByLabelText("Vergleichsbeitrag")).toHaveValue("");
  });

  it("blockiert das Absenden nicht mit einer Meldung zu einem verborgenen Feld", async () => {
    // Die haertere Fassung desselben Fehlers: ein unlesbarer Wert hinter der
    // zugeklappten Klappe war eine Sackgasse - "Der Vergleichsbeitrag ist nicht
    // lesbar" zu einem Feld, das nicht auf dem Bildschirm steht.
    const { onSubmit } = renderModal();

    await userEvent.click(
      screen.getByRole("button", { name: "Auf der Rechnung steht ein Vergleichsbeitrag" }),
    );
    await userEvent.type(screen.getByLabelText("Vergleichsbeitrag"), "abc");
    await userEvent.click(screen.getByRole("button", { name: "Vergleichsbeitrag ausblenden" }));

    await fuelleAus();
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(screen.queryByText(/Der Vergleichsbeitrag ist nicht lesbar/)).toBeNull();
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("nennt beim fehlenden Zugang die eigene Begruendung statt der Browserblase", async () => {
    // Mit `required` verhinderte die native Pflichtpruefung das
    // Submit-Ereignis - `handleSubmit` lief nie, und die beiden Saetze, die
    // erklaeren WARUM gerade diese Daten Pflicht sind, waren unerreichbar.
    const { onSubmit } = renderModal();

    setzeDatum(WIRKUNG, "2026-11-01");
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "13,20");
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Ohne den Tag des Zugangs entsteht keine Frist, sondern eine Rückfrage.",
    );
  });

  it("nennt beim fehlenden Wirkdatum, was ohne es fehlt", async () => {
    const { onSubmit } = renderModal();

    setzeDatum(ZUGANG, "2026-08-30");
    await userEvent.type(screen.getByLabelText("Neuer Beitrag"), "13,20");
    await userEvent.click(screen.getByRole("button", { name: "Schreiben erfassen" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Ohne das Wirksamwerden fehlt das früheste Beendigungsdatum.",
    );
  });

  it("sagt Vorlesewerkzeugen weiterhin, dass beide Daten Pflicht sind", () => {
    // `required` faellt weg, die Ansage nicht: sonst tauschte der Dialog eine
    // unerreichbare Begruendung gegen ein unangesagtes Pflichtfeld.
    renderModal();
    expect(screen.getByLabelText(ZUGANG)).toHaveAttribute("aria-required", "true");
    expect(screen.getByLabelText(WIRKUNG)).toHaveAttribute("aria-required", "true");
  });
});

describe("PremiumNoticeModal – Wege hinaus", () => {
  it("sagt, dass aus dem Schreiben kein zweiter Beitrag an der Police entsteht", () => {
    renderModal();
    expect(
      screen.getByText(/Aus dem Schreiben entsteht kein zweiter Beitrag an der Police/),
    ).toHaveTextContent("Der neue Betrag wandert erst mit „Betrag übernehmen“ in den Vertrag.");
  });

  it("bricht auf Wunsch ab, ohne etwas zu senden", async () => {
    const { onClose, onSubmit } = renderModal();
    await userEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("sperrt beide Schaltflaechen, solange gespeichert wird", () => {
    renderModal({}, { busy: true });
    expect(screen.getByRole("button", { name: "Wird gespeichert …" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  });
});
