import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";
import {
  initialsForLabel,
  radii,
  toneColors,
  toneForLabel,
  typeScale,
  useTheme,
  type AccentTone,
} from "../../theme";

type Props = {
  /** Name des Haendlers, der Kategorie oder des Kontos. */
  label: string;
  /** Erzwingt einen Farbton; ohne Angabe wird er stabil aus dem Namen abgeleitet. */
  tone?: AccentTone;
  /** Statt der Initialen ein Symbol (Kategorien, Vertragsarten). */
  icon?: React.ComponentProps<typeof Ionicons>["name"];
  size?: number;
};

/**
 * Quadratische Kachel mit Initialen oder Symbol - der visuelle Anker jeder
 * Listenzeile im Showcase.
 *
 * Die Farbe kommt aus dem Namen (toneForLabel), nicht aus dem Zufall: derselbe
 * Haendler hat dadurch auf jedem Bildschirm dieselbe Kachel, und die Liste
 * wird beim Ueberfliegen wiedererkennbar, ohne dass irgendwo eine Farbe
 * gespeichert werden muesste.
 */
export function Avatar({ label, tone, icon, size = 42 }: Props) {
  const theme = useTheme();
  const colors = toneColors(theme, tone ?? toneForLabel(label));

  return (
    <View
      style={[
        styles.avatar,
        {
          width: size,
          height: size,
          borderRadius: size >= 40 ? radii.md : radii.sm,
          backgroundColor: colors.muted,
        },
      ]}
    >
      {icon ? (
        <Ionicons name={icon} size={Math.round(size * 0.48)} color={colors.text} />
      ) : (
        <Text style={[styles.initials, { color: colors.text, fontSize: Math.round(size * 0.38) }]}>
          {initialsForLabel(label)}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  avatar: {
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  },
  initials: {
    fontWeight: "800",
    lineHeight: typeScale.title,
  },
});
