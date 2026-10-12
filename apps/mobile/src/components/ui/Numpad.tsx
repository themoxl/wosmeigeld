import * as Haptics from "expo-haptics";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { fontFamily, radii, space, typeScale, useTheme } from "../../theme";

export type NumpadKey = "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "0" | "," | "back";

type Props = {
  onPress: (key: NumpadKey) => void;
  /** Langes Tippen auf Loeschen setzt den Betrag zurueck. */
  onClear?: () => void;
  testID?: string;
};

const KEYS: NumpadKey[] = ["1", "2", "3", "4", "5", "6", "7", "8", "9", ",", "0", "back"];

const LABELS: Record<NumpadKey, string> = {
  "1": "1",
  "2": "2",
  "3": "3",
  "4": "4",
  "5": "5",
  "6": "6",
  "7": "7",
  "8": "8",
  "9": "9",
  "0": "0",
  ",": ",",
  back: "⌫",
};

const A11Y: Record<NumpadKey, string> = {
  "1": "Eins",
  "2": "Zwei",
  "3": "Drei",
  "4": "Vier",
  "5": "Fünf",
  "6": "Sechs",
  "7": "Sieben",
  "8": "Acht",
  "9": "Neun",
  "0": "Null",
  ",": "Komma",
  back: "Letzte Ziffer löschen",
};

/**
 * Eigener Ziffernblock statt der Systemtastatur.
 *
 * Der Grund steht im Showcase: die Systemtastatur verdeckt das halbe Display
 * und zwingt zum Zielen auf 8mm-Tasten. Ein 3x4-Raster mit 56dp-Tasten laesst
 * sich an der Kasse blind und einhaendig bedienen - und darunter bleibt Platz
 * fuer Kategorie und Speichern-Knopf, ohne zu scrollen.
 */
export function Numpad({ onPress, onClear, testID }: Props) {
  const theme = useTheme();

  function handlePress(key: NumpadKey) {
    if (Platform.OS !== "web") {
      void Haptics.selectionAsync().catch(() => {});
    }
    onPress(key);
  }

  return (
    <View style={styles.grid} testID={testID}>
      {KEYS.map((key) => (
        <Pressable
          key={key}
          accessibilityRole="button"
          accessibilityLabel={A11Y[key]}
          onPress={() => handlePress(key)}
          onLongPress={key === "back" ? onClear : undefined}
          style={({ pressed }) => [
            styles.key,
            {
              backgroundColor: pressed ? theme.surfaceHover : theme.surface,
              borderColor: theme.border,
              transform: [{ scale: pressed ? 0.96 : 1 }],
            },
          ]}
        >
          <Text style={[styles.keyLabel, { color: theme.textPrimary }]}>{LABELS[key]}</Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    rowGap: space.sm,
  },
  key: {
    width: "31.5%",
    height: 56,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
    borderWidth: 1,
  },
  keyLabel: {
    fontSize: typeScale.title,
    fontWeight: "700",
    fontFamily: fontFamily.mono,
  },
});
