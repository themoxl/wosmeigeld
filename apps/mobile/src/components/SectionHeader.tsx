import type { ReactNode } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { letterSpacingLabel, space, typeScale, useTheme } from "../theme";

type Props = {
  title: string;
  /** Optionaler Inhalt rechts, z. B. ein Betrag oder Zaehler. Wird vor einem Aktionslink gerendert. */
  trailing?: ReactNode;
  /** Kompakte Textaktion rechts, z. B. "Alle" fuer die Buchungsvorschau auf "Heute". */
  actionLabel?: string;
  onActionPress?: () => void;
  /**
   * Groesse der Ueberschrift. Standard "xs" (12) wie bisher; "sm" (13) fuer
   * reine Navigationslisten wie den Mehr-Tab, in denen die Ueberschrift die
   * einzige Struktur ist und deshalb nicht leiser sein darf als der Inhalt.
   */
  size?: "xs" | "sm";
  /**
   * Abstand nach oben. Standard "md" (16) wie bisher; "lg" (24) ergibt
   * zusammen mit den 8 dp nach unten das Verhaeltnis 3:1, bei dem die
   * Ueberschrift sichtbar am Block darunter klebt statt zwischen zwei
   * Bloecken zu schweben.
   */
  topSpacing?: "md" | "lg";
};

/**
 * Flache Abschnittsueberschrift ohne eigene Karte: dieselbe Grossbuchstaben-
 * Sperrung wie <CardLabel> innerhalb einer Karte, nur eine Ebene hoeher. Beide
 * gleich zu setzen ist Absicht - so liest sich "Abschnitt" ueberall gleich,
 * egal ob die Ueberschrift ueber oder in einer Karte steht.
 *
 * Der Seitenabstand steckt bewusst in der Ueberschrift selbst: die
 * Scrollflaechen der Bildschirme liegen randlos, damit Karten und Striche
 * bis an den Rand laufen koennen.
 */
export function SectionHeader({
  title,
  trailing,
  actionLabel,
  onActionPress,
  size = "xs",
  topSpacing = "md",
}: Props) {
  const theme = useTheme();

  return (
    <View style={[styles.row, topSpacing === "lg" ? styles.rowSpacious : null]}>
      <Text
        accessibilityRole="header"
        style={[
          styles.title,
          size === "sm" ? styles.titleSm : null,
          { color: theme.textSecondary },
        ]}
      >
        {title}
      </Text>
      <View style={styles.trailingGroup}>
        {trailing}
        {actionLabel && onActionPress ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={actionLabel}
            hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
            onPress={onActionPress}
            style={({ pressed }) => [styles.action, { opacity: pressed ? 0.6 : 1 }]}
          >
            <Text style={[styles.actionLabel, { color: theme.positive }]}>{actionLabel}</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: space.lg,
    paddingTop: space.lg,
    paddingBottom: space.sm,
    gap: space.sm,
  },
  rowSpacious: {
    paddingTop: space.xxl,
  },
  title: {
    fontSize: typeScale.caption,
    fontWeight: "700",
    letterSpacing: letterSpacingLabel,
    textTransform: "uppercase",
    flexShrink: 1,
  },
  titleSm: {
    fontSize: typeScale.body,
  },
  trailingGroup: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  action: {
    minHeight: 20,
    justifyContent: "center",
  },
  actionLabel: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
});
