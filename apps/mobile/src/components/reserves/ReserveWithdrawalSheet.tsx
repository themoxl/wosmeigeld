import { useEffect, useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import type { ReservePotResponse, ReserveWithdrawalRequest } from "../../api/types";
import { leading, space, typeScale, useTheme } from "../../theme";
import { parseAmountToMinorUnits } from "../../utils/money";
import { PrimaryButton } from "../PrimaryButton";
import { SheetModal } from "../SheetModal";
import { TextField } from "../TextField";
import { Card, Money } from "../ui";
import { reserveDefaultDate } from "./ReserveDepositSheet";

type Props = {
  visible: boolean;
  pot: ReservePotResponse | null;
  year: number;
  month: number;
  loading: boolean;
  onClose: () => void;
  onConfirm: (input: ReserveWithdrawalRequest) => Promise<unknown>;
};

export function ReserveWithdrawalSheet(props: Props) {
  const { visible, pot, year, month, loading, onClose, onConfirm } = props;
  const theme = useTheme();
  const [amount, setAmount] = useState("");
  const [occurredOn, setOccurredOn] = useState(reserveDefaultDate(year, month));
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setAmount("");
    setOccurredOn(reserveDefaultDate(year, month));
    setNote("");
    setError(null);
  }, [visible, year, month]);

  const submit = async () => {
    if (!pot) return;
    try {
      const amountMinor = parseAmountToMinorUnits(amount);
      if (amountMinor <= 0) throw new Error("Bitte einen positiven Betrag eingeben.");
      if (amountMinor > pot.balance_minor) throw new Error("Der Topf kann nicht überzogen werden.");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(occurredOn))
        throw new Error("Datum bitte als JJJJ-MM-TT eingeben.");
      setError(null);
      await onConfirm({
        pot_id: pot.id,
        amount_minor: amountMinor,
        occurred_on: occurredOn,
        note: note.trim() || null,
      });
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Entnahme konnte nicht erfasst werden.");
    }
  };

  return (
    <SheetModal
      visible={visible}
      title={pot ? `Entnehmen · ${pot.name}` : "Rücklage entnehmen"}
      onClose={onClose}
      size="full"
      footer={
        <PrimaryButton
          label="Entnahme bestätigen"
          onPress={() => void submit()}
          loading={loading}
          variant="danger"
        />
      }
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.flex}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Card density="compact">
            <View style={styles.inlineRow}>
              <Text style={[styles.label, { color: theme.textSecondary }]}>
                Verfügbarer Topfstand
              </Text>
              <Money minorUnits={pot?.balance_minor ?? 0} tone="plain" />
            </View>
            <Text style={[styles.body, { color: theme.textSecondary }]}>
              Die echte Rechnung bleibt in den Buchungen weiterhin eine Ausgabe; es erfolgt keine
              automatische Bankerkennung oder Entnahme.
            </Text>
          </Card>
          <TextField
            label="Betrag in EUR"
            value={amount}
            onChangeText={setAmount}
            keyboardType="decimal-pad"
            autoFocus
          />
          <TextField label="Datum (JJJJ-MM-TT)" value={occurredOn} onChangeText={setOccurredOn} />
          <TextField
            label="Notiz (optional)"
            value={note}
            onChangeText={setNote}
            multiline
            style={styles.note}
          />
          {error ? (
            <Card tone="warning" density="compact">
              <Text accessibilityRole="alert" style={[styles.body, { color: theme.warning }]}>
                {error}
              </Text>
            </Card>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SheetModal>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: { padding: space.lg, gap: space.md, paddingBottom: space.xxxl },
  inlineRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  label: { fontSize: typeScale.body, fontWeight: "600" },
  body: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  note: { minHeight: 110, textAlignVertical: "top", paddingTop: space.md },
});
