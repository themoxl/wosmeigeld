import { useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { createAssetOrDebt } from "../src/api/client";
import type { AssetClass, AssetType } from "../src/api/types";
import { useAuth } from "../src/auth/AuthProvider";
import { OptionList } from "../src/components/OptionList";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { TextField } from "../src/components/TextField";
import { Card } from "../src/components/ui";
import { useHousehold } from "../src/household/HouseholdProvider";
import { space, typeScale, useTheme } from "../src/theme";
import { parseAmountToMinorUnits } from "../src/utils/money";

const ASSET_CLASS_OPTIONS: { value: AssetClass; label: string }[] = [
  { value: "real_estate", label: "Immobilie" },
  { value: "vehicle", label: "Fahrzeug" },
  { value: "depot", label: "Depot" },
  { value: "cash", label: "Bargeld / Konto" },
  { value: "loan", label: "Kredit" },
  { value: "credit", label: "Kreditkarte" },
  { value: "other", label: "Sonstiges" },
];

// Formular zum Erfassen eines Vermoegenswerts oder einer Schuld (Phase 7,
// Architekturplan Abschnitt 11.2). Von app/mehr/vermoegen.tsx aus erreichbar.
export default function NeuesVermoegenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const [type, setType] = useState<AssetType>("asset");
  const [name, setName] = useState("");
  const [assetClass, setAssetClass] = useState<AssetClass>("cash");
  const [valueText, setValueText] = useState("");
  const [interestRateText, setInterestRateText] = useState("");
  const [monthlyPaymentText, setMonthlyPaymentText] = useState("");
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

    let valueMinor: number;
    try {
      valueMinor = parseAmountToMinorUnits(valueText);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Ungültiger Wert.");
      return;
    }

    let interestRatePercent: number | undefined;
    if (interestRateText.trim().length > 0) {
      const parsed = Number.parseFloat(interestRateText.replace(",", "."));
      if (Number.isFinite(parsed)) {
        interestRatePercent = parsed;
      }
    }

    let monthlyPaymentMinor: number | undefined;
    if (monthlyPaymentText.trim().length > 0) {
      try {
        monthlyPaymentMinor = parseAmountToMinorUnits(monthlyPaymentText);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Ungültige monatliche Rate.");
        return;
      }
    }

    setSubmitting(true);
    try {
      await createAssetOrDebt(accessToken, selectedHouseholdId, {
        name: trimmedName,
        type,
        asset_class: assetClass,
        current_value_minor: valueMinor,
        interest_rate_percent: interestRatePercent,
        monthly_payment_minor: monthlyPaymentMinor,
      });
      await queryClient.invalidateQueries({ queryKey: ["assets", selectedHouseholdId] });
      await queryClient.invalidateQueries({ queryKey: ["net-worth", selectedHouseholdId] });
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Eintrag konnte nicht erstellt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Vermögen/Schuld erfassen" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.field}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>Art</Text>
            <OptionList
              options={[
                { value: "asset", label: "Vermögenswert" },
                { value: "debt", label: "Schuld" },
              ]}
              value={type}
              onChange={setType}
            />
          </View>

          <TextField
            label="Bezeichnung"
            value={name}
            onChangeText={setName}
            placeholder="z. B. Eigentumswohnung"
          />

          <View style={styles.field}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>Klasse</Text>
            <OptionList options={ASSET_CLASS_OPTIONS} value={assetClass} onChange={setAssetClass} />
          </View>

          <TextField
            label="Aktueller Wert in EUR"
            value={valueText}
            onChangeText={setValueText}
            placeholder="10000,00"
            keyboardType="numbers-and-punctuation"
          />

          {type === "debt" ? (
            <>
              <TextField
                label="Zinssatz in % (optional)"
                value={interestRateText}
                onChangeText={setInterestRateText}
                placeholder="3,50"
                keyboardType="numbers-and-punctuation"
              />
              <TextField
                label="Rate pro Monat in EUR (optional)"
                value={monthlyPaymentText}
                onChangeText={setMonthlyPaymentText}
                placeholder="500,00"
                keyboardType="numbers-and-punctuation"
              />
            </>
          ) : null}

          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.hint, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}

          <PrimaryButton
            label={type === "asset" ? "Vermögenswert anlegen" : "Schuld anlegen"}
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
});
