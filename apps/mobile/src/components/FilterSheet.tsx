import type { PropsWithChildren } from "react";
import { ScrollView, StyleSheet, View } from "react-native";
import { space } from "../theme";
import { PrimaryButton } from "./PrimaryButton";
import { SheetModal } from "./SheetModal";

type Props = PropsWithChildren<{
  visible: boolean;
  onClose: () => void;
  onReset: () => void;
  onApply: () => void;
  title?: string;
  resetLabel?: string;
  applyLabel?: string;
  /** Graut "Zuruecksetzen" aus, wenn ohnehin kein Filter aktiv ist. */
  resetDisabled?: boolean;
}>;

/**
 * Gemeinsame Sheet-Huelle fuer Filterdialoge (Redesign-Plan Abschnitt 3
 * und 5). Baut auf SheetModal auf und ergaenzt nur die feste Fusszeile mit
 * "Zuruecksetzen" und "Anwenden".
 *
 * Welche Filteroptionen es gibt (Monat, Konto, Kategorie, Richtung, ...)
 * bestimmt ausschliesslich der aufrufende Screen ueber `children` - diese
 * Komponente kennt keine fachlichen Filterfelder, nur die Huelle drumherum.
 */
export function FilterSheet({
  visible,
  onClose,
  onReset,
  onApply,
  title = "Filter",
  resetLabel = "Zurücksetzen",
  applyLabel = "Anwenden",
  resetDisabled = false,
  children,
}: Props) {
  return (
    <SheetModal
      visible={visible}
      title={title}
      onClose={onClose}
      footer={
        <View style={styles.footerRow}>
          <View style={styles.footerButton}>
            <PrimaryButton
              label={resetLabel}
              onPress={onReset}
              disabled={resetDisabled}
              variant="secondary"
            />
          </View>
          <View style={styles.footerButton}>
            <PrimaryButton label={applyLabel} onPress={onApply} variant="primary" />
          </View>
        </View>
      }
    >
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        {children}
      </ScrollView>
    </SheetModal>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    paddingTop: space.md,
    gap: space.xl,
  },
  footerRow: {
    flexDirection: "row",
    gap: space.md,
  },
  footerButton: {
    flex: 1,
  },
});
