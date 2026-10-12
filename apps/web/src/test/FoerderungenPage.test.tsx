import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { FoerderungenPage } from "../pages/FoerderungenPage";

const authState = vi.hoisted(() => ({
  session: {
    access_token: "test-access-token",
    user: { id: "user-1", email: "test@example.com" },
  },
}));

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session: authState.session } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signInWithPassword: vi.fn(),
      signUp: vi.fn(),
      signOut: vi.fn(),
    },
  },
}));

const meResponse = {
  user_id: "user-1",
  display_name: null,
  language: "de",
  timezone: "Europe/Vienna",
  currency: "EUR",
  households: [{ household_id: "household-1", name: "Testhaushalt", role: "owner" }],
};

const curatedProgram = {
  id: "prog-curated",
  semantic_key: "kfw_124_wohneigentumsprogramm",
  provider_key: "kfw",
  provider_name: "KfW",
  program_code: "124",
  title: "KfW-Wohneigentumsprogramm (124)",
  country_code: "DE",
  scope: "national",
  region_codes: [],
  official_url: "https://www.kfw.de/",
  current_version_id: "ver-curated",
  benefit_type: "loan",
  normalization_status: "rules_complete",
  review_due_at: "2099-11-01T00:00:00Z",
  is_stale: false,
  is_own: false,
  is_active: true,
  source_version: "curated-2026-08-04",
  retrieved_at: "2026-08-04T00:00:00Z",
  source_url: "https://www.kfw.de/",
};

const ownProgram = {
  ...curatedProgram,
  id: "prog-own",
  semantic_key: "bayern_extra",
  provider_name: "BayernLabo",
  title: "Eigenes Landesprogramm",
  scope: "regional",
  region_codes: ["DE-BY"],
  is_own: true,
  // Frischeablauf ueberschritten: die Engine stuft solche Treffer auf "unklar".
  review_due_at: "2020-01-01T00:00:00Z",
  is_stale: true,
  normalization_status: "rules_partial",
};

const ownProgramDetail = {
  ...ownProgram,
  current_version: {
    id: "ver-own",
    source_version: "paket-2026-08-08",
    valid_from: "2026-01-01",
    valid_to: null,
    published_at: null,
    retrieved_at: "2026-08-08T00:00:00Z",
    review_due_at: "2099-01-01T00:00:00Z",
    currency: "EUR",
    benefit_type: "loan",
    applicant_types: ["private_individual"],
    project_types: ["property_purchase"],
    criteria_json: {
      compare: { field: "self_occupied", op: "eq", value: true },
    },
    benefit_json: { type: "loan_fixed", amount_minor: 10_000_000 },
    loan_terms_json: {
      max_term_months: 420,
      max_repayment_free_months: 60,
      nominal_rate_bps_annual: 150,
    },
    combination_rules_json: {},
    manual_checks_json: [],
    source_url: "https://example.test/foerderung",
    license_name: null,
    license_url: null,
    attribution: null,
    normalization_status: "rules_partial",
  },
};

const variantProgram = {
  ...ownProgram,
  id: "prog-variants",
  semantic_key: "kfw_308_jung_kauft_alt",
  provider_name: "KfW",
  title: "Jung kauft Alt – Wohneigentum für Familien (308)",
};

const variantProgramDetail = {
  ...ownProgramDetail,
  ...variantProgram,
  current_version: {
    ...ownProgramDetail.current_version,
    id: "ver-variants",
    loan_terms_json: {
      rate_as_of: "2026-08-09",
      note: "Vier gekoppelte Varianten",
      variants: [
        {
          id: "annuity_7_10",
          label: "Annuität 7–10 Jahre",
          repayment_type: "annuity",
          min_term_months: 84,
          max_term_months: 120,
          fixed_rate_months: 120,
          min_repayment_free_months: 12,
          max_repayment_free_months: 24,
          nominal_rate_bps_annual: 1,
          effective_rate_bps_annual: 1,
        },
        {
          id: "bullet_7_10",
          label: "Endfälliges Darlehen 7–10 Jahre",
          repayment_type: "bullet",
          min_term_months: 84,
          max_term_months: 120,
          fixed_rate_months: 120,
          min_repayment_free_months: 0,
          max_repayment_free_months: 0,
          nominal_rate_bps_annual: 69,
          effective_rate_bps_annual: 69,
        },
      ],
    },
  },
};

const bayernProgram = {
  ...ownProgram,
  id: "prog-bayern",
  semantic_key: "bayernlabo_eigenwohnraumfoerderung",
  title: "Eigenwohnraumförderung Bayern",
  benefit_type: "loan_with_grant",
};

const bayernProgramDetail = {
  ...ownProgramDetail,
  ...bayernProgram,
  current_version: {
    ...ownProgramDetail.current_version,
    id: "ver-bayern",
    benefit_type: "loan_with_grant",
    benefit_json: {
      type: "combined",
      components: [
        { type: "loan_percent_of_costs", rate_bps: 3333 },
        { type: "per_unit", unit_field: "children_count", amount_per_unit_minor: 500_000 },
      ],
    },
    loan_terms_json: {
      rate_as_of: "2026-05-01",
      initial_repayment_rate_bps: 200,
      max_repayment_free_months: 12,
      rate_options: [
        {
          id: "fixed_10_years",
          label: "10 Jahre Zinsbindung",
          fixed_rate_months: 120,
          nominal_rate_bps_annual: 265,
          effective_rate_bps_annual: 272,
        },
        {
          id: "fixed_15_years",
          label: "15 Jahre Zinsbindung",
          fixed_rate_months: 180,
          nominal_rate_bps_annual: 295,
          effective_rate_bps_annual: 303,
        },
      ],
    },
  },
};

const packageMeta = {
  id: "kfw_sanierung",
  name: "KfW Sanierung & Effizienz",
  description: "Förderkredite für die Sanierung.",
  icon: "🏚️",
  publisher: "KfW",
  package_version: "2026-08-08",
  researched_on: "2026-08-08",
  program_count: 2,
  installed_count: 0,
  outdated_count: 0,
};

const packagePayload = {
  format_version: "1.0",
  id: "kfw_sanierung",
  name: "KfW Sanierung & Effizienz",
  description: "Förderkredite für die Sanierung.",
  icon: "🏚️",
  publisher: "KfW",
  package_version: "2026-08-08",
  researched_on: "2026-08-08",
  programs: [],
};

const previewResponse = {
  package_id: "kfw_sanierung",
  package_name: "KfW Sanierung & Effizienz",
  new_count: 1,
  updated_count: 0,
  unchanged_count: 0,
  conflict_count: 0,
  invalid_count: 1,
  items: [
    {
      index: 0,
      semantic_key: "kfw_261_beg_wohngebaeude",
      title: "BEG Wohngebäude Kredit (261)",
      provider_name: "KfW",
      benefit_type: "loan_with_grant",
      status: "new",
      recommended: true,
      changed_fields: [],
      existing_source_version: null,
      existing_retrieved_at: null,
      adopted_by_projects: 0,
      problems: [],
    },
    {
      index: 1,
      semantic_key: "kaputtes_programm",
      title: "Kaputtes Programm",
      provider_name: "KfW",
      benefit_type: "grant",
      status: "invalid",
      recommended: false,
      changed_fields: [],
      existing_source_version: null,
      existing_retrieved_at: null,
      adopted_by_projects: 0,
      problems: ["benefit_json bei leeren Angaben: Unbekannter Foerderbetrag-Typ: quatsch"],
    },
  ],
};

function renderPage(options?: {
  programs?: Array<Record<string, unknown>>;
  requests?: Array<{ url: string; init?: RequestInit }>;
}) {
  const programs = options?.programs ?? [curatedProgram, ownProgram];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      options?.requests?.push({ url, init });
      const ok = (body: unknown) =>
        Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });

      // Reihenfolge: die spezifischeren Pfade zuerst, "/v1/funding/programs" ist
      // ein Praefix von nichts, aber "/packages/" von "/packages".
      if (url.includes("/v1/funding/import/preview")) return ok(previewResponse);
      if (url.includes("/v1/funding/import")) {
        return ok({
          programs_created: 1,
          versions_created: 1,
          unchanged: 0,
          skipped_deselected: 0,
          invalid: 1,
          errors: [],
          run_id: "run-1",
        });
      }
      if (url.includes("/v1/funding/packages/")) return ok(packagePayload);
      if (url.includes("/v1/funding/packages")) return ok([packageMeta]);
      if (url.includes("/v1/funding/catalog-status")) {
        return ok({
          providers: [
            {
              provider_key: "kfw",
              last_run_status: "succeeded",
              last_success_at: "2026-08-04T00:00:00Z",
              last_failure_at: null,
              last_error_message: null,
              age_days: 4,
              programs_new: 0,
              programs_changed: 0,
              programs_retired: 0,
            },
          ],
        });
      }
      if (url.includes("/v1/funding/programs/prog-own/terms")) return ok(ownProgramDetail);
      if (url.includes("/v1/funding/programs/prog-own")) return ok(ownProgramDetail);
      if (url.includes("/v1/funding/programs/prog-variants/terms")) {
        return ok(variantProgramDetail);
      }
      if (url.includes("/v1/funding/programs/prog-variants")) return ok(variantProgramDetail);
      if (url.includes("/v1/funding/programs/prog-bayern/terms")) {
        return ok(bayernProgramDetail);
      }
      if (url.includes("/v1/funding/programs/prog-bayern")) return ok(bayernProgramDetail);
      if (url.includes("/v1/funding/programs")) return ok(programs);
      // Register "Pruefung": Vorhabenliste, Szenarien und Foerderprofil.
      // Reihenfolge beachten: /funding-matches und /funding-check liegen UNTER
      // /scenarios/{id}/ - der allgemeinere Zweig darf nicht zuerst greifen,
      // sonst kommt die Szenarioliste als Treffermenge zurueck.
      if (url.includes("/funding-matches")) return ok(null);
      if (url.includes("/funding-check")) return ok(null);
      if (url.includes("/eligibility-profile")) return ok(null);
      if (url.includes("/scenarios")) {
        return ok([
          { id: "scenario-1", project_id: "project-1", name: "Basis", status: "selected" },
        ]);
      }
      if (url.includes("/v1/projects")) {
        return ok([{ id: "project-1", name: "Hausbau Musterstrasse", project_type: "home_build" }]);
      }
      if (url.includes("/v1/me")) return ok(meResponse);
      return ok([]);
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <FoerderungenPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("FoerderungenPage", () => {
  it("trennt mitgelieferte von eigenen Programmen und markiert veraltete", async () => {
    renderPage();

    expect(await screen.findByText("KfW-Wohneigentumsprogramm (124)")).toBeInTheDocument();
    const ownRow = screen.getByText("Eigenes Landesprogramm").closest("tr");
    expect(ownRow).not.toBeNull();
    expect(within(ownRow as HTMLElement).getByText("Eigener Eintrag")).toBeInTheDocument();
    // Abgelaufener Frischeablauf muss sichtbar sein, nicht nur intern wirken.
    expect(within(ownRow as HTMLElement).getByText("Veraltet")).toBeInTheDocument();

    const curatedRow = screen.getByText("KfW-Wohneigentumsprogramm (124)").closest("tr");
    expect(within(curatedRow as HTMLElement).getByText("Mitgeliefert")).toBeInTheDocument();
    // Mitgelieferte Programme lassen sich nicht deaktivieren.
    expect(
      within(curatedRow as HTMLElement).queryByRole("button", { name: "Deaktivieren" }),
    ).toBeNull();
    expect(
      within(ownRow as HTMLElement).getByRole("button", { name: "Deaktivieren" }),
    ).toBeInTheDocument();
  });

  it("zeigt den Recherchestand im Klartext statt als Rohwert", async () => {
    renderPage();
    expect(await screen.findByText("Regeln vollständig")).toBeInTheDocument();
    expect(screen.getByText("Regeln teilweise recherchiert")).toBeInTheDocument();
  });

  it("zeigt die Paketvorschau, ohne etwas zu speichern", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderPage({ requests });

    await screen.findByText("KfW-Wohneigentumsprogramm (124)");
    await user.click(screen.getByRole("button", { name: "Importieren / Exportieren" }));
    await user.click(await screen.findByRole("button", { name: "Installieren" }));

    expect(await screen.findByText("Vorschau — noch nichts gespeichert")).toBeInTheDocument();
    expect(screen.getByText("BEG Wohngebäude Kredit (261)")).toBeInTheDocument();

    // Bis hierher darf nur die Vorschau gerufen worden sein, nie der Import.
    expect(requests.some((r) => r.url.includes("/v1/funding/import/preview"))).toBe(true);
    expect(
      requests.some(
        (r) => r.url.includes("/v1/funding/import") && !r.url.includes("/import/preview"),
      ),
    ).toBe(false);
  });

  it("laesst ungueltige Programme nicht anhaken und schlaegt sie nicht vor", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("KfW-Wohneigentumsprogramm (124)");
    await user.click(screen.getByRole("button", { name: "Importieren / Exportieren" }));
    await user.click(await screen.findByRole("button", { name: "Installieren" }));
    await screen.findByText("Vorschau — noch nichts gespeichert");

    const invalidRow = screen.getByText("Kaputtes Programm").closest("label");
    const invalidBox = within(invalidRow as HTMLElement).getByRole("checkbox");
    expect(invalidBox).toBeDisabled();
    expect(invalidBox).not.toBeChecked();
    // Der Grund steht an der Zeile, nicht in einer Sammelmeldung.
    expect(screen.getByText(/Unbekannter Foerderbetrag-Typ/)).toBeInTheDocument();

    const validRow = screen.getByText("BEG Wohngebäude Kredit (261)").closest("label");
    expect(within(validRow as HTMLElement).getByRole("checkbox")).toBeChecked();

    // Nur das gueltige Programm ist vorausgewaehlt.
    expect(screen.getByRole("button", { name: "1 Programm(e) einspielen" })).toBeInTheDocument();
  });

  it("schickt beim Import die abgewaehlten Positionen mit", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderPage({ requests });

    await screen.findByText("KfW-Wohneigentumsprogramm (124)");
    await user.click(screen.getByRole("button", { name: "Importieren / Exportieren" }));
    await user.click(await screen.findByRole("button", { name: "Installieren" }));
    await screen.findByText("Vorschau — noch nichts gespeichert");
    await user.click(screen.getByRole("button", { name: "1 Programm(e) einspielen" }));

    await waitFor(() => {
      const importCall = requests.find(
        (r) => r.url.includes("/v1/funding/import") && !r.url.includes("/import/preview"),
      );
      expect(importCall).toBeDefined();
      const body = JSON.parse(String(importCall?.init?.body ?? "{}"));
      // Index 1 ist das ungueltige Programm - es wird ausdruecklich uebersprungen.
      expect(body.skip_indices).toEqual([1]);
    });
  });

  it("pflegt Förderkonditionen als neue Programmversion", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderPage({ requests });

    const ownRow = (await screen.findByText("Eigenes Landesprogramm")).closest("tr");
    await user.click(within(ownRow as HTMLElement).getByRole("button", { name: "Konditionen" }));

    const dialog = await screen.findByRole("dialog", { name: "Fördervorlage bearbeiten" });
    const interest = within(dialog).getByLabelText("Nominalzins p.a. (%)");
    await user.clear(interest);
    await user.type(interest, "2,75");
    const amount = within(dialog).getByLabelText("Maximaler Betrag (€)");
    await user.clear(amount);
    await user.type(amount, "120000");
    await user.click(within(dialog).getByRole("button", { name: "Als neue Version speichern" }));

    await waitFor(() => {
      const update = requests.find((request) =>
        request.url.includes("/v1/funding/programs/prog-own/terms"),
      );
      expect(update?.init?.method).toBe("PUT");
      const body = JSON.parse(String(update?.init?.body ?? "{}"));
      expect(body.benefit_json.amount_minor).toBe(12_000_000);
      expect(body.loan_terms_json.nominal_rate_bps_annual).toBe(275);
      expect(body.loan_terms_json.max_term_months).toBe(420);
      expect(body.loan_terms_json.max_repayment_free_months).toBe(60);
    });
  });

  it("ändert Variantenzinsen ohne Laufzeitdaten zu verlieren", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderPage({ programs: [variantProgram], requests });

    const row = (await screen.findByText(variantProgram.title)).closest("tr");
    await user.click(within(row as HTMLElement).getByRole("button", { name: "Konditionen" }));

    const dialog = await screen.findByRole("dialog", { name: "Fördervorlage bearbeiten" });
    const shortRate = within(dialog).getByLabelText("Sollzins (%)", {
      selector: "#funding-variant-nominal-annuity_7_10",
    });
    await user.clear(shortRate);
    await user.type(shortRate, "0,02");
    await user.click(within(dialog).getByRole("button", { name: "Als neue Version speichern" }));

    await waitFor(() => {
      const update = requests.find((request) =>
        request.url.includes("/v1/funding/programs/prog-variants/terms"),
      );
      const body = JSON.parse(String(update?.init?.body ?? "{}"));
      expect(body.loan_terms_json.variants[0]).toMatchObject({
        id: "annuity_7_10",
        min_term_months: 84,
        max_term_months: 120,
        nominal_rate_bps_annual: 2,
        effective_rate_bps_annual: 1,
      });
      expect(body.loan_terms_json.variants[1]).toMatchObject({
        id: "bullet_7_10",
        nominal_rate_bps_annual: 69,
      });
    });
  });

  it("zeigt BayernLabo als kombiniertes Paket mit zwei pflegbaren Zinsbindungen", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderPage({ programs: [bayernProgram], requests });

    const row = (await screen.findByText(bayernProgram.title)).closest("tr");
    await user.click(within(row as HTMLElement).getByRole("button", { name: "Konditionen" }));
    const dialog = await screen.findByRole("dialog", { name: "Fördervorlage bearbeiten" });

    expect(within(dialog).getByText("Darlehensanteil an förderfähigen Kosten")).toBeVisible();
    expect(within(dialog).getByLabelText("Anteil (%)")).toHaveValue("33,33");
    expect(within(dialog).getByText("Kinderzuschuss je Kind")).toBeVisible();
    expect(within(dialog).getByLabelText("Betrag je Kind (€)")).toHaveValue("5000");
    expect(within(dialog).getByText("Zinsbindungsoptionen")).toBeVisible();
    expect(
      within(dialog).getByLabelText("Nominal (%)", {
        selector: "#funding-rate-option-nominal-fixed_10_years",
      }),
    ).toHaveValue("2,65");
    expect(
      within(dialog).getByLabelText("Effektiv (%)", {
        selector: "#funding-rate-option-effective-fixed_15_years",
      }),
    ).toHaveValue("3,03");
    expect(within(dialog).queryByLabelText("Maximale Laufzeit (Jahre)")).not.toBeInTheDocument();
  });

  it("prueft die Foerderfaehigkeit im eigenen Register statt im Vorhaben", async () => {
    // Die Pruefung ist aus dem Vorhaben hierher gewandert: "steht mir das zu"
    // gehoert zum Foerderkatalog, "was kostet es mich" zur Finanzierung. Geld
    // kommt seither ausschliesslich ueber "+ Foerderung" ins Vorhaben.
    renderPage();
    await screen.findByText("Förderprogramme");

    await userEvent.click(screen.getByRole("tab", { name: "Prüfung" }));

    expect(await screen.findByLabelText("Prüfen für Vorhaben")).toBeInTheDocument();
    expect(await screen.findByText("Förderprofil")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Förderung prüfen" })).toBeInTheDocument();
    // Uebernehmen gibt es hier bewusst nicht mehr.
    expect(screen.queryByRole("button", { name: "Als Quelle übernehmen" })).toBeNull();
  });
});
