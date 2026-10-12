import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, Text, View } from "react-native";
import { resolveBookedDuplicate, scanBookedDuplicates } from "../api/client";
import type { ResolveBookedDuplicateInput, ReviewItemBookedDuplicate } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useHousehold } from "../household/HouseholdProvider";
import { space, useTheme } from "../theme";
import { PrimaryButton } from "./PrimaryButton";
import { Card } from "./ui";

export function BookedDuplicatesPanel({
  items,
  total,
}: {
  items: ReviewItemBookedDuplicate[];
  total: number;
}) {
  const theme = useTheme();
  const { accessToken } = useAuth();
  const { selectedHouseholdId, selectedHousehold } = useHousehold();
  const queryClient = useQueryClient();
  const scan = useMutation({
    mutationFn: () => scanBookedDuplicates(accessToken!, selectedHouseholdId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["review", selectedHouseholdId] }),
  });
  const resolve = useMutation({
    mutationFn: (input: ResolveBookedDuplicateInput) =>
      resolveBookedDuplicate(accessToken!, selectedHouseholdId!, input),
    onSuccess: async () => {
      scan.reset();
      await queryClient.invalidateQueries();
    },
  });
  function decide(item: ReviewItemBookedDuplicate, action: ResolveBookedDuplicateInput["action"]) {
    resolve.reset();
    const input = {
      first_transaction_id: item.first.transaction_id,
      first_version: item.first.version,
      second_transaction_id: item.second.transaction_id,
      second_version: item.second.version,
      action,
    };
    if (action === "keep_both") resolve.mutate(input);
    else {
      const booking = action === "archive_first" ? item.first : item.second;
      Alert.alert(
        "Doppelte Buchung entfernen",
        `${booking.payee_label || "Ohne Empfänger"}, ${booking.booked_date}, ${new Intl.NumberFormat("de-DE", { style: "currency", currency: booking.currency }).format((booking.amount_minor ?? 0) / 100)} auf ${booking.account_name}\n\nDie andere Buchung bleibt erhalten. Verknüpfungen der entfernten Buchung werden gelöst.`,
        [
          { text: "Abbrechen", style: "cancel" },
          { text: "Buchung entfernen", style: "destructive", onPress: () => resolve.mutate(input) },
        ],
      );
    }
  }
  const busy = scan.isPending || resolve.isPending;
  return (
    <View style={{ gap: space.md }}>
      <Card>
        <Text style={{ color: theme.textPrimary, fontWeight: "700" }}>
          Duplikate im Buchungsbestand{total > 0 ? ` (${total})` : ""}
        </Text>
        <Text style={{ color: theme.textSecondary }}>
          Prüft alle bereits übernommenen und manuell erfassten Buchungen im Haushalt.
        </Text>
        <PrimaryButton
          label={scan.isPending ? "Prüfe Buchungen…" : "Duplikate erneut prüfen"}
          variant="secondary"
          disabled={busy || !accessToken || !selectedHouseholdId}
          onPress={() => scan.mutate()}
        />
        {scan.data && (
          <Text accessibilityRole="text" style={{ color: theme.textSecondary }}>
            {scan.data.scanned_count} Buchungen geprüft ·{" "}
            {scan.data.total_count === 0
              ? "Keine möglichen Duplikate gefunden."
              : `${scan.data.total_count} mögliche Duplikatpaare gefunden.`}
          </Text>
        )}
        {(scan.error || resolve.error) && (
          <Text accessibilityRole="alert" style={{ color: theme.warning }}>
            {(scan.error || resolve.error)?.message}
          </Text>
        )}
        {total > items.length && (
          <Text style={{ color: theme.textSecondary }}>
            Die ersten {items.length} von {total} Paaren. Nach der Bearbeitung rücken weitere nach.
          </Text>
        )}
      </Card>
      {items.map((item) => (
        <Card key={`${item.first.transaction_id}:${item.second.transaction_id}`}>
          <Text style={{ color: theme.textPrimary, fontWeight: "700" }}>
            Mögliche Doppelbuchung
          </Text>
          {[item.first, item.second].map((booking, index) => (
            <View key={booking.transaction_id} style={{ gap: space.sm, marginVertical: space.sm }}>
              <Text style={{ color: theme.textSecondary }}>
                {index === 0 ? "Früher erfasst" : "Später erfasst"}
              </Text>
              <Text style={{ color: theme.textPrimary, fontWeight: "700" }}>
                {booking.payee_label || "Ohne Empfänger"}
              </Text>
              <Text style={{ color: theme.textPrimary }}>
                {booking.booked_date} ·{" "}
                {new Intl.NumberFormat("de-DE", {
                  style: "currency",
                  currency: booking.currency,
                }).format((booking.amount_minor ?? 0) / 100)}{" "}
                · {booking.account_name}
              </Text>
              <Text style={{ color: theme.textSecondary }}>
                {booking.purpose || "Kein Verwendungszweck"}
              </Text>
              <Text style={{ color: theme.textSecondary }}>
                {booking.import_filename ||
                  (booking.source === "manual" ? "Manuell erfasst" : booking.source)}
              </Text>
              {selectedHousehold?.role !== "viewer" && (
                <PrimaryButton
                  label="Diese Buchung entfernen"
                  variant="secondary"
                  disabled={busy}
                  onPress={() => decide(item, index === 0 ? "archive_first" : "archive_second")}
                />
              )}
            </View>
          ))}
          {selectedHousehold?.role !== "viewer" && (
            <PrimaryButton
              label="Beide behalten"
              variant="secondary"
              disabled={busy}
              onPress={() => decide(item, "keep_both")}
            />
          )}
        </Card>
      ))}
    </View>
  );
}
