import { useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text } from "react-native";
import { createMealWeek } from "../src/api/client";
import { useAuth } from "../src/auth/AuthProvider";
import { dateToIso, IsoDatePickerField } from "../src/components/IsoDatePickerField";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { TextField } from "../src/components/TextField";
import { Card, CardLabel } from "../src/components/ui";
import { useHousehold } from "../src/household/HouseholdProvider";
import { space, typeScale, useTheme } from "../src/theme";
import { parseAmountToMinorUnits } from "../src/utils/money";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

// dateToIso() statt toISOString(): letzteres rechnet in UTC um und lieferte
// oestlich von Greenwich den Sonntag davor statt des Montags.
function mondayOfCurrentWeek(): string {
  const today = new Date();
  const day = today.getDay();
  const diff = (day === 0 ? -6 : 1) - day;
  const monday = new Date(today);
  monday.setDate(today.getDate() + diff);
  return dateToIso(monday);
}

// Formular zum Anlegen einer Essensplan-Woche (Phase 8, Architekturplan
// Abschnitt 6.8). Von app/mehr/essensplan.tsx und app/mehr/einkaufsliste.tsx
// aus erreichbar.
export default function NeueLebensmittelWocheScreen() {
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const [weekStart, setWeekStart] = useState(mondayOfCurrentWeek());
  const [budgetText, setBudgetText] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fieldStyle = { backgroundColor: theme.surfaceRaised };

  async function handleCreate() {
    setError(null);

    if (!DATE_PATTERN.test(weekStart.trim())) {
      setError("Bitte einen Wochenstart auswählen.");
      return;
    }
    if (!accessToken || !selectedHouseholdId) {
      setError("Kein Haushalt ausgewählt.");
      return;
    }

    let budgetMinor = 0;
    if (budgetText.trim().length > 0) {
      try {
        budgetMinor = parseAmountToMinorUnits(budgetText);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Ungültiges Wochenbudget.");
        return;
      }
    }

    setSubmitting(true);
    try {
      await createMealWeek(accessToken, selectedHouseholdId, {
        week_start: weekStart.trim(),
        budget_minor: budgetMinor,
        note: note.trim() || undefined,
      });
      await queryClient.invalidateQueries({ queryKey: ["meal-weeks", selectedHouseholdId] });
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Woche konnte nicht angelegt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Neue Woche" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Card>
            <CardLabel>Planwoche</CardLabel>
            <IsoDatePickerField
              label="Wochenstart (Montag)"
              value={weekStart}
              onChange={setWeekStart}
            />
            <TextField
              label="Wochenbudget in EUR (optional)"
              value={budgetText}
              onChangeText={setBudgetText}
              placeholder="100,00"
              keyboardType="numbers-and-punctuation"
              style={fieldStyle}
            />
            <TextField
              label="Notiz (optional)"
              value={note}
              onChangeText={setNote}
              placeholder="z. B. Besuch am Wochenende"
              style={fieldStyle}
            />
          </Card>

          {error ? (
            <Card tone="warning" density="compact">
              <Text accessibilityRole="alert" style={[styles.error, { color: theme.warning }]}>
                {error}
              </Text>
            </Card>
          ) : null}

          <PrimaryButton
            label="Woche anlegen"
            onPress={() => void handleCreate()}
            loading={submitting}
          />
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.lg,
  },
  error: {
    fontSize: typeScale.body,
  },
});
