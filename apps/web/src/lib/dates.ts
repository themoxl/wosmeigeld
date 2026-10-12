// Diese Datei ist die einzige Stelle, an der Datumswerte fuer die Anzeige
// formatiert werden (Pendant zu money.ts fuer Geldbetraege). Die Reihenfolge
// von Tag/Monat/Jahr richtet sich nach der Nutzereinstellung `date_format`
// (siehe useFormatDate() in hooks/useProfile.ts); Monat+Jahr-Kurzlabels
// (Chart-Achsen, Berichtskoepfe) bleiben bewusst immer im deutschen
// Wortformat, unabhaengig von dieser Einstellung - die App-Sprache ist
// durchgehend Deutsch, ein numerisches "2026-04" mitten in einer
// Chart-Achse waere stilistisch ein Bruch.

export type DateFormatPreference = "iso" | "de" | "us";

export const DATE_FORMAT_LABELS: Record<DateFormatPreference, string> = {
  iso: "ISO (2026-04-03)",
  de: "Deutsch (03.04.2026)",
  us: "US (04/03/2026)",
};

const MONTH_YEAR_FORMATTER = new Intl.DateTimeFormat("de-DE", { month: "short", year: "numeric" });
const MONTH_LONG_FORMATTER = new Intl.DateTimeFormat("de-DE", { month: "long" });
const MONTH_SHORT_FORMATTER = new Intl.DateTimeFormat("de-DE", { month: "short" });

// Ein reines Datum ohne Uhrzeit (YYYY-MM-DD) wird auf Mittag verankert, damit
// die anschliessenden lokalen getDate()/getMonth()/getFullYear()-Aufrufe in
// keiner Zeitzone ueber eine Tagesgrenze rutschen. Werte mit Uhrzeit (Time-
// stamps) werden unveraendert an Date uebergeben.
function toDate(value: string | Date): Date {
  if (value instanceof Date) return value;
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
}

/** Volles Datum gemaess Nutzereinstellung: TT.MM.JJJJ / MM/TT/JJJJ / JJJJ-MM-TT. */
export function formatDate(value: string | Date, preference: DateFormatPreference): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return typeof value === "string" ? value : "";

  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const year = date.getFullYear();

  switch (preference) {
    case "iso":
      return `${year}-${month}-${day}`;
    case "us":
      return `${month}/${day}/${year}`;
    case "de":
    default:
      return `${day}.${month}.${year}`;
  }
}

/** Datum + Uhrzeit gemaess Nutzereinstellung, z. B. "03.04.2026, 21:37". */
export function formatDateTime(value: string | Date, preference: DateFormatPreference): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return typeof value === "string" ? value : "";

  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${formatDate(date, preference)}, ${hours}:${minutes}`;
}

/** Monat+Jahr als deutsches Kurzlabel, z. B. "Apr. 2026". Unabhaengig von der Datumsformat-Einstellung. */
export function formatMonthYear(value: string | Date): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return typeof value === "string" ? value : "";
  return MONTH_YEAR_FORMATTER.format(date);
}

/** Ausgeschriebener deutscher Monatsname, z. B. "August". */
export function monthNameLong(monthIndex0: number): string {
  return MONTH_LONG_FORMATTER.format(new Date(2000, monthIndex0, 1));
}

/** Abgekuerzter deutscher Monatsname ohne Punkt, z. B. "Aug". */
export function monthNameShort(monthIndex0: number): string {
  return MONTH_SHORT_FORMATTER.format(new Date(2000, monthIndex0, 1)).replace(".", "");
}

/**
 * Datum -> "JJJJ-MM-TT" ueber die lokalen Date-Getter (nicht toISOString(),
 * das rechnet auf UTC um und liefert oestlich von Greenwich - also auch in
 * Europe/Vienna - fuer lokale Mitternacht den Vortag).
 */
export function toIsoDateString(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}
