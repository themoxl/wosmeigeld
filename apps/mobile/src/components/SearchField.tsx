import { Ionicons } from "@expo/vector-icons";
import { useEffect, useRef, useState } from "react";
import { Pressable, StyleSheet, TextInput, View } from "react-native";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";

const DEBOUNCE_MS = 300;

type Props = {
  /** Aktueller Suchtext des Aufrufers, z. B. zum Zuruecksetzen von aussen. */
  value: string;
  /** Wird 300ms nach der letzten Eingabe aufgerufen (siehe Komponentenkommentar). */
  onChangeText: (text: string) => void;
  placeholder?: string;
  accessibilityLabel?: string;
  autoFocus?: boolean;
};

/**
 * Gemeinsames, loeschbares Suchfeld (Redesign-Plan Abschnitt 3 und 5, z. B.
 * fuer die Buchungsliste).
 *
 * Die Eingabe reagiert im Feld selbst sofort, meldet neue Suchtexte an den
 * Aufrufer aber erst 300ms nach der letzten Tastatureingabe - bei
 * serverseitiger Suche wuerde sonst jeder Tastendruck einen eigenen Request
 * ausloesen. Der X-Button ist eine eigene, bewusst nicht verzoegerte
 * Aktion: Loeschen soll sofort wirken, nicht erst nach 300ms.
 *
 * Der eingegebene Text lebt ausschliesslich in lokalem React-State dieser
 * Komponente. Es gibt hier keinerlei AsyncStorage-/Offline-Cache-Zugriff -
 * Suchbegriffe sind bewusst fluechtig und duerfen die App beim naechsten
 * Start nicht mit einer alten Suche oeffnen.
 */
export function SearchField({
  value,
  onChangeText,
  placeholder = "Suchen",
  accessibilityLabel = "Suche",
  autoFocus = false,
}: Props) {
  const theme = useTheme();
  const [text, setText] = useState(value);
  // Haelt fest, welchen Wert wir zuletzt selbst gemeldet haben, damit die
  // Sync-Effekt unten eine externe Ruecksetzung (z. B. "Filter
  // zuruecksetzen" an anderer Stelle) von unserer eigenen, verzoegerten
  // Meldung unterscheiden kann.
  const lastEmittedRef = useRef(value);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (value !== lastEmittedRef.current) {
      lastEmittedRef.current = value;
      setText(value);
    }
  }, [value]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  function emit(next: string) {
    lastEmittedRef.current = next;
    onChangeText(next);
  }

  function handleChangeText(next: string) {
    setText(next);
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      emit(next);
    }, DEBOUNCE_MS);
  }

  function handleClear() {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    setText("");
    emit("");
  }

  return (
    <View style={[styles.container, { borderColor: theme.border, backgroundColor: theme.surface }]}>
      <Ionicons name="search" size={18} color={theme.textSecondary} />
      <TextInput
        value={text}
        onChangeText={handleChangeText}
        placeholder={placeholder}
        // textSecondary statt textMuted: der Platzhalter ist hier kein
        // Schmuck, sondern die einzige Erklaerung, wonach gesucht werden kann.
        placeholderTextColor={theme.textSecondary}
        accessibilityLabel={accessibilityLabel}
        autoCorrect={false}
        autoCapitalize="none"
        returnKeyType="search"
        autoFocus={autoFocus}
        style={[styles.input, { color: theme.textPrimary }]}
      />
      {text.length > 0 ? (
        <Pressable
          onPress={handleClear}
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel="Suchtext löschen"
          style={styles.clearButton}
        >
          <Ionicons name="close-circle" size={18} color={theme.textSecondary} />
        </Pressable>
      ) : null}
    </View>
  );
}

// Optik der Karte, aber ohne Schatten: das Suchfeld sitzt im Showcase direkt
// unter der Kopfzeile und soll nicht ueber der Liste zu schweben scheinen.
const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    minHeight: MIN_TOUCH,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingLeft: space.md,
    paddingRight: space.sm,
    gap: space.sm,
  },
  input: {
    flex: 1,
    minHeight: MIN_TOUCH,
    fontSize: typeScale.base,
    paddingVertical: 0,
  },
  clearButton: {
    minWidth: MIN_TOUCH,
    minHeight: MIN_TOUCH,
    alignItems: "center",
    justifyContent: "center",
  },
});
