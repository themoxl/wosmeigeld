import { useQuery } from "@tanstack/react-query";
import { getTransaction } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

export const TRANSACTION_DETAIL_QUERY_KEY = "transactionDetail" as const;

/** Eine einzelne Buchung inkl. Splits (GET /v1/transactions/{id}) fuer die Detailseite (Auftrag Punkt 2). */
export function useTransactionDetail(transactionId: string | undefined) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: [TRANSACTION_DETAIL_QUERY_KEY, selectedHouseholdId, transactionId],
    queryFn: ({ signal }) =>
      getTransaction(
        accessToken as string,
        selectedHouseholdId as string,
        transactionId as string,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!transactionId,
  });
}
