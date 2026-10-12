import type { Ionicons } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { Alert, FlatList, StyleSheet, Text, View } from "react-native";
import { deleteMeal } from "../../src/api/client";
import type { Meal, MealType } from "../../src/api/types";
import { useAuth } from "../../src/auth/AuthProvider";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import {
  Card,
  CardLabel,
  Chip,
  ChipRow,
  DataRow,
  IconButton,
  Money,
  PacingBar,
  StatTile,
} from "../../src/components/ui";
import { useHousehold } from "../../src/household/HouseholdProvider";
import { useMealWeekSummary, useMealWeeks, useMeals } from "../../src/hooks/useLebensmittel";
import { leading, space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import { formatDueDate, formatShortDate, parseIsoDate } from "../../src/utils/dates";
import { useWeekSelection } from "../../src/utils/useWeekSelection";

const MEAL_TYPE_LABELS: Record<MealType, string> = {
  breakfast: "Frühstück",
  lunch: "Mittagessen",
  dinner: "Abendessen",
  snack: "Snack",
};

// Reihenfolge innerhalb eines Tages: so, wie der Tag ablaeuft - nicht so, wie
// die Mahlzeiten angelegt wurden.
const MEAL_TYPE_ORDER: MealType[] = ["breakfast", "lunch", "dinner", "snack"];

const MEAL_TYPE_ICONS: Record<MealType, React.ComponentProps<typeof Ionicons>["name"]> = {
  breakfast: "cafe-outline",
  lunch: "restaurant-outline",
  dinner: "moon-outline",
  snack: "nutrition-outline",
};

const MEAL_TYPE_TONES: Record<MealType, AccentTone> = {
  breakfast: "highlight",
  lunch: "info",
  dinner: "purple",
  snack: "cyan",
};

const MEAL_TYPE_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "Alle" },
  ...MEAL_TYPE_ORDER.map((type) => ({ key: type, label: MEAL_TYPE_LABELS[type] })),
];

const weekdayFormatter = new Intl.DateTimeFormat("de-DE", { weekday: "long" });

// Kalendertag ohne UTC-Umweg: toISOString() wuerde oestlich von Greenwich
// einen Tag zurueckspringen und "Heute" auf den falschen Tag setzen.
function isoDayFromToday(offsetDays: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

type DayGroup = { date: string; meals: Meal[] };

function groupByDay(meals: Meal[]): DayGroup[] {
  const buckets = new Map<string, Meal[]>();
  for (const meal of meals) {
    const bucket = buckets.get(meal.meal_date);
    if (bucket) {
      bucket.push(meal);
    } else {
      buckets.set(meal.meal_date, [meal]);
    }
  }
  return [...buckets]
    .map(([date, dayMeals]) => ({
      date,
      meals: [...dayMeals].sort(
        (a, b) => MEAL_TYPE_ORDER.indexOf(a.meal_type) - MEAL_TYPE_ORDER.indexOf(b.meal_type),
      ),
    }))
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

function dayLabel(isoDate: string, today: string, tomorrow: string): string {
  const weekday = weekdayFormatter.format(parseIsoDate(isoDate));
  const short = formatShortDate(isoDate);
  if (isoDate === today) return `Heute · ${weekday}, ${short}`;
  if (isoDate === tomorrow) return `Morgen · ${weekday}, ${short}`;
  return `${weekday}, ${short}`;
}

function mealSubtitle(meal: Meal): string {
  const parts = [MEAL_TYPE_LABELS[meal.meal_type], `${meal.servings} Portionen`];
  const note = (meal.note ?? "").trim();
  if (note) parts.push(note);
  return parts.join(" · ");
}

// Essensplan laut Architekturplan Abschnitt 6.8 (Phase 8): Woche, Tag,
// Mahlzeit, Gericht, Portionen, geschaetzte Kosten je Woche.
export default function EssensplanScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const weeksQuery = useMealWeeks();
  const weeks = weeksQuery.data;
  const { selectedWeekId, setSelectedWeekId, currentWeekId } = useWeekSelection(weeks);
  const { data: summary } = useMealWeekSummary(currentWeekId);
  const mealsQuery = useMeals(currentWeekId);

  // Filter nach Mahlzeitentyp (Redesign-Auftrag Punkt 5: "Gerichte" mit
  // wischbaren Kategorie-Chips filtern) - das Datenmodell fuehrt bislang
  // keine frei vergebenen Gerichte-Kategorien, meal_type ist das naechst-
  // liegende vorhandene Feld dafuer.
  const [mealTypeFilter, setMealTypeFilter] = useState<string>("all");
  const filteredMeals = (mealsQuery.data ?? []).filter(
    (meal) => mealTypeFilter === "all" || meal.meal_type === mealTypeFilter,
  );
  const dayGroups = groupByDay(filteredMeals);

  const today = isoDayFromToday(0);
  const tomorrow = isoDayFromToday(1);

  const deleteMutation = useMutation({
    mutationFn: (mealId: string) =>
      deleteMeal(accessToken as string, selectedHouseholdId as string, mealId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meals", currentWeekId] });
      void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
    },
  });

  function confirmDelete(meal: Meal) {
    Alert.alert("Mahlzeit löschen?", meal.dish_name, [
      { text: "Abbrechen", style: "cancel" },
      { text: "Löschen", style: "destructive", onPress: () => deleteMutation.mutate(meal.id) },
    ]);
  }

  const budgetRatio =
    summary && summary.budget_minor > 0 ? summary.actual_minor / summary.budget_minor : null;

  return (
    <>
      <Stack.Screen
        options={{
          title: "Essensplan",
          headerRight: () => (
            <View style={styles.headerActions}>
              <IconButton
                icon="cart-outline"
                size={36}
                accessibilityLabel="Einkaufsliste öffnen"
                onPress={() => router.push("/mehr/einkaufsliste")}
              />
              {currentWeekId ? (
                <IconButton
                  icon="add"
                  size={36}
                  tone="positive"
                  accessibilityLabel="Neue Mahlzeit planen"
                  onPress={() => router.push(`/mahlzeit-neu?weekId=${currentWeekId}`)}
                />
              ) : null}
            </View>
          ),
        }}
      />
      <ScreenContainer>
        {weeksQuery.isLoading ? (
          <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={56} />
        ) : weeksQuery.isError ? (
          <InlineQueryState
            status="error"
            title="Wochen konnten nicht geladen werden"
            onRetry={() => void weeksQuery.refetch()}
          />
        ) : !weeks || weeks.length === 0 ? (
          <View style={styles.centeredState}>
            <Card accentEdge="info">
              <CardLabel>Noch keine Woche geplant</CardLabel>
              <Text style={[styles.emptyText, { color: theme.textSecondary }]}>
                Der Essensplan wird wochenweise geführt. Legen Sie die erste Woche an, danach lassen
                sich Mahlzeiten und Einkaufslisten daran hängen.
              </Text>
              <PrimaryButton
                label="Neue Woche anlegen"
                onPress={() => router.push("/lebensmittel-woche-neu")}
              />
            </Card>
          </View>
        ) : (
          <FlatList
            data={dayGroups}
            keyExtractor={(group) => group.date}
            contentContainerStyle={styles.content}
            ListHeaderComponent={
              <View style={styles.headerBlock}>
                <Card density="compact">
                  <CardLabel>Planwoche</CardLabel>
                  <ChipRow>
                    {weeks.map((week) => (
                      <Chip
                        key={week.id}
                        label={formatShortDate(week.week_start)}
                        selected={week.id === selectedWeekId}
                        onPress={() => setSelectedWeekId(week.id)}
                        accessibilityLabel={`Woche ab ${formatDueDate(week.week_start)}`}
                      />
                    ))}
                  </ChipRow>
                  <PrimaryButton
                    label="Neue Woche"
                    variant="secondary"
                    onPress={() => router.push("/lebensmittel-woche-neu")}
                  />
                </Card>

                {summary ? (
                  <Card accentTop="info">
                    <CardLabel>Wochenübersicht</CardLabel>
                    <Money
                      minorUnits={summary.remaining_vs_budget_minor}
                      size="display"
                      tone={summary.remaining_vs_budget_minor < 0 ? "warning" : "positive"}
                    />
                    <Text style={[styles.legend, { color: theme.textSecondary }]}>
                      {summary.remaining_vs_budget_minor < 0
                        ? "Wochenbudget überschritten"
                        : "Rest vom Wochenbudget"}
                    </Text>

                    {budgetRatio == null ? null : (
                      <View style={styles.pacingBlock}>
                        <PacingBar
                          progress={budgetRatio}
                          tone={budgetRatio > 1 ? "warning" : "positive"}
                          accessibilityLabel="Istkosten im Verhältnis zum Wochenbudget"
                        />
                        <Text style={[styles.legend, { color: theme.textMuted }]}>
                          {`${Math.round(budgetRatio * 100)} % des Budgets ausgegeben`}
                        </Text>
                      </View>
                    )}

                    <View style={styles.tileRow}>
                      <StatTile
                        label="BUDGET"
                        tone="info"
                        value={<Money minorUnits={summary.budget_minor} tone="plain" />}
                      />
                      <StatTile
                        label="IST"
                        tone={budgetRatio != null && budgetRatio > 1 ? "warning" : "positive"}
                        value={<Money minorUnits={summary.actual_minor} tone="plain" />}
                        caption="Verknüpfte Einkäufe"
                      />
                    </View>
                    <View style={styles.tileRow}>
                      <StatTile
                        label="PLAN MAHLZEITEN"
                        value={<Money minorUnits={summary.meals_estimated_minor} tone="plain" />}
                        caption="Geschätzte Kosten"
                      />
                      <StatTile
                        label="PLAN ARTIKEL"
                        value={<Money minorUnits={summary.items_planned_minor} tone="plain" />}
                        caption="Summe der Einkaufslisten"
                      />
                    </View>
                  </Card>
                ) : null}

                <ChipRow>
                  {MEAL_TYPE_FILTERS.map((filter) => (
                    <Chip
                      key={filter.key}
                      label={filter.label}
                      selected={filter.key === mealTypeFilter}
                      onPress={() => setMealTypeFilter(filter.key)}
                      accessibilityLabel={`Filter ${filter.label}`}
                    />
                  ))}
                </ChipRow>
              </View>
            }
            ListEmptyComponent={
              mealsQuery.isLoading ? (
                <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={48} />
              ) : mealsQuery.isError ? (
                <InlineQueryState
                  status="error"
                  title="Mahlzeiten konnten nicht geladen werden"
                  onRetry={() => void mealsQuery.refetch()}
                />
              ) : (
                <InlineQueryState
                  status="empty"
                  title="Noch keine Mahlzeiten"
                  message={
                    mealTypeFilter === "all"
                      ? "Planen Sie die erste Mahlzeit für diese Woche."
                      : "Für diesen Mahlzeitentyp ist nichts geplant."
                  }
                />
              )
            }
            renderItem={({ item: group }) => (
              <Card
                accentEdge={
                  group.date === today ? "positive" : group.date === tomorrow ? "info" : undefined
                }
              >
                <CardLabel>{dayLabel(group.date, today, tomorrow)}</CardLabel>
                {group.meals.map((meal, index) => (
                  <DataRow
                    key={meal.id}
                    title={meal.dish_name}
                    subtitle={mealSubtitle(meal)}
                    avatarIcon={MEAL_TYPE_ICONS[meal.meal_type]}
                    avatarTone={MEAL_TYPE_TONES[meal.meal_type]}
                    avatarLabel={MEAL_TYPE_LABELS[meal.meal_type]}
                    trailing={
                      <View style={styles.rowActions}>
                        {meal.estimated_cost_minor == null ? null : (
                          <Money minorUnits={meal.estimated_cost_minor} tone="plain" />
                        )}
                        <IconButton
                          icon="trash-outline"
                          size={36}
                          accessibilityLabel={`Mahlzeit löschen: ${meal.dish_name}`}
                          onPress={() => confirmDelete(meal)}
                        />
                      </View>
                    }
                    divider={index < group.meals.length - 1}
                  />
                ))}
              </Card>
            )}
          />
        )}
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  headerActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  content: {
    padding: space.lg,
    gap: space.lg,
  },
  headerBlock: {
    gap: space.lg,
  },
  centeredState: {
    flexGrow: 1,
    justifyContent: "center",
    padding: space.lg,
  },
  emptyText: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  tileRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  pacingBlock: {
    gap: space.xs,
  },
  legend: {
    fontSize: typeScale.caption,
  },
  rowActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
});
