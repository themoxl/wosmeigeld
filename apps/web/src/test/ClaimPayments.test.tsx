import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ClaimPayments } from "../components/insurance/ClaimPayments";
import { formatMinorUnitsAsEuro } from "../lib/money";
import type {
  ClaimPayment,
  ClaimTransactionCandidate,
  InsuranceClaimDetail,
} from "../lib/insuranceApi";

/**
 * Zahlungen eines Schadenvorgangs (docs/VERSICHERUNGEN-PLAN.md, 4.8; P5).
 *
 * Drei Aussagen tragen diese Ansicht, und alle drei sind leicht zu verfehlen:
 *
 * 1. **Ein Vorschlag ohne Begruendung ist eine Zumutung.** Wer eine Buchung
 *    zuordnen soll, muss sehen, WARUM sie vorgeschlagen wird - sonst bestaetigt
 *    er sie ungeprueft oder gar nicht.
 * 2. **Nicht jedes Geld laeuft ueber das Konto.** Zahlt der Versicherer direkt
 *    an die Werkstatt, gibt es keine Buchung; die Zeile traegt dann kein
 *    `transaction_id` und muss trotzdem erfassbar UND als solche erkennbar
 *    sein. Ohne diesen Fall stuende ein vollstaendig regulierter Kaskoschaden
 *    als unbezahlt da.
 * 3. **Die Richtung haengt an der Art, nicht am Vorzeichen.** Eine
 *    Selbstbeteiligung und eine Rueckforderung sehen auf dem Konto gleich aus
 *    und bedeuten Verschiedenes.
 *
 * Gefaelscht wird `fetch`: geprueft wird, WAS die Oberflaeche schickt - genau
 * das ist an einer gemockten Datenschicht nicht mehr zu sehen.
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

/** Ankertag der Ansicht - als Eigenschaft, damit kein Test von der Uhr des
 *  Rechners abhaengt. */
const HEUTE = new Date(2026, 7, 14);

interface Aufruf {
  method: string;
  pfad: string;
  body: Record<string, unknown> | null;
}

let aufrufe: Aufruf[] = [];

function antwort(daten: unknown, status = 200) {
  return Promise.resolve({ ok: true, status, json: () => Promise.resolve(daten) });
}

/** Betrag als Muster: `Intl` setzt zwischen Zahl und Waehrungszeichen ein
 *  geschuetztes Leerzeichen. */
function betrag(minor: number): RegExp {
  const text = formatMinorUnitsAsEuro(minor);
  return new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s/g, "\\s"));
}

// ---------------------------------------------------------------------------
// Bestandsdaten
// ---------------------------------------------------------------------------

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

function kandidat(overrides: Partial<ClaimTransactionCandidate> = {}): ClaimTransactionCandidate {
  return {
    transaction_id: "txn-7",
    booked_date: "2026-07-18",
    amount_minor: 250000,
    payee_label: "Allianz Versicherungs-AG",
    note: "Schaden SCH-2026-4711",
    score: 92,
    evidence: [
      "Gegenseite entspricht dem Versicherer der Police",
      "Vorgangsnummer steht im Verwendungszweck",
    ],
    remaining_minor: 250000,
    ...overrides,
  };
}

function vorgang(overrides: Partial<InsuranceClaimDetail> = {}): InsuranceClaimDetail {
  return {
    id: "claim-1",
    policy_id: "p-kasko",
    insured_object_id: null,
    title: "Hagelschaden Fahrzeug",
    claim_number: "SCH-2026-4711",
    occurred_on: "2026-06-12",
    reported_on: "2026-06-13",
    location: null,
    description: null,
    police_reference: null,
    status: "payment_expected",
    decision: "accepted_full",
    decided_on: "2026-07-01",
    reduction_reason: null,
    claimed_minor: 480000,
    approved_minor: 300000,
    deductible_minor: 15000,
    settled_minor: 100000,
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
    termination_effect_options: ["immediate"],
    version: 3,
    created_at: "2026-06-13T09:00:00Z",
    updated_at: "2026-07-20T09:00:00Z",
    events: [],
    payments: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Gefaelschter Dienst
// ---------------------------------------------------------------------------

function dienstStarten(kandidaten: ClaimTransactionCandidate[] = []) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const roh = typeof input === "string" ? input : input.toString();
    const url = new URL(roh, "http://test.local");
    const method = (init?.method ?? "GET").toUpperCase();
    const body =
      typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
    aufrufe.push({ method, pfad: url.pathname, body });

    if (url.pathname.endsWith("/transaction-candidates")) {
      return antwort(kandidaten);
    }
    if (url.pathname.endsWith("/payments") && method === "POST") {
      return antwort(zahlung({ id: "pay-neu", ...(body as Partial<ClaimPayment>) }));
    }
    if (method === "DELETE") {
      return antwort(null, 204);
    }
    return antwort([]);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function zeigeZahlungen(claim = vorgang(), canEdit = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ClaimPayments claim={claim} canEdit={canEdit} today={HEUTE} />
    </QueryClientProvider>,
  );
}

/** Die Zeile zu einem Text - Kandidaten wie erfasste Zahlungen. */
function zeile(text: string | RegExp): HTMLElement {
  const treffer = screen.getByText(text).closest("tr");
  if (!treffer) throw new Error(`Keine Zeile zu „${String(text)}“ gefunden.`);
  return treffer as HTMLElement;
}

function letzterAufruf(method: string): Aufruf {
  const letzter = aufrufe.filter((aufruf) => aufruf.method === method).at(-1);
  if (!letzter) throw new Error(`Kein ${method} abgesetzt.`);
  return letzter;
}

beforeEach(() => {
  aufrufe = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------

describe("ClaimPayments – die vorgeschlagenen Kontobewegungen", () => {
  it("nennt zu jedem Kandidaten, warum er vorgeschlagen wird", async () => {
    dienstStarten([
      kandidat(),
      kandidat({
        transaction_id: "txn-8",
        booked_date: "2026-07-25",
        payee_label: "Autohaus Berger",
        amount_minor: 50000,
        remaining_minor: 50000,
        evidence: ["Betrag entspricht der Selbstbeteiligung"],
      }),
    ]);
    zeigeZahlungen();

    expect(
      await screen.findByRole("columnheader", { name: "Warum vorgeschlagen" }),
    ).toBeInTheDocument();

    const begruendungen = within(zeile("Allianz Versicherungs-AG"))
      .getAllByRole("listitem")
      .map((eintrag) => eintrag.textContent);
    expect(begruendungen).toEqual([
      "Gegenseite entspricht dem Versicherer der Police",
      "Vorgangsnummer steht im Verwendungszweck",
    ]);

    expect(
      within(zeile("Autohaus Berger")).getByText("Betrag entspricht der Selbstbeteiligung"),
    ).toBeInTheDocument();
  });

  it("sagt auch dann etwas, wenn der Dienst keine Begründung mitliefert", async () => {
    dienstStarten([kandidat({ evidence: [] })]);
    zeigeZahlungen();

    expect(await screen.findByText("Betrag und Zeitraum passen ungefähr")).toBeInTheDocument();
  });

  it("belegt den Betrag mit dem kleineren von „noch frei“ und „noch offen“", async () => {
    // Anerkannt 3.000,00 €, reguliert 1.000,00 € - offen sind also 2.000,00 €,
    // obwohl die Sammelüberweisung 2.500,00 € frei hat.
    dienstStarten([
      kandidat(),
      kandidat({
        transaction_id: "txn-8",
        payee_label: "Autohaus Berger",
        amount_minor: 50000,
        remaining_minor: 50000,
      }),
    ]);
    zeigeZahlungen();

    await screen.findByText("Allianz Versicherungs-AG");
    expect(within(zeile("Allianz Versicherungs-AG")).getByRole("textbox")).toHaveValue("2000,00");
    // Der kleinere Rest bleibt der Rest der Buchung.
    expect(within(zeile("Autohaus Berger")).getByRole("textbox")).toHaveValue("500,00");
    expect(within(zeile("Allianz Versicherungs-AG")).getByText(betrag(250000))).toBeInTheDocument();
  });

  it("ordnet eine Auszahlung mit Buchungskennung und Buchungstag zu", async () => {
    dienstStarten([kandidat()]);
    zeigeZahlungen();

    await screen.findByText("Allianz Versicherungs-AG");
    await userEvent.click(
      within(zeile("Allianz Versicherungs-AG")).getByRole("button", { name: "Zuordnen" }),
    );

    await waitFor(() => expect(letzterAufruf("POST")).toBeTruthy());
    expect(letzterAufruf("POST").pfad).toBe("/v1/insurance/claims/claim-1/payments");
    expect(letzterAufruf("POST").body).toEqual({
      transaction_id: "txn-7",
      direction: "inbound",
      kind: "settlement",
      amount_minor: 200000,
      // Der Tag der Buchung, nicht der Tag des Zuordnens.
      booked_on: "2026-07-18",
    });
  });

  it("leitet die Richtung aus der Art ab und nicht aus dem Vorzeichen der Buchung", async () => {
    // Selbstbeteiligung und Rückforderung sehen auf dem Konto gleich aus. Die
    // Richtung darf deshalb nicht aus dem Betrag geraten werden.
    dienstStarten([kandidat({ amount_minor: 50000, remaining_minor: 50000 })]);
    zeigeZahlungen();

    await screen.findByText("Allianz Versicherungs-AG");
    const reihe = zeile("Allianz Versicherungs-AG");
    await userEvent.selectOptions(within(reihe).getByRole("combobox"), "deductible");
    await userEvent.click(within(reihe).getByRole("button", { name: "Zuordnen" }));

    await waitFor(() => expect(letzterAufruf("POST")).toBeTruthy());
    expect(letzterAufruf("POST").body).toMatchObject({
      kind: "deductible",
      direction: "outbound",
      amount_minor: 50000,
    });
  });

  it("weist einen Betrag über dem freien Rest ab, statt ihn zu schicken", async () => {
    dienstStarten([kandidat({ remaining_minor: 50000, amount_minor: 50000 })]);
    zeigeZahlungen();

    await screen.findByText("Allianz Versicherungs-AG");
    const reihe = zeile("Allianz Versicherungs-AG");
    const feld = within(reihe).getByRole("textbox");
    await userEvent.clear(feld);
    await userEvent.type(feld, "900,00");

    expect(within(reihe).getByRole("button", { name: "Zuordnen" })).toBeDisabled();
    expect(within(reihe).getByText(/Der Betrag muss über 0 liegen/)).toHaveTextContent(
      betrag(50000),
    );
    expect(aufrufe.filter((aufruf) => aufruf.method === "POST")).toHaveLength(0);
  });

  it("sagt, wenn keine passende Buchung gefunden wurde – und warum das kein Fehler ist", async () => {
    dienstStarten([]);
    zeigeZahlungen();

    expect(await screen.findByText(/Keine offene Buchung gefunden/)).toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Warum vorgeschlagen" }),
    ).not.toBeInTheDocument();
  });
});

describe("ClaimPayments – Geld, das nie über das Konto lief", () => {
  it("erfasst eine Direktzahlung an die Werkstatt ohne Buchungskennung", async () => {
    dienstStarten([]);
    zeigeZahlungen();

    await screen.findByText(/Keine offene Buchung gefunden/);
    // Vorbelegt ist der offene Rest - der häufige Fall ist ein Klick.
    expect(screen.getByLabelText("Betrag")).toHaveValue("2000,00");

    await userEvent.clear(screen.getByLabelText("Betrag"));
    await userEvent.type(screen.getByLabelText("Betrag"), "1.850,00");
    fireEvent.change(screen.getByLabelText("Am"), { target: { value: "2026-07-28" } });
    await userEvent.type(
      screen.getByLabelText("Notiz (optional)"),
      "Direktzahlung an das Autohaus",
    );
    await userEvent.click(screen.getByRole("button", { name: "Zahlung erfassen" }));

    await waitFor(() => expect(letzterAufruf("POST")).toBeTruthy());
    expect(letzterAufruf("POST").body).toEqual({
      // Keine erfundene Buchung: das Geld ist am Haushalt vorbeigeflossen.
      transaction_id: null,
      direction: "inbound",
      kind: "settlement",
      amount_minor: 185000,
      booked_on: "2026-07-28",
      note: "Direktzahlung an das Autohaus",
    });
  });

  it("zieht den vorbelegten Betrag nach, sobald der offene Rest schrumpft", async () => {
    // Das Formular wird nach dem Absenden nicht neu gebaut: stand der volle
    // Rest weiter im Feld, buchte der zweite Klick denselben Betrag ein
    // zweites Mal. `CandidateRow` zieht beim Rendern nach - hier fehlte das.
    dienstStarten([]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const baum = (claim: InsuranceClaimDetail) => (
      <QueryClientProvider client={queryClient}>
        <ClaimPayments claim={claim} canEdit today={HEUTE} />
      </QueryClientProvider>
    );

    const { rerender } = render(baum(vorgang()));
    await screen.findByText(/Keine offene Buchung gefunden/);
    expect(screen.getByLabelText("Betrag")).toHaveValue("2000,00");

    // Der Dienst hat die Direktzahlung verbucht, der Vorgang kommt neu herein.
    rerender(baum(vorgang({ settled_minor: 250000 })));

    expect(screen.getByLabelText("Betrag")).toHaveValue("500,00");
  });

  it("kennzeichnet eine Zahlung ohne eigene Buchung als solche", async () => {
    dienstStarten([]);
    zeigeZahlungen(
      vorgang({
        paid_to_third_party: true,
        payments: [
          zahlung(),
          zahlung({
            id: "pay-2",
            transaction_id: null,
            payee_label: null,
            transaction_amount_minor: null,
            amount_minor: 200000,
            booked_on: "2026-07-28",
            note: "Direktzahlung an das Autohaus",
          }),
        ],
      }),
    );

    const ohneBuchung = zeile("Direktzahlung an das Autohaus");
    expect(within(ohneBuchung).getByText("ohne eigene Buchung")).toBeInTheDocument();
    expect(within(ohneBuchung).getByText(betrag(200000))).toBeInTheDocument();

    // Die zugeordnete Zahlung nennt dagegen die Gegenseite und die ganze Buchung.
    const mitBuchung = zeile("Allianz Versicherungs-AG");
    expect(within(mitBuchung).queryByText("ohne eigene Buchung")).not.toBeInTheDocument();
    expect(within(mitBuchung).getByText(/Buchung gesamt/)).toHaveTextContent(betrag(120000));
  });

  it("nennt die Selbstbeteiligung mit ihrer Richtung", async () => {
    dienstStarten([]);
    zeigeZahlungen(
      vorgang({
        payments: [
          zahlung({
            id: "pay-3",
            kind: "deductible",
            direction: "outbound",
            amount_minor: 15000,
            note: "bar in der Werkstatt beglichen",
          }),
        ],
      }),
    );

    const reihe = zeile("bar in der Werkstatt beglichen");
    expect(within(reihe).getByText("Selbstbeteiligung")).toBeInTheDocument();
    expect(within(reihe).getByText("Ausgang")).toBeInTheDocument();
  });

  it("rechnet den regulierten Betrag nicht aus den Zeilen nach", async () => {
    dienstStarten([]);
    // Die Zeilen ergeben zusammen 3.200,00 €, der Vorgang nennt 1.000,00 €.
    // Maßgeblich ist der Vorgang - eine zweite Summe im Browser wäre die
    // zweite Wahrheit.
    zeigeZahlungen(
      vorgang({
        payments: [
          zahlung({ amount_minor: 120000 }),
          zahlung({ id: "pay-2", amount_minor: 200000, booked_on: "2026-07-28" }),
        ],
      }),
    );

    const satz = screen.getByText(/Reguliert sind laut Vorgang/);
    expect(satz).toHaveTextContent(betrag(100000));
    expect(satz).not.toHaveTextContent(betrag(320000));
    // Und der offene Rest misst sich am anerkannten Betrag, nicht an der Forderung.
    expect(satz).toHaveTextContent("gemessen an dem anerkannten Betrag");
    expect(satz).toHaveTextContent(betrag(200000));
  });

  it("nimmt eine Zuordnung zurück", async () => {
    dienstStarten([]);
    zeigeZahlungen(vorgang({ payments: [zahlung()] }));

    await userEvent.click(
      within(zeile("Allianz Versicherungs-AG")).getByRole("button", {
        name: "Zuordnung entfernen",
      }),
    );

    await waitFor(() => expect(letzterAufruf("DELETE")).toBeTruthy());
    expect(letzterAufruf("DELETE").pfad).toBe("/v1/insurance/claims/claim-1/payments/pay-1");
  });

  it("sagt bei leerer Liste, wie eine Zahlung hierherkommt", async () => {
    dienstStarten([]);
    zeigeZahlungen();

    expect(
      screen.getByText(/Zu diesem Vorgang ist noch keine Zahlung erfasst/),
    ).toBeInTheDocument();
  });
});

describe("ClaimPayments – ohne Schreibrecht", () => {
  it("sucht keine Kandidaten und bietet keine Zuordnung an", async () => {
    dienstStarten([kandidat()]);
    zeigeZahlungen(vorgang({ payments: [zahlung()] }), false);

    // Die Kandidatensuche ist die teuerste Abfrage dieser Ansicht.
    await waitFor(() => expect(screen.getByText("Allianz Versicherungs-AG")).toBeInTheDocument());
    expect(aufrufe).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Zuordnung entfernen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zahlung erfassen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Aktion" })).not.toBeInTheDocument();
  });
});
