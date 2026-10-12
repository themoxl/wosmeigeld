import { useQuery } from "@tanstack/react-query";
import {
  getCashflowTrend,
  getFixedCostsTrend,
  getProjectReportsOverview,
  runAnalyticsQuery,
} from "../api/client";
import type {
  AnalyticsQuery,
  AnalyticsResult,
  CashflowTrendResponse,
  FixedCostsTrendResponse,
  ProjectReportRow,
} from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";

/**
 * Datenzugang der Berichte (docs/ANALYSE-EXPLORER-PLAN.md, Baustein E).
 *
 * Aufbau wie useForecast.ts: Query-Key beginnt mit der Familie, danach der
 * Haushalt, danach die Parameter; `enabled` haengt an Token und Haushalt.
 *
 * Gemeinsame Familie "reports" fuer alle Register: sie haengen an denselben
 * Quellen (Buchungen, Kategorien, Vertraege, Vorhaben) und muessen zusammen
 * ungueltig werden. Die Familie steht bewusst NICHT in PERSISTED_QUERY_KEYS
 * (../api/queryClient.ts) - Berichte sind Auswertungen ueber den gesamten
 * Buchungsbestand und haetten auf der Platte einen weit groesseren
 * Datenabdruck als die drei bereits abgewogenen Familien.
 */
export const REPORTS_QUERY_KEY = "reports";

/** Auswaehlbare Zeitraeume. Zwoelf Monate zeigen ein volles Jahr inklusive Vorjahresmonat. */
export const REPORT_MONTH_OPTIONS = [12, 24] as const;
export type ReportMonthCount = (typeof REPORT_MONTH_OPTIONS)[number];

/** Einnahmen, Ausgaben und Saldo je Monat - die Ist-Reihe. */
export function useCashflowTrend(months: number) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<CashflowTrendResponse>({
    queryKey: [REPORTS_QUERY_KEY, selectedHouseholdId, "cashflow-trend", months],
    queryFn: ({ signal }) =>
      getCashflowTrend(accessToken as string, selectedHouseholdId as string, signal, months),
    enabled: !!accessToken && !!selectedHouseholdId,
  });
}

/** Verlauf der VEREINBARTEN Fixkosten - nicht mit der Ist-Reihe vergleichbar. */
export function useFixedCostsTrend(months: number, enabled = true) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<FixedCostsTrendResponse>({
    queryKey: [REPORTS_QUERY_KEY, selectedHouseholdId, "fixed-costs-trend", months],
    queryFn: ({ signal }) =>
      getFixedCostsTrend(accessToken as string, selectedHouseholdId as string, signal, months),
    enabled: enabled && !!accessToken && !!selectedHouseholdId,
  });
}

/**
 * Eine Berichtsabfrage gegen /v1/analytics.
 *
 * `enabled` steuert, dass nur das sichtbare Register laedt - beim Oeffnen des
 * Screens sonst sechs Auswertungen gleichzeitig, von denen fuenf niemand
 * ansieht. Der `label`-Teil des Schluessels haelt die Register auseinander,
 * ohne die ganze Abfrage in den Schluessel zu serialisieren.
 */
export function useReportQuery(label: string, query: AnalyticsQuery, enabled: boolean) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<AnalyticsResult>({
    queryKey: [REPORTS_QUERY_KEY, selectedHouseholdId, "analytics", label, query],
    queryFn: ({ signal }) =>
      runAnalyticsQuery(accessToken as string, selectedHouseholdId as string, query, signal),
    enabled: enabled && !!accessToken && !!selectedHouseholdId,
  });
}

/** Forecast/Ist/Abweichung je aktivem Vorhaben. */
export function useProjectReports(enabled = true) {
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  return useQuery<ProjectReportRow[]>({
    queryKey: [REPORTS_QUERY_KEY, selectedHouseholdId, "projects"],
    queryFn: ({ signal }) =>
      getProjectReportsOverview(accessToken as string, selectedHouseholdId as string, signal),
    enabled: enabled && !!accessToken && !!selectedHouseholdId,
  });
}
