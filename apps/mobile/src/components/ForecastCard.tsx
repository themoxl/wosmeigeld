import { useRouter } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { FORECAST_CARD_HORIZON_MONTHS, useForecast } from "../hooks/useForecast";
import {
  leading,
  MIN_TOUCH,
  radii,
  space,
  typeScale,
  useTheme,
  type AccentTone,
  type ThemeTokens,
} from "../theme";
import { formatShortDate } from "../utils/dates";
import { describeForecastVerdict, type ForecastTone } from "../utils/forecastPresentation";
import { Badge, Card, CardLabel, Money, TrendChart, type TrendPoint } from "./ui";

/**
 * Die eine Finanzbotschaft auf "Heute"
 * (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 9). Sie ERSETZT die
 * Fixkostenquote, statt sie zu ergaenzen: der Screen ist ausdruecklich auf
 * genau eine Aussage ausgelegt (Kommentar in app/(tabs)/index.tsx), und
 * "Wieviel Prozent gehen fuer Fixkosten weg" beantwortet eine andere Frage als
 * "wann wird mein Geld knapp". FixedCostsRatioCard bleibt bestehen und wird
 * weiterhin auf der Vertragsseite gezeigt.
 *
 * Welche Aussage erscheint, entscheidet describeForecastVerdict() - dieselbe
 * Rangfolge wie auf dem Vorschau-Screen. Zwei Rangfolgen fuer dieselbe Frage
 * waeren zwei Wahrheiten.
 */
export function ForecastCard() {
  const theme = useTheme();
  const router = useRouter();
  const forecastQuery = useForecast({
    horizonMonths: FORECAST_CARD_HORIZON_MONTHS,
    granularity: "day",
  });

  if (forecastQuery.isLoading) {
    return (
      <Card>
        <CardLabel>Vorschau</CardLabel>
        <Text style={[styles.detail, { color: theme.textSecondary }]}>Wird berechnet …</Text>
      </Card>
    );
  }

  const forecast = forecastQuery.data;
  if (forecastQuery.isError || !forecast) {
    // Kein halber Verlauf und keine "0 EUR"-Ersatzaussage: eine Vorschau, die
    // nicht gerechnet werden konnte, hat keine Zahl (Plan 8.3).
    return (
      <Card>
        <CardLabel>Vorschau</CardLabel>
        <Text style={[styles.headline, { color: theme.textPrimary }]}>
          Vorschau konnte nicht berechnet werden.
        </Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Vorschau erneut berechnen"
          onPress={() => void forecastQuery.refetch()}
          style={({ pressed }) => [
            styles.retryButton,
            {
              borderColor: theme.border,
              backgroundColor: theme.surfaceRaised,
              opacity: pressed ? 0.7 : 1,
            },
          ]}
        >
          <Text style={[styles.retryLabel, { color: theme.textPrimary }]}>Erneut versuchen</Text>
        </Pressable>
      </Card>
    );
  }

  const verdict = describeForecastVerdict(forecast);
  const accent = toneToAccent(verdict.tone);
  const metrics = forecast.metrics;
  const points = buildTrendPoints(forecast.daily.map((day) => day.balance_minor));

  return (
    <Card
      accentEdge={accent}
      onPress={() => router.push("/mehr/vorschau")}
      accessibilityRole="button"
      accessibilityLabel={`${verdict.accessibilityLabel} Vorschau öffnen.`}
    >
      <View style={styles.headerRow}>
        <CardLabel>Vorschau</CardLabel>
        <Badge label={verdict.title} tone={accent} />
      </View>

      <View style={styles.body}>
        <Text style={[styles.headline, { color: resolveToneColor(verdict.tone, theme) }]}>
          {verdict.headline}
        </Text>
        <Text style={[styles.detail, { color: theme.textSecondary }]}>{verdict.detail}</Text>
      </View>

      {/* Kompakte Kurve: auf "Heute" ist sie Beiwerk zur Aussage, nicht die
          Aussage selbst - deshalb halbe Hoehe und ohne Beschriftung. */}
      {points.length >= 2 ? (
        <TrendChart
          points={points}
          height={90}
          thresholdMinor={
            forecast.safety_buffer_minor > 0 ? forecast.safety_buffer_minor : undefined
          }
          testID="heute-vorschau-verlauf"
        />
      ) : null}

      <View style={styles.footerRow}>
        <Text style={[styles.footerLabel, { color: theme.textSecondary }]} numberOfLines={1}>
          Tiefstand am {formatShortDate(metrics.lowest_balance_on)}
        </Text>
        <Money
          minorUnits={metrics.lowest_balance_minor}
          tone={metrics.lowest_balance_minor < 0 ? "warning" : "plain"}
        />
      </View>
    </Card>
  );
}

/**
 * Farbzuordnung zur Laufzeit statt im StyleSheet - die Tokens haengen am
 * Hell-/Dunkelmodus. "neutral" bleibt bewusst die primaere Textfarbe: eine
 * Aussage ohne Bewertung darf nicht wie ein Fehler aussehen.
 */
export function resolveToneColor(tone: ForecastTone, theme: ThemeTokens): string {
  switch (tone) {
    case "critical":
      return theme.warning;
    case "watch":
      return theme.highlight;
    case "positive":
      return theme.positive;
    default:
      return theme.textPrimary;
  }
}

/**
 * Derselbe Ton als Akzent des Designsystems, fuer <Card>, <Badge> und
 * <StatTile>. Getrennt von resolveToneColor(), weil diese Bausteine nicht eine
 * Farbe, sondern ein Dreigespann aus Text-, Flaechen- und Hinterlegungsfarbe
 * brauchen (toneColors()).
 */
export function toneToAccent(tone: ForecastTone): AccentTone {
  switch (tone) {
    case "critical":
      return "warning";
    case "watch":
      return "highlight";
    case "positive":
      return "positive";
    default:
      return "neutral";
  }
}

/**
 * Salden als Punkte fuer <TrendChart>, mit den beiden Stellen markiert, wegen
 * der man die Kurve ueberhaupt ansieht: der Tiefstand und der groesste
 * Zufluss (meist der Gehaltseingang). "Groesster Zufluss" ist der groesste
 * Anstieg zum Vortag - ein hoher Saldo allein sagt nichts darueber, wann
 * Geld hereinkam.
 */
export function buildTrendPoints(values: readonly number[]): TrendPoint[] {
  let lowIndex = -1;
  let lowValue = Number.POSITIVE_INFINITY;
  let riseIndex = -1;
  let bestRise = 0;
  let previous: number | null = null;

  values.forEach((value, index) => {
    if (value < lowValue) {
      lowValue = value;
      lowIndex = index;
    }
    if (previous !== null && value - previous > bestRise) {
      bestRise = value - previous;
      riseIndex = index;
    }
    previous = value;
  });

  return values.map((value, index) => ({
    x: index,
    value,
    marker: index === lowIndex ? "low" : index === riseIndex ? "high" : undefined,
  }));
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  body: {
    gap: space.xs,
  },
  headline: {
    fontSize: typeScale.large,
    fontWeight: "700",
    lineHeight: leading(typeScale.large, "tight"),
  },
  detail: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  footerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  footerLabel: {
    fontSize: typeScale.caption,
    flexShrink: 1,
  },
  retryButton: {
    minHeight: MIN_TOUCH,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.lg,
    alignItems: "center",
    justifyContent: "center",
  },
  retryLabel: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
});
