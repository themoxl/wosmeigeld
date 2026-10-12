import { useEffect, useMemo, useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import type { ReserveDepositRequest, ReservePotResponse } from "../../api/types";
import { leading, radii, space, typeScale, useTheme } from "../../theme";
import { formatMinorUnitsForInput, parseAmountToMinorUnits } from "../../utils/money";
import { PrimaryButton } from "../PrimaryButton";
import { SheetModal } from "../SheetModal";
import { TextField } from "../TextField";
import { Card, Money } from "../ui";
import { reserveDefaultDate } from "./ReserveDepositSheet";

type Draft = Record<string, { selected: boolean; amount: string }>;

type Props = {
  visible: boolean;
  pots: ReservePotResponse[];
  year: number;
  month: number;
  loading: boolean;
  onClose: () => void;
  onConfirm: (input: ReserveDepositRequest) => Promise<unknown>;
};

export function ReserveBulkDepositSheet({
  visible,
  pots,
  year,
  month,
  loading,
  onClose,
  onConfirm,
}: Props) {
  const theme = useTheme();
  const eligible = useMemo(
    () =>
      pots.filter(
        (pot) =>
          pot.kind !== "free" &&
          pot.open_minor > 0 &&
          (pot.kind === "contract" || pot.is_included_in_planning),
      ),
    [pots],
  );
  const [draft, setDraft] = useState<Draft>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setDraft(
      Object.fromEntries(
        eligible.map((pot) => [
          pot.id,
          { selected: true, amount: formatMinorUnitsForInput(pot.open_minor) },
        ]),
      ),
    );
    setError(null);
  }, [visible, eligible]);

  const allocations = eligible.flatMap((pot) => {
    const entry = draft[pot.id];
    if (!entry?.selected) return [];
    try {
      const amountMinor = parseAmountToMinorUnits(entry.amount);
      return amountMinor > 0 ? [{ pot_id: pot.id, amount_minor: amountMinor }] : [];
    } catch {
      return [];
    }
  });
  const totalMinor = allocations.reduce((sum, item) => sum + item.amount_minor, 0);

  const submit = async () => {
    try {
      if (allocations.length === 0 || totalMinor <= 0) {
        throw new Error("Bitte mindestens einen Topf mit positivem Betrag auswählen.");
      }
      setError(null);
      await onConfirm({
        occurred_on: reserveDefaultDate(year, month),
        total_minor: totalMinor,
        allocations,
        note: "Ausgewählte Monatsraten mobil bestätigt",
      });
      onClose();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Monatsraten konnten nicht bestätigt werden.",
      );
    }
  };

  return (
    <SheetModal
      visible={visible}
      title="Monatsraten bestätigen"
      onClose={onClose}
      size="full"
      footer={
        <View style={styles.footer}>
          <View style={styles.inlineRow}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>
              Gemeinsame Einzahlung
            </Text>
            <Money minorUnits={totalMinor} size="medium" tone="plain" />
          </View>
          <PrimaryButton
            label="Verbindlich bestätigen"
            onPress={() => void submit()}
            loading={loading}
            disabled={totalMinor <= 0}
          />
        </View>
      }
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.flex}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Prüfe Auswahl und Beträge. Das Öffnen dieses Sheets schreibt noch nichts.
          </Text>
          {eligible.map((pot) => {
            const entry = draft[pot.id] ?? { selected: false, amount: "" };
            let amountMinor = 0;
            try {
              amountMinor = entry.selected ? parseAmountToMinorUnits(entry.amount) : 0;
            } catch {
              amountMinor = 0;
            }
            return (
              <Card key={pot.id}>
                <Pressable
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: entry.selected }}
                  accessibilityLabel={`${pot.name} auswählen`}
                  onPress={() =>
                    setDraft((current) => ({
                      ...current,
                      [pot.id]: { ...entry, selected: !entry.selected },
                    }))
                  }
                  style={styles.selectRow}
                >
                  <View
                    style={[
                      styles.checkbox,
                      {
                        borderColor: entry.selected ? theme.positiveStrong : theme.borderStrong,
                        backgroundColor: entry.selected ? theme.positiveStrong : "transparent",
                      },
                    ]}
                  >
                    <Text style={[styles.checkmark, { color: theme.onAccent }]}>
                      {entry.selected ? "✓" : ""}
                    </Text>
                  </View>
                  <View style={styles.copy}>
                    <Text style={[styles.name, { color: theme.textPrimary }]}>{pot.name}</Text>
                    <View style={styles.metaRow}>
                      <Text style={[styles.meta, { color: theme.textSecondary }]}>Offen</Text>
                      <Money minorUnits={pot.open_minor} tone="muted" />
                    </View>
                  </View>
                </Pressable>
                <TextField
                  label="Betrag in EUR"
                  accessibilityLabel={`Einzahlungsbetrag für ${pot.name}`}
                  value={entry.amount}
                  onChangeText={(amount) =>
                    setDraft((current) => ({ ...current, [pot.id]: { ...entry, amount } }))
                  }
                  keyboardType="decimal-pad"
                  editable={entry.selected}
                />
                <View style={styles.inlineRow}>
                  <Text style={[styles.meta, { color: theme.textSecondary }]}>Aktueller Stand</Text>
                  <Money minorUnits={pot.balance_minor} tone="plain" />
                </View>
                <View style={styles.inlineRow}>
                  <Text style={[styles.meta, { color: theme.textSecondary }]}>
                    Prognose nach Bestätigung
                  </Text>
                  <Money
                    minorUnits={pot.balance_minor + Math.max(0, amountMinor)}
                    tone="positive"
                  />
                </View>
              </Card>
            );
          })}
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
  selectRow: { minHeight: 44, flexDirection: "row", alignItems: "center", gap: space.md },
  checkbox: {
    width: 24,
    height: 24,
    borderWidth: 1,
    borderRadius: radii.xs,
    alignItems: "center",
    justifyContent: "center",
  },
  checkmark: { fontWeight: "700" },
  copy: { flex: 1, gap: space.hair },
  name: { fontSize: typeScale.base, fontWeight: "700" },
  meta: { fontSize: typeScale.caption },
  metaRow: { flexDirection: "row", alignItems: "center", gap: space.xs },
  inlineRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  body: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  label: { fontSize: typeScale.caption, fontWeight: "600" },
  footer: { gap: space.sm },
});
