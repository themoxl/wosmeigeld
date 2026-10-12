// Mobile-Designsystem: Farbwelt aus docs/mobile_redesign_showcase.html.
//
// Warum eigene Palette statt @finanzplaner/design-tokens?
// Die Web-Tokens (gedecktes Waldgruen #1F6D4C auf hellem Papier, 2-6px Radien)
// sind auf eine Desktop-Arbeitsflaeche mit Maus und viel Platz gebaut. Der
// Showcase fuer die Handy-App ist bewusst das Gegenteil: dunkler Grund,
// leuchtende Statusfarben, grosse Radien, kraeftige Zahlen. Beides in EINEN
// Tokensatz zu pressen wuerde die Web-App mitverbiegen (siehe Erinnerung
// "Mobile kommt nach Web" - Web ist fachlich fertig und soll optisch stabil
// bleiben). Deshalb liegt die Handy-Palette hier, und nur hier.
//
// Aufbau je Akzentfarbe drei Stufen:
//   *Strong  - Flaechenfarbe (Fuellung, Button, Balken)
//   *        - Textfarbe auf dem jeweiligen Grund (kontraststark)
//   *Muted   - transparente Hinterlegung fuer Badges/Banner

/** Rohfarben des Showcase (Tailwind-nahe Skala). */
export const raw = {
  forest500: "#22C55E",
  forest400: "#4ADE80",
  forest600: "#16A34A",
  forest700: "#15803D",
  forest800: "#0F5C2E",
  coral500: "#EF4444",
  coral400: "#F87171",
  coral600: "#DC2626",
  coral700: "#B91C1C",
  amber500: "#F59E0B",
  amber400: "#FBBF24",
  amber600: "#D97706",
  amber700: "#B45309",
  blue500: "#3B82F6",
  blue400: "#60A5FA",
  blue600: "#2563EB",
  blue700: "#1D4ED8",
  purple500: "#A855F7",
  purple400: "#C084FC",
  purple600: "#9333EA",
  purple700: "#7E22CE",
  cyan500: "#06B6D4",
  cyan400: "#22D3EE",
  cyan600: "#0891B2",
  cyan700: "#0E7490",
} as const;

export interface MobileThemeTokens {
  // --- Flaechen -----------------------------------------------------------
  /** Seitengrund hinter allen Karten. */
  background: string;
  /** App-Chrom: Kopfzeile, Tableiste, Bottom-Sheet-Rand. */
  chrome: string;
  /** Standardkarte. */
  surface: string;
  /** Kachel INNERHALB einer Karte (Aufteilungsboxen, Ziffernblock). */
  surfaceRaised: string;
  /** Gedrueckter/aktiver Zustand einer Flaeche. */
  surfaceHover: string;
  /** Abdunklung hinter Bottom-Sheets. */
  overlay: string;

  // --- Linien -------------------------------------------------------------
  border: string;
  borderStrong: string;

  // --- Text ---------------------------------------------------------------
  textPrimary: string;
  textSecondary: string;
  /** Dritte Stufe: Fussnoten, "Danach: 2.435 €", Zeitstempel. */
  textMuted: string;
  /** Text auf gefuellten Markenflaechen (Buttons, FAB, aktive Pillen). */
  onAccent: string;

  // --- Marke / Aktion -----------------------------------------------------
  /** Aktionsfarbe (FAB, Primaerbutton, aktiver Tab). */
  primary: string;
  primaryStrong: string;
  /**
   * Traegerflaeche fuer WEISSEN Text: erster Gradientenstop von
   * FAB/Primaerbutton, Fuellung der gewaehlten Pille. Ueber `primaryStrong`
   * erreicht Weiss nur 2,3:1 (dunkel) bzw. 3,3:1 (hell) - deshalb liegt jede
   * Flaeche mit weisser Beschriftung hier und nicht auf `primaryStrong`.
   */
  primaryDeep: string;
  /** Zweiter, dunklerer Gradientenstop fuer FAB/Primaerbutton. */
  primaryDeeper: string;

  // --- Semantik -----------------------------------------------------------
  positive: string;
  positiveStrong: string;
  positiveMuted: string;

  warning: string;
  warningStrong: string;
  warningMuted: string;
  /**
   * Warnflaeche, auf der WEISSER Text 4,5:1 erreicht (Gefahrenknopf,
   * Wischaktion "Löschen"). `warningStrong` ist dafuer im dunklen Theme zu
   * hell (3,8:1).
   */
  warningDeep: string;
  /** Zweiter, dunklerer Gradientenstop des Gefahrenknopfs. */
  warningDeeper: string;

  highlight: string;
  highlightStrong: string;
  highlightMuted: string;
  /** Bernsteinflaeche mit weissem Text (Wischaktion "Kategorie"). */
  highlightDeep: string;

  info: string;
  infoStrong: string;
  infoMuted: string;

  purple: string;
  purpleStrong: string;
  purpleMuted: string;

  cyan: string;
  cyanStrong: string;
  cyanMuted: string;

  // --- Kompatibilitaet mit dem bisherigen Tokensatz ------------------------
  /** Alias auf `surface`; die Web-Tokens kannten beide Namen. */
  surfaceElevated: string;
  /** Alias auf `highlight` - im Bestand als "gelber" Zwischenzustand genutzt. */
  accent: string;
  focusRing: string;
}

export const darkTheme: MobileThemeTokens = {
  background: "#0B0D0E",
  chrome: "#141719",
  surface: "#1C2124",
  surfaceRaised: "#242B2F",
  surfaceHover: "#2B3338",
  overlay: "rgba(0, 0, 0, 0.72)",

  border: "rgba(255, 255, 255, 0.08)",
  borderStrong: "rgba(255, 255, 255, 0.16)",

  // Drei Textstufen mit echtem Abstand UND mindestens 4,5:1 auf jedem der
  // vier Gruende (background/chrome/surface/surfaceRaised). Die frueheren
  // Werte (#9AA5AC / #64717A) verfehlten das in der dritten Stufe deutlich
  // (2,9:1 auf surfaceRaised), obwohl dort echte Information steht.
  textPrimary: "#F0F3F4",
  textSecondary: "#B4BEC5",
  textMuted: "#8A96A0",
  onAccent: "#FFFFFF",

  primary: raw.forest500,
  primaryStrong: raw.forest500,
  primaryDeep: raw.forest700,
  primaryDeeper: raw.forest800,

  positive: raw.forest400,
  positiveStrong: raw.forest500,
  positiveMuted: "rgba(34, 197, 94, 0.15)",

  warning: raw.coral400,
  warningStrong: raw.coral500,
  warningMuted: "rgba(239, 68, 68, 0.15)",
  warningDeep: raw.coral600,
  warningDeeper: raw.coral700,

  highlight: raw.amber400,
  highlightStrong: raw.amber500,
  highlightMuted: "rgba(245, 158, 11, 0.15)",
  highlightDeep: raw.amber700,

  info: raw.blue400,
  infoStrong: raw.blue500,
  infoMuted: "rgba(59, 130, 246, 0.15)",

  purple: raw.purple400,
  purpleStrong: raw.purple500,
  purpleMuted: "rgba(168, 85, 247, 0.15)",

  cyan: raw.cyan400,
  cyanStrong: raw.cyan500,
  cyanMuted: "rgba(6, 182, 212, 0.15)",

  surfaceElevated: "#1C2124",
  accent: raw.amber400,
  focusRing: raw.forest500,
};

// Hellmodus: der Showcase setzt Karten (#f8f9fa) auf einen grauen Grund
// (#f1f3f5). Auf einem Handydisplay bei Sonnenlicht traegt dieser Unterschied
// nicht - deshalb hier weisse Karten auf grauem Grund und eine leicht graue
// Innenkachel. Die Rollenverteilung (Grund < Karte < Innenkachel unterscheidbar)
// bleibt damit erhalten, nur die Richtung dreht sich gegenueber Dunkel um.
export const lightTheme: MobileThemeTokens = {
  background: "#F1F3F5",
  chrome: "#FFFFFF",
  surface: "#FFFFFF",
  surfaceRaised: "#F3F5F7",
  surfaceHover: "#E7EBEE",
  overlay: "rgba(15, 23, 28, 0.45)",

  border: "rgba(0, 0, 0, 0.09)",
  borderStrong: "rgba(0, 0, 0, 0.16)",

  textPrimary: "#1A2024",
  textSecondary: "#46545C",
  textMuted: "#5F6E77",
  onAccent: "#FFFFFF",

  primary: raw.forest600,
  primaryStrong: raw.forest600,
  primaryDeep: raw.forest700,
  primaryDeeper: raw.forest800,

  positive: raw.forest700,
  positiveStrong: raw.forest600,
  positiveMuted: "rgba(22, 163, 74, 0.12)",

  warning: raw.coral700,
  warningStrong: raw.coral600,
  warningMuted: "rgba(220, 38, 38, 0.12)",
  warningDeep: raw.coral600,
  warningDeeper: raw.coral700,

  highlight: raw.amber700,
  highlightStrong: raw.amber600,
  highlightMuted: "rgba(217, 119, 6, 0.12)",
  highlightDeep: raw.amber700,

  info: raw.blue700,
  infoStrong: raw.blue600,
  infoMuted: "rgba(37, 99, 235, 0.12)",

  purple: raw.purple700,
  purpleStrong: raw.purple600,
  purpleMuted: "rgba(147, 51, 234, 0.12)",

  cyan: raw.cyan700,
  cyanStrong: raw.cyan600,
  cyanMuted: "rgba(8, 145, 178, 0.12)",

  surfaceElevated: "#FFFFFF",
  accent: raw.amber700,
  focusRing: raw.forest600,
};

/**
 * Farbtoene fuer Badges, Kategorie-Punkte und Merchant-Avatare. Bewusst von
 * den Statusfarben getrennt benannt, damit eine Kategoriefarbe nie als
 * "gut/schlecht" gelesen wird (gleiche Trennung wie chartCategorical im Web).
 */
export type AccentTone =
  "neutral" | "positive" | "warning" | "highlight" | "info" | "purple" | "cyan";

export const ACCENT_TONES: readonly AccentTone[] = [
  "info",
  "positive",
  "highlight",
  "purple",
  "cyan",
  "warning",
] as const;

export function toneColors(
  theme: MobileThemeTokens,
  tone: AccentTone,
): { text: string; fill: string; muted: string } {
  switch (tone) {
    case "positive":
      return { text: theme.positive, fill: theme.positiveStrong, muted: theme.positiveMuted };
    case "warning":
      return { text: theme.warning, fill: theme.warningStrong, muted: theme.warningMuted };
    case "highlight":
      return { text: theme.highlight, fill: theme.highlightStrong, muted: theme.highlightMuted };
    case "info":
      return { text: theme.info, fill: theme.infoStrong, muted: theme.infoMuted };
    case "purple":
      return { text: theme.purple, fill: theme.purpleStrong, muted: theme.purpleMuted };
    case "cyan":
      return { text: theme.cyan, fill: theme.cyanStrong, muted: theme.cyanMuted };
    case "neutral":
    default:
      return { text: theme.textSecondary, fill: theme.textMuted, muted: theme.surfaceRaised };
  }
}

/**
 * Stabile Farbe aus einem Namen (Haendler, Kategorie, Konto). Gleicher Name
 * ergibt immer denselben Ton - so bekommt "Rewe" in jeder Liste dieselbe
 * Kachelfarbe, ohne dass irgendwo eine Farbe gespeichert werden muss.
 */
export function toneForLabel(label: string): AccentTone {
  let hash = 0;
  for (let index = 0; index < label.length; index += 1) {
    hash = (hash * 31 + label.charCodeAt(index)) % 100_000;
  }
  return ACCENT_TONES[hash % ACCENT_TONES.length] ?? "info";
}

/** Erste ein bis zwei Zeichen eines Namens fuer die Avatar-Kachel. */
export function initialsForLabel(label: string): string {
  const trimmed = label.trim();
  if (!trimmed) return "?";
  const words = trimmed.split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0]?.[0] ?? ""}${words[1]?.[0] ?? ""}`.toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase();
}
