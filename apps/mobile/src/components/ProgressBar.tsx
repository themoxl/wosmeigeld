import { StyleSheet, View } from "react-native";
import { useTheme } from "../theme";

interface ProgressBarProps {
  progress: number; // 0.0 to 1.0 (or >1.0 for overflow)
  color?: string;
  backgroundColor?: string;
  height?: number;
  /**
   * Screenreader-Label fuer den Fortschritt, z. B. "Budget Lebensmittel".
   * Ohne Angabe wird ein neutrales Standardlabel verwendet.
   */
  accessibilityLabel?: string;
}

/**
 * Duenner Fortschrittsbalken ohne Soll-Nadel (Gruppensummen, Fixkostenquote).
 * Wo der Monatstag als Vergleich gebraucht wird, gehoert stattdessen
 * <PacingBar> aus components/ui hin.
 *
 * Meldet Rolle und Wert fuer VoiceOver/TalkBack und zeigt bei Ueberziehung
 * (progress > 1) zusaetzlich zur Warnfarbe eine kleine, sichtbare Spitze am
 * rechten Rand, statt den Balken einfach nur bei 100 % zu kappen.
 */
export function ProgressBar({
  progress,
  color,
  backgroundColor,
  height = 8,
  accessibilityLabel,
}: ProgressBarProps) {
  const theme = useTheme();

  const clampedProgress = Math.max(0, Math.min(1, progress));
  const isOverflowing = progress > 1;
  const activeColor = color ?? (isOverflowing ? theme.warningStrong : theme.positiveStrong);
  // Die Schiene liegt innerhalb einer Karte, also auf surfaceRaised statt auf
  // der Randfarbe - sonst verschwindet der leere Teil des Balkens im Dunkeln.
  const trackColor = backgroundColor ?? theme.surfaceRaised;

  const percent = Math.round(progress * 100);
  const displayPercent = Math.max(0, percent);
  const valueText = isOverflowing
    ? `${displayPercent} Prozent, ${displayPercent - 100} Prozent ueber dem Ziel`
    : `${displayPercent} Prozent`;

  return (
    <View
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={accessibilityLabel ?? "Fortschritt"}
      accessibilityValue={{ min: 0, max: 100, now: Math.min(100, displayPercent), text: valueText }}
      style={styles.container}
    >
      <View
        style={[styles.track, { height, borderRadius: height / 2, backgroundColor: trackColor }]}
      >
        <View
          style={{
            height,
            borderRadius: height / 2,
            width: `${Math.round(clampedProgress * 100)}%`,
            backgroundColor: activeColor,
          }}
        />
      </View>
      {isOverflowing ? (
        <View
          testID="progress-bar-overflow"
          style={[
            styles.overflowMarker,
            {
              borderTopWidth: height / 2 + 2,
              borderBottomWidth: height / 2 + 2,
              borderLeftColor: theme.warningStrong,
            },
          ]}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    width: "100%",
  },
  track: {
    flex: 1,
    overflow: "hidden",
  },
  overflowMarker: {
    marginLeft: 2,
    width: 0,
    height: 0,
    backgroundColor: "transparent",
    borderLeftWidth: 7,
    borderTopColor: "transparent",
    borderBottomColor: "transparent",
  },
});
