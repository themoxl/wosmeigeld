import { StyleSheet, View } from "react-native";
import { radii, toneColors, useTheme, type AccentTone } from "../../theme";

type Props = {
  /** Anteil 0..1 (Werte ueber 1 werden gekappt und farblich gewarnt). */
  progress: number;
  /**
   * Zweiter Marker 0..1: wo man am heutigen Tag stehen SOLLTE.
   * Das ist die "Nadel" aus dem Showcase und der eigentliche Erkenntnisgewinn -
   * ein Balken bei 64% sagt nichts, ohne zu wissen, dass erst Tag 15 von 31 ist.
   */
  pace?: number;
  tone?: AccentTone;
  height?: number;
  accessibilityLabel?: string;
  testID?: string;
};

export function PacingBar({
  progress,
  pace,
  tone = "positive",
  height = 10,
  accessibilityLabel,
  testID,
}: Props) {
  const theme = useTheme();
  const colors = toneColors(theme, tone);

  const clamped = Math.max(0, Math.min(1, progress));
  const percent = Math.round(progress * 100);
  const paceClamped = pace == null ? null : Math.max(0, Math.min(1, pace));

  const valueText =
    paceClamped == null
      ? `${percent} Prozent`
      : `${percent} Prozent verbraucht, planmaessig waeren ${Math.round(paceClamped * 100)} Prozent`;

  return (
    <View
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={accessibilityLabel ?? "Fortschritt"}
      accessibilityValue={{
        min: 0,
        max: 100,
        now: Math.min(100, Math.max(0, percent)),
        text: valueText,
      }}
      testID={testID}
      style={[
        styles.track,
        { height, borderRadius: height / 2, backgroundColor: theme.surfaceRaised },
      ]}
    >
      <View
        style={[
          styles.fill,
          {
            width: `${Math.round(clamped * 100)}%`,
            height,
            borderRadius: height / 2,
            backgroundColor: colors.fill,
          },
        ]}
      />
      {paceClamped == null ? null : (
        <View
          testID={testID ? `${testID}-needle` : undefined}
          style={[
            styles.needle,
            {
              left: `${Math.round(paceClamped * 100)}%`,
              top: -(height * 0.4),
              height: height * 1.8,
              backgroundColor: theme.textPrimary,
              borderColor: theme.surface,
            },
          ]}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  track: {
    width: "100%",
    position: "relative",
  },
  fill: {
    position: "absolute",
    left: 0,
    top: 0,
  },
  needle: {
    position: "absolute",
    width: 3,
    marginLeft: -1.5,
    borderRadius: radii.xs,
    borderWidth: 1,
  },
});
