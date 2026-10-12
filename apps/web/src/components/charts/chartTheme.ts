import type { EChartsOption } from "echarts";

// Diagramm-Theme aus den Design-Tokens, nicht aus ECharts-Defaults
// (docs/ANALYSE-EXPLORER-PLAN.md, D.1).
//
// Seit der Designwahl stehen hier keine Farbwerte mehr, sondern Verweise auf
// die CSS-Variablen des geltenden Designs ("var(--color-...)"). Chart.tsx
// loest sie unmittelbar vor setOption auf und noch einmal nach jedem
// Designwechsel - so zeichnet auch eine gemerkte (useMemo) Option nie in
// den Farben des vorigen Designs. Die Werte selbst stehen in global.css:
// fuer Klassisch die Paletten aus @finanzplaner/design-tokens, fuer die
// Kasten-Designs deren Rollen. Kategorienfarben bleiben bewusst unabhaengig
// von den Warn-/Erfolgsfarben, damit Kategorien in Berichten nicht mit
// Budgetstatus verwechselt werden.

const ref = (name: string) => `var(${name})`;

export const CATEGORICAL = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => ref(`--color-chart-${n}`));
export const SEQUENTIAL = [1, 2, 3, 4, 5, 6].map((n) => ref(`--color-chart-seq-${n}`));
export const DIVERGING = [1, 2, 3, 4, 5].map((n) => ref(`--color-chart-div-${n}`));

export interface ChartPalette {
  text: string;
  textSecondary: string;
  border: string;
  surface: string;
  positive: string;
  warning: string;
  highlight: string;
}

export function readPalette(): ChartPalette {
  return {
    text: ref("--color-text-primary"),
    textSecondary: ref("--color-text-secondary"),
    border: ref("--color-border"),
    surface: ref("--color-surface"),
    positive: ref("--color-positive"),
    warning: ref("--color-warning"),
    highlight: ref("--color-highlight"),
  };
}

const VAR_REF = /^var\((--[\w-]+)\)$/;

/**
 * Ersetzt jeden Verweis "var(--x)" in einer Diagrammoption durch den Wert der
 * Variable am <html>. Canvas kennt keine CSS-Variablen; ECharts braucht den
 * fertigen Farbwert. Funktionen (Formatter) und andere Werte bleiben, wie sie
 * sind. Laesst sich eine Variable nicht lesen (jsdom), bleibt der Verweis
 * stehen - gezeichnet wird dort ohnehin nicht.
 */
export function resolveCssVars<T>(option: T, root: Element = document.documentElement): T {
  const style = getComputedStyle(root);
  const cache = new Map<string, string>();
  const resolve = (value: unknown): unknown => {
    if (typeof value === "string") {
      const name = VAR_REF.exec(value)?.[1];
      if (!name) return value;
      let resolved = cache.get(name);
      if (resolved === undefined) {
        resolved = style.getPropertyValue(name).trim() || value;
        cache.set(name, resolved);
      }
      return resolved;
    }
    if (Array.isArray(value)) return value.map(resolve);
    if (value !== null && typeof value === "object") {
      const proto = Object.getPrototypeOf(value);
      if (proto !== Object.prototype && proto !== null) return value;
      const copy: Record<string, unknown> = {};
      for (const [key, inner] of Object.entries(value)) copy[key] = resolve(inner);
      return copy;
    }
    return value;
  };
  return resolve(option) as T;
}

/** Kurzform fuer Achsenbeschriftungen: 1.200.000 -> "1,2 Mio". */
export function formatAxisAmount(minorUnits: number): string {
  const euro = minorUnits / 100;
  const abs = Math.abs(euro);
  if (abs >= 1_000_000) return `${(euro / 1_000_000).toFixed(1).replace(".", ",")} Mio`;
  if (abs >= 1_000) return `${Math.round(euro / 1_000)} Tsd`;
  return euro.toLocaleString("de-DE", { maximumFractionDigits: 0 });
}

export function formatTooltipAmount(minorUnits: number): string {
  return `${(minorUnits / 100).toLocaleString("de-DE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} €`;
}

/**
 * Grundgeruest, das jedes Diagramm erbt. Ohne diese gemeinsame Basis driften
 * Schriftfarbe, Rasterabstand und Tooltip-Verhalten zwischen den Diagrammen
 * auseinander, sobald es mehr als zwei gibt.
 */
export function baseOption(palette: ChartPalette): EChartsOption {
  return {
    color: CATEGORICAL,
    textStyle: {
      fontFamily: 'Inter, -apple-system, "Segoe UI", Roboto, Arial, sans-serif',
      color: palette.text,
    },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: palette.surface,
      borderColor: palette.border,
      borderWidth: 1,
      textStyle: { color: palette.text, fontSize: 13 },
      // confine haelt den Tooltip im Diagramm - sonst wird er am rechten
      // Rand einer schmalen Karte abgeschnitten.
      confine: true,
    },
    legend: {
      type: "scroll",
      textStyle: { color: palette.textSecondary, fontSize: 12 },
      icon: "roundRect",
      itemWidth: 10,
      itemHeight: 10,
    },
  };
}

export function axisStyle(palette: ChartPalette) {
  return {
    axisLine: { lineStyle: { color: palette.border } },
    axisTick: { show: false },
    axisLabel: { color: palette.textSecondary, fontSize: 12 },
    splitLine: { lineStyle: { color: palette.border, type: "dashed" as const } },
  };
}
