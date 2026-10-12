import { useRouter } from "expo-router";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import type { Account } from "../api/types";
import { useAccounts } from "../hooks/useAccounts";
import { space, typeScale, usePrivacy, useTheme } from "../theme";
import { getAccountTypeLabel } from "../utils/accountType";
import { formatMinorUnits } from "../utils/money";
import { InlineQueryState } from "./InlineQueryState";
import { Card, CardLabel, Money } from "./ui";

// Tippziel eines Textlinks: 20dp Text plus Hitslop ergibt die geforderten 44dp,
// ohne die Kopfzeile auf Knopfhoehe aufzublasen.
const LINK_HIT_SLOP = { top: 12, bottom: 12, left: 12, right: 12 };

/**
 * Waagerecht scrollender Konten-Strip (Showcase, Abschnitt 1). Er ersetzt die
 * frueher senkrechte Kontenliste: auf "Heute" zaehlt der Ueberblick, nicht die
 * Vollstaendigkeit - die liegt einen Tipp entfernt unter "Alle Salden".
 */
export function AccountBalancesStrip() {
  const theme = useTheme();
  const router = useRouter();
  const privacy = usePrivacy();
  const { data: accounts, isLoading, isError, error, refetch } = useAccounts();

  const header = (
    <View style={styles.headerRow}>
      <CardLabel>KONTEN</CardLabel>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Alle Salden"
        hitSlop={LINK_HIT_SLOP}
        onPress={() => router.push("/mehr/vermoegen")}
        style={({ pressed }) => [styles.link, { opacity: pressed ? 0.6 : 1 }]}
      >
        <Text style={[styles.linkLabel, { color: theme.positive }]}>Alle Salden ›</Text>
      </Pressable>
    </View>
  );

  if (isLoading) {
    return (
      <View style={styles.section}>
        {header}
        <InlineQueryState
          status="loading"
          skeletonRows={2}
          skeletonRowHeight={28}
          testID="heute-accounts-loading"
        />
      </View>
    );
  }

  if (isError) {
    return (
      <View style={styles.section}>
        {header}
        <InlineQueryState
          status="error"
          title="Konten konnten nicht geladen werden"
          message={error instanceof Error ? error.message : undefined}
          onRetry={() => void refetch()}
          testID="heute-accounts-error"
        />
      </View>
    );
  }

  const list = accounts ?? [];
  if (list.length === 0) {
    return (
      <View style={styles.section}>
        {header}
        <InlineQueryState
          status="empty"
          title="Noch keine Konten"
          message="Lege ein Konto an, damit hier Salden erscheinen."
          testID="heute-accounts-empty"
        />
      </View>
    );
  }

  return (
    <View style={styles.section}>
      {header}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.strip}
      >
        {list.map((account) => (
          <AccountMiniCard
            key={account.id}
            account={account}
            isPrivacyMode={privacy.isPrivacyMode}
            onPress={() => router.push(`/buchungen?accountId=${account.id}`)}
          />
        ))}
      </ScrollView>
    </View>
  );
}

function AccountMiniCard({
  account,
  isPrivacyMode,
  onPress,
}: {
  account: Account;
  isPrivacyMode: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();

  const typeLabel = getAccountTypeLabel(account.account_type);
  // Der Punkt allein darf nichts bedeuten - deshalb steht derselbe Zustand
  // ausgeschrieben in der Zusatzzeile.
  const caption = account.is_closed
    ? `Geschlossen · ${typeLabel}`
    : [account.institution, typeLabel].filter(Boolean).join(" · ");
  const dotColor = account.is_closed ? theme.textMuted : theme.positiveStrong;

  // `Account` kennt im erzeugten Schema nur `opening_balance_minor` - einen
  // gerechneten aktuellen Kontostand liefert der Endpunkt nicht. Die Zahl wird
  // deshalb genannt, was sie ist ("Startsaldo", genau wie in AccountRow.tsx);
  // die Ueberschrift des Streifens heisst folgerichtig nur noch "KONTEN".
  const balanceForLabel = isPrivacyMode
    ? "verborgen"
    : formatMinorUnits(account.opening_balance_minor, account.currency);

  return (
    <Card
      density="compact"
      style={styles.miniCard}
      onPress={onPress}
      accessibilityLabel={`${account.name}, ${caption}, Startsaldo: ${balanceForLabel}`}
    >
      <View style={styles.miniHeader}>
        <Text style={[styles.miniName, { color: theme.textSecondary }]} numberOfLines={1}>
          {account.name}
        </Text>
        <View style={[styles.dot, { backgroundColor: dotColor }]} />
      </View>
      <Money
        minorUnits={account.opening_balance_minor}
        currency={account.currency}
        tone="plain"
        numberOfLines={1}
      />
      <Text style={[styles.miniCaption, { color: theme.textMuted }]} numberOfLines={1}>
        Startsaldo
      </Text>
      <Text style={[styles.miniCaption, { color: theme.textMuted }]} numberOfLines={1}>
        {caption}
      </Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  section: {
    gap: space.sm,
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
    paddingHorizontal: space.lg,
  },
  link: {
    minHeight: 20,
    justifyContent: "center",
  },
  linkLabel: {
    fontSize: typeScale.caption,
    fontWeight: "700",
  },
  strip: {
    paddingHorizontal: space.lg,
    gap: space.md,
    // Der Kartenschatten wird sonst vom ScrollView-Rand abgeschnitten.
    paddingVertical: space.xs,
  },
  miniCard: {
    width: 168,
  },
  miniHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  miniName: {
    fontSize: typeScale.caption,
    fontWeight: "700",
    flexShrink: 1,
  },
  dot: {
    width: 7,
    height: 7,
    borderRadius: 3.5,
  },
  miniCaption: {
    fontSize: typeScale.micro,
  },
});
