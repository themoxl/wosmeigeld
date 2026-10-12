import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CoverageEditor } from "../components/insurance/CoverageEditor";
import { PolicyDetail } from "../components/insurance/PolicyDetail";
import type {
  InsuranceCoverageKey,
  InsurancePolicy,
  PolicyCoverage,
  PolicyCoverageInput,
  PolicyDeadline,
  PolicyPremium,
  PolicyPurpose,
} from "../lib/insuranceApi";

/**
 * Register "Deckung" (Plan 2.3 Punkt 2, 4.6; P4).
 *
 * Geprueft wird die Aussage, die dieses Register kippt: solange kein Baustein
 * erfasst ist, spricht das Register "Zweck" ueber die SPARTE, danach ueber
 * DIESE POLICE. Beides steht an zwei Stellen - als Vorschau im Deckungsregister
 * und als Spaltenueberschrift im Zweck-Register -, deshalb stehen hier beide
 * Komponenten nebeneinander.
 *
 * Der Zugriff laeuft absichtlich ueber ein gefaelschtes `fetch` und nicht ueber
 * eine gemockte Datenschicht: die Frage "zeigt der Katalog nur Bausteine dieser
 * Sparte?" beantwortet der Dienst, und die Oberflaeche kann sie nur richtig
 * beantworten, wenn sie ihn ueberhaupt mit der Sparte fragt. Genau das ist mit
 * einem gemockten `listCoverageKeys` nicht mehr pruefbar.
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
  url: string;
  body: unknown;
}

let aufrufe: Aufruf[] = [];

function antwort(daten: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(daten) });
}

function katalogEintrag(overrides: Partial<InsuranceCoverageKey> = {}): InsuranceCoverageKey {
  return {
    coverage_key: "fahrraddiebstahl",
    label: "Fahrraddiebstahl",
    purpose_short: "Ersetzt das Rad, wenn es aus dem Keller gestohlen wird.",
    applies_to: ["hausrat"],
    sort_order: 10,
    ...overrides,
  };
}

const KATALOG: InsuranceCoverageKey[] = [
  katalogEintrag(),
  katalogEintrag({
    coverage_key: "elementar",
    label: "Elementarschäden",
    purpose_short: "Überschwemmung, Starkregen, Rückstau.",
    applies_to: ["hausrat", "wohngebaeude"],
    sort_order: 20,
  }),
  katalogEintrag({
    coverage_key: "teilkasko",
    label: "Teilkasko",
    purpose_short: "Diebstahl, Glas, Wild.",
    applies_to: ["kfz_kasko"],
    sort_order: 30,
  }),
];

function baustein(overrides: Partial<PolicyCoverage> = {}): PolicyCoverage {
  return {
    id: "cov-1",
    policy_id: "p-hausrat",
    coverage_key: "elementar",
    label: "Elementarschäden",
    purpose_short: "Überschwemmung, Starkregen, Rückstau.",
    is_included: true,
    sum_insured_minor: null,
    deductible_minor: null,
    benefit_rate_bp: null,
    waiting_until: null,
    note: null,
    created_at: "2026-08-01T09:00:00Z",
    updated_at: "2026-08-01T09:00:00Z",
    ...overrides,
  };
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

function beitrag(overrides: Partial<PolicyPremium> = {}): PolicyPremium {
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

function zweck(overrides: Partial<PolicyPurpose> = {}): PolicyPurpose {
  return {
    purpose_long: "Ersetzt Einrichtung, Kleidung und Wertsachen nach Feuer oder Einbruch.",
    typically_covers: ["Feuer", "Leitungswasser"],
    typically_not_covers: ["Grobe Fahrlässigkeit"],
    coverage_source: "catalog",
    purpose_note: null,
    source_url: null,
    source_checked_on: null,
    caveat: "Ihre konkrete Police kann abweichen.",
    ...overrides,
  };
}

/**
 * Gefaelschter Dienst mit Gedaechtnis.
 *
 * Das PUT ersetzt den Bestand und die naechste Abfrage liest ihn zurueck -
 * anders liesse sich der Uebergang von "Katalogaussage" auf "diese Police"
 * nicht pruefen, denn genau dieser Uebergang entsteht erst durch das
 * Nachladen.
 */
function dienstStarten(anfangsbestand: PolicyCoverage[] = []) {
  let bestand = anfangsbestand;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : null;
    aufrufe.push({ method, url, body });

    if (url.includes("/v1/insurance/coverage-keys")) {
      // Der Dienst filtert nach `line_key`. Fragt die Oberflaeche ohne Sparte,
      // bekommt sie nichts - dann faellt hier auf, dass sie sie nicht mitgibt.
      const lineKey = new URL(url, "http://test.local").searchParams.get("line_key");
      return antwort(KATALOG.filter((eintrag) => eintrag.applies_to.includes(lineKey ?? "")));
    }
    if (url.includes("/coverages")) {
      if (method === "PUT") {
        const eingaben = (body as { coverages: PolicyCoverageInput[] }).coverages;
        bestand = eingaben.map((eingabe, index) =>
          baustein({
            id: `cov-neu-${index}`,
            coverage_key: eingabe.coverage_key,
            label:
              KATALOG.find((eintrag) => eintrag.coverage_key === eingabe.coverage_key)?.label ??
              eingabe.coverage_key,
            is_included: eingabe.is_included,
            sum_insured_minor: eingabe.sum_insured_minor ?? null,
            deductible_minor: eingabe.deductible_minor ?? null,
            benefit_rate_bp: eingabe.benefit_rate_bp ?? null,
            waiting_until: eingabe.waiting_until ?? null,
            note: eingabe.note ?? null,
          }),
        );
      }
      return antwort(bestand);
    }
    return antwort([]);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function zeigeEditor(policy = police()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spion = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <CoverageEditor policy={policy} canEdit />
    </QueryClientProvider>,
  );
  return { queryClient, spion };
}

/** Die Zeile eines Bausteins - die Detailfelder tragen in jeder Zeile
 *  dieselbe Beschriftung, deshalb wird immer innerhalb der Zeile gesucht. */
function zeile(label: string): HTMLElement {
  const treffer = screen.getByText(label).closest("li");
  if (!treffer) throw new Error(`Keine Zeile zu „${label}“ gefunden.`);
  return treffer as HTMLElement;
}

function letztesPut(): { coverages: PolicyCoverageInput[] } {
  const put = aufrufe.filter((aufruf) => aufruf.method === "PUT").at(-1);
  if (!put) throw new Error("Es wurde nichts gespeichert.");
  return put.body as { coverages: PolicyCoverageInput[] };
}

beforeEach(() => {
  aufrufe = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CoverageEditor – der Katalog der Sparte", () => {
  it("fragt den Bausteinkatalog mit der Sparte der Police", async () => {
    dienstStarten();
    zeigeEditor();

    await screen.findByText("Fahrraddiebstahl");
    const katalogAufruf = aufrufe.find((aufruf) => aufruf.url.includes("/coverage-keys"));
    expect(katalogAufruf?.url).toContain("line_key=hausrat");
  });

  it("zeigt nur Bausteine, die zu dieser Sparte passen", async () => {
    dienstStarten();
    zeigeEditor();

    expect(await screen.findByText("Fahrraddiebstahl")).toBeInTheDocument();
    expect(screen.getByText("Elementarschäden")).toBeInTheDocument();
    // "Teilkasko" steht im Katalog, aber nur fuer die Kfz-Kasko. An einer
    // Hausratpolice waere er eine Einladung, Unsinn zu erfassen.
    expect(screen.queryByText("Teilkasko")).not.toBeInTheDocument();
  });

  it("laesst einen Baustein, den diese Sparte nicht kennt, stehen und speichert ihn mit", async () => {
    // Nach einem Spartenwechsel steht an der Police ein Baustein, den der neue
    // Katalog nicht fuehrt. Verschwiegen wuerde er beim naechsten Speichern
    // stillschweigend geloescht.
    dienstStarten([
      baustein({
        id: "cov-alt",
        coverage_key: "teilkasko",
        label: "Teilkasko",
        purpose_short: "Diebstahl, Glas, Wild.",
        is_included: true,
      }),
    ]);
    zeigeEditor();

    expect(await screen.findByText("Teilkasko")).toBeInTheDocument();
    expect(
      within(zeile("Teilkasko")).getByText(/steht nicht im Katalog dieser Sparte/),
    ).toBeInTheDocument();

    await userEvent.selectOptions(
      within(zeile("Fahrraddiebstahl")).getByRole("combobox"),
      "included",
    );
    await userEvent.click(screen.getByRole("button", { name: "Bausteine speichern" }));

    await waitFor(() => expect(letztesPut()).toBeTruthy());
    const geschickt = letztesPut().coverages.map((eintrag) => eintrag.coverage_key);
    expect(geschickt).toContain("teilkasko");
    expect(geschickt).toContain("fahrraddiebstahl");
  });
});

describe("CoverageEditor – was das Register „Zweck“ danach sagt", () => {
  it("nennt ohne erfassten Baustein die Katalogaussage der Sparte", async () => {
    dienstStarten();
    zeigeEditor();

    await screen.findByText("Fahrraddiebstahl");
    expect(screen.getByText(/Katalogaussage der Sparte/)).toBeInTheDocument();
    const hinweis = screen.getByRole("note");
    expect(hinweis).toHaveTextContent(/typischerweise/);
    expect(hinweis).toHaveTextContent(/Sobald mindestens ein Baustein erfasst ist/);
    expect(screen.getByText("kein Baustein erfasst")).toBeInTheDocument();
  });

  it("wechselt nach dem ersten erfassten Baustein auf die Aussage über diese Police", async () => {
    dienstStarten();
    zeigeEditor();

    await screen.findByText("Fahrraddiebstahl");
    await userEvent.selectOptions(
      within(zeile("Fahrraddiebstahl")).getByRole("combobox"),
      "included",
    );
    await userEvent.click(screen.getByRole("button", { name: "Bausteine speichern" }));

    expect(await screen.findByText(/1 Baustein erfasst/)).toBeInTheDocument();
    expect(screen.getByText(/spricht über diese Police/)).toBeInTheDocument();
    expect(screen.queryByText(/Katalogaussage der Sparte/)).not.toBeInTheDocument();
  });

  it("laedt den Zwecktext nach dem Speichern neu, damit die Überschrift nachzieht", async () => {
    dienstStarten();
    const { spion } = zeigeEditor();

    await screen.findByText("Fahrraddiebstahl");
    await userEvent.selectOptions(
      within(zeile("Fahrraddiebstahl")).getByRole("combobox"),
      "included",
    );
    await userEvent.click(screen.getByRole("button", { name: "Bausteine speichern" }));

    await waitFor(() =>
      expect(spion).toHaveBeenCalledWith({
        queryKey: ["insurance-policy-purpose", "household-1"],
      }),
    );
  });

  it("sagt vor dem Leeren, dass danach wieder der Katalog spricht", async () => {
    dienstStarten([baustein()]);
    zeigeEditor();

    await screen.findByText("Elementarschäden");
    await userEvent.selectOptions(within(zeile("Elementarschäden")).getByRole("combobox"), "unset");

    expect(screen.getByText(/zeigt danach wieder die Aussage des Katalogs/)).toBeInTheDocument();
  });
});

describe("CoverageEditor – drei Zustände, nicht zwei", () => {
  it("schickt „nicht enthalten“ als erfasste Aussage und lässt „nicht erfasst“ ganz weg", async () => {
    dienstStarten();
    zeigeEditor();

    await screen.findByText("Elementarschäden");
    await userEvent.selectOptions(
      within(zeile("Elementarschäden")).getByRole("combobox"),
      "excluded",
    );
    await userEvent.click(screen.getByRole("button", { name: "Bausteine speichern" }));

    await waitFor(() => expect(letztesPut()).toBeTruthy());
    const geschickt = letztesPut().coverages;
    expect(geschickt).toEqual([
      expect.objectContaining({ coverage_key: "elementar", is_included: false }),
    ]);
    // Der nicht angefasste Baustein taucht NICHT als `is_included: false` auf -
    // sonst stuende "darueber wurde nichts festgestellt" als "ausdruecklich
    // nicht mitversichert" in der Police.
    expect(geschickt.some((eintrag) => eintrag.coverage_key === "fahrraddiebstahl")).toBe(false);
  });

  it("zeigt die Detailfelder nur an einem enthaltenen Baustein", async () => {
    dienstStarten();
    zeigeEditor();

    await screen.findByText("Fahrraddiebstahl");
    const reihe = zeile("Fahrraddiebstahl");
    expect(within(reihe).queryByLabelText("Versicherungssumme")).not.toBeInTheDocument();

    await userEvent.selectOptions(within(reihe).getByRole("combobox"), "excluded");
    expect(within(reihe).queryByLabelText("Versicherungssumme")).not.toBeInTheDocument();
    // Eine Notiz traegt auch der Ausschluss: "nur mit Zusatzvereinbarung".
    expect(within(reihe).getByLabelText("Notiz (optional)")).toBeInTheDocument();

    await userEvent.selectOptions(within(reihe).getByRole("combobox"), "included");
    expect(within(reihe).getByLabelText("Versicherungssumme")).toBeInTheDocument();
    expect(within(reihe).getByLabelText("Wartezeit bis")).toBeInTheDocument();
  });
});

describe("CoverageEditor – Beträge und Quoten", () => {
  it("schickt Summen als Minor Units und den Leistungssatz als Basispunkte", async () => {
    dienstStarten();
    zeigeEditor();

    await screen.findByText("Fahrraddiebstahl");
    const reihe = zeile("Fahrraddiebstahl");
    await userEvent.selectOptions(within(reihe).getByRole("combobox"), "included");
    await userEvent.type(within(reihe).getByLabelText("Versicherungssumme"), "1.500,50");
    await userEvent.type(within(reihe).getByLabelText("Selbstbeteiligung"), "150");
    await userEvent.type(within(reihe).getByLabelText("Leistungssatz in %"), "80");
    await userEvent.click(screen.getByRole("button", { name: "Bausteine speichern" }));

    await waitFor(() => expect(letztesPut()).toBeTruthy());
    expect(letztesPut().coverages[0]).toMatchObject({
      coverage_key: "fahrraddiebstahl",
      is_included: true,
      sum_insured_minor: 150050,
      deductible_minor: 15000,
      benefit_rate_bp: 8000,
    });
  });

  it("meldet einen unlesbaren Betrag mit dem Namen des Bausteins, statt ihn zu schicken", async () => {
    dienstStarten();
    zeigeEditor();

    await screen.findByText("Fahrraddiebstahl");
    const reihe = zeile("Fahrraddiebstahl");
    await userEvent.selectOptions(within(reihe).getByRole("combobox"), "included");
    await userEvent.type(within(reihe).getByLabelText("Versicherungssumme"), "viel");
    await userEvent.click(screen.getByRole("button", { name: "Bausteine speichern" }));

    expect(
      await screen.findByText("Fahrraddiebstahl: Die Versicherungssumme ist kein gültiger Betrag."),
    ).toBeInTheDocument();
    expect(aufrufe.some((aufruf) => aufruf.method === "PUT")).toBe(false);
  });

  it("liest erfasste Beträge als Eingabe zurück, ohne sie zu verändern", async () => {
    dienstStarten([baustein({ sum_insured_minor: 500000, benefit_rate_bp: 8000 })]);
    zeigeEditor();

    await screen.findByText("Elementarschäden");
    const reihe = zeile("Elementarschäden");
    expect(within(reihe).getByLabelText("Versicherungssumme")).toHaveValue("5.000,00");
    expect(within(reihe).getByLabelText("Leistungssatz in %")).toHaveValue("80");

    await userEvent.click(screen.getByRole("button", { name: "Bausteine speichern" }));
    await waitFor(() => expect(letztesPut()).toBeTruthy());
    expect(letztesPut().coverages[0]).toMatchObject({
      sum_insured_minor: 500000,
      benefit_rate_bp: 8000,
    });
  });
});

describe("CoverageEditor – ohne Katalog", () => {
  it("verweist bei offener Sparte auf das Register „Übersicht“, statt eine leere Liste zu zeigen", async () => {
    dienstStarten();
    zeigeEditor(police({ line_key: "unknown", line_label: "Sparte unbekannt" }));

    expect(await screen.findByText(/führt der Katalog keine Bausteine/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Bausteine speichern" })).not.toBeInTheDocument();
  });
});

describe("PolicyDetail – die Überschrift des Zweck-Registers", () => {
  function zeigeDetail(purpose: PolicyPurpose) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <PolicyDetail
            policy={police()}
            purpose={purpose}
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
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  }

  it("spricht ohne erfasste Bausteine über die Sparte", () => {
    dienstStarten();
    zeigeDetail(zweck());

    expect(screen.getByRole("heading", { name: "Typischerweise enthalten" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Typischerweise nicht enthalten" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Ihre konkrete Police kann abweichen.")).toBeInTheDocument();
    expect(screen.queryByText(/Der Vorbehalt oben gilt für sie nicht/)).not.toBeInTheDocument();
  });

  it("spricht mit erfassten Bausteinen über diese Police und nimmt den Vorbehalt für die Spalten zurück", () => {
    dienstStarten();
    zeigeDetail(zweck({ coverage_source: "policy" }));

    expect(
      screen.getByRole("heading", {
        name: "In dieser Police enthalten · laut erfassten Bausteinen",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: "In dieser Police nicht enthalten · laut erfassten Bausteinen",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Typischerweise enthalten" })).toBeNull();
    // Der Vorbehalt bleibt fuer den Zwecktext stehen und wird nur fuer die
    // beiden Spalten ausdruecklich zurueckgenommen.
    expect(screen.getByText("Ihre konkrete Police kann abweichen.")).toBeInTheDocument();
    expect(screen.getByText(/Der Vorbehalt oben gilt für sie nicht/)).toBeInTheDocument();
  });

  it("führt zum Register „Deckung“, das seine Bausteine selbst holt", async () => {
    dienstStarten();
    zeigeDetail(zweck());

    await userEvent.click(screen.getByRole("tab", { name: "Deckung" }));

    expect(await screen.findByText("Fahrraddiebstahl")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Was ist an dieser Police vereinbart?" }),
    ).toBeInTheDocument();
  });
});
