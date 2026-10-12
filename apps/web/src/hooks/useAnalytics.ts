import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { getAnalyticsFacets, getAnalyticsFields, runAnalyticsQuery } from "../lib/api";
import type { AnalyticsQuery, AnalyticsResult, FacetResult, FieldCatalog } from "../lib/types";

// Zugriff auf den Analyse-Abfragevertrag (docs/ANALYSE-EXPLORER-PLAN.md,
// Baustein B). Der Query-Key traegt die vollstaendige Abfrage - damit
// cachet React Query je Filterkombination und ein Zurueckspringen auf einen
// vorherigen Filterstand kostet keinen Serverabruf.

export function useFieldCatalog() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<FieldCatalog>({
    queryKey: ["analytics-fields", selectedHouseholdId],
    queryFn: () => getAnalyticsFields(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
    // Der Katalog aendert sich nur mit einem Deployment.
    staleTime: 30 * 60 * 1000,
  });
}

export function useAnalyticsQuery(query: AnalyticsQuery, enabled = true) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<AnalyticsResult>({
    queryKey: ["analytics", selectedHouseholdId, query],
    queryFn: ({ signal }) =>
      runAnalyticsQuery(accessToken as string, selectedHouseholdId as string, query, signal),
    enabled: Boolean(accessToken && selectedHouseholdId) && enabled,
    // Beim Filtern soll die vorherige Tabelle stehen bleiben, statt bei jedem
    // Tastendruck auf einen Ladezustand zu springen.
    placeholderData: (previous) => previous,
  });
}

export function useFacets(field: string, query: AnalyticsQuery, enabled: boolean, search?: string) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  // Die Werteliste ist serverseitig gedeckelt. Ohne durchgereichte Suche
  // waeren die Werte jenseits der Deckelung ueberhaupt nicht erreichbar - der
  // Hinweis "bitte suchen, um weitere zu finden" waere dann eine Luege.
  const trimmed = search?.trim() || undefined;

  return useQuery<FacetResult>({
    queryKey: ["analytics-facets", selectedHouseholdId, field, query, trimmed ?? null],
    queryFn: ({ signal }) =>
      getAnalyticsFacets(
        accessToken as string,
        selectedHouseholdId as string,
        { field, query, limit: 200, search: trimmed ?? null },
        signal,
      ),
    enabled: Boolean(accessToken && selectedHouseholdId) && enabled,
    placeholderData: (previous) => previous,
  });
}
