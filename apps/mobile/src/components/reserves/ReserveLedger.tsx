import { useEffect, useState } from "react";
import { Alert, StyleSheet, Switch, Text, View } from "react-native";
import type { BudgetSummary, ReserveOverviewResponse, ReservePotResponse } from "../../api/types";
import { useConnectivity } from "../../hooks/useConnectivity";
import { useReserveActions } from "../../hooks/useReserves";
import { useHousehold } from "../../household/HouseholdProvider";
import { leading, space, typeScale, useTheme } from "../../theme";
import { formatMonthLabel } from "../../utils/dates";
import { InlineQueryState } from "../InlineQueryState";
import { PrimaryButton } from "../PrimaryButton";
import { Badge, Card, CardLabel, Money, StatTile } from "../ui";
import { ReserveBulkDepositSheet } from "./ReserveBulkDepositSheet";
import { ReserveDepositSheet } from "./ReserveDepositSheet";
import { ReserveDistributionSheet } from "./ReserveDistributionSheet";
import { ReservePotCard } from "./ReservePotCard";
import { ReserveReallocationSheet } from "./ReserveReallocationSheet";
import { ReserveWithdrawalSheet } from "./ReserveWithdrawalSheet";

type Props = {
  year: number;
  month: number;
  budget: BudgetSummary | undefined;
  overview: ReserveOverviewResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  includeInPlan: boolean;
  onIncludeInPlanChange: (next: boolean) => void;
};

export function ReserveCompactStrip({
  budget,
  onOpen,
}: {
  budget: BudgetSummary;
  onOpen: () => void;
}) {
  const theme = useTheme();
  return (
    <Card tone="positive" style={styles.screenPad}>
      {/* Sichtbare Ueberschrift statt eines unsichtbaren accessibilityLabel:
          <Card> reicht ein Label nur im antippbaren Fall durch, und der Zweck
          des Blocks soll ohnehin fuer alle lesbar sein. */}
      <CardLabel>Rücklagen im Monatsplan</CardLabel>
      <View style={styles.inlineRow}>
        <Text style={[styles.miniLabel, { color: theme.textSecondary }]}>
          Wirklich frei nach Rücklagen
        </Text>
        <Money minorUnits={budget.planning_truly_free_minor} tone="plain" />
      </View>
      <View style={styles.inlineRow}>
        <Text style={[styles.miniLabel, { color: theme.textSecondary }]}>
          Diesen Monat empfohlen
        </Text>
        <Money minorUnits={budget.total_calculational_reserves_minor} tone="plain" />
      </View>
      <PrimaryButton label="Rücklagen öffnen" onPress={onOpen} variant="secondary" size="compact" />
    </Card>
  );
}

export function ReserveLedger({
  year,
  month,
  budget,
  overview,
  isLoading,
  isError,
  onRetry,
  includeInPlan,
  onIncludeInPlanChange,
}: Props) {
  const theme = useTheme();
  const isOnline = useConnectivity();
  const { selectedHouseholdId } = useHousehold();
  const actions = useReserveActions(year, month);
  const [depositPot, setDepositPot] = useState<ReservePotResponse | null>(null);
  const [withdrawPot, setWithdrawPot] = useState<ReservePotResponse | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [distributionOpen, setDistributionOpen] = useState(false);
  const [reallocationOpen, setReallocationOpen] = useState(false);

  useEffect(() => {
    setDepositPot(null);
    setWithdrawPot(null);
    setBulkOpen(false);
    setDistributionOpen(false);
    setReallocationOpen(false);
  }, [selectedHouseholdId]);

  if (isLoading) {
    return <InlineQueryState status="loading" skeletonRows={6} skeletonRowHeight={72} />;
  }
  if (isError || !overview) {
    return (
      <InlineQueryState
        status="error"
        title="Rücklagen konnten nicht geladen werden"
        message="Bitte prüfe deine Verbindung. Die normalen Budgetdaten bleiben unverändert."
        onRetry={onRetry}
      />
    );
  }

  const contracts = overview.pots.filter((pot) => pot.kind === "contract");
  const goals = overview.pots.filter((pot) => pot.kind === "goal");
  const freePot = overview.pots.find((pot) => pot.kind === "free") ?? null;
  const writingDisabled = !isOnline || actions.isPending;
  const mutationError =
    actions.deposit.error ??
    actions.withdraw.error ??
    actions.reallocate.error ??
    actions.skip.error ??
    actions.undoSkip.error ??
    actions.preview.error;

  const confirmSkip = (pot: ReservePotResponse) => {
    Alert.alert(
      "Diesen Monat nicht zurücklegen?",
      "Der Sollwert bleibt sichtbar. Die nächste Empfehlung kann dadurch steigen.",
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: "Monat auslassen",
          onPress: () => actions.skip.mutate({ pot_id: pot.id, year, month }),
        },
      ],
    );
  };

  return (
    <View style={styles.ledger} testID="reserve-ledger">
      <Card style={styles.screenPad}>
        <CardLabel>Bestätigte Zweckzuordnung</CardLabel>
        <Text accessibilityRole="header" style={[styles.heading, { color: theme.textPrimary }]}>
          Rücklagen für {formatMonthLabel(new Date(year, month - 1, 1))}
        </Text>
        <Text style={[styles.body, { color: theme.textSecondary }]}>
          Sollwerte sind Empfehlungen. Ein echter Stand entsteht erst durch bestätigte Startwerte
          oder Bewegungen – ohne automatische Bankerkennung.
        </Text>
        <View style={styles.toggleRow}>
          <Text style={[styles.toggleLabel, { color: theme.textPrimary }]}>
            Rücklagen in den Monatsplan einrechnen
          </Text>
          <Switch
            accessibilityLabel="Rücklagen in den Monatsplan einrechnen"
            value={includeInPlan}
            onValueChange={onIncludeInPlanChange}
            trackColor={{ false: theme.border, true: theme.positiveStrong }}
          />
        </View>
      </Card>

      {includeInPlan && budget ? <PlanningEquation budget={budget} /> : null}

      <Card style={styles.screenPad}>
        <CardLabel>Monatsstand</CardLabel>
        <View style={styles.metricsRow}>
          <StatTile
            label="Diesen Monat empfohlen"
            tone="info"
            value={<Money minorUnits={overview.recommended_minor} tone="plain" />}
          />
          <StatTile
            label="Bestätigt zurückgelegt"
            tone="positive"
            value={<Money minorUnits={overview.deposited_minor} tone="plain" />}
          />
        </View>
        <View style={styles.metricsRow}>
          <StatTile
            label="Noch offen"
            tone="highlight"
            value={<Money minorUnits={overview.open_minor} tone="plain" />}
          />
          <StatTile
            label="Rücklagen gesamt"
            tone="cyan"
            value={<Money minorUnits={overview.total_balance_minor} tone="plain" />}
          />
        </View>
      </Card>

      {!isOnline ? (
        <Card tone="warning" density="compact" style={styles.screenPad}>
          <Text accessibilityRole="alert" style={[styles.body, { color: theme.warning }]}>
            Rücklagenänderungen benötigen eine Verbindung. Lesen bleibt möglich.
          </Text>
        </Card>
      ) : !actions.canEdit ? (
        <Card density="compact" style={styles.screenPad}>
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Du kannst die Rücklagen lesen. Schreibaktionen sind für deine Haushaltsrolle
            ausgeblendet.
          </Text>
        </Card>
      ) : null}
      {mutationError ? (
        <Card tone="warning" density="compact" style={styles.screenPad}>
          <Text accessibilityRole="alert" style={[styles.body, { color: theme.warning }]}>
            {mutationError instanceof Error
              ? mutationError.message
              : "Rücklagenaktion fehlgeschlagen."}
          </Text>
        </Card>
      ) : null}

      {actions.canEdit ? (
        <View style={[styles.screenPad, styles.primaryAction]}>
          <PrimaryButton
            label="Alle Monatsraten zurücklegen"
            onPress={() => setBulkOpen(true)}
            disabled={writingDisabled || overview.open_minor <= 0}
          />
          <Text style={[styles.miniLabel, { color: theme.textSecondary }]}>
            Öffnet zuerst eine editierbare Bestätigung. Das Soll bleibt Referenz.
          </Text>
        </View>
      ) : null}

      <ReserveGroup
        title="Fixkosten-Rücklagen"
        empty="Keine aktiven Fixkosten-Rücklagen in diesem Monat."
        pots={contracts}
        canEdit={actions.canEdit}
        disabled={writingDisabled}
        onDeposit={setDepositPot}
        onWithdraw={setWithdrawPot}
        onSkip={confirmSkip}
        onUndoSkip={(pot) => actions.undoSkip.mutate({ pot_id: pot.id, year, month })}
      />
      <ReserveGroup
        title="Spartöpfe & Wünsche"
        empty="Keine einbezogenen Spartöpfe für diesen Monat."
        pots={goals}
        canEdit={actions.canEdit}
        disabled={writingDisabled}
        onDeposit={setDepositPot}
        onWithdraw={setWithdrawPot}
        onSkip={confirmSkip}
        onUndoSkip={(pot) => actions.undoSkip.mutate({ pot_id: pot.id, year, month })}
      />

      {freePot ? (
        <Card tone="positive" style={styles.screenPad}>
          <View style={styles.freeHeader}>
            <View style={styles.freeCopy}>
              <Text
                accessibilityRole="header"
                style={[styles.groupTitle, { color: theme.textPrimary }]}
              >
                Freie Rücklage
              </Text>
              <Text style={[styles.body, { color: theme.textSecondary }]}>
                Kein Soll. Geld bleibt sichtbar, bis du es bewusst zuordnest.
              </Text>
            </View>
            <Money minorUnits={freePot.balance_minor} size="medium" tone="plain" />
          </View>
          {actions.canEdit ? (
            <View style={styles.freeActions}>
              <View style={styles.freeAction}>
                <PrimaryButton
                  label="Einzahlung erfassen"
                  onPress={() => setDepositPot(freePot)}
                  disabled={writingDisabled}
                  variant="secondary"
                  size="compact"
                />
              </View>
              <View style={styles.freeAction}>
                <PrimaryButton
                  label="Freien Stand verteilen"
                  onPress={() => setReallocationOpen(true)}
                  disabled={writingDisabled || freePot.balance_minor <= 0}
                  variant="secondary"
                  size="compact"
                />
              </View>
              <View style={styles.freeAction}>
                <PrimaryButton
                  label="Entnehmen"
                  onPress={() => setWithdrawPot(freePot)}
                  disabled={writingDisabled || freePot.balance_minor <= 0}
                  variant="secondary"
                  size="compact"
                />
              </View>
            </View>
          ) : null}
        </Card>
      ) : null}

      {actions.canEdit ? (
        <Card style={styles.screenPad}>
          <CardLabel>Größere Summe</CardLabel>
          <Text
            accessibilityRole="header"
            style={[styles.groupTitle, { color: theme.textPrimary }]}
          >
            Einzahlung bewusst verteilen
          </Text>
          <Text style={[styles.body, { color: theme.textSecondary }]}>
            Zum Beispiel 20.000 €: initial bleibt alles frei. Erst der ausdrückliche Vorschlag und
            danach deine Bestätigung schreiben eine Bewegung.
          </Text>
          <PrimaryButton
            label="Einzahlung verteilen"
            onPress={() => setDistributionOpen(true)}
            disabled={writingDisabled}
            variant="secondary"
          />
        </Card>
      ) : null}

      <ReserveDepositSheet
        visible={!!depositPot}
        pot={depositPot}
        year={year}
        month={month}
        loading={actions.deposit.isPending}
        onClose={() => setDepositPot(null)}
        onConfirm={(input) => actions.deposit.mutateAsync(input)}
      />
      <ReserveWithdrawalSheet
        visible={!!withdrawPot}
        pot={withdrawPot}
        year={year}
        month={month}
        loading={actions.withdraw.isPending}
        onClose={() => setWithdrawPot(null)}
        onConfirm={(input) => actions.withdraw.mutateAsync(input)}
      />
      <ReserveBulkDepositSheet
        visible={bulkOpen}
        pots={overview.pots}
        year={year}
        month={month}
        loading={actions.deposit.isPending}
        onClose={() => setBulkOpen(false)}
        onConfirm={(input) => actions.deposit.mutateAsync(input)}
      />
      <ReserveDistributionSheet
        visible={distributionOpen}
        pots={overview.pots}
        year={year}
        month={month}
        previewLoading={actions.preview.isPending}
        confirmLoading={actions.deposit.isPending}
        onClose={() => setDistributionOpen(false)}
        onPreview={(input) => actions.preview.mutateAsync(input)}
        onConfirm={(input) => actions.deposit.mutateAsync(input)}
      />
      <ReserveReallocationSheet
        visible={reallocationOpen}
        pots={overview.pots}
        year={year}
        month={month}
        loading={actions.reallocate.isPending}
        onClose={() => setReallocationOpen(false)}
        onConfirm={(input) => actions.reallocate.mutateAsync(input)}
      />
    </View>
  );
}

function PlanningEquation({ budget }: { budget: BudgetSummary }) {
  const theme = useTheme();
  const rows = [
    ["+", "Erwartete Einnahmen", budget.planning_expected_income_minor],
    ["−", "Regelmäßige Fixkosten", budget.regular_fixed_costs_monthly_minor],
    ["−", "Fixkosten-Rücklagen", budget.fixed_cost_reserves_monthly_minor],
    ["−", "Variable Monatsbudgets", budget.variable_planned_budget_minor],
    ["−", "Spartöpfe & Wünsche", budget.goal_reserves_monthly_minor],
  ] as const;

  return (
    <Card tone="highlight" style={styles.screenPad}>
      <CardLabel>Rechnerische Planung · keine Buchung</CardLabel>
      <Text accessibilityRole="header" style={[styles.groupTitle, { color: theme.textPrimary }]}>
        Was nach allen Planungsschichten wirklich frei bleibt
      </Text>
      {rows.map(([sign, label, amount]) => (
        <View key={label} style={[styles.equationRow, { borderBottomColor: theme.border }]}>
          <Text style={[styles.sign, { color: theme.textSecondary }]}>{sign}</Text>
          <Text style={[styles.equationLabel, { color: theme.textPrimary }]}>{label}</Text>
          <Money minorUnits={amount} tone="plain" />
        </View>
      ))}
      <View style={styles.inlineRow}>
        <Text style={[styles.groupTitle, { color: theme.textPrimary }]}>Wirklich frei</Text>
        <Money
          minorUnits={budget.planning_truly_free_minor}
          size="medium"
          tone={budget.planning_truly_free_minor < 0 ? "warning" : "positive"}
        />
      </View>
      <Text style={[styles.miniLabel, { color: theme.textSecondary }]}>
        Variable Monatsbudgets sind die übrigen Kategorie-Planbeträge. Bereits über Verträge oder
        Spartöpfe berücksichtigte Kategorien sind herausgerechnet. Das ist kein Kontostand.
      </Text>
    </Card>
  );
}

function ReserveGroup(props: {
  title: string;
  empty: string;
  pots: ReservePotResponse[];
  canEdit: boolean;
  disabled: boolean;
  onDeposit: (pot: ReservePotResponse) => void;
  onWithdraw: (pot: ReservePotResponse) => void;
  onSkip: (pot: ReservePotResponse) => void;
  onUndoSkip: (pot: ReservePotResponse) => void;
}) {
  const theme = useTheme();
  return (
    <View style={styles.group}>
      <View style={[styles.screenPad, styles.inlineRow]}>
        <Text accessibilityRole="header" style={[styles.groupTitle, { color: theme.textPrimary }]}>
          {props.title}
        </Text>
        <Badge label={`${props.pots.length} Töpfe`} />
      </View>
      {props.pots.length === 0 ? (
        <Text style={[styles.screenPad, styles.body, { color: theme.textSecondary }]}>
          {props.empty}
        </Text>
      ) : (
        props.pots.map((pot) => (
          <ReservePotCard
            key={pot.id}
            pot={pot}
            canEdit={props.canEdit}
            actionsDisabled={props.disabled}
            onDeposit={() => props.onDeposit(pot)}
            onWithdraw={() => props.onWithdraw(pot)}
            onSkip={() => props.onSkip(pot)}
            onUndoSkip={() => props.onUndoSkip(pot)}
          />
        ))
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  ledger: { gap: space.md, paddingBottom: space.xxxl },
  screenPad: { marginHorizontal: space.lg },
  heading: { fontSize: typeScale.large, fontWeight: "700" },
  groupTitle: { fontSize: typeScale.medium, fontWeight: "700", flexShrink: 1 },
  body: { fontSize: typeScale.body, lineHeight: leading(typeScale.body) },
  miniLabel: { fontSize: typeScale.micro, lineHeight: leading(typeScale.micro) },
  inlineRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  toggleRow: {
    minHeight: 48,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.md,
  },
  toggleLabel: { flex: 1, fontSize: typeScale.body, fontWeight: "600" },
  equationRow: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  sign: { width: 14, fontSize: typeScale.base, fontWeight: "700" },
  equationLabel: { flex: 1, fontSize: typeScale.body, fontWeight: "600" },
  metricsRow: { flexDirection: "row", gap: space.sm },
  primaryAction: { gap: space.xs },
  group: { gap: space.sm },
  freeHeader: { flexDirection: "row", alignItems: "flex-start", gap: space.md },
  freeCopy: { flex: 1, gap: space.xs },
  freeActions: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  freeAction: { flexGrow: 1, flexBasis: "30%" },
});
