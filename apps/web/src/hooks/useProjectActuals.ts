import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  bulkAssignActuals,
  createActual,
  deleteActual,
  listActuals,
  listTransactionCandidates,
} from "../lib/api";
import type {
  BulkAssignActualsRequest,
  BulkAssignActualsResult,
  CostActual,
  CostActualCreateInput,
  TransactionCandidate,
} from "../lib/types";
import { costItemsQueryKey } from "./useProjects";

/**
 * Datenzugriff fuer die Ist-Kosten-Zuordnung (Plan 4.6/7.2, P4).
 *
 * Die Ist-Liste ist je Kostenposition gecacht (`["cost-actuals", householdId,
 * itemId]`); jede Mutation invalidiert zusaetzlich `costItemsQueryKey`, weil
 * `CostItem.actual_amount_minor` serverseitig aus den Zuordnungen abgeleitet
 * wird und sonst in der Kostentabelle veraltet bliebe.
 */

interface ActualsContext {
  token: string;
  householdId: string;
  enabled: boolean;
}

function useActualsContext(): ActualsContext {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return {
    token: accessToken ?? "",
    householdId: selectedHouseholdId ?? "",
    enabled: Boolean(accessToken && selectedHouseholdId),
  };
}

export const actualsQueryKey = (householdId: string, itemId: string) =>
  ["cost-actuals", householdId, itemId] as const;

export const transactionCandidatesQueryKey = (
  householdId: string,
  projectId: string,
  filters: TransactionCandidateFilters,
) => ["transaction-candidates", householdId, projectId, filters] as const;

export interface TransactionCandidateFilters {
  search?: string;
  accountId?: string;
  dateFrom?: string;
  dateTo?: string;
}

export function useActuals(projectId: string | null, itemId: string | null) {
  const { token, householdId, enabled } = useActualsContext();
  return useQuery<CostActual[]>({
    queryKey: actualsQueryKey(householdId, itemId ?? ""),
    queryFn: () => listActuals(token, householdId, projectId as string, itemId as string),
    enabled: enabled && Boolean(projectId) && Boolean(itemId),
  });
}

export function useCreateActual() {
  const { token, householdId } = useActualsContext();
  const queryClient = useQueryClient();
  return useMutation<
    CostActual,
    Error,
    { projectId: string; itemId: string; scenarioId: string; input: CostActualCreateInput }
  >({
    mutationFn: ({ projectId, itemId, input }) =>
      createActual(token, householdId, projectId, itemId, input),
    onSuccess: (_, { itemId, scenarioId }) => {
      void queryClient.invalidateQueries({ queryKey: actualsQueryKey(householdId, itemId) });
      void queryClient.invalidateQueries({
        queryKey: costItemsQueryKey(householdId, scenarioId),
      });
    },
  });
}

export function useDeleteActual() {
  const { token, householdId } = useActualsContext();
  const queryClient = useQueryClient();
  return useMutation<
    void,
    Error,
    { projectId: string; actualId: string; itemId: string; scenarioId: string }
  >({
    mutationFn: ({ projectId, actualId }) => deleteActual(token, householdId, projectId, actualId),
    onSuccess: (_, { itemId, scenarioId }) => {
      void queryClient.invalidateQueries({ queryKey: actualsQueryKey(householdId, itemId) });
      void queryClient.invalidateQueries({
        queryKey: costItemsQueryKey(householdId, scenarioId),
      });
    },
  });
}

export function useTransactionCandidates(
  projectId: string | null,
  filters: TransactionCandidateFilters,
) {
  const { token, householdId, enabled } = useActualsContext();
  return useQuery<TransactionCandidate[]>({
    queryKey: transactionCandidatesQueryKey(householdId, projectId ?? "", filters),
    queryFn: () =>
      listTransactionCandidates(token, householdId, projectId as string, {
        search: filters.search,
        accountId: filters.accountId,
        dateFrom: filters.dateFrom,
        dateTo: filters.dateTo,
      }),
    enabled: enabled && Boolean(projectId),
  });
}

export function useBulkAssignActuals() {
  const { token, householdId } = useActualsContext();
  const queryClient = useQueryClient();
  return useMutation<
    BulkAssignActualsResult,
    Error,
    { projectId: string; scenarioId: string; input: BulkAssignActualsRequest }
  >({
    mutationFn: ({ projectId, input }) => bulkAssignActuals(token, householdId, projectId, input),
    onSuccess: (_, { input, scenarioId }) => {
      for (const item of input.items) {
        void queryClient.invalidateQueries({
          queryKey: actualsQueryKey(householdId, item.cost_item_id),
        });
      }
      void queryClient.invalidateQueries({
        queryKey: costItemsQueryKey(householdId, scenarioId),
      });
    },
  });
}
