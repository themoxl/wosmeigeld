// Monatswahl fuer die Was-waere-wenn-Zeilen.
//
// Bewusst zwei Auswahlfelder statt <input type="month">: Firefox kennt diesen
// Eingabetyp nicht und faellt auf ein rohes Textfeld zurueck - dort entstehen
// Zeichenketten wie "-012-01", die dann als kaputtes Datum beim Server landen.
// Ein Auswahlfeld hat keinen Tippzustand, also kann es diesen Fall gar nicht
// geben, und Firefox, Chrome und Edge verhalten sich gleich.

import { monthNameLong } from "../../lib/dates";
import { addMonths, isMonthValue } from "./changeDrafts";

interface MonthSelectProps {
  /** "JJJJ-MM" oder null, wenn kein Monat gesetzt ist (nur mit allowEmpty). */
  value: string | null;
  onChange: (month: string | null) => void;
  /** Grundlage der deutschen Bezeichnungen, z. B. "Beginn Neue Ausgabe". */
  ariaBase: string;
  /** Monat, um den herum die Jahresliste aufgespannt wird. */
  anchorMonth: string;
  /** Kein frueherer Monat als dieser. */
  min?: string | null;
  /** Darf leer bleiben — dann erscheint statt der Felder ein Knopf. */
  allowEmpty?: boolean;
}

const YEARS_BACK = 1;
const YEARS_AHEAD = 10;

function yearOf(monthValue: string): number {
  return Number(monthValue.slice(0, 4));
}

function monthOf(monthValue: string): string {
  return monthValue.slice(5, 7);
}

/**
 * Die Jahre, die zur Auswahl stehen. Ein geladenes Szenario kann ausserhalb
 * des Fensters liegen; dann wird sein Jahr zusaetzlich einsortiert, statt dass
 * das Auswahlfeld stillschweigend ein anderes anzeigt und den Wert beim
 * naechsten Speichern ueberschreibt.
 */
function yearOptions(anchorMonth: string, value: string | null, min: string | null): number[] {
  const anchor = yearOf(anchorMonth);
  const years = new Set<number>();
  for (let y = anchor - YEARS_BACK; y <= anchor + YEARS_AHEAD; y += 1) years.add(y);
  if (value) years.add(yearOf(value));
  const lowest = min ? yearOf(min) : null;
  return [...years]
    .filter((y) => lowest === null || y >= lowest || (value !== null && y === yearOf(value)))
    .sort((a, b) => a - b);
}

export function MonthSelect({
  value,
  onChange,
  ariaBase,
  anchorMonth,
  min = null,
  allowEmpty = false,
}: MonthSelectProps) {
  const anchor = isMonthValue(anchorMonth) ? anchorMonth : "2026-01";
  const lowest = min && isMonthValue(min) ? min : null;

  if (allowEmpty && !value) {
    return (
      <button
        type="button"
        className="btn-secondary forecast-month-set"
        onClick={() => onChange(addMonths(lowest ?? anchor, 12))}
      >
        Enddatum festlegen
      </button>
    );
  }

  // Ein unbrauchbarer Wert von aussen darf das Feld nicht sprengen: dann steht
  // der Ankermonat da, und die naechste Auswahl setzt ihn wieder gerade.
  const current = value && isMonthValue(value) ? value : anchor;
  const years = yearOptions(anchor, current, lowest);
  const clamp = (month: string) => (lowest && month < lowest ? lowest : month);

  return (
    <div className="forecast-month" role="group" aria-label={ariaBase}>
      <button
        type="button"
        className="forecast-month-step"
        aria-label={`${ariaBase}: einen Monat zurück`}
        disabled={Boolean(lowest) && current <= lowest!}
        onClick={() => onChange(clamp(addMonths(current, -1)))}
      >
        <span aria-hidden="true">‹</span>
      </button>

      <select
        className="form-input forecast-month-name"
        aria-label={`${ariaBase} Monat`}
        value={monthOf(current)}
        onChange={(event) => onChange(`${yearOf(current)}-${event.target.value}`)}
      >
        {Array.from({ length: 12 }, (_, index) => {
          const month = String(index + 1).padStart(2, "0");
          const candidate = `${yearOf(current)}-${month}`;
          return (
            <option key={month} value={month} disabled={Boolean(lowest) && candidate < lowest!}>
              {monthNameLong(index)}
            </option>
          );
        })}
      </select>

      <select
        className="form-input forecast-month-year"
        aria-label={`${ariaBase} Jahr`}
        value={String(yearOf(current))}
        onChange={(event) => onChange(clamp(`${event.target.value}-${monthOf(current)}`))}
      >
        {years.map((year) => (
          <option key={year} value={String(year)}>
            {year}
          </option>
        ))}
      </select>

      <button
        type="button"
        className="forecast-month-step"
        aria-label={`${ariaBase}: einen Monat vor`}
        onClick={() => onChange(addMonths(current, 1))}
      >
        <span aria-hidden="true">›</span>
      </button>

      {allowEmpty ? (
        <button
          type="button"
          className="forecast-month-clear"
          aria-label={`${ariaBase} entfernen`}
          onClick={() => onChange(null)}
        >
          ohne Ende
        </button>
      ) : null}
    </div>
  );
}
