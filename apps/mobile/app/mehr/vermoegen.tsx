import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { Alert, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { deleteAssetOrDebt } from "../../src/api/client";
import type { AssetDebt } from "../../src/api/types";
import { useAuth } from "../../src/auth/AuthProvider";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import {
  Badge,
  Card,
  CardLabel,
  DataRow,
  Money,
  SplitMeter,
  StatTile,
  type SplitSegment,
} from "../../src/components/ui";
import { useHousehold } from "../../src/household/HouseholdProvider";
import { useAssetsAndDebts, useNetWorthReport } from "../../src/hooks/useGoals";
import { space, typeScale, useTheme, type AccentTone } from "../../src/theme";

// Vermoegen und Schulden laut Architekturplan Abschnitt 11.2 (Phase 7):
// Bewertungen, Nettovermoegensbericht und Schuldentilgungsprognose.

const ASSET_CLASS_LABELS: Record<string, string> = {
  real_estate: "Immobilien",
  vehicle: "Fahrzeuge",
  depot: "Depot",
  cash: "Konten & Bargeld",
  loan: "Darlehen",
  credit: "Kredite",
  other: "Sonstiges",
};

// Feste Farbe je Anlageklasse statt einer aus dem Namen abgeleiteten: der
// Aufteilungsbalken und seine Legende muessen ueber Sitzungen hinweg gleich
// bleiben, sonst wechselt "Depot" bei jedem Datenstand die Farbe.
const ASSET_CLASS_TONES: Record<string, AccentTone> = {
  real_estate: "purple",
  vehicle: "cyan",
  depot: "info",
  cash: "positive",
  loan: "warning",
  credit: "warning",
  other: "highlight",
};

function assetClassLabel(assetClass: string): string {
  return ASSET_CLASS_LABELS[assetClass] ?? assetClass;
}

export default function VermoegenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { accessToken } = useAuth();
  const { selectedHouseholdId } = useHousehold();
  const queryClient = useQueryClient();

  const {
    data: items,
    isLoading: isItemsLoading,
    isError: isItemsError,
    isFetching: isItemsFetching,
    refetch: refetchItems,
  } = useAssetsAndDebts();

  const {
    data: netWorth,
    isLoading: isNetWorthLoading,
    isFetching: isNetWorthFetching,
    refetch: refetchNetWorth,
  } = useNetWorthReport();

  const deleteMutation = useMutation({
    mutationFn: (itemId: string) =>
      deleteAssetOrDebt(accessToken as string, selectedHouseholdId as string, itemId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assets", selectedHouseholdId] });
      void queryClient.invalidateQueries({ queryKey: ["net-worth", selectedHouseholdId] });
    },
  });

  const isRefreshing = isItemsFetching || isNetWorthFetching;
  const onRefresh = () => {
    void refetchItems();
    void refetchNetWorth();
  };

  const assets = (items ?? []).filter((item) => item.type === "asset");
  const debts = (items ?? []).filter((item) => item.type === "debt");
  // Der Bericht antwortet auch fuer einen frischen Haushalt - dann aber mit
  // lauter Nullen. "Leer" heisst deshalb: keine Position erfasst UND keine
  // Summe im Bericht, sonst stuende hier eine Leitkarte ueber 0,00 EUR.
  const hasNetWorthData =
    netWorth != null && (netWorth.total_assets_minor !== 0 || netWorth.total_debts_minor !== 0);
  const isEmpty =
    !isItemsLoading &&
    !isNetWorthLoading &&
    !hasNetWorthData &&
    assets.length === 0 &&
    debts.length === 0;

  const assetSegments: SplitSegment[] = Object.entries(netWorth?.assets_by_class ?? {})
    .filter(([, value]) => value > 0)
    .sort((left, right) => right[1] - left[1])
    .map(([assetClass, value]) => ({
      value,
      tone: ASSET_CLASS_TONES[assetClass] ?? "info",
      label: assetClassLabel(assetClass),
    }));

  function confirmDelete(item: AssetDebt) {
    Alert.alert(item.type === "asset" ? "Vermögenswert löschen?" : "Schuld löschen?", item.name, [
      { text: "Abbrechen", style: "cancel" },
      { text: "Löschen", style: "destructive", onPress: () => deleteMutation.mutate(item.id) },
    ]);
  }

  return (
    <>
      <Stack.Screen
        options={{
          title: "Vermögen und Schulden",
          headerRight: () => (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Vermögenswert oder Schuld anlegen"
              hitSlop={8}
              onPress={() => router.push("/vermoegen-neu")}
            >
              <Text style={[styles.headerAction, { color: theme.positive }]}>+ Neu</Text>
            </Pressable>
          ),
        }}
      />
      <ScreenContainer>
        <ScrollView
          contentContainerStyle={isEmpty ? styles.emptyContent : styles.content}
          refreshControl={<RefreshControl refreshing={isRefreshing} onRefresh={onRefresh} />}
        >
          {isEmpty ? (
            <InlineQueryState
              status="empty"
              title="Noch keine Einträge"
              message="Erfasse Vermögenswerte und Schulden, um den Nettovermögensbericht zu sehen."
            />
          ) : (
            <>
              {isNetWorthLoading ? (
                <Card>
                  <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={26} />
                </Card>
              ) : null}

              {netWorth ? (
                <Card accentTop={netWorth.net_worth_minor < 0 ? "warning" : "positive"}>
                  <CardLabel>GESAMT-NETTOVERMÖGEN</CardLabel>
                  <Money
                    minorUnits={netWorth.net_worth_minor}
                    size="hero"
                    tone={netWorth.net_worth_minor < 0 ? "warning" : "positive"}
                    testID="net-worth-total"
                  />
                  <Text style={[styles.heroCaption, { color: theme.textSecondary }]}>
                    Vermögenswerte abzüglich Schulden, Stand heute
                  </Text>

                  <View style={styles.tileRow}>
                    <StatTile
                      label="VERMÖGEN"
                      tone="positive"
                      value={
                        <Money
                          minorUnits={netWorth.total_assets_minor}
                          size="medium"
                          tone="positive"
                        />
                      }
                      caption="Erfasste Werte und Guthaben auf Konten"
                    />
                    <StatTile
                      label="SCHULDEN"
                      tone="warning"
                      value={
                        <Money
                          minorUnits={netWorth.total_debts_minor}
                          size="medium"
                          tone="warning"
                        />
                      }
                      caption="Erfasste Schulden und Sollsalden"
                    />
                  </View>

                  {assetSegments.length > 0 ? (
                    <View style={styles.splitBlock}>
                      <CardLabel>AUFTEILUNG DER VERMÖGENSWERTE</CardLabel>
                      <SplitMeter
                        segments={assetSegments}
                        height={10}
                        accessibilityLabel={`Aufteilung der Vermögenswerte: ${assetSegments
                          .map((segment) => segment.label)
                          .join(", ")}`}
                        testID="net-worth-split"
                      />
                    </View>
                  ) : null}
                </Card>
              ) : null}

              <Card padding={space.md} style={styles.listCard}>
                <View style={styles.listHead}>
                  <CardLabel>VERMÖGENSWERTE</CardLabel>
                  <Badge label={`${assets.length} erfasst`} />
                </View>

                {isItemsLoading ? (
                  <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={40} />
                ) : isItemsError ? (
                  <InlineQueryState
                    status="error"
                    title="Vermögenswerte konnten nicht geladen werden"
                    onRetry={() => void refetchItems()}
                  />
                ) : assets.length === 0 ? (
                  <InlineQueryState status="empty" title="Noch keine Vermögenswerte." />
                ) : (
                  assets.map((asset, index) => (
                    <Pressable
                      key={asset.id}
                      accessibilityRole="button"
                      accessibilityLabel={asset.name}
                      accessibilityHint="Lange drücken zum Löschen"
                      onLongPress={() => confirmDelete(asset)}
                    >
                      <DataRow
                        title={asset.name}
                        subtitle={assetClassLabel(asset.asset_class)}
                        avatarLabel={assetClassLabel(asset.asset_class)}
                        avatarTone={ASSET_CLASS_TONES[asset.asset_class] ?? "info"}
                        trailing={<Money minorUnits={asset.current_value_minor} tone="plain" />}
                        divider={index < assets.length - 1}
                      />
                    </Pressable>
                  ))
                )}
              </Card>

              <Card padding={space.md} style={styles.listCard}>
                <View style={styles.listHead}>
                  <CardLabel>SCHULDEN</CardLabel>
                  <Badge label={`${debts.length} erfasst`} />
                </View>

                {isItemsLoading ? (
                  <InlineQueryState status="loading" skeletonRows={2} skeletonRowHeight={40} />
                ) : isItemsError ? (
                  <InlineQueryState
                    status="error"
                    title="Schulden konnten nicht geladen werden"
                    onRetry={() => void refetchItems()}
                  />
                ) : debts.length === 0 ? (
                  <InlineQueryState status="empty" title="Noch keine Schulden." />
                ) : (
                  debts.map((debt, index) => (
                    <Pressable
                      key={debt.id}
                      accessibilityRole="button"
                      accessibilityLabel={debt.name}
                      accessibilityHint="Lange drücken zum Löschen"
                      onLongPress={() => confirmDelete(debt)}
                    >
                      <DataRow
                        title={debt.name}
                        subtitle={`${assetClassLabel(debt.asset_class)} · ${debt.interest_rate_percent.toFixed(2)} % Zins`}
                        avatarLabel={assetClassLabel(debt.asset_class)}
                        avatarTone="warning"
                        trailing={
                          <>
                            <Money minorUnits={debt.current_value_minor} tone="warning" />
                            <View style={styles.rateLine}>
                              <Money
                                minorUnits={debt.monthly_payment_minor}
                                tone="muted"
                                style={styles.rateAmount}
                              />
                              <Text style={[styles.rateSuffix, { color: theme.textMuted }]}>
                                /Monat
                              </Text>
                            </View>
                          </>
                        }
                        trailingCaption={
                          debt.payoff_months_projected
                            ? `Tilgung in ${debt.payoff_months_projected} Monaten`
                            : undefined
                        }
                        divider={index < debts.length - 1}
                      />
                    </Pressable>
                  ))
                )}
              </Card>
            </>
          )}
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
  emptyContent: {
    flexGrow: 1,
    justifyContent: "center",
  },
  heroCaption: {
    fontSize: typeScale.caption,
  },
  tileRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  splitBlock: {
    gap: space.sm,
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
  rateLine: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.hair,
  },
  rateAmount: {
    fontSize: typeScale.micro,
    fontWeight: "600",
  },
  rateSuffix: {
    fontSize: typeScale.micro,
  },
});
