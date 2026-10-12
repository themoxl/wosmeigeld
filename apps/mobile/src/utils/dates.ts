// Zentrale Datumsformatierung fuer die mobile App
// (docs/MOBILE-APP-REDESIGN-PLAN.md, Abschnitt 3: "Zentrale Formatter statt
// weiterer ASCII-Labelkopien"). Bisher formatierte jeder Screen Datumswerte
// einzeln; das fuehrt zu abweichenden Formaten ("2026-08-09" roh in
// vertraege.tsx) und macht Aenderungen muehsam. Alle drei geforderten Faelle
// - Monatsname, Buchungstag, Faelligkeitsdatum - leben deshalb hier.
//
// Andere Stellen im Code (ProvenanceCard.tsx, mehr/einstellungen.tsx,
// vorhaben/[id].tsx) verlassen sich bereits erfolgreich auf
// `toLocaleDateString("de-DE")`, also auf dieselbe ICU-Locale-Unterstuetzung
// wie Intl.DateTimeFormat. mehr/review.tsx weicht mit einer fest codierten
// Monatsliste bewusst aus ("Hermes bringt je nach Build keine vollstaendigen
// Locale-Daten mit") - das war eine defensive Einzelentscheidung fuer diese
// Stelle, kein Befund gegen Intl.DateTimeFormat generell. Der Redesign-Plan
// (Abschnitt 3) verlangt hier ausdruecklich Intl.DateTimeFormat("de-DE").

// API-Datumswerte kommen als reine Kalendertag-Strings ohne Uhrzeit
// ("2026-08-09"). `new Date("2026-08-09")` interpretiert das als
// UTC-Mitternacht - in Zeitzonen westlich von UTC koennte der lokale Tag
// dadurch auf den Vortag zurueckfallen. parseIsoDate() zerlegt den String
// deshalb selbst und baut ein lokales Datum, damit immer derselbe Kalendertag
// angezeigt wird, der auch in der Datenbank steht.
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})/;

export function parseIsoDate(isoDate: string): Date {
  const match = ISO_DATE_PATTERN.exec(isoDate);
  if (!match) {
    return new Date(isoDate);
  }
  const [, year, month, day] = match;
  return new Date(Number(year), Number(month) - 1, Number(day));
}

function toDate(value: Date | string): Date {
  return typeof value === "string" ? parseIsoDate(value) : value;
}

/**
 * Lokaler Kalendertag als ISO-String. `toISOString()` waere UTC und kann in
 * Zeitzonen oestlich von UTC einen Tag danebenliegen.
 */
export function toIsoDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/**
 * Erster und letzter Kalendertag eines Monats als ISO-Strings - die
 * Zeitraumgrenzen jeder monatsbezogenen Buchungsabfrage.
 */
export function monthRange(year: number, month: number): { fromDate: string; toDate: string } {
  return {
    fromDate: toIsoDate(new Date(year, month - 1, 1)),
    // Tag 0 des Folgemonats ist der letzte Tag dieses Monats.
    toDate: toIsoDate(new Date(year, month, 0)),
  };
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function isSameCalendarDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

const monthYearFormatter = new Intl.DateTimeFormat("de-DE", {
  month: "long",
  year: "numeric",
});

/**
 * Monatsname fuer den Monatsnavigator, z. B. "August 2026"
 * (Redesign-Plan Abschnitt 3, gemeinsam fuer Buchungen und Budget).
 */
export function formatMonthLabel(date: Date | string): string {
  return monthYearFormatter.format(toDate(date));
}

const longDateFormatter = new Intl.DateTimeFormat("de-DE", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

/**
 * Faelligkeitsdatum eines Vertrags, z. B. "9. August 2026"
 * (loest die bisher unformatiert angezeigten `next_due_date`-Strings in
 * vertraege.tsx ab).
 */
export function formatDueDate(date: Date | string): string {
  return longDateFormatter.format(toDate(date));
}

const shortDayFormatter = new Intl.DateTimeFormat("de-DE", {
  day: "2-digit",
  month: "2-digit",
});

/**
 * Kurzes Tagesdatum ohne Jahr, z. B. "22.10." - fuer Aussagen, in denen das
 * Datum neben einem Betrag steht und der Satz sonst zu lang wird ("Am 22.10.
 * fehlen 245,00 EUR", Ursachenkette, Zahlungsliste der Liquiditaetsvorschau).
 *
 * Das Jahr entfaellt bewusst: die Vorschau zeigt tagesgenaue Werte nur im
 * naheliegenden Fenster, dort ist es eindeutig. Wo ein Datum allein steht und
 * jahresuebergreifend sein kann, bleibt formatDueDate() richtig.
 */
export function formatShortDate(date: Date | string): string {
  return shortDayFormatter.format(toDate(date));
}

/**
 * Ob ein Vertrag am Referenzdatum noch nicht begonnen hat: dieselbe Regel wie
 * `starts_on > today` in services/api/app/services/contract_service.py
 * (build_summary), hier auf lokale ISO-Kalendertag-Strings angewandt. Die
 * Vertragsliste zeigt einen solchen Vertrag weiterhin an, aber mit
 * "Beginnt am ..." statt der naechsten Faelligkeit (Redesign-Plan
 * Abschnitt 7, "Datenkorrektur").
 */
export function isContractNotStarted(
  startsOn: string | null,
  referenceDate: Date = new Date(),
): boolean {
  if (!startsOn) return false;
  const today = startOfDay(referenceDate);
  const start = startOfDay(toDate(startsOn));
  return start.getTime() > today.getTime();
}

/**
 * Buchungstag fuer Tagessektionen in der Buchungsliste: "Heute", "Gestern",
 * sonst ein lokalisiertes Datum (Redesign-Plan Abschnitt 5). `referenceDate`
 * ist fuer Tests da, damit "Heute"/"Gestern" nicht vom Systemdatum abhaengen.
 */
export function formatTransactionDayLabel(
  date: Date | string,
  referenceDate: Date = new Date(),
): string {
  const day = startOfDay(toDate(date));
  const today = startOfDay(referenceDate);

  if (isSameCalendarDay(day, today)) {
    return "Heute";
  }

  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (isSameCalendarDay(day, yesterday)) {
    return "Gestern";
  }

  return longDateFormatter.format(day);
}
