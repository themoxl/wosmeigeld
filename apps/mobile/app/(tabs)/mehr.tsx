import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { useAuth } from "../../src/auth/AuthProvider";
import { ScreenContainer, SCREEN_EDGES_WITH_TOP } from "../../src/components/ScreenContainer";
import { SectionHeader } from "../../src/components/SectionHeader";
import { Badge, Card, DataRow, ScreenHeader } from "../../src/components/ui";
import { useReviewInbox } from "../../src/hooks/useReview";
import { useQueueConflicts } from "../../src/hooks/useTransactionQueue";
import { letterSpacingLabel, space, typeScale, useTheme, type AccentTone } from "../../src/theme";
import { MEHR_SECTIONS } from "../../src/utils/mehrMenu";

/**
 * Erklaerzeile und Kachelfarbe je Menueeintrag.
 *
 * Bewusst hier und nicht in src/utils/mehrMenu.ts: die Liste dort ist
 * Navigationskonfiguration (Schluessel, Route, Symbol) und wird ohne Renderer
 * getestet - Beschreibungstext und Farbton sind dagegen reine Darstellung.
 * Fehlt ein Schluessel, bleibt die Zeile einzeilig und die Kachel nimmt die
 * aus dem Namen abgeleitete Farbe; ein neuer Menueeintrag kann also nichts
 * kaputt machen.
 */
const ROW_DETAILS: Record<string, { description: string; tone: AccentTone }> = {
  buchungen: { description: "Alle Umsätze, Suche und Export", tone: "info" },
  vorschau: { description: "Wann das Geld knapp wird", tone: "cyan" },
  konten: { description: "Girokonto, Tagesgeld, Depot", tone: "positive" },
  versicherungen: { description: "Policen, Beiträge und Fristen", tone: "purple" },
  kategorien: { description: "Zuordnungen und Regeln pflegen", tone: "highlight" },
  import: { description: "Kontoauszüge und Belege einlesen", tone: "cyan" },
  review: { description: "Unklare Buchungen bestätigen", tone: "warning" },
  sparziele: { description: "Rücklagen und Wünsche", tone: "positive" },
  vorhaben: { description: "Größere Projekte durchrechnen", tone: "highlight" },
  vermoegen: { description: "Nettovermögen und Schulden", tone: "purple" },
  essensplan: { description: "Wochenplan für Mahlzeiten", tone: "highlight" },
  einkaufsliste: { description: "Was noch eingekauft werden muss", tone: "cyan" },
  einstellungen: { description: "Erscheinungsbild, Benachrichtigungen", tone: "info" },
  status: { description: "Dienste und Verbindungen prüfen", tone: "neutral" },
};

/**
 * Mehr-Tab: je Gruppe eine Karte mit Symbolkachel, Label und Erklaerzeile
 * (Showcase-Abschnitt "Mehr & Funktionen").
 *
 * ScrollView statt SectionList: gut ein Dutzend statische Eintraege brauchen
 * keine Virtualisierung, und die Karte muss die Zeilen einer Sektion
 * umschliessen - mit SectionList ginge das nur ueber einen leeren data-Trick.
 * Die Eintraege selbst liegen in src/utils/mehrMenu.ts.
 */
export default function MehrScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { signOut, userEmail } = useAuth();

  // Badge auf "Buchungen pruefen": derselbe Review-Inbox-Datenstand, den der
  // Heute-Screen bereits fuer den Review-Hinweis ermittelt (siehe
  // app/(tabs)/index.tsx). Solange die Daten nicht sicher geladen sind, bleibt
  // das Badge weg statt eine falsche Null zu zeigen.
  const { data: reviewData, isLoading: isReviewLoading, isError: isReviewError } = useReviewInbox();
  const reviewCount = reviewData?.total_count ?? 0;
  const showReviewBadge = !isReviewLoading && !isReviewError && reviewCount > 0;

  // Phase 9 (Offline-Kern): der Hinweis erscheint nur, wenn tatsaechlich ein
  // Sync-Konflikt auf eine Entscheidung wartet - eine dauerhafte Zeile fuer
  // eine seltene Randsituation wuerde das Menue nur verwaessern.
  const conflictCount = useQueueConflicts().length;
  const conflictLabel =
    conflictCount === 1 ? "1 Sync-Konflikt lösen" : `${conflictCount} Sync-Konflikte lösen`;

  return (
    <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
      <ScrollView contentContainerStyle={styles.content}>
        <ScreenHeader title="Mehr & Funktionen" />

        {conflictCount > 0 ? (
          <Card
            tone="warning"
            density="compact"
            style={styles.banner}
            onPress={() => router.push("/mehr/sync-konflikte")}
            accessibilityLabel={conflictLabel}
          >
            <View style={styles.bannerRow}>
              <Ionicons
                name="alert-circle-outline"
                size={22}
                color={theme.warning}
                accessibilityElementsHidden
                importantForAccessibility="no"
              />
              <Text style={[styles.bannerLabel, { color: theme.warning }]}>{conflictLabel}</Text>
              <Ionicons
                name="chevron-forward"
                size={18}
                color={theme.warning}
                accessibilityElementsHidden
                importantForAccessibility="no"
              />
            </View>
          </Card>
        ) : null}

        {MEHR_SECTIONS.map((section) => (
          <View key={section.key}>
            <SectionHeader title={section.label} size="sm" topSpacing="lg" />
            {/* padding={0} plus eigener Seitenabstand: die Zeilen bringen ihre
                Hoehe selbst mit und sollen buendig aneinander stossen, damit
                die Trennlinien durchlaufen statt in Luecken zu haengen. */}
            <Card padding={0} style={styles.group}>
              {section.data.map((item, index) => {
                const details = ROW_DETAILS[item.key];
                const withBadge = item.key === "review" && showReviewBadge;
                return (
                  <DataRow
                    key={item.key}
                    title={item.label}
                    subtitle={details?.description}
                    avatarIcon={item.icon}
                    avatarTone={details?.tone}
                    showChevron
                    divider={index < section.data.length - 1}
                    // Jede Zeile dieses Menues fuehrt auf einen anderen
                    // Bildschirm - eine Vorlesehilfe soll den Ortswechsel
                    // ankuendigen statt eine Aktion an Ort und Stelle. Damit
                    // bleibt "Abmelden" weiter unten die einzige echte
                    // Schaltflaeche der Seite.
                    accessibilityRole="link"
                    onPress={() => router.push(item.href)}
                    // Farbe und Position sind nie alleiniger Traeger: die
                    // Vorlesehilfe nennt die Bedeutung der Zahl im Klartext.
                    accessibilityLabel={
                      withBadge ? `${item.label}, ${reviewCount} offen` : item.label
                    }
                    trailing={
                      withBadge ? (
                        <Badge label={`${reviewCount} offen`} tone="warning" size="small" />
                      ) : undefined
                    }
                  />
                );
              })}
            </Card>
          </View>
        ))}

        {userEmail ? (
          <View style={styles.account}>
            <Text style={[styles.signedInLabel, { color: theme.textMuted }]}>Angemeldet als</Text>
            <Text selectable style={[styles.signedInEmail, { color: theme.textSecondary }]}>
              {userEmail}
            </Text>
          </View>
        ) : null}

        {/* Eigene Karte statt letzter Listenzeile: eine destruktive Aktion
            steht allein, damit sie nicht als naechstes Navigationsziel
            mitgelesen wird. Fehlendes Chevron und Zentrierung sind zwei
            nicht-farbliche Signale "Aktion, kein Ziel". */}
        <Card
          density="compact"
          style={styles.logoutCard}
          onPress={() => void signOut()}
          accessibilityLabel="Abmelden"
        >
          <Text style={[styles.logoutLabel, { color: theme.warning }]}>Abmelden</Text>
        </Card>
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: {
    paddingBottom: space.xxxl + space.xl,
  },
  banner: {
    marginHorizontal: space.lg,
    marginTop: space.sm,
  },
  bannerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
    minHeight: 32,
  },
  bannerLabel: {
    flex: 1,
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  group: {
    marginHorizontal: space.lg,
    paddingHorizontal: space.lg,
    gap: 0,
  },
  account: {
    paddingHorizontal: space.lg,
    paddingTop: space.xxl,
    gap: space.hair,
  },
  signedInLabel: {
    fontSize: typeScale.micro,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: letterSpacingLabel,
  },
  signedInEmail: {
    fontSize: typeScale.body,
  },
  logoutCard: {
    marginHorizontal: space.lg,
    marginTop: space.lg,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 52,
  },
  logoutLabel: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
});
