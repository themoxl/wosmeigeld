import { Pressable, StyleSheet, Text, View } from "react-native";
import { leading, MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";
import { Card } from "./ui";

export type InlineQueryStateStatus = "loading" | "error" | "empty";

type Props = {
  status: InlineQueryStateStatus;
  /** Ueberschrift des Zustands, z. B. "Keine Buchungen im August". Hat sinnvolle Standardwerte je Status. */
  title?: string;
  /** Kurzer, verstaendlicher Zusatztext statt einer rohen API-Meldung. */
  message?: string;
  /** Callback fuer den "Erneut versuchen"-Button im Fehlerzustand. Ohne Angabe erscheint kein Button. */
  onRetry?: () => void;
  /** Anzahl der Skeleton-Zeilen im Ladezustand. */
  skeletonRows?: number;
  /** Hoehe je Skeleton-Zeile, in etwa die Endhoehe des spaeter geladenen Inhalts. */
  skeletonRowHeight?: number;
  testID?: string;
};

/**
 * Kompakter, wiederverwendbarer Lade-/Leer-/Fehlerzustand fuer Abschnitte
 * innerhalb eines Screens (Designsystem Abschnitt 8, Regel 6).
 *
 * Die drei Zustaende sind absichtlich unterschiedlich laut: Skelette in der
 * Innenkachelfarbe deuten nur an, dass gleich etwas kommt; "keine Daten" ist
 * ein ruhiger, mittiger Satz; nur der Fehler bekommt eine korallfarbene Karte
 * und die Rolle "alert". So verwechselt niemand einen leeren Monat mit einem
 * Verbindungsabbruch.
 */
export function InlineQueryState({
  status,
  title,
  message,
  onRetry,
  skeletonRows = 3,
  skeletonRowHeight = 14,
  testID,
}: Props) {
  const theme = useTheme();

  if (status === "loading") {
    return (
      <View testID={testID} style={styles.container} accessible accessibilityLabel="Wird geladen">
        {Array.from({ length: Math.max(1, skeletonRows) }).map((_, index) => (
          <View
            key={index}
            importantForAccessibility="no"
            style={[
              styles.skeletonRow,
              {
                height: skeletonRowHeight,
                width: index === skeletonRows - 1 ? "60%" : "100%",
                backgroundColor: theme.surfaceRaised,
              },
            ]}
          />
        ))}
      </View>
    );
  }

  if (status === "error") {
    // Ohne eigenen Aussenabstand: der Zustand steht fast immer schon in einer
    // Karte oder in einer Scrollflaeche mit Seitenrand.
    return (
      <Card tone="warning" density="compact" testID={testID}>
        <View accessibilityRole="alert" style={styles.stateBox}>
          <Text style={[styles.title, { color: theme.textPrimary }]}>
            {title ?? "Daten konnten nicht geladen werden"}
          </Text>
          <Text style={[styles.message, { color: theme.textSecondary }]}>
            {message ?? "Bitte prüfe deine Verbindung und versuche es erneut."}
          </Text>
          {onRetry ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Erneut versuchen"
              onPress={onRetry}
              style={({ pressed }) => [
                styles.retryButton,
                {
                  borderColor: theme.border,
                  backgroundColor: pressed ? theme.surfaceHover : theme.surface,
                },
              ]}
            >
              <Text style={[styles.retryLabel, { color: theme.textPrimary }]}>
                Erneut versuchen
              </Text>
            </Pressable>
          ) : null}
        </View>
      </Card>
    );
  }

  return (
    <View testID={testID} style={[styles.container, styles.stateBox]}>
      <Text style={[styles.title, { color: theme.textPrimary }]}>{title ?? "Keine Daten"}</Text>
      {message ? (
        <Text style={[styles.message, { color: theme.textSecondary }]}>{message}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    paddingHorizontal: space.lg,
    paddingVertical: space.lg,
    gap: space.sm,
  },
  stateBox: {
    alignItems: "center",
    gap: space.sm,
  },
  skeletonRow: {
    borderRadius: radii.xs,
  },
  title: {
    fontSize: typeScale.base,
    fontWeight: "700",
    textAlign: "center",
  },
  message: {
    fontSize: typeScale.body,
    textAlign: "center",
    lineHeight: leading(typeScale.body),
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
