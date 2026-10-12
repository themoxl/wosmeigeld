import type { EChartsOption } from "echarts";
import { dimensionLabel } from "../../lib/analytics";
import { formatMonthYear } from "../../lib/dates";
import type { AnalyticsResult } from "../../lib/types";
import {
  axisStyle,
  baseOption,
  CATEGORICAL,
  DIVERGING,
  formatAxisAmount,
  formatTooltipAmount,
  readPalette,
  SEQUENTIAL,
} from "./chartTheme";

// Diagrammoptionen aus einem Abfrageergebnis (docs/ANALYSE-EXPLORER-PLAN.md,
// D.2). Jede Funktion nimmt dasselbe AnalyticsResult - dadurch laesst sich
// der Diagrammtyp wechseln, ohne die Abfrage neu zu bauen.

function amountFormatter(value: number): string {
  return formatTooltipAmount(value);
}

/** Balken: Kategorie auf der einen, ein Wert auf der anderen Achse. */
export function barOption(
  result: AnalyticsResult,
  dimensionKey: string,
  measureKey: string,
  options: { horizontal?: boolean } = {},
): EChartsOption {
  const palette = readPalette();
  const labels = result.rows.map((row) => dimensionLabel(row, dimensionKey));
  const values = result.rows.map((row) => Number(row[measureKey] ?? 0));

  const categoryAxis = {
    type: "category" as const,
    data: labels,
    ...axisStyle(palette),
    splitLine: { show: false },
    axisLabel: {
      color: palette.textSecondary,
      fontSize: 12,
      // Lange Empfaengernamen sonst als Bandwurm - kuerzen statt drehen,
      // der vollstaendige Name steht im Tooltip.
      formatter: (value: string) => (value.length > 18 ? `${value.slice(0, 17)}…` : value),
    },
  };
  const valueAxis = {
    type: "value" as const,
    ...axisStyle(palette),
    axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
  };

  return {
    ...baseOption(palette),
    tooltip: {
      ...baseOption(palette).tooltip,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value) => amountFormatter(Number(value)),
    },
    xAxis: options.horizontal ? valueAxis : categoryAxis,
    yAxis: options.horizontal ? categoryAxis : valueAxis,
    series: [
      {
        type: "bar",
        // Farbe je Datenpunkt statt per Callback: das bleibt vollstaendig
        // typisiert und erspart einen Cast auf die ECharts-Callback-Signatur.
        //
        // Ausgaben sind negativ gespeichert und werden rot, Einnahmen gruen.
        // Das ist die einzige Stelle, an der Farbe Bedeutung traegt statt nur
        // Kategorien zu unterscheiden.
        data: values.map((value) => ({
          value,
          itemStyle: {
            color: value < 0 ? palette.warning : palette.positive,
            borderRadius: options.horizontal
              ? ([0, 4, 4, 0] as [number, number, number, number])
              : ([4, 4, 0, 0] as [number, number, number, number]),
          },
        })),
        barMaxWidth: 40,
      },
    ],
  };
}

/** Gestapelte Balken je Zeitachse - eine Serie je Gruppe. */
export function stackedBarOption(
  result: AnalyticsResult,
  timeKey: string,
  seriesKey: string,
  measureKey: string,
): EChartsOption {
  const palette = readPalette();
  const times = [...new Set(result.rows.map((row) => String(row[timeKey])))].sort();
  const seriesNames = [...new Set(result.rows.map((row) => dimensionLabel(row, seriesKey)))];

  const series = seriesNames.map((name, index) => ({
    name,
    type: "bar" as const,
    stack: "total",
    itemStyle: { color: CATEGORICAL[index % CATEGORICAL.length] },
    data: times.map((time) => {
      const row = result.rows.find(
        (r) => String(r[timeKey]) === time && dimensionLabel(r, seriesKey) === name,
      );
      return row ? Math.abs(Number(row[measureKey] ?? 0)) : 0;
    }),
  }));

  return {
    ...baseOption(palette),
    tooltip: {
      ...baseOption(palette).tooltip,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value) => amountFormatter(Number(value)),
    },
    xAxis: {
      type: "category",
      data: times.map(formatMonthYear),
      ...axisStyle(palette),
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      ...axisStyle(palette),
      axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
    },
    series,
  };
}

/**
 * Kombidiagramm fuer den Cashflow: Einnahmen und Ausgaben als Balken, das
 * Netto als Linie darueber. Das Netto als dritten Balken zu zeigen waere
 * irrefuehrend - es ist die Differenz der beiden anderen, keine dritte Groesse.
 */
export function cashflowOption(
  months: { label: string; income: number; expense: number; net: number }[],
): EChartsOption {
  const palette = readPalette();
  return {
    ...baseOption(palette),
    tooltip: {
      ...baseOption(palette).tooltip,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value) => amountFormatter(Number(value)),
    },
    legend: { ...baseOption(palette).legend, data: ["Einnahmen", "Ausgaben", "Netto"] },
    xAxis: {
      type: "category",
      data: months.map((m) => m.label),
      ...axisStyle(palette),
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      ...axisStyle(palette),
      axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
    },
    series: [
      {
        name: "Einnahmen",
        type: "bar",
        data: months.map((m) => m.income),
        itemStyle: { color: palette.positive, borderRadius: [4, 4, 0, 0] },
        barMaxWidth: 24,
      },
      {
        name: "Ausgaben",
        type: "bar",
        data: months.map((m) => m.expense),
        itemStyle: { color: palette.warning, borderRadius: [4, 4, 0, 0] },
        barMaxWidth: 24,
      },
      {
        name: "Netto",
        type: "line",
        data: months.map((m) => m.net),
        smooth: true,
        symbolSize: 6,
        lineStyle: { width: 2, color: palette.highlight },
        itemStyle: { color: palette.highlight },
      },
    ],
  };
}

/** Ring statt Torte: der leere Kern nimmt die Gesamtsumme auf. */
export function donutOption(
  result: AnalyticsResult,
  dimensionKey: string,
  measureKey: string,
): EChartsOption {
  const palette = readPalette();
  return {
    ...baseOption(palette),
    tooltip: {
      ...baseOption(palette).tooltip,
      trigger: "item",
      valueFormatter: (value) => amountFormatter(Number(value)),
    },
    legend: { ...baseOption(palette).legend, orient: "vertical", right: 0, top: "middle" },
    series: [
      {
        type: "pie",
        radius: ["45%", "72%"],
        center: ["38%", "50%"],
        avoidLabelOverlap: true,
        label: { show: false },
        data: result.rows.map((row, index) => ({
          name: dimensionLabel(row, dimensionKey),
          value: Math.abs(Number(row[measureKey] ?? 0)),
          itemStyle: { color: CATEGORICAL[index % CATEGORICAL.length] },
        })),
      },
    ],
  };
}

/**
 * Kategorie x Monat als Heatmap - die Monatsmatrix des Bankreports, aber
 * eingefaerbt. Genau hier faellt ein Ausreisser auf, den eine Zahlentabelle
 * verbirgt.
 */
export function heatmapOption(
  rows: { category: string; values: (number | null)[] }[],
  monthLabels: string[],
): EChartsOption {
  const palette = readPalette();
  const data: [number, number, number][] = [];
  let max = 0;
  rows.forEach((row, y) => {
    row.values.forEach((value, x) => {
      if (value === null || value === 0) return;
      const magnitude = Math.abs(value);
      max = Math.max(max, magnitude);
      data.push([x, y, magnitude]);
    });
  });

  return {
    ...baseOption(palette),
    grid: { left: 8, right: 24, top: 8, bottom: 48, containLabel: true },
    tooltip: {
      ...baseOption(palette).tooltip,
      formatter: (params) => {
        // ECharts typisiert den Tooltip-Parameter fuer alle Serientypen
        // gemeinsam; fuer die Heatmap ist `data` das [x, y, wert]-Tripel.
        const cell = (params as unknown as { data: [number, number, number] }).data;
        const category = rows[cell[1]]?.category ?? "";
        const month = monthLabels[cell[0]] ?? "";
        return `${category}<br/>${month}: ${formatTooltipAmount(cell[2])}`;
      },
    },
    legend: { show: false },
    xAxis: {
      type: "category",
      data: monthLabels,
      splitArea: { show: true },
      ...axisStyle(palette),
      splitLine: { show: false },
    },
    yAxis: {
      type: "category",
      data: rows.map((row) => row.category),
      splitArea: { show: true },
      ...axisStyle(palette),
      splitLine: { show: false },
      axisLabel: {
        color: palette.textSecondary,
        fontSize: 12,
        formatter: (value: string) => (value.length > 22 ? `${value.slice(0, 21)}…` : value),
      },
    },
    visualMap: {
      min: 0,
      max: max || 1,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: SEQUENTIAL },
      textStyle: { color: palette.textSecondary, fontSize: 11 },
      formatter: (value) => formatAxisAmount(Number(value)),
    },
    series: [
      {
        type: "heatmap",
        data,
        label: { show: false },
        emphasis: { itemStyle: { borderColor: palette.text, borderWidth: 1 } },
      },
    ],
  };
}

/**
 * Jahresvergleich als abweichende Balken: Mehrausgaben rot, Einsparungen
 * gruen. Die divergierende Palette ist genau fuer diesen Fall gedacht -
 * eine kategoriale waere hier bedeutungslos.
 */
export function deltaBarOption(entries: { label: string; delta: number }[]): EChartsOption {
  const palette = readPalette();
  return {
    ...baseOption(palette),
    tooltip: {
      ...baseOption(palette).tooltip,
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value) => amountFormatter(Number(value)),
    },
    xAxis: {
      type: "value",
      ...axisStyle(palette),
      axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
    },
    yAxis: {
      type: "category",
      data: entries.map((entry) => entry.label),
      ...axisStyle(palette),
      splitLine: { show: false },
      axisLabel: {
        color: palette.textSecondary,
        fontSize: 12,
        formatter: (value: string) => (value.length > 22 ? `${value.slice(0, 21)}…` : value),
      },
    },
    series: [
      {
        type: "bar",
        data: entries.map((entry) => ({
          value: entry.delta,
          itemStyle: {
            // Mehrausgaben in der Warnfarbe, Einsparungen in der positiven -
            // die divergierende Palette ist genau fuer diesen Fall gedacht.
            color: entry.delta > 0 ? DIVERGING[0] : DIVERGING[4],
            borderRadius: 4,
          },
        })),
        barMaxWidth: 22,
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Liquiditaetsvorschau (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, 8.2)
// ---------------------------------------------------------------------------

export interface ForecastPoint {
  label: string;
  balance: number;
  /** Tagesbewegung; steuert den Ereignispunkt. */
  net?: number;
  /** Mehrere grosse Faelligkeiten am selben Tag. */
  heavy?: boolean;
}

/** Ab dieser Tagesbewegung bekommt ein Punkt eine Markierung. */
const FORECAST_EVENT_MINOR = 25_000;

/**
 * Kontostandsverlauf: Flaechenlinie, Nulllinie, Sicherheitspuffer, getoente
 * Zone unter null, Ereignispunkte an Tagen mit spuerbarer Bewegung.
 *
 * Die Ereignispunkte liegen in einer eigenen Streuserie statt in `markPoint`:
 * markPoint kennt nur feste Koordinaten, waehrend die Punkte hier an der
 * Linie kleben muessen und einzeln eingefaerbt werden.
 */
export function forecastBalanceOption(
  points: ForecastPoint[],
  options: { bufferMinor: number; markers?: { index: number; label: string; warn?: boolean }[] },
): EChartsOption {
  const palette = readPalette();
  const base = baseOption(palette);

  // Ereignispunkte sitzen als Einzelsymbole auf derselben Linienserie statt in
  // einer eigenen Streuserie: ScatterChart ist in Chart.tsx bewusst nicht
  // registriert, und ein zweites Chart-Modul waere fuer ein paar Punkte zu
  // teuer. Nicht markierte Tage bekommen symbolSize 0.
  const data = points.map((p) => {
    const isEvent = p.heavy || Math.abs(p.net ?? 0) >= FORECAST_EVENT_MINOR;
    if (!isEvent) return { value: p.balance, symbolSize: 0 };
    const color = (p.net ?? 0) > 0 ? palette.positive : palette.warning;
    return {
      value: p.balance,
      symbolSize: p.heavy ? 11 : 7,
      itemStyle: p.heavy
        ? { color: "transparent", borderColor: color, borderWidth: 2 }
        : { color, borderColor: palette.surface, borderWidth: 1 },
    };
  });

  return {
    ...base,
    legend: { ...base.legend, show: false },
    tooltip: {
      ...base.tooltip,
      trigger: "axis",
      axisPointer: { type: "line" },
      valueFormatter: (value) => amountFormatter(Number(value)),
    },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: "category",
      data: points.map((p) => p.label),
      ...axisStyle(palette),
      splitLine: { show: false },
      axisLabel: { color: palette.textSecondary, fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      ...axisStyle(palette),
      axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
    },
    series: [
      {
        name: "Erwartet",
        type: "line",
        data,
        showSymbol: true,
        smooth: false,
        lineStyle: { width: 2, color: palette.positive },
        itemStyle: { color: palette.positive },
        areaStyle: { color: palette.positive, opacity: 0.12 },
        markLine: {
          silent: true,
          symbol: "none",
          label: { color: palette.textSecondary, fontSize: 11 },
          data: [
            { yAxis: 0, lineStyle: { color: palette.warning, width: 1.5 }, label: { show: false } },
            {
              yAxis: options.bufferMinor,
              lineStyle: { color: palette.highlight, width: 1, type: "dashed" },
              label: { formatter: "Puffer", position: "insideEndTop", color: palette.highlight },
            },
            ...(options.markers ?? []).map((m) => ({
              xAxis: m.index,
              lineStyle: {
                color: m.warn ? palette.warning : palette.highlight,
                width: 1,
                type: "dashed" as const,
              },
              label: {
                formatter: m.label,
                position: "insideStartTop" as const,
                color: m.warn ? palette.warning : palette.highlight,
                fontSize: 11,
              },
            })),
          ],
        },
        markArea: {
          silent: true,
          itemStyle: { color: palette.warning, opacity: 0.08 },
          data: [[{ yAxis: -Number.MAX_SAFE_INTEGER }, { yAxis: 0 }]],
        },
      },
    ],
  };
}

/** Basis gegen Simulation, wahlweise als zwei Linien oder als Differenzflaeche. */
export function forecastCompareOption(
  labels: string[],
  baseSeries: number[],
  simSeries: number[],
  mode: "balance" | "diff",
): EChartsOption {
  const palette = readPalette();
  const base = baseOption(palette);
  const shared = {
    ...base,
    tooltip: {
      ...base.tooltip,
      trigger: "axis" as const,
      valueFormatter: (value: unknown) => amountFormatter(Number(value)),
    },
    grid: { left: 8, right: 16, top: 28, bottom: 8, containLabel: true },
    xAxis: {
      type: "category" as const,
      data: labels,
      ...axisStyle(palette),
      splitLine: { show: false },
      axisLabel: { color: palette.textSecondary, fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: "value" as const,
      ...axisStyle(palette),
      axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
    },
  };

  if (mode === "diff") {
    return {
      ...shared,
      legend: { ...base.legend, show: false },
      series: [
        {
          name: "Vorsprung",
          type: "line",
          data: simSeries.map((v, i) => v - (baseSeries[i] ?? 0)),
          showSymbol: false,
          lineStyle: { width: 2, color: palette.positive },
          itemStyle: { color: palette.positive },
          areaStyle: { color: palette.positive, opacity: 0.16 },
        },
      ],
    };
  }

  return {
    ...shared,
    legend: { ...base.legend, data: ["Basis", "Simulation"] },
    series: [
      {
        name: "Basis",
        type: "line",
        data: baseSeries,
        showSymbol: false,
        lineStyle: { width: 1.5, color: palette.textSecondary, type: "dashed" },
        itemStyle: { color: palette.textSecondary },
      },
      {
        name: "Simulation",
        type: "line",
        data: simSeries,
        showSymbol: false,
        lineStyle: { width: 2, color: palette.positive },
        itemStyle: { color: palette.positive },
        areaStyle: { color: palette.positive, opacity: 0.12 },
      },
    ],
  };
}

/**
 * Basis und bis zu vier Szenarien in einem Diagramm (Plan Stufe 7).
 *
 * Die Basis ist gestrichelt und neutral gefaerbt, die Szenarien nehmen die
 * kategoriale Palette - sie unterscheiden Faelle, keinen Budgetstatus, und
 * duerfen deshalb nicht in Warn- oder Erfolgsfarben stehen.
 */
export function forecastMultiCompareOption(
  labels: string[],
  series: { name: string; values: number[]; dashed?: boolean }[],
  bufferMinor: number,
): EChartsOption {
  const palette = readPalette();
  const base = baseOption(palette);
  return {
    ...base,
    legend: { ...base.legend, data: series.map((s) => s.name) },
    tooltip: {
      ...base.tooltip,
      trigger: "axis",
      valueFormatter: (value) => amountFormatter(Number(value)),
    },
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      ...axisStyle(palette),
      splitLine: { show: false },
      axisLabel: { color: palette.textSecondary, fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      ...axisStyle(palette),
      axisLabel: { color: palette.textSecondary, fontSize: 12, formatter: formatAxisAmount },
    },
    series: series.map((entry, index) => ({
      name: entry.name,
      type: "line" as const,
      data: entry.values,
      showSymbol: false,
      lineStyle: entry.dashed
        ? { width: 1.5, color: palette.textSecondary, type: "dashed" as const }
        : { width: 2, color: CATEGORICAL[(index - 1) % CATEGORICAL.length] },
      itemStyle: {
        color: entry.dashed
          ? palette.textSecondary
          : CATEGORICAL[(index - 1) % CATEGORICAL.length],
      },
      ...(index === 0
        ? {
            markLine: {
              silent: true,
              symbol: "none",
              data: [
                { yAxis: 0, lineStyle: { color: palette.warning, width: 1.5 }, label: { show: false } },
                {
                  yAxis: bufferMinor,
                  lineStyle: { color: palette.highlight, width: 1, type: "dashed" as const },
                  label: { formatter: "Puffer", position: "insideEndTop" as const, color: palette.highlight },
                },
              ],
            },
          }
        : {}),
    })),
  };
}
