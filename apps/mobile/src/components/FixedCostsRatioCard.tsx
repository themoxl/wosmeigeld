import { StyleSheet, Text, View } from "react-native";
import { useBudgetSummary } from "../hooks/useBudget";
import { rateFixedCostsRatio } from "../hooks/useContracts";
import { letterSpacingLabel, space, toneColors, typeScale, useTheme } from "../theme";
import { formatMonthLabel } from "../utils/dates";
import { AFTER_FIXED_COSTS_LABEL, formatIncomeBasisSource } from "../utils/financeLabels";
import { InlineQueryState } from "./InlineQueryState";
import { Badge, Card, CardLabel, Money, PacingBar, SplitMeter, StatTile } from "./ui";

/**
 * Leitkarte "Kostenstruktur" (docs/mobile_redesign_showcase.html, Abschnitt
 * "2. SCREEN: BERICHTE & ANALYTICS").
 *
 * Sie beantwortet die Frage, wegen der man einen Bericht oeffnet: wie viel
 * des Monats ist ueberhaupt noch beeinflussbar? Deshalb stehen Fixkosten und
 * variable Kosten nebeneinander und nicht nur die Quote.
 *
 * Datenquelle ist ausschliesslich die Budgetzusammenfassung des Monats. Der
 * Uebersichtsbericht (/v1/reports/overview) liefert dieselben Fixkostenzahlen
 * - er reicht sie unveraendert aus genau dieser Zusammenfassung durch -, kennt
 * aber nur den LAUFENDEN Monat. Ueber `year`/`month` laesst sich die Karte
 * damit auch auf einen zurueckliegenden Monat richten, ohne einen zweiten
 * Datenpfad zu oeffnen.
 *
 * Bezugsgroesse der Quote sind die GEBUCHTEN Einnahmen; nur wenn keine
 * vorliegen, faellt der Server auf Einnahmevertraege zurueck
 * (`fixed_costs_income_basis`). Das steht als Fussnote in der Karte, weil eine
 * geschaetzte Quote sonst wie eine gemessene aussaehe.
 */
type Props = {
  /** Kalenderjahr des dargestellten Monats. Ohne Angabe der laufende Monat. */
  year?: number;
  /** 1-basierter Monat. Ohne Angabe der laufende Monat. */
  month?: number;
};

export function FixedCostsRatioCard({ year, month }: Props) {
  const theme = useTheme();

  const now = new Date();
  const targetYear = year ?? now.getFullYear();
  const targetMonth = month ?? now.getMonth() + 1;

  const summaryQuery = useBudgetSummary(targetYear, targetMonth);
  const summary = summaryQuery.data;
  const monthLabel = formatMonthLabel(new Date(targetYear, targetMonth - 1, 1));

  if (!summary) {
    return (
      <Card accentTop="info" testID="kostenstruktur">
        <CardLabel>KOSTENSTRUKTUR (IST-ANALYSE)</CardLabel>
        <InlineQueryState
          status={summaryQuery.isError ? "error" : summaryQuery.isLoading ? "loading" : "empty"}
          title={
            summaryQuery.isError
              ? "Die Kostenstruktur konnte nicht geladen werden"
              : summaryQuery.isLoading
                ? undefined
                : `Für ${monthLabel} liegen keine Zahlen vor`
          }
          onRetry={summaryQuery.isError ? () => void summaryQuery.refetch() : undefined}
          skeletonRows={4}
          skeletonRowHeight={40}
          testID="kostenstruktur-zustand"
        />
      </Card>
    );
  }

  const income = summary.total_income_minor;
  const expense = summary.total_spent_minor;
  const fixed = summary.fixed_costs_monthly_minor;
  // Die Aufteilung steht bewusst auf EINER Seite: `fixed_costs_monthly_minor`
  // ist die vereinbarte Monatssumme der Vertraege, `variable_planned_budget_minor`
  // der geplante Rest des Monatsbudgets - beides Planwerte desselben Monats.
  //
  // Frueher stand hier `total_spent_minor - fixed_costs_monthly_minor`. Das
  // mischte zwei unvereinbare Bezugsgroessen: gebuchte Miete steckte in beiden
  // Summanden, noch nicht gebuchte Vertragsbetraege nur in einem - der "Rest"
  // war deshalb gar keine Ausgabengroesse, und an frueheren Monatstagen kam
  // regelmaessig "100 % Fix" heraus.
  const variablePlanned = summary.variable_planned_budget_minor;
  const net = income - expense;

  const structureBase = fixed + variablePlanned;
  const fixedShare = structureBase > 0 ? Math.round((fixed / structureBase) * 100) : null;
  const splitLabel =
    fixedShare === null
      ? "Noch nichts geplant"
      : `${fixedShare} % Fix / ${100 - fixedShare} % Variabel (geplant)`;

  const rating = rateFixedCostsRatio(summary.fixed_costs_ratio_percent, fixed);
  const ratingColors = toneColors(theme, rating.tone);
  const ratioPercent = Math.max(0, Math.min(100, summary.fixed_costs_ratio_percent));
  const percentText =
    rating.tone === "neutral"
      ? "—"
      : `${summary.fixed_costs_ratio_percent.toLocaleString("de-DE", {
          minimumFractionDigits: 1,
          maximumFractionDigits: 1,
        })} %`;

  // "Fehlende Basis": weder gebuchte Einnahmen noch Einnahmevertraege liefern
  // eine Bezugsgroesse - ein errechnetes "0 EUR" waere dann keine echte
  // Aussage, sondern nur die Abwesenheit jeder Grundlage.
  const basisSource = summary.fixed_costs_income_basis;
  const basisAmount =
    basisSource === "actual"
      ? summary.total_income_minor
      : summary.fixed_costs_income_monthly_minor;
  const missingBasis = basisAmount <= 0;
  const basisHintText = missingBasis ? null : formatIncomeBasisSource(basisSource === "contract");

  const savingsRateText = `${summary.savings_rate_percent.toLocaleString("de-DE", {
    maximumFractionDigits: 0,
  })} % der Einnahmen`;

  return (
    <Card accentTop="info" testID="kostenstruktur">
      <View style={styles.headerRow}>
        <CardLabel>KOSTENSTRUKTUR (IST-ANALYSE)</CardLabel>
        <Badge label={splitLabel} tone="info" />
      </View>

      <View style={styles.totalsRow}>
        <View style={styles.totalsColumn}>
          <Text style={[styles.miniLabel, { color: theme.textSecondary }]}>GESAMTAUSGABEN</Text>
          <Money minorUnits={-expense} size="display" tone="plain" />
          <Text style={[styles.caption, { color: theme.textMuted }]}>{monthLabel}</Text>
        </View>
        <View style={[styles.totalsColumn, styles.totalsRight]}>
          <Text style={[styles.miniLabel, { color: theme.textSecondary }]}>EINNAHMEN</Text>
          <Money minorUnits={income} size="medium" tone="positive" />
        </View>
      </View>

      <SplitMeter
        segments={[
          { value: fixed, tone: "info", label: "Fix" },
          { value: variablePlanned, tone: "purple", label: "Variabel" },
        ]}
        accessibilityLabel={`Geplante Kostenstruktur ${monthLabel}: ${splitLabel}`}
        testID="kostenstruktur-aufteilung"
      />

      <View style={styles.tileRow}>
        <StatTile
          label="FIXKOSTEN"
          tone="info"
          value={<Money minorUnits={-fixed} size="medium" tone="plain" />}
          caption="Vereinbarte Verträge: Miete, Strom, Abos"
        />
        <StatTile
          label="VARIABLES BUDGET"
          tone="purple"
          value={<Money minorUnits={-variablePlanned} size="medium" tone="plain" />}
          caption="Geplant für alles außer Verträgen"
        />
      </View>

      <View style={styles.metricRow}>
        <Text style={[styles.metricLabel, { color: theme.textSecondary }]}>Fixkostenquote</Text>
        <View style={styles.metricValue}>
          <Text
            style={[styles.percentValue, { color: ratingColors.text }]}
            accessibilityLabel={rating.tone === "neutral" ? "Quote nicht ermittelbar" : percentText}
          >
            {percentText}
          </Text>
          <Badge label={rating.label} tone={rating.tone} />
        </View>
      </View>

      <PacingBar
        progress={ratioPercent / 100}
        tone={rating.tone}
        accessibilityLabel={`Fixkostenquote: ${Math.round(ratioPercent)} Prozent`}
        testID="kostenstruktur-quote"
      />

      <View style={styles.metricRow}>
        <Text style={[styles.metricLabel, { color: theme.textSecondary }]}>
          {AFTER_FIXED_COSTS_LABEL}
        </Text>
        {missingBasis ? (
          <Text
            style={[styles.percentValue, { color: theme.textSecondary }]}
            accessibilityLabel="Kein Betrag ermittelbar"
          >
            —
          </Text>
        ) : (
          <Money
            minorUnits={summary.available_after_fixed_minor}
            tone={summary.available_after_fixed_minor < 0 ? "warning" : "plain"}
          />
        )}
      </View>

      {basisHintText ? (
        <Text style={[styles.caption, { color: theme.textMuted }]}>{basisHintText}</Text>
      ) : null}

      <View style={[styles.footerRow, { borderTopColor: theme.border }]}>
        <Text style={[styles.metricLabel, { color: theme.textSecondary }]}>
          Monatlicher Sparüberschuss
        </Text>
        <View style={styles.footerValue}>
          <Money minorUnits={net} showPlusSign tone={net < 0 ? "warning" : "positive"} />
          <Text style={[styles.caption, { color: theme.textSecondary }]}>{savingsRateText}</Text>
        </View>
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
    flexWrap: "wrap",
  },
  totalsRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: space.md,
  },
  totalsColumn: {
    gap: space.hair,
    flexShrink: 1,
  },
  totalsRight: {
    alignItems: "flex-end",
  },
  miniLabel: {
    fontSize: typeScale.micro,
    fontWeight: "700",
    letterSpacing: letterSpacingLabel,
  },
  caption: {
    fontSize: typeScale.micro,
  },
  tileRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  metricRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  metricLabel: {
    fontSize: typeScale.caption,
    flexShrink: 1,
  },
  metricValue: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
  },
  percentValue: {
    fontSize: typeScale.medium,
    fontWeight: "800",
  },
  footerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: space.sm,
  },
  footerValue: {
    alignItems: "flex-end",
    gap: space.hair,
  },
});
