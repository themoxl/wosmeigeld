import { ScrollView, StyleSheet } from "react-native";
import type { SavingsGoal } from "../api/types";
import { space } from "../theme";
import { GoalPicker } from "./GoalPicker";
import { SheetModal } from "./SheetModal";

type Props = {
  visible: boolean;
  goals: SavingsGoal[];
  value: string | null;
  onChange: (goalId: string | null) => void;
  onClose: () => void;
  title?: string;
};

/**
 * Sparziel-Auswahl je Split auf der Buchungs-Detailseite.
 *
 * Die Sheet-Huelle (Backdrop, Kopfzeile, "Schliessen") lag frueher als exakte
 * Kopie hier und in CategoryPickerModal; beide verwenden jetzt SheetModal.
 * Sparziele sind eine kurze Liste und brauchen keine Suche - deshalb bleibt
 * es hier beim klassischen Bottom-Sheet statt der vollflaechigen Variante.
 */
export function GoalPickerModal({
  visible,
  goals,
  value,
  onChange,
  onClose,
  title = "Sparziel wählen",
}: Props) {
  return (
    <SheetModal visible={visible} title={title} onClose={onClose}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <GoalPicker goals={goals} value={value} onChange={onChange} />
      </ScrollView>
    </SheetModal>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    paddingTop: 0,
    gap: space.md,
  },
});
