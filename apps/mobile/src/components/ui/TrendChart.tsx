import { StyleSheet, Text, View } from "react-native";
import Svg, { Circle, Defs, LinearGradient, Line, Path, Stop } from "react-native-svg";
import { fontFamily, space, typeScale, useTheme } from "../../theme";

export type TrendPoint = {
  /** Waagerechte Position, beliebige Einheit (z. B. Tagesindex). */
  x: number;
  /** Wert in Cent. */
  value: number;
  /** Punkt hervorheben (Tiefstand, Gehaltseingang). */
  marker?: "low" | "high";
};

type Props = {
  points: TrendPoint[];
  /** Waagerechte Warnlinie in Cent, z. B. der Sicherheitspuffer. */
  thresholdMinor?: number;
  thresholdLabel?: string;
  height?: number;
  testID?: string;
};

const PADDING_TOP = 12;
const PADDING_BOTTOM = 12;
const VIEW_WIDTH = 350;

/**
 * Verlaufskurve der Liquiditaetsvorschau (Showcase: Flaeche mit Verlauf,
 * gestrichelte Pufferlinie, markierte Wendepunkte).
 *
 * Bewusst ohne Achsenbeschriftung: auf Handybreite kostet eine Achse mehr
 * Platz als sie erklaert. Die beiden Zahlen, auf die es ankommt - Tiefstand
 * und Puffer - stehen als Text daneben.
 */
export function TrendChart({
  points,
  thresholdMinor,
  thresholdLabel,
  height = 130,
  testID,
}: Props) {
  const theme = useTheme();

  if (points.length < 2) {
    return (
      <View style={[styles.empty, { height }]} testID={testID}>
        <Text style={[styles.emptyText, { color: theme.textSecondary }]}>
          Noch zu wenig Daten für eine Verlaufskurve.
        </Text>
      </View>
    );
  }

  const values = points.map((point) => point.value);
  const candidates = thresholdMinor == null ? values : [...values, thresholdMinor];
  const minValue = Math.min(...candidates);
  const maxValue = Math.max(...candidates);
  const span = maxValue - minValue || 1;

  const minX = Math.min(...points.map((point) => point.x));
  const maxX = Math.max(...points.map((point) => point.x));
  const spanX = maxX - minX || 1;

  const plotHeight = height - PADDING_TOP - PADDING_BOTTOM;
  const toX = (x: number) => ((x - minX) / spanX) * VIEW_WIDTH;
  const toY = (value: number) => PADDING_TOP + (1 - (value - minValue) / span) * plotHeight;

  const line = points
    .map(
      (point, index) =>
        `${index === 0 ? "M" : "L"} ${toX(point.x).toFixed(2)} ${toY(point.value).toFixed(2)}`,
    )
    .join(" ");
  const area = `${line} L ${VIEW_WIDTH} ${height} L 0 ${height} Z`;

  const dipsBelowThreshold =
    thresholdMinor != null && values.some((value) => value < thresholdMinor);
  const lineColor = dipsBelowThreshold ? theme.warningStrong : theme.positive;

  return (
    <View style={{ height }} testID={testID}>
      <Svg width="100%" height={height} viewBox={`0 0 ${VIEW_WIDTH} ${height}`}>
        <Defs>
          <LinearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor={lineColor} stopOpacity="0.28" />
            <Stop offset="1" stopColor={lineColor} stopOpacity="0" />
          </LinearGradient>
        </Defs>

        {thresholdMinor == null ? null : (
          <Line
            x1="0"
            y1={toY(thresholdMinor)}
            x2={VIEW_WIDTH}
            y2={toY(thresholdMinor)}
            stroke={theme.warningStrong}
            strokeOpacity={0.5}
            strokeWidth={1.5}
            strokeDasharray="5 4"
          />
        )}

        <Path d={area} fill="url(#trend-fill)" />
        <Path
          d={line}
          fill="none"
          stroke={lineColor}
          strokeWidth={3}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {points
          .filter((point) => point.marker)
          .map((point) => (
            <Circle
              key={`marker-${point.x}`}
              cx={toX(point.x)}
              cy={toY(point.value)}
              r={5}
              fill={point.marker === "low" ? theme.highlightStrong : theme.positiveStrong}
              stroke={theme.surface}
              strokeWidth={2}
            />
          ))}
      </Svg>

      {thresholdLabel ? (
        <Text style={[styles.thresholdLabel, { color: theme.warning }]} numberOfLines={1}>
          {thresholdLabel}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  empty: {
    alignItems: "center",
    justifyContent: "center",
  },
  emptyText: {
    fontSize: typeScale.caption,
  },
  thresholdLabel: {
    position: "absolute",
    left: space.xs,
    bottom: 0,
    fontSize: typeScale.nano,
    fontFamily: fontFamily.mono,
  },
});
