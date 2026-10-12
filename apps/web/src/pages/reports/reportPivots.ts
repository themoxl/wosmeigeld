import { formatMonthYear, monthNameLong } from "../../lib/dates";
import type { AnalyticsResult, AnalyticsRow } from "../../lib/types";

export interface ReportMonth {
  key: string;
  year: number;
  month: number;
  label: string;
  from: string;
  to: string;
  isCurrent: boolean;
}

export interface MonthlyPivotRow {
  id: string | null;
  identityKey: string;
  label: string;
  groupId: string | null;
  groupLabel: string | null;
  values: Record<string, number | null>;
  counts: Record<string, number>;
  sum: number;
  average: number;
  count: number;
  share: number;
}

export interface MonthlyPivot {
  months: ReportMonth[];
  rows: MonthlyPivotRow[];
  monthTotals: Record<string, number>;
  sum: number;
  average: number;
  count: number;
}

function isoDate(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function reportMonth(year: number, month: number, today = new Date()): ReportMonth {
  const lastDay = new Date(year, month, 0).getDate();
  return {
    key: `${year}-${String(month).padStart(2, "0")}`,
    year,
    month,
    label: formatMonthYear(new Date(year, month - 1, 1)),
    from: isoDate(year, month, 1),
    to: isoDate(year, month, lastDay),
    isCurrent: year === today.getFullYear() && month === today.getMonth() + 1,
  };
}

export function monthsBetween(from: string, to: string, today = new Date()): ReportMonth[] {
  const fromMatch = /^(\d{4})-(\d{2})/.exec(from);
  const toMatch = /^(\d{4})-(\d{2})/.exec(to);
  if (!fromMatch || !toMatch) return [];

  const startYear = Number(fromMatch[1]);
  const startMonth = Number(fromMatch[2]);
  const endYear = Number(toMatch[1]);
  const endMonth = Number(toMatch[2]);
  const result: ReportMonth[] = [];
  let year = startYear;
  let month = startMonth;
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

export function resultMonths(result: AnalyticsResult, today = new Date()): ReportMonth[] {
  if (result.period_from && result.period_to) {
    return monthsBetween(result.period_from, result.period_to, today);
  }
  const keys = [
    ...new Set(result.rows.map((row) => monthKey(row["date:month"])).filter(Boolean)),
  ].sort();
  if (keys.length === 0) return [];
  return monthsBetween(keys[0]!, keys[keys.length - 1]!, today);
}

export function monthKey(value: unknown): string {
  const match = /^(\d{4})-(\d{2})/.exec(String(value ?? ""));
  return match ? `${match[1]}-${match[2]}` : "";
}

function numberValue(value: unknown): number {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

function rowLabel(row: AnalyticsRow, key: string): string {
  const label = row[`${key}_label`];
  if (typeof label === "string" && label.trim()) return label;
  const raw = row[key];
  return raw === null || raw === undefined || raw === "" ? "Ohne Zuordnung" : String(raw);
}

/**
 * Formt eine serverseitig aggregierte Dimension×Monat-Antwort in ein
 * vollständiges Monatsraster. Identität kommt ausschließlich aus dem
 * Dimensionswert (meist UUID), niemals aus dem sichtbaren Namen.
 */
export function pivotMonthlyResult(
  result: AnalyticsResult,
  dimensionKey: string,
  groupKey?: string,
  today = new Date(),
): MonthlyPivot {
  const months = resultMonths(result, today);
  const byId = new Map<string, MonthlyPivotRow>();

  result.rows.forEach((source) => {
    const rawId = source[dimensionKey];
    const identityKey = rawId === null || rawId === undefined ? "__null__" : String(rawId);
    let row = byId.get(identityKey);
    if (!row) {
      const rawGroup = groupKey ? source[groupKey] : null;
      row = {
        id: rawId === null || rawId === undefined ? null : String(rawId),
        identityKey,
        label: rowLabel(source, dimensionKey),
        groupId: rawGroup === null || rawGroup === undefined ? null : String(rawGroup),
        groupLabel: groupKey ? rowLabel(source, groupKey) : null,
        values: Object.fromEntries(months.map((month) => [month.key, null])),
        counts: Object.fromEntries(months.map((month) => [month.key, 0])),
        sum: 0,
        average: 0,
        count: 0,
        share: 0,
      };
      byId.set(identityKey, row);
    }
    const key = monthKey(source["date:month"]);
    if (!key) return;
    const amount = numberValue(source.sum_amount);
    row.values[key] = (row.values[key] ?? 0) + amount;
    row.counts[key] = (row.counts[key] ?? 0) + numberValue(source.count_amount);
  });

  const rows = [...byId.values()];
  rows.forEach((row) => {
    row.sum = Object.values(row.values).reduce<number>((sum, value) => sum + (value ?? 0), 0);
    row.average = months.length > 0 ? Math.round(row.sum / months.length) : 0;
    row.count = Object.values(row.counts).reduce((sum, value) => sum + value, 0);
  });
  const sum = rows.reduce((total, row) => total + row.sum, 0);
  const magnitude = rows.reduce((total, row) => total + Math.abs(row.sum), 0);
  rows.forEach((row) => {
    row.share = magnitude > 0 ? Math.abs(row.sum) / magnitude : 0;
  });
  rows.sort((a, b) => Math.abs(b.sum) - Math.abs(a.sum) || a.label.localeCompare(b.label, "de"));

  const monthTotals = Object.fromEntries(
    months.map((month) => [
      month.key,
      rows.reduce((total, row) => total + (row.values[month.key] ?? 0), 0),
    ]),
  );
  return {
    months,
    rows,
    monthTotals,
    sum,
    average: months.length > 0 ? Math.round(sum / months.length) : 0,
    count: rows.reduce((total, row) => total + row.count, 0),
  };
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

export function fairYearPeriods(selectedYear: number, today = new Date()): FairYearPeriods | null {
  const currentYear = today.getFullYear();
  if (selectedYear > currentYear) return null;
  const monthCount = selectedYear === currentYear ? today.getMonth() : 12;
  if (monthCount < 1) return null;
  const previousYear = selectedYear - 1;
  const selectedFrom = isoDate(selectedYear, 1, 1);
  const selectedTo = isoDate(
    selectedYear,
    monthCount,
    new Date(selectedYear, monthCount, 0).getDate(),
  );
  const previousFrom = isoDate(previousYear, 1, 1);
  const previousTo = isoDate(
    previousYear,
    monthCount,
    new Date(previousYear, monthCount, 0).getDate(),
  );
  const endMonth = monthNameLong(monthCount - 1);
  return {
    selectedYear,
    previousYear,
    monthCount,
    selectedFrom,
    selectedTo,
    previousFrom,
    previousTo,
    scopeLabel:
      monthCount === 12
        ? `01.01.–31.12.${selectedYear} · gleicher Volljahreszeitraum ${previousYear}`
        : `Januar–${endMonth} ${selectedYear} · gleicher Zeitraum ${previousYear} · nur vollständige Monate`,
  };
}

export interface ComparisonEntry {
  id: string | null;
  identityKey: string;
  label: string;
  selected: number;
  previous: number;
  delta: number;
  deltaPercent: number | null;
}

export function compareDimensionResults(
  selected: AnalyticsResult | undefined,
  previous: AnalyticsResult | undefined,
  dimensionKey: string,
): ComparisonEntry[] {
  const entries = new Map<string, ComparisonEntry>();
  const add = (result: AnalyticsResult | undefined, side: "selected" | "previous") => {
    result?.rows.forEach((row) => {
      const rawId = row[dimensionKey];
      const identityKey = rawId === null || rawId === undefined ? "__null__" : String(rawId);
      const entry = entries.get(identityKey) ?? {
        id: rawId === null || rawId === undefined ? null : String(rawId),
        identityKey,
        label: rowLabel(row, dimensionKey),
        selected: 0,
        previous: 0,
        delta: 0,
        deltaPercent: null,
      };
      entry[side] += numberValue(row.sum_amount);
      entries.set(identityKey, entry);
    });
  };
  add(selected, "selected");
  add(previous, "previous");
  return [...entries.values()]
    .map((entry) => {
      const delta = entry.selected - entry.previous;
      return {
        ...entry,
        delta,
        deltaPercent: entry.previous === 0 ? null : (delta / Math.abs(entry.previous)) * 100,
      };
    })
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta) || a.label.localeCompare(b.label, "de"));
}
