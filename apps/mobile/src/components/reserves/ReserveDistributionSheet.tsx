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
import type {
  ReserveDepositRequest,
  ReserveDistributionPreviewRequest,
  ReserveDistributionPreviewResponse,
  ReservePotResponse,
} from "../../api/types";
import { leading, radii, space, typeScale, useTheme } from "../../theme";
import { formatMinorUnitsForInput, parseAmountToMinorUnits } from "../../utils/money";
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
  previewLoading: boolean;
  confirmLoading: boolean;
  onClose: () => void;
  onPreview: (
    input: ReserveDistributionPreviewRequest,
  ) => Promise<ReserveDistributionPreviewResponse>;
  onConfirm: (input: ReserveDepositRequest) => Promise<unknown>;
};

function minorOrZero(raw: string | undefined): number {
  if (!raw) return 0;
  try {
    return Math.max(0, parseAmountToMinorUnits(raw));
  } catch {
    return 0;
  }
}

export function ReserveDistributionSheet({
  visible,
  pots,
  year,
  month,
  previewLoading,
  confirmLoading,
  onClose,
  onPreview,
  onConfirm,
}: Props) {
  const theme = useTheme();
  const freePot = pots.find((pot) => pot.kind === "free") ?? null;
  const targets = useMemo(() => pots.filter((pot) => pot.kind !== "free"), [pots]);
  const [total, setTotal] = useState("");
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    setTotal("");
    setAmounts({});
    setSelected({});
    setError(null);
  }, [visible]);

  const totalMinor = minorOrZero(total);
  const assignedMinor = Object.values(amounts).reduce((sum, raw) => sum + minorOrZero(raw), 0);
  const exact = totalMinor > 0 && assignedMinor === totalMinor;

  const updateTotal = (raw: string) => {
    setTotal(raw);
    if (!freePot) return;
    setAmounts((current) => ({ ...current, [freePot.id]: raw }));
    setSelected({});
  };

  const requestSuggestion = async () => {
    try {
      if (!freePot || totalMinor <= 0) throw new Error("Bitte zuerst einen Gesamtbetrag eingeben.");
      setError(null);
      const preview = await onPreview({ year, month, total_minor: totalMinor });
      const nextAmounts: Record<string, string> = {
        [freePot.id]: formatMinorUnitsForInput(preview.free_remainder_minor),
      };
      const nextSelected: Record<string, boolean> = {};
      for (const allocation of preview.allocations) {
        nextAmounts[allocation.pot_id] = formatMinorUnitsForInput(allocation.amount_minor);
        nextSelected[allocation.pot_id] = allocation.amount_minor > 0;
      }
      setAmounts(nextAmounts);
      setSelected(nextSelected);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Vorschlag konnte nicht geladen werden.");
    }
  };

  const toggleTarget = (potId: string) => {
    const isSelected = !!selected[potId];
    const removedMinor = isSelected ? minorOrZero(amounts[potId]) : 0;
    setSelected((current) => ({ ...current, [potId]: !isSelected }));
    setAmounts((current) => ({
      ...current,
      [potId]: isSelected ? "" : (current[potId] ?? ""),
      ...(isSelected && freePot
        ? {
            [freePot.id]: formatMinorUnitsForInput(minorOrZero(current[freePot.id]) + removedMinor),
          }
        : {}),
    }));
  };

  const submit = async () => {
    try {
      if (!freePot || !exact)
        throw new Error("Die Zuordnung muss exakt dem Gesamtbetrag entsprechen.");
      const allocations = pots.flatMap((pot) => {
        const amountMinor = minorOrZero(amounts[pot.id]);
        if (amountMinor <= 0 || (pot.kind !== "free" && !selected[pot.id])) return [];
        return [{ pot_id: pot.id, amount_minor: amountMinor }];
      });
      setError(null);
      await onConfirm({
        occurred_on: reserveDefaultDate(year, month),
        total_minor: totalMinor,
        allocations,
        note: "Großbetrag mobil explizit verteilt",
      });
      onClose();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Verteilung konnte nicht bestätigt werden.",
      );
    }
  };

  return (
    <SheetModal
      visible={visible}
      title="Einzahlung verteilen"
      onClose={onClose}
      size="full"
      footer={
        <View style={styles.footer}>
          <View style={styles.assignmentRow}>
            <Text style={[styles.label, { color: exact ? theme.positive : theme.warning }]}>
              Zugeordnet
            </Text>
            <Money minorUnits={assignedMinor} tone={exact ? "positive" : "warning"} />
            <Text style={[styles.label, { color: theme.textSecondary }]}>von</Text>
            <Money minorUnits={totalMinor} tone="plain" />
          </View>
          <PrimaryButton
            label="Verteilung verbindlich bestätigen"
            onPress={() => void submit()}
            loading={confirmLoading}
            disabled={!exact}
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
            Zunächst bleibt der gesamte Betrag sichtbar in der freien Rücklage. Nur du ordnest ihn
            einem Zweck zu; es wird nichts automatisch gebucht.
          </Text>
          <TextField
            label="Gesamtbetrag in EUR"
            value={total}
            onChangeText={updateTotal}
            keyboardType="decimal-pad"
            autoFocus
          />
          <PrimaryButton
            label="Nach offenen/fälligen Töpfen vorschlagen"
            onPress={() => void requestSuggestion()}
            loading={previewLoading}
            disabled={totalMinor <= 0}
            variant="secondary"
          />
          {targets.map((pot) => {
            const active = !!selected[pot.id];
            return (
              <Card key={pot.id}>
                <Pressable
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: active }}
                  accessibilityLabel={`${pot.name} in die Verteilung aufnehmen`}
                  onPress={() => toggleTarget(pot.id)}
                  style={styles.selectRow}
                >
                  <View
                    style={[
                      styles.checkbox,
                      {
                        borderColor: active ? theme.positiveStrong : theme.borderStrong,
                        backgroundColor: active ? theme.positiveStrong : "transparent",
                      },
                    ]}
                  >
                    <Text style={[styles.checkmark, { color: theme.onAccent }]}>
                      {active ? "✓" : ""}
                    </Text>
                  </View>
                  <View style={styles.copy}>
                    <Text style={[styles.name, { color: theme.textPrimary }]}>{pot.name}</Text>
                    <View style={styles.metaRow}>
                      <Text style={[styles.meta, { color: theme.textSecondary }]}>
                        Aktuell offen
                      </Text>
                      <Money minorUnits={pot.open_minor} tone="muted" />
                    </View>
                  </View>
                </Pressable>
                <TextField
                  label="Zuweisung in EUR"
                  accessibilityLabel={`Betrag für ${pot.name}`}
                  value={amounts[pot.id] ?? ""}
                  onChangeText={(raw) => setAmounts((current) => ({ ...current, [pot.id]: raw }))}
                  keyboardType="decimal-pad"
                  editable={active}
                />
              </Card>
            );
          })}
          {freePot ? (
            <Card tone="positive">
              <Text style={[styles.name, { color: theme.textPrimary }]}>Freie Rücklage</Text>
              <Text style={[styles.meta, { color: theme.textSecondary }]}>
                Noch nicht zweckgebunden / freier Rest
              </Text>
              <TextField
                label="Freier Rest in EUR"
                accessibilityLabel="Betrag für Freie Rücklage"
                value={amounts[freePot.id] ?? ""}
                onChangeText={(raw) => setAmounts((current) => ({ ...current, [freePot.id]: raw }))}
                keyboardType="decimal-pad"
              />
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
  body: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  label: { fontSize: typeScale.caption, fontWeight: "700" },
  footer: { gap: space.sm },
  assignmentRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    flexWrap: "wrap",
    gap: space.xs,
  },
});
