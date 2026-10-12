import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text } from "react-native";
import type { ContractCreate } from "../../src/api/types";
import {
  ContractForm,
  EMPTY_CONTRACT_FORM,
  isReserveEligible,
  validateContractForm,
  type ContractFormValues,
} from "../../src/components/ContractForm";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Card } from "../../src/components/ui";
import { useCreateContract } from "../../src/hooks/useContracts";
import { space, typeScale, useTheme } from "../../src/theme";
import { parseAmountToMinorUnits } from "../../src/utils/money";

export default function VertragNeuScreen() {
  const theme = useTheme();
  const router = useRouter();
  const [values, setValues] = useState<ContractFormValues>(EMPTY_CONTRACT_FORM);
  const [error, setError] = useState<string | null>(null);
  const createContract = useCreateContract();

  const handleSave = () => {
    const validationError = validateContractForm(values);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);

    const input: ContractCreate = {
      name: values.name.trim(),
      provider_label: values.providerLabel.trim() || null,
      contract_type: values.contractType,
      direction: values.direction,
      // Immer positiv; die Richtung steht getrennt daneben.
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
    };

    createContract.mutate(input, {
      onSuccess: () => router.back(),
      onError: (mutationError) => setError(mutationError.message),
    });
  };

  return (
    <>
      <Stack.Screen options={{ title: "Neuer Vertrag" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <ContractForm values={values} onChange={setValues} />
          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.error, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}
          <PrimaryButton
            label="Vertrag anlegen"
            onPress={handleSave}
            loading={createContract.isPending}
          />
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
  error: {
    fontSize: typeScale.body,
  },
});
