import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../../theme";

type ChipProps = {
  label: string;
  selected?: boolean;
  onPress?: () => void;
  accessibilityLabel?: string;
  testID?: string;
};

/** Auswahlpille (Schnellkategorien im Erfassungs-Sheet, Filterwerte). */
export function Chip({ label, selected = false, onPress, accessibilityLabel, testID }: ChipProps) {
  const theme = useTheme();

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      accessibilityLabel={accessibilityLabel ?? label}
      testID={testID}
      style={({ pressed }) => [
        styles.chip,
        {
          // primaryDeep statt primaryStrong: die gewaehlte Pille traegt
          // weissen Text, und der erreicht auf primaryStrong nur 2,3:1
          // (dunkel) bzw. 3,3:1 (hell) - die getroffene Wahl waere damit
          // schlechter lesbar als jede nicht getroffene.
          backgroundColor: selected ? theme.primaryDeep : theme.surface,
          borderColor: selected ? theme.primaryDeep : theme.border,
          opacity: pressed ? 0.75 : 1,
        },
      ]}
    >
      <Text
        style={[styles.label, { color: selected ? theme.onAccent : theme.textPrimary }]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </Pressable>
  );
}

/** Waagerecht scrollende Pillenreihe mit randlosem Anschnitt. */
export function ChipRow({ children }: { children: React.ReactNode }) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.row}
    >
      <View style={styles.row}>{children}</View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  chip: {
    minHeight: MIN_TOUCH,
    paddingHorizontal: space.md + 2,
    justifyContent: "center",
    borderRadius: radii.md,
    borderWidth: 1,
  },
  label: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  row: {
    flexDirection: "row",
    gap: space.sm,
    alignItems: "center",
  },
});
