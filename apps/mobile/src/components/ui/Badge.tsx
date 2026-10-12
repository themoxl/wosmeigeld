import { StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";
import { letterSpacingLabel, radii, space, toneColors, typeScale, useTheme } from "../../theme";
import type { AccentTone } from "../../theme";

type Props = {
  label: string;
  tone?: AccentTone;
  /** Kleinere Variante fuer Marker innerhalb einer Listenzeile ("Abo", "Split"). */
  size?: "default" | "small";
  style?: StyleProp<ViewStyle>;
};

/**
 * Statuspille des Showcase ("Im Plan", "83% aufgebraucht", "+5,00 €/Monat").
 *
 * Farbe traegt hier Bedeutung, deshalb steht der Text IMMER ausgeschrieben
 * daneben - eine Pille, die nur durch Gruen/Rot unterscheidbar waere, koennte
 * ein farbfehlsichtiger Nutzer nicht lesen.
 */
export function Badge({ label, tone = "neutral", size = "default", style }: Props) {
  const theme = useTheme();
  const colors = toneColors(theme, tone);

  return (
    <View
      style={[
        styles.badge,
        size === "small" ? styles.small : null,
        { backgroundColor: colors.muted },
        style,
      ]}
    >
      <Text
        style={[
          styles.label,
          size === "small" ? styles.labelSmall : null,
          { color: tone === "neutral" ? theme.textSecondary : colors.text },
        ]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </View>
  );
}

/** Grossbuchstaben-Kartenlabel ("KONTEN & SALDEN"). */
export function CardLabel({ children }: { children: string }) {
  const theme = useTheme();
  return <Text style={[styles.cardLabel, { color: theme.textSecondary }]}>{children}</Text>;
}

const styles = StyleSheet.create({
  badge: {
    paddingHorizontal: space.sm,
    paddingVertical: space.hair,
    borderRadius: radii.xs,
    alignSelf: "flex-start",
  },
  small: {
    paddingHorizontal: space.xs,
    paddingVertical: space.hair,
  },
  label: {
    fontSize: typeScale.micro,
    fontWeight: "700",
  },
  labelSmall: {
    fontSize: typeScale.nano,
  },
  cardLabel: {
    fontSize: typeScale.micro,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: letterSpacingLabel,
  },
});
