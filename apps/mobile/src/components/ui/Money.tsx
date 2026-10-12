import { StyleSheet, Text, type StyleProp, type TextStyle } from "react-native";
import { fontFamily, typeScale, useTheme, usePrivacy, MASKED_AMOUNT } from "../../theme";
import { formatMinorUnits, formatMinorUnitsCompact } from "../../utils/money";

type Size = "row" | "medium" | "display" | "hero";

type Props = {
  /** Betrag in Cent. */
  minorUnits: number;
  currency?: string;
  size?: Size;
  /**
   * "auto" faerbt Einnahmen gruen und laesst Ausgaben in der Textfarbe -
   * genau wie im Showcase, wo nicht jede Ausgabe rot schreit, sondern nur
   * echte Warnungen. "positive"/"warning"/"plain" erzwingen eine Farbe.
   */
  tone?: "auto" | "plain" | "positive" | "warning" | "muted";
  /** Ueberschreibt den globalen Privat-Modus (Bildschirme reichen ihn durch). */
  isPrivacyMode?: boolean;
  /** Kurzform fuer enge Stellen ("28,5k €"). */
  compact?: boolean;
  /** Zeigt bei positiven Werten ein "+" (Einnahmen, Deltas). */
  showPlusSign?: boolean;
  style?: StyleProp<TextStyle>;
  numberOfLines?: number;
  testID?: string;
};

const SIZES: Record<Size, TextStyle> = {
  row: { fontSize: typeScale.medium, fontWeight: "700" },
  medium: { fontSize: typeScale.large, fontWeight: "700" },
  display: { fontSize: typeScale.display, fontWeight: "800" },
  hero: { fontSize: typeScale.hero, fontWeight: "800", letterSpacing: -0.8 },
};

/**
 * Jeder Geldbetrag der App laeuft hier durch: gleiche Schrift (JetBrains
 * Mono), gleiche Rundung, gleiche Vorzeichenlogik - und der Privat-Modus
 * greift automatisch, statt an jeder Fundstelle einzeln.
 *
 * Monospace ist bei Betraegen kein Selbstzweck: nur so stehen die
 * Kommastellen mehrerer Zeilen untereinander und lassen sich vergleichen,
 * ohne sie zu lesen.
 */
export function Money({
  minorUnits,
  currency = "EUR",
  size = "row",
  tone = "auto",
  isPrivacyMode,
  compact = false,
  showPlusSign = false,
  style,
  numberOfLines,
  testID,
}: Props) {
  const theme = useTheme();
  const privacy = usePrivacy();
  const masked = isPrivacyMode ?? privacy.isPrivacyMode;

  const formatted = compact
    ? formatMinorUnitsCompact(minorUnits, currency)
    : formatMinorUnits(minorUnits, currency);
  const withSign = showPlusSign && minorUnits > 0 ? `+${formatted}` : formatted;

  const color =
    tone === "positive"
      ? theme.positive
      : tone === "warning"
        ? theme.warning
        : tone === "muted"
          ? theme.textSecondary
          : tone === "plain"
            ? theme.textPrimary
            : minorUnits > 0
              ? theme.positive
              : theme.textPrimary;

  return (
    <Text
      testID={testID}
      numberOfLines={numberOfLines}
      style={[styles.base, SIZES[size], { color }, style]}
    >
      {masked ? MASKED_AMOUNT : withSign}
    </Text>
  );
}

const styles = StyleSheet.create({
  base: {
    fontFamily: fontFamily.mono,
  },
});

/** Monospace-Textstil fuer Zahlen, die nicht durch <Money> laufen (Prozente, Daten). */
export const monoTextStyle: TextStyle = { fontFamily: fontFamily.mono };
