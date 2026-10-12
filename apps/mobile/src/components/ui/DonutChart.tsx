import { StyleSheet, Text, View } from "react-native";
import Svg, { Circle } from "react-native-svg";
import {
  letterSpacingLabel,
  space,
  toneColors,
  typeScale,
  useTheme,
  type AccentTone,
} from "../../theme";
import { fontFamily } from "../../theme";

export type DonutSlice = {
  label: string;
  value: number;
  tone: AccentTone;
};

type Props = {
  slices: DonutSlice[];
  /** Grosse Zahl in der Mitte (bereits formatiert). */
  centerValue: string;
  /** Beschriftung darunter, z. B. "AUSGABEN". */
  centerLabel: string;
  size?: number;
  strokeWidth?: number;
  testID?: string;
};

// Der Kreis wird ueber stroke-dasharray auf einer Gesamtlaenge von 100
// gezeichnet: Radius 100 / (2*PI) ergibt exakt Umfang 100, dann entspricht
// jeder Anteil in Prozent direkt seiner Strichlaenge. Genau dieser Kniff
// steckt auch im Showcase (r="15.91549430918954").
const CIRCUMFERENCE = 100;
const RADIUS_FOR_UNIT_CIRCUMFERENCE = CIRCUMFERENCE / (2 * Math.PI);

/**
 * Ringdiagramm der Kategorienverteilung.
 *
 * Der Ring zeigt nur die Groessenordnung; abgelesen wird in der Legende
 * darunter. Deshalb steht in der Mitte die Gesamtsumme und nicht der groesste
 * Anteil - die Frage lautet "wie viel war es insgesamt", bevor sie "wofuer"
 * lautet.
 */
export function DonutChart({
  slices,
  centerValue,
  centerLabel,
  size = 160,
  strokeWidth = 6,
  testID,
}: Props) {
  const theme = useTheme();
  const total = slices.reduce((sum, slice) => sum + Math.max(0, slice.value), 0);

  const viewBoxSize = RADIUS_FOR_UNIT_CIRCUMFERENCE * 2 + strokeWidth * 2;
  const center = viewBoxSize / 2;

  let offset = 25; // 25 = Start oben statt rechts.
  const arcs = slices
    .filter((slice) => slice.value > 0)
    .map((slice) => {
      const share = total > 0 ? (Math.max(0, slice.value) / total) * 100 : 0;
      const arc = { slice, share, dashOffset: offset };
      // Negative Richtung, damit die Abschnitte im Uhrzeigersinn folgen.
      offset -= share;
      return arc;
    });

  return (
    <View style={[styles.wrapper, { height: size }]} testID={testID}>
      <Svg width={size} height={size} viewBox={`0 0 ${viewBoxSize} ${viewBoxSize}`}>
        <Circle
          cx={center}
          cy={center}
          r={RADIUS_FOR_UNIT_CIRCUMFERENCE}
          fill="transparent"
          stroke={theme.surfaceRaised}
          strokeWidth={strokeWidth}
        />
        {arcs.map((arc) => (
          <Circle
            key={arc.slice.label}
            cx={center}
            cy={center}
            r={RADIUS_FOR_UNIT_CIRCUMFERENCE}
            fill="transparent"
            stroke={toneColors(theme, arc.slice.tone).fill}
            strokeWidth={strokeWidth}
            strokeDasharray={`${arc.share} ${CIRCUMFERENCE - arc.share}`}
            strokeDashoffset={arc.dashOffset}
            strokeLinecap="butt"
          />
        ))}
      </Svg>

      <View style={styles.center} pointerEvents="none">
        <Text style={[styles.centerValue, { color: theme.textPrimary }]} numberOfLines={1}>
          {centerValue}
        </Text>
        <Text style={[styles.centerLabel, { color: theme.textSecondary }]} numberOfLines={1}>
          {centerLabel.toUpperCase()}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    alignItems: "center",
    justifyContent: "center",
    position: "relative",
  },
  center: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    gap: space.hair,
    paddingHorizontal: space.xl,
  },
  centerValue: {
    fontSize: typeScale.large,
    fontWeight: "800",
    fontFamily: fontFamily.mono,
  },
  centerLabel: {
    fontSize: typeScale.nano,
    fontWeight: "700",
    letterSpacing: letterSpacingLabel,
  },
});
