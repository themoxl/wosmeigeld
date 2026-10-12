import { useQueryClient } from "@tanstack/react-query";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { createMeal } from "../src/api/client";
import type { MealType } from "../src/api/types";
import { useAuth } from "../src/auth/AuthProvider";
import { IsoDatePickerField } from "../src/components/IsoDatePickerField";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { TextField } from "../src/components/TextField";
import { Card, CardLabel, Chip, ChipRow } from "../src/components/ui";
import { useHousehold } from "../src/household/HouseholdProvider";
import { space, typeScale, useTheme } from "../src/theme";
import { parseAmountToMinorUnits } from "../src/utils/money";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const MEAL_TYPE_OPTIONS: { value: MealType; label: string }[] = [
  { value: "breakfast", label: "Frühstück" },
  { value: "lunch", label: "Mittagessen" },
  { value: "dinner", label: "Abendessen" },
  { value: "snack", label: "Snack" },
];

// Formular zum Anlegen einer Mahlzeit (Phase 8, Architekturplan Abschnitt
// 6.8). Von app/mehr/essensplan.tsx aus erreichbar, weekId als Query-Param.
export default function NeueMahlzeitScreen() {
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const { weekId } = useLocalSearchParams<{ weekId: string }>();

  const [mealDate, setMealDate] = useState("");
  const [mealType, setMealType] = useState<MealType>("dinner");
  const [dishName, setDishName] = useState("");
  const [servingsText, setServingsText] = useState("2");
  const [estimatedText, setEstimatedText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fieldStyle = { backgroundColor: theme.surfaceRaised };

  async function handleCreate() {
    setError(null);

    const trimmedDish = dishName.trim();
    if (trimmedDish.length === 0) {
      setError("Bitte ein Gericht eingeben.");
      return;
    }
    if (!DATE_PATTERN.test(mealDate.trim())) {
      setError("Bitte ein Datum auswählen.");
      return;
    }
    if (!accessToken || !selectedHouseholdId || !weekId) {
      setError("Keine Woche ausgewählt.");
      return;
    }

    let estimatedCostMinor: number | undefined;
    if (estimatedText.trim().length > 0) {
      try {
        estimatedCostMinor = parseAmountToMinorUnits(estimatedText);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Ungültige geschätzte Kosten.");
        return;
      }
    }

    const servingsNumber = Number.parseInt(servingsText, 10);

    setSubmitting(true);
    try {
      await createMeal(accessToken, selectedHouseholdId, {
        meal_week_id: weekId,
        meal_date: mealDate.trim(),
        meal_type: mealType,
        dish_name: trimmedDish,
        servings: Number.isFinite(servingsNumber) && servingsNumber > 0 ? servingsNumber : 1,
        estimated_cost_minor: estimatedCostMinor,
      });
      await queryClient.invalidateQueries({ queryKey: ["meals", weekId] });
      await queryClient.invalidateQueries({ queryKey: ["meal-week-summary", weekId] });
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Mahlzeit konnte nicht angelegt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Neue Mahlzeit" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Card>
            <CardLabel>Wann</CardLabel>
            <IsoDatePickerField label="Datum" value={mealDate} onChange={setMealDate} />
            <View style={styles.chipBlock}>
              <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Mahlzeit</Text>
              <ChipRow>
                {MEAL_TYPE_OPTIONS.map((option) => (
                  <Chip
                    key={option.value}
                    label={option.label}
                    selected={option.value === mealType}
                    onPress={() => setMealType(option.value)}
                    accessibilityLabel={`Mahlzeit: ${option.label}`}
                  />
                ))}
              </ChipRow>
            </View>
          </Card>

          <Card>
            <CardLabel>Gericht</CardLabel>
            <TextField
              label="Gericht"
              value={dishName}
              onChangeText={setDishName}
              placeholder="z. B. Spaghetti Bolognese"
              style={fieldStyle}
            />
            <TextField
              label="Portionen"
              value={servingsText}
              onChangeText={setServingsText}
              keyboardType="number-pad"
              style={fieldStyle}
            />
            <TextField
              label="Geschätzte Kosten in EUR (optional)"
              value={estimatedText}
              onChangeText={setEstimatedText}
              placeholder="12,00"
              keyboardType="numbers-and-punctuation"
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
            label="Mahlzeit anlegen"
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
  chipBlock: {
    gap: space.xs,
  },
  fieldLabel: {
    fontSize: typeScale.body,
    fontWeight: "500",
  },
  error: {
    fontSize: typeScale.body,
  },
});
