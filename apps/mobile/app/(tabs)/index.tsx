import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useState } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { AccountBalancesStrip } from "../../src/components/AccountBalancesStrip";
import { ForecastCard } from "../../src/components/ForecastCard";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { QuickAddSheet } from "../../src/components/QuickAddSheet";
import { SafeToSpendHeroCard } from "../../src/components/SafeToSpendHeroCard";
import { ScreenContainer, SCREEN_EDGES_WITH_TOP } from "../../src/components/ScreenContainer";
import { UpcomingBillsCard } from "../../src/components/UpcomingBillsCard";
import {
  Badge,
  Card,
  CardLabel,
  DataRow,
  Fab,
  IconButton,
  Money,
  ScreenHeader,
  StatTile,
} from "../../src/components/ui";
import { useAccounts } from "../../src/hooks/useAccounts";
import { useCategories } from "../../src/hooks/useCategories";
import { FORECAST_CARD_HORIZON_MONTHS, useForecast } from "../../src/hooks/useForecast";
import { useMealWeeks, useMeals } from "../../src/hooks/useLebensmittel";
import { useReviewInbox } from "../../src/hooks/useReview";
import { useTransactions } from "../../src/hooks/useTransactions";
import { isPendingTransaction } from "../../src/offline/pendingTransactions";
import { MIN_TOUCH, space, typeScale, usePrivacy, useTheme } from "../../src/theme";
import { formatTransactionDayLabel } from "../../src/utils/dates";
import { useWeekSelection } from "../../src/utils/useWeekSelection";

const LINK_HIT_SLOP = { top: 12, bottom: 12, left: 12, right: 12 };

// "Samstag, 15. August" - src/utils/dates.ts kennt bisher nur Formate MIT Jahr.
// In der Kopfzeile von "Heute" waere das Jahr Beiwerk: der Bildschirm zeigt
// ausschliesslich den heutigen Tag.
const HEADER_DATE_FORMATTER = new Intl.DateTimeFormat("de-DE", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

/** Tageszeit-Gruss wie im Showcase. */
function greetingFor(date: Date): string {
  const hour = date.getHours();
  if (hour < 11) return "Guten Morgen";
  if (hour < 18) return "Guten Tag";
  return "Guten Abend";
}

/**
 * Bildschirm "Heute" (docs/mobile_redesign_showcase.html, Abschnitt 1).
 *
 * Reihenfolge ist hier die Aussage: erst "wie viel kann ich heute ausgeben",
 * dann "worauf steht es", dann "was kommt", zuletzt "was war". Alles, was
 * Aufmerksamkeit verlangt (Pruefliste, Engpass), steht ueber dem Rueckblick.
 */
export default function HeuteScreen() {
  const theme = useTheme();
  const router = useRouter();
  const privacy = usePrivacy();

  const [quickAddOpen, setQuickAddOpen] = useState(false);

  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

  const { isFetching: isForecastFetching, refetch: refetchForecast } = useForecast({
    horizonMonths: FORECAST_CARD_HORIZON_MONTHS,
    granularity: "day",
  });

  const {
    data: reviewData,
    isLoading: isReviewLoading,
    isError: isReviewError,
    error: reviewError,
    isFetching: isReviewFetching,
    refetch: refetchReview,
  } = useReviewInbox();

  const {
    data: transactions,
    isLoading: isTransactionsLoading,
    isError: isTransactionsError,
    error: transactionsError,
    isFetching: isTransactionsFetching,
    refetch: refetchTransactions,
  } = useTransactions({ limit: 3 });

  const { data: accounts } = useAccounts();
  const { data: categoryGroups } = useCategories();

  const {
    data: mealWeeks,
    isLoading: isMealWeeksLoading,
    isError: isMealWeeksError,
    error: mealWeeksError,
    isFetching: isMealWeeksFetching,
    refetch: refetchMealWeeks,
  } = useMealWeeks();
  const { currentWeekId } = useWeekSelection(mealWeeks);
  const {
    data: meals,
    isLoading: isMealsLoading,
    isError: isMealsError,
    error: mealsError,
    isFetching: isMealsFetching,
    refetch: refetchMeals,
  } = useMeals(currentWeekId ?? undefined);

  const isRefreshing =
    isForecastFetching ||
    isReviewFetching ||
    isTransactionsFetching ||
    isMealWeeksFetching ||
    isMealsFetching;

  function refetchMealsSection() {
    void refetchMealWeeks();
    if (currentWeekId) void refetchMeals();
  }

  const onRefreshAll = () => {
    void refetchForecast();
    void refetchReview();
    void refetchTransactions();
    refetchMealsSection();
  };

  const accountNameById = new Map((accounts ?? []).map((account) => [account.id, account.name]));
  const categoryNameById = new Map(
    (categoryGroups ?? []).flatMap((group) =>
      group.categories.map((category) => [category.id, category.name] as const),
    ),
  );

  const reviewCount = reviewData?.total_count ?? 0;
  const showReviewAction = !isReviewLoading && !isReviewError && reviewCount > 0;
  const reviewActionLabel = `${reviewCount} Buchung${reviewCount === 1 ? "" : "en"} prüfen`;
  const inboxLabel = showReviewAction ? `KI-Inbox öffnen, ${reviewActionLabel}` : "KI-Inbox öffnen";

  const lastTransactions = (transactions ?? []).slice(0, 3);
  const showTransactionsEmpty =
    !isTransactionsLoading && !isTransactionsError && lastTransactions.length === 0;
  const showTransactionsPopulated =
    !isTransactionsLoading && !isTransactionsError && lastTransactions.length > 0;

  const isMealsSectionLoading = isMealWeeksLoading || isMealsLoading;
  const isMealsSectionError = isMealWeeksError || isMealsError;
  const mealsSectionErrorMessage =
    (mealWeeksError instanceof Error ? mealWeeksError.message : undefined) ??
    (mealsError instanceof Error ? mealsError.message : undefined);
  const showMealsPopulated = !isMealsSectionLoading && !isMealsSectionError;

  const mealToday = meals?.find((meal) => meal.meal_date === today);
  const mealTomorrow = meals?.find((meal) => meal.meal_date === tomorrow);

  return (
    <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
      <View style={styles.root}>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={isRefreshing}
              onRefresh={onRefreshAll}
              tintColor={theme.positive}
              colors={[theme.positiveStrong]}
              progressBackgroundColor={theme.surface}
            />
          }
        >
          <ScreenHeader
            eyebrow={HEADER_DATE_FORMATTER.format(now)}
            title={greetingFor(now)}
            actions={
              <>
                <IconButton
                  icon="search-outline"
                  accessibilityLabel="Buchungen durchsuchen"
                  onPress={() => router.push("/buchungen")}
                />
                <IconButton
                  icon={privacy.isPrivacyMode ? "eye-off-outline" : "eye-outline"}
                  tone={privacy.isPrivacyMode ? "highlight" : undefined}
                  accessibilityLabel={
                    privacy.isPrivacyMode ? "Beträge wieder anzeigen" : "Beträge verbergen"
                  }
                  onPress={privacy.toggle}
                />
                <View style={styles.inboxAction}>
                  <IconButton
                    icon="sparkles-outline"
                    accessibilityLabel={inboxLabel}
                    onPress={() => router.push("/mehr/review")}
                  />
                  {showReviewAction ? (
                    <View
                      pointerEvents="none"
                      style={[
                        styles.inboxBadge,
                        { backgroundColor: theme.warningStrong, borderColor: theme.background },
                      ]}
                    >
                      <Text style={[styles.inboxBadgeText, { color: theme.onAccent }]}>
                        {reviewCount > 99 ? "99+" : reviewCount}
                      </Text>
                    </View>
                  ) : null}
                </View>
              </>
            }
          />

          {/* 1. Wie viel kann ich heute ausgeben? */}
          <SafeToSpendHeroCard />

          {/* 2. Worauf steht es gerade? */}
          <AccountBalancesStrip />

          {/* 3. Die eine Finanzbotschaft: Liquiditaetsvorschau */}
          <ForecastCard />

          {/* 4. Was kommt in den naechsten 14 Tagen? */}
          <UpcomingBillsCard />

          {/* 5. Offene Faelle aus der KI-Pruefung */}
          {isReviewLoading ? (
            <InlineQueryState
              status="loading"
              skeletonRows={1}
              skeletonRowHeight={44}
              testID="heute-review-loading"
            />
          ) : null}
          {isReviewError ? (
            <InlineQueryState
              status="error"
              title="Buchungen zur Prüfung konnten nicht geladen werden"
              message={reviewError instanceof Error ? reviewError.message : undefined}
              onRetry={() => void refetchReview()}
              testID="heute-review-error"
            />
          ) : null}
          {showReviewAction ? (
            <Card
              tone="warning"
              density="compact"
              style={[styles.card, styles.reviewCard]}
              onPress={() => router.push("/mehr/review")}
              accessibilityLabel={reviewActionLabel}
            >
              <View style={styles.reviewRow}>
                <Ionicons name="sparkles" size={18} color={theme.warning} />
                <Text style={[styles.reviewLabel, { color: theme.warning }]}>
                  {reviewActionLabel}
                </Text>
                <Ionicons name="chevron-forward" size={18} color={theme.warning} />
              </View>
            </Card>
          ) : null}

          {/* 6. Was war zuletzt? */}
          <Card style={styles.card}>
            <View style={styles.cardHeader}>
              <CardLabel>LETZTE BUCHUNGEN</CardLabel>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Alle Buchungen"
                hitSlop={LINK_HIT_SLOP}
                onPress={() => router.push("/buchungen")}
                style={({ pressed }) => [styles.link, { opacity: pressed ? 0.6 : 1 }]}
              >
                <Text style={[styles.linkLabel, { color: theme.positive }]}>Alle Buchungen ›</Text>
              </Pressable>
            </View>

            {isTransactionsLoading ? (
              <InlineQueryState
                status="loading"
                skeletonRows={3}
                skeletonRowHeight={24}
                testID="heute-transactions-loading"
              />
            ) : null}
            {isTransactionsError ? (
              <InlineQueryState
                status="error"
                title="Buchungen konnten nicht geladen werden"
                message={transactionsError instanceof Error ? transactionsError.message : undefined}
                onRetry={() => void refetchTransactions()}
                testID="heute-transactions-error"
              />
            ) : null}
            {showTransactionsEmpty ? (
              <InlineQueryState
                status="empty"
                title="Noch keine Buchungen"
                testID="heute-transactions-empty"
              />
            ) : null}
            {showTransactionsPopulated ? (
              <View style={styles.list}>
                {lastTransactions.map((transaction, index) => {
                  const pending = isPendingTransaction(transaction);
                  const accountName =
                    accountNameById.get(transaction.account_id) ?? "Unbekanntes Konto";
                  const categoryLabel = transaction.is_split
                    ? "Mehrere Kategorien"
                    : transaction.category_id
                      ? (categoryNameById.get(transaction.category_id) ?? "Ohne Kategorie")
                      : "Ohne Kategorie";
                  const title = transaction.payee_label || accountName || "Buchung";

                  return (
                    <DataRow
                      key={transaction.id}
                      title={title}
                      subtitle={[
                        formatTransactionDayLabel(transaction.booked_date),
                        categoryLabel,
                        accountName,
                      ].join(" · ")}
                      avatarLabel={title}
                      titleBadge={
                        <>
                          {pending ? (
                            <Badge label="Vorgemerkt" tone="highlight" size="small" />
                          ) : null}
                          {transaction.is_split ? (
                            <Badge label="Split" tone="purple" size="small" />
                          ) : null}
                          {transaction.is_transfer ? (
                            <Badge label="Umbuchung" tone="info" size="small" />
                          ) : null}
                        </>
                      }
                      trailing={
                        <Money
                          minorUnits={transaction.amount_minor}
                          currency={transaction.currency}
                        />
                      }
                      // Eine noch nicht uebertragene Buchung hat serverseitig
                      // keine ID - die Detailseite koennte sie nicht laden.
                      onPress={
                        pending ? undefined : () => router.push(`/buchung/${transaction.id}`)
                      }
                      accessibilityLabel={`${title} öffnen`}
                      divider={index < lastTransactions.length - 1}
                    />
                  );
                })}
              </View>
            ) : null}
          </Card>

          {/* 7. Essensplan */}
          <Card style={styles.card}>
            <View style={styles.cardHeader}>
              <CardLabel>ESSENSPLAN</CardLabel>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Essensplan öffnen"
                hitSlop={LINK_HIT_SLOP}
                onPress={() => router.push("/mehr/essensplan")}
                style={({ pressed }) => [styles.link, { opacity: pressed ? 0.6 : 1 }]}
              >
                <Text style={[styles.linkLabel, { color: theme.positive }]}>Öffnen ›</Text>
              </Pressable>
            </View>

            {isMealsSectionLoading ? (
              <InlineQueryState
                status="loading"
                skeletonRows={2}
                skeletonRowHeight={28}
                testID="heute-meals-loading"
              />
            ) : null}
            {isMealsSectionError ? (
              <InlineQueryState
                status="error"
                title="Essensplan konnte nicht geladen werden"
                message={mealsSectionErrorMessage}
                onRetry={refetchMealsSection}
                testID="heute-meals-error"
              />
            ) : null}
            {showMealsPopulated ? (
              mealToday || mealTomorrow ? (
                <View style={styles.mealRow}>
                  <StatTile
                    label="Heute"
                    value={
                      <Text
                        style={[styles.mealName, { color: theme.textPrimary }]}
                        numberOfLines={2}
                      >
                        {mealToday?.dish_name ?? "Nichts geplant"}
                      </Text>
                    }
                  />
                  <StatTile
                    label="Morgen"
                    value={
                      <Text
                        style={[styles.mealName, { color: theme.textPrimary }]}
                        numberOfLines={2}
                      >
                        {mealTomorrow?.dish_name ?? "Nichts geplant"}
                      </Text>
                    }
                  />
                </View>
              ) : (
                <Text style={[styles.mealEmpty, { color: theme.textSecondary }]}>
                  Noch nichts geplant.
                </Text>
              )
            ) : null}
          </Card>
        </ScrollView>

        {/* Schnellerfassung: die haeufigste Handlung an der Kasse */}
        <Fab
          onPress={() => setQuickAddOpen(true)}
          accessibilityLabel="Schnellausgabe erfassen"
          testID="heute-fab"
        />

        <QuickAddSheet
          visible={quickAddOpen}
          onClose={() => setQuickAddOpen(false)}
          onSuccess={() => void refetchTransactions()}
        />
      </View>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    position: "relative",
  },
  content: {
    flexGrow: 1,
    paddingBottom: space.xxxl + 56,
    gap: space.md,
  },
  card: {
    marginHorizontal: space.lg,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  link: {
    minHeight: 20,
    justifyContent: "center",
  },
  linkLabel: {
    fontSize: typeScale.caption,
    fontWeight: "700",
  },
  inboxAction: {
    position: "relative",
  },
  inboxBadge: {
    position: "absolute",
    top: -2,
    right: -2,
    minWidth: 18,
    height: 18,
    paddingHorizontal: 4,
    borderRadius: 9,
    borderWidth: 2,
    alignItems: "center",
    justifyContent: "center",
  },
  inboxBadgeText: {
    fontSize: typeScale.nano,
    fontWeight: "800",
  },
  reviewCard: {
    minHeight: MIN_TOUCH,
    justifyContent: "center",
  },
  reviewRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  reviewLabel: {
    flex: 1,
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  list: {
    marginTop: -space.sm,
  },
  mealRow: {
    flexDirection: "row",
    gap: space.md,
  },
  mealName: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  mealEmpty: {
    fontSize: typeScale.base,
  },
});
