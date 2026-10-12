import { useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { createAccount } from "../../src/api/client";
import type { AccountType } from "../../src/api/types";
import { useAuth } from "../../src/auth/AuthProvider";
import { OptionList } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { TextField } from "../../src/components/TextField";
import { Card } from "../../src/components/ui";
import { useHousehold } from "../../src/household/HouseholdProvider";
import { leading, space, typeScale, useTheme } from "../../src/theme";
import { ACCOUNT_TYPE_OPTIONS } from "../../src/utils/accountType";
import { parseAmountToMinorUnits } from "../../src/utils/money";

// Formular zum Anlegen eines Kontos (Auftrag Punkt 5): Name, Kontotyp aus den
// 8 erlaubten Werten, optional Institut und Startsaldo.
export default function NeuesKontoScreen() {
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();

  const [name, setName] = useState("");
  const [accountType, setAccountType] = useState<AccountType>("checking");
  const [institution, setInstitution] = useState("");
  const [openingBalanceText, setOpeningBalanceText] = useState("");
  const [onBudget, setOnBudget] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate() {
    setError(null);

    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setError("Bitte einen Kontonamen eingeben.");
      return;
    }
    if (!accessToken || !selectedHouseholdId) {
      setError("Kein Haushalt ausgewaehlt.");
      return;
    }

    let openingBalanceMinor = 0;
    if (openingBalanceText.trim().length > 0) {
      try {
        openingBalanceMinor = parseAmountToMinorUnits(openingBalanceText);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Ungueltiger Startsaldo.");
        return;
      }
    }

    setSubmitting(true);
    try {
      await createAccount(accessToken, selectedHouseholdId, {
        name: trimmedName,
        account_type: accountType,
        institution: institution.trim().length > 0 ? institution.trim() : undefined,
        on_budget: onBudget,
        opening_balance_minor: openingBalanceMinor,
      });
      await queryClient.invalidateQueries({ queryKey: ["accounts", selectedHouseholdId] });
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Konto konnte nicht erstellt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "Konto hinzufügen" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <TextField
            label="Name"
            value={name}
            onChangeText={setName}
            placeholder="z. B. Girokonto"
          />

          <View style={styles.field}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>Kontotyp</Text>
            <OptionList
              options={ACCOUNT_TYPE_OPTIONS}
              value={accountType}
              onChange={setAccountType}
            />
          </View>

          <TextField
            label="Institut (optional)"
            value={institution}
            onChangeText={setInstitution}
            placeholder="z. B. Musterbank"
          />

          <TextField
            label="Startsaldo in EUR (optional)"
            value={openingBalanceText}
            onChangeText={setOpeningBalanceText}
            placeholder="0,00"
            keyboardType="numbers-and-punctuation"
          />

          <View style={styles.field}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>Budget</Text>
            <OptionList
              options={[
                { value: "on", label: "On-Budget (zählt zum verfügbaren Budget)" },
                { value: "off", label: "Off-Budget (Vermögen, Depot, Immobilie, Darlehen)" },
              ]}
              value={onBudget ? "on" : "off"}
              onChange={(value) => setOnBudget(value === "on")}
            />
          </View>

          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.notice, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}

          <PrimaryButton
            label="Konto anlegen"
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
  notice: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
});
