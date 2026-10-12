import DateTimePicker, { type DateTimePickerEvent } from "@react-native-community/datetimepicker";
import { Ionicons } from "@expo/vector-icons";
import { createElement, useState, type ChangeEvent } from "react";
import { Platform, Pressable, StyleSheet, Text, useColorScheme, View } from "react-native";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";

const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export function dateToIso(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function isoToLocalDate(value: string): Date {
  if (!ISO_DATE_PATTERN.test(value)) return new Date();
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(5, 7));
  const day = Number(value.slice(8, 10));
  const date = new Date(year, month - 1, day, 12, 0, 0, 0);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

export function formatIsoDateForDisplay(value: string): string {
  if (!ISO_DATE_PATTERN.test(value)) return "Datum auswählen";
  return new Intl.DateTimeFormat("de-DE", {
    day: "2-digit",
    month: "long",
    year: "numeric",
  }).format(isoToLocalDate(value));
}

type Props = {
  label: string;
  value: string;
  onChange: (isoDate: string) => void;
  disabled?: boolean;
  minimumDate?: string;
  maximumDate?: string;
  error?: string;
};

/**
 * Plattformgerechte Datumsauswahl mit stabiler ISO-Schnittstelle.
 * Android/iOS verwenden den nativen Community-Picker; Expo Web bekommt den
 * nativen HTML-Date-Input des Browsers. Dadurch bleibt die Server-Nutzlast
 * YYYY-MM-DD, ohne Nutzerinnen und Nutzer mit ISO-Freitext zu belasten.
 */
export function IsoDatePickerField({
  label,
  value,
  onChange,
  disabled = false,
  minimumDate,
  maximumDate,
  error,
}: Props) {
  const theme = useTheme();
  const colorScheme = useColorScheme();
  const [open, setOpen] = useState(false);
  const selectedDate = isoToLocalDate(value);

  if (Platform.OS === "web") {
    return (
      <View style={styles.field}>
        <Text style={[styles.label, { color: theme.textSecondary }]}>{label}</Text>
        {createElement("input", {
          "aria-label": label,
          type: "date",
          value,
          min: minimumDate,
          max: maximumDate,
          disabled,
          onChange: (event: ChangeEvent<HTMLInputElement>) => onChange(event.currentTarget.value),
          style: {
            boxSizing: "border-box",
            minHeight: 48,
            width: "100%",
            borderWidth: 1,
            borderStyle: "solid",
            borderColor: error ? theme.warning : theme.border,
            borderRadius: radii.md,
            padding: `0 ${space.md}px`,
            fontFamily: "inherit",
            fontSize: typeScale.base,
            color: theme.textPrimary,
            backgroundColor: theme.surface,
            colorScheme: colorScheme === "dark" ? "dark" : "light",
          },
        })}
        {error ? (
          <Text accessibilityRole="alert" style={[styles.error, { color: theme.warning }]}>
            {error}
          </Text>
        ) : null}
      </View>
    );
  }

  const handleChange = (event: DateTimePickerEvent, nextDate?: Date) => {
    if (Platform.OS === "android") setOpen(false);
    if (event.type === "set" && nextDate) onChange(dateToIso(nextDate));
  };

  return (
    <View style={styles.field}>
      <Text style={[styles.label, { color: theme.textSecondary }]}>{label}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${formatIsoDateForDisplay(value)}`}
        accessibilityState={{ disabled, expanded: open }}
        disabled={disabled}
        onPress={() => setOpen(true)}
        style={({ pressed }) => [
          styles.trigger,
          {
            borderColor: error ? theme.warning : theme.border,
            backgroundColor: theme.surface,
            opacity: disabled ? 0.55 : pressed ? 0.7 : 1,
          },
        ]}
      >
        <View style={styles.triggerCopy}>
          <Text style={[styles.dateValue, { color: theme.textPrimary }]}>
            {formatIsoDateForDisplay(value)}
          </Text>
          <Text style={[styles.hint, { color: theme.textMuted }]}>Antippen zum Ändern</Text>
        </View>
        <Ionicons
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
          name="calendar-outline"
          size={22}
          color={theme.primary}
        />
      </Pressable>
      {open ? (
        <View
          style={[
            styles.pickerPanel,
            { borderColor: theme.border, backgroundColor: theme.surface },
          ]}
        >
          <DateTimePicker
            value={selectedDate}
            mode="date"
            display={Platform.OS === "ios" ? "inline" : "default"}
            locale="de-DE"
            themeVariant={colorScheme === "dark" ? "dark" : "light"}
            minimumDate={minimumDate ? isoToLocalDate(minimumDate) : undefined}
            maximumDate={maximumDate ? isoToLocalDate(maximumDate) : undefined}
            onChange={handleChange}
          />
          {Platform.OS === "ios" ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Datumsauswahl schließen"
              onPress={() => setOpen(false)}
              style={[styles.doneButton, { borderTopColor: theme.border }]}
            >
              <Text style={[styles.doneLabel, { color: theme.positive }]}>Fertig</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
      {error ? (
        <Text accessibilityRole="alert" style={[styles.error, { color: theme.warning }]}>
          {error}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: space.xs },
  label: { fontSize: typeScale.body, fontWeight: "600" },
  trigger: {
    minHeight: 52,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  triggerCopy: { flex: 1, gap: space.hair },
  dateValue: { fontSize: typeScale.base, fontWeight: "700" },
  hint: { fontSize: typeScale.micro },
  pickerPanel: { borderWidth: 1, borderRadius: radii.md, overflow: "hidden" },
  doneButton: {
    minHeight: MIN_TOUCH,
    borderTopWidth: StyleSheet.hairlineWidth,
    alignItems: "center",
    justifyContent: "center",
  },
  doneLabel: { fontSize: typeScale.base, fontWeight: "700" },
  error: { fontSize: typeScale.body },
});
