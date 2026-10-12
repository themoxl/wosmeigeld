import {
  darkTheme,
  fontFamily,
  lightTheme,
  radius,
  spacing,
  type ThemeTokens,
} from "@finanzplaner/design-tokens";

// Abschnitt 10.1 des Architekturplans: neutrale helle Arbeitsflaeche als
// Standard, optionales dunkles Carbon-Theme - dazu seit der Designwahl die
// Designs aus dem Designkasten (Dämmerung, OLED, ...).
//
// Farben setzt seitdem allein CSS (global.css, Abschnitt "Designs"): ein
// Designwechsel ist nur ein Attribut am <html>, nichts muss neu gerechnet
// werden. Die Werte der eigenen Designs sind die Tokens aus
// @finanzplaner/design-tokens; src/test/designs.test.ts prueft, dass
// global.css sie wortgleich traegt. Diese Datei setzt nur noch die
// farbunabhaengigen Tokens (Schriften, Abstaende, Radien).

/** Die eigenen Designs und die Tokens, aus denen ihre CSS-Bloecke bestehen. */
export const OWN_DESIGN_TOKENS: Record<"klassisch" | "klassisch-dunkel", ThemeTokens> = {
  klassisch: lightTheme,
  "klassisch-dunkel": darkTheme,
};

export function applyLayoutTokens(root: HTMLElement): void {
  root.style.setProperty("--font-ui", fontFamily.ui);
  root.style.setProperty("--font-mono", fontFamily.mono);
  root.style.setProperty("--font-heading", fontFamily.heading);

  Object.entries(spacing).forEach(([key, value]) => {
    root.style.setProperty(`--space-${key}`, value);
  });

  Object.entries(radius).forEach(([key, value]) => {
    root.style.setProperty(`--radius-${key}`, value);
  });
}
