import { useEffect, useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text } from "react-native";
import type { ReserveDepositRequest, ReservePotResponse } from "../../api/types";
import { leading, space, typeScale, useTheme } from "../../theme";
import { formatMinorUnitsForInput, parseAmountToMinorUnits } from "../../utils/money";
import { PrimaryButton } from "../PrimaryButton";
import { SheetModal } from "../SheetModal";
import { TextField } from "../TextField";
import { Card } from "../ui";

type Props = {
  visible: boolean;
  pot: ReservePotResponse | null;
  year: number;
  month: number;
  loading: boolean;
  onClose: () => void;
  onConfirm: (input: ReserveDepositRequest) => Promise<unknown>;
};

export function reserveDefaultDate(year: number, month: number): string {
  const now = new Date();
  if (now.getFullYear() === year && now.getMonth() + 1 === month) {
    return now.toISOString().slice(0, 10);
  }
  return `${year}-${String(month).padStart(2, "0")}-01`;
}

export function ReserveDepositSheet({
  visible,
  pot,
  year,
  month,
  loading,
  onClose,
  onConfirm,
}: Props) {
  const theme = useTheme();
  const [amount, setAmount] = useState("");
  const [occurredOn, setOccurredOn] = useState(reserveDefaultDate(year, month));
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible || !pot) return;
    setAmount(formatMinorUnitsForInput(pot.open_minor || pot.recommended_minor));
    setOccurredOn(reserveDefaultDate(year, month));
    setNote("");
    setError(null);
  }, [visible, pot, year, month]);

  const submit = async () => {
    if (!pot) return;
    try {
      const amountMinor = parseAmountToMinorUnits(amount);
      if (amountMinor <= 0) throw new Error("Bitte einen positiven Betrag eingeben.");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(occurredOn)) {
        throw new Error("Datum bitte im Format JJJJ-MM-TT eingeben.");
      }
      setError(null);
      await onConfirm({
        occurred_on: occurredOn,
        total_minor: amountMinor,
        allocations: [{ pot_id: pot.id, amount_minor: amountMinor }],
        note: note.trim() || null,
      });
      onClose();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Einzahlung konnte nicht bestätigt werden.",
      );
    }
  };

  return (
    <SheetModal
      visible={visible}
      title={pot ? `Zurücklegen · ${pot.name}` : "Betrag zurücklegen"}
      onClose={onClose}
      size="full"
      footer={
        <PrimaryButton
          label="Verbindlich bestätigen"
          onPress={() => void submit()}
          loading={loading}
        />
      }
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.flex}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Erst die Bestätigung erhöht den echten Topfstand. Das Monatssoll bleibt als Referenz
            sichtbar.
          </Text>
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
  body: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  note: { minHeight: 110, textAlignVertical: "top", paddingTop: space.md },
});
