import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { SortableTableHeader } from "../components/SortableTableHeader";
import { useFormatDate } from "../hooks/useProfile";
import { useHousehold } from "../household/HouseholdProvider";
import {
  ApiError,
  createDish,
  createDishIngredient,
  createDishIngredientsBulk,
  createIngredientAlias,
  createUnitConversion,
  createUnitConversionsBulk,
  createIngredientPrice,
  createMeal,
  createMealWeek,
  createMealsBulk,
  createShoppingItem,
  createShoppingList,
  deleteDish,
  deleteDishIngredient,
  createPriceObservationsBulk,
  deleteIngredientPrice,
  deleteMeal,
  deletePriceObservation,
  deleteShoppingItem,
  deleteShoppingList,
  downloadPriceSheetTemplate,
  fillShoppingListFromMeals,
  getIngredientPriceDetail,
  getMealWeekSummary,
  linkPurchase,
  listDishIngredients,
  listDishes,
  listIngredientPrices,
  listMealWeeks,
  listMeals,
  listPriceGaps,
  listShoppingItems,
  listShoppingLists,
  listTransactions,
  previewPriceSheet,
  seedDefaultDishes,
  suggestDishIngredients,
  suggestWeekPlan,
  unlinkPurchase,
  updateDish,
  updateDishIngredient,
  updateIngredientPrice,
  updateIngredientValuation,
  updateMeal,
  updateMealWeek,
  updateShoppingItem,
} from "../lib/api";
import { monthNameLong, monthNameShort, toIsoDateString } from "../lib/dates";
import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "../lib/money";
import type {
  Dish,
  DishIngredient,
  DishIngredientUpdateInput,
  DishUpdateInput,
  IngredientPrice,
  IngredientPriceUpdateInput,
  UnitConversionCreateInput,
  IngredientSuggestion,
  Meal,
  MealSuggestion,
  MealType,
  MealUpdateInput,
  MealWeek,
  PriceGap,
  PriceObservationInput,
  PriceSheetPreview,
  PriceSheetPreviewRow,
  WeekPlanSuggestionResult,
  ShoppingItem,
  ShoppingItemUpdateInput,
  ShoppingList,
  Transaction,
} from "../lib/types";
import { Modal } from "../components/Modal";
import { AiErrorNotice } from "../components/AiReasonNotice";

const MEAL_TYPE_LABELS: Record<MealType, string> = {
  breakfast: "Frühstück",
  lunch: "Mittagessen",
  dinner: "Abendessen",
  snack: "Snack",
};

const WEEK_LABEL_STORAGE_KEY = "finanzplaner.lebensmittel.weekLabel";

function parseIsoDate(isoDate: string): Date {
  return new Date(`${isoDate}T00:00:00`);
}

function addDays(isoDate: string, days: number): string {
  const date = parseIsoDate(isoDate);
  date.setDate(date.getDate() + days);
  return toIsoDateString(date);
}

/** Montag der Woche, in der `isoDate` liegt (Wochenbeginn nach ISO 8601). */
function mondayOf(isoDate: string): string {
  const date = parseIsoDate(isoDate);
  const weekdayFromMonday = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - weekdayFromMonday);
  return toIsoDateString(date);
}

function mondayOfCurrentWeek(): string {
  return mondayOf(toIsoDateString(new Date()));
}

/**
 * Kalenderwoche und zugehoeriges Wochenjahr nach ISO 8601. Massgeblich ist der
 * Donnerstag der Woche - deshalb kann der 1. Januar noch zur KW 52/53 des
 * Vorjahres gehoeren (und der 31. Dezember bereits zur KW 1 des Folgejahres).
 */
function isoWeek(isoDate: string): { week: number; year: number } {
  const thursday = parseIsoDate(isoDate);
  thursday.setDate(thursday.getDate() - ((thursday.getDay() + 6) % 7) + 3);
  const year = thursday.getFullYear();

  // Der 4. Januar liegt per Definition immer in KW 1.
  const firstThursday = new Date(year, 0, 4);
  firstThursday.setDate(firstThursday.getDate() - ((firstThursday.getDay() + 6) % 7) + 3);

  const week =
    1 + Math.round((thursday.getTime() - firstThursday.getTime()) / (7 * 24 * 60 * 60 * 1000));
  return { week, year };
}

/** "3. – 9. August 2026", bei Monats-/Jahreswechsel entsprechend ausgeschrieben. */
function formatWeekRange(weekStart: string): string {
  const start = parseIsoDate(weekStart);
  const end = parseIsoDate(addDays(weekStart, 6));
  const sameYear = start.getFullYear() === end.getFullYear();
  const sameMonth = sameYear && start.getMonth() === end.getMonth();

  const startLabel = sameMonth
    ? `${start.getDate()}.`
    : sameYear
      ? `${start.getDate()}. ${monthNameLong(start.getMonth())}`
      : `${start.getDate()}. ${monthNameLong(start.getMonth())} ${start.getFullYear()}`;

  return `${startLabel} – ${end.getDate()}. ${monthNameLong(end.getMonth())} ${end.getFullYear()}`;
}

function formatWeekLabel(weekStart: string, style: "date" | "week"): string {
  const { week, year } = isoWeek(weekStart);
  return style === "week" ? `Kalenderwoche ${week} · ${year}` : formatWeekRange(weekStart);
}

function readStoredWeekLabelStyle(): "date" | "week" {
  try {
    return window.localStorage.getItem(WEEK_LABEL_STORAGE_KEY) === "week" ? "week" : "date";
  } catch {
    // localStorage evtl. nicht verfuegbar - dann bleibt es bei der Datumsform.
    return "date";
  }
}

function amountDraft(value: number | null): string {
  return value == null ? "" : (value / 100).toFixed(2).replace(".", ",");
}

function handleCellKeyDown(event: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>): void {
  if (event.key === "Enter") {
    event.preventDefault();
    event.currentTarget.blur();
  }
  if (event.key === "Escape") {
    event.preventDefault();
    event.currentTarget.value = event.currentTarget.dataset.original ?? "";
    event.currentTarget.blur();
  }
}

const DISH_DATALIST_ID = "dish-catalog-options";

function findDishByName(dishes: Dish[], name: string): Dish | undefined {
  const needle = name.trim().toLowerCase();
  return needle ? dishes.find((dish) => dish.name.toLowerCase() === needle) : undefined;
}

function findDishById(dishes: Dish[], id: string | null | undefined): Dish | undefined {
  return id ? dishes.find((dish) => dish.id === id) : undefined;
}

/** Live aus den aktuellen Zutatenpreisen des verknuepften Gerichts, hochgerechnet
 *  auf die tatsaechlich geplanten Portionen dieser Mahlzeit. Im Unterschied zur
 *  "Schätzung" (ein beim Anlegen kopierter Fixwert) zieht dieser Wert bei
 *  spaeteren Preis- oder Portionsaenderungen automatisch mit. */
function liveMealCostMinor(
  dishId: string | null | undefined,
  servings: number,
  dishCatalog: Dish[],
): number | null {
  const dish = findDishById(dishCatalog, dishId);
  if (!dish || dish.cost_per_serving_minor == null) return null;
  return Math.round(dish.cost_per_serving_minor * servings);
}

/**
 * Ein blosses "Fehler" in der Statusspalte sagt nicht, was zu tun ist. Die
 * drei Faelle, die hier praktisch vorkommen, bekommen deshalb Klartext -
 * insbesondere HTTP 404 auf einer Route, die es im Code laengst gibt: dann
 * laeuft der API-Prozess auf altem Stand und muss neu gestartet werden.
 *
 * KI-Fehler (503 bzw. 409 mit {"detail": {"code", "message"}}) zeigen die
 * KI-Vorschlaege ueber AiErrorNotice: Klartext des Servers samt Aktion, bei
 * model_budget_exhausted der Weg zur Reasoning-Einstellung. Wiederholt wird
 * dabei nichts von selbst - auch nicht "ohne Denken".
 */
function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Fehler";
  if (error.code) return error.message;
  if (error.detail === "dish_name_already_exists") return "Name gibt es schon";
  if (error.status === 404) return "Route unbekannt — API neu starten";
  if (error.status === 403) return "Keine Schreibrechte";
  if (error.isNetworkError) return "API nicht erreichbar";
  return error.message;
}

/** "2026-08-05" -> "Mi, 5. Aug." fuer kompakte Vorschlagszeilen. */
function formatShortDate(isoDate: string): string {
  const date = parseIsoDate(isoDate);
  const weekday = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"][date.getDay()];
  return `${weekday}, ${date.getDate()}. ${monthNameShort(date.getMonth())}.`;
}

/** Anlegezeitpunkt einer Einkaufsliste, kurz: "4.8., 21:37". */
function formatCreatedAt(iso: string): string {
  const date = new Date(iso);
  return `${date.getDate()}.${date.getMonth() + 1}., ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

/** Menge ohne unnoetige Nachkommastellen: "750.00" -> "750", "1.50" -> "1,5". */
function formatQuantity(value: string | null): string {
  if (value == null) return "";
  const asNumber = Number(value);
  if (!Number.isFinite(asNumber)) return value;
  return String(asNumber).replace(".", ",");
}

export function LebensmittelPage() {
  const { session } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const token = session?.access_token;
  const householdId = selectedHouseholdId ?? "";

  const [activeTab, setActiveTab] = useState<
    "essensplan" | "einkaufsliste" | "gerichte" | "preise"
  >("essensplan");
  // Einmal beim Mount bestimmt: die angezeigte "aktuelle Woche" soll sich
  // waehrend einer Sitzung nicht unter der Hand verschieben.
  const [todayWeekStart] = useState(mondayOfCurrentWeek);
  const [selectedWeekStart, setSelectedWeekStart] = useState<string | null>(null);
  const [weekLabelStyle, setWeekLabelStyle] = useState<"date" | "week">(readStoredWeekLabelStyle);
  const [isWeekModalOpen, setIsWeekModalOpen] = useState(false);

  const { data: mealWeeks } = useQuery<MealWeek[]>({
    queryKey: ["meal-weeks", householdId],
    queryFn: () => listMealWeeks(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const { data: dishes } = useQuery<Dish[]>({
    queryKey: ["dishes", householdId],
    queryFn: () => listDishes(token!, householdId),
    enabled: Boolean(token && householdId),
  });

  const weeks = mealWeeks ?? [];
  const dishCatalog = dishes ?? [];

  // Die Wochenauswahl haengt am Datum, nicht an einer Datensatz-ID: so laesst
  // sich auch in noch ungeplante Wochen blaettern. Beim ersten Aufruf steht
  // die laufende Woche, falls sie geplant ist, sonst die zuletzt geplante
  // (listMealWeeks liefert absteigend nach week_start).
  const fallbackWeekStart = weeks.some((week) => week.week_start === todayWeekStart)
    ? todayWeekStart
    : (weeks[0]?.week_start ?? todayWeekStart);
  const currentWeekStart = selectedWeekStart ?? fallbackWeekStart;
  const currentWeek = weeks.find((week) => week.week_start === currentWeekStart);
  const currentWeekId = currentWeek?.id ?? "";
  const { week: currentWeekNumber } = isoWeek(currentWeekStart);

  const { data: summary } = useQuery({
    queryKey: ["meal-week-summary", currentWeekId],
    queryFn: () => getMealWeekSummary(token!, householdId, currentWeekId),
    enabled: Boolean(token && householdId && currentWeekId),
  });

  const {
    data: meals,
    isLoading: areMealsLoading,
    isError: areMealsError,
  } = useQuery<Meal[]>({
    queryKey: ["meals", currentWeekId],
    queryFn: () => listMeals(token!, householdId, currentWeekId),
    enabled: Boolean(token && householdId && currentWeekId),
  });

  const { data: shoppingLists } = useQuery<ShoppingList[]>({
    queryKey: ["shopping-lists", currentWeekId],
    queryFn: () => listShoppingLists(token!, householdId, currentWeekId),
    enabled: Boolean(token && householdId && currentWeekId),
  });

  // --- Entwurfszeile "neue Mahlzeit" ---
  // Steht vor der Wochennavigation, weil ein Wochenwechsel das vorgemerkte
  // Datum zuruecksetzt.
  const [mealDate, setMealDate] = useState("");
  const [mealType, setMealType] = useState<MealType>("dinner");
  const [dishName, setDishName] = useState("");
  const [servings, setServings] = useState("2");
  const [estimatedCostEuro, setEstimatedCostEuro] = useState("");
  const [mealNote, setMealNote] = useState("");
  const effectiveMealDate = mealDate || currentWeekStart;
  const draftDish = findDishByName(dishCatalog, dishName);

  // --- Wochennavigation ---
  const goToWeek = (weekStart: string) => {
    setSelectedWeekStart(weekStart);
    setMealDate("");
  };

  const toggleWeekLabelStyle = () => {
    const next = weekLabelStyle === "date" ? "week" : "date";
    setWeekLabelStyle(next);
    try {
      window.localStorage.setItem(WEEK_LABEL_STORAGE_KEY, next);
    } catch {
      // Nur eine Anzeigevorliebe - ohne Speicher bleibt sie sitzungslokal.
    }
  };

  // Budget der zeitlich naechstgelegenen bereits geplanten Woche: eine neue
  // Woche startet damit nicht bei 0 EUR, sondern beim gewohnten Wochenbudget.
  const carryOverBudgetMinor =
    weeks.find((week) => week.week_start <= currentWeekStart)?.budget_minor ??
    weeks[0]?.budget_minor ??
    0;

  const createWeekMutation = useMutation({
    mutationFn: (input: { week_start: string; budget_minor: number; note?: string }) =>
      createMealWeek(token!, householdId, input),
    onSuccess: (week) => {
      void queryClient.invalidateQueries({ queryKey: ["meal-weeks", householdId] });
      setSelectedWeekStart(week.week_start);
    },
  });

  const planCurrentWeek = () => {
    createWeekMutation.mutate({
      week_start: currentWeekStart,
      budget_minor: carryOverBudgetMinor,
    });
  };

  // --- Wochenbudget / Notiz bearbeiten ---
  const [budgetEuro, setBudgetEuro] = useState("");
  const [weekNote, setWeekNote] = useState("");

  const openWeekModal = () => {
    setBudgetEuro(amountDraft(currentWeek?.budget_minor ?? null));
    setWeekNote(currentWeek?.note ?? "");
    setIsWeekModalOpen(true);
  };

  const updateWeekMutation = useMutation({
    mutationFn: (input: { budget_minor: number; note: string | null }) =>
      updateMealWeek(token!, householdId, currentWeekId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meal-weeks", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
      setIsWeekModalOpen(false);
    },
  });

  const handleSaveWeek = (e: React.FormEvent) => {
    e.preventDefault();
    updateWeekMutation.mutate({
      budget_minor: parseAmountToMinorUnits(budgetEuro) ?? 0,
      note: weekNote.trim() || null,
    });
  };

  // --- Mahlzeit anlegen ---
  const createMealMutation = useMutation({
    mutationFn: (input: {
      meal_week_id: string;
      meal_date: string;
      meal_type: MealType;
      dish_name: string;
      dish_id?: string;
      servings: number;
      estimated_cost_minor?: number;
      note?: string;
    }) => createMeal(token!, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meals", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
      setDishName("");
      setEstimatedCostEuro("");
      setMealNote("");
    },
  });

  const deleteMealMutation = useMutation({
    mutationFn: (mealId: string) => deleteMeal(token!, householdId, mealId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meals", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
    },
  });

  const handleCreateMeal = (e: { preventDefault(): void }) => {
    e.preventDefault();
    if (!currentWeekId || !dishName.trim() || !effectiveMealDate) return;
    const estimated = estimatedCostEuro ? parseAmountToMinorUnits(estimatedCostEuro) : null;
    createMealMutation.mutate({
      meal_week_id: currentWeekId,
      meal_date: effectiveMealDate,
      meal_type: mealType,
      dish_name: dishName.trim(),
      dish_id: draftDish?.id,
      servings: Number(servings) || 1,
      estimated_cost_minor: estimated ?? undefined,
      note: mealNote.trim() || undefined,
    });
  };

  /** Uebernimmt Standardportionen und Kostenschaetzung aus dem Katalog in die
   *  Eingabezeile - genau der Punkt, an dem sich das einmalige Anlegen eines
   *  Gerichts auszahlt. Nur beim Wechsel auf ein anderes Kataloggericht, damit
   *  von Hand angepasste Portionen nicht bei jedem weiteren Tastendruck wieder
   *  ueberschrieben werden. */
  const applyDishToDraft = (nextName: string) => {
    setDishName(nextName);
    const nextDish = findDishByName(dishCatalog, nextName);
    if (!nextDish || nextDish.id === draftDish?.id) return;
    setServings(String(nextDish.default_servings));
    // effective_cost_minor statt estimated_cost_minor: das ist der Wert, der
    // am Gericht tatsaechlich gilt - berechnet aus den Zutatenpreisen, oder
    // die Handeingabe, falls eine gesetzt ist.
    setEstimatedCostEuro(amountDraft(nextDish.effective_cost_minor));
    if (nextDish.default_meal_type) setMealType(nextDish.default_meal_type);
  };

  const planDishInCurrentWeek = (dish: Dish) => {
    if (!currentWeekId) return;
    createMealMutation.mutate({
      meal_week_id: currentWeekId,
      meal_date: effectiveMealDate,
      meal_type: dish.default_meal_type ?? "dinner",
      dish_name: dish.name,
      dish_id: dish.id,
      servings: dish.default_servings,
      estimated_cost_minor: dish.effective_cost_minor ?? undefined,
    });
    setActiveTab("essensplan");
  };

  const updateMealMutation = useMutation({
    mutationFn: (input: { mealId: string; patch: MealUpdateInput }) =>
      updateMeal(token!, householdId, input.mealId, input.patch),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meals", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
    },
  });

  const commitMealField = (meal: Meal, field: keyof MealUpdateInput, rawValue: string) => {
    // Der Gerichtname zieht die Katalogverknuepfung mit: passt der neue Name
    // auf einen Katalogeintrag, wird verknuepft, sonst geloest. Portionen und
    // Schaetzung der bereits geplanten Mahlzeit bleiben unangetastet - sie
    // koennen bewusst vom Katalogstandard abweichen.
    if (field === "dish_name") {
      const name = rawValue.trim();
      if (!name) return;
      const nextDishId = findDishByName(dishCatalog, name)?.id ?? null;
      const patch: MealUpdateInput = {};
      if (name !== meal.dish_name) patch.dish_name = name;
      if (nextDishId !== (meal.dish_id ?? null)) patch.dish_id = nextDishId;
      if (Object.keys(patch).length === 0) return;
      updateMealMutation.mutate({ mealId: meal.id, patch });
      return;
    }

    let value: string | number | null = rawValue;
    if (field === "estimated_cost_minor") {
      value = rawValue.trim() ? parseAmountToMinorUnits(rawValue) : null;
      if (rawValue.trim() && value == null) return;
    } else if (field === "servings") {
      value = Math.max(1, Number(rawValue) || 1);
    } else if (field === "note") {
      value = rawValue.trim() || null;
    }

    const previous = meal[field as keyof Meal];
    if (value === previous || (value == null && previous == null)) return;
    updateMealMutation.mutate({ mealId: meal.id, patch: { [field]: value } });
  };

  // --- Gerichte-Katalog ---
  const createDishMutation = useMutation({
    mutationFn: (input: {
      name: string;
      default_meal_type?: MealType;
      default_servings?: number;
      estimated_cost_minor?: number;
      category?: string;
      note?: string;
    }) => createDish(token!, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
    },
  });

  /** Hebt eine spontan geplante Mahlzeit in den Katalog - beim naechsten Mal
   *  steht sie als Vorlage bereit. Existiert der Name dort schon, wird die
   *  Mahlzeit nur noch mit dem vorhandenen Eintrag verknuepft. */
  const rememberMealAsDish = (meal: Meal) => {
    const existing = findDishByName(dishCatalog, meal.dish_name);
    if (existing) {
      updateMealMutation.mutate({ mealId: meal.id, patch: { dish_id: existing.id } });
      return;
    }
    createDishMutation.mutate(
      {
        name: meal.dish_name,
        default_meal_type: meal.meal_type,
        default_servings: meal.servings,
        estimated_cost_minor: meal.estimated_cost_minor ?? undefined,
      },
      {
        onSuccess: (dish) => {
          updateMealMutation.mutate({ mealId: meal.id, patch: { dish_id: dish.id } });
        },
      },
    );
  };

  const updateDishMutation = useMutation({
    mutationFn: (input: { dishId: string; patch: DishUpdateInput }) =>
      updateDish(token!, householdId, input.dishId, input.patch),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
    },
  });

  const deleteDishMutation = useMutation({
    mutationFn: (dishId: string) => deleteDish(token!, householdId, dishId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["meals", currentWeekId] });
    },
  });

  const commitDishField = (dish: Dish, field: keyof DishUpdateInput, rawValue: string) => {
    let value: string | number | null;
    if (field === "estimated_cost_minor") {
      value = rawValue.trim() ? parseAmountToMinorUnits(rawValue) : null;
      if (rawValue.trim() && value == null) return;
    } else if (field === "default_servings") {
      value = Math.max(1, Number(rawValue) || 1);
    } else if (field === "name") {
      value = rawValue.trim();
      if (!value) return;
    } else {
      value = rawValue.trim() || null;
    }

    const previous = dish[field as keyof Dish];
    if (value === previous || (value == null && previous == null)) return;
    updateDishMutation.mutate({ dishId: dish.id, patch: { [field]: value } });
  };

  // --- Einkaufsliste anlegen ---
  const [storeName, setStoreName] = useState("");
  const createShoppingListMutation = useMutation({
    mutationFn: (input: { meal_week_id: string; store?: string }) =>
      createShoppingList(token!, householdId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shopping-lists", currentWeekId] });
      setStoreName("");
    },
  });

  const weekIsPlanned = Boolean(currentWeek);

  const unplannedWeekNotice = (
    <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
      <h2 className="card-heading">{formatWeekRange(currentWeekStart)} ist noch nicht geplant</h2>
      <p className="card-body">
        Mit einem Klick anlegen — das Wochenbudget wird aus der letzten geplanten Woche übernommen
        {carryOverBudgetMinor > 0 && ` (${formatMinorUnitsAsEuro(carryOverBudgetMinor)})`}.
      </p>
      <button
        type="button"
        className="btn-primary"
        onClick={planCurrentWeek}
        disabled={createWeekMutation.isPending}
      >
        {createWeekMutation.isPending ? "Wird angelegt …" : "Diese Woche planen"}
      </button>
    </div>
  );

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1 className="page-heading">Lebensmittel</h1>
          <p className="placeholder-note">
            Essensplan, Einkaufsliste und Wochenbudget mit Verknüpfung zu echten Einkäufen.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          onClick={openWeekModal}
          disabled={!weekIsPlanned}
        >
          Budget & Notiz
        </button>
      </div>

      {/* Wochennavigation: fortlaufend vor und zurueck, unabhaengig davon, ob
          die Zielwoche schon angelegt ist. */}
      <div className="week-nav card">
        <button
          type="button"
          className="week-nav__arrow"
          aria-label="Vorherige Woche"
          onClick={() => goToWeek(addDays(currentWeekStart, -7))}
        >
          ‹
        </button>

        <div className="week-nav__label">
          <strong className="week-nav__title">
            {formatWeekLabel(currentWeekStart, weekLabelStyle)}
          </strong>
          <button
            type="button"
            className="week-nav__kw"
            onClick={toggleWeekLabelStyle}
            title="Anzeige umschalten: Datum oder Kalenderwoche"
            aria-label={`Kalenderwoche ${currentWeekNumber} — Anzeige umschalten`}
          >
            KW {currentWeekNumber}
          </button>
          {!weekIsPlanned && <span className="status-badge status-badge--neutral">ungeplant</span>}
        </div>

        <button
          type="button"
          className="week-nav__arrow"
          aria-label="Nächste Woche"
          onClick={() => goToWeek(addDays(currentWeekStart, 7))}
        >
          ›
        </button>

        <div className="week-nav__tools">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => goToWeek(todayWeekStart)}
            disabled={currentWeekStart === todayWeekStart}
          >
            Heute
          </button>
          {weeks.length > 0 && (
            <select
              className="form-input week-nav__select"
              aria-label="Geplante Woche auswählen"
              value={
                weeks.some((week) => week.week_start === currentWeekStart) ? currentWeekStart : ""
              }
              onChange={(e) => {
                if (e.target.value) goToWeek(e.target.value);
              }}
            >
              <option value="">Geplante Wochen …</option>
              {weeks.map((week) => (
                <option key={week.id} value={week.week_start}>
                  KW {isoWeek(week.week_start).week} · {formatWeekRange(week.week_start)}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {summary && (
        <div className="metric-grid">
          <div className="card metric-card">
            <span className="metric-label">Wochenbudget</span>
            <span className="metric-value">{formatMinorUnitsAsEuro(summary.budget_minor)}</span>
            <span className="metric-hint">Plan</span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Geschätzt (Essensplan)</span>
            <span className="metric-value">
              {formatMinorUnitsAsEuro(summary.meals_estimated_minor)}
            </span>
            <span className="metric-hint">Plan</span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Geplant (Einkaufsliste)</span>
            <span className="metric-value">
              {formatMinorUnitsAsEuro(summary.items_planned_minor)}
            </span>
            <span className="metric-hint">Plan</span>
          </div>
          {/* actual_minor kommt vom Backend als POSITIVE Istkosten (die
              Rohbuchung ist negativ, grocery_service normalisiert sie) -
              Plan und Ist sind dadurch ohne weitere Umrechnung direkt
              vergleichbar. */}
          <div className="card metric-card">
            <span className="metric-label">Ist (verknüpfte Einkäufe)</span>
            <span className="metric-value">{formatMinorUnitsAsEuro(summary.actual_minor)}</span>
            <span className="metric-hint">Ist</span>
          </div>
          <div className="card metric-card">
            <span className="metric-label">Rest zum Wochenbudget</span>
            <span
              className={`metric-value ${summary.remaining_vs_budget_minor < 0 ? "transaction-amount--negative" : "transaction-amount--positive"}`}
            >
              {formatMinorUnitsAsEuro(summary.remaining_vs_budget_minor)}
            </span>
            <span className="metric-hint">Budget minus Ist</span>
          </div>
        </div>
      )}

      <div className="direction-toggle" style={{ marginBottom: "var(--space-4)" }}>
        <button
          type="button"
          className={`direction-btn ${activeTab === "essensplan" ? "direction-btn--active" : ""}`}
          onClick={() => setActiveTab("essensplan")}
        >
          Essensplan ({meals?.length ?? 0})
        </button>
        <button
          type="button"
          className={`direction-btn ${activeTab === "einkaufsliste" ? "direction-btn--active" : ""}`}
          onClick={() => setActiveTab("einkaufsliste")}
        >
          Einkaufsliste ({shoppingLists?.length ?? 0})
        </button>
        <button
          type="button"
          className={`direction-btn ${activeTab === "gerichte" ? "direction-btn--active" : ""}`}
          onClick={() => setActiveTab("gerichte")}
        >
          Gerichte ({dishCatalog.length})
        </button>
        <button
          type="button"
          className={`direction-btn ${activeTab === "preise" ? "direction-btn--active" : ""}`}
          onClick={() => setActiveTab("preise")}
        >
          Preise
        </button>
      </div>

      {/* Vorschlagsliste fuer alle Gericht-Eingabefelder: freie Eingabe bleibt
          moeglich, Katalogeintraege stehen als Vorschlag bereit. */}
      <datalist id={DISH_DATALIST_ID}>
        {dishCatalog.map((dish) => (
          <option key={dish.id} value={dish.name} />
        ))}
      </datalist>

      {activeTab === "essensplan" &&
        (!weekIsPlanned ? (
          unplannedWeekNotice
        ) : (
          <section aria-labelledby="meal-ledger-title">
            <div className="food-section-toolbar">
              <div>
                <h2 id="meal-ledger-title" className="food-section-title">
                  Wochenplan
                </h2>
                <p className="food-section-hint">
                  Zelle wählen, ändern, mit Enter oder Klick außerhalb speichern. Gerichte aus dem
                  Katalog füllen Portionen und Schätzung automatisch.
                </p>
              </div>
              <span className="food-keyboard-hint">Tab · Enter · Esc</span>
            </div>

            {/* key auf die Woche: beim Blättern wird der Assistent neu
                aufgebaut. Sonst überlebte ein Vorschlag den Wochenwechsel und
                würde Termine der alten Woche in die neue schreiben. */}
            <WeekPlanAssistant
              key={currentWeekId}
              mealWeekId={currentWeekId}
              weekLabel={formatWeekRange(currentWeekStart)}
              dishCount={dishCatalog.length}
              token={token!}
              householdId={householdId}
            />
            <div className="txn-table-wrapper food-table-wrap">
              <table className="txn-table food-ledger food-ledger--meals">
                <thead>
                  <tr>
                    <th className="txn-th">Datum</th>
                    <th className="txn-th">Mahlzeit</th>
                    <th className="txn-th">Gericht</th>
                    <th className="txn-th">Port.</th>
                    <th className="txn-th">Schätzung</th>
                    <th
                      className="txn-th"
                      title="Live aus den Zutatenpreisen des verknüpften Gerichts × aktuelle Portionen. Zieht bei Preis- oder Portionsänderung automatisch mit — anders als die Schätzung links, die nur ein beim Anlegen kopierter Fixwert ist."
                    >
                      Berechnet
                    </th>
                    <th className="txn-th">Notiz</th>
                    <th className="txn-th">Status / Aktion</th>
                  </tr>
                </thead>
                <tbody>
                  {areMealsLoading && (
                    <tr>
                      <td className="food-table-message" colSpan={8}>
                        Essensplan wird geladen …
                      </td>
                    </tr>
                  )}
                  {areMealsError && (
                    <tr>
                      <td className="food-table-message food-table-message--error" colSpan={8}>
                        Essensplan konnte nicht geladen werden.
                      </td>
                    </tr>
                  )}
                  {!areMealsLoading && !areMealsError && (meals ?? []).length === 0 && (
                    <tr>
                      <td className="food-table-message" colSpan={8}>
                        Noch leer — die erste Mahlzeit direkt unten eintragen.
                      </td>
                    </tr>
                  )}
                  {(meals ?? []).map((meal) => {
                    const rowIsSaving =
                      updateMealMutation.isPending &&
                      updateMealMutation.variables?.mealId === meal.id;
                    const rowHasError =
                      updateMealMutation.isError &&
                      updateMealMutation.variables?.mealId === meal.id;
                    const fieldProps = (original: string) => ({
                      "data-original": original,
                      onKeyDown: handleCellKeyDown,
                    });
                    const liveCostMinor = liveMealCostMinor(
                      meal.dish_id,
                      meal.servings,
                      dishCatalog,
                    );
                    return (
                      <tr key={meal.id} className="txn-tr food-edit-row">
                        <td className="txn-td">
                          <input
                            type="date"
                            className="food-cell-input food-cell-input--mono"
                            aria-label={`${meal.dish_name}: Datum`}
                            defaultValue={meal.meal_date}
                            onBlur={(e) =>
                              commitMealField(meal, "meal_date", e.currentTarget.value)
                            }
                            {...fieldProps(meal.meal_date)}
                          />
                        </td>
                        <td className="txn-td">
                          <select
                            className="food-cell-input"
                            aria-label={`${meal.dish_name}: Mahlzeit`}
                            defaultValue={meal.meal_type}
                            onBlur={(e) =>
                              commitMealField(meal, "meal_type", e.currentTarget.value)
                            }
                            {...fieldProps(meal.meal_type)}
                          >
                            {(Object.keys(MEAL_TYPE_LABELS) as MealType[]).map((type) => (
                              <option key={type} value={type}>
                                {MEAL_TYPE_LABELS[type]}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="txn-td">
                          <input
                            list={DISH_DATALIST_ID}
                            className="food-cell-input food-cell-input--strong"
                            aria-label={`${meal.dish_name}: Gericht`}
                            defaultValue={meal.dish_name}
                            onBlur={(e) =>
                              commitMealField(meal, "dish_name", e.currentTarget.value)
                            }
                            {...fieldProps(meal.dish_name)}
                          />
                        </td>
                        <td className="txn-td">
                          <input
                            type="number"
                            min={1}
                            className="food-cell-input food-cell-input--mono"
                            aria-label={`${meal.dish_name}: Portionen`}
                            defaultValue={meal.servings}
                            onBlur={(e) => commitMealField(meal, "servings", e.currentTarget.value)}
                            {...fieldProps(String(meal.servings))}
                          />
                        </td>
                        <td className="txn-td">
                          <input
                            inputMode="decimal"
                            className="food-cell-input food-cell-input--mono"
                            aria-label={`${meal.dish_name}: Geschätzte Kosten`}
                            defaultValue={amountDraft(meal.estimated_cost_minor)}
                            placeholder="0,00"
                            onBlur={(e) =>
                              commitMealField(meal, "estimated_cost_minor", e.currentTarget.value)
                            }
                            {...fieldProps(amountDraft(meal.estimated_cost_minor))}
                          />
                        </td>
                        <td
                          className="txn-td food-cost-per-serving"
                          aria-label={`${meal.dish_name}: Berechnete Kosten`}
                          title={
                            liveCostMinor == null
                              ? "Keine Katalog-Verknüpfung oder keine bepreisten Zutaten im Gericht"
                              : `${formatMinorUnitsAsEuro(liveCostMinor)} bei ${meal.servings} Portion(en) — live aus den Zutatenpreisen des Katalogs`
                          }
                        >
                          {liveCostMinor == null ? "–" : formatMinorUnitsAsEuro(liveCostMinor)}
                        </td>
                        <td className="txn-td">
                          <input
                            className="food-cell-input"
                            aria-label={`${meal.dish_name}: Notiz`}
                            defaultValue={meal.note ?? ""}
                            placeholder="Optional"
                            onBlur={(e) => commitMealField(meal, "note", e.currentTarget.value)}
                            {...fieldProps(meal.note ?? "")}
                          />
                        </td>
                        <td className="txn-td food-row-actions">
                          <span
                            className={`food-save-state ${rowHasError ? "food-save-state--error" : ""}`}
                            role="status"
                          >
                            {rowIsSaving
                              ? "Speichert …"
                              : rowHasError
                                ? "Fehler"
                                : meal.dish_id
                                  ? "Aus Katalog"
                                  : "Gespeichert"}
                          </span>
                          {!meal.dish_id && (
                            <button
                              type="button"
                              className="food-row-button"
                              aria-label={`${meal.dish_name} als Gericht merken`}
                              title="In den Gerichte-Katalog übernehmen"
                              onClick={() => rememberMealAsDish(meal)}
                            >
                              Merken
                            </button>
                          )}
                          <button
                            type="button"
                            className="food-delete-button"
                            aria-label={`${meal.dish_name} löschen`}
                            onClick={() => deleteMealMutation.mutate(meal.id)}
                          >
                            Löschen
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  <tr className="food-add-row">
                    <td className="txn-td">
                      <input
                        type="date"
                        className="food-cell-input food-cell-input--mono"
                        aria-label="Neue Mahlzeit: Datum"
                        value={effectiveMealDate}
                        onChange={(e) => setMealDate(e.target.value)}
                      />
                    </td>
                    <td className="txn-td">
                      <select
                        className="food-cell-input"
                        aria-label="Neue Mahlzeit: Mahlzeit"
                        value={mealType}
                        onChange={(e) => setMealType(e.target.value as MealType)}
                      >
                        {(Object.keys(MEAL_TYPE_LABELS) as MealType[]).map((type) => (
                          <option key={type} value={type}>
                            {MEAL_TYPE_LABELS[type]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="txn-td">
                      <input
                        list={DISH_DATALIST_ID}
                        className="food-cell-input food-cell-input--strong"
                        aria-label="Neue Mahlzeit: Gericht"
                        value={dishName}
                        placeholder={
                          dishCatalog.length
                            ? "Gericht wählen oder eintragen …"
                            : "Gericht hinzufügen …"
                        }
                        onChange={(e) => applyDishToDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleCreateMeal(e);
                        }}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        type="number"
                        min={1}
                        className="food-cell-input food-cell-input--mono"
                        aria-label="Neue Mahlzeit: Portionen"
                        value={servings}
                        onChange={(e) => setServings(e.target.value)}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        inputMode="decimal"
                        className="food-cell-input food-cell-input--mono"
                        aria-label="Neue Mahlzeit: Geschätzte Kosten"
                        value={estimatedCostEuro}
                        placeholder="0,00"
                        onChange={(e) => setEstimatedCostEuro(e.target.value)}
                      />
                    </td>
                    <td
                      className="txn-td food-cost-per-serving"
                      aria-label="Neue Mahlzeit: Berechnete Kosten"
                    >
                      {(() => {
                        const draftLiveCostMinor = liveMealCostMinor(
                          draftDish?.id,
                          Number(servings) || 1,
                          dishCatalog,
                        );
                        return draftLiveCostMinor == null
                          ? "–"
                          : formatMinorUnitsAsEuro(draftLiveCostMinor);
                      })()}
                    </td>
                    <td className="txn-td">
                      <input
                        className="food-cell-input"
                        aria-label="Neue Mahlzeit: Notiz"
                        value={mealNote}
                        placeholder="Optional"
                        onChange={(e) => setMealNote(e.target.value)}
                      />
                    </td>
                    <td className="txn-td food-row-actions">
                      <span
                        className={`food-save-state ${createMealMutation.isError ? "food-save-state--error" : ""}`}
                        role="status"
                      >
                        {createMealMutation.isPending
                          ? "Wird angelegt …"
                          : createMealMutation.isError
                            ? "Fehler"
                            : draftDish
                              ? "Aus Katalog"
                              : "Neue Zeile"}
                      </span>
                      <button
                        type="button"
                        className="btn-primary food-add-button"
                        disabled={!dishName.trim() || createMealMutation.isPending}
                        onClick={handleCreateMeal}
                      >
                        + Hinzufügen
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        ))}

      {activeTab === "gerichte" && (
        <DishCatalog
          dishes={dishCatalog}
          isWeekPlanned={weekIsPlanned}
          weekLabel={formatWeekRange(currentWeekStart)}
          token={token!}
          householdId={householdId}
          onCreate={(input) => createDishMutation.mutate(input)}
          createState={{
            isPending: createDishMutation.isPending,
            isError: createDishMutation.isError,
            error: createDishMutation.error,
          }}
          onCommitField={commitDishField}
          updateState={{
            pendingId: updateDishMutation.isPending
              ? (updateDishMutation.variables?.dishId ?? null)
              : null,
            errorId: updateDishMutation.isError
              ? (updateDishMutation.variables?.dishId ?? null)
              : null,
          }}
          onDelete={(dishId) => deleteDishMutation.mutate(dishId)}
          onPlan={planDishInCurrentWeek}
        />
      )}

      {activeTab === "preise" && <IngredientPriceList token={token!} householdId={householdId} />}

      {activeTab === "einkaufsliste" &&
        (!weekIsPlanned ? (
          unplannedWeekNotice
        ) : (
          <>
            <div className="card food-shopping-toolbar">
              <div className="food-shopping-toolbar__create">
                <h2 className="card-heading">Neue Einkaufsliste</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!currentWeekId) return;
                    createShoppingListMutation.mutate({
                      meal_week_id: currentWeekId,
                      store: storeName.trim() || undefined,
                    });
                  }}
                  style={{ display: "flex", gap: "var(--space-3)", alignItems: "flex-end" }}
                >
                  <div className="form-field" style={{ flex: 1 }}>
                    <label className="form-label" htmlFor="shopping-list-store">
                      Laden (optional)
                    </label>
                    <input
                      id="shopping-list-store"
                      type="text"
                      className="form-input"
                      value={storeName}
                      onChange={(e) => setStoreName(e.target.value)}
                      placeholder="z. B. Lidl, Aldi, Rewe oder Edeka"
                    />
                  </div>
                  <button type="submit" className="btn-primary">
                    + Liste
                  </button>
                </form>
              </div>
              <div className="food-import-callout" aria-describedby="food-import-hint">
                <p id="food-import-hint">
                  Preise pflegst du im Tab <strong>Preise</strong> — dort gibt es eine Excel-Vorlage
                  zum Herunterladen und Wiedereinlesen, samt Ladenvergleich je Zutat. Preise werden
                  nicht aus dem Internet abgerufen.
                </p>
              </div>
            </div>

            {(!shoppingLists || shoppingLists.length === 0) && (
              <div className="card" style={{ textAlign: "center", padding: "var(--space-6)" }}>
                <h2 className="card-heading">Noch keine Einkaufsliste</h2>
                <p className="card-body">
                  Legen Sie oben die erste Einkaufsliste für diese Woche an.
                </p>
              </div>
            )}

            {shoppingLists?.map((list) => (
              <ShoppingListCard
                key={list.id}
                list={list}
                meals={meals ?? []}
                weekStart={currentWeekStart}
                token={token!}
                householdId={householdId}
                currentWeekId={currentWeekId}
              />
            ))}
          </>
        ))}

      {isWeekModalOpen && (
        <Modal
          title={`Woche ${formatWeekRange(currentWeekStart)}`}
          onClose={() => setIsWeekModalOpen(false)}
        >
          <form
            onSubmit={handleSaveWeek}
            style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
          >
            <div className="form-field">
              <label className="form-label" htmlFor="week-budget">
                Wochenbudget (€)
              </label>
              <input
                id="week-budget"
                type="text"
                className="form-input form-input--mono"
                value={budgetEuro}
                onChange={(e) => setBudgetEuro(e.target.value)}
                placeholder="100,00"
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="week-note">
                Notiz
              </label>
              <input
                id="week-note"
                type="text"
                className="form-input"
                value={weekNote}
                onChange={(e) => setWeekNote(e.target.value)}
              />
            </div>
            <button type="submit" className="btn-primary" disabled={updateWeekMutation.isPending}>
              {updateWeekMutation.isPending ? "Speichert …" : "Speichern"}
            </button>
          </form>
        </Modal>
      )}
    </section>
  );
}

interface DishCatalogProps {
  dishes: Dish[];
  isWeekPlanned: boolean;
  weekLabel: string;
  token: string;
  householdId: string;
  onCreate: (input: {
    name: string;
    default_meal_type?: MealType;
    default_servings?: number;
    estimated_cost_minor?: number;
    category?: string;
  }) => void;
  createState: { isPending: boolean; isError: boolean; error: unknown };
  onCommitField: (dish: Dish, field: keyof DishUpdateInput, rawValue: string) => void;
  updateState: { pendingId: string | null; errorId: string | null };
  onDelete: (dishId: string) => void;
  onPlan: (dish: Dish) => void;
}

/**
 * Der Gerichte-Katalog: einmal angelegt, in jeder Woche wiederverwendbar.
 * Bewusst haushaltsweit und wochenunabhaengig - er ist die Vorlage, aus der
 * der Wochenplan schoepft, nicht Teil einer einzelnen Woche.
 */
function DishCatalog({
  dishes,
  isWeekPlanned,
  weekLabel,
  token,
  householdId,
  onCreate,
  createState,
  onCommitField,
  updateState,
  onDelete,
  onPlan,
}: DishCatalogProps) {
  const [name, setName] = useState("");
  const [defaultMealType, setDefaultMealType] = useState<MealType>("dinner");
  const [defaultServings, setDefaultServings] = useState("2");
  const [costEuro, setCostEuro] = useState("");
  const [category, setCategory] = useState("");
  const [openDishId, setOpenDishId] = useState<string | null>(null);

  const duplicateName = Boolean(findDishByName(dishes, name));

  const queryClient = useQueryClient();
  const seedMutation = useMutation({
    mutationFn: () => seedDefaultDishes(token, householdId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
    },
  });

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed || duplicateName) return;
    const cost = costEuro ? parseAmountToMinorUnits(costEuro) : null;
    onCreate({
      name: trimmed,
      default_meal_type: defaultMealType,
      default_servings: Number(defaultServings) || 1,
      estimated_cost_minor: cost ?? undefined,
      category: category.trim() || undefined,
    });
    setName("");
    setCostEuro("");
    setCategory("");
  };

  return (
    <section aria-labelledby="dish-catalog-title">
      <div className="food-section-toolbar">
        <div>
          <h2 id="dish-catalog-title" className="food-section-title">
            Gerichte-Katalog
          </h2>
          <p className="food-section-hint">
            Einmal eintragen, in jeder Woche wiederverwenden. Im Wochenplan genügt danach der Name —
            Portionen und Kostenschätzung kommen von hier.
          </p>
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: "0.5rem",
          }}
        >
          <span className="food-keyboard-hint">Tab · Enter · Esc</span>
          <button
            type="button"
            className="btn-secondary"
            style={{ fontSize: "0.85rem", padding: "0.25rem 0.5rem" }}
            onClick={() => seedMutation.mutate()}
            disabled={seedMutation.isPending}
          >
            {seedMutation.isPending ? "Lädt..." : "Standard-Gerichte laden"}
          </button>
        </div>
      </div>

      {/* Beide Bänder stehen ÜBER der Tabelle, weil sie erklären, warum
          Zahlen in ihr fehlen oder abweichen. Unter der Tabelle wären sie
          eine Fußnote zu einer Frage, die man längst gestellt hat. */}
      <PriceGapPanel token={token} householdId={householdId} />
      <ManualEstimateCleanup
        dishes={dishes}
        onClearOverride={(dish) => onCommitField(dish, "estimated_cost_minor", "")}
      />

      <div className="txn-table-wrapper food-table-wrap">
        <table className="txn-table food-ledger food-ledger--dishes">
          <thead>
            <tr>
              <th className="txn-th">Gericht</th>
              <th className="txn-th">Kategorie</th>
              <th className="txn-th">Mahlzeit</th>
              <th className="txn-th">Port.</th>
              <th className="txn-th">Schätzung</th>
              <th className="txn-th">Zutaten</th>
              <th className="txn-th">Geplant</th>
              <th className="txn-th">Status / Aktion</th>
            </tr>
          </thead>
          <tbody>
            {dishes.length === 0 && (
              <tr>
                <td className="food-table-message" colSpan={8}>
                  Noch keine Gerichte — das erste direkt unten eintragen. Danach steht es in jedem
                  Wochenplan zur Auswahl.
                </td>
              </tr>
            )}
            {dishes.map((dish) => {
              const rowIsSaving = updateState.pendingId === dish.id;
              const rowHasError = updateState.errorId === dish.id;
              const isOpen = openDishId === dish.id;
              const fieldProps = (original: string) => ({
                "data-original": original,
                onKeyDown: handleCellKeyDown,
              });
              // Welche Zahl gilt und woher sie kommt. Handeingabe schlägt
              // Berechnung — dieselbe Vorrangregel, die der Server in
              // effective_cost_minor festhält.
              const costOrigin =
                dish.estimated_cost_minor != null
                  ? "manual"
                  : dish.computed_cost_minor != null
                    ? "computed"
                    : "none";
              // Zutaten ohne Menge („Salz") zählen hier nicht mit: sie sind
              // absichtlich unbeziffert und dürfen die Abdeckung nicht
              // kleinrechnen.
              const quantifiedIngredients = dish.priced_ingredients + dish.unpriced_ingredients;
              // Eine Rechnung mit Lücken ist IMMER zu niedrig — sie summiert
              // nur, was einen Preis hat. „ab" sagt das, eine nackte Zahl
              // würde Vollständigkeit vortäuschen.
              const computedLabel =
                dish.computed_cost_minor == null
                  ? null
                  : dish.unpriced_ingredients > 0
                    ? `ab ${amountDraft(dish.computed_cost_minor)}`
                    : amountDraft(dish.computed_cost_minor);
              const costPlaceholder = computedLabel ?? "0,00";
              // Zwei verschiedene Aussagen, deshalb zwei Tooltips: das
              // Zeichen sagt, WOHER die Zahl kommt, das Feld sagt, was
              // Eintragen und Leeren bewirken.
              const originTitle =
                costOrigin === "none"
                  ? "Weder Handeingabe noch Berechnung — es fehlen die Zutatenpreise"
                  : costOrigin === "computed"
                    ? `Aus den Zutatenpreisen berechnet${dish.unpriced_ingredients > 0 ? " und wegen fehlender Preise nur eine Untergrenze" : ""}`
                    : "Von Hand übersteuert — die Berechnung gilt hier nicht";
              const fieldTitle =
                computedLabel == null
                  ? "Kein Zutatenpreis hinterlegt — Wert von Hand eintragen oder Preise nachtragen"
                  : `Eigene Zahl übersteuert die Berechnung, Feld leeren nimmt die Übersteuerung zurück (${computedLabel} €)`;
              return (
                <Fragment key={dish.id}>
                  <tr className="txn-tr food-edit-row">
                    <td className="txn-td">
                      <input
                        className="food-cell-input food-cell-input--strong"
                        aria-label={`${dish.name}: Gericht`}
                        defaultValue={dish.name}
                        onBlur={(e) => onCommitField(dish, "name", e.currentTarget.value)}
                        {...fieldProps(dish.name)}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        className="food-cell-input"
                        aria-label={`${dish.name}: Kategorie`}
                        defaultValue={dish.category ?? ""}
                        placeholder="z. B. Hauptgericht"
                        onBlur={(e) => onCommitField(dish, "category", e.currentTarget.value)}
                        {...fieldProps(dish.category ?? "")}
                      />
                    </td>
                    <td className="txn-td">
                      <select
                        className="food-cell-input"
                        aria-label={`${dish.name}: Mahlzeit`}
                        defaultValue={dish.default_meal_type ?? ""}
                        onBlur={(e) =>
                          onCommitField(dish, "default_meal_type", e.currentTarget.value)
                        }
                        {...fieldProps(dish.default_meal_type ?? "")}
                      >
                        <option value="">—</option>
                        {(Object.keys(MEAL_TYPE_LABELS) as MealType[]).map((type) => (
                          <option key={type} value={type}>
                            {MEAL_TYPE_LABELS[type]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="txn-td">
                      <input
                        type="number"
                        min={1}
                        className="food-cell-input food-cell-input--mono"
                        aria-label={`${dish.name}: Portionen`}
                        defaultValue={dish.default_servings}
                        onBlur={(e) =>
                          onCommitField(dish, "default_servings", e.currentTarget.value)
                        }
                        {...fieldProps(String(dish.default_servings))}
                      />
                    </td>
                    {/* Leeres Feld = berechneter Wert gilt (steht als Platzhalter
                      drin). Eine eingetragene Zahl übersteuert ihn, Feld leeren
                      nimmt die Übersteuerung wieder zurück.

                      Das Herkunftszeichen davor steht bewusst in der Zeile und
                      nicht nur im Tooltip: ohne es sehen „gerechnet" und „von
                      Hand" gleich aus, und die Berechnung bleibt unsichtbar —
                      genau der Zustand, in dem die Preisliste sich nicht
                      auszahlt. */}
                    <td className="txn-td food-cost-cell food-cost-cell--dish">
                      <span className="food-cost-cell__row">
                        <span
                          className={`food-cost-origin food-cost-origin--${costOrigin}`}
                          title={originTitle}
                          aria-hidden="true"
                        >
                          {costOrigin === "manual" ? "✎" : costOrigin === "computed" ? "Σ" : "·"}
                        </span>
                        <input
                          inputMode="decimal"
                          className={`food-cell-input food-cell-input--mono ${costOrigin === "computed" ? "food-cell-input--computed" : ""}`}
                          aria-label={`${dish.name}: Geschätzte Kosten`}
                          defaultValue={amountDraft(dish.estimated_cost_minor)}
                          placeholder={costPlaceholder}
                          title={fieldTitle}
                          onBlur={(e) =>
                            onCommitField(dish, "estimated_cost_minor", e.currentTarget.value)
                          }
                          {...fieldProps(amountDraft(dish.estimated_cost_minor))}
                        />
                      </span>
                      <span className="food-cost-cell__row food-cost-cell__row--sub">
                        {quantifiedIngredients > 0 && (
                          <button
                            type="button"
                            className={`food-cost-coverage ${dish.unpriced_ingredients > 0 ? "food-cost-coverage--gap" : ""}`}
                            title={
                              dish.unpriced_ingredients > 0
                                ? `${dish.priced_ingredients} von ${quantifiedIngredients} Zutaten bepreist — ${dish.unpriced_ingredients} ${dish.unpriced_ingredients === 1 ? "Preis fehlt" : "Preise fehlen"}. Die Rechnung ist damit eine Untergrenze. Klicken zeigt die Zutaten.`
                                : `Alle ${quantifiedIngredients} Zutaten mit Menge sind bepreist. Klicken zeigt die Zutaten.`
                            }
                            onClick={() => setOpenDishId(isOpen ? null : dish.id)}
                          >
                            {dish.priced_ingredients}/{quantifiedIngredients} bepreist
                          </button>
                        )}
                        {dish.cost_per_serving_minor != null && (
                          <span
                            className="food-cost-per-serving"
                            title={`${formatMinorUnitsAsEuro(dish.cost_per_serving_minor)} je Portion bei ${dish.default_servings} Portionen — die Zahl, mit der sich zwei Gerichte vergleichen lassen`}
                          >
                            {formatMinorUnitsAsEuro(dish.cost_per_serving_minor)}/Port.
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="txn-td">
                      <button
                        type="button"
                        className={`food-row-button food-ingredient-toggle ${isOpen ? "food-ingredient-toggle--open" : ""}`}
                        aria-expanded={isOpen}
                        aria-label={`Zutaten von ${dish.name} ${isOpen ? "schließen" : "öffnen"}`}
                        onClick={() => setOpenDishId(isOpen ? null : dish.id)}
                      >
                        {isOpen ? "▾" : "▸"}{" "}
                        {dish.ingredient_count === 0
                          ? "anlegen"
                          : `${dish.ingredient_count} Zutaten`}
                      </button>
                    </td>
                    <td className="txn-td food-usage-cell">
                      <span className="food-usage-count" title={`${dish.usage_count}× eingeplant`}>
                        {dish.usage_count}×
                      </span>
                    </td>
                    <td className="txn-td food-row-actions">
                      <span
                        className={`food-save-state ${rowHasError ? "food-save-state--error" : ""}`}
                        role="status"
                      >
                        {rowIsSaving ? "Speichert …" : rowHasError ? "Fehler" : "Gespeichert"}
                      </span>
                      <button
                        type="button"
                        className="food-row-button"
                        aria-label={`${dish.name} in den Wochenplan übernehmen`}
                        title={
                          isWeekPlanned
                            ? `In den Wochenplan ${weekLabel} übernehmen`
                            : "Erst die Woche planen"
                        }
                        disabled={!isWeekPlanned}
                        onClick={() => onPlan(dish)}
                      >
                        In Woche
                      </button>
                      <button
                        type="button"
                        className="food-delete-button"
                        aria-label={`${dish.name} löschen`}
                        title="Nur der Katalogeintrag wird gelöscht, geplante Mahlzeiten bleiben bestehen"
                        onClick={() => onDelete(dish.id)}
                      >
                        Löschen
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="food-ingredient-row">
                      <td className="txn-td" colSpan={8}>
                        <IngredientEditor dish={dish} token={token} householdId={householdId} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            <tr className="food-add-row">
              <td className="txn-td">
                <input
                  className="food-cell-input food-cell-input--strong"
                  aria-label="Neues Gericht: Gericht"
                  value={name}
                  placeholder="Gericht hinzufügen …"
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      submit();
                    }
                  }}
                />
              </td>
              <td className="txn-td">
                <input
                  className="food-cell-input"
                  aria-label="Neues Gericht: Kategorie"
                  value={category}
                  placeholder="Kategorie"
                  onChange={(e) => setCategory(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <select
                  className="food-cell-input"
                  aria-label="Neues Gericht: Mahlzeit"
                  value={defaultMealType}
                  onChange={(e) => setDefaultMealType(e.target.value as MealType)}
                >
                  {(Object.keys(MEAL_TYPE_LABELS) as MealType[]).map((type) => (
                    <option key={type} value={type}>
                      {MEAL_TYPE_LABELS[type]}
                    </option>
                  ))}
                </select>
              </td>
              <td className="txn-td">
                <input
                  type="number"
                  min={1}
                  className="food-cell-input food-cell-input--mono"
                  aria-label="Neues Gericht: Portionen"
                  value={defaultServings}
                  onChange={(e) => setDefaultServings(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  inputMode="decimal"
                  className="food-cell-input food-cell-input--mono"
                  aria-label="Neues Gericht: Geschätzte Kosten"
                  value={costEuro}
                  placeholder="0,00"
                  onChange={(e) => setCostEuro(e.target.value)}
                />
              </td>
              <td className="txn-td" colSpan={2}>
                <span className="food-section-hint">
                  Zutaten stehen nach dem Anlegen zur Verfügung.
                </span>
              </td>
              <td className="txn-td food-row-actions">
                <span
                  className={`food-save-state ${createState.isError || duplicateName ? "food-save-state--error" : ""}`}
                  role="status"
                >
                  {createState.isPending
                    ? "Wird angelegt …"
                    : duplicateName
                      ? "Gibt es schon"
                      : createState.isError
                        ? describeError(createState.error)
                        : "Neue Zeile"}
                </span>
                <button
                  type="button"
                  className="btn-primary food-add-button"
                  disabled={!name.trim() || duplicateName || createState.isPending}
                  onClick={submit}
                >
                  + Hinzufügen
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * Preislücken: Zutaten mit Menge, aber ohne verwertbaren Preis.
 *
 * Dieselbe Information, die jede Gerichtezeile als „2/4 bepreist" trägt — nur
 * nach ZUTAT gruppiert statt nach Gericht. Das ist der Unterschied zwischen
 * Anzeige und Arbeitsablauf: „Hackfleisch" fehlt in Chili und in der
 * Hackfleischsoße, ist aber ein Handgriff. Nach Gerichten sortiert fasst man
 * dieselbe Zutat zweimal an und ist trotzdem nicht schneller fertig.
 *
 * Sortiert ist nach Wirkung, nicht alphabetisch: die ersten Zeilen bringen den
 * größten Zuwachs an Abdeckung.
 */
function PriceGapPanel({
  token,
  householdId,
  defaultOpen = false,
}: {
  token: string;
  householdId: string;
  defaultOpen?: boolean;
}) {
  const queryClient = useQueryClient();
  const [isOpen, setIsOpen] = useState(defaultOpen);

  const { data: gaps } = useQuery<PriceGap[]>({
    queryKey: ["price-gaps", householdId],
    queryFn: () => listPriceGaps(token, householdId),
    enabled: Boolean(token && householdId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["ingredient-prices", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["dish-ingredients"] });
  };

  // Die Zeilen, die ein Klick erledigen kann. „grob" ist bewusst nicht dabei:
  // eine Größenordnung soll jemand ansehen, bevor sie in die Rechnung geht.
  const bulkReady = (gaps ?? []).filter(
    (gap) => gap.suggested_conversion && gap.suggested_conversion.confidence !== "grob",
  );
  const bulkMutation = useMutation({
    mutationFn: () =>
      createUnitConversionsBulk(
        token,
        householdId,
        bulkReady.map((gap) => ({
          ingredient_name: gap.name,
          unit: gap.suggested_conversion!.unit,
          factor: String(gap.suggested_conversion!.factor),
          base_unit: gap.suggested_conversion!.base_unit,
          origin: "default" as const,
        })),
      ),
    onSuccess: invalidate,
  });

  if (!gaps) return null;
  if (gaps.length === 0) {
    // Im Preise-Tab ist die Bestätigung eine Aussage („nichts offen"), als
    // Banner über dem Katalog wäre sie nur eine Zeile ohne Anlass.
    return defaultOpen ? (
      <p className="food-section-hint food-gap-panel__empty">
        Keine Preislücken — jede Zutat mit Menge hat einen verwertbaren Preis.
      </p>
    ) : null;
  }

  const affectedDishes = new Set(gaps.flatMap((gap) => gap.dishes.map((d) => d.dish_id))).size;

  return (
    <div className="food-gap-panel">
      <button
        type="button"
        className="food-gap-panel__summary"
        aria-expanded={isOpen}
        onClick={() => setIsOpen(!isOpen)}
      >
        <strong>
          {gaps.length} {gaps.length === 1 ? "Zutat" : "Zutaten"} ohne verwertbaren Preis
        </strong>
        {" — "}
        {affectedDishes} {affectedDishes === 1 ? "Gericht rechnet" : "Gerichte rechnen"}{" "}
        unvollständig
        <span aria-hidden="true">{isOpen ? " ▴" : " ▾"}</span>
      </button>

      {isOpen && (
        <>
          <p className="food-section-hint">
            Nach Wirkung sortiert: oben steht, was in den meisten Gerichten steckt. Eine Zutat
            einmal bepreisen repariert jedes Gericht, das sie verwendet. Die Einheit ist aus dem
            Rezept vorbelegt — so entsteht der Einheitenkonflikt gar nicht erst.
          </p>
          {bulkReady.length > 0 && (
            <p className="food-section-hint">
              <button
                type="button"
                className="btn-secondary"
                disabled={bulkMutation.isPending}
                onClick={() => bulkMutation.mutate()}
              >
                {bulkReady.length} {bulkReady.length === 1 ? "Umrechnung" : "Umrechnungen"}{" "}
                übernehmen
              </button>{" "}
              — nachgeschlagene Werte wie „1 Zehe = 5 g". Grobe Richtwerte sind nicht dabei, die
              stehen einzeln in der Liste.
            </p>
          )}
          <div className="txn-table-wrapper food-table-wrap">
            <table className="txn-table food-ledger food-ledger--gaps">
              <thead>
                <tr>
                  <th className="txn-th">Zutat</th>
                  <th className="txn-th">Betrifft</th>
                  <th className="txn-th">Preis</th>
                  <th className="txn-th">für Menge</th>
                  <th className="txn-th">Einheit</th>
                  <th className="txn-th">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {gaps.map((gap) => (
                  <PriceGapRow
                    key={gap.key}
                    gap={gap}
                    token={token}
                    householdId={householdId}
                    onChanged={invalidate}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/** Einheitenname für die Anzeige; ohne Angabe meint das Rezept Stück. */
function normalizeUnitLabel(unit: string | null | undefined): string {
  return unit?.trim() || "Stk";
}

/**
 * In welcher Basis die fehlende Zahl steht: g oder ml.
 *
 * Dieselbe Regel wie im Rechenkern — die Basis kommt von der Seite, die schon
 * metrisch ist. Steht die Packung in Litern, ist die Antwort ml; sonst g.
 */
function conversionBaseFor(gap: PriceGap): "g" | "ml" {
  const pack = (gap.price_pack_unit ?? "").trim().toLowerCase();
  return ["ml", "milliliter", "cl", "l", "liter"].includes(pack) ? "ml" : "g";
}

/**
 * Eine Lücke, direkt in der Zeile zu schließen.
 *
 * Zwei verschiedene Handgriffe hinter einer Zeile, deshalb zwei Formen:
 * „kein Preis" legt eine Preiszeile an, „Einheit passt nicht" stellt die
 * bestehende auf die Rezepteinheit um. Die Menge bleibt bewusst LEER und muss
 * eingetippt werden — sie mit der Rezeptmenge vorzubelegen wäre geraten
 * („1,99 für 500 g", obwohl die Packung ein Kilo hat), und der Fehler fiele
 * nicht auf.
 */
function PriceGapRow({
  gap,
  token,
  householdId,
  onChanged,
}: {
  gap: PriceGap;
  token: string;
  householdId: string;
  onChanged: () => void;
}) {
  const isMismatch = gap.kind === "unit_mismatch";
  const [priceEuro, setPriceEuro] = useState(
    gap.price_minor == null ? "" : amountDraft(gap.price_minor),
  );
  const [packQuantity, setPackQuantity] = useState("");
  const [packUnit, setPackUnit] = useState(gap.recipe_unit ?? "");

  const createMutation = useMutation({
    mutationFn: (input: {
      name: string;
      price_minor: number;
      pack_quantity?: string;
      pack_unit?: string;
    }) => createIngredientPrice(token, householdId, input),
    onSuccess: onChanged,
  });

  const updateMutation = useMutation({
    mutationFn: (input: { priceId: string; patch: IngredientPriceUpdateInput }) =>
      updateIngredientPrice(token, householdId, input.priceId, input.patch),
    onSuccess: onChanged,
  });

  const aliasMutation = useMutation({
    mutationFn: (input: { alias_name: string; price_name: string }) =>
      createIngredientAlias(token, householdId, input),
    onSuccess: onChanged,
  });

  const conversionMutation = useMutation({
    mutationFn: (input: UnitConversionCreateInput) =>
      createUnitConversion(token, householdId, input),
    onSuccess: onChanged,
  });

  // Die Einheit, für die die Zahl fehlt — dieselbe Wahl wie im Rechenkern:
  // die Basis kommt von der Seite, die schon metrisch ist.
  const suggestion = gap.suggested_conversion;
  const [factorDraft, setFactorDraft] = useState("");
  const factorValue = Number(factorDraft.trim().replace(",", "."));
  const factorIsValid =
    factorDraft.trim() !== "" && Number.isFinite(factorValue) && factorValue > 0;

  const saveConversion = (unit: string, factor: string, baseUnit: "g" | "ml", fromList: boolean) =>
    conversionMutation.mutate({
      ingredient_name: gap.name,
      unit,
      factor,
      base_unit: baseUnit,
      origin: fromList ? "default" : "manual",
    });

  const priceMinor = parseAmountToMinorUnits(priceEuro);
  const normalizedQuantity = packQuantity.trim().replace(",", ".");
  const quantityIsValid =
    normalizedQuantity !== "" &&
    Number.isFinite(Number(normalizedQuantity)) &&
    Number(normalizedQuantity) > 0;
  const isPending = createMutation.isPending || updateMutation.isPending || aliasMutation.isPending;
  const error = createMutation.error ?? updateMutation.error ?? aliasMutation.error;

  const submit = () => {
    if (priceMinor == null || !quantityIsValid || isPending) return;
    if (isMismatch && gap.price_id) {
      updateMutation.mutate({
        priceId: gap.price_id,
        patch: {
          price_minor: priceMinor,
          pack_quantity: normalizedQuantity,
          pack_unit: packUnit.trim() || null,
        },
      });
      return;
    }
    createMutation.mutate({
      name: gap.name,
      price_minor: priceMinor,
      pack_quantity: normalizedQuantity,
      pack_unit: packUnit.trim() || undefined,
    });
  };

  const recipeAmounts = gap.dishes
    .map(
      (dish) =>
        `${formatQuantity(dish.quantity)}${dish.unit ? ` ${dish.unit}` : ""} (${dish.dish_name})`,
    )
    .join(" · ");

  return (
    <>
      <tr className="txn-tr food-edit-row">
        <td className="txn-td">
          <span className="food-gap-name">{gap.name}</span>
          {isMismatch && (
            <span className="food-cost-gap" title="Preis vorhanden, Einheit passt nicht">
              Einheit ≠
            </span>
          )}
        </td>
        <td className="txn-td food-gap-scope">
          {gap.dish_count} {gap.dish_count === 1 ? "Gericht" : "Gerichte"}
          {gap.planned_count > 0 && (
            <span
              className="food-usage-count"
              title={`Die betroffenen Gerichte waren ${gap.planned_count}× eingeplant`}
            >
              {gap.planned_count}× geplant
            </span>
          )}
        </td>
        <td className="txn-td">
          <input
            inputMode="decimal"
            className="food-cell-input food-cell-input--mono"
            aria-label={`${gap.name}: Preis`}
            value={priceEuro}
            placeholder="1,99"
            onChange={(e) => setPriceEuro(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
          />
        </td>
        <td className="txn-td">
          <input
            inputMode="decimal"
            className="food-cell-input food-cell-input--mono"
            aria-label={`${gap.name}: Packungsmenge`}
            value={packQuantity}
            placeholder="Packung"
            onChange={(e) => setPackQuantity(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
          />
        </td>
        <td className="txn-td">
          <input
            className="food-cell-input"
            aria-label={`${gap.name}: Einheit`}
            value={packUnit}
            placeholder="kg"
            onChange={(e) => setPackUnit(e.target.value)}
          />
        </td>
        <td className="txn-td food-row-actions">
          <span
            className={`food-save-state ${error ? "food-save-state--error" : ""}`}
            role="status"
          >
            {isPending ? "Speichert …" : error ? describeError(error) : ""}
          </span>
          <button
            type="button"
            className="btn-primary food-add-button"
            disabled={priceMinor == null || !quantityIsValid || isPending}
            onClick={submit}
          >
            {isMismatch ? "Korrigieren" : "+ Preis"}
          </button>
        </td>
      </tr>
      <tr className="food-gap-context">
        <td className="txn-td" colSpan={6}>
          <span className="food-section-hint">Rezept: {recipeAmounts}</span>
          {isMismatch && (
            <span className="food-section-hint">
              {" "}
              — Preisliste steht auf {formatQuantity(gap.price_pack_quantity ?? null)}{" "}
              {gap.price_pack_unit ?? "—"}, das Rezept rechnet in „{gap.recipe_unit ?? "—"}".
              {suggestion
                ? " Es fehlt die Zahl dazwischen."
                : " Dafür braucht es eine Umrechnung dieser Zutat."}
            </span>
          )}
          {/* Die fehlende Zahl, direkt an der Lücke. Der Vorschlag kommt aus
              einer nachgeschlagenen Werteliste — gerechnet wird erst, wenn er
              bestätigt ist. */}
          {isMismatch &&
            (suggestion ? (
              <button
                type="button"
                className="food-gap-suggestion"
                disabled={isPending}
                title={`Für „${gap.name}" hinterlegen: 1 ${suggestion.unit} = ${suggestion.factor} ${suggestion.base_unit}${
                  suggestion.confidence === "grob" ? " (grober Richtwert — bitte prüfen)" : ""
                }`}
                onClick={() =>
                  saveConversion(
                    suggestion.unit,
                    String(suggestion.factor),
                    suggestion.base_unit,
                    true,
                  )
                }
              >
                1 {suggestion.unit} = {suggestion.factor} {suggestion.base_unit} übernehmen
                {suggestion.confidence === "grob" ? " (grob)" : ""}
              </button>
            ) : (
              <span className="food-gap-conversion">
                1 {normalizeUnitLabel(gap.recipe_unit)} ={" "}
                <input
                  inputMode="decimal"
                  className="form-input food-gap-conversion__input"
                  aria-label={`Umrechnung ${gap.name}`}
                  value={factorDraft}
                  placeholder="?"
                  onChange={(e) => setFactorDraft(e.currentTarget.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter" || !factorIsValid || isPending) return;
                    saveConversion(
                      gap.recipe_unit ?? "Stk",
                      String(factorValue),
                      conversionBaseFor(gap),
                      false,
                    );
                  }}
                />{" "}
                {conversionBaseFor(gap)}
                <button
                  type="button"
                  className="food-gap-suggestion"
                  disabled={!factorIsValid || isPending}
                  onClick={() =>
                    saveConversion(
                      gap.recipe_unit ?? "Stk",
                      String(factorValue),
                      conversionBaseFor(gap),
                      false,
                    )
                  }
                >
                  Merken
                </button>
              </span>
            ))}
          {/* Vorschlag, kein Automatismus: „Salz" ist der Anfang von
              „Salzkartoffeln". Übernommen wird erst per Klick. */}
          {gap.suggested_price_name && (
            <button
              type="button"
              className="food-gap-suggestion"
              disabled={isPending}
              title={`„${gap.name}" dauerhaft dem Preis „${gap.suggested_price_name}" zuordnen — statt denselben Preis ein zweites Mal anzulegen`}
              onClick={() =>
                aliasMutation.mutate({
                  alias_name: gap.name,
                  price_name: gap.suggested_price_name!,
                })
              }
            >
              Meintest du „{gap.suggested_price_name}"? Zuordnen
            </button>
          )}
        </td>
      </tr>
    </>
  );
}

/**
 * Handschätzungen, die von der Rechnung abweichen.
 *
 * Eine einmal eingetippte Zahl übersteuert die Berechnung dauerhaft — auch
 * dann noch, wenn die Preisliste inzwischen genauer ist als die Schätzung von
 * damals. Sichtbar wird das sonst nirgends, weil beide Fälle als eine Zahl in
 * derselben Spalte stehen.
 *
 * Verglichen wird NUR gegen vollständige Rechnungen. Eine Rechnung mit Lücken
 * ist bekanntermaßen zu niedrig; sie gegen die Handschätzung zu halten würde
 * lauter „Abweichungen" melden, die keine sind.
 */
function ManualEstimateCleanup({
  dishes,
  onClearOverride,
}: {
  dishes: Dish[];
  onClearOverride: (dish: Dish) => void;
}) {
  const drifted = dishes.filter(
    (dish) =>
      dish.estimated_cost_minor != null &&
      dish.computed_cost_minor != null &&
      dish.computed_cost_minor > 0 &&
      dish.unpriced_ingredients === 0 &&
      Math.abs(dish.estimated_cost_minor - dish.computed_cost_minor) / dish.computed_cost_minor >
        0.2,
  );
  if (drifted.length === 0) return null;

  return (
    <div className="food-gap-panel food-gap-panel--drift">
      <p>
        <strong>
          {drifted.length} {drifted.length === 1 ? "Gericht rechnet" : "Gerichte rechnen"} anders,
          als die Handschätzung sagt
        </strong>{" "}
        — die getippte Zahl gilt weiter, solange sie dasteht.
      </p>
      <ul className="food-drift-list">
        {drifted.map((dish) => (
          <li key={dish.id}>
            <span className="food-gap-name">{dish.name}</span>
            <span className="food-section-hint">
              Hand {formatMinorUnitsAsEuro(dish.estimated_cost_minor!)} · Rechnung{" "}
              {formatMinorUnitsAsEuro(dish.computed_cost_minor!)}
            </span>
            <button
              type="button"
              className="food-row-button"
              title="Handschätzung verwerfen — danach gilt der berechnete Wert und folgt jeder Preisänderung"
              onClick={() => onClearOverride(dish)}
            >
              Rechnung übernehmen
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Grundpreis lesbar machen: „1,78 €/kg", „0,46 €/Stk".
 *
 * unit_price_micro sind Millionstel Cent je Basiseinheit (Gramm bzw.
 * Milliliter). In ganzen Cent gerechnet wären 0,199 ct/g schlicht 0 — deshalb
 * die feine Auflösung in der Datenbank und die Umrechnung erst hier, wo eine
 * Zahl für Menschen entsteht. Erst der Grundpreis macht „1,99 für 1 kg" und
 * „1,29 für 500 g" vergleichbar; genau das rechnet im Laden niemand im Kopf.
 */
function formatUnitPrice(micro: number, baseFamily: string): string {
  if (baseFamily === "masse") return `${formatMinorUnitsAsEuro(Math.round(micro / 1000))}/kg`;
  if (baseFamily === "volumen") return `${formatMinorUnitsAsEuro(Math.round(micro / 1000))}/l`;
  const unit = baseFamily === "stk" ? "Stk" : baseFamily;
  return `${formatMinorUnitsAsEuro(Math.round(micro / 1_000_000))}/${unit}`;
}

// Wie viele Vorschauzeilen tatsächlich gezeichnet werden. Eine Prospektdatei
// über mehrere Händler ist schnell vierstellig; jede Zeile als eigenes Label
// mit Checkbox zu rendern bringt den Browser zum Stehen. Ausgewählt und
// übernommen wird trotzdem alles — gedeckelt ist nur die Darstellung.
const SHEET_PREVIEW_RENDER_LIMIT = 300;

const VALUATION_SOURCE_LABELS: Record<string, string> = {
  manual: "von Hand",
  pinned: "festgenagelt",
  derived: "abgeleitet",
};

/**
 * Excel-Austausch für die Preisliste.
 *
 * Die Datei ist Transport, nicht Speicher: Vorlage herunterladen, außerhalb
 * befüllen (von Hand, oder von einem beliebigen Modell vorstrukturiert),
 * wieder einlesen. Dadurch braucht dieses System weder Bilderkennung noch
 * Dateiablage — und die Beschaffung bleibt ein Problem, das man lösen kann,
 * wo es einem passt.
 *
 * Der Import hängt an, er ersetzt nichts. Deshalb entsteht mit jeder Woche ein
 * Stück Preisverlauf, ohne dass jemand etwas dafür tun muss.
 */
function PriceSheetPanel({
  token,
  householdId,
  onImported,
}: {
  token: string;
  householdId: string;
  onImported: () => void;
}) {
  const [preview, setPreview] = useState<PriceSheetPreview | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());

  const downloadMutation = useMutation({
    mutationFn: () => downloadPriceSheetTemplate(token, householdId),
  });

  const previewMutation = useMutation({
    mutationFn: (file: File) => previewPriceSheet(token, householdId, file),
    onSuccess: (result) => {
      setPreview(result);
      // Vorbelegt angehakt ist nur, was auch wirklich etwas bewirkt: eine
      // bereits vorhandene Sichtung anzuhaken würde mehr versprechen, als das
      // Übernehmen einlöst.
      setChosen(new Set(result.rows.filter((row) => !row.is_duplicate).map((r) => r.row_number)));
    },
  });

  const applyMutation = useMutation({
    mutationFn: (rows: PriceSheetPreviewRow[]) =>
      createPriceObservationsBulk(
        token,
        householdId,
        rows.map((row): PriceObservationInput => ({
          ingredient_name: row.ingredient_name,
          price_minor: row.price_minor,
          pack_quantity: row.pack_quantity,
          pack_unit: row.pack_unit,
          store_name: row.store_name,
          store_location: row.store_location,
          brand: row.brand,
          is_organic: row.quality_tier === "organic",
          is_promotion: row.is_promotion,
          offer_valid_from: row.offer_valid_from,
          offer_valid_to: row.offer_valid_to,
          observed_on: row.observed_on,
          note: row.note,
        })),
        preview?.file_name ?? undefined,
      ),
    onSuccess: () => {
      setPreview(null);
      setChosen(new Set());
      onImported();
    },
  });

  const toggle = (rowNumber: number) => {
    setChosen((previous) => {
      const next = new Set(previous);
      if (next.has(rowNumber)) next.delete(rowNumber);
      else next.add(rowNumber);
      return next;
    });
  };

  const result = applyMutation.data;

  return (
    <div className="food-import-callout food-import-callout--active">
      <div className="food-sheet-actions">
        <button
          type="button"
          className="btn-secondary"
          disabled={downloadMutation.isPending}
          onClick={() => downloadMutation.mutate()}
        >
          {downloadMutation.isPending ? "Wird erzeugt …" : "Vorlage herunterladen"}
        </button>
        <label className="btn-primary food-sheet-upload">
          {previewMutation.isPending ? "Wird gelesen …" : "Ausgefüllte Datei einlesen"}
          <input
            type="file"
            accept=".xlsx"
            disabled={previewMutation.isPending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              // Zurücksetzen, damit dieselbe Datei nach einer Korrektur erneut
              // gewählt werden kann — sonst feuert change kein zweites Mal.
              event.target.value = "";
              if (file) previewMutation.mutate(file);
            }}
          />
        </label>
      </div>
      <p id="food-import-note">
        Die Vorlage bringt alle bekannten Zutaten schon als Zeilen mit — eintragen musst du nur
        Preis, Laden und Datum. Eingelesene Zeilen werden <strong>angehängt</strong>, nicht ersetzt:
        daraus entsteht der Preisverlauf.
      </p>

      {previewMutation.isError && (
        <p className="food-fill-note food-fill-note--error" role="status">
          {describeError(previewMutation.error)}
        </p>
      )}

      {result && (
        <p className="food-fill-note" role="status">
          {result.inserted} Sichtung{result.inserted === 1 ? "" : "en"} übernommen
          {result.created_price_rows > 0 &&
            `, ${result.created_price_rows} neue Zutat(en) angelegt`}
          {result.skipped_duplicates > 0 && `, ${result.skipped_duplicates} bereits vorhanden`}.{" "}
          {result.updated_valuations > 0
            ? `${result.updated_valuations} Preis(e) neu berechnet.`
            : "Kein Bewertungspreis geändert."}
          {result.blocked_by_manual > 0 && (
            <>
              {" "}
              <strong>
                {result.blocked_by_manual} Zutat(en) rechnen weiter mit ihrem von Hand getippten
                Preis.
              </strong>{" "}
              In der Spalte „Herkunft" auf „abgeleitet" stellen, damit die Sichtungen greifen.
            </>
          )}
        </p>
      )}

      {preview && (
        <div className="food-proposal">
          <div className="food-proposal__head">
            <strong>Vorschau — noch nichts gespeichert</strong>
            <span className="food-section-hint">
              Blatt „{preview.sheet_name}", Kopfzeile {preview.header_row}. Abwählen was nicht
              passt, dann übernehmen.
            </span>
          </div>

          {preview.rows.length === 0 && (
            <p className="food-fill-note">
              Keine ausgefüllte Zeile gefunden. Preis, Packung und Einheit müssen stehen.
            </p>
          )}

          {preview.rows.length > SHEET_PREVIEW_RENDER_LIMIT && (
            <p className="food-fill-note">
              Es werden die ersten {SHEET_PREVIEW_RENDER_LIMIT} von {preview.rows.length} Zeilen
              angezeigt — {preview.rows.length} Zeilen einzeln zu zeichnen würde die Seite
              lahmlegen. <strong>Übernommen werden trotzdem alle angehakten</strong>, auch die hier
              nicht sichtbaren.
            </p>
          )}

          {preview.rows.slice(0, SHEET_PREVIEW_RENDER_LIMIT).map((row) => {
            const jump =
              row.previous_unit_price_micro != null && row.previous_unit_price_micro > 0
                ? row.unit_price_micro / row.previous_unit_price_micro
                : null;
            return (
              <label key={row.row_number} className="food-proposal__row">
                <input
                  type="checkbox"
                  checked={chosen.has(row.row_number)}
                  onChange={() => toggle(row.row_number)}
                  aria-label={`Zeile ${row.row_number}, ${row.ingredient_name} übernehmen`}
                />
                <span className="food-proposal__name">
                  {row.ingredient_name}
                  {row.ingredient_is_new && (
                    <span className="status-badge status-badge--neutral">neu</span>
                  )}
                </span>
                <span className="food-proposal__amount">
                  {formatMinorUnitsAsEuro(row.price_minor)}
                  <span className="food-section-hint">
                    {" "}
                    {formatUnitPrice(row.unit_price_micro, row.base_family)}
                  </span>
                </span>
                <span className="food-section-hint">{row.store_name ?? "irgendwo"}</span>
                {row.is_promotion && (
                  <span className="status-badge status-badge--warning">
                    Angebot bis {row.offer_valid_to ? formatShortDate(row.offer_valid_to) : "—"}
                  </span>
                )}
                {row.quality_tier === "organic" && (
                  <span className="status-badge status-badge--neutral">Bio</span>
                )}
                {row.is_duplicate && (
                  <span className="status-badge status-badge--neutral">schon erfasst</span>
                )}
                {/* Ein Zahlendreher fällt hier auf, nicht erst in der
                    Wochensumme. Grenze bewusst großzügig: Lebensmittelpreise
                    schwanken echt, ein Faktor 3 tut es selten. */}
                {jump != null && (jump > 3 || jump < 0.34) && (
                  <span
                    className="food-cost-gap"
                    title={`Zuletzt ${formatUnitPrice(row.previous_unit_price_micro ?? 0, row.base_family)} — bitte prüfen`}
                  >
                    ungewöhnlich
                  </span>
                )}
              </label>
            );
          })}

          {(preview.problems.length > 0 || preview.untouched_rows > 0) && (
            <p className="food-fill-note">
              {preview.untouched_rows > 0 &&
                `${preview.untouched_rows} Vorlagenzeile(n) ohne Eintrag übersprungen. `}
              {preview.problems.length > 0 && (
                <>
                  <strong>{preview.problems.length} Zeile(n) nicht lesbar:</strong>{" "}
                  {preview.problems
                    .slice(0, 3)
                    .map((p) => `Z${p.row_number} ${p.ingredient_name ?? ""} — ${p.message}`)
                    .join(" · ")}
                  {preview.problems.length > 3 && ` … und ${preview.problems.length - 3} weitere`}
                </>
              )}
            </p>
          )}

          {preview.unmapped_headers.length > 0 && (
            <p className="food-fill-note">
              Nicht zugeordnete Spalten: {preview.unmapped_headers.join(", ")}. Deren Inhalt wurde
              ignoriert.
            </p>
          )}

          <div className="food-proposal__actions">
            <button
              type="button"
              className="btn-primary"
              disabled={chosen.size === 0 || applyMutation.isPending}
              onClick={() =>
                applyMutation.mutate(preview.rows.filter((r) => chosen.has(r.row_number)))
              }
            >
              {applyMutation.isPending ? "Übernimmt …" : `${chosen.size} übernehmen`}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setPreview(null)}>
              Verwerfen
            </button>
            {applyMutation.isError && (
              <span className="food-save-state food-save-state--error" role="status">
                {describeError(applyMutation.error)}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Aufklappzeile zu einer Zutat: woher die geltende Zahl kommt, was die Läden
 * verlangen, und der Verlauf.
 *
 * Der Ladenvergleich läuft über den Grundpreis, nicht über den Packungspreis —
 * sonst sähe ein 12er-Kasten für 6,99 € günstiger aus als ein 6er für 3,99 €.
 * Verglichen wird nur innerhalb derselben Einheitenfamilie; „500 g" gegen
 * „1 Pkg" wäre geraten und wird deshalb gar nicht erst nebeneinandergestellt.
 */
function IngredientPriceDetailPanel({
  token,
  householdId,
  price,
  onChanged,
}: {
  token: string;
  householdId: string;
  price: IngredientPrice;
  onChanged: () => void;
}) {
  const ingredientKey = price.name.trim().toLowerCase();
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["ingredient-price-detail", householdId, ingredientKey],
    queryFn: () => getIngredientPriceDetail(token, householdId, ingredientKey),
    enabled: Boolean(token && householdId),
  });

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ["ingredient-price-detail", householdId, ingredientKey],
    });
    onChanged();
  };

  const valuationMutation = useMutation({
    mutationFn: (input: Parameters<typeof updateIngredientValuation>[3]) =>
      updateIngredientValuation(token, householdId, ingredientKey, input),
    onSuccess: refresh,
  });

  const deleteMutation = useMutation({
    mutationFn: (observationId: string) =>
      deletePriceObservation(token, householdId, observationId),
    onSuccess: refresh,
  });

  if (isLoading) {
    return <p className="food-fill-note">Preisherkunft wird geladen …</p>;
  }
  if (isError || !data) {
    return (
      <p className="food-fill-note food-fill-note--error">
        Preisherkunft konnte nicht geladen werden.
      </p>
    );
  }

  const { valuation, matrix, history } = data;
  const cheapest = data.cheapest_unit_price_micro;

  return (
    <div className="food-price-detail">
      <div className="food-price-detail__controls">
        <label className="form-field food-price-detail__field">
          <span className="form-label">Bewertungspreis</span>
          <select
            className="form-input"
            value={valuation.price_source}
            onChange={(e) =>
              valuationMutation.mutate({
                price_source: e.target.value as "manual" | "derived",
                ...(e.target.value === "derived" && !valuation.price_strategy
                  ? { price_strategy: "latest" as const }
                  : {}),
              })
            }
          >
            <option value="manual">von Hand — getippte Zahl gilt</option>
            <option value="derived">abgeleitet — aus den Sichtungen</option>
            {valuation.price_source === "pinned" && (
              <option value="pinned">festgenagelt auf eine Sichtung</option>
            )}
          </select>
        </label>

        {valuation.price_source === "derived" && (
          <label className="form-field food-price-detail__field">
            <span className="form-label">Regel</span>
            <select
              className="form-input"
              value={valuation.price_strategy ?? "latest"}
              onChange={(e) =>
                valuationMutation.mutate({
                  price_strategy: e.target.value as "latest" | "cheapest",
                })
              }
            >
              <option value="latest">zuletzt gesehen</option>
              <option value="cheapest">günstigster Grundpreis</option>
            </select>
          </label>
        )}

        <p className="food-section-hint food-price-detail__note">
          Angebote fließen nie in den Bewertungspreis — ein Gericht wird nicht dauerhaft billiger,
          weil etwas diese Woche im Prospekt steht.
          {valuation.price_is_stale && (
            <>
              {" "}
              <span className="food-cost-gap" title="Keine frische Sichtung innerhalb der Frist">
                Wert ist alt
              </span>
            </>
          )}
        </p>
      </div>

      {matrix.length === 0 ? (
        <p className="food-fill-note">
          Noch keine Sichtung. Über „Vorlage herunterladen" oben eine Excel füllen und einlesen —
          danach steht hier der Vergleich zwischen den Läden.
        </p>
      ) : (
        <>
          <table className="txn-table food-price-matrix">
            <caption className="food-section-hint">
              Aktueller Stand je Laden — verglichen wird der Grundpreis, nicht der Packungspreis.
            </caption>
            <thead>
              <tr>
                <th className="txn-th">Laden</th>
                <th className="txn-th">Qualität</th>
                <th className="txn-th">Packung</th>
                <th className="txn-th">Preis</th>
                <th className="txn-th">Grundpreis</th>
                <th className="txn-th">gesehen</th>
              </tr>
            </thead>
            <tbody>
              {matrix.map((cell) => (
                <tr
                  key={cell.observation_id}
                  className={`txn-tr ${cell.is_current_valuation ? "food-price-matrix__current" : ""}`}
                >
                  <td className="txn-td">
                    {cell.store_label}
                    {cell.is_current_valuation && (
                      <span className="status-badge status-badge--neutral">rechnet</span>
                    )}
                  </td>
                  <td className="txn-td">
                    {cell.quality_tier === "organic" ? "Bio" : "normal"}
                    {cell.brand_tier === "branded" && " · Marke"}
                    {cell.brand_tier === "private_label" && " · Eigenmarke"}
                  </td>
                  <td className="txn-td">
                    {formatQuantity(cell.pack_quantity)} {cell.pack_unit ?? ""}
                  </td>
                  <td className="txn-td food-cell-input--mono">
                    {formatMinorUnitsAsEuro(cell.price_minor)}
                    {cell.is_promotion && (
                      <span className="status-badge status-badge--warning">
                        Angebot bis{" "}
                        {cell.offer_valid_to ? formatShortDate(cell.offer_valid_to) : "—"}
                      </span>
                    )}
                  </td>
                  <td className="txn-td food-cell-input--mono">
                    {formatUnitPrice(cell.unit_price_micro, data.base_family ?? "stk")}
                    {cheapest != null && cell.unit_price_micro === cheapest && (
                      <span className="status-badge status-badge--positive">günstigster</span>
                    )}
                  </td>
                  <td className="txn-td">{formatShortDate(cell.observed_on)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <details className="food-price-history">
            <summary>Verlauf ({history.length} Sichtungen)</summary>
            <ul className="food-price-history__list">
              {history.map((entry) => (
                <li key={entry.id}>
                  <span className="food-cell-input--mono">
                    {formatShortDate(entry.observed_on)}
                  </span>{" "}
                  {entry.store_label ?? "irgendwo"} · {formatMinorUnitsAsEuro(entry.price_minor)}{" "}
                  für {formatQuantity(entry.pack_quantity)} {entry.pack_unit ?? ""} ={" "}
                  {formatUnitPrice(entry.unit_price_micro, entry.base_family)}
                  {entry.is_promotion && " · Angebot"}
                  {entry.brand && ` · ${entry.brand}`}
                  <span className="food-section-hint"> ({entry.source})</span>
                  <button
                    type="button"
                    className="food-delete-button"
                    aria-label={`Sichtung vom ${entry.observed_on} löschen`}
                    title="Eine Korrektur ist eine neue Sichtung — löschen nur bei Tippfehlern"
                    onClick={() => deleteMutation.mutate(entry.id)}
                  >
                    Löschen
                  </button>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </div>
  );
}

/**
 * Zutaten-Preisliste des Haushalts.
 *
 * Der Preis hängt an der Zutat, nicht am Gericht: „Nudeln kosten 1,99 € je kg"
 * gilt in jedem Rezept gleich, und ein hier geänderter Preis wirkt sofort in
 * allen Gerichten, die diese Zutat verwenden. Zugeordnet wird über den Namen,
 * Groß- und Kleinschreibung egal.
 *
 * Preis gilt immer für eine Packung, so wie sie im Laden steht — deshalb
 * Menge und Einheit daneben. Erst daraus entsteht der Grundpreis, mit dem eine
 * Rezeptmenge bewertet wird.
 */
function IngredientPriceList({ token, householdId }: { token: string; householdId: string }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [priceEuro, setPriceEuro] = useState("");
  const [packQuantity, setPackQuantity] = useState("1");
  const [packUnit, setPackUnit] = useState("");
  const [category, setCategory] = useState("");
  // Immer nur EINE Zutat aufgeklappt: die Detailansicht ist breit, und zwei
  // nebeneinander helfen bei einem Ladenvergleich nicht weiter.
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: prices, isLoading } = useQuery<IngredientPrice[]>({
    queryKey: ["ingredient-prices", householdId],
    queryFn: () => listIngredientPrices(token, householdId),
    enabled: Boolean(token && householdId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["ingredient-prices", householdId] });
    // Gerichtskosten und Zutatenkosten hängen daran.
    void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
    void queryClient.invalidateQueries({ queryKey: ["dish-ingredients"] });
  };

  const createMutation = useMutation({
    mutationFn: (input: {
      name: string;
      price_minor: number;
      pack_quantity?: string;
      pack_unit?: string;
      category?: string;
    }) => createIngredientPrice(token, householdId, input),
    onSuccess: () => {
      invalidate();
      setName("");
      setPriceEuro("");
      setPackQuantity("1");
      setPackUnit("");
      setCategory("");
    },
  });

  const updateMutation = useMutation({
    mutationFn: (input: { priceId: string; patch: IngredientPriceUpdateInput }) =>
      updateIngredientPrice(token, householdId, input.priceId, input.patch),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (priceId: string) => deleteIngredientPrice(token, householdId, priceId),
    onSuccess: invalidate,
  });

  const commitField = (
    price: IngredientPrice,
    field: keyof IngredientPriceUpdateInput,
    rawValue: string,
  ) => {
    let value: string | number | null;
    if (field === "price_minor") {
      const parsed = parseAmountToMinorUnits(rawValue);
      if (parsed == null) return;
      value = parsed;
    } else if (field === "pack_quantity") {
      const normalized = rawValue.trim().replace(",", ".");
      if (!Number.isFinite(Number(normalized)) || Number(normalized) <= 0) return;
      value = normalized;
    } else if (field === "name") {
      value = rawValue.trim();
      if (!value) return;
    } else {
      value = rawValue.trim() || null;
    }

    const previous = price[field as keyof IngredientPrice];
    if (value === previous || (value == null && previous == null)) return;
    updateMutation.mutate({ priceId: price.id, patch: { [field]: value } });
  };

  const duplicate = (prices ?? []).some(
    (price) => price.name.trim().toLowerCase() === name.trim().toLowerCase(),
  );

  const submit = () => {
    const trimmed = name.trim();
    const priceMinor = parseAmountToMinorUnits(priceEuro);
    if (!trimmed || duplicate || priceMinor == null) return;
    const quantity = packQuantity.trim().replace(",", ".") || "1";
    createMutation.mutate({
      name: trimmed,
      price_minor: priceMinor,
      pack_quantity: quantity,
      pack_unit: packUnit.trim() || undefined,
      category: category.trim() || undefined,
    });
  };

  return (
    <section aria-labelledby="price-list-title">
      <div className="food-section-toolbar">
        <div>
          <h2 id="price-list-title" className="food-section-title">
            Zutaten-Preisliste
          </h2>
          <p className="food-section-hint">
            Preis einmal je Zutat — er gilt dann in jedem Gericht. „1,99 € für 1 kg" bewertet ein
            Rezept mit 500 g automatisch mit 1,00 €. Gramm/Kilo und Milliliter/Liter werden
            umgerechnet; Stück, Packung oder Dose müssen im Rezept genauso heißen.
          </p>
        </div>
        <span className="food-keyboard-hint">Tab · Enter · Esc</span>
      </div>

      <PriceSheetPanel token={token} householdId={householdId} onImported={invalidate} />

      {/* Hier aufgeklappt statt zusammengefaltet: im Preise-Tab ist das
          Schließen der Lücken die Arbeit, nicht ein Hinweis am Rand. */}
      <PriceGapPanel token={token} householdId={householdId} defaultOpen />

      <div className="txn-table-wrapper food-table-wrap">
        <table className="txn-table food-ledger food-ledger--prices">
          <thead>
            <tr>
              <th className="txn-th">Zutat</th>
              <th className="txn-th">Preis</th>
              <th className="txn-th">für Menge</th>
              <th className="txn-th">Einheit</th>
              <th className="txn-th">Kategorie</th>
              <th className="txn-th">Gerichte</th>
              <th className="txn-th">Herkunft</th>
              <th className="txn-th">Status / Aktion</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td className="food-table-message" colSpan={8}>
                  Preise werden geladen …
                </td>
              </tr>
            )}
            {!isLoading && (prices ?? []).length === 0 && (
              <tr>
                <td className="food-table-message" colSpan={8}>
                  Noch keine Preise — unten eintragen. Danach rechnen sich die Gerichtskosten von
                  selbst.
                </td>
              </tr>
            )}
            {(prices ?? []).map((price) => {
              const rowIsSaving =
                updateMutation.isPending && updateMutation.variables?.priceId === price.id;
              const rowHasError =
                updateMutation.isError && updateMutation.variables?.priceId === price.id;
              const fieldProps = (original: string) => ({
                "data-original": original,
                onKeyDown: handleCellKeyDown,
              });
              const isExpanded = expandedId === price.id;
              return (
                <Fragment key={price.id}>
                  <tr className="txn-tr food-edit-row">
                    <td className="txn-td">
                      <input
                        className="food-cell-input food-cell-input--strong"
                        aria-label={`${price.name}: Zutat`}
                        defaultValue={price.name}
                        onBlur={(e) => commitField(price, "name", e.currentTarget.value)}
                        {...fieldProps(price.name)}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        inputMode="decimal"
                        className="food-cell-input food-cell-input--mono"
                        aria-label={`${price.name}: Preis`}
                        defaultValue={amountDraft(price.price_minor)}
                        placeholder="0,00"
                        onBlur={(e) => commitField(price, "price_minor", e.currentTarget.value)}
                        {...fieldProps(amountDraft(price.price_minor))}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        inputMode="decimal"
                        className="food-cell-input food-cell-input--mono"
                        aria-label={`${price.name}: Packungsmenge`}
                        defaultValue={formatQuantity(price.pack_quantity)}
                        onBlur={(e) => commitField(price, "pack_quantity", e.currentTarget.value)}
                        {...fieldProps(formatQuantity(price.pack_quantity))}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        className="food-cell-input"
                        aria-label={`${price.name}: Einheit`}
                        defaultValue={price.pack_unit ?? ""}
                        placeholder="kg, g, l, Stk"
                        onBlur={(e) => commitField(price, "pack_unit", e.currentTarget.value)}
                        {...fieldProps(price.pack_unit ?? "")}
                      />
                    </td>
                    <td className="txn-td">
                      <input
                        className="food-cell-input"
                        aria-label={`${price.name}: Kategorie`}
                        defaultValue={price.category ?? ""}
                        placeholder="z. B. Trockenware"
                        onBlur={(e) => commitField(price, "category", e.currentTarget.value)}
                        {...fieldProps(price.category ?? "")}
                      />
                    </td>
                    <td className="txn-td food-usage-cell">
                      <span
                        className="food-usage-count"
                        title={`In ${price.used_in_dishes} Gericht${price.used_in_dishes === 1 ? "" : "en"} verwendet`}
                      >
                        {price.used_in_dishes}×
                      </span>
                    </td>
                    <td className="txn-td">
                      <button
                        type="button"
                        className="food-origin-button"
                        aria-expanded={isExpanded}
                        title={
                          price.price_source === "manual"
                            ? "Von Hand getippt — Importe fassen diese Zahl nicht an"
                            : "Aus den Sichtungen abgeleitet"
                        }
                        onClick={() => setExpandedId(isExpanded ? null : price.id)}
                      >
                        {VALUATION_SOURCE_LABELS[price.price_source] ?? price.price_source}
                        {price.price_observed_on &&
                          ` · ${formatShortDate(price.price_observed_on)}`}
                        {price.price_is_stale && (
                          <span className="food-cost-gap" title="Keine frische Sichtung">
                            alt
                          </span>
                        )}
                        <span aria-hidden="true">{isExpanded ? " ▴" : " ▾"}</span>
                      </button>
                    </td>
                    <td className="txn-td food-row-actions">
                      <span
                        className={`food-save-state ${rowHasError ? "food-save-state--error" : ""}`}
                        role="status"
                      >
                        {rowIsSaving
                          ? "Speichert …"
                          : rowHasError
                            ? describeError(updateMutation.error)
                            : "Gespeichert"}
                      </span>
                      <button
                        type="button"
                        className="food-delete-button"
                        aria-label={`Preis für ${price.name} löschen`}
                        title="Nur der Preis wird gelöscht, die Zutat bleibt in jedem Rezept stehen"
                        onClick={() => deleteMutation.mutate(price.id)}
                      >
                        Löschen
                      </button>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="txn-tr food-detail-row">
                      <td className="txn-td" colSpan={8}>
                        <IngredientPriceDetailPanel
                          token={token}
                          householdId={householdId}
                          price={price}
                          onChanged={invalidate}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            <tr className="food-add-row">
              <td className="txn-td">
                <input
                  className="food-cell-input food-cell-input--strong"
                  aria-label="Neuer Preis: Zutat"
                  value={name}
                  placeholder="Zutat hinzufügen …"
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      submit();
                    }
                  }}
                />
              </td>
              <td className="txn-td">
                <input
                  inputMode="decimal"
                  className="food-cell-input food-cell-input--mono"
                  aria-label="Neuer Preis: Preis"
                  value={priceEuro}
                  placeholder="1,99"
                  onChange={(e) => setPriceEuro(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  inputMode="decimal"
                  className="food-cell-input food-cell-input--mono"
                  aria-label="Neuer Preis: Packungsmenge"
                  value={packQuantity}
                  onChange={(e) => setPackQuantity(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  className="food-cell-input"
                  aria-label="Neuer Preis: Einheit"
                  value={packUnit}
                  placeholder="kg"
                  onChange={(e) => setPackUnit(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  className="food-cell-input"
                  aria-label="Neuer Preis: Kategorie"
                  value={category}
                  placeholder="Kategorie"
                  onChange={(e) => setCategory(e.target.value)}
                />
              </td>
              <td className="txn-td" />
              <td className="txn-td" />
              <td className="txn-td food-row-actions">
                <span
                  className={`food-save-state ${createMutation.isError || duplicate ? "food-save-state--error" : ""}`}
                  role="status"
                >
                  {createMutation.isPending
                    ? "Wird angelegt …"
                    : duplicate
                      ? "Gibt es schon"
                      : createMutation.isError
                        ? describeError(createMutation.error)
                        : "Neue Zeile"}
                </span>
                <button
                  type="button"
                  className="btn-primary food-add-button"
                  disabled={!name.trim() || duplicate || createMutation.isPending}
                  onClick={submit}
                >
                  + Hinzufügen
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * Wochenplan-Assistent: Freitext-Wunsch rein, Vorschlag aus dem eigenen
 * Gerichte-Katalog raus.
 *
 * Der Vorschlag lebt nur im State dieser Komponente. Erst "Übernehmen"
 * schreibt, und der Server überspringt dabei jeden Slot, der schon belegt ist
 * — ein Vorschlag kann also nichts überschreiben, was von Hand geplant wurde.
 */
function WeekPlanAssistant({
  mealWeekId,
  weekLabel,
  dishCount,
  token,
  householdId,
}: {
  mealWeekId: string;
  weekLabel: string;
  dishCount: number;
  token: string;
  householdId: string;
}) {
  const queryClient = useQueryClient();
  const [isOpen, setIsOpen] = useState(false);
  const [wish, setWish] = useState("");
  const [servings, setServings] = useState("2");
  const [types, setTypes] = useState<MealType[]>(["dinner"]);
  const [plan, setPlan] = useState<WeekPlanSuggestionResult | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());

  const slotKey = (meal: MealSuggestion) => `${meal.meal_date}|${meal.meal_type}`;

  const suggestMutation = useMutation({
    mutationFn: () =>
      suggestWeekPlan(token, householdId, mealWeekId, {
        wish: wish.trim() || null,
        meal_types: types,
        servings: Number(servings) || 2,
        skip_planned_days: true,
      }),
    onSuccess: (result) => {
      setPlan(result);
      setChosen(new Set(result.meals.map(slotKey)));
    },
  });

  const applyMutation = useMutation({
    mutationFn: (meals: MealSuggestion[]) =>
      createMealsBulk(token, householdId, {
        meal_week_id: mealWeekId,
        meals: meals.map((meal) => ({
          meal_date: meal.meal_date,
          meal_type: meal.meal_type,
          dish_name: meal.dish_name,
          dish_id: meal.dish_id,
          servings: meal.servings,
          estimated_cost_minor: meal.estimated_cost_minor,
        })),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meals", mealWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", mealWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
      void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
      setPlan(null);
      setChosen(new Set());
    },
  });

  const toggleType = (type: MealType) => {
    setTypes((current) =>
      current.includes(type)
        ? current.filter((entry) => entry !== type)
        : [...current, type].sort(
            (a, b) =>
              (Object.keys(MEAL_TYPE_LABELS) as MealType[]).indexOf(a) -
              (Object.keys(MEAL_TYPE_LABELS) as MealType[]).indexOf(b),
          ),
    );
  };

  const chosenMeals = (plan?.meals ?? []).filter((meal) => chosen.has(slotKey(meal)));
  const chosenTotalMinor = chosenMeals.reduce(
    (sum, meal) => sum + (meal.estimated_cost_minor ?? 0),
    0,
  );

  if (!isOpen) {
    return (
      <div className="food-assistant food-assistant--collapsed">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setIsOpen(true)}
          disabled={dishCount === 0}
          title={
            dishCount === 0
              ? "Erst Gerichte im Katalog anlegen — die KI wählt nur daraus"
              : "Wochenplan aus dem eigenen Katalog vorschlagen lassen"
          }
        >
          ✨ Wochenplan vorschlagen
        </button>
        {dishCount === 0 && (
          <span className="food-section-hint">
            Erst Gerichte anlegen — vorgeschlagen wird ausschließlich aus deinem Katalog.
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="card food-assistant">
      <div className="food-assistant__head">
        <div>
          <h3 className="card-heading">Wochenplan vorschlagen · {weekLabel}</h3>
          <p className="food-section-hint">
            Gewählt wird ausschließlich aus deinen {dishCount} Gerichten. Bereits geplante
            Mahlzeiten bleiben unangetastet.
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={() => setIsOpen(false)}>
          Schließen
        </button>
      </div>

      <div className="food-assistant__controls">
        <div className="form-field" style={{ flex: "2 1 260px" }}>
          <label className="form-label" htmlFor="plan-wish">
            Wunsch
          </label>
          <input
            id="plan-wish"
            type="text"
            className="form-input"
            value={wish}
            placeholder="z. B. italienisch, günstig, Geburtstag am Samstag"
            onChange={(e) => setWish(e.target.value)}
          />
        </div>
        <div className="form-field" style={{ flex: "0 0 110px" }}>
          <label className="form-label" htmlFor="plan-servings">
            Portionen
          </label>
          <input
            id="plan-servings"
            type="number"
            min={1}
            max={20}
            className="form-input form-input--mono"
            value={servings}
            onChange={(e) => setServings(e.target.value)}
          />
        </div>
        <div className="form-field" style={{ flex: "1 1 auto" }}>
          <span className="form-label">Mahlzeiten</span>
          <div className="direction-toggle">
            {(Object.keys(MEAL_TYPE_LABELS) as MealType[]).map((type) => (
              <button
                key={type}
                type="button"
                className={`direction-btn ${types.includes(type) ? "direction-btn--active" : ""}`}
                aria-pressed={types.includes(type)}
                onClick={() => toggleType(type)}
              >
                {MEAL_TYPE_LABELS[type]}
              </button>
            ))}
          </div>
        </div>
        <button
          type="button"
          className="btn-primary"
          disabled={types.length === 0 || suggestMutation.isPending}
          onClick={() => suggestMutation.mutate()}
        >
          {suggestMutation.isPending ? "Plant …" : "Vorschlagen"}
        </button>
      </div>

      {suggestMutation.isError && (
        <AiErrorNotice
          className="food-fill-note food-fill-note--error"
          role="status"
          error={suggestMutation.error}
          fallback={describeError(suggestMutation.error)}
        />
      )}

      {plan && (
        <div className="food-proposal">
          <div className="food-proposal__head">
            <strong>Vorschlag — noch nicht gespeichert</strong>
            <span className="food-section-hint">
              Kosten sind die hinterlegten Schätzungen deiner Gerichte, hochgerechnet auf die
              Portionen — keine Zahl davon stammt vom Modell.
            </span>
          </div>

          {plan.meals.length === 0 && (
            <p className="food-fill-note">
              Kein verwertbarer Vorschlag. Entweder ist die Woche schon voll geplant, oder zum
              Wunsch passte nichts aus dem Katalog.
            </p>
          )}

          {plan.meals.map((meal) => (
            <label key={slotKey(meal)} className="food-proposal__row">
              <input
                type="checkbox"
                checked={chosen.has(slotKey(meal))}
                onChange={() =>
                  setChosen((current) => {
                    const next = new Set(current);
                    const key = slotKey(meal);
                    if (next.has(key)) next.delete(key);
                    else next.add(key);
                    return next;
                  })
                }
                aria-label={`${meal.dish_name} am ${meal.meal_date} übernehmen`}
              />
              <span className="food-proposal__amount">{formatShortDate(meal.meal_date)}</span>
              <span className="food-section-hint">{MEAL_TYPE_LABELS[meal.meal_type]}</span>
              <span className="food-proposal__name">
                {meal.dish_name} · {meal.servings} Port.
              </span>
              <span className="food-proposal__amount">
                {meal.estimated_cost_minor == null
                  ? "—"
                  : formatMinorUnitsAsEuro(meal.estimated_cost_minor)}
              </span>
              {meal.reason && <span className="food-section-hint">{meal.reason}</span>}
            </label>
          ))}

          {plan.meals.length > 0 && (
            <p className="food-fill-note">
              Ausgewählt: {formatMinorUnitsAsEuro(chosenTotalMinor)}
              {plan.budget_minor > 0 && (
                <>
                  {" "}
                  von {formatMinorUnitsAsEuro(plan.budget_minor)} Wochenbudget
                  {chosenTotalMinor > plan.budget_minor && " — über Budget"}
                </>
              )}
              .
            </p>
          )}
          {plan.discarded_suggestions > 0 && (
            <p className="food-fill-note">
              {plan.discarded_suggestions} Antwort
              {plan.discarded_suggestions === 1 ? "" : "en"} des Modells verworfen (Gericht nicht im
              Katalog, Tag belegt oder außerhalb der Woche).
            </p>
          )}

          <div className="food-proposal__actions">
            <button
              type="button"
              className="btn-primary"
              disabled={chosenMeals.length === 0 || applyMutation.isPending}
              onClick={() => applyMutation.mutate(chosenMeals)}
            >
              {applyMutation.isPending ? "Übernimmt …" : `${chosenMeals.length} übernehmen`}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setPlan(null)}>
              Verwerfen
            </button>
            {applyMutation.isError && (
              <span className="food-save-state food-save-state--error" role="status">
                {describeError(applyMutation.error)}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Notiz eines Gerichts, lesbar gemacht: Quelle und Zubereitung.
 *
 * Die Notiz ist Freitext und bleibt es auch - hier wird nur DARGESTELLT, nicht
 * strukturiert. Die Rezepte aus scripts/seed_rezepte_katalog.sql folgen der
 * Form "Vorbemerkung\n1. ... 2. ...", und genau die wird erkannt: Zeilen
 * bleiben Zeilen, und eine Zeile mit durchnummerierten Schritten wird zur
 * Liste. Eine von Hand getippte Notiz ("schmeckt den Kindern nicht") steht
 * dann eben als ein Absatz da - richtig so, sie ist kein Rezept.
 */
function DishRecipe({ note }: { note: string | null | undefined }) {
  const text = (note ?? "").trim();
  if (!text) return null;

  const lines = text.split("\n").map((line) => line.trim());
  return (
    <div className="food-recipe">
      {lines.map((line, lineIndex) => {
        // Schritte werden VOR der Nummer getrennt, deshalb ist das erste
        // Stueck leer, wenn die Zeile mit "1." beginnt. Sonst ist es eine
        // Vorbemerkung, die noch vor Schritt 1 steht, und bleibt stehen.
        const parts = line.split(/(?=\d+\.\s)/).filter(Boolean);
        const steps = parts.filter((part) => /^\d+\.\s/.test(part));
        if (steps.length < 2) {
          return (
            <p key={lineIndex} className="food-recipe__lead">
              {line}
            </p>
          );
        }
        const lead = parts.find((part) => !/^\d+\.\s/.test(part));
        return (
          <Fragment key={lineIndex}>
            {lead && <p className="food-recipe__lead">{lead.trim()}</p>}
            <ol className="food-recipe__steps">
              {steps.map((step, stepIndex) => (
                <li key={stepIndex}>{step.replace(/^\d+\.\s*/, "").trim()}</li>
              ))}
            </ol>
          </Fragment>
        );
      })}
    </div>
  );
}

/**
 * Zutatenliste eines Gerichts ("Spaghetti" = Nudeln + Sauce + Kleinkram).
 *
 * Die Mengen gelten fuer die Standardportionen des Gerichts, nicht fuer eine
 * Portion - so, wie es im Rezept steht. Beim Uebertragen in die Einkaufsliste
 * rechnet der Server auf die tatsaechlich geplante Portionszahl hoch.
 */
function IngredientEditor({
  dish,
  token,
  householdId,
}: {
  dish: Dish;
  token: string;
  householdId: string;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unit, setUnit] = useState("");
  const [category, setCategory] = useState("");

  const { data: ingredients, isLoading } = useQuery<DishIngredient[]>({
    queryKey: ["dish-ingredients", dish.id],
    queryFn: () => listDishIngredients(token, householdId, dish.id),
    enabled: Boolean(token && householdId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["dish-ingredients", dish.id] });
    // ingredient_count haengt am Gericht selbst.
    void queryClient.invalidateQueries({ queryKey: ["dishes", householdId] });
    // Eine neue Zutat ohne Preis ist eine neue Luecke, eine geloeschte
    // schliesst eine.
    void queryClient.invalidateQueries({ queryKey: ["price-gaps", householdId] });
  };

  const createMutation = useMutation({
    mutationFn: (input: {
      dish_id: string;
      name: string;
      quantity?: string;
      unit?: string;
      category?: string;
    }) => createDishIngredient(token, householdId, input),
    onSuccess: () => {
      invalidate();
      setName("");
      setQuantity("");
      setUnit("");
      setCategory("");
    },
  });

  const updateMutation = useMutation({
    mutationFn: (input: { ingredientId: string; patch: DishIngredientUpdateInput }) =>
      updateDishIngredient(token, householdId, input.ingredientId, input.patch),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (ingredientId: string) => deleteDishIngredient(token, householdId, ingredientId),
    onSuccess: invalidate,
  });

  // --- KI-Vorschlag ---
  // Der Vorschlag steht nur im Component-State. Erst "Übernehmen" schreibt,
  // und zwar genau die angehakten Zeilen.
  const [proposal, setProposal] = useState<IngredientSuggestion[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());

  const suggestMutation = useMutation({
    mutationFn: () => suggestDishIngredients(token, householdId, dish.id),
    onSuccess: (result) => {
      setProposal(result.ingredients);
      // Was es schon gibt, ist abgewählt - ein zweiter Lauf soll nicht
      // versehentlich Dubletten anlegen.
      setChosen(new Set(result.ingredients.filter((i) => !i.already_present).map((i) => i.name)));
    },
  });

  const applyMutation = useMutation({
    mutationFn: (items: IngredientSuggestion[]) =>
      createDishIngredientsBulk(token, householdId, {
        dish_id: dish.id,
        ingredients: items.map((item) => ({
          name: item.name,
          quantity: item.quantity,
          unit: item.unit,
          category: item.category,
        })),
      }),
    onSuccess: () => {
      invalidate();
      setProposal(null);
      setChosen(new Set());
    },
  });

  const toggleChosen = (name: string) => {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const commitField = (
    ingredient: DishIngredient,
    field: keyof DishIngredientUpdateInput,
    rawValue: string,
  ) => {
    let value: string | null = rawValue.trim() || null;
    if (field === "name") {
      if (!value) return;
    } else if (field === "quantity") {
      // Leere Menge ist gueltig ("Salz"), eine unlesbare nicht.
      if (value !== null) {
        const normalized = value.replace(",", ".");
        if (!Number.isFinite(Number(normalized)) || Number(normalized) <= 0) return;
        value = normalized;
      }
    }
    const previous = ingredient[field as keyof DishIngredient];
    if (value === previous || (value == null && previous == null)) return;
    updateMutation.mutate({ ingredientId: ingredient.id, patch: { [field]: value } });
  };

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const normalized = quantity.trim().replace(",", ".");
    createMutation.mutate({
      dish_id: dish.id,
      name: trimmed,
      quantity: normalized && Number(normalized) > 0 ? normalized : undefined,
      unit: unit.trim() || undefined,
      category: category.trim() || undefined,
    });
  };

  return (
    <div className="food-ingredient-panel">
      <DishRecipe note={dish.note} />
      <div className="food-ingredient-panel__head">
        <div className="food-ingredient-panel__title">
          <strong>Zutaten für {dish.default_servings} Portionen</strong>
          <button
            type="button"
            className="food-row-button"
            onClick={() => suggestMutation.mutate()}
            disabled={suggestMutation.isPending}
            title="Lokales KI-Modell fragen — der Vorschlag wird nur angezeigt, nicht gespeichert"
          >
            {suggestMutation.isPending ? "Fragt Modell …" : "✨ Zutaten vorschlagen"}
          </button>
        </div>
        <span className="food-section-hint">
          Mengen gelten für {dish.default_servings} Portionen — beim Übertragen in die Einkaufsliste
          wird auf die geplanten Portionen hochgerechnet. Menge darf leer bleiben („Salz", „etwas
          Öl").
        </span>
      </div>

      {suggestMutation.isError && (
        <AiErrorNotice
          className="food-fill-note food-fill-note--error"
          role="status"
          error={suggestMutation.error}
          fallback={describeError(suggestMutation.error)}
        />
      )}

      {proposal !== null && (
        <div className="food-proposal">
          <div className="food-proposal__head">
            <strong>Vorschlag — noch nicht gespeichert</strong>
            <span className="food-section-hint">
              Mengen sind eine Schätzung des Modells. Prüfen, abwählen was nicht passt, dann
              übernehmen. Ändern lässt sich danach jede Zeile.
            </span>
          </div>
          {proposal.length === 0 && (
            <p className="food-fill-note">
              Das Modell hatte keinen brauchbaren Vorschlag für „{dish.name}". Zutaten von Hand
              eintragen.
            </p>
          )}
          {proposal.map((item) => (
            <label key={item.name} className="food-proposal__row">
              <input
                type="checkbox"
                checked={chosen.has(item.name)}
                onChange={() => toggleChosen(item.name)}
                aria-label={`${item.name} übernehmen`}
              />
              <span className="food-proposal__name">{item.name}</span>
              <span className="food-proposal__amount">
                {item.quantity ? `${formatQuantity(item.quantity)} ${item.unit ?? ""}`.trim() : "—"}
              </span>
              <span className="food-section-hint">{item.category ?? ""}</span>
              {item.already_present && (
                <span className="status-badge status-badge--neutral">schon vorhanden</span>
              )}
            </label>
          ))}
          <div className="food-proposal__actions">
            <button
              type="button"
              className="btn-primary"
              disabled={chosen.size === 0 || applyMutation.isPending}
              onClick={() => applyMutation.mutate(proposal.filter((i) => chosen.has(i.name)))}
            >
              {applyMutation.isPending ? "Übernimmt …" : `${chosen.size} übernehmen`}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setProposal(null)}>
              Verwerfen
            </button>
            {applyMutation.isError && (
              <span className="food-save-state food-save-state--error" role="status">
                {describeError(applyMutation.error)}
              </span>
            )}
          </div>
        </div>
      )}

      <table className="txn-table food-ledger food-ledger--ingredients">
        <thead>
          <tr>
            <th className="txn-th">Zutat</th>
            <th className="txn-th">Menge</th>
            <th className="txn-th">Einheit</th>
            <th className="txn-th">Kategorie</th>
            <th className="txn-th">Kosten</th>
            <th className="txn-th">Aktion</th>
          </tr>
        </thead>
        <tbody>
          {isLoading && (
            <tr>
              <td className="food-table-message" colSpan={6}>
                Zutaten werden geladen …
              </td>
            </tr>
          )}
          {!isLoading && (ingredients ?? []).length === 0 && (
            <tr>
              <td className="food-table-message" colSpan={6}>
                Noch keine Zutaten — unten eintragen. Danach lässt sich die Einkaufsliste aus dem
                Wochenplan füllen.
              </td>
            </tr>
          )}
          {(ingredients ?? []).map((ingredient) => {
            const fieldProps = (original: string) => ({
              "data-original": original,
              onKeyDown: handleCellKeyDown,
            });
            return (
              <tr key={ingredient.id} className="txn-tr food-edit-row">
                <td className="txn-td">
                  <input
                    className="food-cell-input food-cell-input--strong"
                    aria-label={`${ingredient.name}: Zutat`}
                    defaultValue={ingredient.name}
                    onBlur={(e) => commitField(ingredient, "name", e.currentTarget.value)}
                    {...fieldProps(ingredient.name)}
                  />
                </td>
                <td className="txn-td">
                  <input
                    inputMode="decimal"
                    className="food-cell-input food-cell-input--mono"
                    aria-label={`${ingredient.name}: Menge`}
                    defaultValue={formatQuantity(ingredient.quantity)}
                    placeholder="nach Bedarf"
                    onBlur={(e) => commitField(ingredient, "quantity", e.currentTarget.value)}
                    {...fieldProps(formatQuantity(ingredient.quantity))}
                  />
                </td>
                <td className="txn-td">
                  <input
                    className="food-cell-input"
                    aria-label={`${ingredient.name}: Einheit`}
                    defaultValue={ingredient.unit ?? ""}
                    placeholder="g, ml, Stk"
                    onBlur={(e) => commitField(ingredient, "unit", e.currentTarget.value)}
                    {...fieldProps(ingredient.unit ?? "")}
                  />
                </td>
                <td className="txn-td">
                  <input
                    className="food-cell-input"
                    aria-label={`${ingredient.name}: Kategorie`}
                    defaultValue={ingredient.category ?? ""}
                    placeholder="z. B. Gemüse"
                    onBlur={(e) => commitField(ingredient, "category", e.currentTarget.value)}
                    {...fieldProps(ingredient.category ?? "")}
                  />
                </td>
                {/* Drei unterscheidbare Zustände: Kosten, "kein Preis
                    hinterlegt", "Einheit passt nicht zur Preisliste". Das
                    eine legt man an, das andere korrigiert man. */}
                <td className="txn-td food-cost-cell">
                  {ingredient.cost_minor != null ? (
                    <span className="food-usage-count">
                      {formatMinorUnitsAsEuro(ingredient.cost_minor)}
                    </span>
                  ) : ingredient.unit_mismatch ? (
                    <span
                      className="food-cost-gap"
                      title={`Einheit „${ingredient.unit ?? "—"}" passt nicht zur Preisliste — dort steht eine andere. Im Reiter Preise lässt sich die Umrechnung dieser Zutat hinterlegen.`}
                    >
                      Einheit ≠
                    </span>
                  ) : ingredient.quantity == null ? (
                    <span
                      className="food-section-hint"
                      title="Ohne Menge keine Kosten — das ist bei Salz und Gewürzen so gewollt"
                    >
                      —
                    </span>
                  ) : (
                    <span
                      className="food-cost-gap"
                      title="Kein Preis hinterlegt — im Reiter Preise anlegen"
                    >
                      kein Preis
                    </span>
                  )}
                </td>
                <td className="txn-td food-row-actions">
                  <button
                    type="button"
                    className="food-delete-button"
                    aria-label={`${ingredient.name} entfernen`}
                    onClick={() => deleteMutation.mutate(ingredient.id)}
                  >
                    Entfernen
                  </button>
                </td>
              </tr>
            );
          })}
          <tr className="food-add-row">
            <td className="txn-td">
              <input
                className="food-cell-input food-cell-input--strong"
                aria-label={`${dish.name}: Neue Zutat`}
                value={name}
                placeholder="Zutat hinzufügen …"
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submit();
                  }
                }}
              />
            </td>
            <td className="txn-td">
              <input
                inputMode="decimal"
                className="food-cell-input food-cell-input--mono"
                aria-label={`${dish.name}: Neue Zutat, Menge`}
                value={quantity}
                placeholder="500"
                onChange={(e) => setQuantity(e.target.value)}
              />
            </td>
            <td className="txn-td">
              <input
                className="food-cell-input"
                aria-label={`${dish.name}: Neue Zutat, Einheit`}
                value={unit}
                placeholder="g"
                onChange={(e) => setUnit(e.target.value)}
              />
            </td>
            <td className="txn-td">
              <input
                className="food-cell-input"
                aria-label={`${dish.name}: Neue Zutat, Kategorie`}
                value={category}
                placeholder="Kategorie"
                onChange={(e) => setCategory(e.target.value)}
              />
            </td>
            <td className="txn-td" />
            <td className="txn-td food-row-actions">
              <span
                className={`food-save-state ${createMutation.isError ? "food-save-state--error" : ""}`}
                role="status"
              >
                {createMutation.isPending
                  ? "Wird angelegt …"
                  : createMutation.isError
                    ? describeError(createMutation.error)
                    : ""}
              </span>
              <button
                type="button"
                className="btn-primary food-add-button"
                disabled={!name.trim() || createMutation.isPending}
                onClick={submit}
              >
                + Zutat
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

interface ShoppingListCardProps {
  list: ShoppingList;
  meals: Meal[];
  weekStart: string;
  token: string;
  householdId: string;
  currentWeekId: string;
}

type ShoppingSortKey = "created" | "name" | "category" | "quantity" | "planned" | "actual";
type SortDirection = "asc" | "desc";

/** Zahlenwert einer Mengenangabe; unlesbares zaehlt als 0. */
function quantityValue(raw: string | null | undefined): number {
  const parsed = Number.parseFloat((raw ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Sucht, filtert und sortiert die Posten einer Einkaufsliste.
 *
 * Zwei Festlegungen, die nicht aus der Sortierrichtung folgen:
 *
 *  * Zeilen OHNE Wert (keine Kategorie, kein Preis) stehen immer am Ende,
 *    auch bei absteigender Sortierung. Sonst begruesst einen die Liste beim
 *    Sortieren nach Plan mit einem Block leerer Zeilen, und das Sortieren
 *    haette genau das verdeckt, wonach man gesucht hat.
 *  * Innerhalb derselben Kategorie wird nach Namen sortiert. Eine Kategorie
 *    hat schnell zehn Zeilen; die in Anlagereihenfolge stehen zu lassen macht
 *    das Gruppieren wertlos.
 */
function filterAndSortShoppingItems(
  items: ShoppingItem[],
  search: string,
  hidePurchased: boolean,
  sort: { key: ShoppingSortKey; direction: SortDirection },
): ShoppingItem[] {
  const needle = search.trim().toLowerCase();
  const gefiltert = items.filter((item) => {
    if (hidePurchased && item.is_purchased) return false;
    if (!needle) return true;
    return (
      item.name.toLowerCase().includes(needle) ||
      (item.category ?? "").toLowerCase().includes(needle) ||
      (item.note ?? "").toLowerCase().includes(needle)
    );
  });

  const richtung = sort.direction === "asc" ? 1 : -1;
  const nachName = (a: ShoppingItem, b: ShoppingItem) => a.name.localeCompare(b.name, "de");

  const leerZuletzt = (
    a: string | number | null,
    b: string | number | null,
    vergleich: () => number,
  ): number | null => {
    const aLeer = a === null || a === "";
    const bLeer = b === null || b === "";
    if (aLeer && bLeer) return 0;
    if (aLeer) return 1;
    if (bLeer) return -1;
    return vergleich();
  };

  return [...gefiltert].sort((a, b) => {
    switch (sort.key) {
      case "name":
        return richtung * nachName(a, b);
      case "category": {
        const rang = leerZuletzt(a.category ?? "", b.category ?? "", () =>
          (a.category ?? "").localeCompare(b.category ?? "", "de"),
        );
        if (rang === null) return 0;
        if (rang !== 0 && ((a.category ?? "") === "" || (b.category ?? "") === "")) return rang;
        return rang !== 0 ? richtung * rang : nachName(a, b);
      }
      case "quantity":
        return richtung * (quantityValue(a.quantity) - quantityValue(b.quantity)) || nachName(a, b);
      case "planned":
      case "actual": {
        const feld = sort.key === "planned" ? "planned_price_minor" : "actual_price_minor";
        const rang = leerZuletzt(a[feld], b[feld], () => (a[feld] ?? 0) - (b[feld] ?? 0));
        if (rang === null) return 0;
        if (a[feld] === null || b[feld] === null) return rang;
        return rang !== 0 ? richtung * rang : nachName(a, b);
      }
      case "created":
      default:
        return richtung * a.created_at.localeCompare(b.created_at);
    }
  });
}

function ShoppingListCard({
  list,
  meals,
  weekStart,
  token,
  householdId,
  currentWeekId,
}: ShoppingListCardProps) {
  const queryClient = useQueryClient();
  const { formatDate } = useFormatDate();
  const [isPickerOpen, setIsPickerOpen] = useState(false);

  const {
    data: items,
    isLoading: areItemsLoading,
    isError: areItemsError,
  } = useQuery({
    queryKey: ["shopping-items", list.id],
    queryFn: () => listShoppingItems(token, householdId, list.id),
    enabled: Boolean(token && householdId),
  });

  const [itemName, setItemName] = useState("");
  const [itemCategory, setItemCategory] = useState("");
  const [itemQuantity, setItemQuantity] = useState("1");
  const [itemUnit, setItemUnit] = useState("");
  const [itemPlannedEuro, setItemPlannedEuro] = useState("");
  const [itemActualEuro, setItemActualEuro] = useState("");
  const [itemMealId, setItemMealId] = useState("");

  // Suchen, Filtern, Sortieren geschieht im Browser: eine Einkaufsliste hat
  // Dutzende Zeilen, keine Tausende - ein Serverdurchgang je Tastendruck waere
  // hier nur Wartezeit.
  const [itemSearch, setItemSearch] = useState("");
  const [hidePurchased, setHidePurchased] = useState(false);
  // Vorgabe ist die Kategorie und nicht die Anlagereihenfolge: im Laden laeuft
  // man Regal fuer Regal ab, nicht in der Reihenfolge, in der der Wochenplan
  // die Zutaten ausgespuckt hat.
  const [itemSort, setItemSort] = useState<{ key: ShoppingSortKey; direction: SortDirection }>({
    key: "category",
    direction: "asc",
  });

  const toggleItemSort = (key: ShoppingSortKey) => {
    setItemSort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: "asc" },
    );
  };

  const visibleItems = useMemo(
    () => filterAndSortShoppingItems(items ?? [], itemSearch, hidePurchased, itemSort),
    [items, itemSearch, hidePurchased, itemSort],
  );
  const hiddenCount = (items?.length ?? 0) - visibleItems.length;

  const invalidateList = () => {
    void queryClient.invalidateQueries({ queryKey: ["shopping-items", list.id] });
    void queryClient.invalidateQueries({ queryKey: ["shopping-lists", currentWeekId] });
    void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
  };

  const createItemMutation = useMutation({
    mutationFn: (input: {
      shopping_list_id: string;
      name: string;
      category?: string;
      quantity?: string;
      unit?: string;
      planned_price_minor?: number;
      actual_price_minor?: number;
      meal_id?: string;
    }) => createShoppingItem(token, householdId, input),
    onSuccess: () => {
      invalidateList();
      setItemName("");
      setItemCategory("");
      setItemQuantity("1");
      setItemUnit("");
      setItemPlannedEuro("");
      setItemActualEuro("");
      setItemMealId("");
    },
  });

  const updateItemMutation = useMutation({
    mutationFn: (input: { itemId: string; patch: ShoppingItemUpdateInput }) =>
      updateShoppingItem(token, householdId, input.itemId, input.patch),
    onSuccess: invalidateList,
  });

  const commitItemField = (
    item: ShoppingItem,
    field: keyof ShoppingItemUpdateInput,
    rawValue: string | boolean,
  ) => {
    let value: string | number | boolean | null = rawValue;
    if (field === "planned_price_minor" || field === "actual_price_minor") {
      value = String(rawValue).trim() ? parseAmountToMinorUnits(String(rawValue)) : null;
      if (String(rawValue).trim() && value == null) return;
    } else if (field === "name") {
      value = String(rawValue).trim();
      if (!value) return;
    } else if (field === "category" || field === "unit" || field === "meal_id") {
      value = String(rawValue).trim() || null;
    }
    const previous = item[field as keyof ShoppingItem];
    if (value === previous || (value == null && previous == null)) return;
    updateItemMutation.mutate({ itemId: item.id, patch: { [field]: value } });
  };

  const deleteItemMutation = useMutation({
    mutationFn: (itemId: string) => deleteShoppingItem(token, householdId, itemId),
    onSuccess: invalidateList,
  });

  const linkMutation = useMutation({
    mutationFn: (transactionId: string) =>
      linkPurchase(token, householdId, list.id, { transaction_id: transactionId }),
    onSuccess: () => {
      invalidateList();
      setIsPickerOpen(false);
    },
  });

  const unlinkMutation = useMutation({
    mutationFn: (purchaseLinkId: string) => unlinkPurchase(token, householdId, purchaseLinkId),
    onSuccess: invalidateList,
  });

  // Die Liste loeschen raeumt per Fremdschluessel-Kaskade auch ihre Artikel
  // und Verknuepfungen ab - deshalb Rueckfrage mit Nennung der Anzahl.
  const deleteListMutation = useMutation({
    mutationFn: () => deleteShoppingList(token, householdId, list.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shopping-lists", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
    },
  });

  // Rein rechnerisch aus den Zutaten der geplanten Gerichte - keine KI, keine
  // Schaetzung. Was fehlt, meldet der Server als uebersprungene Mahlzeit.
  const fillMutation = useMutation({
    mutationFn: () => fillShoppingListFromMeals(token, householdId, list.id),
    onSuccess: invalidateList,
  });
  const fillResult = fillMutation.data;

  const { data: candidateTransactions } = useQuery<Transaction[]>({
    queryKey: ["transactions-picker", list.id, weekStart],
    queryFn: () =>
      listTransactions(token, householdId, {
        fromDate: addDays(weekStart, -3),
        toDate: addDays(weekStart, 10),
        limit: 50,
      }),
    enabled: isPickerOpen,
  });

  const linkedIds = new Set(list.linked_transactions.map((t) => t.id));

  return (
    <div className="card" style={{ marginBottom: "var(--space-4)" }}>
      <div className="page-header" style={{ marginBottom: "var(--space-3)" }}>
        <div>
          <h2 className="card-heading">{list.store || "Einkaufsliste"}</h2>
          <span className="status-badge status-badge--neutral">{list.status}</span>
          {/* Ohne Laden heissen mehrere Listen einer Woche gleich - der
              Anlegezeitpunkt ist dann das einzige Unterscheidungsmerkmal. */}
          <span className="metric-hint" style={{ marginLeft: "var(--space-2)" }}>
            angelegt {formatCreatedAt(list.created_at)}
          </span>
        </div>
        {/* actual_total_minor ist - wie actual_minor der Wochenuebersicht -
            bereits als positive Istkosten normalisiert. */}
        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => fillMutation.mutate()}
            disabled={fillMutation.isPending}
            title="Zutaten aller geplanten Gerichte dieser Woche übernehmen"
          >
            {fillMutation.isPending ? "Übernimmt …" : "Aus Wochenplan füllen"}
          </button>
          <button
            type="button"
            className="food-delete-button"
            aria-label={`Einkaufsliste ${list.store || "ohne Laden"} löschen`}
            disabled={deleteListMutation.isPending}
            onClick={() => {
              const itemCount = items?.length ?? 0;
              const detail = itemCount > 0 ? ` samt ${itemCount} Artikel` : "";
              const linked = list.linked_transactions.length;
              const linkNote =
                linked > 0
                  ? ` ${linked} verknüpfte Buchung${linked === 1 ? "" : "en"} wird gelöst (die Buchung selbst bleibt).`
                  : "";
              if (
                window.confirm(
                  `Einkaufsliste „${list.store || "ohne Laden"}“${detail} wirklich löschen?${linkNote}`,
                )
              ) {
                deleteListMutation.mutate();
              }
            }}
          >
            {deleteListMutation.isPending ? "Löscht …" : "Liste löschen"}
          </button>
          <div style={{ textAlign: "right" }}>
            <div className="metric-hint">
              Plan: {formatMinorUnitsAsEuro(list.planned_total_minor)}
            </div>
            <div className="metric-hint">
              Ist: {formatMinorUnitsAsEuro(list.actual_total_minor)}
            </div>
          </div>
        </div>
      </div>

      {fillMutation.isError && (
        <p className="food-fill-note food-fill-note--error" role="status">
          {describeError(fillMutation.error)}
        </p>
      )}
      {fillResult && (
        <p className="food-fill-note" role="status">
          {fillResult.created_items} neu, {fillResult.merged_items} aktualisiert — aus{" "}
          {fillResult.considered_meals}{" "}
          {fillResult.considered_meals === 1 ? "Gericht" : "Gerichten"}. Preise gelten für ganze
          Packungen.
          {fillResult.unpriced_items > 0 && (
            <>
              {" "}
              {fillResult.unpriced_items} Posten ohne Preis — im Reiter Preise hinterlegen, dann
              stimmt die Plansumme.
            </>
          )}
          {fillResult.skipped_meals.length > 0 && (
            <> Ohne hinterlegte Zutaten übersprungen: {fillResult.skipped_meals.join(", ")}.</>
          )}
        </p>
      )}

      <div className="food-list-toolbar">
        <input
          type="search"
          className="food-cell-input"
          aria-label="Einkaufsliste durchsuchen"
          placeholder="Suchen: Artikel, Kategorie, Notiz"
          value={itemSearch}
          onChange={(e) => setItemSearch(e.target.value)}
        />
        <label className="food-toolbar-check">
          <input
            type="checkbox"
            checked={hidePurchased}
            onChange={(e) => setHidePurchased(e.target.checked)}
          />
          Gekauftes ausblenden
        </label>
        {itemSort.key !== "created" && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setItemSort({ key: "created", direction: "asc" })}
            title="Zurück zur Reihenfolge, in der die Posten angelegt wurden"
          >
            Anlagereihenfolge
          </button>
        )}
        <span className="metric-hint" role="status">
          {hiddenCount > 0
            ? `${visibleItems.length} von ${items?.length ?? 0} Posten — ${hiddenCount} ausgeblendet`
            : `${visibleItems.length} Posten`}
        </span>
      </div>

      <div className="txn-table-wrapper food-table-wrap">
        <table className="txn-table food-ledger food-ledger--shopping">
          <thead>
            <tr>
              <th className="txn-th">✓</th>
              <SortableTableHeader
                label="Artikel"
                sortKey="name"
                active={itemSort.key === "name"}
                direction={itemSort.direction}
                onSort={toggleItemSort}
              />
              <SortableTableHeader
                label="Kategorie"
                sortKey="category"
                active={itemSort.key === "category"}
                direction={itemSort.direction}
                onSort={toggleItemSort}
              />
              <SortableTableHeader
                label="Menge"
                sortKey="quantity"
                active={itemSort.key === "quantity"}
                direction={itemSort.direction}
                onSort={toggleItemSort}
              />
              <th className="txn-th">Einheit</th>
              <SortableTableHeader
                label="Plan"
                sortKey="planned"
                active={itemSort.key === "planned"}
                direction={itemSort.direction}
                onSort={toggleItemSort}
              />
              <SortableTableHeader
                label="Ist"
                sortKey="actual"
                active={itemSort.key === "actual"}
                direction={itemSort.direction}
                onSort={toggleItemSort}
              />
              <th className="txn-th">Bedarf</th>
              <th className="txn-th">Mahlzeit</th>
              <th className="txn-th">Status / Aktion</th>
            </tr>
          </thead>
          <tbody>
            {areItemsLoading && (
              <tr>
                <td className="food-table-message" colSpan={10}>
                  Einkaufsliste wird geladen …
                </td>
              </tr>
            )}
            {areItemsError && (
              <tr>
                <td className="food-table-message food-table-message--error" colSpan={10}>
                  Artikel konnten nicht geladen werden.
                </td>
              </tr>
            )}
            {!areItemsLoading && !areItemsError && (items ?? []).length === 0 && (
              <tr>
                <td className="food-table-message" colSpan={10}>
                  Noch leer — den ersten Artikel direkt unten eintragen.
                </td>
              </tr>
            )}
            {/* Unterschieden von "noch leer": hier gibt es Posten, sie sind nur
                alle weggefiltert. Ohne eigene Meldung sieht das aus wie eine
                leere Liste, und man sucht den Fehler an der falschen Stelle. */}
            {!areItemsLoading &&
              !areItemsError &&
              (items ?? []).length > 0 &&
              visibleItems.length === 0 && (
                <tr>
                  <td className="food-table-message" colSpan={10}>
                    Kein Posten passt zur Suche
                    {hidePurchased ? " bzw. ist noch offen" : ""}.
                  </td>
                </tr>
              )}
            {visibleItems.map((item) => {
              const rowIsSaving =
                updateItemMutation.isPending && updateItemMutation.variables?.itemId === item.id;
              const rowHasError =
                updateItemMutation.isError && updateItemMutation.variables?.itemId === item.id;
              const fieldProps = (original: string) => ({
                "data-original": original,
                onKeyDown: handleCellKeyDown,
              });
              return (
                <tr
                  key={item.id}
                  className={`txn-tr food-edit-row ${item.is_purchased ? "food-edit-row--purchased" : ""}`}
                >
                  <td className="txn-td food-check-cell">
                    <input
                      type="checkbox"
                      aria-label={`${item.name} gekauft`}
                      checked={item.is_purchased}
                      onChange={(e) => commitItemField(item, "is_purchased", e.target.checked)}
                    />
                  </td>
                  <td className="txn-td">
                    <input
                      className="food-cell-input food-cell-input--strong"
                      aria-label={`${item.name}: Artikel`}
                      defaultValue={item.name}
                      onBlur={(e) => commitItemField(item, "name", e.currentTarget.value)}
                      {...fieldProps(item.name)}
                    />
                  </td>
                  <td className="txn-td">
                    <input
                      className="food-cell-input"
                      aria-label={`${item.name}: Kategorie`}
                      defaultValue={item.category ?? ""}
                      placeholder="z. B. Gemüse"
                      onBlur={(e) => commitItemField(item, "category", e.currentTarget.value)}
                      {...fieldProps(item.category ?? "")}
                    />
                  </td>
                  <td className="txn-td">
                    <input
                      className="food-cell-input food-cell-input--mono"
                      aria-label={`${item.name}: Menge`}
                      defaultValue={item.quantity}
                      onBlur={(e) => commitItemField(item, "quantity", e.currentTarget.value)}
                      {...fieldProps(item.quantity)}
                    />
                  </td>
                  <td className="txn-td">
                    <input
                      className="food-cell-input"
                      aria-label={`${item.name}: Einheit`}
                      defaultValue={item.unit ?? ""}
                      onBlur={(e) => commitItemField(item, "unit", e.currentTarget.value)}
                      {...fieldProps(item.unit ?? "")}
                    />
                  </td>
                  <td className="txn-td">
                    <input
                      inputMode="decimal"
                      className="food-cell-input food-cell-input--mono"
                      aria-label={`${item.name}: Planpreis`}
                      defaultValue={amountDraft(item.planned_price_minor)}
                      placeholder="0,00"
                      onBlur={(e) =>
                        commitItemField(item, "planned_price_minor", e.currentTarget.value)
                      }
                      {...fieldProps(amountDraft(item.planned_price_minor))}
                    />
                  </td>
                  <td className="txn-td">
                    <input
                      inputMode="decimal"
                      className="food-cell-input food-cell-input--mono"
                      aria-label={`${item.name}: Istpreis`}
                      defaultValue={amountDraft(item.actual_price_minor)}
                      placeholder="0,00"
                      onBlur={(e) =>
                        commitItemField(item, "actual_price_minor", e.currentTarget.value)
                      }
                      {...fieldProps(amountDraft(item.actual_price_minor))}
                    />
                  </td>
                  <td className="txn-td">
                    <input
                      className="food-cell-input"
                      aria-label={`${item.name}: Bedarf`}
                      defaultValue={item.note ?? ""}
                      placeholder={item.from_meal_plan ? "" : "Notiz"}
                      title={
                        item.from_meal_plan
                          ? "Aus dem Wochenplan übernommen — wird beim nächsten Abgleich neu gesetzt"
                          : "Eigene Notiz"
                      }
                      onBlur={(e) => commitItemField(item, "note", e.currentTarget.value)}
                      {...fieldProps(item.note ?? "")}
                    />
                  </td>
                  <td className="txn-td">
                    <select
                      className="food-cell-input"
                      aria-label={`${item.name}: Mahlzeit`}
                      defaultValue={item.meal_id ?? ""}
                      onBlur={(e) => commitItemField(item, "meal_id", e.currentTarget.value)}
                      {...fieldProps(item.meal_id ?? "")}
                    >
                      <option value="">Keine</option>
                      {meals.map((meal) => (
                        <option key={meal.id} value={meal.id}>
                          {meal.dish_name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="txn-td food-row-actions">
                    <span
                      className={`food-save-state ${rowHasError ? "food-save-state--error" : ""}`}
                      role="status"
                    >
                      {rowIsSaving ? "Speichert …" : rowHasError ? "Fehler" : "Gespeichert"}
                    </span>
                    <button
                      type="button"
                      className="food-delete-button"
                      aria-label={`${item.name} löschen`}
                      onClick={() => deleteItemMutation.mutate(item.id)}
                    >
                      Löschen
                    </button>
                  </td>
                </tr>
              );
            })}
            <tr className="food-add-row">
              <td className="txn-td food-check-cell">
                <span aria-hidden="true">＋</span>
              </td>
              <td className="txn-td">
                <input
                  className="food-cell-input food-cell-input--strong"
                  aria-label="Neuer Artikel: Artikel"
                  value={itemName}
                  placeholder="Artikel hinzufügen …"
                  onChange={(e) => setItemName(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  className="food-cell-input"
                  aria-label="Neuer Artikel: Kategorie"
                  value={itemCategory}
                  placeholder="Kategorie"
                  onChange={(e) => setItemCategory(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  className="food-cell-input food-cell-input--mono"
                  aria-label="Neuer Artikel: Menge"
                  value={itemQuantity}
                  onChange={(e) => setItemQuantity(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  className="food-cell-input"
                  aria-label="Neuer Artikel: Einheit"
                  value={itemUnit}
                  placeholder="Stk"
                  onChange={(e) => setItemUnit(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  inputMode="decimal"
                  className="food-cell-input food-cell-input--mono"
                  aria-label="Neuer Artikel: Planpreis"
                  value={itemPlannedEuro}
                  placeholder="0,00"
                  onChange={(e) => setItemPlannedEuro(e.target.value)}
                />
              </td>
              <td className="txn-td">
                <input
                  inputMode="decimal"
                  className="food-cell-input food-cell-input--mono"
                  aria-label="Neuer Artikel: Istpreis"
                  value={itemActualEuro}
                  placeholder="0,00"
                  onChange={(e) => setItemActualEuro(e.target.value)}
                />
              </td>
              <td className="txn-td" />
              <td className="txn-td">
                <select
                  className="food-cell-input"
                  aria-label="Neuer Artikel: Mahlzeit"
                  value={itemMealId}
                  onChange={(e) => setItemMealId(e.target.value)}
                >
                  <option value="">Keine</option>
                  {meals.map((meal) => (
                    <option key={meal.id} value={meal.id}>
                      {meal.dish_name}
                    </option>
                  ))}
                </select>
              </td>
              <td className="txn-td food-row-actions">
                <span
                  className={`food-save-state ${createItemMutation.isError ? "food-save-state--error" : ""}`}
                  role="status"
                >
                  {createItemMutation.isPending
                    ? "Wird angelegt …"
                    : createItemMutation.isError
                      ? "Fehler"
                      : "Neue Zeile"}
                </span>
                <button
                  type="button"
                  className="btn-primary food-add-button"
                  disabled={!itemName.trim() || createItemMutation.isPending}
                  onClick={() => {
                    const plannedMinor = itemPlannedEuro
                      ? parseAmountToMinorUnits(itemPlannedEuro)
                      : null;
                    const actualMinor = itemActualEuro
                      ? parseAmountToMinorUnits(itemActualEuro)
                      : null;
                    createItemMutation.mutate({
                      shopping_list_id: list.id,
                      name: itemName.trim(),
                      category: itemCategory.trim() || undefined,
                      quantity: itemQuantity || undefined,
                      unit: itemUnit.trim() || undefined,
                      planned_price_minor: plannedMinor ?? undefined,
                      actual_price_minor: actualMinor ?? undefined,
                      meal_id: itemMealId || undefined,
                    });
                  }}
                >
                  + Hinzufügen
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div
        style={{
          marginTop: "var(--space-4)",
          borderTop: "1px solid var(--color-border)",
          paddingTop: "var(--space-3)",
        }}
      >
        <h3 className="card-heading">Verknüpfte Transaktionen</h3>
        {list.linked_transactions.length === 0 && (
          <p className="metric-hint">Noch keine Transaktion verknüpft.</p>
        )}
        {list.linked_transactions.map((tx) => (
          <div
            key={tx.id}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "var(--space-2) 0",
            }}
          >
            <span>
              {formatDate(tx.booked_date)} · {tx.payee_label || "—"} ·{" "}
              <strong>{formatMinorUnitsAsEuro(tx.amount_minor)}</strong>
            </span>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => unlinkMutation.mutate(list.id)}
            >
              Entfernen
            </button>
          </div>
        ))}

        <button
          type="button"
          className="btn-secondary"
          style={{ marginTop: "var(--space-2)" }}
          onClick={() => setIsPickerOpen((open) => !open)}
        >
          {isPickerOpen ? "Auswahl schließen" : "+ Transaktion verknüpfen"}
        </button>

        {isPickerOpen && (
          <div style={{ marginTop: "var(--space-2)" }}>
            {(candidateTransactions ?? [])
              .filter((tx) => !linkedIds.has(tx.id))
              .map((tx) => (
                <div
                  key={tx.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "var(--space-2) 0",
                    borderBottom: "1px solid var(--color-border)",
                  }}
                >
                  <span>
                    {formatDate(tx.booked_date)} · {tx.payee_label || "—"} ·{" "}
                    <strong>{formatMinorUnitsAsEuro(tx.amount_minor)}</strong>
                  </span>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => linkMutation.mutate(tx.id)}
                  >
                    Verknüpfen
                  </button>
                </div>
              ))}
            {(candidateTransactions ?? []).length === 0 && (
              <p className="metric-hint">Keine Transaktionen im Zeitraum gefunden.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
