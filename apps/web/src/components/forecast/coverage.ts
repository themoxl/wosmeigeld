import type { ForecastResponse } from "../../lib/types";

/**
 * Anteil der regelmaessigen Zahlungsstroeme, die als Vertrag modelliert sind.
 *
 * Alles ohne Vertragsdeckung landet im variablen Topf - je groesser der ist,
 * desto weniger weiss die Vorschau ueber die Zukunft. Die Zahl steht in der
 * Datenqualitaetsleiste und als Abnahme am Ende der Einrichtung.
 *
 * Eigene Datei, weil `ForecastSetup.tsx` sonst Komponente UND Funktion
 * exportiert - das bricht Fast Refresh (react-refresh/only-export-components).
 */
export function coveragePercent(forecast: ForecastResponse): number {
  const m = forecast.metrics;
  const modelled = m.income_monthly_minor + m.fixed_monthly_minor;
  const total = modelled + m.variable_monthly_minor;
  if (total <= 0) return 0;
  return Math.round((modelled / total) * 100);
}
