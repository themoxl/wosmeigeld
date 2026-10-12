// Siehe FINANZ_APP_ARCHITEKTURPLAN.md Abschnitt 10.1 (Designrichtung):
// neutrale helle Arbeitsflaeche als Standard, optionales dunkles Carbon-Theme,
// Waldgruen fuer positive Finanzzustaende, Koralle fuer Warnungen/Ausgaben,
// Gold nur fuer Ziele und ausgewaehlte Highlights.

export const brand = {
  forest: {
    50: "#EAF3EE",
    100: "#CBE3D5",
    300: "#5C9C7C",
    500: "#1F6D4C",
    600: "#19583E",
    700: "#164F37",
  },
  coral: {
    50: "#FCEEEC",
    100: "#F7D3CC",
    300: "#EF9484",
    500: "#E8604C",
    600: "#C94A38",
    700: "#A83B2C",
  },
  gold: {
    50: "#FBF4E4",
    100: "#F1DFB2",
    300: "#DFC07E",
    500: "#C9A34E",
    600: "#A9853A",
  },
} as const;

export const neutral = {
  0: "#FFFFFF",
  50: "#F7F7F5",
  100: "#EFEFEC",
  200: "#E4E4E1",
  300: "#D0D0CB",
  400: "#A6A6A0",
  500: "#767671",
  600: "#54544F",
  700: "#3A3A36",
  800: "#232320",
  900: "#161614",
} as const;

export const carbon = {
  background: "#121214",
  surface: "#1B1B1E",
  surfaceRaised: "#232326",
  border: "rgba(255, 255, 255, 0.08)",
  textPrimary: "#EDEDEC",
  textSecondary: "#A0A09B",
} as const;

// Semantische Tokens
export interface ThemeTokens {
  background: string;
  surface: string;
  surfaceRaised: string;
  border: string;
  textPrimary: string;
  textSecondary: string;
  positive: string;
  positiveMuted: string;
  warning: string;
  warningMuted: string;
  /**
   * Koralle als TEXTfarbe. `warning` ist als Flaechenfarbe gebaut und erreicht
   * als Text auf hellem Grund nur rund 3:1 - zu wenig fuer WCAG AA. Dieses
   * Token ist der dunklere Zwilling fuer Beschriftungen, Zaehler und
   * destruktive Textaktionen (5,6:1 auf warningMuted, 5,9:1 auf surface).
   */
  warningStrong: string;
  highlight: string;
  highlightMuted: string;
  focusRing: string;
  /**
   * Bedienfarbe: Hauptknopf, aktiver Reiter, Auswahl. Bewusst getrennt von
   * `positive` (Geldfarbe): in den Designs der Designwahl ist der Akzent z. B.
   * Abendrot, waehrend Einnahmen gruen bleiben. In Klassisch sind beide gleich.
   */
  accent: string;
  accentMuted: string;
  /** Schrift auf einer Akzentflaeche. */
  onAccent: string;
  /** Zweite Zierfarbe (Gruppenkuerzel, Markenschild, KI-Karten). */
  accentSecondary: string;
  /** Schleier hinter Dialogen. */
  overlay: string;
  /** Neutrale Information (z. B. "in Bearbeitung"). */
  info: string;
}

export const lightTheme: ThemeTokens = {
  background: neutral[50],
  surface: neutral[0],
  surfaceRaised: neutral[0],
  border: neutral[200],
  textPrimary: neutral[900],
  textSecondary: neutral[600],
  positive: brand.forest[500],
  positiveMuted: brand.forest[50],
  warning: brand.coral[500],
  warningMuted: brand.coral[50],
  warningStrong: brand.coral[700],
  highlight: brand.gold[500],
  highlightMuted: brand.gold[50],
  focusRing: brand.forest[500],
  accent: brand.forest[500],
  accentMuted: brand.forest[50],
  onAccent: neutral[0],
  accentSecondary: brand.gold[500],
  overlay: "rgba(22, 22, 20, 0.45)",
  info: "#2F6F9E",
};

export const darkTheme: ThemeTokens = {
  background: carbon.background,
  surface: carbon.surface,
  surfaceRaised: carbon.surfaceRaised,
  border: carbon.border,
  textPrimary: carbon.textPrimary,
  textSecondary: carbon.textSecondary,
  positive: brand.forest[300],
  positiveMuted: "#1A2B22",
  warning: brand.coral[300],
  warningMuted: "#2E1D18",
  warningStrong: brand.coral[300],
  highlight: brand.gold[300],
  highlightMuted: "#2E2717",
  focusRing: brand.forest[300],
  accent: brand.forest[300],
  accentMuted: "#1A2B22",
  onAccent: carbon.surface,
  accentSecondary: brand.gold[300],
  overlay: "rgba(22, 22, 20, 0.45)",
  info: "#2F6F9E",
};

// Kategoriale Diagrammfarben (Apache ECharts). Bewusst neutral gehalten und
// unabhaengig von Warn-/Erfolgsfarben, damit Kategorien in Berichten nicht
// mit Budgetstatus verwechselt werden.
export const chartCategorical = [
  "#1F6D4C",
  "#2F6F9E",
  "#C9A34E",
  "#8B5CA8",
  "#E8604C",
  "#3B9C9C",
  "#B0763F",
  "#6B7280",
] as const;

export const chartSequential = [
  "#EAF3EE",
  "#CBE3D5",
  "#8FC1A5",
  "#5C9C7C",
  "#1F6D4C",
  "#164F37",
] as const;

export const chartDiverging = [
  brand.coral[600],
  brand.coral[300],
  neutral[200],
  brand.forest[300],
  brand.forest[600],
] as const;
