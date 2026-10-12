import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getBudgetSummary, getOverviewReport, updateBudgetAllocations } from "../api/client";
import type { BudgetAllocationInput } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/** Monats-Budget-Zusammenfassung (GET /v1/budgets/{year}/{month}). */
export function useBudgetSummary(year: number, month: number) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["budget", selectedHouseholdId, year, month],
    queryFn: ({ signal }) =>
      getBudgetSummary(accessToken as string, selectedHouseholdId as string, year, month, signal),
    enabled: !!accessToken && !!selectedHouseholdId && year > 0 && month >= 1 && month <= 12,
  });
}

/** Allokationen anpassen (PUT /v1/budgets/{year}/{month}/allocations). */
export function useUpdateBudgetAllocations(year: number, month: number) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (allocations: BudgetAllocationInput[]) =>
      updateBudgetAllocations(
        accessToken as string,
        selectedHouseholdId as string,
        year,
        month,
        allocations,
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["budget", selectedHouseholdId, year, month],
      });
      void queryClient.invalidateQueries({
        queryKey: ["overviewReport", selectedHouseholdId],
      });
    },
  });
}

/** Dashboard Overview Report (GET /v1/reports/overview). */
export function useOverviewReport() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["overviewReport", selectedHouseholdId],
    queryFn: ({ signal }) =>
      getOverviewReport(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}
