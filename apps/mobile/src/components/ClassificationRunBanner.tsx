import { useRouter } from "expo-router";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { isClassificationRunActive, useLatestClassificationRun } from "../hooks/useAI";
import { elevation, radii, space, typeScale, useTheme } from "../theme";
import { Card } from "./ui";

/**
 * Globaler Status des KI-Laufs (siehe docs/KI-FINANZASSISTENT-PLAN.md,
 * Ausbaustufe 0). Der Lauf laeuft im Worker weiter, waehrend die App im
 * Hintergrund ist oder der Nutzer in einem anderen Tab arbeitet - deshalb
 * haengt der Hinweis am Wurzel-Layout und nicht am Review-Screen. Ein Tipp
 * fuehrt dorthin, wo die Ergebnisse landen.
 *
 * Bewusst nur waehrend eines aktiven Laufs sichtbar: ein dauerhaft
 * eingeblendetes Ergebnisbanner ueber der Tableiste waere im Weg. Das Ergebnis
 * eines beendeten Laufs steht auf dem Review-Screen.
 */
export function ClassificationRunBanner() {
  const theme = useTheme();
  const router = useRouter();
  const { data: run } = useLatestClassificationRun();

  if (!run || !isClassificationRunActive(run)) {
    return null;
  }

  return (
    <View style={styles.container} pointerEvents="box-none">
      {/* Pillenform statt Kartenradius: das Banner schwebt ueber dem Inhalt
          und soll sich davon absetzen, nicht wie eine weitere Karte lesen. */}
      <Card
        tone={run.is_stale ? "warning" : undefined}
        onPress={() => router.push("/mehr/review")}
        accessibilityLabel="KI-Finanz-Check anzeigen"
        padding={0}
        style={[styles.banner, elevation.floating]}
      >
        {run.is_stale ? null : <ActivityIndicator size="small" color={theme.positive} />}
        <Text
          style={[styles.label, { color: run.is_stale ? theme.warning : theme.textPrimary }]}
          numberOfLines={1}
        >
          {run.is_stale
            ? "KI-Finanz-Check ohne Rückmeldung"
            : `KI-Finanz-Check: Kategorien ${run.processed_count}/${run.total_count}`}
        </Text>
      </Card>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: "absolute",
    left: 0,
    right: 0,
    // Ueber der Tableiste (49-83 px inkl. Home-Indicator) und ueber dem
    // Inhalt von Stack-Screens.
    bottom: 100,
    alignItems: "center",
  },
  banner: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    borderRadius: radii.pill,
    paddingHorizontal: space.lg,
    paddingVertical: space.sm,
    minHeight: 44,
    maxWidth: "90%",
  },
  label: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
});
