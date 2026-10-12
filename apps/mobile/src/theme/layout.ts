import { Platform, type ViewStyle } from "react-native";

// Masse des Handy-Designsystems (docs/mobile_redesign_showcase.html).
//
// Reine Zahlen statt CSS-Strings: React Native rechnet in
// dichteunabhaengigen Punkten ohne Einheit. Ein Umrechnungshelfer fuer die
// CSS-px-Strings der Web-Tokens wird hier deshalb nicht mehr gebraucht.

/** 4er-Raster. */
export const space = {
  /** 2 - nur fuer Haarabstaende innerhalb einer Zeile. */
  hair: 2,
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
} as const;

/**
 * Grosse Radien sind das auffaelligste Merkmal des Showcase: Karten 20,
 * Innenkacheln/Buttons 14, Bottom-Sheets 28.
 */
export const radii = {
  xs: 6,
  sm: 8,
  md: 14,
  lg: 20,
  xl: 28,
  pill: 999,
} as const;

/**
 * Schriftgroessen. Die Namen folgen der Rolle, nicht der Groesse, damit ein
 * spaeterer Dynamic-Type-Faktor an einer Stelle greifen kann.
 */
export const typeScale = {
  /** 10 - Kleinstlabel INNERHALB eines Bausteins (Kachel-Fussnote,
   * Ringmitte, Schwellenmarke, kleine Badge). Nie fuer Fliesstext. */
  nano: 10,
  /** 11 - Grossbuchstaben-Kartenlabel, Fussnoten. */
  micro: 11,
  /** 12 - Zweitzeile einer Listenzeile, Legenden. */
  caption: 12,
  /** 13 - Fliesstext in Karten. */
  body: 13,
  /** 14 - Listentitel, Standardtext. */
  base: 14,
  /** 15 - Betraege in Listen, Kartenueberschriften. */
  medium: 15,
  /** 18 - Sheet-Titel, Abschnittsueberschriften. */
  large: 18,
  /** 22 - Bildschirmtitel. */
  title: 22,
  /** 26 - grosse Kennzahl in einer Nebenkarte. */
  display: 26,
  /** 32 - Leitzahl der Hero-Karte. */
  hero: 32,
} as const;

/**
 * Zeilenabstaende. Ohne diesen Token liefen die Faktoren auseinander (1.4,
 * 1.45, 1.5 und feste Pixelwerte fuer dieselbe Schriftgroesse), und derselbe
 * Fliesstext hatte je nach Bildschirm eine andere Dichte.
 *
 * `tight` fuer einzeilige bis zweizeilige Beschriftungen, `normal` fuer
 * echten Fliesstext (Hinweissaetze, Erklaerungen).
 */
export const lineHeightFactor = {
  tight: 1.3,
  normal: 1.45,
} as const;

/**
 * Zeilenhoehe zu einer Schriftgroesse. Gerundet, weil React Native mit
 * gebrochenen Zeilenhoehen je Plattform unterschiedlich umgeht.
 */
export function leading(size: number, density: keyof typeof lineHeightFactor = "normal"): number {
  return Math.round(size * lineHeightFactor[density]);
}

export const weight = {
  regular: "400",
  medium: "500",
  semibold: "600",
  bold: "700",
  black: "800",
} as const;

/** Grossbuchstaben-Label: 11px, 700, weit gesperrt. */
export const letterSpacingLabel = 0.7;

/**
 * Schatten. iOS nutzt shadow*, Android elevation - beide muessen gesetzt sein,
 * sonst sieht die Karte auf genau einer Plattform flach aus.
 */
function shadow(opacity: number, radius: number, offsetY: number, elevation: number): ViewStyle {
  return Platform.select<ViewStyle>({
    android: { elevation },
    default: {
      // Kein Palettenwert: Schwarz ist hier keine Farbe, sondern die
      // Abdunklung selbst - der sichtbare Ton entsteht erst aus
      // shadowOpacity ueber dem jeweiligen Grund und ist in Hell wie Dunkel
      // derselbe. Ein Theme-Token waere in beiden Modi identisch.
      shadowColor: "#000000",
      shadowOpacity: opacity,
      shadowRadius: radius,
      shadowOffset: { width: 0, height: offsetY },
    },
  }) as ViewStyle;
}

export const elevation = {
  /** Karten. */
  card: shadow(0.16, 12, 4, 2),
  /** Bottom-Sheets und Ueberlagerungen. */
  sheet: shadow(0.32, 24, -6, 16),
  /** FAB. */
  floating: shadow(0.3, 16, 8, 8),
} as const;

/**
 * Tippziel-Mindestgroesse (Material/HIG: 44-48dp). Wird von IconButton,
 * Tab-Eintraegen und Listenzeilen als minHeight verwendet.
 */
export const HIT_SLOP = { top: 8, bottom: 8, left: 8, right: 8 } as const;
export const MIN_TOUCH = 44;
