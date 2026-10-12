import { Stack, useRouter } from "expo-router";
import { Pressable, RefreshControl, SectionList, StyleSheet, Text, View } from "react-native";
import type { InsurancePolicy } from "../../src/api/types";
import { EmptyState } from "../../src/components/EmptyState";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { PolicyRow } from "../../src/components/PolicyRow";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { SectionHeader } from "../../src/components/SectionHeader";
import { Card, CardLabel, Money } from "../../src/components/ui";
import { useConnectivity } from "../../src/hooks/useConnectivity";
import {
  groupPoliciesByRiskField,
  useInsuranceContext,
  useInsurancePolicies,
  useInsuranceSummary,
} from "../../src/hooks/useInsurance";
import { leading, MIN_TOUCH, space, typeScale, useTheme } from "../../src/theme";

/**
 * Versicherungsbestand, gegliedert nach Risikobereich
 * (docs/VERSICHERUNGEN-PLAN.md, Abschnitt 7, P2b).
 *
 * Die Gruppierung ersetzt hier die Filterleiste des Web. Das ist kein Ersatz
 * aus Platzmangel, sondern die passendere Form: mobil sucht man nicht "alle
 * Policen mit Status aktiv", sondern "was habe ich fuer mein Auto". Ein
 * Suchfeld und sechs Statusfilter waeren fuer einen Bestand von einem Dutzend
 * Zeilen Bedienaufwand ohne Ertrag.
 *
 * Bewusst KEINE Sortierung im Client: die Reihenfolge innerhalb einer Gruppe
 * kommt aus der Serverantwort (`sort=line`). Eine zweite Sortierregel hier
 * waere die zweite Stelle, an der sich dieselbe Reihenfolge entscheidet.
 */
export default function VersicherungenScreen() {
  const theme = useTheme();
  const router = useRouter();
  const isConnected = useConnectivity();
  const { canEdit } = useInsuranceContext();

  const policiesQuery = useInsurancePolicies();
  const summaryQuery = useInsuranceSummary();

  const policies = policiesQuery.data ?? [];
  const sections = groupPoliciesByRiskField(policies);
  const summary = summaryQuery.data ?? null;

  /**
   * Die offenen Angaben als ein Satz, nicht als drei Zahlen.
   *
   * Der Plan verlangt, dass die Kennzahlen ihre Unschaerfe mittragen: eine
   * Jahressumme aus einem Bestand mit drei nicht zugeordneten Policen ist
   * keine Auskunft, sondern eine Behauptung. Erledigt wird das Nachtragen im
   * Web - deshalb steht hier eine Feststellung und kein Knopf.
   */
  const openHints: string[] = [];
  if (summary) {
    if (summary.unknown_line_count > 0) {
      openHints.push(
        summary.unknown_line_count === 1
          ? "1 Police ohne Sparte"
          : `${summary.unknown_line_count} Policen ohne Sparte`,
      );
    }
    const openMainDue = summary.missing_main_due_count + summary.unconfirmed_main_due_count;
    if (openMainDue > 0) {
      openHints.push(
        openMainDue === 1
          ? "1 Termin ohne bestätigte Hauptfälligkeit"
          : `${openMainDue} Termine ohne bestätigte Hauptfälligkeit`,
      );
    }
  }

  return (
    <>
      <Stack.Screen
        options={{
          title: "Versicherungen",
          headerRight: canEdit
            ? () => (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Neue Versicherung anlegen"
                  hitSlop={8}
                  onPress={() => router.push("/versicherung/neu")}
                  style={styles.headerAction}
                >
                  <Text style={[styles.headerActionLabel, { color: theme.positive }]}>+ Neu</Text>
                </Pressable>
              )
            : undefined,
        }}
      />
      <ScreenContainer>
        {/* Der Hinweis nennt die Folge, nicht den Zustand: Versicherungsdaten
            werden bewusst nicht auf dem Geraet zwischengespeichert (Plan 7.1,
            Begruendung in src/hooks/useInsurance.ts), ohne Netz ist die Liste
            deshalb wirklich leer und nicht nur veraltet. */}
        {!isConnected ? (
          <View style={styles.offlineBanner}>
            <Card tone="info" density="compact">
              <Text style={[styles.bannerText, { color: theme.info }]}>
                Offline: Versicherungsdaten liegen aus Datenschutzgründen nicht auf dem Gerät. Die
                Liste bleibt leer, bis wieder eine Verbindung besteht.
              </Text>
            </Card>
          </View>
        ) : null}

        <SectionList
          sections={sections}
          keyExtractor={(item: InsurancePolicy) => item.id}
          // Die Ueberschriften sind transparent und wuerden beim Kleben ueber
          // den Zeilen darunter stehen.
          stickySectionHeadersEnabled={false}
          refreshControl={
            <RefreshControl
              refreshing={policiesQuery.isFetching}
              onRefresh={() => {
                void policiesQuery.refetch();
                void summaryQuery.refetch();
              }}
            />
          }
          contentContainerStyle={policies.length === 0 ? styles.emptyContent : styles.content}
          renderSectionHeader={({ section }) => (
            <SectionHeader title={section.title} size="sm" topSpacing="lg" />
          )}
          renderItem={({ item }: { item: InsurancePolicy }) => <PolicyRow policy={item} />}
          ItemSeparatorComponent={() => (
            <View style={[styles.separator, { backgroundColor: theme.border }]} />
          )}
          ListEmptyComponent={
            policiesQuery.isLoading ? (
              <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={44} />
            ) : policiesQuery.isError ? (
              <InlineQueryState
                status="error"
                title="Versicherungen nicht geladen"
                message={policiesQuery.error.message}
                onRetry={() => void policiesQuery.refetch()}
              />
            ) : !isConnected ? (
              /* Ohne Netz weiss die App NICHT, dass der Bestand leer ist - sie
                 weiss nur, dass sie ihn nicht kennt: die Abfrage pausiert
                 (networkMode "online"), und zwischengespeichert wird hier
                 bewusst nichts (Plan 7.1). "Noch keine Versicherung erfasst"
                 waere an dieser Stelle eine Behauptung ueber Daten, die gerade
                 niemand gelesen hat - und fuer einen Haushalt mit zwoelf
                 Policen schlicht falsch. */
              <EmptyState
                title="Bestand offline nicht abrufbar"
                description="Sobald wieder eine Verbindung besteht, lädt die Liste von selbst."
              />
            ) : (
              <EmptyState
                title="Noch keine Versicherung erfasst"
                description="Mit „+ Neu“ oben rechts genügen Sparte, Versicherer, Policennummer und der Geldfall. Deckungen, Fristen und Beteiligte werden in der Webansicht nachgetragen."
              />
            )
          }
          ListFooterComponent={
            summary && policies.length > 0 ? (
              <View style={styles.footer}>
                <Card>
                  <CardLabel>Bestand im Überblick</CardLabel>
                  <Text style={[styles.footerLine, { color: theme.textSecondary }]}>
                    {summary.policy_count === 1 ? "1 Police" : `${summary.policy_count} Policen`} ·{" "}
                    {summary.active_count} aktiv
                  </Text>

                  <View style={styles.footerRow}>
                    <Text style={[styles.footerLine, { color: theme.textSecondary }]}>
                      Jahresbeitrag in den Fixkosten:
                    </Text>
                    <Money minorUnits={summary.yearly_in_fixed_costs_minor} tone="plain" />
                  </View>

                  {/* Fremdbeitraege stehen getrennt und nie addiert - sie sind
                      kein Teil der Fixkosten, und die Summe aus beiden Toepfen
                      waere die erste Zahl, die jemand dafuer haelt. */}
                  {summary.yearly_paid_by_others_minor > 0 ? (
                    <>
                      <View style={styles.footerRow}>
                        <Text style={[styles.footerLine, { color: theme.textSecondary }]}>
                          Von Dritten gezahlt:
                        </Text>
                        <Money minorUnits={summary.yearly_paid_by_others_minor} tone="muted" />
                      </View>
                      <Text style={[styles.footerNote, { color: theme.textMuted }]}>
                        pro Jahr, nicht in den Fixkosten
                      </Text>
                    </>
                  ) : null}

                  {openHints.length > 0 ? (
                    <Text style={[styles.footerLine, { color: theme.textSecondary }]}>
                      Offen: {openHints.join(" · ")}
                    </Text>
                  ) : null}
                </Card>
              </View>
            ) : null
          }
        />
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    paddingBottom: space.xxl,
  },
  emptyContent: {
    flexGrow: 1,
  },
  headerAction: {
    minHeight: MIN_TOUCH,
    justifyContent: "center",
    paddingHorizontal: space.xs,
  },
  headerActionLabel: {
    fontSize: typeScale.medium,
    fontWeight: "700",
  },
  offlineBanner: {
    paddingHorizontal: space.lg,
    paddingTop: space.md,
  },
  bannerText: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  separator: {
    height: StyleSheet.hairlineWidth,
    // Bis zur Labelkante eingerueckt: die Linie trennt Zeilen, ohne den Block
    // optisch zu zerschneiden.
    marginLeft: space.lg,
  },
  footer: {
    paddingHorizontal: space.lg,
    paddingTop: space.xxl,
  },
  footerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.md,
  },
  footerLine: {
    flexShrink: 1,
    fontSize: typeScale.body,
  },
  footerNote: {
    fontSize: typeScale.micro,
  },
});
