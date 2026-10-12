import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  NeedsProfileForm,
  type NeedsProfileFormProps,
} from "../components/insurance/NeedsProfileForm";
import type {
  NeedsCompleteness,
  NeedsPersonProfile,
  NeedsProfile,
  NeedsProfileInput,
} from "../lib/insuranceApi";

/**
 * Das Bedarfsprofil (Plan 4.9 und 10.1, P6).
 *
 * Der ganze Bildschirm haengt an einer einzigen Unterscheidung: **eine
 * unbeantwortete Frage ist keine Verneinung.** Wer nicht gesagt hat, dass er
 * keinen Oeltank besitzt, hat nicht gesagt, dass er keinen hat - und aus einer
 * ausgebliebenen Antwort darf niemals ein Befund werden (Leitsatz 4 der Phase).
 *
 * Die Tests pruefen deshalb beide Enden dieser Unterscheidung: dass man sie im
 * Bildschirm SIEHT und dass sie beim Speichern als `null` und nicht als `false`
 * herausgeht. Das zweite ohne das erste waere eine stille Zusage; das erste ohne
 * das zweite eine Beschriftung ohne Deckung.
 *
 * Der zweite Strang ist der Datenschutz: die Personenangaben sind der
 * sensibelste Datensatz des Moduls. Die Oberflaeche baut dafuer kein zweites
 * Rechtesystem, sondern liest ab, was der Server als schreibbar meldet.
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

/** Ein Profil, in dem NICHTS beantwortet ist - der Zustand jedes Haushalts, der
 *  das Formular zum ersten Mal sieht. */
function profil(overrides: Partial<NeedsProfile> = {}): NeedsProfile {
  return {
    housing_form: null,
    living_area_sqm: null,
    elemental_zone: null,
    has_oil_tank: null,
    liquid_reserve_minor: null,
    pets: null,
    travel_frequency: null,
    vehicle_count: null,
    side_business: null,
    volunteering: null,
    last_checked_on: null,
    version: 1,
    persons: [],
    ...overrides,
  };
}

function person(overrides: Partial<NeedsPersonProfile> = {}): NeedsPersonProfile {
  return {
    insured_person_id: "person-1",
    display_name: "Muster",
    employment_status: null,
    net_income_minor: null,
    health_coverage: null,
    beihilfe_rate_bp: null,
    has_dependents: null,
    education_until: null,
    moved_out_on: null,
    can_manage: true,
    version: 1,
    ...overrides,
  };
}

function renderFormular(props: Partial<NeedsProfileFormProps> = {}) {
  const onSave = vi.fn();
  const onSavePerson = vi.fn();
  const alle: NeedsProfileFormProps = {
    profile: profil(),
    completeness: null,
    loading: false,
    error: null,
    saving: false,
    saveError: null,
    canEdit: true,
    onSave,
    onSavePerson,
    ...props,
  };
  const view = render(<NeedsProfileForm {...alle} />);
  return { ...view, onSave, onSavePerson };
}

/** Der Rahmen einer Frage - ueber ihre Beschriftung gegriffen. Er traegt den
 *  einzigen Zustand, den dieses Formular sichtbar machen muss. */
function frage(beschriftung: string): HTMLElement {
  const feld = screen.getByLabelText(beschriftung);
  const rahmen = feld.closest(".insurance-needs-question");
  if (!rahmen) throw new Error(`Frage "${beschriftung}" hat keinen Rahmen`);
  return rahmen as HTMLElement;
}

function speichern(): HTMLElement {
  return screen.getByRole("button", { name: "Angaben speichern" });
}

/** Die Angaben, die beim Speichern des Haushalts herausgehen. */
function gespeichert(onSave: ReturnType<typeof vi.fn>): NeedsProfileInput {
  expect(onSave).toHaveBeenCalledTimes(1);
  return onSave.mock.calls[0]?.[0] as NeedsProfileInput;
}

// ---------------------------------------------------------------------------
// Pflichtfall 1: „keine Angabe" ist sichtbar von „nein" unterschieden
// ---------------------------------------------------------------------------

describe("NeedsProfileForm – „keine Angabe“ ist nicht „nein“", () => {
  it("bietet jede Ja/Nein-Frage mit drei Antworten an", () => {
    renderFormular();

    const auswahl = screen.getByLabelText("Öltank auf dem Grundstück?");
    const antworten = within(auswahl as HTMLElement)
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(antworten).toEqual(["keine Angabe", "ja", "nein"]);
  });

  it("zeichnet die unbeantwortete Frage sichtbar aus, die verneinte nicht", () => {
    const { unmount } = renderFormular({ profile: profil({ has_oil_tank: null }) });

    const offen = frage("Öltank auf dem Grundstück?");
    expect(screen.getByLabelText("Öltank auf dem Grundstück?")).toHaveValue("");
    // Nicht nur der Vorbelegungszustand eines leeren Feldes: ein eigener,
    // lesbarer Vermerk am Rahmen.
    expect(within(offen).getByText(/ohne Angabe – dazu entsteht kein Befund/)).toBeInTheDocument();
    expect(offen.className).toContain("insurance-needs-question--unset");
    const textOffen = offen.textContent ?? "";

    unmount();
    renderFormular({ profile: profil({ has_oil_tank: false }) });

    const verneint = frage("Öltank auf dem Grundstück?");
    expect(screen.getByLabelText("Öltank auf dem Grundstück?")).toHaveValue("no");
    expect(within(verneint).queryByText(/ohne Angabe/)).toBeNull();
    expect(verneint.className).not.toContain("insurance-needs-question--unset");

    // Die beiden Zustaende sehen im Bildschirm verschieden aus - und zwar im
    // Text, nicht nur in einer Klasse.
    expect(verneint.textContent).not.toEqual(textOffen);
  });

  it("speichert eine unbeantwortete Frage als „keine Angabe“ und nicht als „nein“", async () => {
    const nutzer = userEvent.setup();
    const { onSave } = renderFormular();

    await nutzer.click(speichern());

    const eingabe = gespeichert(onSave);
    expect(eingabe.has_oil_tank).toBeNull();
    expect(eingabe.side_business).toBeNull();
    expect(eingabe.volunteering).toBeNull();
    expect(eingabe.housing_form).toBeNull();
    expect(eingabe.living_area_sqm).toBeNull();
    expect(eingabe.vehicle_count).toBeNull();
    expect(eingabe.liquid_reserve_minor).toBeNull();
    expect(eingabe.travel_frequency).toBeNull();
    expect(eingabe.elemental_zone).toBeNull();

    // Ausdruecklich: keine einzige Verneinung ist dabei entstanden.
    expect(Object.values(eingabe)).not.toContain(false);
  });

  it("unterscheidet beim Speichern „nein“ von der ausgebliebenen Antwort", async () => {
    const nutzer = userEvent.setup();
    const { onSave } = renderFormular();

    await nutzer.selectOptions(screen.getByLabelText("Öltank auf dem Grundstück?"), "no");
    await nutzer.click(speichern());

    const eingabe = gespeichert(onSave);
    expect(eingabe.has_oil_tank).toBe(false);
    // Das Nachbarfeld bleibt unbeantwortet - eine Antwort faerbt nicht ab.
    expect(eingabe.volunteering).toBeNull();
  });

  it("hält bei den Tieren drei Aussagen auseinander", async () => {
    const nutzer = userEvent.setup();

    // 1. nicht beantwortet -> null
    const ohne = renderFormular({ profile: profil({ pets: null }) });
    await nutzer.click(speichern());
    expect(gespeichert(ohne.onSave).pets).toBeNull();
    ohne.unmount();

    // 2. „keine Tiere" -> leere Liste, die einzige Aussage, die R14a tragen darf
    const keine = renderFormular({ profile: profil({ pets: null }) });
    await nutzer.selectOptions(screen.getByLabelText("Tiere im Haushalt?"), "no");
    await nutzer.click(speichern());
    expect(gespeichert(keine.onSave).pets).toEqual([]);
    keine.unmount();

    // 3. benannte Tiere -> die Arten, nach denen der Katalog fragt
    const hund = renderFormular({ profile: profil({ pets: ["dog"] }) });
    expect(screen.getByLabelText("Tiere im Haushalt?")).toHaveValue("yes");
    expect(screen.getByRole("checkbox", { name: "Hund" })).toBeChecked();
    await nutzer.click(screen.getByRole("checkbox", { name: "Katze" }));
    await nutzer.click(speichern());
    expect(gespeichert(hund.onSave).pets).toEqual(["dog", "cat"]);
  });

  it("zählt „nachgesehen, aber unbekannt“ als Antwort", async () => {
    const nutzer = userEvent.setup();
    const { onSave } = renderFormular();

    // Der Unterschied ist im Hilfetext benannt und wird beim Speichern gehalten:
    // „unbekannt" ist eine Aussage, „keine Angabe" ist keine.
    expect(
      screen.getByText(
        /„Nachgesehen, aber unbekannt“ ist eine Antwort, „keine Angabe“ ist keine\./,
      ),
    ).toBeInTheDocument();

    await nutzer.selectOptions(screen.getByLabelText("Hochwasserzone (ZÜRS)"), "unknown");
    await nutzer.click(speichern());
    expect(gespeichert(onSave).elemental_zone).toBe("unknown");
  });

  it("nimmt die 0 bei den Fahrzeugen als Antwort und nicht als Leerfeld", async () => {
    const nutzer = userEvent.setup();
    const { onSave } = renderFormular();

    await nutzer.type(screen.getByLabelText("Fahrzeuge im Haushalt"), "0");
    expect(frage("Fahrzeuge im Haushalt").className).not.toContain(
      "insurance-needs-question--unset",
    );

    await nutzer.click(speichern());
    expect(gespeichert(onSave).vehicle_count).toBe(0);
  });

  it("sagt im Kopf, dass eine offene Frage keinen Befund erzeugt", () => {
    renderFormular();
    expect(screen.getByText(/Eine Frage ohne Antwort ist keine Verneinung:/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /wo nichts steht, prüft der Abgleich nichts und meldet dazu auch keine Lücke/,
      ),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Was eine fehlende Angabe kostet - benannt, aber nicht bewertet
// ---------------------------------------------------------------------------

describe("NeedsProfileForm – welche Regeln eine fehlende Angabe blockiert", () => {
  const vollstaendigkeit: NeedsCompleteness = {
    missing: [
      { field: "has_oil_tank", scope: "household", person_id: null, blocks_rule_keys: ["R13"] },
      {
        field: "employment_status",
        label: "Erwerbsstatus",
        scope: "person",
        person_id: "person-1",
        person_name: "Muster",
        blocks_rule_keys: ["R4", "R4a"],
      },
    ],
  };

  it("nennt an der offenen Frage die Regeln, die dadurch ungeprüft bleiben", () => {
    renderFormular({ completeness: vollstaendigkeit });
    expect(
      within(frage("Öltank auf dem Grundstück?")).getByText(/Ungeprüft bleiben dadurch: R13\./),
    ).toBeInTheDocument();
  });

  it("nimmt den Hinweis zurück, sobald die Frage beantwortet ist", () => {
    renderFormular({
      completeness: vollstaendigkeit,
      profile: profil({ has_oil_tank: false }),
    });
    expect(within(frage("Öltank auf dem Grundstück?")).queryByText(/Ungeprüft bleiben/)).toBeNull();
  });

  it("führt die fehlenden Angaben zusammen, ohne daraus eine Note zu machen", () => {
    const { container } = renderFormular({
      completeness: vollstaendigkeit,
      profile: profil({ persons: [person()] }),
    });

    expect(
      screen.getByText("Welche Regeln zurzeit nicht geprüft werden können"),
    ).toBeInTheDocument();
    expect(screen.getByText(/ohne diese Angabe bleiben R4, R4a ungeprüft/)).toBeInTheDocument();

    // Kein Vollstaendigkeitsbalken, keine Quote, kein Prozentwert ueber das
    // Profil - dieselbe Grenze wie bei den Befunden (Plan 10.2).
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\bScore\b/i);
    expect(text).not.toMatch(/\bProzent\b/i);
    expect(text).not.toMatch(/\d+\s*(?:von|\/)\s*\d+\s*(?:Angaben|Fragen|Feldern)/);
    expect(container.querySelector("progress")).toBeNull();
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
    expect(container.querySelector('[role="meter"]')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Eingaben, die keine Zahl sind
// ---------------------------------------------------------------------------

describe("NeedsProfileForm – ungültige Eingaben", () => {
  it("speichert eine unlesbare Zahl nicht als „keine Angabe“", async () => {
    const nutzer = userEvent.setup();
    const { onSave } = renderFormular();

    await nutzer.type(screen.getByLabelText("Wohnfläche in m²"), "hundertvierzig");
    await nutzer.click(speichern());

    expect(onSave).not.toHaveBeenCalled();
    expect(
      within(screen.getByRole("alert")).getByText(
        "Die Wohnfläche ist eine ganze Zahl in Quadratmetern.",
      ),
    ).toBeInTheDocument();
  });

  it("rechnet einen deutschen Betrag in Minor Units um", async () => {
    const nutzer = userEvent.setup();
    const { onSave } = renderFormular();

    await nutzer.type(screen.getByLabelText("Verfügbare Rücklage"), "5.000,50");
    await nutzer.click(speichern());

    expect(gespeichert(onSave).liquid_reserve_minor).toBe(500050);
  });
});

// ---------------------------------------------------------------------------
// Personen: derselbe Schutz wie bei den Gesundheitspolicen
// ---------------------------------------------------------------------------

describe("NeedsProfileForm – Personenangaben", () => {
  it("lässt eine Person, die der Server nur zum Lesen freigibt, nicht ändern", () => {
    renderFormular({
      profile: profil({ persons: [person({ can_manage: false, display_name: "Sophie" })] }),
    });

    expect(screen.getByLabelText("Erwerbsstatus")).toBeDisabled();
    expect(screen.getByLabelText("Nettoeinkommen im Monat")).toBeDisabled();
    expect(
      screen.getByText("Diese Angaben dürfen Sie lesen, aber nicht ändern."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Angaben zu Sophie speichern" })).toBeNull();
  });

  it("behandelt eine Person ohne Freigabeangabe als nur lesbar", () => {
    const { can_manage: _weg, ...ohneAngabe } = person({ display_name: "Sophie" });
    renderFormular({ profile: profil({ persons: [ohneAngabe as NeedsPersonProfile] }) });

    // Die vorsichtige Richtung: fehlt die Angabe, wird nicht geschrieben.
    expect(screen.getByLabelText("Erwerbsstatus")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Angaben zu Sophie speichern" })).toBeInTheDocument();
  });

  it("erklärt eine leere Personenliste, statt sie als Tatsache zu behaupten", () => {
    renderFormular({ profile: profil({ persons: [] }) });

    // Zwei Ursachen, eine Anzeige - und die Oberflaeche behauptet keine davon.
    expect(
      screen.getByText(
        /es ist keine versicherte Person erfasst, oder Sie sind für keine freigegeben/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Wer keine Freigabe hat, liest an dieser Stelle gar nichts/),
    ).toBeInTheDocument();
  });

  it("fragt den Beihilfesatz nur bei Beihilfe und lässt ihn beim Wechsel nicht stehen", async () => {
    const nutzer = userEvent.setup();
    const { onSavePerson } = renderFormular({
      profile: profil({
        persons: [person({ health_coverage: "pkv_beihilfe", beihilfe_rate_bp: 7000 })],
      }),
    });

    expect(screen.getByLabelText("Beihilfebemessungssatz")).toHaveValue("7000");

    await nutzer.selectOptions(screen.getByLabelText("Krankenversicherung"), "gkv");
    expect(screen.queryByLabelText("Beihilfebemessungssatz")).toBeNull();

    await nutzer.click(screen.getByRole("button", { name: "Angaben zu Muster speichern" }));

    expect(onSavePerson).toHaveBeenCalledTimes(1);
    expect(onSavePerson).toHaveBeenCalledWith("person-1", {
      employment_status: null,
      net_income_minor: null,
      health_coverage: "gkv",
      // Ein stehen gebliebener Bemessungssatz waere eine Angabe, die niemand
      // mehr gemacht hat.
      beihilfe_rate_bp: null,
      has_dependents: null,
      education_until: null,
      moved_out_on: null,
    });
  });

  it("speichert die Datumsangaben einer Person und lässt die offenen offen", async () => {
    const nutzer = userEvent.setup();
    const { onSavePerson } = renderFormular({
      profile: profil({ persons: [person()] }),
    });

    fireEvent.change(screen.getByLabelText("Erstausbildung endet am"), {
      target: { value: "2027-07-31" },
    });
    await nutzer.click(screen.getByRole("button", { name: "Angaben zu Muster speichern" }));

    const eingabe = onSavePerson.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(eingabe.education_until).toBe("2027-07-31");
    expect(eingabe.moved_out_on).toBeNull();
    expect(eingabe.has_dependents).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Zustaende der Seite
// ---------------------------------------------------------------------------

describe("NeedsProfileForm – Ausbaustand, Fehler, Leserechte", () => {
  it("nennt den fehlenden Endpunkt einen Ausbaustand und keine Störung", () => {
    renderFormular({ profile: null, unavailable: true, error: "404", loading: false });

    expect(
      screen.getByText(/Der Bedarfsabgleich ist auf diesem Server noch nicht eingerichtet\./),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Es fehlen keine Angaben – es fehlt der Endpunkt\./),
    ).toBeInTheDocument();
    // Kein Alarm: ein Ausbaustand ist kein Fehler des Nutzers.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("404")).toBeNull();
  });

  it("zeigt einem Lesenden keine Schaltfläche zum Speichern", () => {
    renderFormular({ canEdit: false, profile: profil({ persons: [person()] }) });

    expect(screen.queryByRole("button", { name: "Angaben speichern" })).toBeNull();
    expect(screen.getByLabelText("Wie wohnen Sie?")).toBeDisabled();
    expect(screen.getByLabelText("Erwerbsstatus")).toBeDisabled();
  });

  it("meldet einen Speicherfehler dort, wo gespeichert wurde", () => {
    renderFormular({ saveError: "Die Angaben wurden zwischenzeitlich geändert." });
    expect(
      within(screen.getByRole("alert")).getByText("Die Angaben wurden zwischenzeitlich geändert."),
    ).toBeInTheDocument();
  });

  it("nennt den Stand des letzten Abgleichs", () => {
    renderFormular({ profile: profil({ last_checked_on: "2026-08-05" }) });
    expect(screen.getByText("Zuletzt geprüft am 05.08.2026.")).toBeInTheDocument();
  });
});

/**
 * Die beiden Fehler des Formulars, die bei der Abnahme gefunden und behoben
 * wurden. Beide drehen sich um dieselbe Frage: welche Aussage gehoert zu wem.
 */
describe("Bedarfsprofil – die Korrekturen der Abnahme", () => {
  it("macht aus „ja, Tiere“ ohne Art kein „keine Tiere“", async () => {
    const nutzer = userEvent.setup();
    const { onSave } = renderFormular({ profile: profil({ pets: null }) });

    await nutzer.selectOptions(screen.getByLabelText("Tiere im Haushalt?"), "yes");
    await nutzer.click(speichern());

    // Ungeprueft ginge hier `[]` raus - serverseitig "gefragt, keine Tiere".
    // Aus dem Ja des Nutzers wuerde ein Nein.
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/Bitte wählen Sie mindestens eine Tierart aus/)).toBeInTheDocument();
  });

  it("meldet den Fehler einer Person nur unter dieser Person", () => {
    renderFormular({
      profile: profil({
        persons: [
          person({ insured_person_id: "person-a", display_name: "Anna" }),
          person({ insured_person_id: "person-b", display_name: "Bela" }),
        ],
      }),
      savingPersonId: null,
      personSaveError: { personId: "person-b", message: "Bela wurde zwischenzeitlich geändert." },
    });

    // Genau eine Meldung, nicht drei: bei Angaben, deren Kern die Trennung
    // nach Personenfreigabe ist, darf ein Fehler nicht unter einer fremden
    // Person stehen.
    const meldungen = screen.getAllByRole("alert");
    expect(meldungen).toHaveLength(1);
    expect(meldungen[0]).toHaveTextContent("Bela wurde zwischenzeitlich geändert.");
  });

  it("meldet einen laufenden Speichervorgang nur an der Stelle, die speichert", () => {
    renderFormular({
      profile: profil({
        persons: [
          person({ insured_person_id: "person-a", display_name: "Anna" }),
          person({ insured_person_id: "person-b", display_name: "Bela" }),
        ],
      }),
      saving: false,
      savingPersonId: "person-b",
    });

    expect(screen.getAllByText("Wird gespeichert …")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Angaben speichern" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Angaben zu Anna speichern" })).toBeEnabled();
  });
});
