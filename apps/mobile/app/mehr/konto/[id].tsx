import { useQueryClient } from "@tanstack/react-query";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { patchAccount } from "../../../src/api/client";
import { useAuth } from "../../../src/auth/AuthProvider";
import { InlineQueryState } from "../../../src/components/InlineQueryState";
import { OptionList } from "../../../src/components/OptionList";
import { PrimaryButton } from "../../../src/components/PrimaryButton";
import { ScreenContainer } from "../../../src/components/ScreenContainer";
import { TextField } from "../../../src/components/TextField";
import { Badge, Card, CardLabel, Money } from "../../../src/components/ui";
import { useHousehold } from "../../../src/household/HouseholdProvider";
import { useAccounts } from "../../../src/hooks/useAccounts";
import { space, typeScale, useTheme } from "../../../src/theme";
import { getAccountTypeLabel } from "../../../src/utils/accountType";
import { formatDueDate } from "../../../src/utils/dates";

// Konto bearbeiten (Auftrag Punkt 3): Name/Institut und Schliessen ueber
// PATCH /v1/accounts/{id}. Es gibt keinen eigenen GET /v1/accounts/{id}
// -Endpunkt - die Ausgangsdaten kommen bewusst aus der bereits geladenen
// Kontenliste (GET /v1/accounts via useAccounts(), react-query-gecacht),
// von der aus dieser Screen erreichbar ist.
export default function KontoBearbeitenScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const theme = useTheme();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const {
    data: accounts,
    isLoading,
    isError: accountsFailed,
    error: accountsError,
    refetch: refetchAccounts,
  } = useAccounts();

  const account = (accounts ?? []).find((a) => a.id === id) ?? null;

  const [name, setName] = useState("");
  const [institution, setInstitution] = useState("");
  const [isClosed, setIsClosed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // State waehrend des Renderns aus den geladenen Kontodaten initialisieren
  // (nicht per useEffect, siehe ausfuehrlicher Kommentar in
  // app/buchung/[id].tsx: die aktive react-hooks/set-state-in-effect-Regel
  // verbietet synchrones setState direkt im Effektkoerper).
  const [initializedAccountId, setInitializedAccountId] = useState<string | null>(null);
  if (account && account.id !== initializedAccountId) {
    setInitializedAccountId(account.id);
    setName(account.name);
    setInstitution(account.institution ?? "");
    setIsClosed(account.is_closed);
  }

  async function handleSave() {
    if (!accessToken || !selectedHouseholdId || !account) return;

    const trimmedName = name.trim();
    if (trimmedName.length === 0) {
      setError("Bitte einen Kontonamen eingeben.");
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      await patchAccount(accessToken, selectedHouseholdId, account.id, {
        name: trimmedName,
        institution: institution.trim().length > 0 ? institution.trim() : undefined,
        is_closed: isClosed,
      });
      await queryClient.invalidateQueries({ queryKey: ["accounts", selectedHouseholdId] });
      router.back();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Konto konnte nicht gespeichert werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: account?.name ?? "Konto bearbeiten" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {isLoading && !account ? (
            <Card>
              <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={20} />
            </Card>
          ) : null}

          {/* Ladefehler vor "nicht gefunden": ohne Netz ist `account` ebenfalls
              null, und ein geloeschtes Konto ist etwas anderes als eine
              gescheiterte Abfrage. */}
          {!isLoading && !account && accountsFailed ? (
            <InlineQueryState
              status="error"
              title="Konto konnte nicht geladen werden"
              message={accountsError instanceof Error ? accountsError.message : undefined}
              onRetry={() => void refetchAccounts()}
            />
          ) : null}

          {!isLoading && !account && !accountsFailed ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.message, { color: theme.warning }]} accessibilityRole="alert">
                Konto nicht gefunden.
              </Text>
            </Card>
          ) : null}

          {account ? (
            <>
              <Card>
                <CardLabel>STARTSALDO</CardLabel>
                <Money
                  minorUnits={account.opening_balance_minor}
                  currency={account.currency}
                  size="display"
                  tone="plain"
                />
                <Text style={[styles.caption, { color: theme.textSecondary }]}>
                  {getAccountTypeLabel(account.account_type)}
                  {account.opening_balance_date
                    ? ` · Stand ${formatDueDate(account.opening_balance_date)}`
                    : ""}
                </Text>
                {!account.on_budget ? (
                  <Badge label="Off-Budget · zählt nicht ins Budget" tone="highlight" />
                ) : null}
              </Card>

              <TextField label="Name" value={name} onChangeText={setName} />

              <TextField
                label="Institut (optional)"
                value={institution}
                onChangeText={setInstitution}
                placeholder="z. B. Musterbank"
              />

              <View style={styles.field}>
                <Text style={[styles.label, { color: theme.textSecondary }]}>Status</Text>
                <OptionList
                  options={[
                    { value: "active", label: "Aktiv" },
                    { value: "closed", label: "Geschlossen" },
                  ]}
                  value={isClosed ? "closed" : "active"}
                  onChange={(value) => setIsClosed(value === "closed")}
                />
              </View>

              {error ? (
                <Card tone="warning" density="compact">
                  <Text
                    style={[styles.message, { color: theme.warning }]}
                    accessibilityRole="alert"
                  >
                    {error}
                  </Text>
                </Card>
              ) : null}

              <PrimaryButton
                label="Speichern"
                onPress={() => void handleSave()}
                loading={submitting}
              />
            </>
          ) : null}
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
  caption: {
    fontSize: typeScale.caption,
  },
  message: {
    fontSize: typeScale.body,
  },
  field: {
    gap: space.sm,
  },
  label: {
    fontSize: typeScale.body,
    fontWeight: "500",
  },
});
