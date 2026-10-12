import { useQuery } from "@tanstack/react-query";
import {
  getMealWeekSummary,
  listMealWeeks,
  listMeals,
  listShoppingItems,
  listShoppingLists,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/** Geplante Wochen (GET /v1/meal-weeks), neueste zuerst. */
export function useMealWeeks() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["meal-weeks", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listMealWeeks(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Plan- vs. Istkosten-Zusammenfassung einer Woche (GET /v1/meal-weeks/{id}/summary). */
export function useMealWeekSummary(mealWeekId: string | undefined) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["meal-week-summary", mealWeekId],
    queryFn: ({ signal }) =>
      getMealWeekSummary(
        accessToken as string,
        selectedHouseholdId as string,
        mealWeekId as string,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!mealWeekId,
  });
}

/** Mahlzeiten einer Woche (GET /v1/meals?meal_week_id=). */
export function useMeals(mealWeekId: string | undefined) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["meals", mealWeekId],
    queryFn: ({ signal }) =>
      listMeals(accessToken as string, selectedHouseholdId as string, mealWeekId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId && !!mealWeekId,
  });
}

/** Einkaufslisten einer Woche (GET /v1/shopping-lists?meal_week_id=). */
export function useShoppingLists(mealWeekId: string | undefined) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["shopping-lists", mealWeekId],
    queryFn: ({ signal }) =>
      listShoppingLists(
        accessToken as string,
        selectedHouseholdId as string,
        mealWeekId as string,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!mealWeekId,
  });
}

/** Artikel einer Einkaufsliste (GET /v1/shopping-items?shopping_list_id=). */
export function useShoppingItems(shoppingListId: string | undefined) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["shopping-items", shoppingListId],
    queryFn: ({ signal }) =>
      listShoppingItems(
        accessToken as string,
        selectedHouseholdId as string,
        shoppingListId as string,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!shoppingListId,
  });
}
