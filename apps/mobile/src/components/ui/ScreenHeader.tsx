import type { ReactNode } from "react";
import { StyleSheet, Text, View } from "react-native";
import { letterSpacingLabel, space, typeScale, useTheme } from "../../theme";

type Props = {
  title: string;
  /** Kleine Zeile ueber dem Titel, z. B. "SAMSTAG, 15. AUGUST". */
  eyebrow?: string;
  /** Erklaerung unter dem Titel. */
  subtitle?: string;
  /** Symbolknoepfe rechts. */
  actions?: ReactNode;
};

/**
 * Bildschirm-Kopfzeile im Inhaltsbereich (22px, sehr fett), nicht die
 * Navigationsleiste.
 *
 * Der Showcase verzichtet auf die native Titelleiste: der Titel scrollt mit
 * und gibt oben im Bild Platz frei, den auf einem 6-Zoll-Display sonst eine
 * halb leere Leiste belegt.
 */
export function ScreenHeader({ title, eyebrow, subtitle, actions }: Props) {
  const theme = useTheme();

  return (
    <View style={styles.row}>
      <View style={styles.textColumn}>
        {eyebrow ? (
          <Text style={[styles.eyebrow, { color: theme.textSecondary }]}>
            {eyebrow.toUpperCase()}
          </Text>
        ) : null}
        <Text style={[styles.title, { color: theme.textPrimary }]} numberOfLines={2}>
          {title}
        </Text>
        {subtitle ? (
          <Text style={[styles.subtitle, { color: theme.textSecondary }]}>{subtitle}</Text>
        ) : null}
      </View>
      {actions ? <View style={styles.actions}>{actions}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.md,
    paddingHorizontal: space.lg,
    paddingTop: space.sm,
    paddingBottom: space.xs,
  },
  textColumn: {
    flex: 1,
    gap: space.hair,
  },
  eyebrow: {
    fontSize: typeScale.caption,
    fontWeight: "700",
    letterSpacing: letterSpacingLabel,
  },
  title: {
    fontSize: typeScale.title,
    fontWeight: "800",
    letterSpacing: -0.6,
  },
  subtitle: {
    fontSize: typeScale.caption,
  },
  actions: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
});
