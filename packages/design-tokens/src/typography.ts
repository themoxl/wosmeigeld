// Abschnitt 10.1: Inter fuer Oberflaeche, JetBrains Mono fuer Geldwerte und
// Tabellen, Newsreader sparsam fuer Abschnittsueberschriften.

export const fontFamily = {
  ui: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  mono: '"JetBrains Mono", "SFMono-Regular", Consolas, monospace',
  heading: '"Newsreader", Georgia, serif',
} as const;

export const fontSize = {
  xs: "12px",
  sm: "13px",
  base: "14px",
  md: "15px",
  lg: "18px",
  xl: "22px",
  xxl: "28px",
} as const;

export const fontWeight = {
  regular: 400,
  medium: 500,
  semibold: 600,
} as const;

export const lineHeight = {
  tight: 1.2,
  normal: 1.45,
  relaxed: 1.6,
} as const;
