import { parseAmountToMinorUnits } from "./money";
import type {
  AnalyticsColumn,
  AnalyticsFilterNode,
  AnalyticsQuery,
  AnalyticsRow,
  DimensionRef,
  PeriodSpec,
} from "./types";

// Abfragebau, Drilldown-Ableitung und URL-Serialisierung an einer Stelle
// (docs/ANALYSE-EXPLORER-PLAN.md, Baustein B/D).
//
// Der Drilldown wird bewusst EINMAL auf Abfrageebene geloest und nicht je
// Diagramm verdrahtet: dadurch gilt fuer jedes Diagramm, jede Tabellenzelle
// und jede Kennzahlkachel dieselbe Zusage - anklicken fuehrt zu den Buchungen.

export const PERIOD_PRESETS: { key: string; label: string; period: PeriodSpec }[] = [
  { key: "current_month", label: "Dieser Monat", period: { kind: "current_month" } },
  { key: "3m", label: "Letzte 3 Monate", period: { kind: "last_n_months", n: 3 } },
  { key: "6m", label: "Letzte 6 Monate", period: { kind: "last_n_months", n: 6 } },
  { key: "12m", label: "Letzte 12 Monate", period: { kind: "last_n_months", n: 12 } },
  { key: "ytd", label: "Dieses Jahr", period: { kind: "year_to_date" } },
  { key: "all", label: "Gesamter Zeitraum", period: { kind: "all_time" } },
];

/** Sammelt alle Blattbedingungen eines Filterbaums. */
export function collectConditions(
  node: AnalyticsFilterNode | null | undefined,
): NonNullable<AnalyticsFilterNode["condition"]>[] {
  if (!node) return [];
  if (node.condition) return [node.condition];
  const children = node.all_of ?? node.any_of ?? (node.not_of ? [node.not_of] : []);
  return children.flatMap((child) => collectConditions(child));
}

/**
 * Setzt genau eine Bedingung je Feld. Der Explorer verwaltet Filter als
 * flache Liste - ein verschachtelter Baum waere fuer die Chip-Leiste weder
 * darstellbar noch rueckgaengig zu machen.
 */
export function conditionsToFilter(
  conditions: NonNullable<AnalyticsFilterNode["condition"]>[],
): AnalyticsFilterNode | null {
  if (conditions.length === 0) return null;
  if (conditions.length === 1) return { condition: conditions[0] };
  return { all_of: conditions.map((condition) => ({ condition })) };
}

export function upsertCondition(
  query: AnalyticsQuery,
  condition: NonNullable<AnalyticsFilterNode["condition"]>,
): AnalyticsQuery {
  const rest = collectConditions(query.where).filter((c) => c.field !== condition.field);
  return { ...query, where: conditionsToFilter([...rest, condition]) };
}

export function removeCondition(query: AnalyticsQuery, field: string): AnalyticsQuery {
  const rest = collectConditions(query.where).filter((c) => c.field !== field);
  return { ...query, where: conditionsToFilter(rest) };
}

export function findCondition(
  query: AnalyticsQuery,
  field: string,
): NonNullable<AnalyticsFilterNode["condition"]> | undefined {
  return collectConditions(query.where).find((c) => c.field === field);
}

/**
 * Leitet aus einer Aggregatabfrage und einem angeklickten Segment die
 * Detailabfrage ab: gleiche Quelle, gleiche Filter, grain=rows, plus die
 * Bedingung des Segments.
 *
 * `null` als Segmentwert ist dabei ein echter Wert ("Ohne Kategorie") und
 * wird zu is_null - nicht zu einem Vergleich gegen den Text "null".
 */
export function drilldownQuery(query: AnalyticsQuery, row: AnalyticsRow): AnalyticsQuery {
  let detail: AnalyticsQuery = {
    ...query,
    grain: "rows",
    group_by: [],
    measures: [],
    order_by: [],
    top_n: null,
    limit: 200,
  };

  for (const dimension of query.group_by ?? []) {
    const key = dimensionKey(dimension);
    const value = row[key];
    // Die zusammengefasste Restzeile hat kein einzelnes Segment - ein
    // Drilldown darauf waere eine Behauptung.
    if (value === "__rest__") return detail;
    if (value === null || value === undefined) {
      detail = upsertCondition(detail, { field: dimension.field, op: "is_null", value: null });
    } else if (dimension.granularity) {
      const { from, to } = granularityRange(String(value), dimension.granularity);
      detail = { ...detail, period: { kind: "custom", from_date: from, to_date: to } };
    } else {
      detail = upsertCondition(detail, {
        field: dimension.field,
        op: "eq",
        value: String(value),
      });
    }
  }
  return detail;
}

/**
 * Beschreibung eines anklickbaren Segments - Feld, Wert, Zeitraum, Richtung.
 * Genau die Form, in der `GET /v1/insights` seine Drilldowns mitliefert; sie
 * reicht aber auch fuer Berichts- und Budgetzeilen.
 */
export interface DrilldownSpec {
  field?: string | null;
  /** `null` bei gesetztem `field` bedeutet "ohne Zuordnung", nicht "egal". */
  value?: string | null;
  from_date: string;
  to_date: string;
  direction?: "expense" | "income" | null;
  /** Fachliche Cashflow-Zuordnung; anders als `direction` nicht vom Vorzeichen abgeleitet. */
  cashflowRole?: "expense" | "income" | "net" | null;
}

/**
 * Baut die Zeilenabfrage zu einem Segment.
 *
 * Bewusst hier und nicht je aufrufender Seite: dadurch fuehrt jeder Drilldown
 * im Produkt - Bericht, Budget, Empfehlung, Explorer - zu derselben Abfrage
 * gegen denselben Endpunkt. Eine Seite mit eigener Detaillogik wuerde frueher
 * oder spaeter eine andere Menge zeigen als ihre eigene Summe.
 */
export function rowsQuery(spec: DrilldownSpec): AnalyticsQuery {
  let query: AnalyticsQuery = {
    grain: "rows",
    group_by: [],
    measures: [],
    fields: [],
    period: { kind: "custom", from_date: spec.from_date, to_date: spec.to_date },
    order_by: [],
    limit: 200,
    include_transfers: false,
    include_archived_categories: true,
    // Dieselbe Auswahlregel wie in insights_service und budget_service:
    // sonst zeigt die Detailliste Buchungen, die in der Summe darueber nicht
    // enthalten sind.
    only_on_budget: true,
  };

  if (spec.field) {
    query =
      spec.value === null || spec.value === undefined
        ? upsertCondition(query, { field: spec.field, op: "is_null", value: null })
        : upsertCondition(query, { field: spec.field, op: "eq", value: spec.value });
  }
  if (spec.direction) {
    query = upsertCondition(query, { field: "direction", op: "eq", value: spec.direction });
  }
  if (spec.cashflowRole) {
    query = upsertCondition(query, {
      field: "cashflow_role",
      op: spec.cashflowRole === "net" ? "in" : "eq",
      value: spec.cashflowRole === "net" ? ["income", "expense"] : spec.cashflowRole,
    });
  }
  return query;
}

/** Schluessel, unter dem eine Dimension in den Ergebniszeilen steht. */
export function dimensionKey(dimension: DimensionRef): string {
  return dimension.granularity ? `${dimension.field}:${dimension.granularity}` : dimension.field;
}

/** Anzeigetext einer Dimensionszelle - bevorzugt das aufgeloeste Label. */
export function dimensionLabel(row: AnalyticsRow, key: string): string {
  const label = row[`${key}_label`];
  if (typeof label === "string" && label.length > 0) return label;
  const raw = row[key];
  if (raw === null || raw === undefined) return "Ohne Zuordnung";
  return String(raw);
}

function granularityRange(
  value: string,
  granularity: NonNullable<DimensionRef["granularity"]>,
): { from: string; to: string } {
  const start = new Date(value);
  const end = new Date(start);
  if (granularity === "day") {
    // Ende = Anfang.
  } else if (granularity === "week") {
    end.setDate(end.getDate() + 6);
  } else if (granularity === "month") {
    end.setMonth(end.getMonth() + 1);
    end.setDate(0);
  } else if (granularity === "quarter") {
    end.setMonth(end.getMonth() + 3);
    end.setDate(0);
  } else {
    end.setFullYear(end.getFullYear() + 1);
    end.setDate(0);
    end.setMonth(11);
    end.setDate(31);
  }
  return { from: isoDate(start), to: isoDate(end) };
}

/**
 * Erster und letzter Tag eines Kalendermonats als ISO-Datum.
 *
 * Bewusst ueber `isoDate` aus lokalen Datumsteilen statt ueber
 * `toISOString()`: letzteres rechnet nach UTC um und liefert in jeder Zone
 * oestlich von Greenwich den Vortag - aus dem 30.06. wird der 29.06., und ein
 * Drilldown verliert genau den letzten Tag des Monats.
 */
export function monthRange(year: number, month: number): { from: string; to: string } {
  return {
    from: isoDate(new Date(year, month - 1, 1)),
    to: isoDate(new Date(year, month, 0)),
  };
}

function isoDate(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(
    value.getDate(),
  ).padStart(2, "0")}`;
}

/**
 * Menschenlesbare Beschreibung einer Bedingung fuer die Chip-Leiste.
 * Komplexer Filterzustand muss ablesbar bleiben, sonst weiss niemand mehr,
 * warum eine Summe so klein ist.
 */
export function describeCondition(
  condition: NonNullable<AnalyticsFilterNode["condition"]>,
  fieldLabel: string,
  valueLabels?: Map<string, string>,
  formatValue?: (value: unknown) => string,
): string {
  const resolve = (value: unknown): string => {
    const raw = String(value);
    return valueLabels?.get(raw) ?? formatValue?.(value) ?? raw;
  };
  switch (condition.op) {
    case "is_null":
      return `${fieldLabel} ist leer`;
    case "is_not_null":
      return `${fieldLabel} ist gesetzt`;
    case "in": {
      const values = Array.isArray(condition.value) ? condition.value : [];
      if (values.length === 1) return `${fieldLabel} ist ${resolve(values[0])}`;
      return `${fieldLabel} ist ${values.length} Werte`;
    }
    case "not_in": {
      const values = Array.isArray(condition.value) ? condition.value : [];
      return `${fieldLabel} ist nicht ${values.length} Werte`;
    }
    case "between": {
      const values = Array.isArray(condition.value) ? condition.value : [];
      return `${fieldLabel} zwischen ${resolve(values[0])} und ${resolve(values[1])}`;
    }
    case "contains":
      return `${fieldLabel} enthält „${condition.value}“`;
    case "starts_with":
      return `${fieldLabel} beginnt mit „${condition.value}“`;
    case "gt":
      return `${fieldLabel} > ${resolve(condition.value)}`;
    case "gte":
      return `${fieldLabel} ≥ ${resolve(condition.value)}`;
    case "lt":
      return `${fieldLabel} < ${resolve(condition.value)}`;
    case "lte":
      return `${fieldLabel} ≤ ${resolve(condition.value)}`;
    case "ne":
      return `${fieldLabel} ≠ ${resolve(condition.value)}`;
    default:
      return `${fieldLabel} = ${resolve(condition.value)}`;
  }
}

/**
 * Die Mikrosyntax der Spaltenfilter: `>100`, `<-50`, `10..99`, `=exakt`,
 * `!nicht`. Sehr hohe Bedienungsdichte pro Pixel - und sie wird hier
 * geparst, nicht im Server: was ankommt, ist immer eine typisierte Bedingung.
 *
 * Betraege werden in Minor Units umgerechnet, weil der Nutzer Euro tippt.
 */
export function parseFilterExpression(
  field: string,
  kind: AnalyticsColumn["kind"],
  input: string,
  isMoney = true,
): NonNullable<AnalyticsFilterNode["condition"]> | null {
  const text = input.trim();
  if (!text) return null;

  if (kind === "num") {
    const toNumber = (value: string) => parseNumericInput(value, isMoney);
    const range = text.match(/^(-?[\d.,]+)\s*\.\.\s*(-?[\d.,]+)$/);
    if (range?.[1] && range[2]) {
      const from = toNumber(range[1]);
      const to = toNumber(range[2]);
      if (from === null || to === null) return null;
      return { field, op: "between", value: from <= to ? [from, to] : [to, from] };
    }
    const comparison = text.match(/^(>=|<=|!=|<>|>|<|=)\s*(-?[\d.,]+)$/);
    if (comparison?.[1] && comparison[2]) {
      const opMap: Record<string, NonNullable<AnalyticsFilterNode["condition"]>["op"]> = {
        ">=": "gte",
        "<=": "lte",
        "!=": "ne",
        "<>": "ne",
        ">": "gt",
        "<": "lt",
        "=": "eq",
      };
      const op = opMap[comparison[1]];
      const value = toNumber(comparison[2]);
      if (op && value !== null) return { field, op, value };
    }
    const plain = toNumber(text);
    if (plain !== null) return { field, op: "eq", value: plain };
    return null;
  }

  if (kind === "date") {
    // Datumsspalten tragen ISO-Text; die Mikrosyntax bleibt dieselbe.
    const range = text.match(/^(\d{4}-\d{2}-\d{2})\s*\.\.\s*(\d{4}-\d{2}-\d{2})$/);
    if (range?.[1] && range[2]) {
      const [from, to] = range[1] <= range[2] ? [range[1], range[2]] : [range[2], range[1]];
      return { field, op: "between", value: [from, to] };
    }
    const comparison = text.match(/^(>=|<=|>|<|=)\s*(\d{4}-\d{2}-\d{2})$/);
    if (comparison?.[1] && comparison[2]) {
      const opMap: Record<string, NonNullable<AnalyticsFilterNode["condition"]>["op"]> = {
        ">=": "gte",
        "<=": "lte",
        ">": "gt",
        "<": "lt",
        "=": "eq",
      };
      const op = opMap[comparison[1]];
      if (op) return { field, op, value: comparison[2] };
    }
    return { field, op: "contains", value: text };
  }

  if (text.startsWith("!=")) return { field, op: "ne", value: text.slice(2).trim() };
  if (text.startsWith("!")) return { field, op: "ne", value: text.slice(1).trim() };
  if (text.startsWith("=")) return { field, op: "eq", value: text.slice(1).trim() };
  return { field, op: "contains", value: text };
}

/**
 * Zahleneingabe der Mikrosyntax. Geldspalten laufen ueber den einzigen
 * Umrechner des Projekts (`parseAmountToMinorUnits`) statt ueber eine zweite
 * Kommaregel - sonst haette ">1.234,56" hier eine andere Bedeutung als
 * dasselbe im Betragsfeld eines Formulars.
 */
function parseNumericInput(value: string, isMoney: boolean): number | null {
  if (isMoney) return parseAmountToMinorUnits(value);
  const normalized = Number(
    value
      .replace(/\s/g, "")
      .replace(/\.(?=\d{3}\b)/g, "")
      .replace(",", "."),
  );
  return Number.isFinite(normalized) ? normalized : null;
}

/**
 * Der vollstaendige Explorer-Zustand in der URL. Damit ist eine Ansicht
 * teilbar, per Zurueck-Taste bedienbar und ueberlebt einen Reload - der
 * heutige Kontofilter im Buchungsregister kann nichts davon.
 */
export function queryToSearchParams(query: AnalyticsQuery): URLSearchParams {
  const params = new URLSearchParams();
  params.set("q", btoa(encodeURIComponent(JSON.stringify(query))));
  return params;
}

export function queryFromSearchParams(
  params: URLSearchParams,
  fallback: AnalyticsQuery,
): AnalyticsQuery {
  const raw = params.get("q");
  if (!raw) return fallback;
  try {
    return JSON.parse(decodeURIComponent(atob(raw))) as AnalyticsQuery;
  } catch {
    // Eine unlesbare URL darf die Seite nicht leer lassen.
    return fallback;
  }
}
