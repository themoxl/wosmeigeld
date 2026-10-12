import { useQuery } from "@tanstack/react-query";
import { listAccounts } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/** Konten des aktuell ausgewaehlten Haushalts (GET /v1/accounts). */
export function useAccounts() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["accounts", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listAccounts(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}
