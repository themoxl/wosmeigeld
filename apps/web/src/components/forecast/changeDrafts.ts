// Entwurfsmodell des Was-waere-wenn (Plan Abschnitt 6.2, Stufe 5c).
//
// Bewusst ohne React: die Regeln, welcher Aenderungstyp welche Felder braucht
// und welche Vorlage zu welchen Zeilen fuehrt, sind reine Logik und werden
// ohne Renderer geprueft. Die Oberflaeche liest hier nur ab.

import { parseAmountToMinorUnits } from "../../lib/money";
import type {
  ForecastChange,
  ForecastChangeType,
  HouseholdScenarioChange,
  HouseholdScenarioChangeInput,
} from "../../lib/types";

export interface ChangeDraft {
  /** Nur clientseitig, damit Zeilen ohne Server-ID unterscheidbar bleiben. */
  id: string;
  enabled: boolean;
  /** Rohtext des Betragsfeldes; die Umrechnung passiert erst beim Uebernehmen. */
  amountText: string;
  change: ForecastChange;
}

export interface ChangeTypeSpec {
  value: ForecastChangeType;
  /** Nutzersprache, nicht Schemaname. */
  label: string;
  /** Braucht einen bestehenden Vertrag als Ziel. */
  needsTarget: boolean;
  /** Traegt einen Betrag (bei *_end gibt es keinen). */
  needsAmount: boolean;
  /** Auf welche Vertragsrichtung sich das Ziel bezieht. */
  targetDirection: "income" | "expense" | null;
  /** Wirkt sich als Zufluss oder Abfluss aus. */
  sign: "income" | "expense";
}

export const CHANGE_TYPES: ChangeTypeSpec[] = [
  {
    value: "income_add",
    label: "Einkommen hinzufügen",
    needsTarget: false,
    needsAmount: true,
    targetDirection: null,
    sign: "income",
  },
  {
    value: "income_change",
    label: "Einkommen ändern",
    needsTarget: true,
    needsAmount: true,
    targetDirection: "income",
    sign: "income",
  },
  {
    value: "income_end",
    label: "Einkommen fällt weg",
    needsTarget: true,
    needsAmount: false,
    targetDirection: "income",
    sign: "income",
  },
  {
    value: "expense_add",
    label: "Neue Ausgabe",
    needsTarget: false,
    needsAmount: true,
    targetDirection: null,
    sign: "expense",
  },
  {
    value: "expense_change",
    label: "Ausgabe ändern",
    needsTarget: true,
    needsAmount: true,
    targetDirection: "expense",
    sign: "expense",
  },
  {
    value: "expense_end",
    label: "Zahlung beenden",
    needsTarget: true,
    needsAmount: false,
    targetDirection: "expense",
    sign: "expense",
  },
  {
    value: "one_off",
    label: "Einmalzahlung",
    needsTarget: false,
    needsAmount: true,
    targetDirection: null,
    sign: "expense",
  },
];

export function typeSpec(value: ForecastChangeType): ChangeTypeSpec {
  return CHANGE_TYPES.find((t) => t.value === value) ?? CHANGE_TYPES[0]!;
}

export const AMOUNT_MODES: { value: "absolute" | "delta" | "percent"; label: string }[] = [
  { value: "absolute", label: "Betrag ersetzen" },
  { value: "delta", label: "um Betrag ändern" },
  { value: "percent", label: "um Prozent ändern" },
];

export const FREQUENCIES: { value: ForecastChange["frequency"]; label: string }[] = [
  { value: "once", label: "einmalig" },
  { value: "weekly", label: "wöchentlich" },
  { value: "monthly", label: "monatlich" },
  { value: "quarterly", label: "vierteljährlich" },
  { value: "semiannual", label: "halbjährlich" },
  { value: "yearly", label: "jährlich" },
];

export const CONFIDENCES: { value: ForecastChange["confidence"]; label: string }[] = [
  { value: "contracted", label: "sicher" },
  { value: "estimated", label: "wahrscheinlich" },
  { value: "speculative", label: "Idee" },
];

let counter = 0;
export function nextDraftId(): string {
  counter += 1;
  return `draft-${counter}`;
}

/** Erster Tag des Monats, der auf `today` folgt — als "JJJJ-MM". */
export function nextMonthValue(today: Date): string {
  const d = new Date(today.getFullYear(), today.getMonth() + 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function firstOfMonth(monthValue: string): string {
  return `${monthValue}-01`;
}

export function monthValueOf(isoDate: string): string {
  return isoDate.slice(0, 7);
}

/**
 * Letzter Tag des Monats als "JJJJ-MM-TT" — der richtige Wert fuer `ends_on`.
 * Der Rechenkern bricht die Faelligkeitsschleife bei `cursor > ends_on` ab;
 * ein Monatserster verschluckt also die Zahlung genau dieses Monats.
 */
export function lastOfMonth(monthValue: string): string {
  const [year, month] = monthValue.split("-").map(Number) as [number, number];
  const probe = new Date(2000, 0, 1);
  // setFullYear statt new Date(y, ...): der Konstruktor liest zweistellige
  // Jahre als 19xx, und genau solche Werte wollen wir hier sauber behandeln.
  probe.setFullYear(year, month, 0);
  return `${monthValue}-${String(probe.getDate()).padStart(2, "0")}`;
}

/** Monate auf "JJJJ-MM" addieren; der Jahreswechsel faellt dabei mit ab. */
export function addMonths(monthValue: string, delta: number): string {
  const [year, month] = monthValue.split("-").map(Number) as [number, number];
  const probe = new Date(2000, 0, 1);
  probe.setFullYear(year, month - 1 + delta, 1);
  return `${probe.getFullYear()}-${String(probe.getMonth() + 1).padStart(2, "0")}`;
}

/** "JJJJ-MM" mit einem Monat zwischen 01 und 12. */
export function isMonthValue(value: string): boolean {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(value);
}

/** "JJJJ-MM-TT", und der Tag muss es in diesem Monat wirklich geben. */
export function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [, month, day] = value.split("-").map(Number) as [number, number, number];
  if (month < 1 || month > 12 || day < 1) return false;
  return day <= Number(lastOfMonth(value.slice(0, 7)).slice(8));
}

// Jahre ausserhalb dieses Fensters sind Tippfehler, keine Planung. Bewusst
// weit und fest statt relativ zu heute, damit Tests keine Zeitbombe werden.
const MIN_YEAR = 2000;
const MAX_YEAR = 2100;

/**
 * Vorlagen fuer die Faelle, die wirklich vorkommen (Plan Stufe 5c). Eine
 * Vorlage darf mehrere Zeilen erzeugen — ein Autokauf ist eine Einmalzahlung
 * UND eine laufende Rate, und genau diese Kombination vergisst man von Hand.
 */
export interface ChangeTemplate {
  key: string;
  label: string;
  hint: string;
  build: (startMonth: string) => ChangeDraft[];
}

function draft(change: ForecastChange, amountText: string, enabled = true): ChangeDraft {
  return { id: nextDraftId(), enabled, amountText, change };
}

export const CHANGE_TEMPLATES: ChangeTemplate[] = [
  {
    key: "raise",
    label: "Gehaltserhöhung",
    hint: "bestehendes Gehalt um einen Betrag anheben",
    build: (start) => [
      draft(
        {
          change_type: "income_change",
          label: "Gehaltserhöhung",
          amount_mode: "delta",
          amount_minor: 20000,
          frequency: "monthly",
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "200,00",
      ),
    ],
  },
  {
    key: "parttime",
    label: "Elternzeit oder Teilzeit",
    hint: "ein Einkommen sinkt auf einen festen Betrag",
    build: (start) => [
      draft(
        {
          change_type: "income_change",
          label: "Teilzeit",
          amount_mode: "absolute",
          amount_minor: 145000,
          frequency: "monthly",
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "1.450,00",
      ),
    ],
  },
  {
    key: "second-income",
    label: "Partner steigt wieder ein",
    hint: "zweites Einkommen, dazu die Betreuungskosten",
    build: (start) => [
      draft(
        {
          change_type: "income_add",
          label: "Zweites Einkommen",
          amount_mode: "absolute",
          amount_minor: 145000,
          frequency: "monthly",
          due_day: 15,
          income_kind: "salary",
          starts_on: firstOfMonth(start),
          confidence: "speculative",
        },
        "1.450,00",
      ),
      draft(
        {
          change_type: "expense_add",
          label: "Kinderbetreuung",
          amount_mode: "absolute",
          amount_minor: -28000,
          frequency: "monthly",
          due_day: 1,
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "280,00",
      ),
    ],
  },
  {
    key: "unemployment",
    label: "Arbeitslosigkeit",
    hint: "Gehalt fällt weg, Arbeitslosengeld kommt",
    build: (start) => [
      draft(
        {
          change_type: "income_change",
          label: "Arbeitslosengeld statt Gehalt",
          amount_mode: "absolute",
          amount_minor: 180000,
          frequency: "monthly",
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "1.800,00",
      ),
    ],
  },
  {
    key: "childcare",
    label: "Neue Betreuungskosten",
    hint: "Kita, Hort oder Tagesmutter",
    build: (start) => [
      draft(
        {
          change_type: "expense_add",
          label: "Kinderbetreuung",
          amount_mode: "absolute",
          amount_minor: -28000,
          frequency: "monthly",
          due_day: 1,
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "280,00",
      ),
    ],
  },
  {
    key: "move",
    label: "Umzug",
    hint: "neue Miete plus einmalige Umzugskosten",
    build: (start) => [
      draft(
        {
          change_type: "expense_change",
          label: "Neue Miete",
          amount_mode: "absolute",
          amount_minor: -130000,
          frequency: "monthly",
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "1.300,00",
      ),
      draft(
        {
          change_type: "one_off",
          label: "Umzugskosten",
          amount_mode: "absolute",
          amount_minor: -250000,
          frequency: "once",
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "2.500,00",
      ),
    ],
  },
  {
    key: "loan",
    label: "Neue Kreditrate",
    hint: "laufende Rate mit Ende",
    build: (start) => [
      draft(
        {
          change_type: "expense_add",
          label: "Kreditrate",
          amount_mode: "absolute",
          amount_minor: -25000,
          frequency: "monthly",
          due_day: 1,
          starts_on: firstOfMonth(start),
          confidence: "contracted",
        },
        "250,00",
      ),
    ],
  },
  {
    key: "car",
    label: "Auto anschaffen",
    hint: "Anzahlung einmalig, Rate und Versicherung laufend",
    build: (start) => [
      draft(
        {
          change_type: "one_off",
          label: "Anzahlung Auto",
          amount_mode: "absolute",
          amount_minor: -500000,
          frequency: "once",
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "5.000,00",
      ),
      draft(
        {
          change_type: "expense_add",
          label: "Finanzierungsrate",
          amount_mode: "absolute",
          amount_minor: -22000,
          frequency: "monthly",
          due_day: 1,
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "220,00",
      ),
      draft(
        {
          change_type: "expense_add",
          label: "KFZ-Versicherung",
          amount_mode: "absolute",
          amount_minor: -60000,
          frequency: "yearly",
          due_day: 1,
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "600,00",
      ),
    ],
  },
  {
    key: "repair",
    label: "Einmalige Reparatur",
    hint: "ein Betrag an einem Tag",
    build: (start) => [
      draft(
        {
          change_type: "one_off",
          label: "Reparatur",
          amount_mode: "absolute",
          amount_minor: -120000,
          frequency: "once",
          starts_on: firstOfMonth(start),
          confidence: "estimated",
        },
        "1.200,00",
      ),
    ],
  },
];

export function emptyDraft(startMonth: string): ChangeDraft {
  return draft(
    {
      change_type: "expense_add",
      label: "Neue Ausgabe",
      amount_mode: "absolute",
      amount_minor: -10000,
      frequency: "monthly",
      due_day: 1,
      starts_on: firstOfMonth(startMonth),
      confidence: "estimated",
    },
    "100,00",
  );
}

export interface DraftIssue {
  /**
   * "fehlt": der Nutzer ist noch nicht fertig — das ist kein Fehler, sondern
   * ein Zwischenstand und wird ruhig angezeigt. "widerspruch": die Angaben
   * passen nicht zusammen; das darf laut werden.
   */
  kind: "fehlt" | "widerspruch";
  message: string;
}

function monthIssue(iso: string, label: string): DraftIssue | null {
  if (!isIsoDate(iso)) {
    return { kind: "widerspruch", message: `${label} ist kein gültiger Monat.` };
  }
  const year = Number(iso.slice(0, 4));
  if (year < MIN_YEAR || year > MAX_YEAR) {
    return { kind: "widerspruch", message: `${label} liegt außerhalb des Zeitraums.` };
  }
  return null;
}

/**
 * Prueft eine Zeile, bevor sie an den Server geht, und trennt dabei zwei
 * Faelle, die die Oberflaeche verschieden behandeln muss: eine Angabe fehlt
 * noch (der Nutzer tippt gerade) oder die Angaben widersprechen sich.
 */
export function draftIssue(d: ChangeDraft): DraftIssue | null {
  const spec = typeSpec(d.change.change_type);
  if (!d.change.label.trim()) return { kind: "fehlt", message: "Bezeichnung fehlt." };
  if (spec.needsTarget && !d.change.target_contract_id) {
    return { kind: "fehlt", message: "Ziel auswählen." };
  }
  if (spec.needsAmount) {
    if (d.change.amount_mode === "percent") {
      const percent = d.change.amount_percent;
      if (percent === null || percent === undefined || percent === "") {
        return { kind: "fehlt", message: "Prozentsatz fehlt." };
      }
    } else if (d.change.amount_minor === null || d.change.amount_minor === undefined) {
      return { kind: "fehlt", message: "Betrag fehlt." };
    } else if (d.change.amount_minor === 0) {
      // Der Server lehnt 0 ausdruecklich ab — hier ist es ein Widerspruch und
      // keine fehlende Angabe, denn im Feld steht etwas.
      return { kind: "widerspruch", message: "Betrag darf nicht 0 sein." };
    }
  }
  if (d.change.change_type === "one_off" && d.change.frequency !== "once") {
    return { kind: "widerspruch", message: "Einmalzahlung braucht den Rhythmus „einmalig“." };
  }
  if (d.change.change_type !== "one_off" && d.change.frequency === "once") {
    return { kind: "widerspruch", message: "„Einmalig“ gibt es nur bei einer Einmalzahlung." };
  }
  const startIssue = monthIssue(d.change.starts_on, "Beginn");
  if (startIssue) return startIssue;
  if (d.change.ends_on) {
    const endIssue = monthIssue(d.change.ends_on, "Ende");
    if (endIssue) return endIssue;
    if (d.change.ends_on < d.change.starts_on) {
      return { kind: "widerspruch", message: "Ende liegt vor dem Beginn." };
    }
  }
  return null;
}

/**
 * Dieselbe Pruefung als Text — der Weg, den Aufrufer ausserhalb des Editors
 * gehen (Serverfilter, Szenariospeicherung).
 */
export function validateDraft(d: ChangeDraft): string | null {
  return draftIssue(d)?.message ?? null;
}

/**
 * Bringt eine Zeile nach einem Typwechsel wieder in einen gueltigen Zustand.
 * Ohne das bleibt z. B. ein Zielvertrag an einer Zeile haengen, die gar kein
 * Ziel mehr kennt — und der Server lehnt sie ab.
 */
export function normalizeForType(
  change: ForecastChange,
  nextType: ForecastChangeType,
): ForecastChange {
  const spec = typeSpec(nextType);
  const next: ForecastChange = { ...change, change_type: nextType };

  if (!spec.needsTarget) next.target_contract_id = null;
  if (!spec.needsAmount) {
    next.amount_mode = null;
    next.amount_minor = null;
    next.amount_percent = null;
  } else if (!next.amount_mode) {
    next.amount_mode = "absolute";
  }

  if (nextType === "one_off") {
    next.frequency = "once";
    next.ends_on = null;
  } else if (next.frequency === "once") {
    next.frequency = "monthly";
  }

  // Vorzeichen folgt dem Typ, nicht der Eingabe: der Nutzer tippt immer einen
  // positiven Betrag, die Richtung steckt in der Auswahl.
  if (typeof next.amount_minor === "number") {
    const magnitude = Math.abs(next.amount_minor);
    next.amount_minor = spec.sign === "expense" ? -magnitude : magnitude;
  }
  // Der Server erlaubt beides ausschliesslich bei income_add. Blieb hier
  // income_kind haengen, lehnte er die ganze Simulation ab - ohne dass der
  // Nutzer etwas anderes getan haette, als den Typ umzustellen.
  if (nextType !== "income_add") {
    next.income_kind = null;
    next.person_id = null;
  } else if (!next.income_kind) {
    next.income_kind = "salary";
  }

  return next;
}

/** Betrag aus dem Eingabefeld uebernehmen; Vorzeichen kommt aus dem Typ. */
export function withAmount(change: ForecastChange, minor: number): ForecastChange {
  const spec = typeSpec(change.change_type);
  const magnitude = Math.abs(minor);
  return { ...change, amount_minor: spec.sign === "expense" ? -magnitude : magnitude };
}

/**
 * Den Rohtext des Betragsfeldes neu einlesen. Noetig nach jedem Wechsel von
 * Typ oder Betragsart: sonst zeigt das Feld weiter "1.450,00", waehrend
 * gerechnet wird, was vor dem Wechsel galt.
 *
 * Leerer oder unlesbarer Text bedeutet ausdruecklich "kein Betrag" — nicht
 * "nimm den alten weiter".
 */
export function syncAmountFromText(change: ForecastChange, amountText: string): ForecastChange {
  const spec = typeSpec(change.change_type);
  if (!spec.needsAmount) return change;

  if (change.amount_mode === "percent") {
    const raw = amountText.trim().replace(",", ".");
    const value = raw === "" ? null : Number(raw);
    return {
      ...change,
      amount_percent: value !== null && Number.isFinite(value) ? value : null,
      amount_minor: null,
    };
  }

  const minor = parseAmountToMinorUnits(amountText);
  if (minor === null) return { ...change, amount_minor: null, amount_percent: null };
  return { ...withAmount(change, minor), amount_percent: null };
}

/**
 * Ein gespeichertes Szenario zurueck in Entwurfszeilen bringen (Plan Stufe 7).
 *
 * Das Betragsfeld wird aus dem gespeicherten Wert zurueckgeschrieben, damit
 * der Nutzer im Eingabefeld wiederfindet, was er getippt hat - immer als
 * positiver Betrag, weil das Vorzeichen aus dem Typ folgt.
 */
export function changesToDrafts(changes: HouseholdScenarioChange[]): ChangeDraft[] {
  return [...changes]
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((change) => {
      const { id, include_in_forecast, sort_order, note, ...forecastChange } = change;
      void sort_order;
      void note;
      const amountText =
        forecastChange.amount_mode === "percent"
          ? String(forecastChange.amount_percent ?? "").replace(".", ",")
          : forecastChange.amount_minor === null || forecastChange.amount_minor === undefined
            ? ""
            : (Math.abs(forecastChange.amount_minor) / 100).toLocaleString("de-DE", {
                minimumFractionDigits: 2,
              });
      return {
        id: `saved-${id}`,
        enabled: include_in_forecast,
        amountText,
        change: forecastChange as ForecastChange,
      };
    });
}

/**
 * Entwurfszeilen in die Speicherform bringen (Plan Stufe 7).
 *
 * Ungueltige Zeilen fliegen vorher raus - ein gespeichertes Szenario soll
 * rechenbar sein, nicht halbfertig. `include_in_forecast` haelt fest, dass
 * eine Zeile bewusst ausgeschaltet ist; sie bleibt erhalten, wirkt aber nicht.
 */
export function draftsToChanges(drafts: ChangeDraft[]): HouseholdScenarioChangeInput[] {
  return drafts
    .filter((d) => validateDraft(d) === null)
    .map((d, index) => ({
      ...d.change,
      include_in_forecast: d.enabled,
      sort_order: index,
      note: null,
    }));
}
