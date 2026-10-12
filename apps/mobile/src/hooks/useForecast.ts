import { useQuery } from "@tanstack/react-query";
import { getForecast, getForecastUpcoming } from "../api/client";
import type { ForecastDayGroup, ForecastGranularity, ForecastResponse } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/**
 * Liquiditaetsvorschau (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Stufe 5).
 *
 * Aufbau wie useBudget.ts: Query-Key beginnt mit der Familie, danach der
 * Haushalt, danach die Parameter; `enabled` haengt an Token und Haushalt.
 *
 * Gemeinsame Familie "forecast" fuer Vorschau und "naechste Zahlungen": beide
 * haengen an denselben Quellen (Buchungen, Konten, Vertraege, Budgets,
 * Ruecklagen), muessen also immer gemeinsam invalidiert werden - und werden
 * gemeinsam vom persistenten Cache ausgeschlossen (siehe
 * ../api/queryClient.ts, PERSISTED_QUERY_KEYS).
 */
export const FORECAST_QUERY_KEY = "forecast";

/**
 * Horizont der Karte auf "Heute". Bewusst kuerzer als der Screen: die Karte
 * sagt "in den naechsten 90 Tagen", und `metrics` bezieht sich immer auf den
 * ANGEFRAGTEN Horizont. Mit einem Halbjahreshorizont wuerde die Karte einen
 * Engpass im fuenften Monat als heutige Botschaft ausgeben.
 */
export const FORECAST_CARD_HORIZON_MONTHS = 3;

/**
 * Horizont des Vorschau-Screens: sechs Monatskarten, und `daily` reicht damit
 * weit ueber die 90 Tage der Sparkline hinaus - eine zweite Anfrage nur fuer
 * die Monatsreihe waere derselbe Rechenlauf ein zweites Mal.
 */
export const FORECAST_SCREEN_HORIZON_MONTHS = 6;

/** Tagesfenster der Sparkline und der Zahlungsliste. */
export const FORECAST_DAILY_WINDOW_DAYS = 90;

interface ForecastQueryOptions {
  horizonMonths?: number;
  granularity?: ForecastGranularity;
  includeSavings?: boolean;
}

/**
 * `staleTime: 0` ueberschreibt den globalen 30-Sekunden-Default absichtlich:
 * eine Prognose auf veralteten Daten ist eine Luege (Leitsatz 6). Sie wird
 * deshalb bei jedem Oeffnen neu gerechnet, statt aus dem Cache zu kommen.
 */
export function useForecast(options: ForecastQueryOptions = {}) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const horizonMonths = options.horizonMonths ?? null;
  const granularity = options.granularity ?? "day";
  const includeSavings = options.includeSavings ?? false;

  return useQuery<ForecastResponse>({
    queryKey: [FORECAST_QUERY_KEY, selectedHouseholdId, horizonMonths, granularity, includeSavings],
    queryFn: ({ signal }) =>
      getForecast(accessToken as string, selectedHouseholdId as string, signal, {
        horizonMonths: horizonMonths ?? undefined,
        granularity,
        includeSavings,
      }),
    enabled: !!accessToken && !!selectedHouseholdId,
    staleTime: 0,
  });
}

/** Die naechsten Zahlungen, nach Tagen gruppiert (GET /v1/forecast/upcoming). */
export function useForecastUpcoming(days = FORECAST_DAILY_WINDOW_DAYS) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<ForecastDayGroup[]>({
    queryKey: [FORECAST_QUERY_KEY, selectedHouseholdId, "upcoming", days],
    queryFn: ({ signal }) =>
      getForecastUpcoming(accessToken as string, selectedHouseholdId as string, signal, days),
    enabled: !!accessToken && !!selectedHouseholdId,
    staleTime: 0,
  });
}
