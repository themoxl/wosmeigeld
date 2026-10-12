import { Ionicons } from "@expo/vector-icons";
import { Stack } from "expo-router";
import { useMemo, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useAccounts } from "../src/hooks/useAccounts";
import {
  useAssignGoalToSplits,
  useSavingsGoals,
  useUnassignedGoalSplits,
} from "../src/hooks/useGoals";
import { EmptyState } from "../src/components/EmptyState";
import { GoalPickerModal } from "../src/components/GoalPickerModal";
import { InlineQueryState } from "../src/components/InlineQueryState";
import { PrimaryButton } from "../src/components/PrimaryButton";
import { ScreenContainer } from "../src/components/ScreenContainer";
import { Card, DataRow, Money } from "../src/components/ui";
import { radii, space, typeScale, useTheme } from "../src/theme";

// Rueckwirkende Sparziel-Zuordnung (Architekturplan 6.7, Option (c) neben
// der direkten Zuordnung am Split in app/buchung/[id].tsx): bereits gebuchte
// Transaktionen ohne Sparziel-Zuordnung nachtraeglich einem Sparziel
// zuweisen. Anders als die Review-Inbox mit Mehrfachauswahl, da hier
// haeufig viele alte Buchungen auf einmal zugeordnet werden.
export default function ZieleZuordnenScreen() {
  const theme = useTheme();
  // Stack-Bildschirm ausserhalb der Tabs: ScreenContainer haelt per Vorgabe
  // nur links/rechts frei, die Aktionsleiste unten muss ihre Kante selbst
  // reservieren.
  const insets = useSafeAreaInsets();
  const {
    data: candidates,
    isLoading,
    isError,
    error,
    isFetching,
    refetch,
  } = useUnassignedGoalSplits();
  const { data: accounts } = useAccounts();
  const { data: goals } = useSavingsGoals();
  const assignMutation = useAssignGoalToSplits();

  const [selectedSplitIds, setSelectedSplitIds] = useState<Set<string>>(new Set());
  const [goalPickerVisible, setGoalPickerVisible] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const accountNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const account of accounts ?? []) {
      map.set(account.id, account.name);
    }
    return map;
  }, [accounts]);

  function toggleSplit(splitId: string) {
    setSelectedSplitIds((current) => {
      const next = new Set(current);
      if (next.has(splitId)) {
        next.delete(splitId);
      } else {
        next.add(splitId);
      }
      return next;
    });
  }

  function handleAssign(goalId: string | null) {
    setGoalPickerVisible(false);
    if (!goalId || selectedSplitIds.size === 0) return;
    assignMutation.mutate(
      { split_ids: Array.from(selectedSplitIds), goal_id: goalId },
      {
        onSuccess: (result) => {
          setMessage(`${result.updated} Buchung(en) zugeordnet.`);
          setSelectedSplitIds(new Set());
        },
        onError: (cause) => {
          setMessage(
            cause instanceof Error
              ? `Zuordnung fehlgeschlagen: ${cause.message}`
              : "Zuordnung fehlgeschlagen.",
          );
        },
      },
    );
  }

  const list = candidates ?? [];

  return (
    <>
      <Stack.Screen options={{ title: "Buchungen zuordnen" }} />
      <ScreenContainer>
        <FlatList
          data={list}
          keyExtractor={(item) => item.split_id}
          refreshControl={
            <RefreshControl refreshing={isFetching} onRefresh={() => void refetch()} />
          }
          contentContainerStyle={list.length === 0 ? styles.emptyContent : styles.content}
          ListEmptyComponent={
            isLoading ? (
              <InlineQueryState status="loading" skeletonRows={5} skeletonRowHeight={44} />
            ) : isError ? (
              /* Ohne diesen Zweig sah ein gescheiterter Ladevorgang aus wie
                 "alles bereits zugeordnet" - also wie ein Erfolg. */
              <InlineQueryState
                status="error"
                title="Offene Buchungen konnten nicht geladen werden"
                message={error instanceof Error ? error.message : undefined}
                onRetry={() => void refetch()}
              />
            ) : (
              <EmptyState
                title="Keine offenen Buchungen"
                description="Alle Buchungen sind entweder bereits einem Sparziel zugeordnet oder es gibt (noch) keine."
              />
            )
          }
          renderItem={({ item }) => {
            const selected = selectedSplitIds.has(item.split_id);
            const title = item.payee_label || item.note || item.booked_date;
            return (
              <Card density="compact" padding={space.sm}>
                <DataRow
                  title={title}
                  subtitle={`${accountNameById.get(item.account_id) ?? "Unbekanntes Konto"} · ${item.booked_date}`}
                  // Die Auswahl traegt hier die Bedeutung, nicht der Empfaenger -
                  // deshalb steht das Kaestchen an der Stelle der Avatarkachel.
                  leading={
                    <View
                      style={[
                        styles.checkbox,
                        {
                          borderColor: selected ? theme.primaryStrong : theme.borderStrong,
                          backgroundColor: selected ? theme.primaryStrong : "transparent",
                        },
                      ]}
                    >
                      {selected ? (
                        <Ionicons name="checkmark" size={16} color={theme.onAccent} />
                      ) : null}
                    </View>
                  }
                  trailing={<Money minorUnits={item.amount_minor} />}
                  onPress={() => toggleSplit(item.split_id)}
                  accessibilityLabel={`${title}, ${selected ? "ausgewählt" : "nicht ausgewählt"}`}
                  divider={false}
                />
              </Card>
            );
          }}
        />

        {selectedSplitIds.size > 0 ? (
          <View
            style={[
              styles.actionBar,
              {
                borderTopColor: theme.border,
                backgroundColor: theme.chrome,
                paddingBottom: Math.max(insets.bottom, space.lg),
              },
            ]}
          >
            <Text style={[styles.actionCount, { color: theme.textSecondary }]}>
              {selectedSplitIds.size} ausgewählt
            </Text>
            <PrimaryButton
              label="Sparziel wählen & zuweisen"
              onPress={() => setGoalPickerVisible(true)}
              loading={assignMutation.isPending}
            />
          </View>
        ) : null}

        {message ? (
          <Text accessibilityRole="alert" style={[styles.message, { color: theme.textSecondary }]}>
            {message}
          </Text>
        ) : null}

        <GoalPickerModal
          visible={goalPickerVisible}
          goals={goals ?? []}
          value={null}
          onChange={handleAssign}
          onClose={() => setGoalPickerVisible(false)}
          title="Sparziel für Auswahl wählen"
        />
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.sm,
  },
  emptyContent: {
    flexGrow: 1,
  },
  checkbox: {
    width: 24,
    height: 24,
    borderRadius: radii.xs,
    borderWidth: 2,
    alignItems: "center",
    justifyContent: "center",
  },
  actionBar: {
    borderTopWidth: 1,
    padding: space.lg,
    gap: space.sm,
  },
  actionCount: {
    fontSize: typeScale.body,
  },
  message: {
    fontSize: typeScale.body,
    padding: space.lg,
    paddingTop: 0,
  },
});
