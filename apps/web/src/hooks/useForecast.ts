import { useMutation, useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import {
  getForecast,
  getForecastSettings,
  getForecastUpcoming,
  simulateForecast,
  updateForecastSettings,
} from "../lib/api";
import type {
  ForecastDayGroup,
  ForecastGranularity,
  ForecastResponse,
  ForecastSettings,
  ForecastSettingsInput,
  ForecastSimulationInput,
  ForecastSimulationResponse,
} from "../lib/types";

/**
 * Liquiditaetsvorschau.
 *
 * Kein `staleTime`: die Vorschau haengt an Buchungen, Konten, Vertraegen,
 * Budgets und Ruecklagen. Jede Mutation in diesen Bereichen invalidiert
 * `["forecast", householdId]` (siehe FORECAST_QUERY_KEY) - ein laenger
 * gehaltener Cache wuerde nach einer Buchungsaenderung stumm veraltete Zahlen
 * zeigen, und das ist bei einer Warnfunktion schlimmer als ein Nachladen.
 */
export const FORECAST_QUERY_KEY = "forecast";

export function useForecast(
  householdIdOverride: string | null | undefined,
  params: { horizonMonths?: number; granularity?: ForecastGranularity; includeSavings?: boolean },
) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const householdId = householdIdOverride ?? selectedHouseholdId;

  return useQuery<ForecastResponse>({
    queryKey: [
      FORECAST_QUERY_KEY,
      householdId,
      params.horizonMonths ?? null,
      params.granularity ?? "day",
      params.includeSavings ?? false,
    ],
    queryFn: () => getForecast(accessToken as string, householdId as string, params),
    enabled: Boolean(accessToken && householdId),
  });
}

export function useForecastUpcoming(days = 90) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<ForecastDayGroup[]>({
    queryKey: [FORECAST_QUERY_KEY, "upcoming", selectedHouseholdId, days],
    queryFn: () => getForecastUpcoming(accessToken as string, selectedHouseholdId as string, days),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
}

export function useForecastSettings() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<ForecastSettings>({
    queryKey: [FORECAST_QUERY_KEY, "settings", selectedHouseholdId],
    queryFn: () => getForecastSettings(accessToken as string, selectedHouseholdId as string),
    enabled: Boolean(accessToken && selectedHouseholdId),
  });
}

export function useUpdateForecastSettings() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useMutation<ForecastSettings, Error, ForecastSettingsInput>({
    mutationFn: (input) =>
      updateForecastSettings(accessToken as string, selectedHouseholdId as string, input),
  });
}

/**
 * Simulation. Bewusst eine Mutation und keine Query: sie hat einen Body, wird
 * bei jeder Eingabe neu ausgeloest und soll nichts zwischenspeichern - ein
 * Szenarioentwurf ist per Definition fluechtig (Plan Abschnitt 6.1).
 */
export function useForecastSimulation() {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useMutation<ForecastSimulationResponse, Error, ForecastSimulationInput>({
    // Schluessel, damit die zentrale Invalidierung in main.tsx die Simulation
    // ueberspringt: sie aendert keinen Bestand.
    mutationKey: [FORECAST_QUERY_KEY, "simulate"],
    mutationFn: (input) =>
      simulateForecast(accessToken as string, selectedHouseholdId as string, input),
  });
}
