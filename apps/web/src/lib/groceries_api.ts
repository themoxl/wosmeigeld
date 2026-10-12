import { apiFetch } from "./api";
import type {
  Dish,
  DishCreateInput,
  DishUpdateInput,
  MealWeek,
  MealWeekCreateInput,
  MealWeekUpdateInput,
  ShoppingList,
  ShoppingListCreateInput,
  ShoppingListUpdateInput,
} from "./types";

// --- Dishes ---

export function getDishes(token: string, householdId: string) {
  return apiFetch<Dish[]>("/v1/groceries/dishes", { token, householdId });
}

export function getDish(id: string, token: string, householdId: string) {
  return apiFetch<Dish>(`/v1/groceries/dishes/${id}`, { token, householdId });
}

export function createDish(data: DishCreateInput, token: string, householdId: string) {
  return apiFetch<Dish>("/v1/groceries/dishes", {
    method: "POST",
    body: JSON.stringify(data),
    token,
    householdId,
  });
}

export function updateDish(id: string, data: DishUpdateInput, token: string, householdId: string) {
  return apiFetch<Dish>(`/v1/groceries/dishes/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    token,
    householdId,
  });
}

export function deleteDish(id: string, token: string, householdId: string) {
  return apiFetch<void>(`/v1/groceries/dishes/${id}`, {
    method: "DELETE",
    token,
    householdId,
  });
}

// --- Meal Weeks ---

export function getMealWeeks(token: string, householdId: string) {
  return apiFetch<MealWeek[]>("/v1/groceries/meal-weeks", { token, householdId });
}

export function getMealWeek(id: string, token: string, householdId: string) {
  return apiFetch<MealWeek>(`/v1/groceries/meal-weeks/${id}`, { token, householdId });
}

export function createMealWeek(data: MealWeekCreateInput, token: string, householdId: string) {
  return apiFetch<MealWeek>("/v1/groceries/meal-weeks", {
    method: "POST",
    body: JSON.stringify(data),
    token,
    householdId,
  });
}

export function updateMealWeek(
  id: string,
  data: MealWeekUpdateInput,
  token: string,
  householdId: string,
) {
  return apiFetch<MealWeek>(`/v1/groceries/meal-weeks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    token,
    householdId,
  });
}

// --- Shopping Lists ---

export function getShoppingLists(token: string, householdId: string) {
  return apiFetch<ShoppingList[]>("/v1/groceries/shopping-lists", { token, householdId });
}

export function getShoppingList(id: string, token: string, householdId: string) {
  return apiFetch<ShoppingList>(`/v1/groceries/shopping-lists/${id}`, { token, householdId });
}

export function createShoppingList(
  data: ShoppingListCreateInput,
  token: string,
  householdId: string,
) {
  return apiFetch<ShoppingList>("/v1/groceries/shopping-lists", {
    method: "POST",
    body: JSON.stringify(data),
    token,
    householdId,
  });
}

export function updateShoppingList(
  id: string,
  data: ShoppingListUpdateInput,
  token: string,
  householdId: string,
) {
  return apiFetch<ShoppingList>(`/v1/groceries/shopping-lists/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    token,
    householdId,
  });
}

// --- Prices ---

export function uploadPricesCsv(file: File, token: string, householdId: string) {
  const formData = new FormData();
  formData.append("file", file);

  return apiFetch<{ imported: number }>("/v1/groceries/prices/import", {
    method: "POST",
    body: formData,
    token,
    householdId,
  });
}
