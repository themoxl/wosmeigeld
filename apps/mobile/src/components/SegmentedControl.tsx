import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";
import { elevation, MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";

// Gemeinsamer Segmented Control, Gestaltung nach
// docs/mobile_redesign_showcase.html (".segmented-tabs"): eine gerahmte
// Schiene, in der nur das GEWAEHLTE Segment als erhabene Flaeche sitzt.
// Anders als bei einzelnen Umschaltknoepfen sieht man dadurch auf einen Blick,
// dass sich die Optionen gegenseitig ausschliessen. Der umschliessende
// Container traegt die Rolle "tablist", damit Screenreader die Gruppe als
// zusammengehoerige Tabs statt einzelner Buttons ansagen.

export type SegmentedControlOption<TValue extends string> = {
  value: TValue;
  label: string;
  /** Sprechenderes Accessibility-Label als der sichtbare Text, falls noetig. */
  accessibilityLabel?: string;
};

export type SegmentedControlProps<TValue extends string> = {
  options: SegmentedControlOption<TValue>[];
  value: TValue;
  onChange: (value: TValue) => void;
  /** Name der gesamten Tab-Gruppe fuer Screenreader, z. B. "Ausgaben oder Einnahmen". */
  accessibilityLabel?: string;
  style?: StyleProp<ViewStyle>;
};

export function SegmentedControl<TValue extends string>({
  options,
  value,
  onChange,
  accessibilityLabel,
  style,
}: SegmentedControlProps<TValue>) {
  const theme = useTheme();

  return (
    <View
      accessibilityRole="tablist"
      accessibilityLabel={accessibilityLabel}
      style={[
        styles.container,
        { backgroundColor: theme.surface, borderColor: theme.border },
        style,
      ]}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            accessibilityLabel={option.accessibilityLabel ?? option.label}
            onPress={() => onChange(option.value)}
            style={({ pressed }) => [
              styles.segment,
              selected
                ? [styles.segmentSelected, elevation.card, { backgroundColor: theme.surfaceRaised }]
                : null,
              { opacity: pressed ? 0.7 : 1 },
            ]}
          >
            <Text
              numberOfLines={1}
              style={[
                styles.label,
                {
                  color: selected ? theme.textPrimary : theme.textSecondary,
                  fontWeight: selected ? "700" : "600",
                },
              ]}
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
    flexDirection: "row",
    gap: space.hair,
    padding: space.hair,
    borderWidth: 1,
    borderRadius: radii.md,
  },
  segment: {
    flex: 1,
    // Tippziel aus dem Designsystem (MIN_TOUCH), nicht die reine optische
    // Hoehe der Schiene - ein Segment ist ein Bedienelement und muss auch
    // dann mit dem Daumen sicher zu treffen sein, wenn das Label kurz ist.
    minHeight: MIN_TOUCH,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.sm,
    paddingHorizontal: space.xs,
  },
  segmentSelected: {
    borderRadius: radii.sm,
  },
  label: {
    fontSize: typeScale.caption,
  },
});
