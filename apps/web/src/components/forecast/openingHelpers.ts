import type { ForecastOpeningAccount } from "../../lib/types";

// Eigene Datei, weil `OpeningBreakdown.tsx` sonst Komponente UND Funktionen
// exportiert - das bricht Fast Refresh (react-refresh/only-export-components).

/** Summe der Konten, die bewusst draussen bleiben. */
export function excludedTotalMinor(accounts: ForecastOpeningAccount[]): number {
  return accounts.filter((a) => !a.included).reduce((sum, a) => sum + a.balance_minor, 0);
}

/**
 * Aeltester Buchungsstand unter den einbezogenen Konten.
 *
 * Nicht der juengste: fuer die Frage "wie alt ist meine Grundlage" zaehlt das
 * schwaechste Glied. Ein taeglich importiertes Girokonto macht ein seit
 * Wochen unberuehrtes Zweitkonto nicht aktuell.
 */
export function oldestIncludedBooking(accounts: ForecastOpeningAccount[]): string | null {
  const dates = accounts
    .filter((a) => a.included && a.last_booking_on)
    .map((a) => a.last_booking_on as string)
    .sort();
  return dates[0] ?? null;
}
