import { useEffect, useMemo, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import type { CategoryBudgetStatus } from "../api/types";
import { useAccounts } from "../hooks/useAccounts";
import { useTransactionsPage } from "../hooks/useTransactions";
import { letterSpacingLabel, space, typeScale, useTheme } from "../theme";
import {
  calcProgress,
  describeExpenseCategory,
  describePlanBasis,
} from "../utils/budgetPresentation";
import { formatMonthLabel, monthRange } from "../utils/dates";
import { BUDGET_REMAINING_LABEL } from "../utils/financeLabels";
import { formatMinorUnitsForInput, parseAmountToMinorUnits } from "../utils/money";
import { InlineQueryState } from "./InlineQueryState";
import { PrimaryButton } from "./PrimaryButton";
import { SheetModal } from "./SheetModal";
import { TextField } from "./TextField";
import { TransactionRow } from "./TransactionRow";
import { Card, CardLabel, Chip, ChipRow, Money, PacingBar } from "./ui";

type Props = {
  category: CategoryBudgetStatus | null;
  visible: boolean;
  onClose: () => void;
  onSavePlan: (newAmountStr: string) => void;
  isSaving?: boolean;
  /**
   * Der im Budget angezeigte Monat. Die Kategorie selbst traegt kein Datum -
   * ohne diese Angabe wuesste das Sheet nicht, welchen Zeitraum die
   * Buchungsliste darunter zeigen soll.
   */
  year: number;
  month: number;
  /**
   * Monatsfortschritt 0..1 fuer die Soll-Nadel. Kommt vom Budget-Bildschirm,
   * weil nur der weiss, ob der angezeigte Monat der laufende ist.
   */
  pace?: number;
  /**
   * Fehler des letzten Speicherversuchs. Er MUSS hier stehen und nicht auf
   * dem Bildschirm darunter: SheetModal rendert ein react-native <Modal> und
   * verdeckt damit alles, was der Bildschirm selbst zeichnet.
   */
  error?: string | null;
};

/** Anzahl der Buchungen, die im Sheet als Beleg fuer die Zahl stehen. */
const TRANSACTION_PREVIEW_COUNT = 5;

/** Schnellanpassung des Planbetrags in Cent - Reihenfolge wie im Showcase. */
const QUICK_ADJUST_MINOR = [-5000, -1000, 1000, 5000] as const;

function quickAdjustLabel(deltaMinor: number): string {
  const sign = deltaMinor < 0 ? "−" : "+";
  return `${sign}${Math.abs(deltaMinor) / 100} €`;
}

export function CategoryDrilldownSheet({
  category,
  visible,
  onClose,
  onSavePlan,
  isSaving,
  year,
  month,
  pace,
  error,
}: Props) {
  const theme = useTheme();
  const accountsQuery = useAccounts();
  const accounts = accountsQuery?.data;

  const range = useMemo(() => monthRange(year, month), [year, month]);
  // Gefiltert abfragen statt "die 50 neuesten des Haushalts holen und lokal
  // sieben": ohne Kategorie- und Zeitraumfilter blieb dieses Fenster bei
  // normalem Buchungsaufkommen fuer die meisten Kategorien leer, und das
  // Sheet behauptete trotzdem "diesen Monat noch keine Buchungen".
  const txQuery = useTransactionsPage({
    categoryId: category?.category_id,
    fromDate: range.fromDate,
    toDate: range.toDate,
  });

  const [inputAmount, setInputAmount] = useState<string>("0,00");
  const [currentPlanMinor, setCurrentPlanMinor] = useState<number>(0);

  // Sync state when category changes
  useEffect(() => {
    if (category) {
      setCurrentPlanMinor(category.planned_minor);
      setInputAmount(formatMinorUnitsForInput(category.planned_minor));
    }
  }, [category]);

  const accountNameById = useMemo(() => {
    return new Map((accounts ?? []).map((account) => [account.id, account.name]));
  }, [accounts]);

  const categoryTransactions = useMemo(
    () => txQuery.items.slice(0, TRANSACTION_PREVIEW_COUNT),
    [txQuery.items],
  );

  if (!category) return null;

  const spentMinor = category.spent_minor;
  // Dieselbe Aufbereitung wie die Kategoriekarte, aus der das Sheet geoeffnet
  // wurde (app/(tabs)/budget.tsx): Zielgroesse ist Plan + Uebertrag. Ohne den
  // Uebertrag stuenden unter demselben Wort zwei verschiedene Zahlen.
  const serverDisplay = describeExpenseCategory(category);
  const planBasis = describePlanBasis(currentPlanMinor, category.rollover_minor);
  const planUnchanged = currentPlanMinor === category.planned_minor;
  const remainingMinor = planUnchanged
    ? serverDisplay.remainingMinor
    : planBasis.totalMinor - spentMinor;
  // calcProgress liefert bei einem Erstattungsueberschuss (spent < 0) bewusst
  // 0 statt eines negativen Anteils - genau wie die Karte darunter.
  const spentShare = calcProgress(spentMinor, planBasis.totalMinor);
  const spentPct = Math.round(spentShare * 100);

  const isOverspent = remainingMinor < 0;
  const tone = isOverspent ? "warning" : spentPct > 80 ? "highlight" : "positive";
  const monthLabel = formatMonthLabel(new Date(year, month - 1, 1));

  const handleAdjust = (deltaMinor: number) => {
    const next = Math.max(0, currentPlanMinor + deltaMinor);
    setCurrentPlanMinor(next);
    setInputAmount(formatMinorUnitsForInput(next));
  };

  const handleInputChange = (text: string) => {
    setInputAmount(text);
    try {
      const minor = parseAmountToMinorUnits(text);
      if (minor >= 0) setCurrentPlanMinor(minor);
    } catch {
      // Zwischenstaende beim Tippen ("", "-", "12,") sind kein Fehler - der
      // Planbetrag bleibt dann einfach stehen, bis der Text wieder lesbar ist.
    }
  };

  const handleSave = () => {
    onSavePlan(inputAmount);
  };

  return (
    <SheetModal
      visible={visible}
      title={category.category_name}
      size="full"
      onClose={onClose}
      footer={
        <>
          {error ? (
            <Card tone="warning" density="compact">
              <Text accessibilityRole="alert" style={[styles.error, { color: theme.warning }]}>
                {error}
              </Text>
            </Card>
          ) : null}
          <PrimaryButton
            label={isSaving ? "Wird gespeichert..." : "Speichern"}
            onPress={handleSave}
            disabled={isSaving}
          />
        </>
      }
    >
      <ScrollView
        contentContainerStyle={styles.content}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
        <Text style={[styles.subline, { color: theme.textSecondary }]}>
          {`Kategorie: ${category.category_name} · ${monthLabel}`}
        </Text>

        <Card>
          <CardLabel>{BUDGET_REMAINING_LABEL}</CardLabel>
          <Money
            minorUnits={remainingMinor}
            size="hero"
            tone={isOverspent ? "warning" : "positive"}
          />

          <PacingBar
            progress={spentShare}
            pace={pace}
            tone={tone}
            accessibilityLabel={`Budget ${category.category_name}`}
          />

          <View style={styles.metaRow}>
            <View style={styles.metaItem}>
              <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
                {serverDisplay.isSurplus ? "Mehr erstattet" : "Ausgegeben"}
              </Text>
              <Money minorUnits={Math.abs(spentMinor)} tone="plain" />
            </View>
            <View style={[styles.metaItem, styles.metaItemRight]}>
              <Text style={[styles.metaLabel, { color: theme.textSecondary }]}>
                {planBasis.basisLabel}
              </Text>
              <Money minorUnits={planBasis.totalMinor} tone="plain" />
            </View>
          </View>

          <Text style={[styles.footnote, { color: theme.textMuted }]}>
            {`${spentPct} % der Planbasis verbraucht`}
            {pace == null ? "" : ` · planmäßig wären ${Math.round(pace * 100)} %`}
          </Text>
        </Card>

        <View style={styles.section}>
          <Text style={[styles.sectionTitle, { color: theme.textSecondary }]}>
            SCHNELLANPASSUNG PLAN
          </Text>
          <ChipRow>
            {QUICK_ADJUST_MINOR.map((deltaMinor) => (
              <Chip
                key={deltaMinor}
                label={quickAdjustLabel(deltaMinor)}
                onPress={() => handleAdjust(deltaMinor)}
                accessibilityLabel={`Planbetrag um ${Math.abs(deltaMinor) / 100} Euro ${
                  deltaMinor < 0 ? "verringern" : "erhöhen"
                }`}
              />
            ))}
          </ChipRow>
          <TextField
            label="Neuer Planbetrag (EUR)"
            value={inputAmount}
            onChangeText={handleInputChange}
            keyboardType="numbers-and-punctuation"
            placeholder="0,00"
          />
        </View>

        <View style={styles.section}>
          <Text style={[styles.sectionTitle, { color: theme.textSecondary }]}>
            LETZTE BUCHUNGEN IN DIESER KATEGORIE
          </Text>
          {txQuery.isLoading ? (
            <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={48} />
          ) : txQuery.isError ? (
            <InlineQueryState
              status="error"
              message="Buchungen dieser Kategorie konnten nicht geladen werden."
              onRetry={() => void txQuery.refetch()}
            />
          ) : categoryTransactions.length === 0 ? (
            <InlineQueryState
              status="empty"
              title="Keine Buchungen"
              message={`In ${monthLabel} gibt es in dieser Kategorie noch keine Buchung.`}
            />
          ) : (
            <Card padding={space.md}>
              {categoryTransactions.map((tx) => (
                <TransactionRow
                  key={tx.id}
                  transaction={tx}
                  accountName={accountNameById.get(tx.account_id) ?? "Konto"}
                  categoryName={category.category_name}
                />
              ))}
            </Card>
          )}
        </View>
      </ScrollView>
    </SheetModal>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.lg,
    paddingBottom: space.xxxl,
  },
  subline: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  error: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  metaRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: space.md,
  },
  metaItem: {
    gap: space.hair,
  },
  metaItemRight: {
    alignItems: "flex-end",
  },
  metaLabel: {
    fontSize: typeScale.caption,
    fontWeight: "600",
  },
  footnote: {
    fontSize: typeScale.micro,
  },
  section: {
    gap: space.sm,
  },
  sectionTitle: {
    fontSize: typeScale.micro,
    fontWeight: "700",
    letterSpacing: letterSpacingLabel,
  },
});
