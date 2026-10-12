import { StyleSheet, Text, View } from "react-native";
import type { BudgetSummary } from "../api/types";
import { leading, space, typeScale, useTheme } from "../theme";
import { SPENDING_MIX_LABEL } from "../utils/financeLabels";
import { Badge, Card, CardLabel, Money, SplitMeter, type SplitSegment } from "./ui";

// 50/30/20-Karte des Showcase, Abschnitt "3. SCREEN: BUDGET & ENVELOPES".
//
// Die Zahlen kommen ausschliesslich aus den bucket_*-Feldern der
// Budgetauskunft (budget_service.py: needs/wants/savings der kategorisierten
// Nettoausgaben). Die frueheren Naeherungen dieser Karte - "Grundbedarf =
// Fixkosten + 40 % der Ausgaben" - waren erfundene Kennzahlen und sind
// ersatzlos entfallen.
//
// Wichtig fuer den Wortlaut: bucket_*_percent ist der Anteil an den
// KATEGORISIERTEN NETTOAUSGABEN, nicht am Einkommen. Deshalb heisst die Karte
// "Ist-Ausgabenmix" (utils/financeLabels.ts) und 50/30/20 steht als Zielmix
// daneben, nicht als Quote vom Gehalt.

type Props = {
  budget: BudgetSummary | undefined;
};

/** Zielanteile des Mixes - dieselbe Reihenfolge wie die Abschnitte im Balken. */
const TARGET_NEEDS_PERCENT = 50;
const TARGET_WANTS_PERCENT = 30;
const TARGET_SAVINGS_PERCENT = 20;

export function BudgetStrategyCard({ budget }: Props) {
  const theme = useTheme();

  if (!budget) return null;

  const needsMinor = budget.bucket_needs_minor;
  const wantsMinor = budget.bucket_wants_minor;
  const savingsMinor = budget.bucket_savings_minor;
  const unassignedMinor = budget.bucket_unassigned_spent_minor;
  const classifiedMinor = needsMinor + wantsMinor + savingsMinor;

  // Ohne einen einzigen zugeordneten Euro waere jeder Anteil 0 % - eine
  // Balkengrafik daraus saehe nach "alles im Rahmen" aus, obwohl schlicht
  // nichts gemessen wurde.
  if (classifiedMinor <= 0) {
    return (
      <Card>
        <CardLabel>{`${SPENDING_MIX_LABEL} · 50 / 30 / 20`}</CardLabel>
        <Text style={[styles.hint, { color: theme.textSecondary }]}>
          Noch keine Ausgabe mit Zielmix-Zuordnung in diesem Monat. Die Zuordnung (Grundbedarf,
          Wünsche, Sparen) wird je Kategorie gepflegt.
        </Text>
      </Card>
    );
  }

  const needsPercent = budget.bucket_needs_percent;
  const savingsPercent = budget.bucket_savings_percent;

  // Aussage bewusst auf den Mix bezogen: zu viel Grundbedarf ODER zu wenig
  // Sparen verschiebt die Aufteilung weg vom Zielbild.
  const withinTarget =
    needsPercent <= TARGET_NEEDS_PERCENT && savingsPercent >= TARGET_SAVINGS_PERCENT;

  const segments: SplitSegment[] = [
    {
      value: needsMinor,
      tone: "info",
      label: "Grundbedarf",
      detail: `Ziel ${TARGET_NEEDS_PERCENT} %`,
    },
    {
      value: wantsMinor,
      tone: "purple",
      label: "Wünsche",
      detail: `Ziel ${TARGET_WANTS_PERCENT} %`,
    },
    {
      value: savingsMinor,
      tone: "positive",
      label: "Sparen",
      detail: `Ziel ${TARGET_SAVINGS_PERCENT} %`,
    },
  ];

  const meterLabel = `${SPENDING_MIX_LABEL}: Grundbedarf ${Math.round(needsPercent)} von 50 Prozent, Wünsche ${Math.round(budget.bucket_wants_percent)} von 30 Prozent, Sparen ${Math.round(savingsPercent)} von 20 Prozent`;

  return (
    <Card>
      <View style={styles.header}>
        <CardLabel>{`${SPENDING_MIX_LABEL} · 50 / 30 / 20`}</CardLabel>
        <Badge
          label={withinTarget ? "Zielmix eingehalten" : "Zielmix überschritten"}
          tone={withinTarget ? "positive" : "warning"}
        />
      </View>

      <SplitMeter segments={segments} height={10} accessibilityLabel={meterLabel} />

      <View style={styles.footRow}>
        <Text style={[styles.footLabel, { color: theme.textSecondary }]}>
          Kategorisierte Nettoausgaben
        </Text>
        <Money minorUnits={classifiedMinor} size="row" tone="plain" />
      </View>

      {unassignedMinor !== 0 ? (
        <View style={styles.footRow}>
          <Text style={[styles.footLabel, { color: theme.textSecondary }]}>
            Ohne Zielmix-Zuordnung
          </Text>
          <Money minorUnits={unassignedMinor} size="row" tone="muted" />
        </View>
      ) : null}

      <Text style={[styles.hint, { color: theme.textMuted }]}>
        Anteile an den kategorisierten Nettoausgaben – nicht am Einkommen.
      </Text>
    </Card>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  footRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  footLabel: {
    flexShrink: 1,
    fontSize: typeScale.caption,
    fontWeight: "600",
  },
  hint: {
    fontSize: typeScale.micro,
    lineHeight: leading(typeScale.micro),
  },
});
