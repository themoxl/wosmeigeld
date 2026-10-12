import type { PropsWithChildren } from "react";
import { ScrollView, StyleSheet } from "react-native";
import { space } from "../theme";
import { SheetModal } from "./SheetModal";

type Props = PropsWithChildren<{
  visible: boolean;
  onClose: () => void;
  title?: string;
}>;

/**
 * Gemeinsame Sheet-Huelle fuer Sortierdialoge (Redesign-Plan Abschnitt 3
 * und 5).
 *
 * Anders als FilterSheet braucht Sortieren keine feste Fusszeile: eine
 * Auswahl (typischerweise per OptionList in `children`) gilt sofort, der
 * aufrufende Screen schliesst das Sheet dafuer meist direkt im eigenen
 * onChange. Diese Komponente bleibt deshalb bewusst duenn und liefert nur
 * Titel sowie gepolsterten, scrollbaren Inhalt ueber SheetModal - welche
 * Sortieroptionen es gibt, bestimmt weiterhin ausschliesslich der Screen.
 */
export function SortSheet({ visible, onClose, title = "Sortieren", children }: Props) {
  return (
    <SheetModal visible={visible} title={title} onClose={onClose}>
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
    gap: space.md,
  },
});
