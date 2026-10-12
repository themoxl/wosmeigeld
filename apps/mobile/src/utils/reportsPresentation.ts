// Aufbereitung der Berichte fuer das Telefon (docs/ANALYSE-EXPLORER-PLAN.md,
// Baustein E).
//
// Die Zahlen kommen fertig aggregiert vom Server; hier wird ausschliesslich
// umgeformt, sortiert und beschriftet - nie neu gerechnet. Einzige Ausnahme
// sind Summen ueber den bereits gelieferten Zeitraum (Periodensumme,
// Monatsmittel, Anteil), die zwangslaeufig auf der Anzeigeseite entstehen.
//
// Bewusst ohne Renderer, wie forecastPresentation.ts: die Auswahl des
// Entscheidungsmonats, die Rangfolge und die Vorjahresabgrenzung sind die
// fehleranfaelligen Teile und lassen sich hier ohne laufenden Screen pruefen.
import type {
  AnalyticsResult,
  AnalyticsRow,
  CashflowTrendResponse,
  FixedCostsMonthData,
  ProjectReportRow,
} from "../api/types";
import { contractTypeLabel } from "./financeLabels";
import { formatMinorUnits } from "./money";

export interface ReportMonth {
  key: string;
  year: number;
  month: number;
  /** "August 2026" - fuer Vorlesehilfe und Kopfzeilen. */
  label: string;
  /** "08/26" - fuer enge Zeilen mit Betrag daneben. */
  shortLabel: string;
  from: string;
  to: string;
  isCurrent: boolean;
}

const monthYearFormatter = new Intl.DateTimeFormat("de-DE", { month: "long", year: "numeric" });

function isoDate(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function reportMonth(year: number, month: number, today = new Date()): ReportMonth {
  const lastDay = new Date(year, month, 0).getDate();
  return {
    key: `${year}-${String(month).padStart(2, "0")}`,
    year,
    month,
    label: monthYearFormatter.format(new Date(year, month - 1, 1)),
    shortLabel: `${String(month).padStart(2, "0")}/${String(year).slice(2)}`,
    from: isoDate(year, month, 1),
    to: isoDate(year, month, lastDay),
    isCurrent: year === today.getFullYear() && month === today.getMonth() + 1,
  };
}

export function monthKey(value: unknown): string {
  const match = /^(\d{4})-(\d{2})/.exec(String(value ?? ""));
  return match ? `${match[1]}-${match[2]}` : "";
}

export function monthsBetween(from: string, to: string, today = new Date()): ReportMonth[] {
  const fromMatch = /^(\d{4})-(\d{2})/.exec(from);
  const toMatch = /^(\d{4})-(\d{2})/.exec(to);
  if (!fromMatch || !toMatch) return [];

  const endYear = Number(toMatch[1]);
  const endMonth = Number(toMatch[2]);
  const result: ReportMonth[] = [];
  let year = Number(fromMatch[1]);
  let month = Number(fromMatch[2]);
  while (year < endYear || (year === endYear && month <= endMonth)) {
    result.push(reportMonth(year, month, today));
    month += 1;
    if (month === 13) {
      month = 1;
      year += 1;
    }
  }
  return result;
}

function numberValue(value: unknown): number {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

/**
 * Das Monatsraster einer Antwort. Bevorzugt der vom Server gemeldete Zeitraum
 * - dann bleiben auch Monate ohne eine einzige Buchung als Luecke sichtbar.
 * Nur wenn er fehlt, wird er aus den vorhandenen Zeilen abgeleitet.
 */
export function resultMonths(result: AnalyticsResult, today = new Date()): ReportMonth[] {
  if (result.period_from && result.period_to) {
    return monthsBetween(result.period_from, result.period_to, today);
  }
  const keys = [
    ...new Set(result.rows.map((row) => monthKey(row["date:month"])).filter(Boolean)),
  ].sort();
  const first = keys[0];
  const last = keys.at(-1);
  return first && last ? monthsBetween(first, last, today) : [];
}

/**
 * Anzeigetext einer Dimensionszelle. Die Identitaet einer Zeile kommt
 * ausschliesslich aus dem Dimensionswert (meist einer UUID), niemals aus
 * diesem Text - zwei Kategorien duerfen gleich heissen.
 */
function rowLabel(row: AnalyticsRow, key: string): string {
  const label = row[`${key}_label`];
  if (typeof label === "string" && label.trim()) return label;
  const raw = row[key];
  return raw === null || raw === undefined || raw === "" ? "Ohne Zuordnung" : String(raw);
}

export interface RankedMonthValue {
  key: string;
  shortLabel: string;
  value: number;
}

export interface RankedRow {
  id: string | null;
  identityKey: string;
  label: string;
  groupLabel: string | null;
  /** Summe ueber den gesamten Zeitraum, in Minor Units. */
  sum: number;
  /** Durchschnitt je Monat des Zeitraums, in Minor Units. */
  average: number;
  /** Anzahl der Buchungen hinter der Summe. */
  count: number;
  /** Anteil am Betragsvolumen aller Zeilen, 0..1. */
  share: number;
  /** Monatswerte in Zeitachsenreihenfolge - Grundlage des Balkenstreifens. */
  monthly: RankedMonthValue[];
}

export interface RankedResult {
  months: ReportMonth[];
  rows: RankedRow[];
  total: number;
  monthTotals: RankedMonthValue[];
}

/**
 * Formt eine serverseitig aggregierte Dimension×Monat-Antwort in eine
 * Rangliste mit vollstaendigem Monatsraster.
 *
 * Auf dem Telefon gibt es bewusst KEINE Monatstabelle wie im Web (dieselbe
 * Entscheidung wie in der Vorschau, siehe app/mehr/vorschau.tsx): zwoelf
 * Zahlenspalten sind im Hochformat unlesbar. Stattdessen traegt jede Zeile
 * ihre Periodensumme, ihren Monatsschnitt und ihre Monatswerte als
 * Balkenstreifen - dieselbe Information, ohne Querscrollen.
 */
export function rankDimensionResult(
  result: AnalyticsResult,
  dimensionKey: string,
  groupKey?: string,
  today = new Date(),
): RankedResult {
  const months = resultMonths(result, today);

  const byId = new Map<string, RankedRow & { values: Map<string, number> }>();

  result.rows.forEach((source) => {
    const rawId = source[dimensionKey];
    const identityKey = rawId === null || rawId === undefined ? "__null__" : String(rawId);
    let row = byId.get(identityKey);
    if (!row) {
      row = {
        id: rawId === null || rawId === undefined ? null : String(rawId),
        identityKey,
        label: rowLabel(source, dimensionKey),
        groupLabel: groupKey ? rowLabel(source, groupKey) : null,
        sum: 0,
        average: 0,
        count: 0,
        share: 0,
        monthly: [],
        values: new Map<string, number>(),
      };
      byId.set(identityKey, row);
    }
    const key = monthKey(source["date:month"]);
    if (key) {
      row.values.set(key, (row.values.get(key) ?? 0) + numberValue(source.sum_amount));
    }
    row.count += numberValue(source.count_amount);
  });

  const rows = [...byId.values()].map((row) => {
    const monthly = months.map((month) => ({
      key: month.key,
      shortLabel: month.shortLabel,
      value: row.values.get(month.key) ?? 0,
    }));
    const sum = monthly.reduce((total, entry) => total + entry.value, 0);
    return {
      id: row.id,
      identityKey: row.identityKey,
      label: row.label,
      groupLabel: row.groupLabel,
      sum,
      average: months.length > 0 ? Math.round(sum / months.length) : 0,
      count: row.count,
      share: 0,
      monthly,
    };
  });

  const magnitude = rows.reduce((total, row) => total + Math.abs(row.sum), 0);
  rows.forEach((row) => {
    row.share = magnitude > 0 ? Math.abs(row.sum) / magnitude : 0;
  });
  rows.sort((a, b) => Math.abs(b.sum) - Math.abs(a.sum) || a.label.localeCompare(b.label, "de"));

  return {
    months,
    rows,
    total: rows.reduce((total, row) => total + row.sum, 0),
    monthTotals: months.map((month) => ({
      key: month.key,
      shortLabel: month.shortLabel,
      value: rows.reduce(
        (total, row) => total + (row.monthly.find((entry) => entry.key === month.key)?.value ?? 0),
        0,
      ),
    })),
  };
}

export type FigureTone = "positive" | "negative" | "neutral";

export interface ReportFigure {
  key: string;
  label: string;
  value: string;
  tone: FigureTone;
  /** Vergleich zum Vormonat bzw. Vorjahreszeitraum - im Klartext, nicht als Pfeil. */
  comparison: string;
  /** Einordnung des gesamten Zeitraums, damit ein Ausreissermonat auffaellt. */
  periodHint: string;
  accessibilityLabel: string;
}

export interface CashflowMonthRow {
  key: string;
  title: string;
  incomeLabel: string;
  expenseLabel: string;
  netLabel: string;
  netIsNegative: boolean;
  isRunning: boolean;
  from: string;
  to: string;
  accessibilityLabel: string;
}

export interface CashflowOverview {
  /** Juengster vollstaendig abgeschlossener Monat. */
  decisionMonth: ReportMonth;
  figures: ReportFigure[];
  months: CashflowMonthRow[];
}

function signedAmount(value: number): string {
  return `${value > 0 ? "+" : ""}${formatMinorUnits(value)}`;
}

function amountTone(value: number): FigureTone {
  return value >= 0 ? "positive" : "negative";
}

/**
 * Kennzahlen und Monatsreihe des Cashflows.
 *
 * Der laufende Monat ist absichtlich NICHT der Bezugspunkt der Kennzahlen: er
 * ist unvollstaendig, und "Ausgaben im August" waere am 3. August eine
 * Erfolgsmeldung ohne Grundlage. Er bleibt in der Monatsreihe stehen, dort
 * aber ausdruecklich als laufend gekennzeichnet.
 *
 * Gibt `null` zurueck, wenn es keinen einzigen Monat gibt - der Screen zeigt
 * dann einen Leerzustand statt Nullen, die wie ein Ergebnis aussehen.
 */
export function describeCashflow(
  trend: CashflowTrendResponse,
  today = new Date(),
): CashflowOverview | null {
  const entries = trend.months;
  if (entries.length === 0) return null;

  const isRunning = (entry: { year: number; month: number }) =>
    entry.year === today.getFullYear() && entry.month === today.getMonth() + 1;

  const complete = entries.filter((entry) => !isRunning(entry));
  const decision = complete.at(-1) ?? entries.at(-1);
  if (!decision) return null;
  const previous = complete
    .filter(
      (entry) =>
        entry.year < decision.year ||
        (entry.year === decision.year && entry.month < decision.month),
    )
    .at(-1);

  const decisionMonth = reportMonth(decision.year, decision.month, today);
  const periodIncome = entries.reduce((sum, entry) => sum + entry.income_minor, 0);
  const periodExpense = entries.reduce((sum, entry) => sum + entry.expense_minor, 0);
  const periodNet = entries.reduce((sum, entry) => sum + entry.net_minor, 0);
  const decisionSavings =
    decision.income_minor > 0 ? (decision.net_minor / decision.income_minor) * 100 : 0;
  const previousSavings =
    previous && previous.income_minor > 0
      ? (previous.net_minor / previous.income_minor) * 100
      : null;
  const periodSavings = periodIncome > 0 ? (periodNet / periodIncome) * 100 : 0;

  const compare = (current: number, before: number | undefined) =>
    before === undefined
      ? "Kein vollständiger Vormonat zum Vergleich"
      : `${signedAmount(current - before)} zum Vormonat`;

  const figure = (
    key: string,
    label: string,
    value: number,
    comparison: string,
    periodHint: string,
  ): ReportFigure => ({
    key,
    label,
    value: formatMinorUnits(value),
    tone: amountTone(value),
    comparison,
    periodHint,
    accessibilityLabel: `${label}: ${formatMinorUnits(value)}. ${comparison}. ${periodHint}.`,
  });

  const averageOrZero = (total: number) =>
    entries.length > 0 ? Math.round(total / entries.length) : 0;

  const savingsComparison =
    previousSavings === null
      ? "Kein vollständiger Vormonat zum Vergleich"
      : `${decisionSavings - previousSavings >= 0 ? "+" : ""}${(
          decisionSavings - previousSavings
        ).toFixed(1)} Prozentpunkte zum Vormonat`;

  const figures: ReportFigure[] = [
    figure(
      "income",
      "Einnahmen",
      decision.income_minor,
      compare(decision.income_minor, previous?.income_minor),
      `Ø Zeitraum ${formatMinorUnits(averageOrZero(periodIncome))}`,
    ),
    figure(
      "expense",
      "Ausgaben",
      -decision.expense_minor,
      compare(-decision.expense_minor, previous ? -previous.expense_minor : undefined),
      `Ø Zeitraum ${formatMinorUnits(-averageOrZero(periodExpense))}`,
    ),
    figure(
      "net",
      "Saldo",
      decision.net_minor,
      compare(decision.net_minor, previous?.net_minor),
      `Summe Zeitraum ${formatMinorUnits(periodNet)}`,
    ),
    {
      key: "savings",
      label: "Sparquote",
      value: `${decisionSavings.toFixed(1)} %`,
      tone: decisionSavings >= 0 ? "positive" : "negative",
      comparison: savingsComparison,
      periodHint: `Zeitraum ${periodSavings.toFixed(1)} %`,
      accessibilityLabel: `Sparquote: ${decisionSavings.toFixed(
        1,
      )} Prozent. ${savingsComparison}. Zeitraum ${periodSavings.toFixed(1)} Prozent.`,
    },
  ];

  const months = entries
    .map((entry): CashflowMonthRow => {
      const month = reportMonth(entry.year, entry.month, today);
      const running = isRunning(entry);
      return {
        key: month.key,
        title: month.label,
        incomeLabel: formatMinorUnits(entry.income_minor),
        expenseLabel: formatMinorUnits(-entry.expense_minor),
        netLabel: formatMinorUnits(entry.net_minor),
        netIsNegative: entry.net_minor < 0,
        isRunning: running,
        from: month.from,
        to: month.to,
        accessibilityLabel: `${month.label}${running ? ", laufender Monat" : ""}. Einnahmen ${formatMinorUnits(
          entry.income_minor,
        )}, Ausgaben ${formatMinorUnits(-entry.expense_minor)}, Saldo ${formatMinorUnits(
          entry.net_minor,
        )}.`,
      };
    })
    .reverse();

  return { decisionMonth, figures, months };
}

export interface FairYearPeriods {
  selectedYear: number;
  previousYear: number;
  monthCount: number;
  selectedFrom: string;
  selectedTo: string;
  previousFrom: string;
  previousTo: string;
  scopeLabel: string;
}

const MONTH_NAMES = [
  "Januar",
  "Februar",
  "März",
  "April",
  "Mai",
  "Juni",
  "Juli",
  "August",
  "September",
  "Oktober",
  "November",
  "Dezember",
];

/**
 * Gleich lange Zeitraeume fuer den Jahresvergleich.
 *
 * Im laufenden Jahr werden nur die vollstaendig abgeschlossenen Monate
 * verglichen und im Vorjahr genau dieselben - sonst staende ein halbes Jahr
 * gegen ein ganzes und jedes Jahr saehe sparsamer aus als sein Vorgaenger.
 *
 * `null`, wenn der Vergleich nicht fair zu ziehen ist: Zukunftsjahr, oder
 * Januar des laufenden Jahres (dann gibt es noch keinen abgeschlossenen
 * Monat).
 */
export function fairYearPeriods(selectedYear: number, today = new Date()): FairYearPeriods | null {
  const currentYear = today.getFullYear();
  if (selectedYear > currentYear) return null;
  const monthCount = selectedYear === currentYear ? today.getMonth() : 12;
  if (monthCount < 1) return null;

  const previousYear = selectedYear - 1;
  const endMonthName = MONTH_NAMES[monthCount - 1];
  return {
    selectedYear,
    previousYear,
    monthCount,
    selectedFrom: isoDate(selectedYear, 1, 1),
    selectedTo: isoDate(selectedYear, monthCount, new Date(selectedYear, monthCount, 0).getDate()),
    previousFrom: isoDate(previousYear, 1, 1),
    previousTo: isoDate(previousYear, monthCount, new Date(previousYear, monthCount, 0).getDate()),
    scopeLabel:
      monthCount === 12
        ? `Gesamtes Jahr ${selectedYear} gegen ${previousYear}`
        : `Januar–${endMonthName} ${selectedYear} gegen denselben Zeitraum ${previousYear}`,
  };
}

export interface ComparisonEntry {
  id: string | null;
  identityKey: string;
  label: string;
  selected: number;
  previous: number;
  delta: number;
  /** `null`, wenn es im Vorjahreszeitraum keinen Betrag gab - "unendlich mehr" ist keine Aussage. */
  deltaPercent: number | null;
  selectedLabel: string;
  previousLabel: string;
  deltaLabel: string;
  deltaPercentLabel: string;
  accessibilityLabel: string;
}

/** Kategorien beider Zeitraeume gegenueberstellen, groesste Veraenderung zuerst. */
export function compareCategoryResults(
  selected: AnalyticsResult | undefined,
  previous: AnalyticsResult | undefined,
  dimensionKey = "category",
): ComparisonEntry[] {
  const entries = new Map<
    string,
    { id: string | null; label: string; selected: number; previous: number }
  >();

  const add = (result: AnalyticsResult | undefined, side: "selected" | "previous") => {
    result?.rows.forEach((row) => {
      const rawId = row[dimensionKey];
      const identityKey = rawId === null || rawId === undefined ? "__null__" : String(rawId);
      const entry = entries.get(identityKey) ?? {
        id: rawId === null || rawId === undefined ? null : String(rawId),
        label: rowLabel(row, dimensionKey),
        selected: 0,
        previous: 0,
      };
      entry[side] += numberValue(row.sum_amount);
      entries.set(identityKey, entry);
    });
  };
  add(selected, "selected");
  add(previous, "previous");

  return [...entries.entries()]
    .map(([identityKey, entry]) => {
      const delta = entry.selected - entry.previous;
      const deltaPercent = entry.previous === 0 ? null : (delta / Math.abs(entry.previous)) * 100;
      const deltaLabel = signedAmount(delta);
      const deltaPercentLabel =
        deltaPercent === null
          ? "kein Vorjahreswert"
          : `${deltaPercent > 0 ? "+" : ""}${deltaPercent.toFixed(1)} %`;
      return {
        id: entry.id,
        identityKey,
        label: entry.label,
        selected: entry.selected,
        previous: entry.previous,
        delta,
        deltaPercent,
        selectedLabel: formatMinorUnits(entry.selected),
        previousLabel: formatMinorUnits(entry.previous),
        deltaLabel,
        deltaPercentLabel,
        accessibilityLabel: `${entry.label}: ${formatMinorUnits(
          entry.selected,
        )} gegenüber ${formatMinorUnits(entry.previous)} im Vorjahreszeitraum, ${deltaLabel}.`,
      };
    })
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta) || a.label.localeCompare(b.label, "de"));
}

export interface FixedCostsTypeRow {
  key: string;
  label: string;
  monthlyLabel: string;
  yearlyLabel: string;
  shareLabel: string;
}

export interface FixedCostsMonthRow {
  key: string;
  title: string;
  amountLabel: string;
  contractCount: number;
  yoyLabel: string;
  accessibilityLabel: string;
}

export interface FixedCostsOverview {
  latestMonthLabel: string;
  figures: ReportFigure[];
  types: FixedCostsTypeRow[];
  months: FixedCostsMonthRow[];
}

/**
 * Register "Fixkosten": was war in diesem Monat VEREINBART?
 *
 * Die Werte stammen aus dem Betragsverlauf der Vertraege, nicht aus
 * Buchungen. Deshalb gibt es hier auch keinen Drilldown: hinter einer
 * Jahrespraemie, die in allen zwoelf Monaten vereinbart und in genau einem
 * gebucht ist, liegen keine zwoelf Buchungen.
 */
export function describeFixedCosts(months: FixedCostsMonthData[]): FixedCostsOverview | null {
  const latest = months.at(-1);
  if (!latest) return null;

  const latestMonth = reportMonth(latest.year, latest.month);
  const yoyLabel = (percent: number | null) =>
    percent === null
      ? "Vorjahresmonat nicht bekannt"
      : `${percent > 0 ? "+" : ""}${percent.toFixed(1)} % gegenüber dem Vorjahresmonat`;

  const figures: ReportFigure[] = [
    {
      key: "current",
      label: `Fixkosten ${latestMonth.shortLabel}`,
      value: formatMinorUnits(latest.fixed_costs_minor),
      tone: "negative",
      comparison: yoyLabel(latest.yoy_percent),
      periodHint: `${latest.contract_count} Vertrag/Verträge, auf den Monat gerechnet`,
      accessibilityLabel: `Fixkosten ${latestMonth.label}: ${formatMinorUnits(
        latest.fixed_costs_minor,
      )}. ${yoyLabel(latest.yoy_percent)}.`,
    },
    {
      key: "yearly",
      label: "Hochgerechnet aufs Jahr",
      value: formatMinorUnits(latest.fixed_costs_minor * 12),
      tone: "negative",
      comparison: "Zwölf Monate zum heutigen Stand",
      periodHint: "Keine Prognose, nur die aktuelle Vereinbarung",
      accessibilityLabel: `Hochgerechnet aufs Jahr: ${formatMinorUnits(
        latest.fixed_costs_minor * 12,
      )}.`,
    },
  ];

  const types = [...latest.by_type]
    .sort((a, b) => b.amount_minor - a.amount_minor)
    .map((entry) => ({
      key: entry.contract_type,
      label: contractTypeLabel(entry.contract_type),
      monthlyLabel: formatMinorUnits(entry.amount_minor),
      yearlyLabel: formatMinorUnits(entry.amount_minor * 12),
      shareLabel:
        latest.fixed_costs_minor > 0
          ? `${Math.round((entry.amount_minor / latest.fixed_costs_minor) * 100)} %`
          : "—",
    }));

  const monthRows = [...months].reverse().map((entry): FixedCostsMonthRow => {
    const month = reportMonth(entry.year, entry.month);
    return {
      key: month.key,
      title: month.label,
      amountLabel: formatMinorUnits(entry.fixed_costs_minor),
      contractCount: entry.contract_count,
      yoyLabel:
        entry.yoy_percent === null
          ? "—"
          : `${entry.yoy_percent > 0 ? "+" : ""}${entry.yoy_percent.toFixed(1)} %`,
      accessibilityLabel: `${month.label}: ${formatMinorUnits(entry.fixed_costs_minor)} aus ${
        entry.contract_count
      } Verträgen. ${yoyLabel(entry.yoy_percent)}.`,
    };
  });

  return { latestMonthLabel: latestMonth.label, figures, types, months: monthRows };
}

export interface ProjectReportEntry {
  key: string;
  name: string;
  scenarioName: string;
  forecastLabel: string;
  actualLabel: string;
  varianceLabel: string;
  varianceIsNegative: boolean;
  financingLabel: string;
  warningCount: number;
  accessibilityLabel: string;
}

export interface ProjectReportsOverview {
  projectCount: number;
  figures: ReportFigure[];
  entries: ProjectReportEntry[];
}

/**
 * Register "Vorhaben": Forecast, Ist und Finanzierungskosten je aktivem
 * Vorhaben. Eigener Datenpfad, keine Vermischung mit dem Cashflow - eine
 * Bausumme haengt an Kostenpositionen und Tilgungsplaenen, nicht an Splits.
 */
export function describeProjectReports(rows: ProjectReportRow[]): ProjectReportsOverview | null {
  if (rows.length === 0) return null;

  const totals = rows.reduce(
    (acc, row) => ({
      forecast: acc.forecast + row.forecast_cost_minor,
      actual: acc.actual + row.actual_cost_minor,
      financing: acc.financing + row.financing_cost_minor,
    }),
    { forecast: 0, actual: 0, financing: 0 },
  );

  const figures: ReportFigure[] = [
    {
      key: "forecast",
      label: "Forecast (Cash)",
      value: formatMinorUnits(totals.forecast),
      tone: "neutral",
      comparison: `${rows.length} aktive${rows.length === 1 ? "s" : ""} Vorhaben`,
      periodHint: "Ausgewähltes Szenario je Vorhaben",
      accessibilityLabel: `Forecast über alle Vorhaben: ${formatMinorUnits(totals.forecast)}.`,
    },
    {
      key: "actual",
      label: "Ist",
      value: formatMinorUnits(totals.actual),
      tone: "neutral",
      comparison: `Abweichung ${signedAmount(totals.forecast - totals.actual)}`,
      periodHint: "Zugeordnete tatsächliche Kosten",
      accessibilityLabel: `Ist über alle Vorhaben: ${formatMinorUnits(totals.actual)}.`,
    },
    {
      key: "financing",
      label: "Finanzierungskosten",
      value: formatMinorUnits(totals.financing),
      tone: "negative",
      comparison: "Zinsen laut Tilgungsplan",
      periodHint: "Nicht im Forecast enthalten",
      accessibilityLabel: `Finanzierungskosten: ${formatMinorUnits(totals.financing)}.`,
    },
  ];

  const entries = rows.map((row): ProjectReportEntry => {
    const variance = row.variance_minor;
    return {
      key: row.project_id,
      name: row.project_name,
      scenarioName: row.scenario_name,
      forecastLabel: formatMinorUnits(row.forecast_cost_minor),
      actualLabel: formatMinorUnits(row.actual_cost_minor),
      varianceLabel: signedAmount(variance),
      varianceIsNegative: variance < 0,
      financingLabel: formatMinorUnits(row.financing_cost_minor),
      warningCount: row.warnings.length,
      accessibilityLabel: `${row.project_name}, Szenario ${row.scenario_name}. Forecast ${formatMinorUnits(
        row.forecast_cost_minor,
      )}, Ist ${formatMinorUnits(row.actual_cost_minor)}, Abweichung ${signedAmount(variance)}.${
        row.warnings.length > 0 ? ` ${row.warnings.length} Warnung(en).` : ""
      }`,
    };
  });

  return { projectCount: rows.length, figures, entries };
}

/**
 * Zuschnitt-Zeile ueber jedem Register. Sie steht dort, weil eine Summe ohne
 * ihre Abgrenzung nicht pruefbar ist: dieselben Buchungen ergeben je nach
 * Transferbehandlung und Kontenauswahl verschiedene Zahlen.
 */
export function describeScope(
  register: "ueberblick" | "kategorien" | "empfaenger" | "vergleich" | "fixkosten" | "vorhaben",
  months: number,
  extra?: string,
): string {
  switch (register) {
    case "fixkosten":
      return `Letzte ${months} Monate · vereinbarte Vertragsbeträge`;
    case "vorhaben":
      return "Aktive Vorhaben · ausgewähltes Szenario";
    case "vergleich":
      return extra ? `${extra} · nur vollständige Monate` : "Nur vollständige Monate";
    default:
      return `Letzte ${months} Monate · Transfers aus · nur Budgetkonten`;
  }
}

/** Betragsformatierung der Berichte an einer Stelle, damit Register nicht auseinanderlaufen. */
export { formatMinorUnits as formatReportAmount };
