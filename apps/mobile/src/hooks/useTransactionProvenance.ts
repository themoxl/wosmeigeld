import { useQuery } from "@tanstack/react-query";
import { getTransactionProvenance } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

export const TRANSACTION_PROVENANCE_QUERY_KEY = "transactionProvenance" as const;

/**
 * Herkunft einer Buchung (GET /v1/transactions/{id}/provenance) - Bankrohtext,
 * Importherkunft und je Split, wodurch die Kategorie zustande kam
 * (docs/ANALYSE-EXPLORER-PLAN.md, Baustein A).
 */
export function useTransactionProvenance(transactionId: string | undefined) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: [TRANSACTION_PROVENANCE_QUERY_KEY, selectedHouseholdId, transactionId],
    queryFn: ({ signal }) =>
      getTransactionProvenance(
        accessToken as string,
        selectedHouseholdId as string,
        transactionId as string,
        signal,
      ),
    enabled: !!accessToken && !!selectedHouseholdId && !!transactionId,
  });
}
