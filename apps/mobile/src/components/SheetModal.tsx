import type { PropsWithChildren, ReactNode } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { elevation, radii, space, typeScale, useTheme } from "../theme";

type Props = PropsWithChildren<{
  visible: boolean;
  title: string;
  onClose: () => void;
  /**
   * "sheet" = klassisches Bottom-Sheet (max. 85% Hoehe) fuer kurze Listen.
   * "full" = nahezu vollflaechig, fuer Inhalte mit feststehender Suche - der
   * Plan verlangt das ausdruecklich fuer die Kategorieauswahl.
   */
  size?: "sheet" | "full";
  /** Direkt unter dem Kopf, ausserhalb des scrollenden Bereichs (z. B. Suchfeld). */
  header?: ReactNode;
  /** Ueber dem unteren Rand fixiert (z. B. eine Aktionszeile). */
  footer?: ReactNode;
}>;

/**
 * Gemeinsame Huelle aller Sheets der App.
 *
 * Gestaltung nach docs/mobile_redesign_showcase.html: 28px Radius oben, ein
 * Ziehgriff als Aufforderung zum Wegwischen, kraeftig abgedunkelter Grund.
 * Der Griff ist bewusst nur Signal - gezogen wird nicht, weil das Projekt
 * ohne react-native-gesture-handler auskommt (siehe SwipeableRow, das
 * ebenfalls nur Animated/PanResponder verwendet). Ein Tipp auf den Grund
 * schliesst, und der Titel traegt einen echten Schliessen-Knopf.
 */
export function SheetModal({
  visible,
  title,
  onClose,
  size = "sheet",
  header,
  footer,
  children,
}: Props) {
  const theme = useTheme();
  // Ein RN-<Modal> deckt den GANZEN Bildschirm ab - die Safe-Area des
  // umgebenden Screens gilt hier nicht mehr. Ohne diesen Abstand liegen der
  // Speichern-Knopf im Fuss bzw. die letzte Listenzeile unter dem
  // iOS-Home-Indicator (34dp) oder der Android-Gestenleiste.
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, space.sm);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable
        style={[
          size === "full" ? styles.backdropThin : styles.backdrop,
          { backgroundColor: theme.overlay },
        ]}
        accessibilityRole="button"
        accessibilityLabel="Schließen"
        onPress={onClose}
      />
      <View
        style={[
          size === "full" ? styles.sheetFull : styles.sheet,
          elevation.sheet,
          { backgroundColor: theme.chrome, borderColor: theme.borderStrong },
        ]}
      >
        <View style={[styles.handle, { backgroundColor: theme.textMuted }]} />
        <View style={styles.headerRow}>
          <Text style={[styles.title, { color: theme.textPrimary }]} numberOfLines={1}>
            {title}
          </Text>
          <Pressable
            onPress={onClose}
            hitSlop={12}
            accessibilityRole="button"
            accessibilityLabel="Auswahl schließen"
            style={styles.closeButton}
          >
            <Text style={[styles.closeLabel, { color: theme.textSecondary }]}>Schließen</Text>
          </Pressable>
        </View>
        {header}
        <View style={[styles.body, footer ? null : { paddingBottom: bottomInset }]}>
          {children}
        </View>
        {footer ? (
          <View
            style={[styles.footer, { borderTopColor: theme.border, paddingBottom: bottomInset }]}
          >
            {footer}
          </View>
        ) : null}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
  },
  // Nahezu vollflaechig: nur ein schmaler Streifen bleibt zum Wegtippen.
  backdropThin: {
    height: 56,
  },
  sheet: {
    maxHeight: "85%",
    borderTopWidth: 1,
    borderTopLeftRadius: radii.xl,
    borderTopRightRadius: radii.xl,
    paddingTop: space.sm,
  },
  sheetFull: {
    flex: 1,
    borderTopWidth: 1,
    borderTopLeftRadius: radii.xl,
    borderTopRightRadius: radii.xl,
    paddingTop: space.sm,
  },
  handle: {
    width: 40,
    height: 4,
    borderRadius: 2,
    alignSelf: "center",
    opacity: 0.6,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.md,
    paddingHorizontal: space.xl,
    paddingVertical: space.md,
  },
  title: {
    flex: 1,
    fontSize: typeScale.large,
    fontWeight: "700",
  },
  closeButton: {
    minHeight: 44,
    justifyContent: "center",
  },
  closeLabel: {
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  body: {
    flexShrink: 1,
  },
  footer: {
    borderTopWidth: StyleSheet.hairlineWidth,
    padding: space.lg,
    gap: space.sm,
  },
});
