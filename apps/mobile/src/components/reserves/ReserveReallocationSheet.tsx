import { useEffect, useMemo, useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import type { ReservePotResponse, ReserveReallocationRequest } from "../../api/types";
import { leading, space, typeScale, useTheme } from "../../theme";
import { parseAmountToMinorUnits } from "../../utils/money";
import { PrimaryButton } from "../PrimaryButton";
import { SheetModal } from "../SheetModal";
import { TextField } from "../TextField";
import { Card, Money } from "../ui";
import { reserveDefaultDate } from "./ReserveDepositSheet";

type Props = {
  visible: boolean;
  pots: ReservePotResponse[];
  year: number;
  month: number;
  loading: boolean;
  onClose: () => void;
  onConfirm: (input: ReserveReallocationRequest) => Promise<unknown>;
};

export function ReserveReallocationSheet({
  visible,
  pots,
  year,
  month,
  loading,
  onClose,
  onConfirm,
}: Props) {
  const theme = useTheme();
  const freePot = pots.find((pot) => pot.kind === "free") ?? null;
  const targets = useMemo(() => pots.filter((pot) => pot.kind !== "free"), [pots]);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setAmounts({});
      setError(null);
    }
  }, [visible]);

  const allocations = targets.flatMap((pot) => {
    try {
      const amountMinor = parseAmountToMinorUnits(amounts[pot.id] ?? "");
      return amountMinor > 0 ? [{ pot_id: pot.id, amount_minor: amountMinor }] : [];
    } catch {
      return [];
    }
  });
  const totalMinor = allocations.reduce((sum, item) => sum + item.amount_minor, 0);
  const remaining = (freePot?.balance_minor ?? 0) - totalMinor;
  const valid = !!freePot && totalMinor > 0 && remaining >= 0;

  const submit = async () => {
    try {
      if (!valid)
        throw new Error(
          remaining < 0
            ? "Die freie Rücklage kann nicht überzogen werden."
            : "Bitte mindestens einen Betrag zuordnen.",
        );
      setError(null);
      await onConfirm({
        occurred_on: reserveDefaultDate(year, month),
        allocations,
        note: "Freie Rücklage mobil bewusst umverteilt",
      });
      onClose();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Umverteilung konnte nicht bestätigt werden.",
      );
    }
  };

  return (
    <SheetModal
      visible={visible}
      title="Aus freier Rücklage verteilen"
      onClose={onClose}
      size="full"
      footer={
        <View style={styles.footer}>
          <View style={styles.inlineRow}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>Verbleibend frei</Text>
            <Money minorUnits={remaining} tone={remaining < 0 ? "warning" : "plain"} />
          </View>
          <PrimaryButton
            label="Umverteilung bestätigen"
            onPress={() => void submit()}
            loading={loading}
            disabled={!valid}
          />
        </View>
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
                Quelle: Freie Rücklage
              </Text>
              <Money minorUnits={freePot?.balance_minor ?? 0} tone="plain" />
            </View>
            <Text style={[styles.body, { color: theme.textSecondary }]}>
              Die Umverteilung ist netto null und ändert die Gesamtrücklage nicht.
            </Text>
          </Card>
          {targets.map((pot) => (
            <Card key={pot.id}>
              <Text style={[styles.name, { color: theme.textPrimary }]}>{pot.name}</Text>
              <View style={styles.metaRow}>
                <Text style={[styles.meta, { color: theme.textSecondary }]}>Aktuell offen</Text>
                <Money minorUnits={pot.open_minor} tone="muted" />
              </View>
              <TextField
                label="Zuweisung in EUR"
                accessibilityLabel={`Betrag für ${pot.name}`}
                value={amounts[pot.id] ?? ""}
                onChangeText={(raw) => setAmounts((current) => ({ ...current, [pot.id]: raw }))}
                keyboardType="decimal-pad"
              />
            </Card>
          ))}
          {remaining < 0 ? (
            <Card tone="warning" density="compact">
              <Text accessibilityRole="alert" style={[styles.body, { color: theme.warning }]}>
                Die freie Rücklage kann nicht überzogen werden.
              </Text>
            </Card>
          ) : null}
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
  name: { fontSize: typeScale.base, fontWeight: "700" },
  meta: { fontSize: typeScale.caption },
  metaRow: { flexDirection: "row", alignItems: "center", gap: space.xs },
  body: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  label: { fontSize: typeScale.caption, fontWeight: "600" },
  inlineRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  footer: { gap: space.sm },
});
