import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ObjectsPanel } from "../components/insurance/ObjectsPanel";
import { PersonsEditor } from "../components/insurance/PersonsEditor";
import { RegistrationHistoryPanel } from "../components/insurance/RegistrationHistoryPanel";
import type {
  InsuranceLine,
  InsurancePolicy,
  InsuredObject,
  ObjectRegistration,
  PolicyDeadline,
  PolicyObjectLink,
  PolicyPerson,
  PolicyPremium,
} from "../lib/insuranceApi";
import type { ReimbursementPerson } from "../lib/reimbursementsApi";

/**
 * Die Objektseite von P4 (Plan 4.4, 4.5): Register "Objekte", "Kennzeichen"
 * und "Personen".
 *
 * Sie stehen hier zusammen, weil sie denselben Bestand lesen - die
 * Verknuepfungsliste der Police, die Objektliste des Haushalts und die
 * Personenliste des Erstattungsmoduls. Ein Fahrzeug, das im Objektregister
 * angelegt wird, muss im Kennzeichenregister auftauchen; ein Halter dort ist
 * dieselbe Person wie im Personenregister. Getrennte Dateien haetten denselben
 * gefaelschten Dienst dreimal.
 *
 * Gefaelscht wird `fetch` und nicht die Datenschicht: geprueft werden soll
 * unter anderem, WAS die Oberflaeche schickt (`clear_fields` statt `null`, ein
 * einziger Aufruf statt Anlegen plus Verknuepfen) - das ist an einem gemockten
 * Modul nicht mehr zu sehen.
 */

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ accessToken: "test-token" }),
}));

vi.mock("../household/HouseholdProvider", () => ({
  useHousehold: () => ({ selectedHouseholdId: "household-1" }),
}));

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

interface Aufruf {
  method: string;
  pfad: string;
  body: unknown;
  ifMatch: string | null;
}

let aufrufe: Aufruf[] = [];

function antwort(daten: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(daten) });
}

/** Der Vortag - so, wie ihn der Dienst beim Schliessen einer Zulassung
 *  einsetzt. Er steht hier im gefaelschten Dienst und nicht in der
 *  Oberflaeche, und genau das ist die Aussage eines der Tests. */
function vortag(iso: string): string {
  const [jahr = 1970, monat = 1, tag = 1] = iso.split("-").map(Number);
  const datum = new Date(jahr, monat - 1, tag - 1);
  return `${datum.getFullYear()}-${String(datum.getMonth() + 1).padStart(2, "0")}-${String(
    datum.getDate(),
  ).padStart(2, "0")}`;
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

function beitrag(): PolicyPremium {
  return {
    kind: "none",
    contract_id: null,
    contract_name: null,
    contract_amount_minor: null,
    contract_frequency: null,
    external_premium_minor: null,
    external_frequency: null,
    premium_payer: null,
    premium_payer_note: null,
    premium_share_bp: 10000,
    yearly_in_fixed_costs_minor: null,
  };
}

function police(overrides: Partial<InsurancePolicy> = {}): InsurancePolicy {
  return {
    id: "p-kfz",
    line_key: "kfz_haftpflicht",
    line_label: "Kfz-Haftpflicht",
    line_purpose_short: "Zahlt, was Sie mit dem Fahrzeug bei anderen anrichten.",
    risk_field: "mobility",
    label: null,
    insurer_name: "HUK-COBURG",
    policy_number: "KFZ-1234",
    customer_number: null,
    tariff_name: null,
    purpose_note: null,
    status: "active",
    starts_on: "2019-04-01",
    ends_on: null,
    main_due_on: "2027-01-01",
    main_due_source: "user_confirmed",
    main_due_confirmed_at: "2026-08-01T09:00:00Z",
    notice_anchor: "term_end",
    anchor_basis_kind: "statute",
    notice_period_months: 1,
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
    is_health_scoped: false,
    attrs: {},
    data_source: "manual",
    line_confirmed_at: "2026-08-01T09:00:00Z",
    line_deferred_at: null,
    note: null,
    premium: beitrag(),
    deadline: frist(),
    completeness_filled: 6,
    completeness_total: 9,
    next_task: null,
    version: 1,
    created_at: "2026-08-01T09:00:00Z",
    updated_at: "2026-08-01T09:00:00Z",
    ...overrides,
  };
}

function objekt(overrides: Partial<InsuredObject> = {}): InsuredObject {
  return {
    id: "obj-golf",
    kind: "vehicle",
    label: "VW Golf",
    asset_id: null,
    address_line: null,
    postal_code: null,
    city: null,
    living_area_sqm: null,
    build_year: null,
    attrs: {},
    current_plate: "M-AB 1234",
    current_keeper_person_id: null,
    current_keeper_name: null,
    policy_count: 2,
    acquired_on: "2019-04-01",
    disposed_on: null,
    is_active: true,
    version: 3,
    created_at: "2019-04-01T09:00:00Z",
    updated_at: "2026-08-01T09:00:00Z",
    ...overrides,
  };
}

function verknuepfung(overrides: Partial<PolicyObjectLink> = {}): PolicyObjectLink {
  return {
    id: "link-golf",
    policy_id: "p-kfz",
    insured_object_id: "obj-golf",
    kind: "vehicle",
    label: "VW Golf",
    acquired_on: "2019-04-01",
    disposed_on: null,
    current_plate: "M-AB 1234",
    note: null,
    created_at: "2019-04-01T09:00:00Z",
    ...overrides,
  };
}

function zulassung(overrides: Partial<ObjectRegistration> = {}): ObjectRegistration {
  return {
    id: "reg-1",
    insured_object_id: "obj-golf",
    plate: "M-AB 1234",
    keeper_person_id: null,
    keeper_name: null,
    valid_from: "2019-04-01",
    valid_to: null,
    note: null,
    created_at: "2019-04-01T09:00:00Z",
    ...overrides,
  };
}

function person(overrides: Partial<ReimbursementPerson> = {}): ReimbursementPerson {
  return {
    id: "person-anna",
    display_name: "Anna",
    birth_date: null,
    linked_user_id: null,
    is_active: true,
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:00Z",
    ...overrides,
  };
}

function policenPerson(overrides: Partial<PolicyPerson> = {}): PolicyPerson {
  return {
    id: "pp-1",
    policy_id: "p-pkv",
    insured_person_id: "person-anna",
    display_name: "Anna",
    role: "insured",
    share_bp: null,
    valid_from: null,
    valid_to: null,
    note: null,
    created_at: "2026-01-01T10:00:00Z",
    ...overrides,
  };
}

interface Bestand {
  links: PolicyObjectLink[];
  objects: InsuredObject[];
  registrations: ObjectRegistration[];
  people: ReimbursementPerson[];
  persons: PolicyPerson[];
}

/**
 * Gefaelschter Dienst mit Gedaechtnis: was geschrieben wird, liest die naechste
 * Abfrage zurueck. Nur so laesst sich pruefen, was nach dem Speichern in der
 * Oberflaeche steht - und nicht bloss, was sie abgeschickt hat.
 */
function dienstStarten(anfang: Partial<Bestand> = {}) {
  const bestand: Bestand = {
    links: anfang.links ?? [],
    objects: anfang.objects ?? [],
    registrations: anfang.registrations ?? [],
    people: anfang.people ?? [],
    persons: anfang.persons ?? [],
  };

  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const zerlegt = new URL(url, "http://test.local");
    const pfad = zerlegt.pathname;
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
    const ifMatch = init?.headers ? new Headers(init.headers).get("If-Match") : null;
    aufrufe.push({ method, pfad, body, ifMatch });

    if (pfad.endsWith("/v1/reimbursements/people")) {
      return antwort(bestand.people);
    }

    const zulassungen = /\/v1\/insurance\/objects\/([^/]+)\/registrations$/.exec(pfad);
    if (zulassungen) {
      const objektId = zulassungen[1] as string;
      if (method === "POST") {
        const eingabe = body as ObjectRegistration;
        bestand.registrations = bestand.registrations.map((eintrag) =>
          eintrag.insured_object_id === objektId && eintrag.valid_to === null
            ? { ...eintrag, valid_to: vortag(eingabe.valid_from) }
            : eintrag,
        );
        const neu = zulassung({
          id: `reg-${bestand.registrations.length + 1}`,
          insured_object_id: objektId,
          plate: eingabe.plate ?? null,
          keeper_person_id: eingabe.keeper_person_id ?? null,
          valid_from: eingabe.valid_from,
          valid_to: null,
          note: eingabe.note ?? null,
        });
        bestand.registrations = [...bestand.registrations, neu];
        return antwort(neu);
      }
      return antwort(
        bestand.registrations.filter((eintrag) => eintrag.insured_object_id === objektId),
      );
    }

    if (/\/v1\/insurance\/policies\/[^/]+\/objects$/.test(pfad)) {
      if (method === "POST") {
        const eingabe = body as {
          insured_object_id?: string;
          new_object?: Partial<InsuredObject>;
        };
        const neuesObjekt = eingabe.new_object
          ? objekt({
              ...eingabe.new_object,
              id: `obj-neu-${bestand.objects.length + 1}`,
              current_plate: null,
              policy_count: 1,
              acquired_on: eingabe.new_object.acquired_on ?? null,
            })
          : (bestand.objects.find((eintrag) => eintrag.id === eingabe.insured_object_id) as
              InsuredObject | undefined);
        if (!neuesObjekt) return antwort({});
        if (eingabe.new_object) bestand.objects = [...bestand.objects, neuesObjekt];
        const neueVerknuepfung = verknuepfung({
          id: `link-${bestand.links.length + 1}`,
          insured_object_id: neuesObjekt.id,
          kind: neuesObjekt.kind,
          label: neuesObjekt.label,
          acquired_on: neuesObjekt.acquired_on,
          disposed_on: neuesObjekt.disposed_on,
          current_plate: neuesObjekt.current_plate,
        });
        bestand.links = [...bestand.links, neueVerknuepfung];
        return antwort(neueVerknuepfung);
      }
      return antwort(bestand.links);
    }

    const loesen = /\/v1\/insurance\/policies\/[^/]+\/objects\/([^/]+)$/.exec(pfad);
    if (loesen && method === "DELETE") {
      bestand.links = bestand.links.filter((eintrag) => eintrag.insured_object_id !== loesen[1]);
      return antwort(null);
    }

    if (/\/v1\/insurance\/policies\/[^/]+\/persons$/.test(pfad)) {
      return antwort(bestand.persons);
    }

    const personLoesen = /\/v1\/insurance\/policies\/[^/]+\/persons\/([^/]+)$/.exec(pfad);
    if (personLoesen && method === "DELETE") {
      bestand.persons = bestand.persons.filter((eintrag) => eintrag.id !== personLoesen[1]);
      return antwort(null);
    }

    if (pfad.endsWith("/v1/insurance/objects")) {
      return antwort(bestand.objects);
    }

    const aendern = /\/v1\/insurance\/objects\/([^/]+)$/.exec(pfad);
    if (aendern && method === "PATCH") {
      const geaendert = bestand.objects.map((eintrag) =>
        eintrag.id === aendern[1]
          ? {
              ...eintrag,
              ...(body as Partial<InsuredObject>),
              version: eintrag.version + 1,
              acquired_on:
                (body as { clear_fields?: string[] }).clear_fields?.includes("acquired_on") === true
                  ? null
                  : ((body as Partial<InsuredObject>).acquired_on ?? eintrag.acquired_on),
            }
          : eintrag,
      );
      bestand.objects = geaendert;
      bestand.links = bestand.links.map((eintrag) => {
        const objektDazu = geaendert.find((eintrag2) => eintrag2.id === eintrag.insured_object_id);
        return objektDazu
          ? { ...eintrag, acquired_on: objektDazu.acquired_on, label: objektDazu.label }
          : eintrag;
      });
      return antwort(bestand.objects.find((eintrag) => eintrag.id === aendern[1]));
    }

    return antwort([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return bestand;
}

function mitAbfragen(kind: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{kind}</QueryClientProvider>);
}

const KFZ_LINIE: InsuranceLine[] = [
  {
    line_key: "kfz_haftpflicht",
    risk_field: "mobility",
    gdv_sparte: "030",
    label: "Kfz-Haftpflicht",
    purpose_short: "Zahlt, was Sie mit dem Fahrzeug bei anderen anrichten.",
    purpose_long: "Pflichtversicherung nach dem Pflichtversicherungsgesetz.",
    typically_covers: [],
    typically_not_covers: [],
    is_placeholder: false,
    need_tier: "must",
    need_reason: null,
    source_url: null,
    source_checked_on: null,
    default_notice_anchor: "term_end",
    default_anchor_basis_kind: "statute",
    default_notice_period_months: 1,
    default_main_due_md: "01-01",
    default_min_term_months: 12,
    default_deadline_kind: "kuendigungsfrist",
    special_law: "PflVG",
    completeness_fields: [],
    covered_by_keys: [],
    requires_object_kind: "vehicle",
    sort_order: 10,
  },
];

function letzterAufruf(method: string, teil: string): Aufruf {
  const treffer = aufrufe.filter(
    (aufruf) => aufruf.method === method && aufruf.pfad.includes(teil),
  );
  const letzter = treffer.at(-1);
  if (!letzter) throw new Error(`Kein ${method} auf „${teil}“.`);
  return letzter;
}

beforeEach(() => {
  aufrufe = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ObjectsPanel – das Erwerbsdatum als Fristanker", () => {
  it("begründet am Formular, warum der Erwerb dort steht", async () => {
    dienstStarten();
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    await userEvent.click(screen.getByRole("radio", { name: "neues Objekt anlegen" }));

    expect(screen.getByLabelText("Erwerb am")).toBeInTheDocument();
    const begruendung = screen.getByText(/Anker der Erwerberfrist/);
    expect(begruendung).toHaveTextContent("§ 96 Abs. 2 S. 2 VVG");
    expect(begruendung).toHaveTextContent("ein Monat ab Erwerb");
    expect(begruendung).toHaveTextContent("Fristbeginn am Folgetag");
    // Kein stiller Rueckfall auf Vertragsbeginn oder Anlage der Police.
    expect(begruendung).toHaveTextContent(/nie aus einem anderen Feld abgeleitet/);
  });

  it("nennt bei einem Fahrzeug zusätzlich den Weg zu Kennzeichen und Halter", async () => {
    dienstStarten();
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    await userEvent.click(screen.getByRole("radio", { name: "neues Objekt anlegen" }));
    expect(screen.getByText(/Kennzeichen und Halter tragen Sie danach/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Art"), "pet");
    expect(screen.queryByText(/Kennzeichen und Halter tragen Sie danach/)).not.toBeInTheDocument();
  });

  it("legt Objekt und Verknüpfung in EINEM Aufruf an, mit dem Erwerbsdatum darin", async () => {
    dienstStarten();
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    await userEvent.click(screen.getByRole("radio", { name: "neues Objekt anlegen" }));
    await userEvent.type(screen.getByLabelText("Bezeichnung"), "VW Golf, blau");
    fireEvent.change(screen.getByLabelText("Erwerb am"), { target: { value: "2026-03-10" } });
    await userEvent.click(screen.getByRole("button", { name: "Objekt hinzufügen" }));

    await waitFor(() => expect(letzterAufruf("POST", "/objects")).toBeTruthy());
    expect(letzterAufruf("POST", "/objects").body).toEqual({
      new_object: { kind: "vehicle", label: "VW Golf, blau", acquired_on: "2026-03-10" },
    });
    // Genau ein Schreibvorgang: ein angelegtes, aber nicht verknuepftes Objekt
    // waere ein Fehlschlag mit halbem Ergebnis.
    expect(aufrufe.filter((aufruf) => aufruf.method === "POST")).toHaveLength(1);
    expect(await screen.findByText("erworben am 10.03.2026")).toBeInTheDocument();
  });

  it("benennt am Fahrzeug ohne Erwerbsdatum die Lücke und ihre Folge", async () => {
    dienstStarten({
      links: [verknuepfung({ acquired_on: null })],
      objects: [objekt({ acquired_on: null })],
    });
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    expect(await screen.findByText("Erwerbsdatum fehlt")).toBeInTheDocument();
    const hinweis = screen.getByText(/Erwerberfrist nach § 96 Abs. 2 S. 2 VVG nicht berechenbar/);
    expect(hinweis).toHaveTextContent(/nicht geschätzt, sondern gar nicht angezeigt/);
    // Der Weg zum Nachtragen steht an der Luecke, nicht in einem Menue.
    expect(screen.getByRole("button", { name: "Erwerbsdatum nachtragen" })).toBeInTheDocument();
  });

  it("trägt ein fehlendes Erwerbsdatum nach und schickt den Versionszähler mit", async () => {
    dienstStarten({
      links: [verknuepfung({ acquired_on: null })],
      objects: [objekt({ acquired_on: null, version: 3 })],
    });
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    await userEvent.click(await screen.findByRole("button", { name: "Erwerbsdatum nachtragen" }));
    fireEvent.change(screen.getByLabelText("Erwerb am"), { target: { value: "2026-03-10" } });
    await userEvent.click(screen.getByRole("button", { name: "Angaben speichern" }));

    await waitFor(() => expect(letzterAufruf("PATCH", "/objects/obj-golf")).toBeTruthy());
    const patch = letzterAufruf("PATCH", "/objects/obj-golf");
    expect(patch.body).toMatchObject({ acquired_on: "2026-03-10", label: "VW Golf" });
    expect(patch.ifMatch).toBe('"3"');
    expect(await screen.findByText("erworben am 10.03.2026")).toBeInTheDocument();
  });

  it("löscht ein falsches Erwerbsdatum über clear_fields und nicht über null", async () => {
    dienstStarten({ links: [verknuepfung()], objects: [objekt()] });
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    await userEvent.click(await screen.findByRole("button", { name: "Angaben bearbeiten" }));
    fireEvent.change(screen.getByLabelText("Erwerb am"), { target: { value: "" } });
    await userEvent.click(screen.getByRole("button", { name: "Angaben speichern" }));

    await waitFor(() => expect(letzterAufruf("PATCH", "/objects/obj-golf")).toBeTruthy());
    const rumpf = letzterAufruf("PATCH", "/objects/obj-golf").body as Record<string, unknown>;
    // `null` hiesse beim Dienst "nicht mitgeschickt" - das Datum bliebe stehen.
    expect(rumpf.clear_fields).toContain("acquired_on");
    expect(Object.keys(rumpf)).not.toContain("acquired_on");
    expect(await screen.findByText("Erwerbsdatum fehlt")).toBeInTheDocument();
  });
});

describe("ObjectsPanel – die Veräußerung", () => {
  it("nennt die Anzeigepflicht und gerade kein Kündigungsrecht", async () => {
    dienstStarten({
      links: [verknuepfung({ disposed_on: "2026-06-30" })],
      objects: [objekt({ disposed_on: "2026-06-30" })],
    });
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    expect(await screen.findByText("abgegeben")).toBeInTheDocument();
    expect(screen.getByText("abgegeben am 30.06.2026")).toBeInTheDocument();
    const hinweis = screen.getByText(/begründet kein Kündigungsrecht/);
    expect(hinweis).toHaveTextContent("§ 97 Abs. 1 VVG");
    expect(hinweis).toHaveTextContent(/bleibt deshalb erfasst/);
  });

  it("bietet ein abgegebenes Objekt nicht zur Verknüpfung an, hält es aber erfasst", async () => {
    dienstStarten({
      links: [],
      objects: [
        objekt({ disposed_on: "2026-06-30" }),
        objekt({ id: "obj-hund", kind: "pet", label: "Hund Emma" }),
      ],
    });
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    const auswahl = await screen.findByLabelText("Objekt");
    await waitFor(() =>
      expect(within(auswahl).getByRole("option", { name: /Hund Emma/ })).toBeInTheDocument(),
    );
    expect(within(auswahl).queryByRole("option", { name: /VW Golf/ })).not.toBeInTheDocument();
  });

  it("weist eine Veräußerung vor dem Erwerb ab, bevor sie beim Dienst landet", async () => {
    dienstStarten({ links: [verknuepfung()], objects: [objekt()] });
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    await userEvent.click(await screen.findByRole("button", { name: "Angaben bearbeiten" }));
    fireEvent.change(screen.getByLabelText("Veräußert am"), { target: { value: "2018-01-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Angaben speichern" }));

    expect(
      await screen.findByText("Die Veräußerung kann nicht vor dem Erwerb liegen."),
    ).toBeInTheDocument();
    expect(aufrufe.some((aufruf) => aufruf.method === "PATCH")).toBe(false);
  });
});

describe("ObjectsPanel – die Verknüpfung lösen", () => {
  it("sagt vorher, dass nur die Verknüpfung geht, und löst über die Objektkennung", async () => {
    dienstStarten({ links: [verknuepfung()], objects: [objekt()] });
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    await userEvent.click(await screen.findByRole("button", { name: "Verknüpfung lösen" }));
    expect(screen.getByText(/Das Objekt selbst bleibt erhalten/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Ja, lösen" }));
    await waitFor(() => expect(letzterAufruf("DELETE", "/objects/obj-golf")).toBeTruthy());
    expect(await screen.findByText(/Noch kein Objekt erfasst/)).toBeInTheDocument();
  });

  it("nennt an der leeren Liste die Objektart, die diese Sparte erwartet", async () => {
    dienstStarten();
    mitAbfragen(<ObjectsPanel policy={police()} canEdit lines={KFZ_LINIE} />);

    expect(
      await screen.findByText(/Diese Sparte erwartet ein Objekt der Art „Fahrzeug“/),
    ).toBeInTheDocument();
  });
});

describe("RegistrationHistoryPanel – der Wechsel schließt den laufenden Eintrag", () => {
  it("sagt vor dem Speichern, dass der laufende Eintrag geschlossen wird und stehen bleibt", async () => {
    dienstStarten({
      links: [verknuepfung()],
      objects: [objekt()],
      registrations: [zulassung()],
    });
    mitAbfragen(<RegistrationHistoryPanel policy={police()} canEdit />);

    expect(await screen.findByText("laufend")).toBeInTheDocument();
    const vorschau = screen.getByRole("note");
    expect(within(vorschau).getByText("Was beim Speichern passiert")).toBeInTheDocument();
    expect(vorschau).toHaveTextContent("M-AB 1234");
    expect(vorschau).toHaveTextContent(/wird geschlossen/);
    expect(vorschau).toHaveTextContent(/bleibt in der Historie stehen/);
    expect(vorschau).toHaveTextContent(/für „VW Golf“/);
    expect(screen.getByRole("button", { name: "Wechsel speichern" })).toBeInTheDocument();
  });

  it("rechnet den letzten Tag der alten Zulassung nicht selbst aus", async () => {
    dienstStarten({
      links: [verknuepfung()],
      objects: [objekt()],
      registrations: [zulassung()],
    });
    mitAbfragen(<RegistrationHistoryPanel policy={police()} canEdit />);

    await screen.findByText("laufend");
    fireEvent.change(screen.getByLabelText("Gilt ab"), { target: { value: "2026-09-01" } });

    expect(screen.getByRole("note")).toHaveTextContent("Ab 01.09.2026");
    // Der Vortag ist die Rechnung des Dienstes. Stuende er hier schon, gaebe es
    // zwei letzte Tage derselben Zulassung.
    expect(screen.queryByText(/31\.08\.2026/)).toBeNull();
  });

  it("zeigt den geschlossenen Zeitraum erst, wenn er vom Dienst zurückkommt", async () => {
    dienstStarten({
      links: [verknuepfung()],
      objects: [objekt()],
      registrations: [zulassung()],
    });
    mitAbfragen(<RegistrationHistoryPanel policy={police()} canEdit />);

    await screen.findByText("laufend");
    await userEvent.type(screen.getByLabelText("Kennzeichen"), "n-cd 77");
    fireEvent.change(screen.getByLabelText("Gilt ab"), { target: { value: "2026-09-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Wechsel speichern" }));

    await waitFor(() => expect(letzterAufruf("POST", "/registrations")).toBeTruthy());
    expect(letzterAufruf("POST", "/registrations").body).toEqual({
      plate: "N-CD 77",
      keeper_person_id: null,
      valid_from: "2026-09-01",
      note: null,
    });
    // Der letzte Tag der alten Zeile steht erst jetzt da - gerechnet hat ihn
    // der Dienst, nicht der Browser.
    expect(await screen.findByText("01.04.2019 – 31.08.2026")).toBeInTheDocument();
    const laufend = screen.getByText("laufend").closest("li") as HTMLElement;
    expect(laufend).toHaveTextContent("N-CD 77");
    expect(laufend).toHaveTextContent("seit 01.09.2026");
  });

  it("nennt beim ersten Eintrag, dass nichts geschlossen wird", async () => {
    dienstStarten({ links: [verknuepfung()], objects: [objekt()], registrations: [] });
    mitAbfragen(<RegistrationHistoryPanel policy={police()} canEdit />);

    expect(await screen.findByText(/noch keine Zulassung erfasst/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/Es wird nichts geschlossen/);
    expect(screen.getByRole("button", { name: "Zulassung erfassen" })).toBeInTheDocument();
  });

  it("fängt einen Beginn ab, der nicht nach dem laufenden Eintrag liegt", async () => {
    dienstStarten({
      links: [verknuepfung()],
      objects: [objekt()],
      registrations: [zulassung()],
    });
    mitAbfragen(<RegistrationHistoryPanel policy={police()} canEdit />);

    await screen.findByText("laufend");
    await userEvent.type(screen.getByLabelText("Kennzeichen"), "N-CD 77");
    fireEvent.change(screen.getByLabelText("Gilt ab"), { target: { value: "2019-04-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Wechsel speichern" }));

    expect(
      await screen.findByText(
        /muss nach dem Beginn des laufenden Eintrags liegen \(01\.04\.2019\)/,
      ),
    ).toBeInTheDocument();
    expect(
      aufrufe.some((aufruf) => aufruf.pfad.endsWith("/registrations") && aufruf.method === "POST"),
    ).toBe(false);
  });

  it("sagt an einer Police ohne Fahrzeug, wo Kennzeichen geführt werden", async () => {
    dienstStarten({
      links: [
        verknuepfung({
          id: "link-haus",
          insured_object_id: "obj-haus",
          kind: "property",
          label: "Wohnung",
        }),
      ],
      objects: [
        objekt({ id: "obj-haus", kind: "property", label: "Wohnung", current_plate: null }),
      ],
    });
    mitAbfragen(<RegistrationHistoryPanel policy={police({ line_key: "hausrat" })} canEdit />);

    expect(await screen.findByText(/An dieser Police hängt kein Fahrzeug/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Zulassung|Wechsel/ })).toBeNull();
  });
});

describe("PersonsEditor – die Sichtbarkeit hängt an der Person", () => {
  const pkv = police({
    id: "p-pkv",
    line_key: "pkv",
    line_label: "Private Krankenversicherung",
    risk_field: "health",
    is_health_scoped: true,
  });

  it("sagt bei einer gesundheitsnahen Police, was die erste Person ändert", async () => {
    dienstStarten({ people: [person()], persons: [] });
    mitAbfragen(<PersonsEditor policy={pkv} canEdit />);

    const hinweis = await screen.findByRole("note");
    expect(hinweis).toHaveTextContent("Diese Police ist gesundheitsnah.");
    expect(hinweis).toHaveTextContent(/entscheidet die Gesundheitsfreigabe für diese Person/);
    expect(hinweis).toHaveTextContent(/Eigentümer sehen sie immer/);
    expect(hinweis).toHaveTextContent(/dieses Register vergibt keine/);

    // Und dasselbe noch einmal am Formular, unmittelbar vor dem Knopf.
    expect(
      screen.getByText(/verengt sich die Sichtbarkeit dieser Police auf Eigentümer/),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/für alle Mitglieder des Haushalts sichtbar/),
    ).toBeInTheDocument();
  });

  it("schweigt an einer Police, die nicht gesundheitsnah ist", async () => {
    dienstStarten({ people: [person()], persons: [] });
    mitAbfragen(<PersonsEditor policy={police()} canEdit />);

    expect(await screen.findByText(/Noch keine Person erfasst/)).toBeInTheDocument();
    expect(screen.queryByText(/gesundheitsnah/)).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.getByText(/welche Policen betreffen dieses Kind/)).toBeInTheDocument();
  });

  it("nennt beim Entfernen der letzten Person die Folge für die Sichtbarkeit", async () => {
    dienstStarten({ people: [person()], persons: [policenPerson()] });
    mitAbfragen(<PersonsEditor policy={pkv} canEdit />);

    await userEvent.click(await screen.findByRole("button", { name: "Entfernen" }));
    const rueckfrage = screen.getByText(/Verknüpfung lösen\?/);
    expect(rueckfrage).toHaveTextContent(/Die Person selbst bleibt erhalten/);
    expect(rueckfrage).toHaveTextContent(/wieder für alle Mitglieder des Haushalts sichtbar/);
  });

  it("hält eine nicht mehr auflösbare Person fest, statt die Zuordnung zu verschweigen", async () => {
    dienstStarten({
      people: [],
      persons: [policenPerson({ display_name: null, insured_person_id: "person-weg" })],
    });
    mitAbfragen(<PersonsEditor policy={pkv} canEdit />);

    expect(await screen.findByText("Name nicht auflösbar")).toBeInTheDocument();
    expect(screen.getByText(/gehört nicht mehr zum Haushalt/)).toBeInTheDocument();
    // Ohne Auswahl darf die Oberflaeche nicht zum Doppelanlegen einladen.
    expect(screen.getByText(/lassen Sie sich freigeben/)).toBeInTheDocument();
  });

  it("bietet den Versicherungsnehmer nicht zweimal an", async () => {
    dienstStarten({
      people: [person(), person({ id: "person-ben", display_name: "Ben" })],
      persons: [policenPerson({ role: "policyholder" })],
    });
    mitAbfragen(<PersonsEditor policy={police()} canEdit />);

    const rollen = await screen.findByLabelText("Rolle");
    await waitFor(() =>
      expect(within(rollen).getByRole("option", { name: /Versicherungsnehmer/ })).toBeDisabled(),
    );
    expect(within(rollen).getByRole("option", { name: /Versicherungsnehmer/ })).toHaveTextContent(
      "(bereits vergeben)",
    );
  });
});
