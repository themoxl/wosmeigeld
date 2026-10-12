import { useState } from "react";
import { StyleSheet, Text, TextInput, View, type TextInputProps } from "react-native";
import { radii, space, typeScale, useTheme } from "../theme";

type Props = TextInputProps & {
  label: string;
  error?: string;
};

/**
 * Text-Eingabefeld mit Beschriftung und optionalem Fehlertext - die Grundlage
 * jedes Formulars (Anmeldung, Konto, Buchung).
 *
 * Der Feldgrund ist `surfaceRaised`, nicht `surface`: das Feld sitzt in aller
 * Regel INNERHALB einer <Card>, und eine Flaeche in Kartenfarbe waere dort
 * unsichtbar. Der Fokusrahmen wechselt nur die Farbe und nicht die Randbreite,
 * damit die Zeile beim Antippen nicht springt.
 */
export function TextField({ label, error, style, onFocus, onBlur, ...inputProps }: Props) {
  const theme = useTheme();
  const [focused, setFocused] = useState(false);

  const borderColor = error ? theme.warning : focused ? theme.focusRing : theme.border;

  return (
    <View style={styles.container}>
      <Text style={[styles.label, { color: theme.textSecondary }]}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor={theme.textSecondary}
        onFocus={(event) => {
          setFocused(true);
          onFocus?.(event);
        }}
        onBlur={(event) => {
          setFocused(false);
          onBlur?.(event);
        }}
        style={[
          styles.input,
          inputProps.multiline ? styles.inputMultiline : null,
          {
            borderColor,
            color: theme.textPrimary,
            backgroundColor: theme.surfaceRaised,
          },
          style,
        ]}
        {...inputProps}
      />
      {error ? (
        <Text accessibilityRole="alert" style={[styles.error, { color: theme.warning }]}>
          {error}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: space.xs,
  },
  label: {
    fontSize: typeScale.caption,
    fontWeight: "600",
  },
  input: {
    minHeight: 48,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    fontSize: typeScale.base,
  },
  inputMultiline: {
    minHeight: 88,
    paddingTop: space.md,
    paddingBottom: space.md,
    textAlignVertical: "top",
  },
  error: {
    fontSize: typeScale.caption,
  },
});
