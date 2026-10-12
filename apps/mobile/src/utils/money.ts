// Geldwerte sind IMMER Ganzzahlen in Cent (Minor Units), niemals Float
// (siehe FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 2). Bei der Texteingabe
// eines Betrags (z. B. "12,34") wird deshalb der String zerlegt und in
// Ganzzahl-Arithmetik umgerechnet, NICHT `parseFloat(...) * 100`
// (Rundungsfehler, z. B. parseFloat("1.15") * 100 === 114.99999999999999).
//
// Unterstuetzt werden deutsches ("1.234,56") und englisches ("1,234.56")
// Tausender-/Dezimaltrennzeichen-Muster sowie reine Ganzzahlen ("12" ->
// 1200 Cent). Optionales fuehrendes Vorzeichen ("-12,34") wird uebernommen;
// im Buchungsformular wird das Vorzeichen zusaetzlich ueber den separaten
// Ausgabe/Einnahme-Umschalter gesetzt.
export function parseAmountToMinorUnits(raw: string): number {
  const trimmed = raw.trim();
  if (trimmed.length === 0) {
    throw new Error("Bitte einen Betrag eingeben.");
  }

  const negative = trimmed.startsWith("-");
  const unsigned = trimmed.replace(/^[+-]/, "");

  if (!/^[0-9.,\s]+$/.test(unsigned)) {
    throw new Error("Betrag enthält ungültige Zeichen.");
  }

  const lastComma = unsigned.lastIndexOf(",");
  const lastDot = unsigned.lastIndexOf(".");
  const decimalIndex = Math.max(lastComma, lastDot);

  const integerRaw = decimalIndex === -1 ? unsigned : unsigned.slice(0, decimalIndex);
  const fractionRaw = decimalIndex === -1 ? "" : unsigned.slice(decimalIndex + 1);

  // Alles ausser Ziffern im Ganzzahlteil sind Tausendertrennzeichen -
  // entfernen. Im Nachkommateil koennen (bei vertauschten Trennzeichen wie
  // "1,234.56") ebenfalls noch Tausendertrennzeichen uebrig sein.
  const integerDigits = integerRaw.replace(/[.,\s]/g, "");
  const fractionDigits = fractionRaw.replace(/[.,\s]/g, "");

  if (integerDigits.length === 0 && fractionDigits.length === 0) {
    throw new Error("Bitte einen Betrag eingeben.");
  }

  const wholeUnits = integerDigits.length === 0 ? 0 : Number.parseInt(integerDigits, 10);

  let cents: number;
  if (fractionDigits.length === 0) {
    cents = 0;
  } else if (fractionDigits.length === 1) {
    cents = Number.parseInt(fractionDigits, 10) * 10;
  } else {
    // Auf zwei Nachkommastellen runden (kaufmaennisch), ohne Float-Division.
    const kept = fractionDigits.slice(0, 2);
    const roundUp = Number.parseInt(fractionDigits.charAt(2) || "0", 10) >= 5;
    cents = Number.parseInt(kept, 10) + (roundUp ? 1 : 0);
  }

  const minorUnits = wholeUnits * 100 + cents;
  return negative ? -minorUnits : minorUnits;
}

// Waehrungszeichen statt Waehrungscode in der Anzeige: "12,34 €" liest sich
// auf einem Handydisplay schneller als "12,34 EUR" und ist genau die
// Darstellung aus docs/mobile_redesign_showcase.html. Waehrungen ohne
// gebraeuchliches Zeichen behalten ihren Code (z. B. "10,00 CHF").
const CURRENCY_SYMBOLS: Record<string, string> = {
  EUR: "€",
  USD: "$",
  GBP: "£",
  JPY: "¥",
};

/** Waehrungszeichen einer ISO-Waehrung, sonst der Code selbst. */
export function currencySymbol(currency = "EUR"): string {
  return CURRENCY_SYMBOLS[currency.toUpperCase()] ?? currency;
}

// Formatiert Cent-Ganzzahlen fuer die Anzeige (z. B. "12,34 €"), ebenfalls
// ohne Umweg ueber eine Float-Division fuer die Cent-/Ganzzahlaufteilung.
export function formatMinorUnits(minorUnits: number, currency = "EUR"): string {
  const sign = minorUnits < 0 ? "-" : "";
  const absolute = Math.abs(minorUnits);
  const whole = Math.floor(absolute / 100);
  const cents = absolute % 100;
  const wholeGrouped = whole.toLocaleString("de-DE");
  return `${sign}${wholeGrouped},${String(cents).padStart(2, "0")} ${currencySymbol(currency)}`;
}

/**
 * Verkuerzte Darstellung fuer enge Stellen (Diagrammmitte, Achsen, Kacheln):
 * "28.450,00 €" wird zu "28,5k €". Ab einer Million entsprechend "1,2 Mio €".
 * Bewusst NICHT fuer Listen und Details verwenden - dort zaehlt der Cent.
 */
export function formatMinorUnitsCompact(minorUnits: number, currency = "EUR"): string {
  const sign = minorUnits < 0 ? "-" : "";
  const units = Math.abs(minorUnits) / 100;
  const symbol = currencySymbol(currency);

  if (units >= 1_000_000) {
    return `${sign}${(units / 1_000_000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Mio ${symbol}`;
  }
  if (units >= 10_000) {
    return `${sign}${(units / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })}k ${symbol}`;
  }
  return `${sign}${units.toLocaleString("de-DE", { maximumFractionDigits: 0 })} ${symbol}`;
}

// Wie formatMinorUnits(), aber ohne Waehrungssuffix und Tausendertrennzeichen
// - gedacht, um einen bestehenden Betrag in ein editierbares Textfeld
// vorzubelegen (z. B. Splits-Bearbeitung in der Buchungsdetailseite), dessen
// Wert per parseAmountToMinorUnits() wieder eingelesen wird.
export function formatMinorUnitsForInput(minorUnits: number): string {
  const sign = minorUnits < 0 ? "-" : "";
  const absolute = Math.abs(minorUnits);
  const whole = Math.floor(absolute / 100);
  const cents = absolute % 100;
  return `${sign}${whole},${String(cents).padStart(2, "0")}`;
}
