import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, type StyleProp, type ViewStyle } from "react-native";
import { HIT_SLOP, radii, toneColors, useTheme, type AccentTone } from "../../theme";

type Props = {
  icon: React.ComponentProps<typeof Ionicons>["name"];
  onPress: () => void;
  accessibilityLabel: string;
  /** Faerbt Symbol und Grund ein (z. B. aktiver Privat-Modus). */
  tone?: AccentTone;
  /**
   * Gesperrt: nimmt den Knopf aus der Bedienung UND sagt das an. Eine reine
   * Ausgrauung ueber `style` bliebe fuer Vorlesehilfen unsichtbar - der Knopf
   * wuerde weiter als bedienbar angekuendigt und beim Tippen folgenlos bleiben.
   */
  disabled?: boolean;
  size?: number;
  style?: StyleProp<ViewStyle>;
  testID?: string;
};

/** Runder Symbolknopf der Bildschirm-Kopfzeile (Suche, Privat-Modus, Export). */
export function IconButton({
  icon,
  onPress,
  accessibilityLabel,
  tone,
  disabled = false,
  size = 40,
  style,
  testID,
}: Props) {
  const theme = useTheme();
  const colors = tone ? toneColors(theme, tone) : null;

  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      hitSlop={HIT_SLOP}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ disabled }}
      testID={testID}
      style={({ pressed }) => [
        styles.button,
        {
          width: size,
          height: size,
          borderRadius: radii.pill,
          backgroundColor: colors ? colors.muted : theme.surface,
          borderColor: colors ? colors.fill : theme.border,
          opacity: disabled ? 0.35 : pressed ? 0.7 : 1,
          transform: [{ scale: !disabled && pressed ? 0.93 : 1 }],
        },
        style,
      ]}
    >
      <Ionicons
        name={icon}
        size={Math.round(size * 0.46)}
        color={colors ? colors.text : theme.textPrimary}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
  },
});
