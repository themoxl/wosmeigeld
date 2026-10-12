import { Platform, type TextStyle } from "react-native";
import { fontFamily as webFontFamily } from "@finanzplaner/design-tokens";

// Schriften des Handy-Designsystems (docs/mobile_redesign_showcase.html):
// Inter fuer die Oberflaeche, JetBrains Mono fuer jeden Geldbetrag.
//
// React Native kennt keine Schriftfamilie mit mehreren Schnitten: jede Datei
// ist eine eigene Familie ("Inter_600SemiBold"). Damit im ganzen Code
// weiterhin `fontFamily: fontFamily.mono` + `fontWeight: "700"` geschrieben
// werden kann, sind fontFamily.ui/.mono nur SCHLUESSELWOERTER. Aufgeloest
// werden sie zentral in resolveFontStyle() - beim Rendern jedes <Text> durch
// den Aufsatz in globalFont.tsx.

/** Schluesselwort fuer die Oberflaechenschrift (Inter). */
export const FONT_UI = "app-ui";
/** Schluesselwort fuer die Geldschrift (JetBrains Mono). */
export const FONT_MONO = "app-mono";

export const fontFamily = {
  ui: FONT_UI,
  heading: FONT_UI,
  mono: FONT_MONO,
} as const;

/** Schnitte, die tatsaechlich geladen werden. Alles andere rundet ab. */
const UI_BY_WEIGHT: Record<string, string> = {
  "100": "Inter_400Regular",
  "200": "Inter_400Regular",
  "300": "Inter_400Regular",
  "400": "Inter_400Regular",
  "500": "Inter_500Medium",
  "600": "Inter_600SemiBold",
  "700": "Inter_700Bold",
  "800": "Inter_800ExtraBold",
  "900": "Inter_800ExtraBold",
  normal: "Inter_400Regular",
  bold: "Inter_700Bold",
};

const MONO_BY_WEIGHT: Record<string, string> = {
  "100": "JetBrainsMono_400Regular",
  "200": "JetBrainsMono_400Regular",
  "300": "JetBrainsMono_400Regular",
  "400": "JetBrainsMono_400Regular",
  "500": "JetBrainsMono_500Medium",
  "600": "JetBrainsMono_700Bold",
  "700": "JetBrainsMono_700Bold",
  "800": "JetBrainsMono_800ExtraBold",
  "900": "JetBrainsMono_800ExtraBold",
  normal: "JetBrainsMono_400Regular",
  bold: "JetBrainsMono_700Bold",
};

const SYSTEM_MONO = Platform.select({
  ios: "Menlo",
  android: "monospace",
  default: "monospace",
});

let fontsReady = false;

/** Wird von useAppFonts() gesetzt, sobald expo-font die Dateien geladen hat. */
export function setFontsReady(ready: boolean): void {
  fontsReady = ready;
}

export function areFontsReady(): boolean {
  return fontsReady;
}

/**
 * Uebersetzt (Schluesselwort, Schriftstaerke) in eine echte, geladene
 * Schriftfamilie.
 *
 * Die Staerke wird dabei ENTFERNT: der Schnitt steckt bereits in der Datei,
 * und ein zusaetzliches fontWeight laesst Android denselben Schnitt ein
 * zweites Mal kuenstlich fetten ("faux bold"), was besonders bei Betraegen
 * sofort auffaellt.
 *
 * Solange die Schriften noch nicht geladen sind (erster Frame, Tests, Web
 * ohne Netz), bleibt die Systemschrift stehen - dann muss die Staerke
 * erhalten bleiben, damit fette Zahlen fett aussehen.
 */
export function resolveFontStyle(
  family: string | undefined,
  fontWeight: TextStyle["fontWeight"] | undefined,
): { fontFamily?: string; fontWeight?: TextStyle["fontWeight"] } | null {
  const isUi = family === FONT_UI || family === undefined || family === null;
  const isMono = family === FONT_MONO;

  if (!isUi && !isMono) {
    // Fremde Familie (z. B. ein Icon-Font) unveraendert durchreichen.
    return null;
  }

  if (!fontsReady) {
    if (isMono) return { fontFamily: SYSTEM_MONO, fontWeight };
    return family === undefined || family === null ? null : { fontFamily: undefined, fontWeight };
  }

  const key = fontWeight == null ? "400" : String(fontWeight);
  const table = isMono ? MONO_BY_WEIGHT : UI_BY_WEIGHT;
  const resolved = table[key] ?? table["400"];
  return { fontFamily: resolved, fontWeight: undefined };
}

/** Nur noch zur Dokumentation: der CSS-Stack, den die Web-App verwendet. */
export const webFontFamilyReference = webFontFamily;
