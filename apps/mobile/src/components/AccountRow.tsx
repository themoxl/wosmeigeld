import type { Ionicons } from "@expo/vector-icons";
import type { ComponentProps } from "react";
import { StyleSheet, View } from "react-native";
import type { Account } from "../api/types";
import { space, type AccentTone } from "../theme";
import { getAccountTypeLabel } from "../utils/accountType";
import { Badge, DataRow, Money } from "./ui";

type IconName = ComponentProps<typeof Ionicons>["name"];

type Props = {
  account: Account;
  onPress?: () => void;
  /** Trennlinie unten - in einer Liste bei der letzten Zeile abschalten. */
  divider?: boolean;
};

// Symbol je Kontotyp. Beim Ueberfliegen einer Kontenliste findet man sein
// Girokonto ueber die Kachel schneller als ueber den Typnamen in der zweiten
// Zeile - deshalb traegt die Kachel hier ein Symbol statt der Initialen.
const ACCOUNT_ICONS: Record<string, IconName> = {
  checking: "card-outline",
  credit_card: "card-outline",
  savings: "wallet-outline",
  cash: "cash-outline",
  brokerage: "trending-up-outline",
  loan: "document-text-outline",
  real_estate: "home-outline",
};

const FALLBACK_ICON: IconName = "ellipsis-horizontal-outline";

/** Eine Zeile in der Kontenliste: Kachel, Name, Typ/Institut, Startsaldo. */
export function AccountRow({ account, onPress, divider = true }: Props) {
  const typeLabel = getAccountTypeLabel(account.account_type);
  const subtitle = account.institution ? `${typeLabel} · ${account.institution}` : typeLabel;

  // Off-Budget und geschlossen sind Zustaende, die den Saldo in jeder
  // Auswertung anders behandeln lassen - sie stehen deshalb ausgeschrieben in
  // der Zeile und nicht nur als abweichende Farbe.
  const markers: { label: string; tone: AccentTone }[] = [];
  if (!account.on_budget) markers.push({ label: "Off-Budget", tone: "highlight" });
  if (account.is_closed) markers.push({ label: "Geschlossen", tone: "neutral" });

  return (
    <DataRow
      title={account.name}
      subtitle={subtitle}
      avatarLabel={account.name}
      avatarIcon={ACCOUNT_ICONS[account.account_type] ?? FALLBACK_ICON}
      titleBadge={
        markers.length > 0 ? (
          <View style={styles.markers}>
            {markers.map((marker) => (
              <Badge key={marker.label} label={marker.label} tone={marker.tone} size="small" />
            ))}
          </View>
        ) : null
      }
      trailing={
        <Money
          minorUnits={account.opening_balance_minor}
          currency={account.currency}
          tone="plain"
        />
      }
      trailingCaption="Startsaldo"
      onPress={onPress}
      accessibilityLabel={onPress ? `${account.name} bearbeiten` : undefined}
      showChevron={Boolean(onPress)}
      divider={divider}
    />
  );
}

const styles = StyleSheet.create({
  markers: {
    flexDirection: "row",
    gap: space.xs,
  },
});
