import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ClaimTable } from "../components/insurance/ClaimTable";
import { PolicyDetail } from "../components/insurance/PolicyDetail";
import { formatMinorUnitsAsEuro } from "../lib/money";
import {
  CLAIM_ANCHOR_MISSING_TEXT,
  type ClaimEvent,
  type ClaimPayment,
  type ClaimStatus,
  type InsuranceClaimDetail,
  type InsurancePolicy,
  type PolicyDeadline,
  type PolicyPremium,
  type PolicyPurpose,
} from "../lib/insuranceApi";

/**
 * Register "Schäden" und der Vorgang darin (docs/VERSICHERUNGEN-PLAN.md, 4.8;
 * P5).
 *
 * Geprueft werden die vier Aussagen, an denen dieses Register haengt und die
 * eine Oberflaeche leicht falsch macht:
 *
 * 1. **Gesundheit gehoert nicht hierher.** An einer PKV-Police entsteht das
 *    Register gar nicht erst - stattdessen steht dort der Weg zur
 *    Erstattungsseite (Leitsatz 2). Ein Register, das jeden Anlageversuch mit
 *    einem Serverfehler beantwortet, waere schlimmer als keines.
 * 2. **Offene Stufen sehen offen aus.** Der Zeitstrahl beantwortet die Frage
 *    "was fehlt noch" nur, wenn die noch nicht erreichten Stufen ueberhaupt
 *    dastehen - und zwar als "steht aus" und nicht als leere Zeile.
 * 3. **Der fehlende Fristanker ist ein Satz.** Ohne Abschluss der
 *    Verhandlungen gibt es kein Fristende; ein leeres Feld liesse offen, ob
 *    die Frist fehlt oder nur nicht geladen wurde.
 * 4. **Gerechnet wird am Server.** Die Tageszahl kommt aus `days_left`; fehlt
 *    das Rechenergebnis, steht das Datum ohne Countdown da statt mit einer
 *    zweiten, spaeter abweichenden Zahl.
 *
 * Gefaelscht wird `fetch` und nicht die Datenschicht: geprueft wird unter
 * anderem, WAS die Oberflaeche schickt (der Vorgang mit vier Angaben, der
 * Statuswechsel ohne pauschales `decision: null`) - das ist an einem gemockten
 * `createInsuranceClaim` nicht mehr zu sehen.
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

/** Der Ankertag der Tests. Als Eigenschaft an die Komponente, damit kein Test
 *  von der Uhr des Rechners abhaengt. */
const HEUTE = new Date(2026, 7, 14);

interface Aufruf {
  method: string;
  pfad: string;
  suche: string;
  body: Record<string, unknown> | null;
}

let aufrufe: Aufruf[] = [];

function antwort(daten: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(daten) });
}

/** Fehlerantwort ueber `text()`, weil `apiFetch` den Koerper so liest - nur so
 *  entsteht die maschinenlesbare Kennung in `ApiError.detail`. */
function fehler(status: number, detail: string) {
  return Promise.resolve({
    ok: false,
    status,
    text: () => Promise.resolve(JSON.stringify({ detail })),
    json: () => Promise.resolve({ detail }),
  });
}

/** Betrag als Muster: `Intl` setzt zwischen Zahl und Waehrungszeichen ein
 *  geschuetztes Leerzeichen, das in einem Zeichenkettenvergleich stolpert. */
function betrag(minor: number): RegExp {
  const text = formatMinorUnitsAsEuro(minor);
  return new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s/g, "\\s"));
}

// ---------------------------------------------------------------------------
// Bestandsdaten
// ---------------------------------------------------------------------------

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
    is_health_scoped: false,
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

/** Die gesundheitsnahe Police. Beide Merkmale gesetzt, weil `isHealthScoped`
 *  auf die Spalte sieht und ersatzweise auf das Risikofeld. */
function pkvPolice(): InsurancePolicy {
  return police({
    id: "p-pkv",
    line_key: "pkv",
    line_label: "Private Krankenversicherung",
    risk_field: "health",
    insurer_name: "Barmenia",
    is_health_scoped: true,
  });
}

function zweck(): PolicyPurpose {
  return {
    purpose_long: "Ersetzt Einrichtung, Kleidung und Wertsachen nach Feuer oder Einbruch.",
    typically_covers: ["Feuer", "Leitungswasser"],
    typically_not_covers: ["Grobe Fahrlässigkeit"],
    coverage_source: "catalog",
    purpose_note: null,
    source_url: null,
    source_checked_on: null,
    caveat: "Ihre konkrete Police kann abweichen.",
  };
}

let ereignisZaehler = 0;

function ereignis(toStatus: ClaimStatus, occurredOn: string): ClaimEvent {
  ereignisZaehler += 1;
  return {
    id: ereignisZaehler,
    claim_id: "claim-1",
    from_status: null,
    to_status: toStatus,
    occurred_on: occurredOn,
    payload: {},
    created_at: `${occurredOn}T09:00:00Z`,
  };
}

function zahlung(overrides: Partial<ClaimPayment> = {}): ClaimPayment {
  return {
    id: "pay-1",
    claim_id: "claim-1",
    transaction_id: "txn-1",
    direction: "inbound",
    kind: "settlement",
    amount_minor: 120000,
    booked_on: "2026-07-20",
    note: null,
    created_at: "2026-07-20T09:00:00Z",
    payee_label: "Allianz Versicherungs-AG",
    transaction_amount_minor: 120000,
    ...overrides,
  };
}

function vorgang(overrides: Partial<InsuranceClaimDetail> = {}): InsuranceClaimDetail {
  return {
    id: "claim-1",
    policy_id: "p-hausrat",
    insured_object_id: null,
    title: "Leitungswasser Bad",
    claim_number: "SCH-2026-4711",
    occurred_on: "2026-06-12",
    reported_on: "2026-06-13",
    location: "Badezimmer",
    description: null,
    police_reference: null,
    status: "in_review",
    decision: null,
    decided_on: null,
    reduction_reason: null,
    claimed_minor: 480000,
    approved_minor: null,
    deductible_minor: 15000,
    settled_minor: 0,
    paid_to_third_party: false,
    next_action: null,
    next_action_due: null,
    termination_basis: "vvg_92",
    negotiations_closed_on: null,
    negotiations_closed_reason: null,
    negotiations_closed_source: null,
    negotiations_closed_transaction_id: null,
    negotiations_closed_certainty: "unbekannt",
    special_termination_state: "open_undetermined",
    special_termination_until: null,
    termination_effect_options: ["immediate", "end_of_period"],
    version: 1,
    created_at: "2026-06-13T09:00:00Z",
    updated_at: "2026-06-13T09:00:00Z",
    events: [ereignis("reported", "2026-06-13"), ereignis("in_review", "2026-06-20")],
    payments: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Gefaelschter Dienst mit Gedaechtnis
// ---------------------------------------------------------------------------

function dienstStarten(
  bestand: InsuranceClaimDetail[] = [],
  optionen: { anlageFehler?: string } = {},
) {
  const vorgaenge = new Map(bestand.map((eintrag) => [eintrag.id, eintrag]));

  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const roh = typeof input === "string" ? input : input.toString();
    const url = new URL(roh, "http://test.local");
    const method = (init?.method ?? "GET").toUpperCase();
    const body =
      typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
    aufrufe.push({ method, pfad: url.pathname, suche: url.search, body });

    if (url.pathname === "/v1/insurance/claims" && method === "GET") {
      const policyId = url.searchParams.get("policy_id");
      const liste = [...vorgaenge.values()]
        .filter((eintrag) => !policyId || eintrag.policy_id === policyId)
        // Die Liste des Dienstes traegt weder Journal noch Zahlungen - die
        // Reihenfolge muss die Oberflaeche selbst herstellen.
        .map(({ events: _events, payments: _payments, ...rest }) => rest);
      return antwort(liste);
    }

    if (url.pathname === "/v1/insurance/claims" && method === "POST") {
      if (optionen.anlageFehler) return fehler(422, optionen.anlageFehler);
      const neu = vorgang({
        ...(body as Partial<InsuranceClaimDetail>),
        id: "claim-neu",
        status: "draft",
        special_termination_state: "none",
        events: [],
        payments: [],
      });
      vorgaenge.set(neu.id, neu);
      return antwort(neu);
    }

    const kandidaten = url.pathname.match(
      /^\/v1\/insurance\/claims\/([^/]+)\/transaction-candidates$/,
    );
    if (kandidaten) return antwort([]);

    const statuswechsel = url.pathname.match(/^\/v1\/insurance\/claims\/([^/]+)\/status$/);
    if (statuswechsel && method === "POST") {
      const vorher = vorgaenge.get(statuswechsel[1] as string);
      if (vorher && body) {
        const nachher: InsuranceClaimDetail = {
          ...vorher,
          status: body.status as ClaimStatus,
          decision: (body.decision as InsuranceClaimDetail["decision"]) ?? vorher.decision,
          decided_on: (body.decided_on as string | null) ?? vorher.decided_on,
          reduction_reason: (body.reduction_reason as string | null) ?? vorher.reduction_reason,
          events: [
            ...vorher.events,
            ereignis(body.status as ClaimStatus, body.occurred_on as string),
          ],
        };
        vorgaenge.set(nachher.id, nachher);
        return antwort(nachher);
      }
    }

    const abschluss = url.pathname.match(/^\/v1\/insurance\/claims\/([^/]+)\/close-negotiations$/);
    if (abschluss && method === "POST") {
      const vorher = vorgaenge.get(abschluss[1] as string);
      if (vorher && body) {
        const nachher: InsuranceClaimDetail = {
          ...vorher,
          negotiations_closed_on: body.negotiations_closed_on as string,
          negotiations_closed_reason:
            body.reason as InsuranceClaimDetail["negotiations_closed_reason"],
          negotiations_closed_transaction_id: (body.transaction_id as string | null) ?? null,
          negotiations_closed_certainty: body.transaction_id ? "belegt" : "geschaetzt",
          special_termination_state: "open_until",
        };
        vorgaenge.set(nachher.id, nachher);
        return antwort(nachher);
      }
    }

    const einzeln = url.pathname.match(/^\/v1\/insurance\/claims\/([^/]+)$/);
    if (einzeln && method === "GET") {
      return antwort(vorgaenge.get(einzeln[1] as string) ?? null);
    }

    return antwort([]);
  });

  vi.stubGlobal("fetch", fetchMock);
}

// ---------------------------------------------------------------------------
// Ansichten
// ---------------------------------------------------------------------------

function zeigeRegister(policy = police(), canEdit = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ClaimTable policy={policy} canEdit={canEdit} today={HEUTE} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

function zeigeDetail(policy: InsurancePolicy) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PolicyDetail
          policy={policy}
          purpose={zweck()}
          purposeLoading={false}
          purposeError={null}
          lines={[]}
          canEdit
          busy={false}
          error={null}
          onClose={vi.fn()}
          onConfirmMainDue={vi.fn()}
          onConfirmNoticeTerms={vi.fn()}
          onConfirmLine={vi.fn()}
          today={HEUTE}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Die Zeile eines Vorgangs in der Liste. */
function zeile(titel: string): HTMLElement {
  const treffer = screen.getByText(titel).closest("tr");
  if (!treffer) throw new Error(`Keine Zeile zu „${titel}“ gefunden.`);
  return treffer as HTMLElement;
}

async function vorgangOeffnen(titel: string) {
  await userEvent.click(within(zeile(titel)).getByRole("button", { name: "Vorgang öffnen" }));
  return screen.findByRole("region", { name: "Stand des Vorgangs" });
}

interface Stufe {
  label: string;
  datum: string;
  notiz: string | null;
  zustand: "done" | "open" | "cancelled";
}

/** Der Zeitstrahl als Liste - Beschriftung, Datumszeile und Zustand je Stufe. */
function stufen(): Stufe[] {
  const bereich = screen.getByRole("region", { name: "Stand des Vorgangs" });
  return within(bereich)
    .getAllByRole("listitem")
    .map((eintrag) => ({
      label: eintrag.querySelector(".insurance-claim-stage-label")?.textContent ?? "",
      datum: eintrag.querySelector(".insurance-claim-stage-date")?.textContent ?? "",
      notiz: eintrag.querySelector(".insurance-claim-stage-note")?.textContent ?? null,
      zustand: eintrag.className.includes("--done")
        ? "done"
        : eintrag.className.includes("--cancelled")
          ? "cancelled"
          : "open",
    }));
}

/** Ein Feld des Sonderkuendigungsbandes samt seinem Inhalt. */
function bandFeld(bezeichnung: string): HTMLElement {
  const band = screen.getByRole("note");
  const bezeichner = within(band).getByText(bezeichnung);
  const wert = bezeichner.nextElementSibling;
  if (!wert) throw new Error(`Zum Feld „${bezeichnung}“ steht kein Wert.`);
  return wert as HTMLElement;
}

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
  ereignisZaehler = 0;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------

describe("Schadenregister – wo ein Vorgang hingehört", () => {
  it("zeigt an einer gesundheitsnahen Police kein Register, sondern den Weg zur Erstattungsseite", () => {
    dienstStarten([vorgang()]);
    zeigeDetail(pkvPolice());

    expect(screen.queryByRole("tab", { name: "Schäden" })).not.toBeInTheDocument();

    const verweis = screen.getByRole("region", { name: "Schäden und Leistungsfälle" });
    expect(within(verweis).getByText(/Diese Police ist gesundheitsnah/)).toBeInTheDocument();
    expect(within(verweis).getByRole("link", { name: /Zu den Erstattungen/ })).toHaveAttribute(
      "href",
      "/erstattungen",
    );

    // Und es wird auch nichts geladen: ein Bestand, den es fachlich nicht gibt,
    // wird nicht abgefragt.
    expect(aufrufe.filter((aufruf) => aufruf.pfad.includes("/claims"))).toHaveLength(0);
  });

  it("zeigt an einer Sachpolice das Register und lädt seinen Bestand erst beim Öffnen", async () => {
    dienstStarten([vorgang()]);
    zeigeDetail(police());

    expect(
      screen.queryByRole("region", { name: "Schäden und Leistungsfälle" }),
    ).not.toBeInTheDocument();
    // Solange die Übersicht offen ist, fragt niemand nach Vorgängen.
    expect(aufrufe.filter((aufruf) => aufruf.pfad.includes("/claims"))).toHaveLength(0);

    await userEvent.click(screen.getByRole("tab", { name: "Schäden" }));

    expect(await screen.findByText("Leitungswasser Bad")).toBeInTheDocument();
    expect(letzterAufruf("GET", "/v1/insurance/claims").suche).toContain("policy_id=p-hausrat");
  });

  it("verweist auf die Erstattungen, wenn der Server einen Vorgang als gesundheitsnah abweist", async () => {
    // Der Trigger aus Leitsatz 2 ist die letzte Instanz - auch dort antwortet
    // die Oberfläche mit dem Weg und nicht mit der Rohkennung.
    dienstStarten([], { anlageFehler: "insurance_claim_health_scoped" });
    zeigeRegister();

    await userEvent.click(await screen.findByRole("button", { name: "Vorgang erfassen" }));
    await userEvent.type(screen.getByLabelText("Was ist passiert"), "Zahnersatz");
    await userEvent.click(screen.getByRole("button", { name: "Vorgang anlegen" }));

    expect(await screen.findByRole("link", { name: "Zu den Erstattungen" })).toHaveAttribute(
      "href",
      "/erstattungen",
    );
    expect(screen.queryByText(/insurance_claim_health_scoped/)).not.toBeInTheDocument();
  });
});

describe("ClaimTable – die Liste der Vorgänge", () => {
  it("stellt offene Vorgänge voran und nennt gefordert und reguliert nebeneinander", async () => {
    dienstStarten([
      vorgang({
        id: "claim-alt",
        title: "Fahrraddiebstahl",
        occurred_on: "2026-01-08",
        status: "closed",
        claimed_minor: 90000,
        settled_minor: 90000,
        special_termination_state: "expired",
      }),
      vorgang({
        title: "Leitungswasser Bad",
        occurred_on: "2026-06-12",
        claimed_minor: 480000,
        settled_minor: 120000,
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    const zeilen = screen.getAllByRole("row").slice(1);
    expect(within(zeilen[0] as HTMLElement).getByText("Leitungswasser Bad")).toBeInTheDocument();
    expect(within(zeilen[1] as HTMLElement).getByText("Fahrraddiebstahl")).toBeInTheDocument();

    const offen = zeile("Leitungswasser Bad");
    expect(within(offen).getByText(betrag(480000))).toBeInTheDocument();
    expect(within(offen).getByText(betrag(120000))).toBeInTheDocument();
    // Was noch aussteht, steht als eigener Hinweis daneben - nicht als dritter
    // Betrag in einer eigenen Spalte. (Der Anfang der Zeichenkette grenzt ihn
    // gegen den Streifen "Sonderkündigung offen" derselben Zeile ab.)
    expect(within(offen).getByText(/^offen\s/)).toHaveTextContent(betrag(360000));
  });

  it("markiert ein laufendes Sonderkündigungsfenster schon in der Liste", async () => {
    dienstStarten([
      vorgang({ special_termination_state: "open_undetermined" }),
      vorgang({
        id: "claim-alt",
        title: "Fahrraddiebstahl",
        occurred_on: "2026-01-08",
        status: "closed",
        special_termination_state: "expired",
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    expect(
      within(zeile("Leitungswasser Bad")).getByText("Sonderkündigung offen"),
    ).toBeInTheDocument();
    // Ein verfallenes Fenster ist kein laufendes.
    expect(
      within(zeile("Fahrraddiebstahl")).queryByText("Sonderkündigung offen"),
    ).not.toBeInTheDocument();
  });

  it("sagt bei leerem Bestand, wofür sich ein Vorgang lohnt", async () => {
    dienstStarten([]);
    zeigeRegister();

    expect(
      await screen.findByText(/An dieser Police ist kein Vorgang erfasst/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("legt einen Vorgang mit vier Angaben an und belegt den Schadentag mit heute", async () => {
    dienstStarten([]);
    zeigeRegister();

    await userEvent.click(await screen.findByRole("button", { name: "Vorgang erfassen" }));
    expect(screen.getByLabelText("Schadentag")).toHaveValue("2026-08-14");

    await userEvent.type(screen.getByLabelText("Was ist passiert"), "Sturmschaden Dach");
    fireEvent.change(screen.getByLabelText("Schadentag"), { target: { value: "2026-07-02" } });
    await userEvent.type(screen.getByLabelText("Gefordert (optional)"), "1.250,50");
    await userEvent.click(screen.getByRole("button", { name: "Vorgang anlegen" }));

    await waitFor(() => expect(letzterAufruf("POST", "/v1/insurance/claims")).toBeTruthy());
    expect(letzterAufruf("POST", "/v1/insurance/claims").body).toEqual({
      policy_id: "p-hausrat",
      title: "Sturmschaden Dach",
      occurred_on: "2026-07-02",
      reported_on: null,
      claim_number: null,
      claimed_minor: 125050,
      location: null,
    });
  });

  it("nimmt keine Meldung entgegen, die vor dem Schaden liegt", async () => {
    dienstStarten([]);
    zeigeRegister();

    await userEvent.click(await screen.findByRole("button", { name: "Vorgang erfassen" }));
    await userEvent.type(screen.getByLabelText("Was ist passiert"), "Sturmschaden Dach");
    fireEvent.change(screen.getByLabelText("Schadentag"), { target: { value: "2026-07-02" } });
    fireEvent.change(screen.getByLabelText("Gemeldet am (optional)"), {
      target: { value: "2026-06-30" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Vorgang anlegen" }));

    expect(
      await screen.findByText("Gemeldet werden kann erst, nachdem der Schaden eingetreten ist."),
    ).toBeInTheDocument();
    expect(aufrufe.filter((aufruf) => aufruf.method === "POST")).toHaveLength(0);
  });

  it("verlangt eine Bezeichnung, bevor es etwas schickt", async () => {
    dienstStarten([]);
    zeigeRegister();

    await userEvent.click(await screen.findByRole("button", { name: "Vorgang erfassen" }));
    await userEvent.click(screen.getByRole("button", { name: "Vorgang anlegen" }));

    expect(
      await screen.findByText(
        "Ohne eine kurze Bezeichnung ist der Vorgang später nicht wiederzuerkennen.",
      ),
    ).toBeInTheDocument();
    expect(aufrufe.filter((aufruf) => aufruf.method === "POST")).toHaveLength(0);
  });

  it("bietet ohne Schreibrecht kein Anlageformular an", async () => {
    dienstStarten([vorgang()]);
    zeigeRegister(police(), false);

    await screen.findByText("Leitungswasser Bad");
    expect(screen.queryByRole("button", { name: "Vorgang erfassen" })).not.toBeInTheDocument();
  });
});

describe("Zeitstrahl – offene Stufen sehen offen aus", () => {
  it("zeigt alle vier Stufen und die noch nicht erreichten als „steht aus“", async () => {
    dienstStarten([vorgang()]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    const gelesen = stufen();
    expect(gelesen).toHaveLength(4);
    expect(gelesen[0]).toMatchObject({
      label: "gemeldet",
      datum: "13.06.2026",
      zustand: "done",
    });
    expect(gelesen[1]).toMatchObject({
      label: "in Bearbeitung",
      datum: "20.06.2026",
      zustand: "done",
    });
    // Die beiden offenen Stufen stehen da - mit Wort, nicht als leere Zeile.
    expect(gelesen[2]).toMatchObject({
      label: "reguliert oder abgelehnt",
      datum: "steht aus",
      zustand: "open",
    });
    expect(gelesen[3]).toMatchObject({ label: "ausgezahlt", datum: "steht aus", zustand: "open" });
  });

  it("liest die Daten aus dem Journal und nicht aus dem Stand allein", async () => {
    // Das Journal sagt, WANN der Stand erreicht wurde; `reported_on` ist eine
    // fachliche Angabe, die jederzeit korrigiert wird.
    dienstStarten([
      vorgang({
        reported_on: "2026-06-30",
        events: [ereignis("reported", "2026-06-13"), ereignis("in_review", "2026-06-20")],
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(stufen()[0]?.datum).toBe("13.06.2026");
  });

  it("hält eine erreichte Stufe ohne Datum erreicht, statt sie zurückzustufen", async () => {
    dienstStarten([vorgang({ reported_on: null, events: [] })]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(stufen()[0]).toMatchObject({
      label: "gemeldet",
      datum: "erreicht, Datum nicht erfasst",
      zustand: "done",
      notiz: "Meldedatum nicht erfasst",
    });
  });

  it("lässt die Stufen nach einer Rücknahme entfallen statt sie offen zu halten", async () => {
    dienstStarten([
      vorgang({
        status: "withdrawn",
        events: [ereignis("reported", "2026-06-13"), ereignis("withdrawn", "2026-06-25")],
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    const gelesen = stufen();
    expect(gelesen[0]).toMatchObject({ label: "gemeldet", zustand: "done" });
    expect(gelesen[1]?.zustand).toBe("cancelled");
    expect(gelesen[1]?.datum).toContain("entfällt");
    expect(gelesen[3]?.zustand).toBe("cancelled");
    expect(gelesen[3]?.datum).toContain("25.06.2026");
  });

  it("hält die Auszahlung offen, solange nur ein Abschlag geflossen ist", async () => {
    dienstStarten([
      vorgang({
        status: "payment_expected",
        decision: "accepted_full",
        decided_on: "2026-07-01",
        approved_minor: 400000,
        settled_minor: 150000,
        events: [
          ereignis("reported", "2026-06-13"),
          ereignis("in_review", "2026-06-20"),
          ereignis("decided", "2026-07-01"),
        ],
        payments: [zahlung({ kind: "advance", amount_minor: 150000, booked_on: "2026-07-10" })],
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    const auszahlung = stufen()[3];
    expect(auszahlung?.zustand).toBe("open");
    expect(auszahlung?.datum).toBe("10.07.2026");
    expect(auszahlung?.notiz).toBe("Ein Teilbetrag ist eingegangen, der Rest steht aus.");
  });

  it("meldet an einem abgelehnten, abgeschlossenen Vorgang keine Auszahlung", async () => {
    // Der Abschluss ist KEIN Beleg dafuer, dass gezahlt wurde: `closed` steht
    // auch am Ende einer Ablehnung und trug damit denselben Rang wie `paid`.
    // Die Stufe stand deshalb als „erreicht, Datum nicht erfasst“ da - eine
    // Auszahlung, die es nie gab, samt Ausrede fuer das fehlende Datum.
    dienstStarten([
      vorgang({
        status: "closed",
        decision: "rejected",
        decided_on: "2026-07-01",
        approved_minor: 0,
        settled_minor: 0,
        payments: [],
        events: [
          ereignis("reported", "2026-06-13"),
          ereignis("in_review", "2026-06-20"),
          ereignis("decided", "2026-07-01"),
          ereignis("closed", "2026-07-02"),
        ],
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    const gelesen = stufen();
    expect(gelesen[2]).toMatchObject({ label: "abgelehnt", zustand: "done" });
    expect(gelesen[3]?.zustand).toBe("cancelled");
    expect(gelesen[3]?.datum).toBe("entfällt – Vorgang abgelehnt");
    // Und die Ruecknahme behaelt ihren eigenen Grund - nicht den der Ablehnung.
    expect(gelesen[3]?.datum).not.toContain("zurückgezogen");
  });

  it("hält die Auszahlung eines abgeschlossenen Vorgangs mit Geld erreicht", async () => {
    // Die Gegenprobe: geschlossen UND reguliert bleibt „ausgezahlt“ - der Fix
    // gegen die Ablehnung darf den Regelfall nicht mitnehmen.
    dienstStarten([
      vorgang({
        status: "closed",
        decision: "accepted_full",
        decided_on: "2026-07-01",
        approved_minor: 400000,
        settled_minor: 400000,
        payments: [zahlung({ amount_minor: 400000, booked_on: "2026-07-10" })],
        events: [ereignis("reported", "2026-06-13"), ereignis("decided", "2026-07-01")],
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(stufen()[3]).toMatchObject({
      label: "ausgezahlt",
      datum: "10.07.2026",
      zustand: "done",
    });
  });
});

describe("Sonderkündigungsband – der fehlende Anker ist ein Satz", () => {
  it("nennt statt eines leeren Fristfeldes, was fehlt und was daraus folgt", async () => {
    dienstStarten([
      vorgang({
        termination_basis: "vvg_92",
        special_termination_state: "open_undetermined",
        special_termination_until: null,
        negotiations_closed_on: null,
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(screen.getByText("Sonderkündigungsrecht besteht")).toBeInTheDocument();
    const fristende = bandFeld("Fristende");
    expect(fristende).toHaveTextContent(CLAIM_ANCHOR_MISSING_TEXT);
    expect(fristende.textContent?.trim()).not.toBe("");

    // Und der Satz nennt den Anker der Sparte: den Abschluss der
    // Verhandlungen, nicht die Entscheidung des Versicherers.
    expect(
      screen.getByText(/Sonderkündigungsrecht besteht seit dem Schadentag \(12\.06\.2026\)/),
    ).toHaveTextContent("dem Abschluss der Verhandlungen über die Entschädigung");
    expect(screen.getByText(/Sonderkündigungsrecht besteht seit dem Schadentag/)).toHaveTextContent(
      "dieses Datum steht noch nicht fest",
    );
  });

  it("nennt an einer Haftpflichtpolice den Anker des § 111 VVG", async () => {
    dienstStarten([
      vorgang({
        termination_basis: "vvg_111",
        special_termination_state: "open_undetermined",
        special_termination_until: null,
      }),
    ]);
    zeigeRegister(police({ line_key: "haftpflicht", line_label: "Privathaftpflicht" }));

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(bandFeld("Fristende")).toHaveTextContent(CLAIM_ANCHOR_MISSING_TEXT);
    expect(screen.getByText(/Sonderkündigungsrecht besteht seit dem Schadentag/)).toHaveTextContent(
      "der Anerkennung oder Ablehnung des Freistellungsanspruchs oder der Rechtskraft des Urteils",
    );
  });

  it("übernimmt die Tageszahl aus dem Rechenergebnis des Dienstes", async () => {
    dienstStarten([
      vorgang({
        special_termination_state: "open_until",
        special_termination_until: "2026-09-15",
        negotiations_closed_on: "2026-08-15",
        negotiations_closed_reason: "full_payment",
        negotiations_closed_certainty: "belegt",
        special_termination: {
          state: "open_until",
          deadline_kind: "kuendigungsfrist",
          deadline_legal_on: "2026-09-15",
          safe_service_on: "2026-09-12",
          recommended_action_on: "2026-09-08",
          days_left: 25,
          basis: "§ 92 VVG",
          basis_kind: "statute",
        },
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(bandFeld("Empfohlen absenden bis")).toHaveTextContent("08.09.2026");
    expect(bandFeld("Empfohlen absenden bis")).toHaveTextContent("noch 25 Tage");
    expect(bandFeld("Rechtliches Fristende")).toHaveTextContent("15.09.2026");
    expect(bandFeld("Kündigung wirkt")).toHaveTextContent(
      "sofort oder zum Schluss der laufenden Versicherungsperiode",
    );
    // Herkunft des Ankers - ein Datum ohne sie wäre eine Behauptung.
    expect(screen.getByText(/Gerechnet ab dem 15\.08\.2026/)).toHaveTextContent(
      "belegt durch einen zugeordneten Kontoumsatz",
    );
  });

  it("zeigt ohne Rechenergebnis das Datum ohne Tageszahl statt einer zweiten Rechnung", async () => {
    dienstStarten([
      vorgang({
        special_termination_state: "open_until",
        special_termination_until: "2026-09-15",
        negotiations_closed_on: "2026-08-15",
        negotiations_closed_reason: "settlement_letter",
        negotiations_closed_certainty: "geschaetzt",
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(bandFeld("Empfohlen absenden bis")).toHaveTextContent("15.09.2026");
    expect(screen.queryByText(/noch \d+ Tage/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^heute$/)).not.toBeInTheDocument();
    expect(screen.getByText(/Voraussichtlich, gerechnet ab dem 15\.08\.2026/)).toHaveTextContent(
      "dieser Anker wurde von Ihnen gesetzt und ist nicht belegt",
    );
  });

  it("blendet ein verfallenes Fenster nicht aus", async () => {
    dienstStarten([
      vorgang({
        status: "closed",
        special_termination_state: "expired",
        special_termination_until: "2026-07-15",
        negotiations_closed_on: "2026-06-15",
        negotiations_closed_reason: "final_rejection",
        negotiations_closed_certainty: "abgeleitet",
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(screen.getByText("Sonderkündigungsrecht ist verfallen")).toBeInTheDocument();
    expect(bandFeld("Fenster endete am")).toHaveTextContent("15.07.2026");
    expect(screen.getByText(/Das Fenster bleibt sichtbar/)).toBeInTheDocument();
  });

  it("deutet an einer Sparte ohne Sonderrecht kein Fenster an", async () => {
    dienstStarten([vorgang({ termination_basis: "none", special_termination_state: "none" })]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(screen.getByRole("note")).toHaveTextContent(
      "ist kein Sonderkündigungsrecht nach einem Schaden hinterlegt",
    );
    // Ohne Sonderrecht gibt es auch kein Ankerformular.
    expect(screen.queryByRole("button", { name: "Abschluss eintragen" })).not.toBeInTheDocument();
  });
});

describe("Vorgangsaktionen – was der Statuswechsel aussagt", () => {
  it("schickt Entscheidung und Kürzungsgrund nur beim Übergang nach „entschieden“", async () => {
    dienstStarten([vorgang()]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    await userEvent.selectOptions(screen.getByLabelText("Neuer Stand"), "decided");
    await userEvent.selectOptions(
      screen.getByLabelText("Wie ist entschieden worden"),
      "accepted_partial",
    );
    await userEvent.type(screen.getByLabelText("Grund der Kürzung"), "Zeitwert statt Neuwert");
    fireEvent.change(screen.getByLabelText("An welchem Tag"), { target: { value: "2026-07-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Stand speichern" }));

    await waitFor(() => expect(letzterAufruf("POST", "/status")).toBeTruthy());
    expect(letzterAufruf("POST", "/status").body).toEqual({
      status: "decided",
      occurred_on: "2026-07-01",
      decision: "accepted_partial",
      decided_on: "2026-07-01",
      reduction_reason: "Zeitwert statt Neuwert",
    });
  });

  it("löscht bei einem anderen Wechsel keine getroffene Entscheidung", async () => {
    dienstStarten([
      vorgang({
        status: "decided",
        decision: "accepted_full",
        decided_on: "2026-07-01",
        approved_minor: 400000,
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    await userEvent.selectOptions(screen.getByLabelText("Neuer Stand"), "paid");
    fireEvent.change(screen.getByLabelText("An welchem Tag"), { target: { value: "2026-07-20" } });
    await userEvent.click(screen.getByRole("button", { name: "Stand speichern" }));

    await waitFor(() => expect(letzterAufruf("POST", "/status")).toBeTruthy());
    // Kein `decision: null` - der Vorgang verlöre sonst seine Historie, weil
    // jemand ihn auf „ausgezahlt“ gesetzt hat.
    expect(letzterAufruf("POST", "/status").body).toEqual({
      status: "paid",
      occurred_on: "2026-07-20",
    });
  });

  it("weist einen Stand vor dem Schadentag ab", async () => {
    dienstStarten([vorgang()]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    await userEvent.selectOptions(screen.getByLabelText("Neuer Stand"), "reported");
    fireEvent.change(screen.getByLabelText("An welchem Tag"), { target: { value: "2026-06-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Stand speichern" }));

    expect(
      await screen.findByText("Ein Stand kann nicht vor dem Schadentag erreicht worden sein."),
    ).toBeInTheDocument();
    expect(aufrufe.filter((aufruf) => aufruf.pfad.includes("/status"))).toHaveLength(0);
  });

  it("bietet an einer Haftpflichtpolice keinen Grund an, den § 111 VVG nicht kennt", async () => {
    dienstStarten([vorgang({ termination_basis: "vvg_111" })]);
    zeigeRegister(police({ line_key: "haftpflicht", line_label: "Privathaftpflicht" }));

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    const gruende = screen.getByLabelText("Woraus ergibt sich das");
    expect(
      within(gruende).getByRole("option", { name: "Anerkennung des Freistellungsanspruchs" }),
    ).toBeInTheDocument();
    expect(
      within(gruende).queryByRole("option", { name: "vollständige Zahlung" }),
    ).not.toBeInTheDocument();
  });

  it("übernimmt den Beleg des Vorschlags nur, solange Datum und Grund unverändert sind", async () => {
    dienstStarten([
      vorgang({
        status: "paid",
        decision: "accepted_full",
        decided_on: "2026-07-01",
        approved_minor: 400000,
        settled_minor: 400000,
        negotiations_suggestion: {
          negotiations_closed_on: "2026-07-20",
          reason: "full_payment",
          certainty: "belegt",
          source: "transaction",
          transaction_id: "txn-1",
          hint: "letzte zugeordnete Zahlung",
        },
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(screen.getByLabelText("Abgeschlossen am")).toHaveValue("2026-07-20");
    expect(screen.getByText(/Der zugeordnete Kontoumsatz wandert mit/)).toBeInTheDocument();

    // Ein anderes Datum ist keine Buchung mehr, sondern eine Einschätzung.
    fireEvent.change(screen.getByLabelText("Abgeschlossen am"), {
      target: { value: "2026-07-25" },
    });
    expect(screen.getByText(/bleibt das Ankerdatum eine Angabe von Ihnen/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Abschluss eintragen" }));
    await waitFor(() => expect(letzterAufruf("POST", "/close-negotiations")).toBeTruthy());
    expect(letzterAufruf("POST", "/close-negotiations").body).toEqual({
      negotiations_closed_on: "2026-07-25",
      reason: "full_payment",
      transaction_id: null,
    });
  });

  it("schlägt bei einer Kürzung bewusst kein Datum vor", async () => {
    dienstStarten([
      vorgang({
        status: "decided",
        decision: "accepted_partial",
        decided_on: "2026-07-01",
        reduction_reason: "Zeitwert statt Neuwert",
        approved_minor: 300000,
      }),
    ]);
    zeigeRegister();

    await screen.findByText("Leitungswasser Bad");
    await vorgangOeffnen("Leitungswasser Bad");

    expect(screen.getByText(/schlägt das Werkzeug bewusst kein Datum vor/)).toHaveTextContent(
      "die Entscheidung ist hier der Anfang der Verhandlung und nicht ihr Ende",
    );
    // Nicht das Entscheidungsdatum, sondern der Ankertag der Ansicht.
    expect(screen.getByLabelText("Abgeschlossen am")).toHaveValue("2026-08-14");
    expect(screen.getByLabelText("Woraus ergibt sich das")).toHaveValue("");
  });
});
