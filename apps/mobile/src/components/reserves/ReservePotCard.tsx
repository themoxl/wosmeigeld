import { useState } from "react";
import { Pressable, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import type { ReservePotResponse } from "../../api/types";
import { leading, space, typeScale, useTheme, type AccentTone } from "../../theme";
import { formatDueDate } from "../../utils/dates";
import { PrimaryButton } from "../PrimaryButton";
import { Avatar, Badge, Card, Money, PacingBar } from "../ui";

const STATUS_LABEL: Record<ReservePotResponse["status"], string> = {
  open: "Offen",
  partial: "Teilweise",
  done: "Erledigt",
  skipped: "Diesen Monat nicht",
};

const STATUS_TONE: Record<ReservePotResponse["status"], AccentTone> = {
  open: "neutral",
  partial: "highlight",
  done: "positive",
  skipped: "neutral",
};

const FREQUENCY_LABEL: Record<string, string> = {
  quarterly: "Quartalsweise",
  semiannual: "Halbjährlich",
  yearly: "Jährlich",
};

/**
 * Ab wann wird die Kennzahlenfolge "Soll → Bestaetigt → Stand → Naechster
 * Monat" zweizeilig (2x2) statt vierspaltig?
 *
 * Vier Werte samt Pfeilen nebeneinander brauchen Platz, den ein Telefon nicht
 * hat: bei 360 dp bleiben je Wert rund 75 dp, und ein Betrag wie
 * "234.567,89 €" passt dort nicht mehr in eine Zeile. Dieselbe Enge
 * entsteht auf breiten Geraeten, sobald der Nutzer die Systemschrift
 * vergroessert - deshalb zaehlt `fontScale` gleichberechtigt neben der Breite
 * und nicht als Nachgedanke.
 *
 * 480 dp trennt sauber: das breiteste Telefon liegt bei 430 dp, das
 * schmalste Tablet bei 744 dp.
 */
export const RESERVE_SEQUENCE_STACK_WIDTH = 480;

export function shouldStackReserveSequence(width: number, fontScale: number): boolean {
  return width < RESERVE_SEQUENCE_STACK_WIDTH || fontScale > 1;
}

type Props = {
  pot: ReservePotResponse;
  canEdit: boolean;
  actionsDisabled: boolean;
  onDeposit: () => void;
  onWithdraw: () => void;
  onSkip: () => void;
  onUndoSkip: () => void;
};

export function ReservePotCard({
  pot,
  canEdit,
  actionsDisabled,
  onDeposit,
  onWithdraw,
  onSkip,
  onUndoSkip,
}: Props) {
  const theme = useTheme();
  const { width, fontScale } = useWindowDimensions();
  const stacked = shouldStackReserveSequence(width, fontScale);
  const [historyOpen, setHistoryOpen] = useState(false);
  // Label und Betrag getrennt: als zusammengesetzter Satz lief der Wert an
  // <Money> vorbei und blieb im Privat-Modus offen stehen, waehrend Soll,
  // Bestaetigt und Stand derselben Karte zu "•••• €" wurden.
  const periodOrGoalLabel = pot.target_amount_minor
    ? pot.kind === "contract"
      ? "Periodenbetrag"
      : "Zielbetrag"
    : null;
  const dateLabel = pot.next_due_date
    ? `Fällig: ${formatDueDate(pot.next_due_date)}`
    : pot.target_date
      ? `Ziel: ${formatDueDate(pot.target_date)}`
      : null;
  const increasedNextMonth =
    pot.status !== "done" && pot.next_month_recommended_minor > pot.recommended_minor;
  // Anteil des Monatssolls, der bereits bestaetigt zurueckgelegt wurde.
  const depositShare = pot.recommended_minor > 0 ? pot.deposited_minor / pot.recommended_minor : 0;

  return (
    <Card testID={`reserve-pot-${pot.kind}`} style={styles.screenPad}>
      <View style={styles.titleRow}>
        <Avatar
          label={pot.name}
          size={34}
          icon={pot.kind === "contract" ? "document-text-outline" : "flag-outline"}
        />
        <View style={styles.titleCopy}>
          <Text accessibilityRole="header" style={[styles.title, { color: theme.textPrimary }]}>
            {pot.name}
          </Text>
          <View style={styles.metaRow}>
            <Text style={[styles.meta, { color: theme.textSecondary }]}>
              {pot.kind === "contract"
                ? (FREQUENCY_LABEL[pot.frequency ?? ""] ?? "Fixkosten-Rücklage")
                : "Spartopf"}
              {periodOrGoalLabel ? ` · ${periodOrGoalLabel}:` : ""}
            </Text>
            {periodOrGoalLabel ? (
              <Money minorUnits={pot.target_amount_minor ?? 0} tone="muted" style={styles.meta} />
            ) : null}
          </View>
          {dateLabel ? (
            <Text style={[styles.meta, { color: theme.textSecondary }]}>{dateLabel}</Text>
          ) : null}
        </View>
        <Badge label={STATUS_LABEL[pot.status]} tone={STATUS_TONE[pot.status]} />
      </View>

      {pot.recommended_minor > 0 ? (
        <PacingBar
          progress={depositShare}
          tone={pot.status === "done" ? "positive" : "highlight"}
          height={8}
          accessibilityLabel={`Monatssoll ${pot.name}`}
        />
      ) : null}

      <View
        style={stacked ? styles.sequenceStacked : styles.sequence}
        accessibilityLabel="Soll, bestätigt, Stand und nächster Monat"
      >
        <Metric label="Soll" value={pot.recommended_minor} stacked={stacked} />
        {stacked ? null : <Text style={[styles.arrow, { color: theme.textSecondary }]}>→</Text>}
        <Metric label="Bestätigt" value={pot.deposited_minor} stacked={stacked} />
        {stacked ? null : <Text style={[styles.arrow, { color: theme.textSecondary }]}>→</Text>}
        <Metric label="Stand" value={pot.balance_minor} stacked={stacked} />
        {stacked ? null : <Text style={[styles.arrow, { color: theme.textSecondary }]}>→</Text>}
        <Metric label="Nächster Monat" value={pot.next_month_recommended_minor} stacked={stacked} />
      </View>

      <View style={styles.openRow}>
        <Text style={[styles.openLabel, { color: theme.textSecondary }]}>Noch offen</Text>
        <Money minorUnits={pot.open_minor} tone={pot.open_minor > 0 ? "plain" : "positive"} />
      </View>
      {increasedNextMonth ? (
        <Text style={[styles.carryHint, { color: theme.warning }]}>
          Ein ausgelassener oder teilweiser Monat erhöht die nächste Empfehlung.
        </Text>
      ) : null}

      {canEdit ? (
        <View style={styles.actions}>
          {pot.open_minor > 0 ? (
            <View style={styles.action}>
              <PrimaryButton
                label={pot.status === "partial" ? "Betrag anpassen" : "Jetzt zurücklegen"}
                onPress={onDeposit}
                disabled={actionsDisabled}
                size="compact"
              />
            </View>
          ) : null}
          <View style={styles.action}>
            <PrimaryButton
              label="Entnehmen"
              onPress={onWithdraw}
              disabled={actionsDisabled}
              variant="secondary"
              size="compact"
            />
          </View>
          {pot.status === "skipped" ? (
            <View style={styles.action}>
              <PrimaryButton
                label="Auslassen rückgängig"
                onPress={onUndoSkip}
                disabled={actionsDisabled}
                variant="secondary"
                size="compact"
              />
            </View>
          ) : pot.open_minor > 0 ? (
            <View style={styles.action}>
              <PrimaryButton
                label="Diesen Monat nicht"
                onPress={onSkip}
                disabled={actionsDisabled}
                variant="secondary"
                size="compact"
              />
            </View>
          ) : null}
        </View>
      ) : null}

      {(pot.movements?.length ?? 0) > 0 ? (
        <View style={[styles.history, { borderTopColor: theme.border }]}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: historyOpen }}
            onPress={() => setHistoryOpen((open) => !open)}
            style={styles.historyToggle}
          >
            <Text style={[styles.historyToggleText, { color: theme.textSecondary }]}>
              {historyOpen ? "Bewegungen ausblenden" : "Letzte Bewegungen anzeigen"}
            </Text>
          </Pressable>
          {historyOpen
            ? pot.movements?.map((movement) => (
                <View key={movement.id} style={styles.movement}>
                  <View style={styles.openRow}>
                    <Text style={[styles.meta, { color: theme.textSecondary }]}>
                      {formatDueDate(movement.occurred_on)}
                    </Text>
                    <Money minorUnits={movement.amount_minor} showPlusSign />
                  </View>
                  {movement.note ? (
                    <Text style={[styles.meta, { color: theme.textSecondary }]}>
                      {movement.note}
                    </Text>
                  ) : null}
                </View>
              ))
            : null}
        </View>
      ) : null}
    </Card>
  );
}

function Metric({ label, value, stacked }: { label: string; value: number; stacked: boolean }) {
  const theme = useTheme();
  return (
    <View style={stacked ? styles.metricStacked : styles.metric}>
      <Text style={[styles.metricLabel, { color: theme.textSecondary }]}>{label}</Text>
      <Money minorUnits={value} tone="plain" style={styles.metricValue} />
    </View>
  );
}

const styles = StyleSheet.create({
  screenPad: { marginHorizontal: space.lg },
  titleRow: { flexDirection: "row", alignItems: "flex-start", gap: space.sm },
  titleCopy: { flex: 1, gap: space.hair },
  metaRow: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: space.xs },
  title: { fontSize: typeScale.medium, fontWeight: "700" },
  meta: { fontSize: typeScale.micro, lineHeight: leading(typeScale.micro) },
  sequence: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  // 2x2 statt vierspaltig: die Pfeile entfallen, weil sie ueber einen
  // Zeilenumbruch hinweg in die falsche Richtung zeigen wuerden. Die
  // Lesereihenfolge traegt die Abfolge, der accessibilityLabel nennt sie.
  sequenceStacked: {
    flexDirection: "row",
    flexWrap: "wrap",
    rowGap: space.sm,
    columnGap: space.sm,
  },
  metric: { flex: 1, gap: space.hair, minWidth: 0 },
  metricStacked: { flexBasis: "47%", flexGrow: 1, gap: space.hair, minWidth: 0 },
  metricLabel: { fontSize: typeScale.nano, fontWeight: "600" },
  metricValue: { fontSize: typeScale.caption, fontWeight: "700", flexWrap: "wrap" },
  arrow: { fontSize: typeScale.body, paddingHorizontal: 2 },
  openRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  openLabel: { fontSize: typeScale.body, fontWeight: "600" },
  carryHint: { fontSize: typeScale.micro, lineHeight: leading(typeScale.micro) },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  action: { flexGrow: 1, flexBasis: "40%" },
  history: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: space.sm },
  historyToggle: { minHeight: 44, justifyContent: "center" },
  historyToggleText: { fontSize: typeScale.body, fontWeight: "600" },
  movement: { gap: space.hair, paddingVertical: space.xs },
});
