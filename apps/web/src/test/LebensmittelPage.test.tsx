import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthProvider";
import { HouseholdProvider } from "../household/HouseholdProvider";
import { LebensmittelPage } from "../pages/LebensmittelPage";

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

const mockMealWeek = {
  id: "week-1",
  week_start: "2026-08-03",
  budget_minor: 10000,
  note: null,
  created_at: "2026-08-01T00:00:00Z",
};

// actual_minor liefert das Backend als POSITIVE Istkosten (grocery_service
// dreht das Vorzeichen der negativen Rohbuchung), remaining_vs_budget_minor
// ist entsprechend budget_minor - actual_minor.
const mockSummary = {
  meal_week_id: "week-1",
  week_start: "2026-08-03",
  budget_minor: 10000,
  meals_estimated_minor: 1200,
  items_planned_minor: 550,
  actual_minor: 4550,
  remaining_vs_budget_minor: 5450,
};

const mockDish = {
  id: "dish-1",
  name: "Chili con Carne",
  default_meal_type: "dinner",
  default_servings: 4,
  estimated_cost_minor: 950,
  category: "Hauptgericht",
  note: null,
  usage_count: 3,
  ingredient_count: 2,
  // Aus den Zutatenpreisen berechnet; estimated_cost_minor ist die
  // Handeingabe und uebersteuert, solange sie gesetzt ist.
  computed_cost_minor: 780,
  unpriced_ingredients: 1,
  priced_ingredients: 1,
  unquantified_ingredients: 0,
  effective_cost_minor: 950,
  cost_per_serving_minor: 238,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

// Zutat ohne verwertbaren Preis, wie sie der Arbeitsvorrat liefert: nach
// Zutat gruppiert, mit allen Gerichten, die daran haengen.
const mockPriceGap = {
  key: "hackfleisch",
  name: "Hackfleisch",
  kind: "no_price",
  dishes: [
    { dish_id: "dish-1", dish_name: "Chili con Carne", quantity: "500.00", unit: "g" },
    { dish_id: "dish-2", dish_name: "Bolognese", quantity: "300.00", unit: "g" },
  ],
  dish_count: 2,
  planned_count: 3,
  recipe_unit: "g",
  price_id: null,
  price_name: null,
  price_minor: null,
  price_pack_quantity: null,
  price_pack_unit: null,
  suggested_price_name: null,
  suggested_conversion: null,
};

const mockPrices = [
  {
    id: "price-1",
    name: "Nudeln",
    price_minor: 199,
    pack_quantity: "1.00",
    pack_unit: "kg",
    category: "Trockenware",
    note: null,
    used_in_dishes: 2,
    price_source: "manual",
    price_observed_on: null,
    price_is_stale: false,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
];

// Eine eingelesene Excel-Zeile, wie sie die Vorschau zurueckgibt. Ausdruecklich
// nichts Gespeichertes - geschrieben wird erst ueber den Bulk-Endpunkt.
const mockSheetPreview = {
  file_name: "Preise.xlsx",
  sheet_name: "Preise",
  header_row: 1,
  rows: [
    {
      row_number: 2,
      ingredient_name: "Nudeln",
      ingredient_is_new: false,
      store_name: "Aldi",
      store_location: null,
      brand: null,
      quality_tier: "standard",
      brand_tier: "unknown",
      price_minor: 89,
      pack_quantity: "500.00",
      pack_unit: "g",
      base_family: "masse",
      unit_price_micro: 178000,
      is_promotion: false,
      offer_valid_from: null,
      offer_valid_to: null,
      observed_on: "2026-08-05",
      note: null,
      is_duplicate: false,
      previous_unit_price_micro: null,
    },
    {
      row_number: 3,
      ingredient_name: "Haferflocken",
      ingredient_is_new: true,
      store_name: "Aldi",
      store_location: null,
      brand: null,
      quality_tier: "standard",
      brand_tier: "unknown",
      price_minor: 79,
      pack_quantity: "500.00",
      pack_unit: "g",
      base_family: "masse",
      unit_price_micro: 158000,
      is_promotion: false,
      offer_valid_from: null,
      offer_valid_to: null,
      observed_on: "2026-08-05",
      note: null,
      is_duplicate: false,
      previous_unit_price_micro: null,
    },
  ],
  problems: [{ row_number: 9, ingredient_name: "Butter", message: "Angebot ohne „gueltig bis“." }],
  untouched_rows: 12,
  unmapped_headers: [],
};

// Ladenvergleich: das Angebot bei Rewe ist teurer als der Normalpreis bei
// Aldi - sichtbar nur ueber den Grundpreis.
const mockPriceDetail = {
  ingredient_key: "nudeln",
  ingredient_name: "Nudeln",
  valuation: {
    price_source: "manual",
    price_strategy: null,
    preferred_store_id: null,
    quality_preference: null,
    planning_base_family: null,
    price_observation_id: null,
    price_observed_on: null,
    price_is_stale: false,
  },
  matrix: [
    {
      store_id: "store-1",
      store_label: "Rewe",
      quality_tier: "standard",
      brand_tier: "branded",
      observation_id: "obs-1",
      price_minor: 179,
      pack_quantity: "500.00",
      pack_unit: "g",
      unit_price_micro: 358000,
      observed_on: "2026-08-05",
      is_promotion: true,
      offer_valid_to: "2026-08-08",
      is_current_valuation: false,
    },
    {
      store_id: "store-2",
      store_label: "Aldi",
      quality_tier: "standard",
      brand_tier: "private_label",
      observation_id: "obs-2",
      price_minor: 89,
      pack_quantity: "500.00",
      pack_unit: "g",
      unit_price_micro: 178000,
      observed_on: "2026-08-04",
      is_promotion: false,
      offer_valid_to: null,
      is_current_valuation: true,
    },
  ],
  history: [],
  cheapest_unit_price_micro: 178000,
  dearest_unit_price_micro: 358000,
  base_family: "masse",
};

const mockIngredients = [
  {
    id: "ing-1",
    dish_id: "dish-1",
    name: "Hackfleisch",
    quantity: "500.00",
    unit: "g",
    category: "Fleisch",
    note: null,
    sort_order: 1,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
  {
    id: "ing-2",
    dish_id: "dish-1",
    name: "Salz",
    quantity: null,
    unit: null,
    category: null,
    note: null,
    sort_order: 2,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
];

function renderLebensmittelPage(options?: {
  meals?: Array<Record<string, unknown>>;
  dishes?: Array<Record<string, unknown>>;
  ingredients?: Array<Record<string, unknown>>;
  prices?: Array<Record<string, unknown>>;
  priceGaps?: Array<Record<string, unknown>>;
  priceDetail?: Record<string, unknown>;
  sheetPreview?: Record<string, unknown>;
  shoppingLists?: Array<Record<string, unknown>>;
  shoppingItems?: Array<Record<string, unknown>>;
  weeks?: Array<Record<string, unknown>>;
  requests?: Array<{ url: string; init?: RequestInit }>;
  /** Ersetzt die Antwort auf POST .../plan-suggestions (z. B. 503 mit KI-Grund). */
  planSuggestionResponse?: { status: number; body: unknown };
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      options?.requests?.push({ url, init });
      // Reihenfolge wichtig: die spezifischeren Pfade zuerst. "/v1/me" ist
      // ein Praefix von "/v1/meal-weeks", und "/v1/meal-weeks" ist eines von
      // ".../plan-suggestions" - ".includes" wuerde sonst falsch anschlagen.
      if (url.includes("/ingredient-suggestions")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              dish_id: "dish-1",
              dish_name: "Chili con Carne",
              servings: 4,
              model_name: "test-model",
              ingredients: [
                {
                  name: "Hackfleisch",
                  quantity: "500.00",
                  unit: "g",
                  category: "Fleisch",
                  already_present: true,
                },
                {
                  name: "Kidneybohnen",
                  quantity: "400.00",
                  unit: "g",
                  category: "Konserven",
                  already_present: false,
                },
              ],
            }),
        });
      }
      if (url.includes("/plan-suggestions") && options?.planSuggestionResponse) {
        const { status, body } = options.planSuggestionResponse;
        return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
      }
      if (url.includes("/plan-suggestions")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              meal_week_id: "week-1",
              week_start: "2026-08-03",
              budget_minor: 10000,
              model_name: "test-model",
              meals: [
                {
                  meal_date: "2026-08-03",
                  meal_type: "dinner",
                  dish_id: "dish-1",
                  dish_name: "Chili con Carne",
                  servings: 4,
                  estimated_cost_minor: 950,
                  reason: "günstig",
                },
              ],
              estimated_total_minor: 950,
              discarded_suggestions: 2,
            }),
        });
      }
      if (url.includes("/v1/dish-ingredients/bulk") || url.includes("/v1/meals/bulk")) {
        return Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve([]) });
      }
      // Vor "/v1/ingredient-prices": der Detailpfad enthaelt es als Praefix.
      if (url.includes("/detail")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.priceDetail ?? mockPriceDetail),
        });
      }
      if (url.includes("/v1/price-sheet/preview")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.sheetPreview ?? mockSheetPreview),
        });
      }
      if (url.includes("/v1/price-observations")) {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () =>
            Promise.resolve({
              inserted: 1,
              skipped_duplicates: 0,
              rejected: [],
              created_price_rows: 1,
              updated_valuations: 0,
              blocked_by_manual: 1,
            }),
        });
      }
      if (url.includes("/v1/ingredient-prices")) {
        return Promise.resolve({
          ok: true,
          status: init?.method === "POST" ? 201 : 200,
          json: () =>
            Promise.resolve(init?.method ? (options?.prices?.[0] ?? {}) : (options?.prices ?? [])),
        });
      }
      if (url.includes("/v1/meal-weeks/week-1/summary")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockSummary),
        });
      }
      if (url.includes("/v1/meal-weeks")) {
        return Promise.resolve({
          ok: true,
          status: init?.method === "POST" ? 201 : 200,
          json: () => Promise.resolve(options?.weeks ?? [mockMealWeek]),
        });
      }
      if (url.includes("/v1/meals")) {
        return Promise.resolve({
          ok: true,
          status: init?.method === "POST" ? 201 : 200,
          json: () =>
            Promise.resolve(init?.method ? (options?.meals?.[0] ?? {}) : (options?.meals ?? [])),
        });
      }
      // Reihenfolge: "/price-gaps" haengt ebenfalls an "/v1/dishes".
      if (url.includes("/price-gaps")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.priceGaps ?? []),
        });
      }
      if (url.includes("/v1/ingredient-aliases")) {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () =>
            Promise.resolve({
              id: "alias-1",
              alias_name: "Stückige Tomaten (Dose)",
              price_name: "Tomaten, stückig 400g",
              created_at: "2026-08-01T00:00:00Z",
            }),
        });
      }
      // Wieder Reihenfolge: ".../ingredients" haengt an einem /v1/dishes-Pfad.
      if (url.includes("/ingredients")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.ingredients ?? []),
        });
      }
      if (url.includes("/v1/dish-ingredients")) {
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () => Promise.resolve(options?.ingredients?.[0] ?? {}),
        });
      }
      if (url.includes("/v1/dishes")) {
        return Promise.resolve({
          ok: true,
          status: init?.method === "POST" ? 201 : 200,
          json: () =>
            Promise.resolve(init?.method ? (options?.dishes?.[0] ?? {}) : (options?.dishes ?? [])),
        });
      }
      if (url.includes("/fill-from-meals")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              created_items: 4,
              merged_items: 1,
              considered_meals: 2,
              skipped_meals: ["Reste"],
              unpriced_items: 2,
            }),
        });
      }
      if (url.includes("/v1/shopping-items")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.shoppingItems ?? []),
        });
      }
      if (url.includes("/v1/shopping-lists")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(options?.shoppingLists ?? []),
        });
      }
      if (url.includes("/v1/me")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(meResponse),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
    }),
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthProvider>
          <HouseholdProvider>
            <LebensmittelPage />
          </HouseholdProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const einkaufsliste = {
  id: "list-1",
  meal_week_id: "week-1",
  store: "Supermarkt",
  status: "planning",
  planned_total_minor: 0,
  actual_total_minor: 0,
  linked_transactions: [],
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

function einkaufsposten(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    id: "item-1",
    shopping_list_id: "list-1",
    meal_id: null,
    name: "Posten",
    category: null,
    quantity: "1.00",
    unit: "g",
    planned_price_minor: null,
    actual_price_minor: null,
    is_purchased: false,
    note: null,
    from_meal_plan: true,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

/**
 * Die Artikelnamen in der Reihenfolge, in der die Tabelle sie zeigt.
 *
 * "Neuer Artikel: Artikel" ist ausgenommen: das ist das leere Eingabefeld der
 * Anlegezeile unter der Tabelle, kein Posten der Liste.
 */
function artikelReihenfolge(): string[] {
  return screen
    .queryAllByRole("textbox", { name: /: Artikel$/ })
    .filter((element) => element.getAttribute("aria-label") !== "Neuer Artikel: Artikel")
    .map((element) => (element as HTMLInputElement).value);
}

describe("LebensmittelPage", () => {
  beforeEach(() => {
    // Die Seite leitet "diese Woche" und "naechste Woche" aus dem AKTUELLEN
    // Datum ab, waehrend die Vorgabedaten dieser Datei fest in Kalenderwoche
    // 32/2026 liegen. Ohne feste Systemzeit haengt das Ergebnis am
    // Ausfuehrungstag: ab dem 10.08.2026 wanderte "naechste Woche" von KW 33
    // auf KW 34 und zwei Tests wurden rot, ohne dass sich Code geaendert hat.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-05T09:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    // Die Beschriftungsvorliebe (Datum/Kalenderwoche) liegt bewusst in
    // localStorage - jsdom haelt die daran ueber Testgrenzen hinweg fest.
    window.localStorage.clear();
  });

  it("zeigt Wochenbudget, Plan- und Istkosten getrennt voneinander an", async () => {
    renderLebensmittelPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Lebensmittel" })).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText("Wochenbudget")).toBeInTheDocument();
    });

    // Plankosten (Essensplan + Einkaufsliste) und Istkosten (verknuepfte
    // Transaktionen) werden als eigenstaendige Werte ausgewiesen - keine
    // Vermischung, wie es das Abnahmekriterium aus Abschnitt 17 verlangt.
    expect(screen.getByText("100,00 €")).toBeInTheDocument(); // Wochenbudget
    expect(screen.getByText("12,00 €")).toBeInTheDocument(); // Geschaetzt (Essensplan)
    expect(screen.getByText("5,50 €")).toBeInTheDocument(); // Geplant (Einkaufsliste)
    expect(screen.getByText("45,50 €")).toBeInTheDocument(); // Ist (positive Kosten)
  });

  it("zeigt den Rest zum Wochenbudget aus remaining_vs_budget_minor an", async () => {
    renderLebensmittelPage();

    await waitFor(() => {
      expect(screen.getByText("Rest zum Wochenbudget")).toBeInTheDocument();
    });

    // 100,00 EUR Budget minus 45,50 EUR Ist - der Rest schrumpft mit jeder
    // Ausgabe, statt (wie vor der Vorzeichenkorrektur) zu wachsen.
    expect(screen.getByText("54,50 €")).toBeInTheDocument();
  });

  it("legt eine Mahlzeit direkt über die Tabellenzeile an", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests });

    // getByLabelText statt getByRole: das Feld bietet den Gerichte-Katalog per
    // datalist an und hat dadurch die Rolle "combobox", nicht "textbox".
    const dish = await screen.findByLabelText("Neue Mahlzeit: Gericht");
    await user.type(dish, "Kartoffelsuppe");
    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/meals") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        meal_week_id: "week-1",
        meal_date: "2026-08-03",
        dish_name: "Kartoffelsuppe",
        servings: 2,
      });
    });
  });

  it("schreibt die Woche als Datum aus und nennt die Kalenderwoche", async () => {
    renderLebensmittelPage();

    // Statt "2026-08-03": ausgeschriebener Zeitraum plus KW-Chip. Der
    // 3. August 2026 ist ein Montag und liegt in KW 32.
    expect(await screen.findByText("3. – 9. August 2026")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Kalenderwoche 32/ })).toHaveTextContent("KW 32");
  });

  it("schaltet die Beschriftung zwischen Datum und Kalenderwoche um", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage();

    await user.click(await screen.findByRole("button", { name: /Kalenderwoche 32/ }));

    expect(screen.getByText("Kalenderwoche 32 · 2026")).toBeInTheDocument();
    expect(screen.queryByText("3. – 9. August 2026")).not.toBeInTheDocument();
  });

  it("blättert fortlaufend zur nächsten Woche und legt sie mit einem Klick an", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests });

    await user.click(await screen.findByRole("button", { name: "Nächste Woche" }));

    // Auch eine noch nicht geplante Woche ist erreichbar - der Sprung haengt
    // am Datum, nicht an vorhandenen Datensaetzen.
    expect(screen.getByText("10. – 16. August 2026")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Kalenderwoche 33/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Diese Woche planen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/meal-weeks") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      // Das Wochenbudget der zuletzt geplanten Woche wird uebernommen, statt
      // bei 0 EUR zu starten.
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        week_start: "2026-08-10",
        budget_minor: 10000,
      });
    });
  });

  it("springt über die Pfeile auch zurück in frühere Wochen", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage();

    await user.click(await screen.findByRole("button", { name: "Vorherige Woche" }));

    expect(screen.getByText("27. Juli – 2. August 2026")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Kalenderwoche 31/ })).toBeInTheDocument();
  });

  it("füllt Portionen und Schätzung aus dem Gerichte-Katalog vor und verknüpft das Gericht", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests, dishes: [mockDish] });

    const dishField = await screen.findByLabelText("Neue Mahlzeit: Gericht");
    await user.type(dishField, "Chili con Carne");

    // Genau der Punkt der Wiederverwendung: der Name genuegt, Portionen und
    // Kostenschaetzung kommen aus dem Katalog.
    await waitFor(() => {
      expect(screen.getByLabelText("Neue Mahlzeit: Portionen")).toHaveValue(4);
    });
    expect(screen.getByLabelText("Neue Mahlzeit: Geschätzte Kosten")).toHaveValue("9,50");

    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/meals") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        dish_name: "Chili con Carne",
        dish_id: "dish-1",
        servings: 4,
        estimated_cost_minor: 950,
      });
    });
  });

  it("zeigt den Gerichte-Katalog als eigenen Bereich mit Verwendungszähler", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({ dishes: [mockDish] });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));

    expect(screen.getByRole("heading", { name: "Gerichte-Katalog" })).toBeInTheDocument();
    expect(screen.getByLabelText("Chili con Carne: Gericht")).toHaveValue("Chili con Carne");
    expect(screen.getByLabelText("Chili con Carne: Portionen")).toHaveValue(4);
    expect(screen.getByText("3×")).toBeInTheDocument();
  });

  it("klappt die Zutatenliste eines Gerichts auf und legt eine Zutat an", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests, dishes: [mockDish], ingredients: mockIngredients });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(screen.getByRole("button", { name: "Zutaten von Chili con Carne öffnen" }));

    expect(await screen.findByLabelText("Hackfleisch: Zutat")).toHaveValue("Hackfleisch");
    // "500.00" wird ohne unnötige Nachkommastellen dargestellt.
    expect(screen.getByLabelText("Hackfleisch: Menge")).toHaveValue("500");
    // Zutaten ohne Menge ("Salz") sind erlaubt und bleiben leer.
    expect(screen.getByLabelText("Salz: Menge")).toHaveValue("");

    await user.type(screen.getByLabelText("Chili con Carne: Neue Zutat"), "Kidneybohnen");
    await user.type(screen.getByLabelText("Chili con Carne: Neue Zutat, Menge"), "400");
    await user.type(screen.getByLabelText("Chili con Carne: Neue Zutat, Einheit"), "g");
    await user.click(screen.getByRole("button", { name: "+ Zutat" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/dish-ingredients") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        dish_id: "dish-1",
        name: "Kidneybohnen",
        quantity: "400",
        unit: "g",
      });
    });
  });

  it("zeigt Quelle und Zubereitung aus der Gerichtsnotiz als Schrittliste", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({
      dishes: [
        {
          ...mockDish,
          note: "Quelle: Kochbuch, Seite 12.\n1. Zwiebeln anbraten. 2. Hack dazu. 3. 20 Min. köcheln.",
        },
      ],
      ingredients: mockIngredients,
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(screen.getByRole("button", { name: "Zutaten von Chili con Carne öffnen" }));

    expect(await screen.findByText("Quelle: Kochbuch, Seite 12.")).toBeInTheDocument();
    // Die Nummern kommen aus der Liste, nicht aus dem Text - sonst stünde
    // "1. 1. Zwiebeln anbraten" da.
    const steps = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(steps).toEqual(
      expect.arrayContaining(["Zwiebeln anbraten.", "Hack dazu.", "20 Min. köcheln."]),
    );
  });

  it("zeigt eine Gerichtsnotiz ohne Schritte unverändert als Absatz", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({
      dishes: [{ ...mockDish, note: "Schmeckt den Kindern nicht." }],
      ingredients: mockIngredients,
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(screen.getByRole("button", { name: "Zutaten von Chili con Carne öffnen" }));

    expect(await screen.findByText("Schmeckt den Kindern nicht.")).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("zeigt KI-Zutaten als Vorschlag und übernimmt nur die angehakten", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests, dishes: [mockDish], ingredients: mockIngredients });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(screen.getByRole("button", { name: "Zutaten von Chili con Carne öffnen" }));
    await user.click(await screen.findByRole("button", { name: "✨ Zutaten vorschlagen" }));

    expect(await screen.findByText("Vorschlag — noch nicht gespeichert")).toBeInTheDocument();
    // Bereits vorhandene Zutaten sind abgewählt, damit ein zweiter Lauf keine
    // Dubletten anlegt.
    expect(screen.getByRole("checkbox", { name: "Hackfleisch übernehmen" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Kidneybohnen übernehmen" })).toBeChecked();

    // Der Vorschlag allein hat nichts geschrieben.
    expect(
      requests.some((entry) => entry.url.includes("/bulk") && entry.init?.method === "POST"),
    ).toBe(false);

    await user.click(screen.getByRole("button", { name: "1 übernehmen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/dish-ingredients/bulk") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      const body = JSON.parse(String(request?.init?.body));
      expect(body.dish_id).toBe("dish-1");
      expect(body.ingredients).toHaveLength(1);
      expect(body.ingredients[0]).toMatchObject({ name: "Kidneybohnen", quantity: "400.00" });
    });
  });

  it("schlägt einen Wochenplan aus dem Katalog vor und meldet Verworfenes", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests, dishes: [mockDish] });

    await user.click(await screen.findByRole("button", { name: "✨ Wochenplan vorschlagen" }));
    await user.type(screen.getByLabelText("Wunsch"), "italienisch");
    await user.click(screen.getByRole("button", { name: "Vorschlagen" }));

    expect(await screen.findByText(/Chili con Carne · 4 Port\./)).toBeInTheDocument();
    // Die Kostenzahl kommt aus der Katalogschätzung, nicht vom Modell.
    expect(screen.getByText(/Ausgewählt: 9,50 € von 100,00 € Wochenbudget/)).toBeInTheDocument();
    // Verworfene Modellantworten werden benannt statt verschwiegen.
    expect(screen.getByText(/2 Antworten des Modells verworfen/)).toBeInTheDocument();

    const suggestRequest = requests.find((entry) => entry.url.includes("/plan-suggestions"));
    expect(JSON.parse(String(suggestRequest?.init?.body))).toMatchObject({
      wish: "italienisch",
      meal_types: ["dinner"],
      skip_planned_days: true,
    });

    await user.click(screen.getByRole("button", { name: "1 übernehmen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/meals/bulk") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body)).meals[0]).toMatchObject({
        meal_date: "2026-08-03",
        dish_id: "dish-1",
        dish_name: "Chili con Carne",
        servings: 4,
      });
    });
  });

  it("nennt beim Wochenplan den Grund des KI-Kerns und den Weg zur Reasoning-Einstellung", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({
      requests,
      dishes: [mockDish],
      planSuggestionResponse: {
        status: 503,
        body: {
          detail: {
            code: "model_budget_exhausted",
            message:
              "Das Antwortbudget war aufgebraucht, bevor eine Antwort stand - meist hat das Denken (Reasoning) den freien Platz verbraucht. Abhilfe: Reasoning unter Einstellungen › KI ausschalten oder das Modell mit größerem Fenster laden.",
          },
        },
      },
    });

    await user.click(await screen.findByRole("button", { name: "✨ Wochenplan vorschlagen" }));
    await user.click(screen.getByRole("button", { name: "Vorschlagen" }));

    const notice = await screen.findByText(/Das Antwortbudget war aufgebraucht/);
    expect(notice).not.toHaveTextContent("model_budget_exhausted");
    expect(
      screen.getByRole("link", { name: "Reasoning unter Einstellungen › KI umstellen" }),
    ).toHaveAttribute("href", "/einstellungen?tab=ki");
    // Kein stilles "ohne Denken" hinterher: genau ein Aufruf.
    expect(requests.filter((entry) => entry.url.includes("/plan-suggestions"))).toHaveLength(1);
  });

  it("bietet den Wochenplan-Assistenten ohne Gerichte nicht an", async () => {
    renderLebensmittelPage();

    const button = await screen.findByRole("button", { name: "✨ Wochenplan vorschlagen" });
    // Ohne Katalog hat die KI nichts zum Kombinieren - der Knopf bleibt aus
    // und sagt auch, warum.
    expect(button).toBeDisabled();
    expect(
      screen.getByText(/vorgeschlagen wird ausschließlich aus deinem Katalog/i),
    ).toBeInTheDocument();
  });

  it("liest eine ausgefüllte Excel ein und übernimmt nur die angehakten Zeilen", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests, prices: mockPrices });

    await user.click(await screen.findByRole("button", { name: "Preise" }));

    const file = new File(["x"], "Preise.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await user.upload(screen.getByLabelText(/Ausgefüllte Datei einlesen/i), file);

    // Vorschau, ausdrücklich noch nichts Gespeichertes.
    expect(await screen.findByText("Vorschau — noch nichts gespeichert")).toBeInTheDocument();
    // Der Grundpreis steht daneben — erst er macht Packungsgrößen vergleichbar.
    expect(screen.getByText(/1,78 €\/kg/)).toBeInTheDocument();
    // Eine Zutat, die es noch nicht gibt, ist als solche gekennzeichnet.
    expect(screen.getByText("neu")).toBeInTheDocument();
    // Unlesbare Zeilen verschwinden nicht stillschweigend ...
    expect(screen.getByText(/1 Zeile\(n\) nicht lesbar/)).toBeInTheDocument();
    // ... und unausgefüllte Vorlagenzeilen sind kein Fehler.
    expect(screen.getByText(/12 Vorlagenzeile\(n\) ohne Eintrag übersprungen/)).toBeInTheDocument();

    // Eine Zeile abwählen, dann übernehmen.
    await user.click(screen.getByLabelText("Zeile 3, Haferflocken übernehmen"));
    await user.click(screen.getByRole("button", { name: "1 übernehmen" }));

    await waitFor(() => {
      const call = requests.find(
        (entry) =>
          entry.url.endsWith("/v1/price-observations/bulk") && entry.init?.method === "POST",
      );
      expect(call).toBeDefined();
      const body = JSON.parse(String(call?.init?.body)) as {
        observations: Array<{ ingredient_name: string }>;
      };
      expect(body.observations).toHaveLength(1);
      expect(body.observations[0]?.ingredient_name).toBe("Nudeln");
    });

    // Der Import fasst von Hand getippte Preise nicht an — und sagt das.
    expect(
      await screen.findByText(/rechnen weiter mit ihrem von Hand getippten/i),
    ).toBeInTheDocument();
  });

  it("klappt die Preisherkunft auf und stellt die Läden über den Grundpreis gegenüber", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({ prices: mockPrices });

    await user.click(await screen.findByRole("button", { name: "Preise" }));
    await user.click(screen.getByRole("button", { name: /von Hand/ }));

    // Das Angebot bei Rewe (3,58 €/kg) ist teurer als der Normalpreis bei
    // Aldi (1,78 €/kg) — genau der Fall, den man ohne Grundpreis nie sieht.
    expect(await screen.findByText(/3,58 €\/kg/)).toBeInTheDocument();
    expect(screen.getByText(/1,78 €\/kg/)).toBeInTheDocument();
    expect(screen.getByText("günstigster")).toBeInTheDocument();
    expect(screen.getByText(/Angebot bis/)).toBeInTheDocument();
    // Und die Zusage steht dabei, nicht nur im Code.
    expect(screen.getByText(/Angebote fließen nie in den Bewertungspreis/)).toBeInTheDocument();
  });

  it("bietet im Einkaufslisten-Tab keinen KI-Preisimport mehr an", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({ shoppingLists: [] });

    await user.click(await screen.findByRole("button", { name: /^Einkaufsliste \(/ }));

    expect(screen.queryByRole("button", { name: /Preisliste mit KI importieren/i })).toBeNull();
    expect(screen.getByText(/Preise werden nicht aus dem Internet abgerufen/)).toBeInTheDocument();
  });

  it("pflegt Zutatenpreise als eigene Liste und zeigt die betroffenen Gerichte", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests, prices: mockPrices });

    await user.click(await screen.findByRole("button", { name: "Preise" }));

    expect(screen.getByRole("heading", { name: "Zutaten-Preisliste" })).toBeInTheDocument();
    expect(screen.getByLabelText("Nudeln: Preis")).toHaveValue("1,99");
    // "1.00" wird ohne unnötige Nachkommastellen dargestellt.
    expect(screen.getByLabelText("Nudeln: Packungsmenge")).toHaveValue("1");
    // Zeigt, worauf sich eine Preisänderung auswirkt.
    expect(screen.getByText("2×")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Neuer Preis: Zutat"), "Hackfleisch");
    await user.type(screen.getByLabelText("Neuer Preis: Preis"), "5,49");
    await user.clear(screen.getByLabelText("Neuer Preis: Packungsmenge"));
    await user.type(screen.getByLabelText("Neuer Preis: Packungsmenge"), "500");
    await user.type(screen.getByLabelText("Neuer Preis: Einheit"), "g");
    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/ingredient-prices") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        name: "Hackfleisch",
        price_minor: 549,
        pack_quantity: "500",
        pack_unit: "g",
      });
    });
  });

  it("weist eine bereits vorhandene Zutat in der Preisliste ab", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({ prices: mockPrices });

    await user.click(await screen.findByRole("button", { name: "Preise" }));
    await user.type(screen.getByLabelText("Neuer Preis: Zutat"), "nudeln");

    // Zwei Preise für dieselbe Zutat wären genau das, was die Liste
    // verhindern soll - Groß-/Kleinschreibung zählt dabei nicht.
    expect(screen.getByText("Gibt es schon")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Hinzufügen" })).toBeDisabled();
  });

  it("zeigt die berechnete Schätzung als Vorgabe und die Handeingabe als Übersteuerung", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({ dishes: [mockDish] });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));

    // estimated_cost_minor (9,50) ist gesetzt und gewinnt; der berechnete
    // Wert steht als Platzhalter dahinter bereit.
    const field = screen.getByLabelText("Chili con Carne: Geschätzte Kosten");
    expect(field).toHaveValue("9,50");
    // "ab", weil ein Preis fehlt: eine Rechnung mit Lücken summiert nur, was
    // bepreist ist, und ist damit immer zu niedrig. Eine nackte 7,80 würde
    // Vollständigkeit vortäuschen.
    expect(field).toHaveAttribute("placeholder", "ab 7,80");
    // Abdeckung statt nackter Lückenzahl: "1 von 2" sagt, wie weit die
    // Rechnung trägt.
    expect(screen.getByText("1/2 bepreist")).toBeInTheDocument();
  });

  it("zeigt die Herkunft der Zahl in der Zeile, nicht nur im Tooltip", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({
      dishes: [
        mockDish,
        {
          ...mockDish,
          id: "dish-2",
          name: "Linsensuppe",
          // Keine Handeingabe: die Berechnung gilt.
          estimated_cost_minor: null,
          computed_cost_minor: 640,
          unpriced_ingredients: 0,
          priced_ingredients: 3,
          effective_cost_minor: 640,
          cost_per_serving_minor: 160,
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (2)" }));

    // ✎ = von Hand übersteuert, Σ = aus den Zutatenpreisen gerechnet. Ohne
    // dieses Zeichen sehen beide Fälle gleich aus.
    expect(screen.getByTitle(/Von Hand übersteuert/)).toBeInTheDocument();
    expect(screen.getByText("✎")).toBeInTheDocument();
    expect(screen.getByText("Σ")).toBeInTheDocument();
    // Vollständige Rechnung: keine Untergrenze, also kein "ab".
    expect(screen.getByLabelText("Linsensuppe: Geschätzte Kosten")).toHaveAttribute(
      "placeholder",
      "6,40",
    );
    // Kosten je Portion machen zwei Gerichte überhaupt vergleichbar.
    expect(screen.getByText("1,60 €/Port.")).toBeInTheDocument();
  });

  it("bietet abweichende Handschätzungen zum Verwerfen an", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({
      requests,
      dishes: [
        {
          ...mockDish,
          // Vollständige Rechnung (6,00) gegen Handeingabe (9,50): 58 %
          // daneben. Nur bei vollständiger Rechnung ist der Vergleich
          // überhaupt aussagekräftig.
          estimated_cost_minor: 950,
          computed_cost_minor: 600,
          unpriced_ingredients: 0,
          priced_ingredients: 2,
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    expect(
      screen.getByText(/Gericht rechnet anders, als die Handschätzung sagt/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Rechnung übernehmen" }));

    // Übersteuerung verwerfen heißt: das Feld auf null setzen, nicht auf 0.
    await waitFor(() => {
      const request = requests.find((entry) => entry.init?.method === "PATCH");
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toEqual({ estimated_cost_minor: null });
    });
  });

  it("verschweigt keine abweichende Handschätzung, solange die Rechnung Lücken hat", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({
      dishes: [
        {
          ...mockDish,
          estimated_cost_minor: 950,
          computed_cost_minor: 300,
          // Unvollständig: die Rechnung ist bekanntermaßen zu niedrig. Sie
          // als "Abweichung" zu melden wäre ein Fehlalarm.
          unpriced_ingredients: 2,
          priced_ingredients: 1,
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    expect(
      screen.queryByText(/rechnet anders, als die Handschätzung sagt/),
    ).not.toBeInTheDocument();
  });

  it("legt ein Gericht im Katalog an", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests });

    await user.click(await screen.findByRole("button", { name: "Gerichte (0)" }));
    await user.type(screen.getByLabelText("Neues Gericht: Gericht"), "Linsensuppe");
    await user.clear(screen.getByLabelText("Neues Gericht: Portionen"));
    await user.type(screen.getByLabelText("Neues Gericht: Portionen"), "3");
    await user.type(screen.getByLabelText("Neues Gericht: Geschätzte Kosten"), "6,40");
    await user.click(screen.getByRole("button", { name: "+ Hinzufügen" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/dishes") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        name: "Linsensuppe",
        default_servings: 3,
        estimated_cost_minor: 640,
      });
    });
  });

  it("füllt die Einkaufsliste aus dem Wochenplan und meldet Übersprungenes", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({
      shoppingLists: [
        {
          id: "list-1",
          meal_week_id: "week-1",
          store: "Supermarkt",
          status: "planning",
          planned_total_minor: 0,
          actual_total_minor: 0,
          linked_transactions: [],
          created_at: "2026-08-01T00:00:00Z",
          updated_at: "2026-08-01T00:00:00Z",
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Einkaufsliste (1)" }));
    await user.click(await screen.findByRole("button", { name: "Aus Wochenplan füllen" }));

    // Was nicht beitragen konnte, wird benannt - sonst sähe eine halb
    // gefüllte Liste vollständig aus.
    expect(
      await screen.findByText(/4 neu, 1 aktualisiert — aus 2 Gerichten\./),
    ).toBeInTheDocument();
    // Der Preis auf der Liste ist der Kassenpreis, nicht der anteilige Wert.
    expect(screen.getByText(/Preise gelten für ganze Packungen\./)).toBeInTheDocument();
    // Was unbewertet blieb, wird benannt - sonst wirkt die Plansumme fertig.
    expect(screen.getByText(/2 Posten ohne Preis/)).toBeInTheDocument();
    expect(screen.getByText(/Ohne hinterlegte Zutaten übersprungen: Reste\./)).toBeInTheDocument();
  });

  it("sortiert die Einkaufsliste nach Kategorie und lässt nach Spalten umsortieren", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({
      shoppingLists: [einkaufsliste],
      shoppingItems: [
        einkaufsposten({ id: "item-1", name: "Zwiebeln", category: "Gemüse", quantity: "3.00" }),
        einkaufsposten({ id: "item-2", name: "Nudeln", category: "Trockenware", quantity: "1.00" }),
        // Ohne Kategorie: gehoert ans Ende, nicht an den Anfang.
        einkaufsposten({ id: "item-3", name: "Backpulver", category: null, quantity: "2.00" }),
        einkaufsposten({ id: "item-4", name: "Apfel", category: "Gemüse", quantity: "5.00" }),
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Einkaufsliste (1)" }));

    // Vorgabe: nach Kategorie, innerhalb der Kategorie nach Namen, ohne
    // Kategorie zuletzt.
    await waitFor(() =>
      expect(artikelReihenfolge()).toEqual(["Apfel", "Zwiebeln", "Nudeln", "Backpulver"]),
    );

    await user.click(screen.getByRole("button", { name: "Nach Artikel sortieren" }));
    expect(artikelReihenfolge()).toEqual(["Apfel", "Backpulver", "Nudeln", "Zwiebeln"]);

    // Zweiter Klick auf dieselbe Spalte dreht die Richtung um.
    await user.click(screen.getByRole("button", { name: "Nach Artikel sortieren" }));
    expect(artikelReihenfolge()).toEqual(["Zwiebeln", "Nudeln", "Backpulver", "Apfel"]);

    await user.click(screen.getByRole("button", { name: "Nach Menge sortieren" }));
    expect(artikelReihenfolge()).toEqual(["Nudeln", "Backpulver", "Zwiebeln", "Apfel"]);
  });

  it("durchsucht die Einkaufsliste und blendet Gekauftes aus", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({
      shoppingLists: [einkaufsliste],
      shoppingItems: [
        einkaufsposten({ id: "item-1", name: "Zwiebeln", category: "Gemüse" }),
        einkaufsposten({ id: "item-2", name: "Nudeln", category: "Trockenware" }),
        einkaufsposten({
          id: "item-3",
          name: "Basmatireis",
          category: "Trockenware",
          is_purchased: true,
        }),
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Einkaufsliste (1)" }));
    await waitFor(() => expect(artikelReihenfolge()).toHaveLength(3));

    // Die Suche greift auch auf die Kategorie zu, nicht nur auf den Namen.
    await user.type(
      screen.getByRole("searchbox", { name: "Einkaufsliste durchsuchen" }),
      "trocken",
    );
    expect(artikelReihenfolge()).toEqual(["Basmatireis", "Nudeln"]);
    expect(screen.getByText("2 von 3 Posten — 1 ausgeblendet")).toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: "Einkaufsliste durchsuchen" }));
    await user.click(screen.getByLabelText("Gekauftes ausblenden"));
    expect(artikelReihenfolge()).toEqual(["Zwiebeln", "Nudeln"]);

    // Filtert man alles weg, muss das als Filter erkennbar sein und nicht wie
    // eine leere Liste aussehen.
    await user.type(screen.getByRole("searchbox", { name: "Einkaufsliste durchsuchen" }), "xyz");
    expect(screen.getByText(/Kein Posten passt zur Suche/)).toBeInTheDocument();
  });

  it("löscht eine Einkaufsliste nach Rückfrage", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderLebensmittelPage({
      requests,
      shoppingLists: [
        {
          id: "list-1",
          meal_week_id: "week-1",
          store: null,
          status: "planning",
          planned_total_minor: 0,
          actual_total_minor: 0,
          linked_transactions: [],
          created_at: "2026-08-04T19:37:00Z",
          updated_at: "2026-08-04T19:37:00Z",
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Einkaufsliste (1)" }));
    await user.click(
      await screen.findByRole("button", { name: "Einkaufsliste ohne Laden löschen" }),
    );

    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => {
      const request = requests.find(
        (entry) =>
          entry.url.endsWith("/v1/shopping-lists/list-1") && entry.init?.method === "DELETE",
      );
      expect(request).toBeDefined();
    });
    confirmSpy.mockRestore();
  });

  it("speichert Änderungen an einer Mahlzeit beim Verlassen der Tabellenzelle per PATCH", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({
      requests,
      meals: [
        {
          id: "meal-1",
          meal_week_id: "week-1",
          meal_date: "2026-08-03",
          meal_type: "dinner",
          dish_name: "Kartoffelsuppe",
          servings: 2,
          estimated_cost_minor: 450,
          note: null,
          created_at: "2026-08-01T00:00:00Z",
          updated_at: "2026-08-01T00:00:00Z",
        },
      ],
    });

    const dish = await screen.findByLabelText("Kartoffelsuppe: Gericht");
    await user.clear(dish);
    await user.type(dish, "Linsensuppe");
    await user.tab();

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/meals/meal-1") && entry.init?.method === "PATCH",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toEqual({ dish_name: "Linsensuppe" });
    });
  });

  // -------------------------------------------------------------------------
  // Preislücken
  // -------------------------------------------------------------------------

  it("fasst fehlende Preise nach Zutat zusammen statt nach Gericht", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({ dishes: [mockDish], priceGaps: [mockPriceGap] });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));

    // Eine Zutat, zwei Gerichte — ein Handgriff, nicht zwei.
    const summary = await screen.findByRole("button", {
      name: /1 Zutat ohne verwertbaren Preis/,
    });
    expect(summary).toHaveTextContent("2 Gerichte rechnen unvollständig");

    await user.click(summary);
    expect(screen.getByText("Hackfleisch")).toBeInTheDocument();
    expect(screen.getByText(/Chili con Carne/)).toBeInTheDocument();
    expect(screen.getByText(/Bolognese/)).toBeInTheDocument();
  });

  it("legt einen fehlenden Preis direkt in der Lückenzeile an", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({ requests, dishes: [mockDish], priceGaps: [mockPriceGap] });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(
      await screen.findByRole("button", { name: /1 Zutat ohne verwertbaren Preis/ }),
    );

    // Die Einheit ist aus dem Rezept vorbelegt — damit entsteht der
    // Einheitenkonflikt gar nicht erst.
    expect(screen.getByLabelText("Hackfleisch: Einheit")).toHaveValue("g");
    // Die Packungsmenge bleibt bewusst leer: sie zu raten wäre ein Fehler,
    // der nicht auffällt.
    expect(screen.getByLabelText("Hackfleisch: Packungsmenge")).toHaveValue("");
    expect(screen.getByRole("button", { name: "+ Preis" })).toBeDisabled();

    await user.type(screen.getByLabelText("Hackfleisch: Preis"), "4,99");
    await user.type(screen.getByLabelText("Hackfleisch: Packungsmenge"), "500");
    await user.click(screen.getByRole("button", { name: "+ Preis" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/ingredient-prices") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toMatchObject({
        name: "Hackfleisch",
        price_minor: 499,
        pack_quantity: "500",
        pack_unit: "g",
      });
    });
  });

  it("korrigiert bei einem Einheitenkonflikt die vorhandene Preiszeile", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({
      requests,
      dishes: [mockDish],
      priceGaps: [
        {
          ...mockPriceGap,
          key: "tomatensauce",
          name: "Tomatensauce",
          kind: "unit_mismatch",
          dishes: [{ dish_id: "dish-1", dish_name: "Auflauf", quantity: "1.00", unit: "Pkg" }],
          dish_count: 1,
          recipe_unit: "Pkg",
          price_id: "price-9",
          price_name: "Tomatensauce",
          price_minor: 89,
          price_pack_quantity: "400.00",
          price_pack_unit: "g",
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(
      await screen.findByRole("button", { name: /1 Zutat ohne verwertbaren Preis/ }),
    );

    // Hier fehlt nichts, es passt nur nicht — der Preis steht schon da und
    // muss nicht neu getippt werden.
    expect(screen.getByLabelText("Tomatensauce: Preis")).toHaveValue("0,89");
    expect(screen.getByLabelText("Tomatensauce: Einheit")).toHaveValue("Pkg");
    expect(screen.getByText(/das Rezept rechnet in „Pkg"/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Tomatensauce: Packungsmenge"), "1");
    await user.click(screen.getByRole("button", { name: "Korrigieren" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) =>
          entry.url.endsWith("/v1/ingredient-prices/price-9") && entry.init?.method === "PATCH",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toEqual({
        price_minor: 89,
        pack_quantity: "1",
        pack_unit: "Pkg",
      });
    });
  });

  it("ordnet eine abweichende Schreibweise erst nach Bestätigung zu", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({
      requests,
      dishes: [mockDish],
      priceGaps: [
        {
          ...mockPriceGap,
          key: "stückige tomaten (dose)",
          name: "Stückige Tomaten (Dose)",
          suggested_price_name: "Tomaten, stückig 400g",
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(
      await screen.findByRole("button", { name: /1 Zutat ohne verwertbaren Preis/ }),
    );

    // Vorschlag, kein Automatismus: "Salz" ist der Anfang von
    // "Salzkartoffeln", deshalb passiert bis zum Klick nichts.
    expect(requests.some((entry) => entry.url.includes("/v1/ingredient-aliases"))).toBe(false);

    await user.click(
      screen.getByRole("button", { name: /Meintest du „Tomaten, stückig 400g"\? Zuordnen/ }),
    );

    await waitFor(() => {
      const request = requests.find(
        (entry) => entry.url.endsWith("/v1/ingredient-aliases") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toEqual({
        alias_name: "Stückige Tomaten (Dose)",
        price_name: "Tomaten, stückig 400g",
      });
    });
  });

  it("übernimmt die nachgeschlagene Umrechnung erst nach Bestätigung", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({
      requests,
      dishes: [mockDish],
      priceGaps: [
        {
          ...mockPriceGap,
          key: "knoblauch",
          name: "Knoblauch",
          kind: "unit_mismatch",
          dishes: [{ dish_id: "dish-1", dish_name: "Curry", quantity: "4.00", unit: "Zehen" }],
          dish_count: 1,
          recipe_unit: "Zehen",
          price_id: "price-7",
          price_name: "Knoblauch",
          price_minor: 129,
          price_pack_quantity: "100.00",
          price_pack_unit: "g",
          suggested_conversion: {
            unit: "zehe",
            factor: "5",
            base_unit: "g",
            confidence: "plausibel",
          },
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(
      await screen.findByRole("button", { name: /1 Zutat ohne verwertbaren Preis/ }),
    );

    // Vorschlag, kein Automatismus - bis zum Klick wird nichts gespeichert.
    expect(requests.some((entry) => entry.url.includes("/v1/ingredient-unit-conversions"))).toBe(
      false,
    );

    await user.click(screen.getByRole("button", { name: /1 zehe = 5 g übernehmen/ }));

    await waitFor(() => {
      const request = requests.find(
        (entry) =>
          entry.url.endsWith("/v1/ingredient-unit-conversions") && entry.init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.init?.body))).toEqual({
        ingredient_name: "Knoblauch",
        unit: "zehe",
        factor: "5",
        base_unit: "g",
        origin: "default",
      });
    });
  });

  it("nimmt ohne Vorschlag die selbst getippte Zahl entgegen", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    renderLebensmittelPage({
      requests,
      dishes: [mockDish],
      priceGaps: [
        {
          ...mockPriceGap,
          key: "gemüsebrühe",
          name: "Gemüsebrühe",
          kind: "unit_mismatch",
          dishes: [{ dish_id: "dish-1", dish_name: "Suppe", quantity: "360.00", unit: "ml" }],
          dish_count: 1,
          recipe_unit: "ml",
          price_id: "price-8",
          price_name: "Gemüsebrühe",
          price_minor: 199,
          price_pack_quantity: "135.00",
          price_pack_unit: "g",
        },
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(
      await screen.findByRole("button", { name: /1 Zutat ohne verwertbaren Preis/ }),
    );

    await user.type(screen.getByLabelText("Umrechnung Gemüsebrühe"), "0,02");
    await user.click(screen.getByRole("button", { name: "Merken" }));

    await waitFor(() => {
      const request = requests.find(
        (entry) =>
          entry.url.endsWith("/v1/ingredient-unit-conversions") && entry.init?.method === "POST",
      );
      expect(JSON.parse(String(request?.init?.body))).toEqual({
        ingredient_name: "Gemüsebrühe",
        unit: "ml",
        factor: "0.02",
        base_unit: "g",
        origin: "manual",
      });
    });
  });

  it("übernimmt alle nachgeschlagenen Umrechnungen in einem Aufruf", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const user = userEvent.setup();
    const withSuggestion = (name: string, unit: string, factor: string, confidence: string) => ({
      ...mockPriceGap,
      key: name.toLowerCase(),
      name,
      kind: "unit_mismatch",
      recipe_unit: unit,
      price_id: `price-${name}`,
      price_pack_unit: "g",
      suggested_conversion: { unit, factor, base_unit: "g", confidence },
    });
    renderLebensmittelPage({
      requests,
      dishes: [mockDish],
      priceGaps: [
        withSuggestion("Knoblauch", "zehe", "5", "plausibel"),
        withSuggestion("Zwiebeln", "stk", "80", "belegt"),
        // Grobe Richtwerte gehören nicht in die Sammelübernahme.
        withSuggestion("Nelken", "stk", "0.2", "grob"),
      ],
    });

    await user.click(await screen.findByRole("button", { name: "Gerichte (1)" }));
    await user.click(
      await screen.findByRole("button", { name: /3 Zutaten ohne verwertbaren Preis/ }),
    );

    await user.click(screen.getByRole("button", { name: "2 Umrechnungen übernehmen" }));

    await waitFor(() => {
      const request = requests.find((entry) =>
        entry.url.endsWith("/v1/ingredient-unit-conversions/bulk"),
      );
      expect(request).toBeDefined();
      const body = JSON.parse(String(request?.init?.body)) as {
        items: { ingredient_name: string }[];
      };
      expect(body.items.map((item) => item.ingredient_name)).toEqual(["Knoblauch", "Zwiebeln"]);
    });
  });

  it("meldet im Preise-Tab ausdrücklich, wenn keine Lücken offen sind", async () => {
    const user = userEvent.setup();
    renderLebensmittelPage({ prices: mockPrices, priceGaps: [] });

    await user.click(await screen.findByRole("button", { name: "Preise" }));
    expect(
      await screen.findByText(/Keine Preislücken — jede Zutat mit Menge hat einen/),
    ).toBeInTheDocument();
  });
});
