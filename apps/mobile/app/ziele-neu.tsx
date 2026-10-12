import { useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { createSavingsGoal, createWishlistItem } from "../src/api/client";
import { useAuth } from "../src/auth/AuthProvider";
import { IsoDatePickerField } from "../src/components/IsoDatePickerField";
import { OptionList } from "../src/components/OptionList";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { TextField } from "../src/components/TextField";
import { Card } from "../src/components/ui";
import { useHousehold } from "../src/household/HouseholdProvider";
import { MIN_TOUCH, space, typeScale, useTheme } from "../src/theme";
import { parseAmountToMinorUnits } from "../src/utils/money";

type Kind = "goal" | "wish";
type PlanningMode = "target_date" | "monthly_amount";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

// Formular zum Anlegen eines Sparziels oder Wunsches (Phase 7, Architekturplan
// Abschnitt 11.2). Von app/(tabs)/ziele.tsx aus erreichbar.
export default function NeuesZielScreen() {
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const [kind, setKind] = useState<Kind>("goal");
  const [name, setName] = useState("");
  const [targetAmountText, setTargetAmountText] = useState("");
  const [startingAmountText, setStartingAmountText] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [planningMode, setPlanningMode] = useState<PlanningMode>("target_date");
  const [monthlyContributionText, setMonthlyContributionText] = useState("");
  const [includeInPlanning, setIncludeInPlanning] = useState(true);
  const [priceText, setPriceText] = useState("");
  const [priority, setPriority] = useState("1");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    setError(null);

    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setError("Bitte eine Bezeichnung eingeben.");
      return;
    }
    if (!accessToken || !selectedHouseholdId) {
      setError("Kein Haushalt ausgewählt.");
      return;
    }

    setSubmitting(true);
    try {
      if (kind === "goal") {
        let targetAmountMinor: number;
        try {
          targetAmountMinor = parseAmountToMinorUnits(targetAmountText);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : "Ungültiger Zielbetrag.");
          return;
        }
        if (targetAmountMinor <= 0) {
          setError("Der Zielbetrag muss positiv sein.");
          return;
        }
        let startingAmountMinor = 0;
        if (startingAmountText.trim().length > 0) {
          startingAmountMinor = parseAmountToMinorUnits(startingAmountText);
        }
        const trimmedDate = targetDate.trim();
        let monthlyContributionMinor: number | null = null;
        if (planningMode === "target_date") {
          if (!DATE_PATTERN.test(trimmedDate)) {
            setError("Für 'Bis Datum sparen' ist ein Zieldatum im Format JJJJ-MM-TT Pflicht.");
            return;
          }
        } else {
          try {
            monthlyContributionMinor = parseAmountToMinorUnits(monthlyContributionText);
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Monatsbetrag ist ungültig.");
            return;
          }
          if (monthlyContributionMinor <= 0) {
            setError("Der feste Monatsbetrag muss positiv sein.");
            return;
          }
        }

        await createSavingsGoal(accessToken, selectedHouseholdId, {
          name: trimmedName,
          target_amount_minor: targetAmountMinor,
          starting_amount_minor: startingAmountMinor,
          planning_mode: planningMode,
          target_date: planningMode === "target_date" ? trimmedDate : null,
          monthly_contribution_minor:
            planningMode === "monthly_amount" ? monthlyContributionMinor : null,
          include_in_planning: includeInPlanning,
        });
        await queryClient.invalidateQueries({ queryKey: ["goals", selectedHouseholdId] });
        await queryClient.invalidateQueries({ queryKey: ["budget", selectedHouseholdId] });
        await queryClient.invalidateQueries({ queryKey: ["reserves", selectedHouseholdId] });
      } else {
        let priceMinor: number;
        try {
          priceMinor = parseAmountToMinorUnits(priceText);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : "Ungültiger Preis.");
          return;
        }
        const priorityNumber = Number.parseInt(priority, 10);

        await createWishlistItem(accessToken, selectedHouseholdId, {
          name: trimmedName,
          price_minor: priceMinor,
          priority: Number.isFinite(priorityNumber) ? priorityNumber : 1,
          note: note.trim() || null,
        });
        await queryClient.invalidateQueries({ queryKey: ["wishlist", selectedHouseholdId] });
      }
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Eintrag konnte nicht erstellt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Neues Ziel" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.field}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>Art</Text>
            <OptionList
              options={[
                { value: "goal", label: "Sparziel" },
                { value: "wish", label: "Wunsch" },
              ]}
              value={kind}
              onChange={setKind}
            />
          </View>

          <TextField
            label="Bezeichnung"
            value={name}
            onChangeText={setName}
            placeholder={kind === "goal" ? "z. B. Notgroschen" : "z. B. Neues Fahrrad"}
          />

          {kind === "goal" ? (
            <>
              <TextField
                label="Zielbetrag in EUR"
                value={targetAmountText}
                onChangeText={setTargetAmountText}
                placeholder="1000,00"
                keyboardType="numbers-and-punctuation"
              />
              <TextField
                label="Startbetrag in EUR (optional)"
                value={startingAmountText}
                onChangeText={setStartingAmountText}
                placeholder="0,00"
                keyboardType="numbers-and-punctuation"
              />
              <View style={styles.field}>
                <Text style={[styles.label, { color: theme.textSecondary }]}>Planungsmodus</Text>
                <OptionList
                  options={[
                    { value: "target_date", label: "Bis Datum sparen" },
                    { value: "monthly_amount", label: "Fester Monatsbetrag" },
                  ]}
                  value={planningMode}
                  onChange={setPlanningMode}
                />
              </View>
              {planningMode === "target_date" ? (
                <IsoDatePickerField label="Zieldatum" value={targetDate} onChange={setTargetDate} />
              ) : (
                <TextField
                  label="Fester Monatsbetrag in EUR"
                  value={monthlyContributionText}
                  onChangeText={setMonthlyContributionText}
                  placeholder="50,00"
                  keyboardType="numbers-and-punctuation"
                />
              )}
              <View style={[styles.switchRow, { borderTopColor: theme.border }]}>
                <Text style={[styles.switchLabel, { color: theme.textPrimary }]}>
                  In Monatsplanung einrechnen
                </Text>
                <Switch
                  accessibilityLabel="In Monatsplanung einrechnen"
                  value={includeInPlanning}
                  onValueChange={setIncludeInPlanning}
                  trackColor={{ false: theme.border, true: theme.primaryStrong }}
                />
              </View>
            </>
          ) : (
            <>
              <TextField
                label="Preis in EUR"
                value={priceText}
                onChangeText={setPriceText}
                placeholder="199,00"
                keyboardType="numbers-and-punctuation"
              />
              <TextField
                label="Priorität (1 = höchste, 5 = niedrigste)"
                value={priority}
                onChangeText={setPriority}
                placeholder="1"
                keyboardType="number-pad"
              />
              <TextField
                label="Notiz"
                value={note}
                onChangeText={setNote}
                placeholder="Warum ist dir dieser Wunsch wichtig?"
                multiline
                style={styles.noteField}
              />
            </>
          )}

          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.hint, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}

          <PrimaryButton
            label={kind === "goal" ? "Sparziel anlegen" : "Wunsch anlegen"}
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
  field: {
    gap: space.sm,
  },
  label: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  hint: {
    fontSize: typeScale.body,
  },
  switchRow: {
    minHeight: MIN_TOUCH + 4,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.md,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  switchLabel: {
    flex: 1,
    fontSize: typeScale.base,
    fontWeight: "600",
  },
  noteField: {
    minHeight: 120,
    textAlignVertical: "top",
    paddingTop: space.md,
  },
});
