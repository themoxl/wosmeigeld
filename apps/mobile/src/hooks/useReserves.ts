import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createReserveDeposit,
  createReserveReallocation,
  createReserveWithdrawal,
  getReserveOverview,
  previewReserveDistribution,
  skipReserveMonth,
  undoSkipReserveMonth,
} from "../api/client";
import type {
  ReserveDepositRequest,
  ReserveDistributionPreviewRequest,
  ReserveReallocationRequest,
  ReserveSkipRequest,
  ReserveWithdrawalRequest,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

export const reserveQueryKey = (householdId: string | null, year: number, month: number) =>
  ["reserves", householdId, year, month] as const;

export function useReserveOverview(year: number, month: number, visible = true) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  return useQuery({
    queryKey: reserveQueryKey(selectedHouseholdId, year, month),
    queryFn: ({ signal }) =>
      getReserveOverview(accessToken as string, selectedHouseholdId as string, year, month, signal),
    enabled:
      visible &&
      Boolean(accessToken && selectedHouseholdId) &&
      year > 0 &&
      month >= 1 &&
      month <= 12,
  });
}

/**
 * Alle Ledger-Schreibvorgänge sind absichtlich online-only, nicht
 * optimistisch und nicht mit der Buchungs-Offlinequeue verbunden. Erst die
 * Serverantwort aktualisiert die sichtbaren Stände.
 */
export function useReserveActions(year: number, month: number) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const queryClient = useQueryClient();
  const householdId = selectedHouseholdId ?? "";
  const token = accessToken ?? "";
  const canEdit = selectedHousehold?.role === "owner" || selectedHousehold?.role === "editor";

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["reserves", householdId] }),
      queryClient.invalidateQueries({ queryKey: ["budget", householdId] }),
      queryClient.invalidateQueries({ queryKey: ["goals", householdId] }),
      queryClient.invalidateQueries({ queryKey: ["contracts", householdId] }),
      queryClient.invalidateQueries({ queryKey: ["contract-summary", householdId] }),
    ]);
  };

  const deposit = useMutation({
    mutationFn: (input: ReserveDepositRequest) => createReserveDeposit(token, householdId, input),
    networkMode: "online" as const,
    onSuccess: invalidate,
  });
  const withdraw = useMutation({
    mutationFn: (input: ReserveWithdrawalRequest) =>
      createReserveWithdrawal(token, householdId, input),
    networkMode: "online" as const,
    onSuccess: invalidate,
  });
  const reallocate = useMutation({
    mutationFn: (input: ReserveReallocationRequest) =>
      createReserveReallocation(token, householdId, input),
    networkMode: "online" as const,
    onSuccess: invalidate,
  });
  const skip = useMutation({
    mutationFn: (input: ReserveSkipRequest) => skipReserveMonth(token, householdId, input),
    networkMode: "online" as const,
    onSuccess: invalidate,
  });
  const undoSkip = useMutation({
    mutationFn: (input: ReserveSkipRequest) => undoSkipReserveMonth(token, householdId, input),
    networkMode: "online" as const,
    onSuccess: invalidate,
  });
  const preview = useMutation({
    mutationFn: (input: ReserveDistributionPreviewRequest) =>
      previewReserveDistribution(token, householdId, input),
    networkMode: "online" as const,
  });

  return {
    canEdit,
    year,
    month,
    deposit,
    withdraw,
    reallocate,
    skip,
    undoSkip,
    preview,
    isPending:
      deposit.isPending ||
      withdraw.isPending ||
      reallocate.isPending ||
      skip.isPending ||
      undoSkip.isPending ||
      preview.isPending,
  };
}
