import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  assignGoalToSplits,
  getNetWorthReport,
  listAssetsAndDebts,
  listSavingsGoals,
  listUnassignedGoalSplits,
  listWishlistItems,
} from "../api/client";
import type { BulkAssignGoalInput } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/** Sparziele (GET /v1/goals). */
export function useSavingsGoals() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["goals", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listSavingsGoals(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Splits ohne Sparziel-Zuordnung, rueckwirkende Zuordnung (Architekturplan
 * 6.7, Option (c)). */
export function useUnassignedGoalSplits(accountId?: string) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["unassigned-goal-splits", selectedHouseholdId, accountId],
    queryFn: ({ signal }) =>
      listUnassignedGoalSplits(
        accessToken as string,
        selectedHouseholdId as string,
        accountId,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

export function useAssignGoalToSplits() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: BulkAssignGoalInput) =>
      assignGoalToSplits(accessToken as string, selectedHouseholdId as string, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["goals", selectedHouseholdId] });
      void queryClient.invalidateQueries({
        queryKey: ["unassigned-goal-splits", selectedHouseholdId],
      });
    },
  });
}

/** Wunschliste mit Kaufampel (GET /v1/wishlist). */
export function useWishlistItems() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["wishlist", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listWishlistItems(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Vermoegen & Schulden (GET /v1/assets). */
export function useAssetsAndDebts() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["assets", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listAssetsAndDebts(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Nettovermoegensbericht (GET /v1/reports/net-worth). */
export function useNetWorthReport() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["net-worth", selectedHouseholdId],
    queryFn: ({ signal }) =>
      getNetWorthReport(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}
