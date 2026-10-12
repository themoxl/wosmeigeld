import { Stack, useRouter } from "expo-router";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { AccountRow } from "../../src/components/AccountRow";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { Badge, Card, CardLabel } from "../../src/components/ui";
import { useAccounts } from "../../src/hooks/useAccounts";
import { space, typeScale, useTheme } from "../../src/theme";

// Konten-Screen (Auftrag Punkt 5): Liste via GET /v1/accounts, Anlegen ueber
// app/mehr/konten-neu.tsx.
export default function KontenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { data: accounts, isLoading, isError, error, refetch, isFetching } = useAccounts();
  const list = accounts ?? [];

  return (
    <>
      <Stack.Screen
        options={{
          title: "Konten",
          headerRight: () => (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Neues Konto anlegen"
              hitSlop={8}
              onPress={() => router.push("/mehr/konten-neu")}
            >
              <Text style={[styles.headerAction, { color: theme.positive }]}>+ Neu</Text>
            </Pressable>
          ),
        }}
      />
      <ScreenContainer>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={isFetching && !isLoading}
              onRefresh={() => void refetch()}
            />
          }
        >
          <Card padding={space.md} style={styles.listCard}>
            <View style={styles.listHead}>
              <CardLabel>KONTEN & SALDEN</CardLabel>
              {list.length > 0 ? <Badge label={`${list.length} Konten`} /> : null}
            </View>

            {isLoading ? (
              <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={40} />
            ) : isError ? (
              <InlineQueryState
                status="error"
                title="Konten konnten nicht geladen werden"
                message={
                  error instanceof Error
                    ? error.message
                    : "Bitte prüfe deine Verbindung und versuche es erneut."
                }
                onRetry={() => void refetch()}
              />
            ) : list.length === 0 ? (
              <InlineQueryState
                status="empty"
                title="Noch keine Konten"
                message="Lege dein erstes Konto an, z. B. Girokonto oder Bargeld."
              />
            ) : (
              list.map((account, index) => (
                <AccountRow
                  key={account.id}
                  account={account}
                  divider={index < list.length - 1}
                  onPress={() => router.push(`/mehr/konto/${account.id}`)}
                />
              ))
            )}
          </Card>
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  headerAction: { fontSize: typeScale.medium, fontWeight: "600" },
  content: {
    padding: space.lg,
    gap: space.lg,
  },
  // Die Zeilen bringen ihren eigenen senkrechten Abstand und ihre Trennlinie
  // mit; der Kartenabstand wuerde sie zusaetzlich auseinanderziehen.
  listCard: { gap: 0 },
  listHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
    paddingBottom: space.sm,
  },
});
