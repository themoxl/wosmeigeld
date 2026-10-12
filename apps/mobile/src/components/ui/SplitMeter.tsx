import { StyleSheet, Text, View } from "react-native";
import { radii, space, toneColors, typeScale, useTheme, type AccentTone } from "../../theme";

export type SplitSegment = {
  /** Beliebige Groesse in derselben Einheit wie die anderen Abschnitte. */
  value: number;
  tone: AccentTone;
  label: string;
  /** Zusatz in der Legende, z. B. "1.450,00 €". */
  detail?: string;
};

type Props = {
  segments: SplitSegment[];
  height?: number;
  /** Legende unter dem Balken anzeigen. */
  showLegend?: boolean;
  accessibilityLabel?: string;
  testID?: string;
};

/**
 * Anteilsbalken fuer Fix/Variabel, 50-30-20 und die Vermoegensaufteilung.
 *
 * Statt eines Kuchendiagramms: auf Handybreite lassen sich drei bis vier
 * Anteile in einem 12px-Streifen genauer ablesen als in einem Kreis, und die
 * Legende darunter nennt Zahl und Farbe zusammen.
 */
export function SplitMeter({
  segments,
  height = 12,
  showLegend = true,
  accessibilityLabel,
  testID,
}: Props) {
  const theme = useTheme();
  const total = segments.reduce((sum, segment) => sum + Math.max(0, segment.value), 0);

  if (total <= 0) {
    return (
      <View
        testID={testID}
        style={[
          styles.track,
          { height, borderRadius: height / 2, backgroundColor: theme.surfaceRaised },
        ]}
      />
    );
  }

  const withShare = segments.map((segment) => ({
    ...segment,
    share: Math.max(0, segment.value) / total,
  }));

  return (
    <View style={styles.wrapper} testID={testID}>
      <View
        accessible
        accessibilityLabel={
          accessibilityLabel ??
          withShare.map((s) => `${s.label} ${Math.round(s.share * 100)} Prozent`).join(", ")
        }
        style={[styles.track, { height, borderRadius: height / 2 }]}
      >
        {withShare.map((segment, index) => (
          <View
            key={`${segment.label}-${index}`}
            style={{
              flex: segment.share,
              backgroundColor: toneColors(theme, segment.tone).fill,
              marginRight: index === withShare.length - 1 ? 0 : 2,
            }}
          />
        ))}
      </View>

      {showLegend ? (
        <View style={styles.legend}>
          {withShare.map((segment, index) => (
            <View key={`legend-${segment.label}-${index}`} style={styles.legendItem}>
              <View
                style={[styles.dot, { backgroundColor: toneColors(theme, segment.tone).fill }]}
              />
              <Text style={[styles.legendText, { color: theme.textSecondary }]} numberOfLines={1}>
                {segment.label} {Math.round(segment.share * 100)}%
                {segment.detail ? ` · ${segment.detail}` : ""}
              </Text>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    gap: space.sm,
  },
  track: {
    width: "100%",
    flexDirection: "row",
    overflow: "hidden",
  },
  legend: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: space.md,
  },
  legendItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: radii.pill,
  },
  legendText: {
    fontSize: typeScale.micro,
    fontWeight: "600",
  },
});
