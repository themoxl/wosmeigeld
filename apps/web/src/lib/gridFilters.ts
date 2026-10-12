import { formatMinorUnitsAsEuro, parseAmountToMinorUnits } from "./money";
import type { AnalyticsColumn, AnalyticsCompareOp, AnalyticsCondition } from "./types";

// Das Operatorangebot der Spaltenfilter (docs/ANALYSE-EXPLORER-PLAN.md, C.1).
//
// Bewusst EINE Quelle fuer beide Tabellen: der Explorer schickt die erzeugte
// Bedingung an den Server, das Buchungsregister wertet dieselbe Bedingung im
// Browser aus (`matchesCondition`). Zwei getrennte Operatorlisten waeren die
// sichere Art, dass "enthaelt" an der einen Stelle etwas anderes bedeutet als
// an der anderen.
//
// Der Wertevorrat je Spaltentyp ergibt sich aus dem Feldkatalog des Servers
// (`ColumnKind`), nicht aus dem, was eine einzelne Seite gerade anzeigt.

export interface OperatorSpec {
  op: AnalyticsCompareOp;
  label: string;
  /** Anzahl der Werteingaben: 0 = wertlos, 1 = ein Feld, 2 = Von/Bis. */
  arity: 0 | 1 | 2;
}

const TEXT_OPERATORS: OperatorSpec[] = [
  { op: "contains", label: "enthält", arity: 1 },
  { op: "starts_with", label: "beginnt mit", arity: 1 },
  { op: "eq", label: "ist genau", arity: 1 },
  { op: "ne", label: "ist nicht", arity: 1 },
  { op: "is_null", label: "ist leer", arity: 0 },
  { op: "is_not_null", label: "ist gesetzt", arity: 0 },
];

const NUMBER_OPERATORS: OperatorSpec[] = [
  { op: "eq", label: "= gleich", arity: 1 },
  { op: "ne", label: "≠ ungleich", arity: 1 },
  { op: "gt", label: "> größer", arity: 1 },
  { op: "gte", label: "≥ größer/gleich", arity: 1 },
  { op: "lt", label: "< kleiner", arity: 1 },
  { op: "lte", label: "≤ kleiner/gleich", arity: 1 },
  { op: "between", label: "zwischen", arity: 2 },
  { op: "is_null", label: "ist leer", arity: 0 },
  { op: "is_not_null", label: "ist gesetzt", arity: 0 },
];

const DATE_OPERATORS: OperatorSpec[] = [
  { op: "eq", label: "am", arity: 1 },
  { op: "gte", label: "ab", arity: 1 },
  { op: "lte", label: "bis", arity: 1 },
  { op: "gt", label: "nach", arity: 1 },
  { op: "lt", label: "vor", arity: 1 },
  { op: "between", label: "zwischen", arity: 2 },
  { op: "is_null", label: "ist leer", arity: 0 },
  { op: "is_not_null", label: "ist gesetzt", arity: 0 },
];

const BOOL_OPERATORS: OperatorSpec[] = [
  { op: "eq", label: "ist", arity: 1 },
  { op: "is_null", label: "ist leer", arity: 0 },
  { op: "is_not_null", label: "ist gesetzt", arity: 0 },
];

/** Operatoren, die eine Mehrfachauswahl aus der Werteliste tragen. */
export const VALUE_LIST_OPERATORS: OperatorSpec[] = [
  { op: "in", label: "ist einer von", arity: 1 },
  { op: "not_in", label: "ist keiner von", arity: 1 },
];

export function operatorsFor(kind: AnalyticsColumn["kind"]): OperatorSpec[] {
  switch (kind) {
    case "num":
      return NUMBER_OPERATORS;
    case "date":
      return DATE_OPERATORS;
    case "bool":
      return BOOL_OPERATORS;
    default:
      return TEXT_OPERATORS;
  }
}

export function operatorSpec(kind: AnalyticsColumn["kind"], op: AnalyticsCompareOp): OperatorSpec {
  const all = [...operatorsFor(kind), ...VALUE_LIST_OPERATORS];
  return all.find((spec) => spec.op === op) ?? { op, label: op, arity: 1 };
}

export function defaultOperatorFor(kind: AnalyticsColumn["kind"]): AnalyticsCompareOp {
  return kind === "num" || kind === "date" || kind === "bool" ? "eq" : "contains";
}

/**
 * Baut aus der Eingabemaske eine Bedingung. `null` heisst "noch keine
 * gueltige Eingabe" - der Filter wird dann nicht gesetzt, statt stillschweigend
 * als 0 oder als leerer Text zu greifen.
 */
export function buildCondition(
  field: string,
  kind: AnalyticsColumn["kind"],
  isMoney: boolean,
  op: AnalyticsCompareOp,
  inputs: readonly [string, string],
): AnalyticsCondition | null {
  const spec = operatorSpec(kind, op);
  if (spec.arity === 0) {
    return { field, op, value: null };
  }

  const first = inputs[0]?.trim() ?? "";
  const second = inputs[1]?.trim() ?? "";

  if (kind === "num") {
    const from = parseNumberInput(first, isMoney);
    if (from === null) return null;
    if (op === "between") {
      const to = parseNumberInput(second, isMoney);
      if (to === null) return null;
      return { field, op, value: from <= to ? [from, to] : [to, from] };
    }
    return { field, op, value: from };
  }

  if (kind === "date") {
    if (!first) return null;
    if (op === "between") {
      if (!second) return null;
      return { field, op, value: first <= second ? [first, second] : [second, first] };
    }
    return { field, op, value: first };
  }

  if (kind === "bool") {
    if (first !== "true" && first !== "false") return null;
    return { field, op, value: first === "true" };
  }

  if (!first) return null;
  return { field, op, value: first };
}

/**
 * Umkehrung von `buildCondition` fuer das Wiederoeffnen des Menues: eine
 * gesetzte Bedingung muss sich unveraendert wiederfinden, sonst aendert
 * blosses Aufklappen den Filter.
 */
export function conditionInputs(
  condition: AnalyticsCondition | null | undefined,
  kind: AnalyticsColumn["kind"],
  isMoney: boolean,
): [string, string] {
  if (!condition) return ["", ""];
  const values = Array.isArray(condition.value) ? condition.value : [condition.value];
  const render = (value: unknown): string => {
    if (value === null || value === undefined) return "";
    if (kind === "num" && typeof value === "number") {
      return isMoney ? (value / 100).toFixed(2).replace(".", ",") : String(value);
    }
    return String(value);
  };
  return [render(values[0]), render(values[1])];
}

/** Werte einer `in`/`not_in`-Bedingung als Zeichenketten (NULL als `__null__`). */
export const NULL_VALUE_KEY = "__null__";

export function conditionValueKeys(condition: AnalyticsCondition | null | undefined): string[] {
  if (!condition || (condition.op !== "in" && condition.op !== "not_in")) return [];
  const values = Array.isArray(condition.value) ? condition.value : [];
  return values.map((value) => (value === null ? NULL_VALUE_KEY : String(value)));
}

export function valueKeysToCondition(
  field: string,
  op: "in" | "not_in",
  keys: readonly string[],
): AnalyticsCondition | null {
  if (keys.length === 0) return null;
  return {
    field,
    op,
    value: keys.map((key) => (key === NULL_VALUE_KEY ? null : key)),
  };
}

function parseNumberInput(raw: string, isMoney: boolean): number | null {
  if (!raw) return null;
  if (isMoney) return parseAmountToMinorUnits(raw);
  const normalized = Number(raw.replace(/\s/g, "").replace(",", "."));
  return Number.isFinite(normalized) ? normalized : null;
}

/**
 * Wertet eine Bedingung im Browser aus.
 *
 * Notwendig, weil das Buchungsregister seine Zeilen nicht ueber den
 * Analyse-Endpunkt laedt: dieselbe Bedingung, die der Explorer zu SQL
 * kompiliert, muss dort lokal dieselbe Menge treffen. Der Vergleich folgt
 * bewusst denselben Regeln wie `_compile_filter` im Server -
 * Textvergleiche ohne Gross-/Kleinschreibung, NULL greift nur bei
 * is_null/is_not_null.
 */
export function matchesCondition(condition: AnalyticsCondition, cellValue: unknown): boolean {
  const { op } = condition;
  const empty = cellValue === null || cellValue === undefined || cellValue === "";

  if (op === "is_null") return empty;
  if (op === "is_not_null") return !empty;
  if (empty) return false;

  if (op === "in" || op === "not_in") {
    const keys = conditionValueKeys(condition);
    const hit = keys.includes(String(cellValue));
    return op === "in" ? hit : !hit;
  }

  if (op === "between") {
    const values = Array.isArray(condition.value) ? condition.value : [];
    const [from, to] = values;
    if (from === null || from === undefined || to === null || to === undefined) return false;
    if (typeof cellValue === "number") {
      return cellValue >= Number(from) && cellValue <= Number(to);
    }
    const text = String(cellValue);
    return text >= String(from) && text <= String(to);
  }

  const target = condition.value;
  if (target === null || target === undefined) return false;

  if (typeof cellValue === "number" || typeof target === "number") {
    const left = Number(cellValue);
    const right = Number(target);
    if (Number.isNaN(left) || Number.isNaN(right)) return false;
    switch (op) {
      case "eq":
        return left === right;
      case "ne":
        return left !== right;
      case "gt":
        return left > right;
      case "gte":
        return left >= right;
      case "lt":
        return left < right;
      case "lte":
        return left <= right;
      default:
        break;
    }
  }

  if (typeof cellValue === "boolean" || typeof target === "boolean") {
    const left = cellValue === true || cellValue === "true";
    const right = target === true || target === "true";
    return op === "ne" ? left !== right : left === right;
  }

  const left = String(cellValue);
  const right = String(target);
  const lowerLeft = left.toLocaleLowerCase();
  const lowerRight = right.toLocaleLowerCase();
  switch (op) {
    case "contains":
      return lowerLeft.includes(lowerRight);
    case "starts_with":
      return lowerLeft.startsWith(lowerRight);
    case "eq":
      return lowerLeft === lowerRight;
    case "ne":
      return lowerLeft !== lowerRight;
    case "gt":
      return left > right;
    case "gte":
      return left >= right;
    case "lt":
      return left < right;
    case "lte":
      return left <= right;
    default:
      return false;
  }
}

/** Anzeigetext eines Filterwerts fuer Chips und Menuekopf. */
export function formatConditionValue(
  condition: AnalyticsCondition,
  kind: AnalyticsColumn["kind"],
  isMoney: boolean,
): string {
  const money = (value: unknown) =>
    typeof value === "number" ? formatMinorUnitsAsEuro(value) : String(value);
  const one = (value: unknown) => (isMoney && kind === "num" ? money(value) : String(value));

  if (condition.op === "is_null" || condition.op === "is_not_null") return "";
  if (Array.isArray(condition.value)) {
    if (condition.op === "between") {
      return `${one(condition.value[0])} – ${one(condition.value[1])}`;
    }
    return `${condition.value.length} Werte`;
  }
  if (typeof condition.value === "boolean") return condition.value ? "ja" : "nein";
  return one(condition.value);
}
