import { useQuery } from "@tanstack/react-query";
import { listCategories } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/** Kategoriegruppen inkl. Kategorien des aktuell ausgewaehlten Haushalts (GET /v1/categories). */
export function useCategories() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery({
    queryKey: ["categories", selectedHouseholdId],
    queryFn: ({ signal }) =>
      listCategories(accessToken as string, selectedHouseholdId as string, signal),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}
