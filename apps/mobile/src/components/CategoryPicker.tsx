import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { CategoryGroup } from "../api/types";
import { radii, space, typeScale, useTheme } from "../theme";
import { categoryPathById } from "../utils/categorySearch";
import { CategoryPickerModal } from "./CategoryPickerModal";

type Props = {
  groups: CategoryGroup[];
  value: string | null;
  onChange: (categoryId: string | null) => void;
  /** Betrag der Buchung in Cent - priorisiert passende Arten, versteckt nichts. */
  amountMinor?: number | null;
  title?: string;
};

/**
 * Eingebettetes Kategoriefeld fuer Formulare (z. B. die Buchungsdetailseite).
 *
 * Frueher war das eine ausgeschriebene, nicht durchsuchbare Gruppenliste
 * direkt im Formular. Mit dem Standardpaket (50 Kategorien) waere das eine
 * unbedienbar lange Seite geworden, und der Plan verlangt ausdruecklich, dass
 * "Buchungszeile, Detailseite, neue Buchung und Review denselben Suchpicker
 * benutzen". Deshalb ist es jetzt eine einzelne Zeile, die das durchsuchbare
 * Sheet oeffnet und den vollstaendigen Pfad `Gruppe · Kategorie` anzeigt.
 *
 * Die Zeile sieht bewusst aus wie ein <TextField> (gleicher Grund, gleicher
 * Radius, 48dp hoch) - sie steht in denselben Formularen direkt daneben.
 */
export function CategoryPicker({
  groups,
  value,
  onChange,
  amountMinor = null,
  title = "Kategorie wählen",
}: Props) {
  const theme = useTheme();
  const [sheetVisible, setSheetVisible] = useState(false);
  const label = categoryPathById(groups, value);

  return (
    <View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Kategorie: ${label}`}
        accessibilityHint="Oeffnet die durchsuchbare Kategorieauswahl."
        accessibilityState={{ expanded: sheetVisible }}
        onPress={() => setSheetVisible(true)}
        style={({ pressed }) => [
          styles.field,
          {
            borderColor: value ? theme.primaryStrong : theme.border,
            backgroundColor: theme.surfaceRaised,
            opacity: pressed ? 0.7 : 1,
          },
        ]}
      >
        <Text
          style={[styles.value, { color: value ? theme.textPrimary : theme.textSecondary }]}
          numberOfLines={2}
        >
          {label}
        </Text>
        <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
      </Pressable>

      <CategoryPickerModal
        visible={sheetVisible}
        groups={groups}
        value={value}
        amountMinor={amountMinor}
        title={title}
        onChange={(categoryId) => {
          setSheetVisible(false);
          onChange(categoryId);
        }}
        onClose={() => setSheetVisible(false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  field: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
    minHeight: 48,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
  },
  value: {
    flex: 1,
    fontSize: typeScale.base,
    fontWeight: "600",
  },
});
