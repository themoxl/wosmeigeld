import { useRouter } from "expo-router";
import { StyleSheet, Text, View } from "react-native";
import { useBudgetSummary } from "../hooks/useBudget";
import { useTransactions } from "../hooks/useTransactions";
import { space, typeScale, usePrivacy, useTheme, type AccentTone } from "../theme";
import { toIsoDate } from "../utils/dates";
import { formatMinorUnits } from "../utils/money";
import { InlineQueryState } from "./InlineQueryState";
import { Badge, Card, CardLabel, Money, PacingBar } from "./ui";

/**
 * Leitkarte von "Heute" (Showcase, Abschnitt 1): beantwortet die eine Frage,
 * wegen der man die App im Alltag oeffnet - "wie viel kann ich heute noch
 * ausgeben?".
 *
 * Die Zahl ist bewusst der Budgetrest geteilt durch die RESTLICHEN Tage des
 * Monats, nicht durch alle: am 28. eines Monats ist ein Dreissigstel des
 * Budgets keine brauchbare Auskunft mehr.
 */
export function SafeToSpendHeroCard() {
  const theme = useTheme();
  const router = useRouter();
  const privacy = usePrivacy();

  const now = new Date();
  const currentYear = now.getFullYear();
  const currentMonth = now.getMonth() + 1;
  const currentDay = now.getDate();
  const totalDaysInMonth = new Date(currentYear, currentMonth, 0).getDate();
  const daysLeft = Math.max(1, totalDaysInMonth - currentDay + 1);

  const budgetQuery = useBudgetSummary(currentYear, currentMonth);

  // "Heute ausgegeben" steht in keiner Kennzahl der API - es entsteht erst aus
  // den Buchungen des heutigen Tages. Eigene, tagesscharfe Abfrage statt einer
  // Schaetzung aus dem Monatswert.
  const todayIso = toIsoDate(now);
  const todayQuery = useTransactions({ fromDate: todayIso, toDate: todayIso });

  if (budgetQuery.isLoading) {
    return (
      <Card style={styles.card}>
        <CardLabel>HEUTE FREI VERFÜGBAR</CardLabel>
        <InlineQueryState
          status="loading"
          skeletonRows={3}
          skeletonRowHeight={20}
          testID="heute-safe-to-spend-loading"
        />
      </Card>
    );
  }

  if (budgetQuery.isError) {
    return (
      <Card tone="warning" style={styles.card}>
        <InlineQueryState
          status="error"
          title="Budget konnte nicht geladen werden"
          message={budgetQuery.error instanceof Error ? budgetQuery.error.message : undefined}
          onRetry={() => void budgetQuery.refetch()}
          testID="heute-safe-to-spend-error"
        />
      </Card>
    );
  }

  const budget = budgetQuery.data;
  const remainingMinor = budget?.total_remaining_minor ?? 0;
  const totalPlanMinor = budget?.total_planned_minor ?? 0;
  const totalSpentMinor = budget?.total_spent_minor ?? 0;

  // Ausgaben des heutigen Tages: Umbuchungen zaehlen nicht als Ausgabe, und
  // eine Erstattung (positiver Betrag) mindert den Tageswert nicht kuenstlich.
  //
  // Der Datumsfilter wird hier NOCHMAL angelegt: useTransactions mischt die
  // noch nicht uebertragenen Eintraege der Offline-Queue unabhaengig vom
  // abgefragten Zeitraum dazu (pendingCreatesAsTransactions filtert nur nach
  // Konto) - eine vorgemerkte Buchung von gestern zaehlte sonst als "heute".
  const todaySpentMinor = (todayQuery.data ?? [])
    .filter(
      (transaction) =>
        transaction.booked_date === todayIso &&
        !transaction.is_transfer &&
        transaction.amount_minor < 0,
    )
    .reduce((sum, transaction) => sum + Math.abs(transaction.amount_minor), 0);

  // Der Tagessatz bezieht sich auf den Stand VOR den heutigen Ausgaben.
  // `total_remaining_minor` hat sie bereits abgezogen (budget_service:
  // remaining = geplant + Uebertrag - ausgegeben); ohne das Zurueckrechnen
  // wuerden sie unten in "Noch übrig" ein zweites Mal abgezogen, und die
  // Karte zeigte "0,00 €", waehrend die Leitzahl darueber noch Geld ausweist.
  const dailyMinor = Math.round(Math.max(0, remainingMinor + todaySpentMinor) / daysLeft);

  // Zwei Anteile, die nur zusammen etwas aussagen: der verbrauchte Teil des
  // Plans und der verstrichene Teil des Monats. Genau diese Gegenueberstellung
  // ist die Nadel im Balken.
  const spentRatio = totalPlanMinor > 0 ? totalSpentMinor / totalPlanMinor : 0;
  const paceRatio = currentDay / totalDaysInMonth;
  const spentPercent = Math.min(100, Math.max(0, Math.round(spentRatio * 100)));

  const isCritical = remainingMinor <= 0 || spentPercent > 90;
  const isOnPlan = spentRatio <= paceRatio;
  const tone: AccentTone = isCritical ? "warning" : isOnPlan ? "positive" : "highlight";
  const statusLabel = isCritical ? "Budget knapp" : isOnPlan ? "Im Plan" : "Erhöhtes Tempo";

  const leftTodayMinor = Math.max(0, dailyMinor - todaySpentMinor);
  // Bei Ladefehler oder waehrend des Ladens lieber gar keine Tageszeile als
  // eine, die faelschlich "0,00 € ausgegeben" behauptet.
  const showTodayLine = !todayQuery.isLoading && !todayQuery.isError;

  const dailyForLabel = privacy.isPrivacyMode ? "verborgen" : formatMinorUnits(dailyMinor);

  return (
    <Card
      accentEdge={tone}
      style={styles.card}
      onPress={() => router.push("/(tabs)/budget")}
      accessibilityLabel={`Heute frei verfügbar: ${dailyForLabel} pro Tag. ${statusLabel}.`}
      testID="heute-safe-to-spend"
    >
      <View style={styles.headerRow}>
        <CardLabel>HEUTE FREI VERFÜGBAR</CardLabel>
        <Badge label={statusLabel} tone={tone} />
      </View>

      <View style={styles.amountRow}>
        <Money minorUnits={dailyMinor} size="hero" tone="plain" />
        <Text style={[styles.amountSuffix, { color: theme.textSecondary }]}> / Tag</Text>
      </View>

      {showTodayLine ? (
        <View style={styles.todayRow}>
          <View style={styles.todayItem}>
            <Text style={[styles.legendLabel, { color: theme.textSecondary }]}>
              Heute ausgegeben
            </Text>
            <Money minorUnits={todaySpentMinor} tone="plain" style={styles.legendAmount} />
          </View>
          <View style={[styles.todayItem, styles.todayItemRight]}>
            <Text style={[styles.legendLabel, { color: theme.textSecondary }]}>Noch übrig</Text>
            <Money minorUnits={leftTodayMinor} tone="positive" style={styles.legendAmount} />
          </View>
        </View>
      ) : null}

      <View style={styles.pacingSection}>
        <PacingBar
          progress={spentRatio}
          pace={paceRatio}
          tone={tone}
          accessibilityLabel="Verbrauch des Monatsbudgets"
          testID="heute-pacing"
        />
        <View style={styles.legendRow}>
          <View style={styles.legendItem}>
            <Text style={[styles.legendLabel, { color: theme.textSecondary }]}>Ausgegeben:</Text>
            <Money minorUnits={totalSpentMinor} tone="plain" style={styles.legendAmount} />
            <Text style={[styles.legendLabel, { color: theme.textSecondary }]}>
              ({spentPercent} %)
            </Text>
          </View>
          <View style={styles.legendItem}>
            <Text style={[styles.legendLabel, { color: theme.textSecondary }]}>Rest:</Text>
            <Money
              minorUnits={remainingMinor}
              tone={remainingMinor < 0 ? "warning" : "positive"}
              style={styles.legendAmount}
            />
          </View>
        </View>
        {/* Ohne diesen Satz waere die Nadel eine Linie ohne Bedeutung. */}
        <Text style={[styles.needleNote, { color: theme.textMuted }]}>
          Nadel: Tag {currentDay} von {totalDaysInMonth}
        </Text>
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    marginHorizontal: space.lg,
  },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: space.sm,
  },
  amountRow: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  amountSuffix: {
    fontSize: typeScale.base,
    fontWeight: "500",
  },
  todayRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: space.sm,
  },
  todayItem: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: space.xs,
    flexShrink: 1,
  },
  todayItemRight: {
    justifyContent: "flex-end",
  },
  pacingSection: {
    gap: space.sm,
  },
  legendRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: space.sm,
  },
  legendItem: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: space.xs,
    flexShrink: 1,
  },
  legendLabel: {
    fontSize: typeScale.caption,
  },
  legendAmount: {
    fontSize: typeScale.caption,
    fontWeight: "700",
  },
  needleNote: {
    fontSize: typeScale.micro,
  },
});
