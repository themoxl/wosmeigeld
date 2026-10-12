import { StyleSheet, Text, View } from "react-native";
import type { SplitProvenance } from "../api/types";
import { useTransactionProvenance } from "../hooks/useTransactionProvenance";
import { leading, radii, space, typeScale, useTheme, type AccentTone } from "../theme";
import { provenanceExplanation, SOURCE_LABELS } from "../utils/provenanceText";
import { Badge, Card, CardLabel, Money } from "./ui";
import { InlineQueryState } from "./InlineQueryState";

// "Warum ist diese Buchung hier?" auch unterwegs - die Frage stellt sich am
// Handy genauso wie am Schreibtisch (docs/ANALYSE-EXPLORER-PLAN.md,
// Baustein G).

/**
 * Farbton je Herkunft. Der Ton ist nur eine Lesehilfe - die Herkunft steht in
 * der Pille immer ausgeschrieben daneben.
 */
const SOURCE_TONES: Record<SplitProvenance["category_source"], AccentTone> = {
  unset: "warning",
  unknown: "neutral",
  manual: "info",
  import: "cyan",
  rule: "purple",
  ai: "highlight",
  history: "neutral",
};

function formatDate(value: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleDateString("de-DE");
}

function SplitBlock({ split }: { split: SplitProvenance }) {
  const theme = useTheme();
  const explanation = provenanceExplanation(split);
  const when = formatDate(split.categorized_at);

  return (
    <View style={[styles.split, { backgroundColor: theme.surfaceRaised }]}>
      <View style={styles.splitHead}>
        <Text style={[styles.category, { color: theme.textPrimary }]} numberOfLines={2}>
          {split.category_name ?? "Ohne Kategorie"}
          {split.category_is_archived ? " (archiviert)" : ""}
        </Text>
        <Money minorUnits={split.amount_minor} />
      </View>

      <Badge
        tone={SOURCE_TONES[split.category_source]}
        label={SOURCE_LABELS[split.category_source]}
      />

      {explanation ? (
        <Text style={[styles.explanation, { color: theme.textPrimary }]}>{explanation}</Text>
      ) : null}

      {when ? (
        <Text style={[styles.meta, { color: theme.textMuted }]}>Zugeordnet am {when}</Text>
      ) : null}
    </View>
  );
}

function RawRow({ label, value }: { label: string; value: string | null }) {
  const theme = useTheme();
  if (!value) return null;
  return (
    <View style={styles.rawRow}>
      <Text style={[styles.rawLabel, { color: theme.textSecondary }]}>{label}</Text>
      <Text style={[styles.rawValue, { color: theme.textPrimary }]}>{value}</Text>
    </View>
  );
}

export function ProvenanceCard({ transactionId }: { transactionId: string }) {
  const theme = useTheme();
  const { data, isPending, isError, refetch } = useTransactionProvenance(transactionId);

  if (isPending) {
    return (
      <Card>
        <CardLabel>Warum ist diese Buchung hier?</CardLabel>
        <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={16} />
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card>
        <CardLabel>Warum ist diese Buchung hier?</CardLabel>
        <InlineQueryState
          status="error"
          title="Herkunft konnte nicht geladen werden"
          onRetry={() => void refetch()}
        />
      </Card>
    );
  }

  const origin = data.import_origin;
  const hasRawData = Boolean(data.payee_raw || data.purpose_raw || data.bank_transaction_code);

  return (
    <Card>
      <CardLabel>Warum ist diese Buchung hier?</CardLabel>

      {data.splits.map((split) => (
        <SplitBlock key={split.split_id} split={split} />
      ))}

      {hasRawData ? (
        <View style={styles.rawBlock}>
          <RawRow label="Empfänger laut Bank" value={data.payee_raw} />
          <RawRow label="Verwendungszweck" value={data.purpose_raw} />
          <RawRow label="Buchungsart" value={data.bank_transaction_code} />
        </View>
      ) : (
        <Text style={[styles.meta, { color: theme.textSecondary }]}>
          Für diese Buchung liegt kein Bankrohtext vor
          {data.source === "manual" ? " – sie wurde von Hand erfasst." : "."}
        </Text>
      )}

      {origin ? (
        <View style={styles.rawBlock}>
          <RawRow label="Importdatei" value={origin.filename} />
          <RawRow label="Format" value={origin.detected_format} />
          <RawRow
            label="Zeile in der Datei"
            value={origin.row_number !== null ? String(origin.row_number) : null}
          />
        </View>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  split: {
    borderRadius: radii.sm,
    padding: space.md,
    gap: space.xs,
  },
  splitHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: space.sm,
  },
  category: {
    fontSize: typeScale.base,
    fontWeight: "700",
    flexShrink: 1,
  },
  explanation: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  meta: {
    fontSize: typeScale.micro,
  },
  rawBlock: {
    gap: space.xs,
  },
  rawRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  rawLabel: {
    fontSize: typeScale.caption,
    flex: 2,
  },
  rawValue: {
    fontSize: typeScale.caption,
    flex: 3,
  },
});
