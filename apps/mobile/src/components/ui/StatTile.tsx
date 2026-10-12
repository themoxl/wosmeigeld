import type { ReactNode } from "react";
import { StyleSheet, Text, View } from "react-native";
import { radii, space, toneColors, typeScale, useTheme, type AccentTone } from "../../theme";

type Props = {
  label: string;
  /** Grosse Zahl - meist ein <Money>. */
  value: ReactNode;
  /** Erklaerung darunter, z. B. "Miete, Strom, DSL, Abos". */
  caption?: string;
  /** Faerbt den Streifen links; ohne Angabe bleibt die Kachel neutral. */
  tone?: AccentTone;
};

/**
 * Innenkachel einer Karte (Showcase: die beiden Boxen "Fixkosten"/"Variable
 * Kosten" unter dem Aufteilungsbalken).
 *
 * Der farbige Streifen links wiederholt die Farbe aus dem Balken darueber -
 * so muss man Balkenabschnitt und Zahl nicht ueber die Legende verbinden.
 */
export function StatTile({ label, value, caption, tone }: Props) {
  const theme = useTheme();
  const colors = tone ? toneColors(theme, tone) : null;

  return (
    <View
      style={[
        styles.tile,
        {
          backgroundColor: theme.surfaceRaised,
          borderLeftColor: colors ? colors.fill : theme.border,
        },
      ]}
    >
      <Text style={[styles.label, { color: theme.textSecondary }]} numberOfLines={1}>
        {label}
      </Text>
      {value}
      {caption ? (
        <Text style={[styles.caption, { color: theme.textMuted }]} numberOfLines={2}>
          {caption}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  tile: {
    flex: 1,
    minWidth: 0,
    padding: space.md,
    borderRadius: radii.sm,
    borderLeftWidth: 3,
    gap: space.hair,
  },
  label: {
    fontSize: typeScale.micro,
    fontWeight: "700",
  },
  caption: {
    fontSize: typeScale.nano,
  },
});
