import { Pressable, StyleSheet, Text, View } from "react-native";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";

export type Option<T extends string> = {
  value: T;
  label: string;
};

type Props<T extends string> = {
  options: Option<T>[];
  value: T | null;
  onChange: (value: T) => void;
  /** Name der Gruppe fuer Screenreader, z. B. "Konto". */
  accessibilityLabel?: string;
};

/**
 * Einfache eingebettete Auswahlliste (kein natives Picker-/Modal-Widget
 * noetig) fuer kurze, feste Optionsmengen wie Kontotyp oder Ausgabe/Einnahme.
 *
 * Die getroffene Wahl wird durch ein Haekchen UND die Farbe markiert - eine
 * rein farbige Hervorhebung waere fuer farbfehlsichtige Nutzer nicht lesbar.
 */
export function OptionList<T extends string>({
  options,
  value,
  onChange,
  accessibilityLabel,
}: Props<T>) {
  const theme = useTheme();

  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={accessibilityLabel}
      style={styles.container}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="radio"
            accessibilityState={{ selected }}
            accessibilityLabel={option.label}
            onPress={() => onChange(option.value)}
            style={({ pressed }) => [
              styles.row,
              {
                borderColor: selected ? theme.primaryStrong : theme.border,
                backgroundColor: selected ? theme.positiveMuted : theme.surfaceRaised,
                opacity: pressed ? 0.7 : 1,
              },
            ]}
          >
            <Text
              style={[styles.check, { color: selected ? theme.positive : "transparent" }]}
              importantForAccessibility="no"
            >
              {"✓"}
            </Text>
            <Text
              style={[styles.label, { color: selected ? theme.positive : theme.textPrimary }]}
              numberOfLines={2}
            >
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: space.sm,
  },
  row: {
    minHeight: MIN_TOUCH + 4,
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
  },
  check: {
    width: 16,
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  label: {
    flex: 1,
    fontSize: typeScale.base,
    fontWeight: "600",
  },
});
