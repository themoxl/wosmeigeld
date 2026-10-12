import { Ionicons } from "@expo/vector-icons";
import { useMemo, useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import { MIN_TOUCH, radii, space, typeScale, useTheme } from "../theme";
import { formatMonthLabel } from "../utils/dates";
import { SheetModal } from "./SheetModal";
import { IconButton } from "./ui";

// Gemeinsamer Monatsnavigator im Handy-Designsystem (Showcase-Abschnitt
// "Month Switcher Carousel Header"): zwei runde Symbolknoepfe, dazwischen der
// Monatsname und darunter der abgedeckte Zeitraum. Der Zeitraum steht dort,
// weil "August" allein nicht sagt, ob ein Bericht den ganzen Monat oder nur
// die bisher vergangenen Tage umfasst.
//
// Die Schnittstelle (`year`/`month` als getrennte Zahlen, `month` 1-basiert)
// bleibt unveraendert, damit budget.tsx und buchungen.tsx nichts anpassen
// muessen.

export type MonthNavigatorProps = {
  /** Kalenderjahr des angezeigten Monats. */
  year: number;
  /** 1-basierter Monat (1 = Januar ... 12 = Dezember). */
  month: number;
  onChange: (year: number, month: number) => void;
  /** Anzahl Monate im Auswahl-Sheet, rueckwirkend ab `referenceDate`. Default 12. */
  monthsBack?: number;
  /** Nur fuer Tests: welcher Monat als "aktuell" fuer die Sheet-Liste gilt. Default `new Date()`. */
  referenceDate?: Date;
  style?: StyleProp<ViewStyle>;
};

type MonthOption = { year: number; month: number };

/** Verschiebt (year, month) um `delta` Monate, ueber Jahresgrenzen hinweg. */
function shiftMonth(year: number, month: number, delta: number): MonthOption {
  const totalMonths = year * 12 + (month - 1) + delta;
  const resultYear = Math.floor(totalMonths / 12);
  const resultMonth = ((totalMonths % 12) + 12) % 12;
  return { year: resultYear, month: resultMonth + 1 };
}

function buildRecentMonths(monthsBack: number, referenceDate: Date): MonthOption[] {
  const startYear = referenceDate.getFullYear();
  const startMonth = referenceDate.getMonth() + 1;
  const options: MonthOption[] = [];
  for (let i = 0; i < monthsBack; i++) {
    options.push(shiftMonth(startYear, startMonth, -i));
  }
  return options;
}

const rangeFormatter = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "short" });

/** "1. Aug. – 31. Aug." - der vom Monat abgedeckte Zeitraum. */
function formatMonthRange(year: number, month: number): string {
  const first = new Date(year, month - 1, 1);
  const last = new Date(year, month, 0);
  return `${rangeFormatter.format(first)} – ${rangeFormatter.format(last)}`;
}

export function MonthNavigator({
  year,
  month,
  onChange,
  monthsBack = 12,
  referenceDate,
  style,
}: MonthNavigatorProps) {
  const theme = useTheme();
  const [pickerOpen, setPickerOpen] = useState(false);

  const label = useMemo(() => formatMonthLabel(new Date(year, month - 1, 1)), [year, month]);
  const range = useMemo(() => formatMonthRange(year, month), [year, month]);

  const options = useMemo(
    () => buildRecentMonths(monthsBack, referenceDate ?? new Date()),
    [monthsBack, referenceDate],
  );

  const handlePrev = () => {
    const next = shiftMonth(year, month, -1);
    onChange(next.year, next.month);
  };

  const handleNext = () => {
    const next = shiftMonth(year, month, 1);
    onChange(next.year, next.month);
  };

  const handleSelect = (option: MonthOption) => {
    setPickerOpen(false);
    onChange(option.year, option.month);
  };

  return (
    <View style={[styles.container, style]}>
      <IconButton
        icon="chevron-back"
        onPress={handlePrev}
        accessibilityLabel="Vorheriger Monat"
        size={MIN_TOUCH}
        style={styles.arrowButton}
      />

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Monat wählen, aktuell ${label}`}
        accessibilityHint="Öffnet die Monatsauswahl"
        onPress={() => setPickerOpen(true)}
        style={({ pressed }) => [styles.monthButton, { opacity: pressed ? 0.6 : 1 }]}
      >
        <Text style={[styles.monthTitle, { color: theme.textPrimary }]}>{label}</Text>
        <Text style={[styles.monthRange, { color: theme.textSecondary }]}>{range}</Text>
      </Pressable>

      <IconButton
        icon="chevron-forward"
        onPress={handleNext}
        accessibilityLabel="Nächster Monat"
        size={MIN_TOUCH}
        style={styles.arrowButton}
      />

      <SheetModal visible={pickerOpen} title="Monat wählen" onClose={() => setPickerOpen(false)}>
        <ScrollView contentContainerStyle={styles.optionList} keyboardShouldPersistTaps="handled">
          {options.map((option) => {
            const optionLabel = formatMonthLabel(new Date(option.year, option.month - 1, 1));
            const selected = option.year === year && option.month === month;
            return (
              <Pressable
                key={`${option.year}-${option.month}`}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                accessibilityLabel={optionLabel}
                onPress={() => handleSelect(option)}
                style={({ pressed }) => [
                  styles.optionRow,
                  {
                    borderBottomColor: theme.border,
                    backgroundColor: pressed ? theme.surfaceHover : "transparent",
                  },
                ]}
              >
                <Text
                  style={[
                    styles.optionText,
                    {
                      color: selected ? theme.positive : theme.textPrimary,
                      fontWeight: selected ? "700" : "500",
                    },
                  ]}
                >
                  {optionLabel}
                </Text>
                {selected ? <Ionicons name="checkmark" size={18} color={theme.positive} /> : null}
              </Pressable>
            );
          })}
        </ScrollView>
      </SheetModal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  arrowButton: {
    minWidth: MIN_TOUCH,
    minHeight: MIN_TOUCH,
  },
  monthButton: {
    flex: 1,
    minHeight: MIN_TOUCH,
    alignItems: "center",
    justifyContent: "center",
    gap: space.hair,
  },
  monthTitle: {
    fontSize: typeScale.large,
    fontWeight: "800",
    letterSpacing: -0.3,
  },
  monthRange: {
    fontSize: typeScale.micro,
  },
  optionList: {
    paddingHorizontal: space.lg,
    paddingBottom: space.lg,
  },
  optionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: MIN_TOUCH + 4,
    paddingHorizontal: space.sm,
    borderRadius: radii.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  optionText: {
    fontSize: typeScale.base,
  },
});
