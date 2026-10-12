import { Ionicons } from "@expo/vector-icons";
import type { ReactNode } from "react";
import {
  Pressable,
  StyleSheet,
  Text,
  View,
  type AccessibilityRole,
  type StyleProp,
  type TextStyle,
} from "react-native";
import { MIN_TOUCH, space, typeScale, useTheme, type AccentTone } from "../../theme";
import { Avatar } from "./Avatar";

type Props = {
  title: string;
  /**
   * Zusatzstil fuer den Titel. Additiv ergaenzt fuer die Einkaufsliste: ein
   * erledigter Artikel wird durchgestrichen, und das laesst sich ohne Zugriff
   * auf den Titel-Textstil nicht ausdruecken. Ohne Angabe unveraendert.
   */
  titleStyle?: StyleProp<TextStyle>;
  /** Zweite Zeile: Datum, Kategorie, Konto - alles, was den Titel einordnet. */
  subtitle?: string;
  /** Kachel links. Ohne Angabe wird sie aus `title` abgeleitet. */
  avatarLabel?: string;
  avatarTone?: AccentTone;
  avatarIcon?: React.ComponentProps<typeof Ionicons>["name"];
  /** Ohne Kachel (z. B. Kategorienlegende mit farbigem Punkt). */
  leading?: ReactNode;
  /** Marker rechts neben dem Titel (Abo, Split, Vorgemerkt). */
  titleBadge?: ReactNode;
  /** Rechte Spalte: Betrag und Zusatzzeile. */
  trailing?: ReactNode;
  /** Fussnote unter dem Betrag, z. B. "Danach: 2.435 €". */
  trailingCaption?: string;
  onPress?: () => void;
  accessibilityLabel?: string;
  /**
   * Rolle des Tippziels; Vorgabe "button". Zeilen, die auf einen anderen
   * Bildschirm fuehren, geben "link" an - eine Vorlesehilfe kuendigt damit
   * einen Ortswechsel an statt einer Aktion an Ort und Stelle.
   * (Additiv ergaenzt: ohne die Angabe verhaelt sich die Zeile wie bisher.)
   */
  accessibilityRole?: AccessibilityRole;
  showChevron?: boolean;
  /** Trennlinie unten - in Listen fuer alle ausser der letzten Zeile. */
  divider?: boolean;
  testID?: string;
};

/**
 * Listenzeile des Showcase: Kachel, zweizeiliger Text, rechts Betrag mit
 * Fussnote.
 *
 * Genau diese Form wiederholt sich auf Heute, Buchungen, Vertraegen und in
 * den Sheets. Sie hier einmal zu definieren haelt Tippziel (mindestens 44dp),
 * Textabschneiden und Trennlinien ueberall gleich - und macht aus einer
 * Aenderung an der Zeilenhoehe eine Aenderung an einer Datei.
 */
export function DataRow({
  title,
  titleStyle,
  subtitle,
  avatarLabel,
  avatarTone,
  avatarIcon,
  leading,
  titleBadge,
  trailing,
  trailingCaption,
  onPress,
  accessibilityLabel,
  accessibilityRole = "button",
  showChevron = false,
  divider = true,
  testID,
}: Props) {
  const theme = useTheme();

  const body = (
    <>
      {leading ?? <Avatar label={avatarLabel ?? title} tone={avatarTone} icon={avatarIcon} />}

      <View style={styles.textColumn}>
        <View style={styles.titleRow}>
          <Text style={[styles.title, { color: theme.textPrimary }, titleStyle]} numberOfLines={1}>
            {title}
          </Text>
          {titleBadge}
        </View>
        {subtitle ? (
          <Text style={[styles.subtitle, { color: theme.textSecondary }]} numberOfLines={1}>
            {subtitle}
          </Text>
        ) : null}
      </View>

      {trailing || trailingCaption ? (
        <View style={styles.trailingColumn}>
          {trailing}
          {trailingCaption ? (
            <Text style={[styles.trailingCaption, { color: theme.textMuted }]} numberOfLines={1}>
              {trailingCaption}
            </Text>
          ) : null}
        </View>
      ) : null}

      {showChevron ? <Ionicons name="chevron-forward" size={18} color={theme.textMuted} /> : null}
    </>
  );

  const rowStyle = [
    styles.row,
    divider
      ? { borderBottomColor: theme.border, borderBottomWidth: StyleSheet.hairlineWidth }
      : null,
  ];

  if (!onPress) {
    return (
      <View style={rowStyle} testID={testID}>
        {body}
      </View>
    );
  }

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole={accessibilityRole}
      accessibilityLabel={accessibilityLabel ?? title}
      testID={testID}
      style={({ pressed }) => [rowStyle, pressed ? { backgroundColor: theme.surfaceHover } : null]}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    minHeight: MIN_TOUCH + 12,
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
    paddingVertical: space.md,
  },
  textColumn: {
    flex: 1,
    gap: space.hair,
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
  },
  title: {
    fontSize: typeScale.base,
    fontWeight: "700",
    flexShrink: 1,
  },
  subtitle: {
    fontSize: typeScale.caption,
  },
  trailingColumn: {
    alignItems: "flex-end",
    gap: space.hair,
  },
  trailingCaption: {
    fontSize: typeScale.micro,
  },
});
