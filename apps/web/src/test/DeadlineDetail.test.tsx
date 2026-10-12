import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DeadlineDetail } from "../components/insurance/DeadlineDetail";
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

let kette: HTMLElement;

function renderHerleitung(overrides: Partial<InsurancePolicy> = {}) {
  const onOpenPolicy = vi.fn();
  const onClose = vi.fn();
  const { container } = render(
    <DeadlineDetail policy={police(overrides)} onOpenPolicy={onOpenPolicy} onClose={onClose} />,
  );
  // Die Kette ausdruecklich ueber ihre Klasse greifen: neben ihr steht eine
  // <dl> mit Grundlage und Feiertagskalender, und je nach Umgebung zaehlt auch
  // die als Liste.
  kette = container.querySelector("ol.insurance-chain") as HTMLElement;
  return { onOpenPolicy, onClose };
}

/** Die fuenf Glieder der Kette, in der Reihenfolge, in der sie dastehen. */
function stufen(): HTMLElement[] {
  return within(kette).getAllByRole("listitem");
}

function stufe(index: number): HTMLElement {
  const alle = stufen();
  return alle[index] as HTMLElement;
}

/** Wert eines Glieds - dieselbe Stelle, an der bei fehlender Angabe "nicht
 *  berechenbar" stehen muss statt gar nichts. */
function wert(index: number): string {
  return (stufe(index).querySelector(".insurance-chain-value") as HTMLElement).textContent ?? "";
}

function herkunft(index: number): string {
  return (stufe(index).querySelector(".insurance-chain-origin") as HTMLElement).textContent ?? "";
}

describe("DeadlineDetail – die fünf Glieder", () => {
  it("legt die ganze Kette offen, statt ein Datum zu behaupten", () => {
    renderHerleitung();

    expect(stufen()).toHaveLength(5);
    expect(stufe(0)).toHaveTextContent("Hauptfälligkeit");
    expect(stufe(1)).toHaveTextContent("Kündigungsfrist");
    expect(stufe(2)).toHaveTextContent("Rechtliches Fristende");
    expect(stufe(3)).toHaveTextContent("Letzter sicherer Zustelltag");
    expect(stufe(4)).toHaveTextContent("Empfohlen absenden bis");

    expect(wert(0)).toBe("01.10.2026");
    expect(wert(1)).toBe("3 Monate");
    expect(wert(2)).toBe("30.09.2026");
    expect(wert(3)).toBe("27.09.2026");
    expect(wert(4)).toBe("20.09.2026");

    // Und das Ergebnis daneben: bis dahin laeuft die Versicherung weiter.
    expect(screen.getByText("Wirksam zum").parentElement).toHaveTextContent("31.12.2026");
  });

  it("sagt an der Hauptfaelligkeit, dass sie der Beginn des Versicherungsjahres ist", () => {
    // Hart erarbeitete Wahrheit des Rechenkerns: wer das dreht, erklaert eine
    // verspaetete Kuendigung fuer rechtzeitig.
    renderHerleitung();
    expect(stufe(0)).toHaveTextContent(
      "Der Beginn des Versicherungsjahres, nicht sein Ende – die laufende Periode endet am Tag davor.",
    );
  });

  it("nennt bei einer Kuendigungsfrist ausdruecklich, dass das Fristende NICHT wandert", () => {
    renderHerleitung();

    const fristende = stufe(2);
    expect(fristende).toHaveTextContent(
      "§ 193 BGB ist auf Kündigungsfristen weder unmittelbar noch entsprechend anwendbar",
    );
    expect(fristende).toHaveTextContent("BGH, Urteil vom 17.02.2005 – III ZR 172/04");
    expect(fristende).toHaveTextContent(
      "Das Fristende wandert deshalb nicht auf den nächsten Werktag",
    );
    expect(fristende).toHaveTextContent("eine Kündigung danach wäre verspätet");
    // Die Gegenaussage darf hier nicht zugleich dastehen.
    expect(fristende).not.toHaveTextContent("tritt der nächste Werktag an seine Stelle");
  });

  it("dreht den Satz bei einer Fristart um, auf die § 193 BGB anwendbar ist", () => {
    renderHerleitung({ deadline: frist({ deadline_kind: "widerrufsfrist" }) });

    const fristende = stufe(2);
    expect(fristende).toHaveTextContent(
      "Für Widerrufsfristen gilt § 193 BGB: fällt das Fristende auf einen Samstag, Sonntag oder Feiertag, tritt der nächste Werktag an seine Stelle.",
    );
    expect(fristende).not.toHaveTextContent("III ZR 172/04");
    // Die Fristart steht auch im Kopf, damit die Herleitung nicht zu einer
    // anderen Frist gelesen wird als der gemeinten.
    expect(screen.getByText(/^Hausrat · Allianz ·/)).toHaveTextContent("Widerrufsfrist");
  });

  it("begruendet den sicheren Zustelltag mit dem Zugang, nicht mit dem Absenden", () => {
    renderHerleitung();
    expect(stufe(3)).toHaveTextContent(
      "Eine Kündigung wirkt erst mit Zugang beim Versicherer (§ 130 Abs. 1 BGB).",
    );
    expect(stufe(3)).toHaveTextContent("der letzte sichere Tag liegt deshalb vor dem Fristende");
  });

  it("liest den Versandpuffer als Abstand zweier Serverdaten ab, statt ihn nachzurechnen", () => {
    // 20.09. absenden, 27.09. sicherer Zugang: sieben Tage. Stuende die
    // Zuordnung Zustellweg -> Werktage hier noch einmal, gaebe es zwei
    // Meinungen ueber denselben Tag.
    renderHerleitung();
    expect(stufe(4)).toHaveTextContent(
      "Zustellweg Brief: 7 Tage vor dem sicheren Zustelltag – so lange braucht die Zustellung.",
    );
  });

  it("laesst den Versandpuffer weg, wo der Zugang auf den Absendetag faellt", () => {
    renderHerleitung({
      service_channel: "kuendigungsbutton",
      deadline: frist({ safe_service_on: "2026-09-30", recommended_action_on: "2026-09-30" }),
    });
    expect(stufe(4)).toHaveTextContent(
      "Zustellweg Kündigungsbutton: der Zugang fällt auf den Absendetag, ein Versandpuffer entfällt.",
    );
  });
});

describe("DeadlineDetail – Feiertag und Bundesland", () => {
  it("nennt den Feiertag, der den Zustelltag verschoben hat, beim Namen", () => {
    renderHerleitung({
      recipient_state: "BY",
      deadline: frist({ safe_service_on: "2026-10-30", safe_service_holiday: "Allerheiligen" }),
    });
    expect(stufe(3)).toHaveTextContent("Hier verschiebt ihn Allerheiligen.");
  });

  it("schweigt ueber den Feiertag, solange der Server keinen liefert", () => {
    // Ein Feiertagskalender im Browser waere eine zweite Meinung ueber
    // denselben Tag - also lieber kein Satz als ein geratener.
    renderHerleitung();
    expect(stufe(3)).not.toHaveTextContent("Hier verschiebt ihn");
  });

  it("fuehrt den Feiertagskalender am Sitz des Empfaengers, nicht am Haushalt", () => {
    renderHerleitung({ recipient_state: "BY" });
    const zeile = screen.getByText("Feiertagskalender").closest("div") as HTMLElement;
    expect(zeile).toHaveTextContent("Bayern");
    expect(zeile).toHaveTextContent(
      "Sitz des Empfängers – § 193 BGB stellt auf den Feiertag am Erklärungsort ab.",
    );
  });

  it("sagt beim fehlenden Bundesland, was dann gilt, statt das Feld leer zu lassen", () => {
    renderHerleitung({ recipient_state: null });
    const zeile = screen.getByText("Feiertagskalender").closest("div") as HTMLElement;
    expect(zeile).toHaveTextContent("kein Bundesland erfasst");
    expect(zeile).toHaveTextContent("zählen nur die bundesweiten Feiertage");
    expect(zeile).toHaveTextContent("Die Angabe gehört an die Police, nicht in die Einstellungen.");
  });

  it("reicht ein unbekanntes Kuerzel durch, statt es zu verschlucken", () => {
    renderHerleitung({ recipient_state: "XX" });
    expect(screen.getByText("Feiertagskalender").closest("div")).toHaveTextContent("XX");
  });
});

describe("DeadlineDetail – jede Fristart bekommt ihr eigenes Recht", () => {
  it("stellt eine Zahlungsfrist nicht unter Paragraph 130 BGB und nicht unter den Kuendigungsbutton", () => {
    // Eine Zahlung ist keine empfangsbeduerftige Willenserklaerung, § 130 BGB
    // regelt sie nicht, und § 312k BGB hat mit ihr nichts zu tun. Beide Saetze
    // standen frueher unter JEDER Fristart.
    renderHerleitung({ deadline: frist({ deadline_kind: "zahlungsfrist" }) });

    // Der Paragraph darf genannt werden - aber nur, um zu sagen, dass er hier
    // NICHT gilt. Die zusagende Fassung ("wirkt erst mit Zugang … § 130
    // Abs. 1 BGB") waere die falsche Auskunft.
    expect(stufe(3)).not.toHaveTextContent("§ 130 Abs. 1 BGB");
    expect(stufe(3)).toHaveTextContent("§ 130 BGB regelt sie nicht");
    expect(stufe(3)).toHaveTextContent("wann der Betrag beim Versicherer eingeht");
    expect(screen.queryByText(/§ 312k BGB/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Kündigungsbutton/)).not.toBeInTheDocument();
  });

  it("schickt eine Klagefrist ans Gericht und nicht an den Versicherer", () => {
    renderHerleitung({ deadline: frist({ deadline_kind: "klagefrist" }) });

    expect(stufe(3)).toHaveTextContent("Eingang bei Gericht");
    expect(stufe(3)).not.toHaveTextContent("§ 130");
    expect(screen.queryByText(/Kündigungsbutton/)).not.toBeInTheDocument();
  });

  it("behaelt Paragraph 130 BGB dort, wo er gilt", () => {
    // Kuendigung, Widerruf und Anfechtung SIND empfangsbeduerftige
    // Willenserklaerungen - der Satz darf nicht pauschal verschwinden.
    renderHerleitung({ deadline: frist({ deadline_kind: "widerrufsfrist" }) });

    expect(stufe(3)).toHaveTextContent("Ein Widerruf wirkt erst mit Zugang");
    expect(stufe(3)).toHaveTextContent("§ 130 Abs. 1 BGB");
    // Der Kuendigungsbutton bleibt trotzdem draussen: er kuendigt Vertraege.
    expect(screen.queryByText(/§ 312k BGB/)).not.toBeInTheDocument();
  });

  it("verspricht Wirksamkeit nur dort, wo gekuendigt wird", () => {
    renderHerleitung({ deadline: frist({ deadline_kind: "zahlungsfrist" }) });
    expect(screen.queryByText("Wirksam zum")).not.toBeInTheDocument();
    expect(screen.getByText("Ende der laufenden Periode")).toBeInTheDocument();
  });

  it("laesst der Kuendigung ihren Rettungsweg ueber den Kuendigungsbutton", () => {
    renderHerleitung();
    expect(stufe(3)).toHaveTextContent("Eine Kündigung wirkt erst mit Zugang");
    expect(screen.getByText(/§ 312k BGB/)).toBeInTheDocument();
    expect(screen.getByText("Wirksam zum")).toBeInTheDocument();
  });
});

describe("DeadlineDetail – fehlende und angenommene Angaben", () => {
  it("zeigt bei fehlender Hauptfaelligkeit an jeder Stufe eine Aussage statt eines leeren Feldes", () => {
    renderHerleitung({
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
    });

    // Glied 1 sagt, WAS fehlt; die drei gerechneten Glieder sagen, dass sie
    // deshalb nicht berechenbar sind. Kein Feld bleibt leer.
    expect(wert(0)).toBe("nicht erfasst");
    expect(herkunft(0)).toBe("nicht berechenbar – Angabe fehlt");
    expect(wert(2)).toBe("nicht berechenbar – Angabe fehlt");
    expect(wert(3)).toBe("nicht berechenbar");
    expect(wert(4)).toBe("nicht berechenbar");
    for (const index of [0, 1, 2, 3, 4]) {
      expect(wert(index).trim()).not.toBe("");
    }
    expect(screen.getByText("Wirksam zum").parentElement).toHaveTextContent("nicht berechenbar");

    // Und die Begruendung des Rechners steht dabei, nicht nur sein Schweigen.
    expect(
      screen.getByText("Hauptfälligkeit fehlt – ohne sie gibt es keinen Kündigungstermin."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Solange eine Angabe dieser Kette fehlt, gibt es hier kein Datum – geraten wird nicht.",
      ),
    ).toBeInTheDocument();
  });

  it("nennt eine fehlende Kuendigungsfrist beim Namen, statt null Monate zu rechnen", () => {
    renderHerleitung({ notice_period_months: null });
    expect(wert(1)).toBe("nicht hinterlegt");
    expect(stufe(1)).toHaveClass("insurance-chain-step--not_computable");
  });

  it("erfindet keine Herkunft fuer eine Kuendigungsfrist, die es nicht gibt", () => {
    // `notice_terms_source` steht auch dann auf 'catalog_suggested', wenn der
    // Katalog gar nichts vorgeschlagen hat. "nicht hinterlegt, angenommen
    // (Katalog)" widersprach sich selbst - haette der Katalog etwas
    // vorgeschlagen, stuende eine Zahl da.
    renderHerleitung({
      notice_period_months: null,
      notice_terms_source: "catalog_suggested",
    });

    expect(wert(1)).toBe("nicht hinterlegt");
    expect(herkunft(1)).toBe("nicht berechenbar – Angabe fehlt");
    expect(herkunft(1)).not.toContain("Katalog");
  });

  it("setzt die Kuendigungsfrist im Singular, wenn es ein Monat ist", () => {
    renderHerleitung({ notice_period_months: 1 });
    expect(wert(1)).toBe("1 Monat");
  });

  it("zeigt an jedem Glied seine eigene Sicherheit, damit sichtbar ist, welches die Kette traegt", () => {
    // Die Hauptfaelligkeit ist geraten, die Fristlaenge bestaetigt. Traegen
    // alle Glieder dieselbe Ampel, ist nicht mehr abzulesen, was zu
    // bestaetigen waere.
    renderHerleitung({
      main_due_source: "catalog_suggested",
      notice_terms_source: "user_confirmed",
      deadline: frist({ certainty: "assumed" }),
    });

    expect(stufe(0)).toHaveClass("insurance-chain-step--assumed");
    expect(herkunft(0)).toBe("angenommen (Katalog)");
    expect(stufe(1)).toHaveClass("insurance-chain-step--confirmed");
    expect(herkunft(1)).toBe("bestätigt (manuell)");
    for (const index of [2, 3, 4]) {
      expect(stufe(index)).toHaveClass("insurance-chain-step--assumed");
      expect(herkunft(index)).toBe("angenommen (Katalog)");
    }

    expect(
      screen.getByText(
        "Solange ein Glied dieser Kette angenommen ist, bleibt das Ergebnis eine Annahme: kein Countdown, keine Ampel, keine Erinnerung.",
      ),
    ).toBeInTheDocument();
  });

  it("schweigt ueber die Sicherheit, wo die ganze Kette bestaetigt ist", () => {
    renderHerleitung();
    expect(screen.queryByText(/bleibt das Ergebnis eine Annahme/)).not.toBeInTheDocument();
    expect(screen.queryByText(/geraten wird nicht/)).not.toBeInTheDocument();
  });

  it("nennt die Grundlage als Wort und den Nachweis nur, wenn der Server ihn liefert", () => {
    renderHerleitung({
      anchor_basis_kind: "policy_terms",
      deadline: frist({ basis: "§ 11 Abs. 4 VVG", basis_note: "Kürzere Frist laut Bedingungen." }),
    });

    const zeile = screen.getByText("Grundlage").closest("div") as HTMLElement;
    expect(zeile).toHaveTextContent("Versicherungsbedingungen");
    expect(zeile).toHaveTextContent("§ 11 Abs. 4 VVG");
    // Die Wendung im Satz kommt aus demselben Wert wie das Wort in der Zeile -
    // nie aus dem Freitext.
    expect(stufe(1)).toHaveTextContent(
      "Gerechnet wird zum Ablauf des Versicherungsjahres, laut Ihren Versicherungsbedingungen.",
    );
    expect(screen.getByText("Kürzere Frist laut Bedingungen.")).toBeInTheDocument();
  });

  it("laesst die neuen Nachweisfelder aus, solange der Server sie nicht kennt", () => {
    // `basis`, `basis_note` und `action_hint` entstehen erst mit dem
    // Fristenregister. Fehlen sie, schweigt die Oberflaeche - sie denkt sich
    // keinen Nachweis aus.
    renderHerleitung();
    const zeile = screen.getByText("Grundlage").closest("div") as HTMLElement;
    expect(zeile).toHaveTextContent("Gesetz");
    expect(zeile.querySelector(".insurance-kv-origin")).toBeNull();
  });

  it("zeigt den Handlungshinweis, wo es einen gibt", () => {
    renderHerleitung({
      deadline: frist({ action_hint: "Kündigung schriftlich an den Versicherer." }),
    });
    expect(screen.getByText("Kündigung schriftlich an den Versicherer.")).toBeInTheDocument();
  });
});

describe("DeadlineDetail – Wege hinaus", () => {
  it("fuehrt in die Police, denn bestaetigt wird dort und nicht hier", async () => {
    const { onOpenPolicy } = renderHerleitung();
    await userEvent.click(screen.getByRole("button", { name: "Police öffnen" }));
    expect(onOpenPolicy).toHaveBeenCalledWith("p-hausrat");
  });

  it("schliesst die Herleitung auf Wunsch wieder", async () => {
    const { onClose } = renderHerleitung();
    await userEvent.click(screen.getByRole("button", { name: "Schließen" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("laesst keine Schaltflaeche stehen, die nichts tut", () => {
    render(<DeadlineDetail policy={police()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("weist darauf hin, dass ein verpasster Absendetag die Frist noch nicht verloren gibt", () => {
    renderHerleitung();
    expect(screen.getByText(/Wird der empfohlene Tag verpasst/)).toHaveTextContent(
      "bis zum rechtlichen Fristende kann ein Zugang auf einem schnelleren Weg",
    );
  });
});
