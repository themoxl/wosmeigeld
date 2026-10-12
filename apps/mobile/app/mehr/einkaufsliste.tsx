import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { Alert, FlatList, StyleSheet, Text, TextInput, View } from "react-native";
import {
  createShoppingItem,
  createShoppingList,
  deleteShoppingItem,
  linkPurchase,
  unlinkPurchase,
  updateShoppingItem,
} from "../../src/api/client";
import type { ShoppingItem, ShoppingList, ShoppingListStatus } from "../../src/api/types";
import { useAuth } from "../../src/auth/AuthProvider";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import {
  Badge,
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
import { useMealWeeks, useShoppingItems, useShoppingLists } from "../../src/hooks/useLebensmittel";
import { useTransactions } from "../../src/hooks/useTransactions";
import { leading, radii, space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import { formatDueDate, formatShortDate, parseIsoDate } from "../../src/utils/dates";
import { useWeekSelection } from "../../src/utils/useWeekSelection";

const STATUS_LABELS: Record<ShoppingListStatus, string> = {
  planning: "In Planung",
  shopping: "Beim Einkauf",
  completed: "Erledigt",
};

const STATUS_TONES: Record<ShoppingListStatus, AccentTone> = {
  planning: "neutral",
  shopping: "info",
  completed: "positive",
};

// Bewusst NICHT ueber toISOString(): das rechnet in UTC um und wuerde in
// Zeitzonen oestlich von Greenwich einen Tag zurueckspringen, obwohl hier ein
// reiner Kalendertag gemeint ist.
function toIsoDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function addDays(isoDate: string, days: number): string {
  const date = parseIsoDate(isoDate);
  date.setDate(date.getDate() + days);
  return toIsoDay(date);
}

type ItemGroup = { key: string; label: string | null; items: ShoppingItem[] };

/**
 * Artikel nach Kategorie buendeln - im Laden laeuft man Regal fuer Regal ab,
 * nicht in Eingabereihenfolge. Ohne gepflegte Kategorien bleibt es bewusst
 * eine einzige Liste, statt jeden Artikel unter "Ohne Kategorie" zu haengen.
 */
function groupByCategory(items: ShoppingItem[]): ItemGroup[] {
  const hasCategory = items.some((item) => (item.category ?? "").trim().length > 0);
  if (!hasCategory) {
    return [{ key: "alle", label: null, items }];
  }

  const buckets = new Map<string, ShoppingItem[]>();
  for (const item of items) {
    const key = (item.category ?? "").trim() || "Ohne Kategorie";
    const bucket = buckets.get(key);
    if (bucket) {
      bucket.push(item);
    } else {
      buckets.set(key, [item]);
    }
  }
  return [...buckets].map(([label, groupItems]) => ({ key: label, label, items: groupItems }));
}

/** Mengenangabe fuer die zweite Zeile - "1" ohne Einheit sagt nichts und entfaellt. */
function quantityLabel(item: ShoppingItem): string | undefined {
  const quantity = (item.quantity ?? "").trim();
  const unit = (item.unit ?? "").trim();
  if (unit) return `${quantity || "1"} ${unit}`;
  return quantity && quantity !== "1" ? quantity : undefined;
}

// Einkaufsliste laut Architekturplan Abschnitt 6.8 (Phase 8): Artikel mit
// Plan-/Istpreis je Einkaufsliste, plus Verknuepfung zu echten
// Banktransaktionen (kein kuenstliches Aufteilen eines Kontoumsatzes).
export default function EinkaufslisteScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const weeksQuery = useMealWeeks();
  const weeks = weeksQuery.data;
  const { selectedWeekId, setSelectedWeekId, currentWeekId } = useWeekSelection(weeks);
  const listsQuery = useShoppingLists(currentWeekId);

  const createListMutation = useMutation({
    mutationFn: () =>
      createShoppingList(accessToken as string, selectedHouseholdId as string, {
        meal_week_id: currentWeekId as string,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shopping-lists", currentWeekId] });
    },
  });

  const weekStart = weeks?.find((week) => week.id === currentWeekId)?.week_start;

  return (
    <>
      <Stack.Screen options={{ title: "Einkaufsliste" }} />
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
                Eine Einkaufsliste gehört immer zu einer Planwoche. Legen Sie zuerst eine Woche im
                Essensplan an.
              </Text>
              <PrimaryButton
                label="Neue Woche anlegen"
                onPress={() => router.push("/lebensmittel-woche-neu")}
              />
            </Card>
          </View>
        ) : (
          <FlatList
            data={listsQuery.data ?? []}
            keyExtractor={(item) => item.id}
            contentContainerStyle={styles.content}
            ListHeaderComponent={
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
                  label="Neue Einkaufsliste"
                  variant="secondary"
                  onPress={() => createListMutation.mutate()}
                  loading={createListMutation.isPending}
                />
              </Card>
            }
            ListEmptyComponent={
              listsQuery.isLoading ? (
                <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={48} />
              ) : listsQuery.isError ? (
                <InlineQueryState
                  status="error"
                  title="Einkaufslisten konnten nicht geladen werden"
                  onRetry={() => void listsQuery.refetch()}
                />
              ) : (
                <InlineQueryState
                  status="empty"
                  title="Noch keine Einkaufsliste"
                  message="Legen Sie oben die erste Einkaufsliste für diese Woche an."
                />
              )
            }
            renderItem={({ item }) => (
              <ShoppingListCard
                list={item}
                weekStart={weekStart ?? item.created_at.slice(0, 10)}
                currentWeekId={currentWeekId as string}
              />
            )}
          />
        )}
      </ScreenContainer>
    </>
  );
}

interface ShoppingListCardProps {
  list: ShoppingList;
  weekStart: string;
  currentWeekId: string;
}

function ShoppingListCard({ list, weekStart, currentWeekId }: ShoppingListCardProps) {
  const theme = useTheme();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const [draft, setDraft] = useState("");
  const [isPickerOpen, setIsPickerOpen] = useState(false);

  const itemsQuery = useShoppingItems(list.id);
  const { data: candidateTransactions } = useTransactions(
    isPickerOpen
      ? { fromDate: addDays(weekStart, -3), toDate: addDays(weekStart, 10), limit: 50 }
      : undefined,
  );

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["shopping-items", list.id] });
    void queryClient.invalidateQueries({ queryKey: ["shopping-lists", currentWeekId] });
    void queryClient.invalidateQueries({ queryKey: ["meal-week-summary", currentWeekId] });
  };

  const createItemMutation = useMutation({
    mutationFn: (name: string) =>
      createShoppingItem(accessToken as string, selectedHouseholdId as string, {
        shopping_list_id: list.id,
        name,
      }),
    onSuccess: () => {
      setDraft("");
      invalidate();
    },
    onError: (cause: unknown) => {
      Alert.alert(
        "Artikel konnte nicht angelegt werden",
        cause instanceof Error ? cause.message : "Bitte später erneut versuchen.",
      );
    },
  });

  const togglePurchasedMutation = useMutation({
    mutationFn: (input: { itemId: string; isPurchased: boolean }) =>
      updateShoppingItem(accessToken as string, selectedHouseholdId as string, input.itemId, {
        is_purchased: input.isPurchased,
      }),
    onSuccess: invalidate,
  });

  const deleteItemMutation = useMutation({
    mutationFn: (itemId: string) =>
      deleteShoppingItem(accessToken as string, selectedHouseholdId as string, itemId),
    onSuccess: invalidate,
  });

  const linkMutation = useMutation({
    mutationFn: (transactionId: string) =>
      linkPurchase(accessToken as string, selectedHouseholdId as string, list.id, {
        transaction_id: transactionId,
      }),
    onSuccess: () => {
      invalidate();
      setIsPickerOpen(false);
    },
  });

  const unlinkMutation = useMutation({
    mutationFn: (purchaseLinkId: string) =>
      unlinkPurchase(accessToken as string, selectedHouseholdId as string, purchaseLinkId),
    onSuccess: invalidate,
    onError: (cause: unknown) => {
      Alert.alert(
        "Verknüpfung konnte nicht gelöst werden",
        cause instanceof Error ? cause.message : "Bitte später erneut versuchen.",
      );
    },
  });

  function handleQuickAdd() {
    const name = draft.trim();
    if (name.length === 0 || createItemMutation.isPending) return;
    createItemMutation.mutate(name);
  }

  function confirmDeleteItem(item: ShoppingItem) {
    Alert.alert("Artikel löschen?", item.name, [
      { text: "Abbrechen", style: "cancel" },
      { text: "Löschen", style: "destructive", onPress: () => deleteItemMutation.mutate(item.id) },
    ]);
  }

  const items = itemsQuery.data ?? [];
  const groups = groupByCategory(items);
  const openCount = items.filter((item) => !item.is_purchased).length;
  const linkedIds = new Set(list.linked_transactions.map((transaction) => transaction.id));
  const planRatio =
    list.planned_total_minor > 0 ? list.actual_total_minor / list.planned_total_minor : null;

  return (
    <Card>
      <View style={styles.cardHeader}>
        <View style={styles.cardHeading}>
          <CardLabel>Einkaufsliste</CardLabel>
          <Text style={[styles.cardTitle, { color: theme.textPrimary }]} numberOfLines={1}>
            {list.store || "Ohne Laden"}
          </Text>
        </View>
        <Badge label={STATUS_LABELS[list.status]} tone={STATUS_TONES[list.status]} />
      </View>

      <View style={styles.tileRow}>
        <StatTile
          label="PLAN"
          tone="info"
          value={<Money minorUnits={list.planned_total_minor} tone="plain" />}
          caption="Summe der Planpreise"
        />
        <StatTile
          label="IST"
          tone={planRatio != null && planRatio > 1 ? "warning" : "positive"}
          value={<Money minorUnits={list.actual_total_minor} tone="plain" />}
          caption="Verknüpfte Einkäufe"
        />
      </View>

      {planRatio == null ? null : (
        <View style={styles.pacingBlock}>
          <PacingBar
            progress={planRatio}
            tone={planRatio > 1 ? "warning" : "positive"}
            accessibilityLabel="Istkosten im Verhältnis zum Plan"
          />
          <Text style={[styles.legend, { color: theme.textMuted }]}>
            {`${Math.round(planRatio * 100)} % des Plans ausgegeben`}
          </Text>
        </View>
      )}

      <View style={styles.captureRow}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          onSubmitEditing={handleQuickAdd}
          placeholder="Artikel hinzufügen"
          placeholderTextColor={theme.textSecondary}
          accessibilityLabel="Neuer Artikel"
          returnKeyType="done"
          style={[
            styles.captureInput,
            {
              backgroundColor: theme.surfaceRaised,
              borderColor: theme.border,
              color: theme.textPrimary,
            },
          ]}
        />
        <PrimaryButton
          label="Hinzufügen"
          onPress={handleQuickAdd}
          loading={createItemMutation.isPending}
          disabled={draft.trim().length === 0}
        />
      </View>

      {itemsQuery.isLoading ? (
        <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={20} />
      ) : itemsQuery.isError ? (
        <InlineQueryState
          status="error"
          title="Artikel konnten nicht geladen werden"
          onRetry={() => void itemsQuery.refetch()}
        />
      ) : items.length === 0 ? (
        <InlineQueryState
          status="empty"
          title="Noch keine Artikel"
          message="Tragen Sie oben den ersten Artikel ein."
        />
      ) : (
        <>
          <Text style={[styles.legend, { color: theme.textMuted }]}>
            {openCount === 0
              ? `Alle ${items.length} Artikel erledigt`
              : `${openCount} von ${items.length} Artikeln offen`}
          </Text>
          {groups.map((group) => (
            <View key={group.key}>
              {group.label ? <CardLabel>{group.label}</CardLabel> : null}
              {group.items.map((item, index) => (
                <View key={item.id} style={item.is_purchased ? styles.doneRow : null}>
                  <DataRow
                    title={item.name}
                    titleStyle={item.is_purchased ? styles.doneTitle : null}
                    subtitle={quantityLabel(item)}
                    leading={
                      <View style={styles.checkbox}>
                        <Ionicons
                          name={item.is_purchased ? "checkmark-circle" : "square-outline"}
                          size={26}
                          color={item.is_purchased ? theme.positive : theme.textMuted}
                        />
                      </View>
                    }
                    trailing={
                      <View style={styles.rowActions}>
                        {item.planned_price_minor == null ? null : (
                          <Money minorUnits={item.planned_price_minor} tone="plain" />
                        )}
                        <IconButton
                          icon="trash-outline"
                          size={36}
                          accessibilityLabel={`Artikel löschen: ${item.name}`}
                          onPress={() => confirmDeleteItem(item)}
                        />
                      </View>
                    }
                    divider={index < group.items.length - 1}
                    accessibilityLabel={`${item.name}, ${item.is_purchased ? "erledigt" : "offen"}`}
                    onPress={() =>
                      togglePurchasedMutation.mutate({
                        itemId: item.id,
                        isPurchased: !item.is_purchased,
                      })
                    }
                  />
                </View>
              ))}
            </View>
          ))}
        </>
      )}

      <View style={styles.linksBlock}>
        <CardLabel>Verknüpfte Einkäufe</CardLabel>
        {list.linked_transactions.length === 0 ? (
          <Text style={[styles.legend, { color: theme.textMuted }]}>
            Noch keine Buchung verknüpft.
          </Text>
        ) : (
          list.linked_transactions.map((transaction, index) => (
            <DataRow
              key={transaction.id}
              title={transaction.payee_label || "Ohne Empfänger"}
              subtitle={formatDueDate(transaction.booked_date)}
              avatarLabel={transaction.payee_label || "Einkauf"}
              trailing={
                <View style={styles.rowActions}>
                  <Money minorUnits={transaction.amount_minor} />
                  <IconButton
                    icon="close-circle-outline"
                    size={36}
                    accessibilityLabel={`Verknüpfung lösen: ${transaction.payee_label || "Buchung"}`}
                    onPress={() => unlinkMutation.mutate(transaction.id)}
                  />
                </View>
              }
              divider={index < list.linked_transactions.length - 1}
            />
          ))
        )}

        <PrimaryButton
          label={isPickerOpen ? "Auswahl schließen" : "Buchung verknüpfen"}
          variant="secondary"
          onPress={() => setIsPickerOpen((open) => !open)}
        />

        {!isPickerOpen
          ? null
          : (candidateTransactions ?? [])
              .filter((transaction) => !linkedIds.has(transaction.id))
              .map((transaction) => (
                <DataRow
                  key={transaction.id}
                  title={transaction.payee_label || "Ohne Empfänger"}
                  subtitle={formatDueDate(transaction.booked_date)}
                  avatarLabel={transaction.payee_label || "Buchung"}
                  trailing={<Money minorUnits={transaction.amount_minor} />}
                  trailingCaption="Antippen zum Verknüpfen"
                  accessibilityLabel={`Buchung verknüpfen: ${transaction.payee_label || "Ohne Empfänger"}`}
                  onPress={() => linkMutation.mutate(transaction.id)}
                />
              ))}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
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
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  cardHeading: {
    flexShrink: 1,
    gap: space.hair,
  },
  cardTitle: {
    fontSize: typeScale.large,
    fontWeight: "700",
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
  captureRow: {
    flexDirection: "row",
    alignItems: "stretch",
    gap: space.sm,
  },
  captureInput: {
    flex: 1,
    minHeight: 48,
    borderWidth: 1,
    borderRadius: radii.md,
    paddingHorizontal: space.md,
    fontSize: typeScale.base,
  },
  checkbox: {
    width: 28,
    alignItems: "center",
  },
  rowActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  // Erledigte Artikel bleiben lesbar, treten aber zurueck - zusammen mit dem
  // Durchstreichen traegt hier nicht die Farbe allein die Bedeutung.
  doneRow: {
    opacity: 0.55,
  },
  doneTitle: {
    textDecorationLine: "line-through",
  },
  linksBlock: {
    gap: space.xs,
  },
});
