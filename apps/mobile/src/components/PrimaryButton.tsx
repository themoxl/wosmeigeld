import { LinearGradient } from "expo-linear-gradient";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { elevation, MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";

type Variant = "primary" | "secondary" | "danger";

type Props = {
  label: string;
  onPress: () => void;
  loading?: boolean;
  disabled?: boolean;
  variant?: Variant;
  /** Kompakte Zeile innerhalb einer Karte (36-40dp statt 48dp). */
  size?: "default" | "compact";
};

/**
 * Aktions-Button mit Ladezustand.
 *
 * Die Primaerfassung traegt denselben Verlauf wie der FAB (Showcase
 * ".btn-submit"/".mobile-fab") - so ist auf jedem Bildschirm dieselbe Farbe
 * "die Aktion, die dich weiterbringt", und alles andere bleibt ruhig.
 */
export function PrimaryButton({
  label,
  onPress,
  loading = false,
  disabled = false,
  variant = "primary",
  size = "default",
}: Props) {
  const theme = useTheme();
  const isDisabled = disabled || loading;

  const textColor = variant === "secondary" ? theme.textPrimary : theme.onAccent;

  const content = loading ? (
    <ActivityIndicator color={textColor} />
  ) : (
    <Text
      numberOfLines={1}
      style={[styles.label, size === "compact" ? styles.labelCompact : null, { color: textColor }]}
    >
      {label}
    </Text>
  );

  const shape = [styles.button, size === "compact" ? styles.buttonCompact : null];

  if (variant === "secondary") {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: isDisabled }}
        onPress={isDisabled ? undefined : onPress}
        style={({ pressed }) => [
          shape,
          styles.flat,
          {
            backgroundColor: theme.surfaceRaised,
            borderColor: theme.border,
            opacity: isDisabled ? 0.5 : pressed ? 0.8 : 1,
          },
        ]}
      >
        {content}
      </Pressable>
    );
  }

  // Beide Verlaeufe laufen bewusst innerhalb der DUNKLEN Stufe der jeweiligen
  // Farbe. Der Showcase setzt fuer den Primaerknopf forest-500 -> #15803d,
  // dort erreicht das weisse Label am hellen Ende aber nur 2,3:1 (dunkles
  // Theme) - und beim Gefahrenknopf war der Verlauf sogar aufsteigend hell
  // (coral-500 -> coral-400, 3,8 bis 2,8:1). Der Verlauf bleibt als Merkmal
  // erhalten, nur eine Stufe tiefer: ueber primaryDeep/primaryDeeper bzw.
  // warningDeep steht Weiss durchgehend ueber 5:1.
  const gradient: [string, string] =
    variant === "danger"
      ? [theme.warningDeep, theme.warningDeeper]
      : [theme.primaryDeep, theme.primaryDeeper];

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: isDisabled }}
      onPress={isDisabled ? undefined : onPress}
      style={({ pressed }) => [
        shape,
        elevation.card,
        {
          opacity: isDisabled ? 0.5 : pressed ? 0.9 : 1,
          transform: [{ scale: pressed ? 0.99 : 1 }],
        },
      ]}
    >
      <LinearGradient
        colors={gradient}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[StyleSheet.absoluteFill, styles.gradient]}
      />
      <View style={styles.content}>{content}</View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    minHeight: 48,
    borderRadius: radii.md,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: space.lg,
    overflow: "hidden",
  },
  buttonCompact: {
    // Nicht kleiner als MIN_TOUCH: die kompakte Fassung traegt die
    // Hauptaktionen in Karten ("Bestätigen", "Vertrag öffnen") und war mit
    // 38dp das haeufigste zu kleine Tippziel der App.
    minHeight: MIN_TOUCH,
    paddingHorizontal: space.md,
  },
  flat: {
    borderWidth: 1,
  },
  gradient: {
    borderRadius: radii.md,
  },
  content: {
    alignItems: "center",
    justifyContent: "center",
  },
  label: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  labelCompact: {
    fontSize: typeScale.caption,
  },
});
