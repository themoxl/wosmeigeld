// Architekturplan Abschnitt 2: Geldwerte sind IMMER Ganzzahlen in Cent
// (Minor Units), niemals Float. Diese Datei ist die einzige Stelle, an der
// Text aus einem Eingabefeld in Minor Units umgerechnet wird bzw. Minor
// Units fuer die Anzeige formatiert werden.

/**
 * Parst eine Betragseingabe wie "12,34", "12.34", "1.234,56", "1,234.56",
 * "1234" oder "-12,5" in ganzzahlige Minor Units (Cent).
 *
 * Bewusst KEINE Verwendung von `parseFloat(input) * 100`, weil das bei
 * Gleitkommazahlen zu Rundungsfehlern fuehren kann (z. B. 1.005 * 100 wird in
 * JS nicht exakt 100.5). Stattdessen wird der Eingabetext in einen
 * Ganzzahl- und einen Nachkommateil zerlegt und beides als Integer
 * verrechnet.
 *
 * Das jeweils LETZTE Vorkommen von "," oder "." im Text gilt als
 * Dezimaltrennzeichen; alle anderen "," / "." davor gelten als
 * Tausendertrennzeichen und werden entfernt. Der Nachkommateil darf hoechstens
 * zwei Ziffern haben (mehr ist bei einem Centbetrag nicht sinnvoll und waere
 * ohnehin nicht eindeutig von einem Tausendertrennzeichen zu unterscheiden).
 *
 * Gibt bei ungueltiger Eingabe `null` zurueck (statt zu werfen), damit
 * Formulare den Fehlerfall ohne try/catch pruefen koennen.
 */
export function parseAmountToMinorUnits(rawInput: string): number | null {
  let input = rawInput.trim();
  if (!input) {
    return null;
  }

  let negative = false;
  if (input.startsWith("-")) {
    negative = true;
    input = input.slice(1);
  } else if (input.startsWith("+")) {
    input = input.slice(1);
  }

  if (!input) {
    return null;
  }

  const lastComma = input.lastIndexOf(",");
  const lastDot = input.lastIndexOf(".");
  const decimalIndex = Math.max(lastComma, lastDot);

  let integerPart: string;
  let fractionPart: string;

  if (decimalIndex === -1) {
    integerPart = input;
    fractionPart = "";
  } else {
    integerPart = input.slice(0, decimalIndex);
    fractionPart = input.slice(decimalIndex + 1);
  }

  // Tausendertrennzeichen (beliebige weitere "," oder ".") aus dem
  // Ganzzahlteil entfernen.
  integerPart = integerPart.replace(/[.,]/g, "");

  if (!/^\d*$/.test(integerPart) || !/^\d*$/.test(fractionPart)) {
    return null;
  }

  if (integerPart === "" && fractionPart === "") {
    return null;
  }

  if (fractionPart.length > 2) {
    return null;
  }

  const normalizedFraction = fractionPart.padEnd(2, "0");
  const normalizedInteger = integerPart === "" ? "0" : integerPart;

  const minorUnits = Number(normalizedInteger) * 100 + Number(normalizedFraction);

  if (!Number.isFinite(minorUnits) || !Number.isInteger(minorUnits)) {
    return null;
  }

  return negative ? -minorUnits : minorUnits;
}

/**
 * Formatiert ganzzahlige Minor Units (Cent) als lokalisierten Geldbetrag,
 * z. B. 123456 -> "1.234,56 €". Nutzt Intl.NumberFormat, damit Rundung und
 * Tausendertrennzeichen konsistent mit dem Browser-Locale bleiben.
 */
export function formatMinorUnitsAsEuro(minorUnits: number, currency = "EUR"): string {
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency,
  }).format(minorUnits / 100);
}
