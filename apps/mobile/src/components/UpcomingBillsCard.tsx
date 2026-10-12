import { useRouter } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { ForecastFlow } from "../api/types";
import { useForecastUpcoming } from "../hooks/useForecast";
import { space, typeScale, useTheme } from "../theme";
import { formatShortDate, parseIsoDate } from "../utils/dates";
import { InlineQueryState } from "./InlineQueryState";
import { Card, CardLabel, DataRow, Money } from "./ui";

const LINK_HIT_SLOP = { top: 12, bottom: 12, left: 12, right: 12 };

/** Fenster der Karte in Tagen - deckungsgleich mit der Ueberschrift. */
const UPCOMING_WINDOW_DAYS = 14;

/** Hoechstens vier Zeilen: darunter ist es eine Uebersicht, darueber eine Liste. */
const MAX_ROWS = 4;

// Woher eine geplante Zahlung stammt. Der Showcase schreibt hier
// "Dauerauftrag"/"Lastschrift" - das weiss die Vorschau nicht, sie kennt nur
// die Quelle der Bewegung. Lieber die belegbare Herkunft als eine erfundene
// Zahlungsart.
const SOURCE_LABELS: Record<ForecastFlow["source_kind"], string> = {
  contract: "Vertrag",
  transaction: "Geplante Buchung",
  simulation: "Simulation",
};

/**
 * Die naechsten Faelligkeiten mit dem Saldo DANACH (Showcase, Abschnitt 1).
 *
 * Der Saldo je Zeile ist der eigentliche Zweck der Karte: eine Liste
 * anstehender Abbuchungen beantwortet nicht, ob sie gedeckt sind - erst
 * "Danach: 2.435 €" tut das.
 */
export function UpcomingBillsCard() {
  const theme = useTheme();
  const router = useRouter();
  const {
    data: upcomingGroups,
    isLoading,
    isError,
    error,
    refetch,
  } = useForecastUpcoming(UPCOMING_WINDOW_DAYS);

  const nextBills = (upcomingGroups ?? []).slice(0, MAX_ROWS);
  const today = new Date();

  return (
    <Card style={styles.card}>
      <View style={styles.headerRow}>
        <CardLabel>{`NÄCHSTE FÄLLIGKEITEN (${UPCOMING_WINDOW_DAYS} TAGE)`}</CardLabel>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Vorschau öffnen"
          hitSlop={LINK_HIT_SLOP}
          onPress={() => router.push("/mehr/vorschau")}
          style={({ pressed }) => [styles.link, { opacity: pressed ? 0.6 : 1 }]}
        >
          <Text style={[styles.linkLabel, { color: theme.positive }]}>Vorschau ›</Text>
        </Pressable>
      </View>

      {isLoading ? (
        <InlineQueryState
          status="loading"
          skeletonRows={3}
          skeletonRowHeight={24}
          testID="heute-upcoming-loading"
        />
      ) : null}

      {isError ? (
        <InlineQueryState
          status="error"
          title="Fälligkeiten konnten nicht geladen werden"
          message={error instanceof Error ? error.message : undefined}
          onRetry={() => void refetch()}
          testID="heute-upcoming-error"
        />
      ) : null}

      {!isLoading && !isError && nextBills.length === 0 ? (
        <InlineQueryState
          status="empty"
          title={`Keine Fälligkeiten in den nächsten ${UPCOMING_WINDOW_DAYS} Tagen`}
          testID="heute-upcoming-empty"
        />
      ) : null}

      {!isLoading && !isError && nextBills.length > 0 ? (
        <View style={styles.list}>
          {nextBills.map((group, index) => {
            const leadFlow = group.flows[0];
            const title = leadFlow?.label || "Zahlung";
            const furtherFlows = Math.max(0, group.flows.length - 1);
            const subtitle = [
              relativeDayLabel(group.on, today),
              leadFlow ? SOURCE_LABELS[leadFlow.source_kind] : null,
              furtherFlows > 0 ? `+${furtherFlows} weitere` : null,
            ]
              .filter(Boolean)
              .join(" · ");

            return (
              <DataRow
                key={`${group.on}-${title}`}
                title={title}
                subtitle={subtitle}
                avatarLabel={title}
                onPress={() => router.push("/mehr/vorschau")}
                accessibilityLabel={`${title}, ${subtitle}`}
                divider={index < nextBills.length - 1}
                trailing={
                  <>
                    <Money minorUnits={group.total_minor} showPlusSign />
                    <View style={styles.afterRow}>
                      <Text style={[styles.afterLabel, { color: theme.textMuted }]}>Danach:</Text>
                      <Money
                        minorUnits={group.balance_after_minor}
                        tone={group.balance_after_minor < 0 ? "warning" : "muted"}
                        style={styles.afterAmount}
                      />
                    </View>
                  </>
                }
              />
            );
          })}
        </View>
      ) : null}
    </Card>
  );
}

/**
 * "Heute" / "Morgen" / "In 3 Tagen" / "In 12 Tagen (28.08.)" - ab einer Woche
 * kommt das Datum dazu, weil "in 12 Tagen" allein niemand in einen Kalendertag
 * uebersetzt. src/utils/dates.ts kennt nur absolute Formate.
 */
function relativeDayLabel(isoDate: string, today: Date): string {
  const target = parseIsoDate(isoDate);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const days = Math.round((target.getTime() - startOfToday.getTime()) / 86_400_000);

  if (days <= 0) return "Heute";
  if (days === 1) return "Morgen";
  if (days < 7) return `In ${days} Tagen`;
  return `In ${days} Tagen (${formatShortDate(isoDate)})`;
}

const styles = StyleSheet.create({
  card: {
    marginHorizontal: space.lg,
  },
  headerRow: {
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
  list: {
    marginTop: -space.sm,
  },
  afterRow: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: space.xs,
  },
  afterLabel: {
    fontSize: typeScale.micro,
  },
  afterAmount: {
    fontSize: typeScale.micro,
    fontWeight: "400",
  },
});
