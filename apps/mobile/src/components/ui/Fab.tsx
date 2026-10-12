import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { Platform, Pressable, StyleSheet } from "react-native";
import { elevation, radii, space, useTheme } from "../../theme";

type Props = {
  onPress: () => void;
  accessibilityLabel: string;
  icon?: React.ComponentProps<typeof Ionicons>["name"];
  /** Abstand von unten - muss die Tableiste bzw. Safe-Area beruecksichtigen. */
  bottom?: number;
  testID?: string;
};

/**
 * Schwebender Schnellzugriff (Showcase: gruener Verlaufskreis ueber der
 * Tableiste). Loest beim Tippen eine kurze Vibration aus - bei einer
 * Erfassung an der Supermarktkasse schaut man aufs Display, nicht auf die
 * Bestaetigung.
 */
export function Fab({ onPress, accessibilityLabel, icon = "add", bottom, testID }: Props) {
  const theme = useTheme();

  function handlePress() {
    if (Platform.OS !== "web") {
      void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    }
    onPress();
  }

  return (
    <Pressable
      onPress={handlePress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      testID={testID}
      style={({ pressed }) => [
        styles.fab,
        elevation.floating,
        { bottom: bottom ?? space.lg, shadowColor: theme.primaryStrong },
        pressed ? styles.pressed : null,
      ]}
    >
      <LinearGradient
        // Gleiche Stufen wie beim Primaerbutton (siehe PrimaryButton.tsx):
        // das weisse Symbol braucht eine Flaeche, auf der es auch bei
        // Sonnenlicht steht.
        colors={[theme.primaryDeep, theme.primaryDeeper]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.gradient}
      >
        <Ionicons name={icon} size={30} color={theme.onAccent} />
      </LinearGradient>
    </Pressable>
  );
}

const SIZE = 58;

const styles = StyleSheet.create({
  fab: {
    position: "absolute",
    right: space.lg,
    width: SIZE,
    height: SIZE,
    borderRadius: radii.pill,
  },
  gradient: {
    flex: 1,
    borderRadius: radii.pill,
    alignItems: "center",
    justifyContent: "center",
  },
  pressed: {
    transform: [{ scale: 0.92 }],
    opacity: 0.9,
  },
});
