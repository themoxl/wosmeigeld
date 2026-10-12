import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import type { Contract, ContractTermDeadline, ContractUpdate } from "../../src/api/types";
import {
  ContractForm,
  EMPTY_CONTRACT_FORM,
  isReserveEligible,
  validateContractForm,
  type ContractFormValues,
} from "../../src/components/ContractForm";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Badge, Card, CardLabel, Money } from "../../src/components/ui";
import {
  useContractContext,
  useContractHistory,
  useContracts,
  useUpdateContract,
} from "../../src/hooks/useContracts";
import { leading, space, typeScale, useTheme } from "../../src/theme";
import { parseAmountToMinorUnits } from "../../src/utils/money";

function toFormValues(contract: Contract): ContractFormValues {
  return {
    name: contract.name,
    providerLabel: contract.provider_label ?? "",
    contractType: contract.contract_type,
    direction: contract.direction,
    amount: (contract.amount_minor / 100).toFixed(2).replace(".", ","),
    frequency: contract.frequency,
    nextDueDate: contract.next_due_date,
    status: contract.status,
    cancelledOn: contract.cancelled_on ?? "",
    endsOn: contract.ends_on ?? "",
    note: contract.note ?? "",
    reserveEnabled: contract.reserve_enabled,
    reserveStartedOn: contract.reserve_started_on ?? new Date().toISOString().slice(0, 10),
    reserveStartingAmount: (contract.reserve_starting_amount_minor / 100)
      .toFixed(2)
      .replace(".", ","),
  };
}

function countdownLabel(daysLeft: number): string {
  if (daysLeft <= 0) return "heute";
  if (daysLeft === 1) return "morgen";
  return `noch ${daysLeft} Tage`;
}

/**
 * Kuendigungsfrist als Countdown (docs/VERTRAEGE-FIXKOSTEN-PLAN.md, I4).
 *
 * Die Kante ist `deadline_on`, nicht `term_ends_on`: wer bis zum Laufzeitende
 * wartet, hat die Frist bereits verpasst. Bei `open_ended` gibt es nichts zu
 * verpassen - dort waere ein Countdown erfundene Dringlichkeit, deshalb nennt
 * die Karte nur den fruehestmoeglichen Wirksamkeitstag.
 *
 * Die 30-Tage-Kante fuer die Warnfarbe ist dieselbe, bei der der Server sein
 * erstes 'term_ending'-Ereignis erzeugt.
 */
function TermDeadlineCard({ deadline }: { deadline: ContractTermDeadline }) {
  const theme = useTheme();
  const urgent = deadline.kind === "fixed_term" && deadline.days_left <= 30;

  return (
    <Card accentEdge={urgent ? "warning" : "info"}>
      <CardLabel>Kündigung</CardLabel>
      {deadline.kind === "fixed_term" ? (
        <>
          <View style={styles.deadlineHeadline}>
            <Text
              style={[styles.deadlineValue, { color: urgent ? theme.warning : theme.textPrimary }]}
            >
              {countdownLabel(deadline.days_left)}
            </Text>
            {urgent ? <Badge label="Frist läuft" tone="warning" /> : null}
          </View>
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Kündigung muss bis {deadline.deadline_on} beim Anbieter sein.
          </Text>
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Wirksam zum {deadline.term_ends_on}
            {deadline.renewal_index > 1 ? ` (${deadline.renewal_index}. Laufzeit)` : ""}.
          </Text>
        </>
      ) : (
        <>
          <Text style={[styles.deadlineValue, { color: theme.textPrimary }]}>
            Jederzeit kuendbar
          </Text>
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Keine Mindestlaufzeit. Fruehestens wirksam zum {deadline.term_ends_on}.
          </Text>
        </>
      )}
    </Card>
  );
}

/**
 * Vertrag bearbeiten inklusive Betragsverlauf.
 *
 * Der Verlauf ist der eigentliche Gewinn gegenueber der alten Fixkostenliste:
 * er zeigt, wann sich ein Vertrag verteuert hat, statt eine Preisaenderung als
 * zweiten Eintrag auftauchen zu lassen.
 */
export default function VertragBearbeitenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { canEdit } = useContractContext();

  const contractsQuery = useContracts({ includeEnded: true });
  const historyQuery = useContractHistory(id ?? null);
  const updateContract = useUpdateContract();

  const contract = (contractsQuery.data ?? []).find((item) => item.id === id) ?? null;

  // Formularzustand einmal je geladenem Vertrag setzen, nicht bei jedem
  // Refetch - sonst gingen unfertige Eingaben verloren. Dasselbe Render-Muster
  // wie in app/kategorie/[id].tsx (kein setState im Effekt).
  const [initializedId, setInitializedId] = useState<string | null>(null);
  const [values, setValues] = useState<ContractFormValues>(EMPTY_CONTRACT_FORM);
  const [error, setError] = useState<string | null>(null);

  if (contract && contract.id !== initializedId) {
    setInitializedId(contract.id);
    setValues(toFormValues(contract));
  }

  const handleSave = () => {
    if (!contract) return;
    const validationError = validateContractForm(values);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);

    const patch: ContractUpdate = {
      name: values.name.trim(),
      provider_label: values.providerLabel.trim() || null,
      contract_type: values.contractType,
      direction: values.direction,
      amount_minor: Math.abs(parseAmountToMinorUnits(values.amount)),
      frequency: values.frequency,
      next_due_date: values.nextDueDate,
      status: values.status,
      cancelled_on: values.cancelledOn || null,
      ends_on: values.endsOn || null,
      note: values.note.trim() || null,
      reserve_enabled: isReserveEligible(values) ? values.reserveEnabled : false,
      reserve_started_on:
        isReserveEligible(values) && values.reserveEnabled ? values.reserveStartedOn : null,
      reserve_starting_amount_minor:
        isReserveEligible(values) && values.reserveEnabled
          ? Math.max(0, parseAmountToMinorUnits(values.reserveStartingAmount || "0"))
          : 0,
      clear_fields: [
        ...(values.providerLabel.trim() ? [] : (["provider_label"] as const)),
        ...(values.cancelledOn ? [] : (["cancelled_on"] as const)),
        ...(values.endsOn ? [] : (["ends_on"] as const)),
        ...(values.note.trim() ? [] : (["note"] as const)),
        ...(isReserveEligible(values) && values.reserveEnabled
          ? []
          : (["reserve_started_on"] as const)),
      ],
    };

    updateContract.mutate(
      { contractId: contract.id, version: contract.version, patch },
      {
        onSuccess: () => router.back(),
        onError: (mutationError) => setError(mutationError.message),
      },
    );
  };

  if (contractsQuery.isLoading) {
    return (
      <ScreenContainer>
        <InlineQueryState status="loading" skeletonRows={6} />
      </ScreenContainer>
    );
  }

  // Erst der Ladefehler, dann "gibt es nicht": bei Serverfehler oder ohne Netz
  // ist `contract` ebenfalls undefined - "existiert nicht mehr" waere dann eine
  // Falschaussage ueber fremde Daten.
  if (contractsQuery.isError) {
    return (
      <>
        <Stack.Screen options={{ title: "Vertrag" }} />
        <ScreenContainer>
          <View style={styles.notFound}>
            <InlineQueryState
              status="error"
              title="Vertrag konnte nicht geladen werden"
              message={
                contractsQuery.error instanceof Error ? contractsQuery.error.message : undefined
              }
              onRetry={() => void contractsQuery.refetch()}
            />
          </View>
        </ScreenContainer>
      </>
    );
  }

  if (!contract) {
    return (
      <>
        <Stack.Screen options={{ title: "Vertrag" }} />
        <ScreenContainer>
          <View style={styles.notFound}>
            <Card tone="warning" density="compact">
              <Text style={[styles.body, { color: theme.warning }]}>
                Dieser Vertrag existiert nicht mehr.
              </Text>
            </Card>
          </View>
        </ScreenContainer>
      </>
    );
  }

  const steps = historyQuery.data ?? [];

  return (
    <>
      <Stack.Screen options={{ title: contract.name }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {contract.term_deadline ? <TermDeadlineCard deadline={contract.term_deadline} /> : null}

          {contract.reserve_enabled ? (
            <Card accentEdge="positive">
              <CardLabel>Rücklage aktiv</CardLabel>
              <View style={styles.reserveRow}>
                <Text style={[styles.body, { color: theme.textPrimary }]}>
                  Monatlich zurücklegen · Startwert
                </Text>
                <Money minorUnits={contract.reserve_starting_amount_minor} tone="plain" />
              </View>
              <Text style={[styles.body, { color: theme.textSecondary }]}>
                Details und bestätigte Bewegungen findest du unter Budget → Rücklagen.
              </Text>
            </Card>
          ) : null}

          <Card>
            <CardLabel>Betragsverlauf</CardLabel>
            {steps.length === 0 ? (
              <Text style={[styles.body, { color: theme.textSecondary }]}>
                Noch kein Verlauf erfasst.
              </Text>
            ) : (
              steps.map((step) => (
                <View key={step.id} style={[styles.historyRow, { borderColor: theme.border }]}>
                  <Money
                    minorUnits={
                      contract.direction === "expense" ? -step.amount_minor : step.amount_minor
                    }
                    tone="plain"
                  />
                  <Text style={[styles.historyPeriod, { color: theme.textSecondary }]}>
                    {step.valid_from} – {step.valid_to ?? "heute"}
                  </Text>
                  {step.delta_minor ? (
                    <Money
                      minorUnits={step.delta_minor}
                      showPlusSign
                      tone={step.delta_minor > 0 ? "warning" : "positive"}
                    />
                  ) : null}
                </View>
              ))
            )}
          </Card>

          {canEdit ? (
            <>
              <ContractForm values={values} onChange={setValues} />
              {error ? (
                <Card tone="warning" density="compact">
                  <Text style={[styles.body, { color: theme.warning }]} accessibilityRole="alert">
                    {error}
                  </Text>
                </Card>
              ) : null}
              <PrimaryButton
                label="Speichern"
                onPress={handleSave}
                loading={updateContract.isPending}
              />
            </>
          ) : (
            <Card tone="info" density="compact">
              <Text style={[styles.body, { color: theme.info }]}>
                Zum Bearbeiten fehlen die Rechte in diesem Haushalt.
              </Text>
            </Card>
          )}
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    gap: space.lg,
    padding: space.lg,
    paddingBottom: space.xxl,
  },
  body: {
    flexShrink: 1,
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  deadlineHeadline: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  deadlineValue: {
    fontSize: typeScale.large,
    fontWeight: "800",
  },
  reserveRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.md,
  },
  historyRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: space.sm,
    paddingVertical: space.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  historyPeriod: {
    flexShrink: 1,
    fontSize: typeScale.caption,
  },
  notFound: {
    padding: space.lg,
  },
});
