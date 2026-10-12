import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  createDebtPlan,
  deleteDebtPlan,
  getDebtPlan,
  getDebtSchedule,
  listDebtPlans,
  replaceDebtDraws,
  replaceDebtFees,
  replaceDebtRatePeriods,
  replaceDebtSpecialRepayments,
  updateDebtPlan,
} from "../lib/api";
import type {
  DebtPlan,
  DebtPlanCreateInput,
  DebtPlanUpdateInput,
  DebtSchedule,
  DrawInput,
  FeeInput,
  RatePeriodInput,
  SpecialRepaymentInput,
} from "../lib/types";

/**
 * Tilgungsplaene (Plan 7.3/4.8): gemeinsames Modell fuer Projektdarlehen
 * (ueber `project_financing_source_id`) und Bestandsschulden. Eigene Datei,
 * weil der Editor auch ausserhalb von `ProjectDetailPage` sinnvoll ist.
 */

const debtPlansQueryKey = (householdId: string, sourceId?: string) =>
  ["debt-plans", householdId, sourceId ?? "all"] as const;
const debtPlanQueryKey = (householdId: string, debtPlanId: string) =>
  ["debt-plan", householdId, debtPlanId] as const;
const debtScheduleQueryKey = (householdId: string, debtPlanId: string) =>
  ["debt-schedule", householdId, debtPlanId] as const;

interface DebtPlanContext {
  token: string;
  householdId: string;
  enabled: boolean;
}

function useDebtPlanContext(): DebtPlanContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
  };
}

function useInvalidateDebtPlan() {
  const queryClient = useQueryClient();
  const { householdId } = useDebtPlanContext();
  return useCallback(
    (debtPlanId: string, financingSourceId?: string) => {
      void queryClient.invalidateQueries({ queryKey: debtPlanQueryKey(householdId, debtPlanId) });
      void queryClient.invalidateQueries({
        queryKey: debtScheduleQueryKey(householdId, debtPlanId),
      });
      void queryClient.invalidateQueries({ queryKey: debtPlansQueryKey(householdId) });
      if (financingSourceId) {
        void queryClient.invalidateQueries({
          queryKey: debtPlansQueryKey(householdId, financingSourceId),
        });
        void queryClient.invalidateQueries({ queryKey: ["financing-sources", householdId] });
      }
    },
    [queryClient, householdId],
  );
}

export function useDebtPlansByFinancingSource(financingSourceId: string | null) {
  const { token, householdId, enabled } = useDebtPlanContext();
  return useQuery<DebtPlan[]>({
    queryKey: debtPlansQueryKey(householdId, financingSourceId ?? undefined),
    queryFn: () => listDebtPlans(token, householdId, financingSourceId ?? undefined),
    enabled: enabled && Boolean(financingSourceId),
  });
}

export function useDebtPlan(debtPlanId: string | null) {
  const { token, householdId, enabled } = useDebtPlanContext();
  return useQuery<DebtPlan>({
    queryKey: debtPlanQueryKey(householdId, debtPlanId ?? ""),
    queryFn: () => getDebtPlan(token, householdId, debtPlanId as string),
    enabled: enabled && Boolean(debtPlanId),
  });
}

export function useDebtSchedule(debtPlanId: string | null, horizonMonths?: number) {
  const { token, householdId, enabled } = useDebtPlanContext();
  return useQuery<DebtSchedule>({
    queryKey: [...debtScheduleQueryKey(householdId, debtPlanId ?? ""), horizonMonths ?? 360],
    queryFn: () => getDebtSchedule(token, householdId, debtPlanId as string, horizonMonths),
    enabled: enabled && Boolean(debtPlanId),
    retry: false,
  });
}

export function useCreateDebtPlan() {
  const { token, householdId } = useDebtPlanContext();
  const invalidate = useInvalidateDebtPlan();
  return useMutation<DebtPlan, Error, DebtPlanCreateInput>({
    mutationFn: (input) => createDebtPlan(token, householdId, input),
    onSuccess: (plan) => invalidate(plan.id, plan.project_financing_source_id ?? undefined),
  });
}

export function useUpdateDebtPlan() {
  const { token, householdId } = useDebtPlanContext();
  const invalidate = useInvalidateDebtPlan();
  return useMutation<
    DebtPlan,
    Error,
    { debtPlanId: string; version: number; input: DebtPlanUpdateInput }
  >({
    mutationFn: ({ debtPlanId, version, input }) =>
      updateDebtPlan(token, householdId, debtPlanId, input, version),
    onSuccess: (plan) => invalidate(plan.id, plan.project_financing_source_id ?? undefined),
  });
}

export function useDeleteDebtPlan() {
  const { token, householdId } = useDebtPlanContext();
  const invalidate = useInvalidateDebtPlan();
  return useMutation<void, Error, { debtPlanId: string; financingSourceId?: string }>({
    mutationFn: ({ debtPlanId }) => deleteDebtPlan(token, householdId, debtPlanId),
    onSuccess: (_, { debtPlanId, financingSourceId }) => invalidate(debtPlanId, financingSourceId),
  });
}

export function useReplaceDebtRatePeriods() {
  const { token, householdId } = useDebtPlanContext();
  const invalidate = useInvalidateDebtPlan();
  return useMutation<
    Awaited<ReturnType<typeof replaceDebtRatePeriods>>,
    Error,
    { debtPlanId: string; financingSourceId?: string; periods: RatePeriodInput[] }
  >({
    mutationFn: ({ debtPlanId, periods }) =>
      replaceDebtRatePeriods(token, householdId, debtPlanId, periods),
    onSuccess: (_, { debtPlanId, financingSourceId }) => invalidate(debtPlanId, financingSourceId),
  });
}

export function useReplaceDebtDraws() {
  const { token, householdId } = useDebtPlanContext();
  const invalidate = useInvalidateDebtPlan();
  return useMutation<
    Awaited<ReturnType<typeof replaceDebtDraws>>,
    Error,
    { debtPlanId: string; financingSourceId?: string; draws: DrawInput[] }
  >({
    mutationFn: ({ debtPlanId, draws }) => replaceDebtDraws(token, householdId, debtPlanId, draws),
    onSuccess: (_, { debtPlanId, financingSourceId }) => invalidate(debtPlanId, financingSourceId),
  });
}

export function useReplaceDebtFees() {
  const { token, householdId } = useDebtPlanContext();
  const invalidate = useInvalidateDebtPlan();
  return useMutation<
    Awaited<ReturnType<typeof replaceDebtFees>>,
    Error,
    { debtPlanId: string; financingSourceId?: string; fees: FeeInput[] }
  >({
    mutationFn: ({ debtPlanId, fees }) => replaceDebtFees(token, householdId, debtPlanId, fees),
    onSuccess: (_, { debtPlanId, financingSourceId }) => invalidate(debtPlanId, financingSourceId),
  });
}

export function useReplaceDebtSpecialRepayments() {
  const { token, householdId } = useDebtPlanContext();
  const invalidate = useInvalidateDebtPlan();
  return useMutation<
    Awaited<ReturnType<typeof replaceDebtSpecialRepayments>>,
    Error,
    { debtPlanId: string; financingSourceId?: string; specialRepayments: SpecialRepaymentInput[] }
  >({
    mutationFn: ({ debtPlanId, specialRepayments }) =>
      replaceDebtSpecialRepayments(token, householdId, debtPlanId, specialRepayments),
    onSuccess: (_, { debtPlanId, financingSourceId }) => invalidate(debtPlanId, financingSourceId),
  });
}
