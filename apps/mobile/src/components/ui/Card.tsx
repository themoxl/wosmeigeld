import type { PropsWithChildren, ReactNode } from "react";
import {
  Pressable,
  StyleSheet,
  View,
  type StyleProp,
  type ViewStyle,
  type AccessibilityRole,
} from "react-native";
import { elevation, radii, space, toneColors, useTheme, type AccentTone } from "../../theme";

type Props = PropsWithChildren<{
  /** Farbiger Streifen links - markiert die Leitkarte eines Bildschirms. */
  accentEdge?: AccentTone;
  /** Farbiger Streifen oben - Alternative fuer Auswertungs-/Analysekarten. */
  accentTop?: AccentTone;
  /** Faerbt Rand und Grund ein: fuer Hinweis- und Warnbanner. */
  tone?: AccentTone;
  /** Weniger Innenabstand fuer dichte Listenkarten. */
  density?: "default" | "compact";
  onPress?: () => void;
  accessibilityLabel?: string;
  accessibilityRole?: AccessibilityRole;
  style?: StyleProp<ViewStyle>;
  testID?: string;
  /** Ueberschreibt den Innenabstand komplett (z. B. fuer randlose Listen). */
  padding?: number;
  children?: ReactNode;
}>;

/**
 * Karte des Handy-Designsystems: 20px Radius, 1px Rand, weicher Schatten.
 *
 * Sie ersetzt die flache Web-Karte (4px Radius, kein Schatten) auf allen
 * Bildschirmen. Der Schatten ist der Grund, warum hier nicht einfach eine
 * <View> mit Style genuegt: er braucht auf Android elevation und auf iOS
 * shadow*, und beides an ueber dreissig Stellen einzeln zu wiederholen waere
 * genau die Art Abweichung, die ein Designsystem verhindern soll.
 */
export function Card({
  accentEdge,
  accentTop,
  tone,
  density = "default",
  onPress,
  accessibilityLabel,
  accessibilityRole,
  style,
  testID,
  padding,
  children,
}: Props) {
  const theme = useTheme();
  const toned = tone ? toneColors(theme, tone) : null;

  const base: StyleProp<ViewStyle> = [
    styles.card,
    density === "compact" ? styles.compact : null,
    padding == null ? null : { padding },
    {
      backgroundColor: toned ? toned.muted : theme.surface,
      borderColor: toned ? toned.fill : theme.border,
    },
    accentEdge ? { borderLeftWidth: 4, borderLeftColor: toneColors(theme, accentEdge).fill } : null,
    accentTop ? { borderTopWidth: 3, borderTopColor: toneColors(theme, accentTop).fill } : null,
    elevation.card,
    style,
  ];

  if (!onPress) {
    return (
      <View style={base} testID={testID}>
        {children}
      </View>
    );
  }

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole={accessibilityRole ?? "button"}
      accessibilityLabel={accessibilityLabel}
      testID={testID}
      style={({ pressed }) => [base, pressed ? styles.pressed : null]}
    >
      {children}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: 1,
    borderRadius: radii.lg,
    padding: space.lg,
    gap: space.md,
  },
  compact: {
    padding: space.md,
    gap: space.sm,
  },
  pressed: {
    opacity: 0.88,
    transform: [{ scale: 0.995 }],
  },
});
