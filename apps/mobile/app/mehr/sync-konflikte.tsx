import { useQueryClient } from "@tanstack/react-query";
import { Stack } from "expo-router";
import { Alert, FlatList, StyleSheet, Text, View } from "react-native";
import { EmptyState } from "../../src/components/EmptyState";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Card, CardLabel, Money } from "../../src/components/ui";
import { useCategories } from "../../src/hooks/useCategories";
import { TRANSACTION_DETAIL_QUERY_KEY } from "../../src/hooks/useTransactionDetail";
import { useQueueConflicts } from "../../src/hooks/useTransactionQueue";
import { flush } from "../../src/offline/queueProcessor";
import { discardConflictedEntry, retryConflictedEntry } from "../../src/offline/queueStore";
import {
  isVersionedEntry,
  type QueueConflict,
  type QueueEntry,
} from "../../src/offline/queueTypes";
import { leading, radii, space, typeScale, useTheme } from "../../src/theme";
import { categoryPathById } from "../../src/utils/categorySearch";

function describeKind(entry: QueueEntry): string {
  switch (entry.kind) {
    case "create":
      return "Neue Buchung";
    case "update":
      return "Geänderte Details";
    case "splits":
      return "Geänderter Betrag/Kategorien";
    case "category":
      return "Geänderte Kategorie";
    case "delete":
      return "Gelöschte Buchung";
  }
}

/** Erklaert, was passiert ist - in der Sprache der Nutzerin, nicht in HTTP-Codes. */
function describeConflict(conflict: QueueConflict): string {
  switch (conflict.kind) {
    case "version_conflict":
      return "Diese Buchung wurde an anderer Stelle geändert, während die Änderung hier noch nicht übertragen war.";
    case "transaction_archived":
      return "Diese Buchung wurde an anderer Stelle gelöscht. Die Änderung zu senden würde sie wieder herstellen.";
    case "uncertain_create":
      return "Die App wurde beendet, während diese Buchung gerade übertragen wurde. Ob sie beim Server angekommen ist, lässt sich nicht feststellen.";
    case "rejected":
      return conflict.message;
  }
}

/** Beschriftung der "trotzdem senden"-Aktion, je nach Konfliktart. */
function retryLabel(conflict: QueueConflict): string {
  switch (conflict.kind) {
    case "version_conflict":
    case "transaction_archived":
      return "Meine Version behalten";
    case "uncertain_create":
      return "Erneut anlegen";
    case "rejected":
      return "Erneut versuchen";
  }
}

/**
 * Phase 9 (Offline-Kern): Eintraege der Write Queue, die auf eine
 * Entscheidung warten - weil der Server einen Versionskonflikt gemeldet hat
 * (409), weil eine Neuanlage beim App-Ende in der Schwebe war oder weil ein
 * Eintrag dauerhaft nicht uebertragbar ist.
 *
 * Es wird bewusst NICHT automatisch zusammengefuehrt: bei Betraegen und
 * Kategorien waere jede Heuristik eine stille Falschbuchung. Stattdessen
 * werden beide Staende gezeigt und die Nutzerin entscheidet pro Eintrag.
 */
export default function SyncKonflikteScreen() {
  const theme = useTheme();
  const queryClient = useQueryClient();
  const conflicts = useQueueConflicts();
  // Nur zur Anzeige: eine Kategorie-ID im Konflikteintrag sagt der Nutzerin
  // nichts, der Pfad `Gruppe · Kategorie` schon.
  const { data: categoryGroups } = useCategories();

  function invalidateAfterResolution(): void {
    void queryClient.invalidateQueries({ queryKey: ["transactions"] });
    void queryClient.invalidateQueries({ queryKey: [TRANSACTION_DETAIL_QUERY_KEY] });
  }

  function handleKeepLocal(entry: QueueEntry): void {
    const conflict = entry.conflict;
    if (!conflict) return;
    const currentVersion =
      conflict.kind === "version_conflict" || conflict.kind === "transaction_archived"
        ? conflict.currentVersion
        : undefined;

    Alert.alert(
      retryLabel(conflict),
      conflict.kind === "uncertain_create"
        ? "Die Buchung wird noch einmal angelegt. Falls der erste Versuch doch angekommen ist, steht sie danach doppelt in der Liste."
        : "Die lokale Änderung wird erneut gesendet und überschreibt dabei den zwischenzeitlichen Stand.",
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: "Fortfahren",
          onPress: () => {
            void retryConflictedEntry(entry.entryId, currentVersion).then(() => flush());
          },
        },
      ],
    );
  }

  function handleDiscard(entry: QueueEntry): void {
    Alert.alert(
      "Änderung verwerfen?",
      "Die lokale Änderung wird nicht gesendet und geht verloren. Der Stand auf dem Server bleibt, wie er ist.",
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: "Verwerfen",
          style: "destructive",
          onPress: () => {
            void discardConflictedEntry(entry.entryId).then(invalidateAfterResolution);
          },
        },
      ],
    );
  }

  return (
    <>
      <Stack.Screen options={{ title: "Sync-Konflikte" }} />
      <ScreenContainer>
        <FlatList
          data={conflicts}
          keyExtractor={(entry) => entry.entryId}
          contentContainerStyle={styles.content}
          ListEmptyComponent={
            <EmptyState
              title="Keine Konflikte"
              description="Alle offline erfassten Änderungen konnten übertragen werden."
            />
          }
          renderItem={({ item: entry }) => {
            const conflict = entry.conflict;
            if (!conflict) return null;
            const serverState =
              conflict.kind === "version_conflict" || conflict.kind === "transaction_archived"
                ? { detail: conflict.current, version: conflict.currentVersion }
                : null;
            const server = serverState?.detail ?? null;

            return (
              <Card accentEdge="warning">
                <CardLabel>{describeKind(entry)}</CardLabel>
                <Text style={[styles.explanation, { color: theme.textSecondary }]}>
                  {describeConflict(conflict)}
                </Text>

                {serverState ? (
                  <View
                    style={[
                      styles.compareBox,
                      { borderColor: theme.border, backgroundColor: theme.surfaceRaised },
                    ]}
                  >
                    <Text style={[styles.compareLabel, { color: theme.textSecondary }]}>
                      Stand auf dem Server (Version {serverState.version})
                    </Text>
                    <View style={styles.compareValueRow}>
                      <Money
                        minorUnits={serverState.detail.amount_minor}
                        currency={serverState.detail.currency}
                        tone="plain"
                      />
                      <Text style={[styles.compareMeta, { color: theme.textSecondary }]}>
                        {serverState.detail.booked_date}
                      </Text>
                    </View>
                    <Text style={[styles.compareMeta, { color: theme.textSecondary }]}>
                      {serverState.detail.payee_label ||
                        serverState.detail.note ||
                        "Ohne Empfänger/Notiz"}
                    </Text>
                  </View>
                ) : null}

                {isVersionedEntry(entry) ? (
                  <View
                    style={[
                      styles.compareBox,
                      { borderColor: theme.border, backgroundColor: theme.surfaceRaised },
                    ]}
                  >
                    <Text style={[styles.compareLabel, { color: theme.textSecondary }]}>
                      Meine Änderung (auf Basis von Version {entry.expectedVersion})
                    </Text>
                    {entry.kind === "splits" ? (
                      <Money
                        minorUnits={entry.payload.amount_minor}
                        currency={server?.currency ?? "EUR"}
                        tone="plain"
                      />
                    ) : (
                      <Text style={[styles.compareValue, { color: theme.textPrimary }]}>
                        {entry.kind === "category"
                          ? categoryPathById(categoryGroups ?? [], entry.payload.category_id)
                          : (entry.payload.booked_date ?? "Datum unverändert")}
                      </Text>
                    )}
                    <Text style={[styles.compareMeta, { color: theme.textSecondary }]}>
                      {entry.kind === "splits"
                        ? `${entry.payload.splits.length} Split(s)`
                        : entry.kind === "category"
                          ? `Bisher: ${categoryPathById(categoryGroups ?? [], server?.category_id ?? null)}`
                          : (entry.payload.payee_label ?? "Empfänger unverändert")}
                    </Text>
                  </View>
                ) : null}

                <View style={styles.actionRow}>
                  <PrimaryButton
                    label={retryLabel(conflict)}
                    onPress={() => handleKeepLocal(entry)}
                  />
                  <PrimaryButton
                    label="Verwerfen"
                    variant="danger"
                    onPress={() => handleDiscard(entry)}
                  />
                </View>
              </Card>
            );
          }}
        />
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.lg,
    flexGrow: 1,
  },
  explanation: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  compareBox: {
    borderWidth: 1,
    borderRadius: radii.sm,
    padding: space.md,
    gap: space.xs,
  },
  compareLabel: {
    fontSize: typeScale.micro,
    fontWeight: "700",
  },
  compareValueRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  compareValue: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  compareMeta: {
    fontSize: typeScale.caption,
  },
  actionRow: {
    gap: space.sm,
  },
});
