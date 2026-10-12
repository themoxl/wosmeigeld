import { useRouter } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import type {
  AnalyticsQuery,
  AnalyticsResult,
  AnalyticsRow,
  CashflowTrendResponse,
  FixedCostsTrendResponse,
  ProjectReportRow,
} from "../../src/api/types";
import { FixedCostsRatioCard } from "../../src/components/FixedCostsRatioCard";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { MonthNavigator } from "../../src/components/MonthNavigator";
import { ScreenContainer, SCREEN_EDGES_WITH_TOP } from "../../src/components/ScreenContainer";
import {
  SegmentedControl,
  type SegmentedControlOption,
} from "../../src/components/SegmentedControl";
import { SheetModal } from "../../src/components/SheetModal";
import {
  Badge,
  Card,
  CardLabel,
  Chip,
  ChipRow,
  DataRow,
  DonutChart,
  IconButton,
  Money,
  PacingBar,
  ScreenHeader,
  StatTile,
  monoTextStyle,
  type DonutSlice,
} from "../../src/components/ui";
import {
  useCashflowTrend,
  useFixedCostsTrend,
  useProjectReports,
  useReportQuery,
} from "../../src/hooks/useReports";
import {
  leading,
  MASKED_PLAIN,
  radii,
  space,
  toneColors,
  toneForLabel,
  typeScale,
  useMaskedAmount,
  usePrivacy,
  useTheme,
  type AccentTone,
  type ThemeTokens,
} from "../../src/theme";
import { formatShortDate } from "../../src/utils/dates";
import { formatMinorUnits, formatMinorUnitsCompact } from "../../src/utils/money";
import {
  categoryMonthlyQuery,
  drilldownRowsQuery,
  groupMonthlyQuery,
  payeeMonthlyQuery,
  yearComparisonQuery,
  DRILLDOWN_ROW_LIMIT,
  type DrilldownSpec,
} from "../../src/utils/reportQueries";
import {
  compareCategoryResults,
  describeCashflow,
  describeFixedCosts,
  describeProjectReports,
  describeScope,
  fairYearPeriods,
  rankDimensionResult,
  reportMonth,
  type FigureTone,
  type RankedResult,
  type RankedRow,
  type ReportFigure,
} from "../../src/utils/reportsPresentation";

/**
 * Berichte, lesend (docs/ANALYSE-EXPLORER-PLAN.md, Baustein E;
 * docs/mobile_redesign_showcase.html, Abschnitt "2. SCREEN: BERICHTE &
 * ANALYTICS").
 *
 * Seit dem Redesign ein Bottom-Tab statt eines Eintrags unter "Mehr": die
 * Buchungsliste ist im Alltag passiv, Berichte beantworten die Frage, wegen
 * der man die App oeffnet.
 *
 * Dieselben sechs Register wie im Web und dieselben Abfragen dahinter - aber
 * nicht dieselbe Darstellung. Die Weboberflaeche zeigt Dimension×Monat als
 * Kreuztabelle; im Hochformat waeren das zwoelf Zahlenspalten, die niemand
 * liest. Hier traegt stattdessen jede Zeile ihre Periodensumme, ihren
 * Monatsschnitt und ihren Anteil, und die Monatswerte stehen als
 * Balkenstreifen darunter.
 *
 * Es gibt bewusst keinen Abfragebaukasten: der Explorer bleibt der
 * Weboberflaeche vorbehalten. Was hier steht, sind sechs feste Fragen.
 *
 * Die Zusage aus Leitplanke 1 gilt auch auf dem Telefon: von jeder Zahl aus
 * gebuchten Betraegen fuehrt hoechstens ein Tippen zu den Buchungen dahinter.
 * Ausgenommen sind die Register "Fixkosten" (vereinbarte Vertragsbetraege) und
 * "Vorhaben" (Kostenpositionen und Tilgungsplaene) - dort liegen hinter den
 * Zahlen keine Buchungen, zu denen ein Drilldown fuehren koennte.
 */

type Register = "ueberblick" | "kategorien" | "empfaenger" | "vergleich" | "fixkosten" | "vorhaben";

const REGISTERS: { key: Register; label: string }[] = [
  { key: "ueberblick", label: "Überblick" },
  { key: "kategorien", label: "Kategorien" },
  { key: "empfaenger", label: "Empfänger" },
  { key: "vergleich", label: "Jahresvergleich" },
  { key: "fixkosten", label: "Fixkosten" },
  { key: "vorhaben", label: "Vorhaben" },
];

/** Zeitraeume des Showcase-Umschalters. */
type Timeframe = "monat" | "quartal" | "halbjahr" | "jahr";

const TIMEFRAME_OPTIONS: SegmentedControlOption<Timeframe>[] = [
  { value: "monat", label: "Monat", accessibilityLabel: "Zeitraum Monat" },
  { value: "quartal", label: "3 Monate", accessibilityLabel: "Zeitraum 3 Monate" },
  { value: "halbjahr", label: "6 Monate", accessibilityLabel: "Zeitraum 6 Monate" },
  { value: "jahr", label: "12 Monate", accessibilityLabel: "Zeitraum 12 Monate" },
];

const TIMEFRAME_MONTHS: Record<Timeframe, number> = {
  monat: 1,
  quartal: 3,
  halbjahr: 6,
  jahr: 12,
};

/** Wie viele Zeilen ein Register zeigt, bevor "Alle anzeigen" erscheint. */
const RANKED_PREVIEW_COUNT = 8;
/** Kategoriegruppen unter dem Ring - mehr Zeilen als Ringabschnitte lesbar sind. */
const CATEGORY_PREVIEW_COUNT = 6;

interface DrilldownRequest {
  title: string;
  subtitle: string;
  query: AnalyticsQuery;
}

/** Verschiebt (year, month) um `delta` Monate, ueber Jahresgrenzen hinweg. */
function shiftMonth(year: number, month: number, delta: number): { year: number; month: number } {
  const total = year * 12 + (month - 1) + delta;
  return { year: Math.floor(total / 12), month: (((total % 12) + 12) % 12) + 1 };
}

export default function BerichteScreen() {
  const theme = useTheme();
  const privacy = usePrivacy();
  const [register, setRegister] = useState<Register>("ueberblick");
  const [timeframe, setTimeframe] = useState<Timeframe>("monat");
  const [drilldown, setDrilldown] = useState<DrilldownRequest | null>(null);

  // Ein einziges "heute" fuer den ganzen Screen: sonst koennte die Auswahl des
  // Entscheidungsmonats zwischen zwei Registern kippen, wenn der Screen ueber
  // Mitternacht offen bleibt.
  const today = useMemo(() => new Date(), []);
  const [selectedMonth, setSelectedMonth] = useState(() => ({
    year: today.getFullYear(),
    month: today.getMonth() + 1,
  }));
  const [comparisonYear, setComparisonYear] = useState(() => new Date().getFullYear());

  const months = TIMEFRAME_MONTHS[timeframe];
  const isMonthView = timeframe === "monat";

  const monthWindow = useMemo(() => {
    const current = reportMonth(selectedMonth.year, selectedMonth.month, today);
    const before = shiftMonth(selectedMonth.year, selectedMonth.month, -1);
    return { current, previous: reportMonth(before.year, before.month, today) };
  }, [selectedMonth, today]);

  // Cashflow- und Fixkostenverlauf sind Reihen: mit einem einzigen Punkt gibt
  // es weder einen abgeschlossenen Vormonat noch einen Verlauf. In der
  // Monatsansicht bleiben sie deshalb bei zwoelf Monaten - der Navigator
  // steuert die Monatskarten daruber, nicht die Reihen.
  const trendMonths = isMonthView ? 12 : months;

  const withPeriod = useCallback(
    (query: AnalyticsQuery, from: string, to: string): AnalyticsQuery => ({
      ...query,
      period: { kind: "custom", from_date: from, to_date: to },
    }),
    [],
  );

  // Der Ring braucht zwei Monate: die Veraenderung je Kategoriegruppe soll aus
  // denselben Zahlen belegt sein wie der Betrag daneben, nicht aus einer
  // zweiten Abfrage mit eigenem Zuschnitt. Angezeigt wird trotzdem nur der
  // gewaehlte Monat.
  const groupQuery = useMemo(
    () =>
      isMonthView
        ? withPeriod(groupMonthlyQuery(2), monthWindow.previous.from, monthWindow.current.to)
        : groupMonthlyQuery(months),
    [isMonthView, withPeriod, monthWindow, months],
  );
  const categoryQuery = useMemo(
    () =>
      isMonthView
        ? withPeriod(categoryMonthlyQuery(1), monthWindow.current.from, monthWindow.current.to)
        : categoryMonthlyQuery(months),
    [isMonthView, withPeriod, monthWindow, months],
  );
  const payeeQuery = useMemo(
    () =>
      isMonthView
        ? withPeriod(payeeMonthlyQuery(1), monthWindow.current.from, monthWindow.current.to)
        : payeeMonthlyQuery(months),
    [isMonthView, withPeriod, monthWindow, months],
  );
  const periods = useMemo(() => fairYearPeriods(comparisonYear, today), [comparisonYear, today]);

  const trend = useCashflowTrend(trendMonths);
  const groups = useReportQuery("groups", groupQuery, register === "ueberblick");
  const categories = useReportQuery("categories", categoryQuery, register === "kategorien");
  const payees = useReportQuery("payees", payeeQuery, register === "empfaenger");
  const fixedCosts = useFixedCostsTrend(trendMonths, register === "fixkosten");
  const projects = useProjectReports(register === "vorhaben");

  const selectedYearQuery = useMemo(
    () => yearComparisonQuery(periods?.selectedFrom ?? "", periods?.selectedTo ?? ""),
    [periods],
  );
  const previousYearQuery = useMemo(
    () => yearComparisonQuery(periods?.previousFrom ?? "", periods?.previousTo ?? ""),
    [periods],
  );
  const selectedYearResult = useReportQuery(
    `year-${comparisonYear}`,
    selectedYearQuery,
    register === "vergleich" && periods !== null,
  );
  const previousYearResult = useReportQuery(
    `year-${comparisonYear - 1}`,
    previousYearQuery,
    register === "vergleich" && periods !== null,
  );

  const openDrilldown = (title: string, subtitle: string, spec: DrilldownSpec) => {
    setDrilldown({ title, subtitle, query: drilldownRowsQuery(spec) });
  };

  // Der Umschalter steuert nur Auswertungen aus Buchungen. "Jahresvergleich"
  // bringt seine eigene Jahreswahl mit, "Vorhaben" kennt gar keinen Zeitraum.
  const showTimeframe = register !== "vergleich" && register !== "vorhaben";
  const showMonthNavigator = showTimeframe && isMonthView && register !== "fixkosten";

  const scopeText = useMemo(() => {
    if (register === "vergleich" || register === "vorhaben") {
      return describeScope(register, months, periods?.scopeLabel);
    }
    if (register === "fixkosten") return describeScope(register, trendMonths);
    return isMonthView
      ? `${monthWindow.current.label} · Transfers aus · nur Budgetkonten`
      : describeScope(register, months);
  }, [register, months, trendMonths, isMonthView, monthWindow, periods]);

  const isRefreshing =
    trend.isFetching ||
    groups.isFetching ||
    categories.isFetching ||
    payees.isFetching ||
    fixedCosts.isFetching ||
    projects.isFetching;

  const onRefresh = () => {
    void trend.refetch();
    if (register === "ueberblick") void groups.refetch();
    if (register === "kategorien") void categories.refetch();
    if (register === "empfaenger") void payees.refetch();
    if (register === "fixkosten") void fixedCosts.refetch();
    if (register === "vorhaben") void projects.refetch();
    if (register === "vergleich") {
      void selectedYearResult.refetch();
      void previousYearResult.refetch();
    }
  };

  return (
    <>
      <ScreenContainer edges={SCREEN_EDGES_WITH_TOP}>
        <ScrollView
          contentContainerStyle={styles.content}
          refreshControl={
            <RefreshControl
              refreshing={isRefreshing}
              onRefresh={onRefresh}
              tintColor={theme.textSecondary}
            />
          }
        >
          <ScreenHeader
            title="Berichte & Analyse"
            actions={
              <IconButton
                icon={privacy.isPrivacyMode ? "eye-off-outline" : "eye-outline"}
                tone={privacy.isPrivacyMode ? "highlight" : undefined}
                onPress={privacy.toggle}
                accessibilityLabel={
                  privacy.isPrivacyMode ? "Beträge einblenden" : "Beträge verbergen"
                }
                testID="berichte-privatmodus"
              />
            }
          />

          <View style={styles.body}>
            {showTimeframe ? (
              <SegmentedControl
                options={TIMEFRAME_OPTIONS}
                value={timeframe}
                onChange={setTimeframe}
                accessibilityLabel="Zeitraum der Auswertung"
              />
            ) : null}

            {showMonthNavigator ? (
              <MonthNavigator
                year={selectedMonth.year}
                month={selectedMonth.month}
                onChange={(year, month) => setSelectedMonth({ year, month })}
              />
            ) : null}

            <RegisterStrip value={register} onChange={setRegister} />

            <Text
              style={[styles.scope, { color: theme.textSecondary }]}
              testID="berichte-zuschnitt"
            >
              {scopeText}
            </Text>

            {register === "ueberblick" ? (
              <UeberblickRegister
                trendQuery={trend}
                groupsQuery={groups}
                months={trendMonths}
                monthWindow={monthWindow}
                isMonthView={isMonthView}
                today={today}
                theme={theme}
                onDrilldown={openDrilldown}
              />
            ) : null}

            {register === "kategorien" ? (
              <RankedRegister
                title="Kategorien"
                note="Ausgaben je Kategorie über den gewählten Zeitraum. Die größte zuerst."
                query={categories}
                dimensionKey="category"
                groupKey="category_group"
                today={today}
                theme={theme}
                onDrilldown={openDrilldown}
              />
            ) : null}

            {register === "empfaenger" ? (
              <RankedRegister
                title="Empfänger"
                note="Wohin das Geld geht, unabhängig von der Kategorie."
                query={payees}
                dimensionKey="payee"
                today={today}
                theme={theme}
                onDrilldown={openDrilldown}
              />
            ) : null}

            {register === "vergleich" ? (
              <VergleichRegister
                year={comparisonYear}
                onChangeYear={setComparisonYear}
                periods={periods}
                selected={selectedYearResult}
                previous={previousYearResult}
                theme={theme}
                onDrilldown={openDrilldown}
              />
            ) : null}

            {register === "fixkosten" ? (
              <FixkostenRegister query={fixedCosts} theme={theme} />
            ) : null}

            {register === "vorhaben" ? <VorhabenRegister query={projects} theme={theme} /> : null}
          </View>
        </ScrollView>
      </ScreenContainer>

      {drilldown ? (
        <DrilldownSheet request={drilldown} theme={theme} onClose={() => setDrilldown(null)} />
      ) : null}
    </>
  );
}

/**
 * Sechs Register passen nicht nebeneinander auf ein Telefon. Statt sie zu
 * kuerzen ("Vergl.") laeuft der Streifen waagerecht - die Beschriftungen
 * bleiben damit dieselben Woerter wie im Web.
 */
function RegisterStrip({
  value,
  onChange,
}: {
  value: Register;
  onChange: (next: Register) => void;
}) {
  return (
    <View accessibilityRole="tablist" accessibilityLabel="Berichtsregister">
      <ChipRow>
        {REGISTERS.map((entry) => (
          <Chip
            key={entry.key}
            label={entry.label}
            selected={entry.key === value}
            onPress={() => onChange(entry.key)}
          />
        ))}
      </ChipRow>
    </View>
  );
}

function toneForFigure(tone: FigureTone): AccentTone | undefined {
  if (tone === "positive") return "positive";
  if (tone === "negative") return "warning";
  return undefined;
}

/**
 * Ein bereits fertig formatierter Wert aus src/utils/reportsPresentation.ts.
 *
 * <Money> braucht Minor Units; die Aufbereitung liefert bewusst Zeichenketten
 * (auch Prozentwerte, die gar kein Betrag sind), und sie hier neu zu rechnen
 * wuerde die geprueften Regeln der Auswertung verdoppeln. Deshalb dieselbe
 * Schrift und derselbe Privat-Modus wie in <Money>, nur ohne Umrechnung.
 */
function ReportValue({
  value,
  tone,
  size = typeScale.large,
}: {
  value: string;
  tone: FigureTone;
  size?: number;
}) {
  const theme = useTheme();
  const mask = useMaskedAmount();

  const color =
    tone === "positive" ? theme.positive : tone === "negative" ? theme.warning : theme.textPrimary;

  // Prozentwerte verraten kein Vermoegen und bleiben im Privat-Modus stehen.
  const isPercent = value.trim().endsWith("%");

  return (
    <Text style={[monoTextStyle, styles.reportValue, { fontSize: size, color }]}>
      {isPercent ? value : mask(value)}
    </Text>
  );
}

/** Kennzahlen einer Auswertung als 2x2-Raster aus Innenkacheln. */
function FigureGrid({ figures }: { figures: ReportFigure[] }) {
  const theme = useTheme();
  const rows: ReportFigure[][] = [];
  for (let index = 0; index < figures.length; index += 2) {
    rows.push(figures.slice(index, index + 2));
  }

  return (
    <View style={styles.figureGrid}>
      {rows.map((row, rowIndex) => (
        <View key={`figures-${rowIndex}`} style={styles.tileRow}>
          {row.map((figure) => (
            <StatTile
              key={figure.key}
              label={figure.label}
              tone={toneForFigure(figure.tone)}
              caption={figure.periodHint}
              value={
                <View accessible accessibilityLabel={figure.accessibilityLabel}>
                  <ReportValue value={figure.value} tone={figure.tone} />
                  <Text style={[styles.figureHint, { color: theme.textSecondary }]}>
                    {figure.comparison}
                  </Text>
                </View>
              }
            />
          ))}
          {row.length === 1 ? <View style={styles.tileFiller} /> : null}
        </View>
      ))}
    </View>
  );
}

/**
 * Monatswerte einer Zeile als Balkenstreifen. Ersetzt die Monatsspalten der
 * Web-Kreuztabelle; die genauen Zahlen stehen nach dem Antippen in der
 * Buchungsliste, die Form zeigt hier nur, ob eine Ausgabe gleichmaessig
 * verteilt oder ein einzelner Ausreisser ist.
 */
function MonthlyStrip({
  row,
  theme,
  style,
}: {
  row: RankedRow;
  theme: ThemeTokens;
  style?: StyleProp<ViewStyle>;
}) {
  const peak = Math.max(...row.monthly.map((entry) => Math.abs(entry.value)), 1);
  return (
    <View
      style={[styles.strip, style]}
      accessible
      accessibilityLabel={`Verlauf über ${row.monthly.length} Monate, größter Monatswert ${formatMinorUnits(
        row.monthly.reduce(
          (max, entry) => (Math.abs(entry.value) > Math.abs(max) ? entry.value : max),
          0,
        ),
      )}`}
    >
      {row.monthly.map((entry) => (
        <View key={entry.key} style={styles.stripSlot}>
          <View
            style={[
              styles.stripBar,
              {
                height: `${Math.max(4, Math.round((Math.abs(entry.value) / peak) * 100))}%`,
                backgroundColor: entry.value === 0 ? theme.border : theme.highlightStrong,
              },
            ]}
          />
        </View>
      ))}
    </View>
  );
}

interface QueryLike<T> {
  data?: T;
  isLoading: boolean;
  isError: boolean;
  refetch: () => unknown;
}

type OpenDrilldown = (title: string, subtitle: string, spec: DrilldownSpec) => void;

interface MonthWindow {
  current: ReturnType<typeof reportMonth>;
  previous: ReturnType<typeof reportMonth>;
}

function UeberblickRegister({
  trendQuery,
  groupsQuery,
  months,
  monthWindow,
  isMonthView,
  today,
  theme,
  onDrilldown,
}: {
  trendQuery: QueryLike<CashflowTrendResponse>;
  groupsQuery: QueryLike<AnalyticsResult>;
  months: number;
  monthWindow: MonthWindow;
  isMonthView: boolean;
  today: Date;
  theme: ThemeTokens;
  onDrilldown: OpenDrilldown;
}) {
  const mask = useMaskedAmount();
  const overview = useMemo(
    () => (trendQuery.data ? describeCashflow(trendQuery.data, today) : null),
    [trendQuery.data, today],
  );

  return (
    <>
      <FixedCostsRatioCard year={monthWindow.current.year} month={monthWindow.current.month} />

      <CategoryDonutCard
        query={groupsQuery}
        monthWindow={monthWindow}
        isMonthView={isMonthView}
        today={today}
        theme={theme}
        onDrilldown={onDrilldown}
      />

      {trendQuery.isLoading && !trendQuery.data ? (
        <Card>
          <CardLabel>MONATSÜBERSICHT</CardLabel>
          <InlineQueryState
            status="loading"
            skeletonRows={4}
            skeletonRowHeight={48}
            testID="berichte-loading"
          />
        </Card>
      ) : null}

      {trendQuery.isError ? (
        <Card>
          <CardLabel>MONATSÜBERSICHT</CardLabel>
          <InlineQueryState
            status="error"
            title="Die Monatsübersicht konnte nicht geladen werden"
            onRetry={() => void trendQuery.refetch()}
            testID="berichte-error"
          />
        </Card>
      ) : null}

      {!trendQuery.isLoading && !trendQuery.isError && !overview ? (
        <Card>
          <CardLabel>MONATSÜBERSICHT</CardLabel>
          <InlineQueryState
            status="empty"
            title="Noch keine Buchungen im Zeitraum"
            message={`In den letzten ${months} Monaten liegen keine gebuchten Beträge auf Budgetkonten.`}
          />
        </Card>
      ) : null}

      {overview ? (
        <>
          <Card accentTop="positive">
            <View style={styles.cardHeader}>
              <CardLabel>ENTSCHEIDUNGSMONAT</CardLabel>
              <Badge label={overview.decisionMonth.label} tone="positive" />
            </View>
            <Text style={[styles.note, { color: theme.textSecondary }]}>
              Der jüngste vollständig abgeschlossene Monat. Der laufende Monat bleibt außen vor,
              solange er nicht zu Ende ist.
            </Text>
            <FigureGrid figures={overview.figures} />
          </Card>

          <Card>
            <CardLabel>MONAT FÜR MONAT</CardLabel>
            <Text style={[styles.note, { color: theme.textSecondary }]}>
              Antippen öffnet die Buchungen des Monats.
            </Text>
            {overview.months.map((month, index) => (
              <DataRow
                key={month.key}
                avatarIcon="calendar-outline"
                avatarTone={month.isRunning ? "highlight" : "info"}
                title={month.title}
                titleBadge={
                  month.isRunning ? (
                    <Badge label="laufend" tone="highlight" size="small" />
                  ) : undefined
                }
                subtitle={`Einnahmen ${mask(month.incomeLabel)} · Ausgaben ${mask(
                  month.expenseLabel,
                )}`}
                trailing={
                  <ReportValue
                    value={month.netLabel}
                    tone={month.netIsNegative ? "negative" : "positive"}
                    size={typeScale.medium}
                  />
                }
                accessibilityLabel={`${month.accessibilityLabel} Buchungen anzeigen.`}
                onPress={() =>
                  onDrilldown(month.title, "Buchungen dieses Monats", {
                    from_date: month.from,
                    to_date: month.to,
                    cashflowRole: "net",
                  })
                }
                divider={index < overview.months.length - 1}
              />
            ))}
          </Card>
        </>
      ) : null}
    </>
  );
}

/**
 * Im Privat-Modus traegt der Erkenntnissatz zwei echte Betraege - er wird
 * deshalb ganz ersetzt statt nur die Zahlen zu punktieren; ein Satz mit
 * "•••• €" an zwei Stellen ist keine Aussage mehr.
 */
const INSIGHT_MASKED_TEXT =
  "Im Privat-Modus verborgen. Der Vergleich zum Vormonat erscheint wieder, sobald der Modus aus ist.";

interface CategorySlice {
  key: string;
  id: string | null;
  label: string;
  /** Betrag der angezeigten Periode in Minor Units (Ausgaben negativ). */
  amount: number;
  /** Vergleichsbetrag des Vormonats; `null`, wenn keiner vorliegt. */
  previous: number | null;
  tone: AccentTone;
}

/**
 * Kategoriegruppen der Anzeigeperiode.
 *
 * In der Monatsansicht liefert die Abfrage zwei Monate; herausgesucht werden
 * die Werte ueber den Monatsschluessel und nicht ueber die Position im
 * Raster - eine Gruppe ohne Buchung im Vormonat wuerde sonst eine Spalte
 * verrutschen.
 */
function buildCategorySlices(
  ranked: RankedResult,
  monthWindow: MonthWindow,
  isMonthView: boolean,
): CategorySlice[] {
  return ranked.rows
    .map((row) => {
      const current = isMonthView
        ? (row.monthly.find((entry) => entry.key === monthWindow.current.key)?.value ?? 0)
        : row.sum;
      const previousEntry = isMonthView
        ? row.monthly.find((entry) => entry.key === monthWindow.previous.key)
        : undefined;
      return {
        key: row.identityKey,
        id: row.id,
        label: row.label,
        amount: current,
        previous: previousEntry ? previousEntry.value : null,
        tone: toneForLabel(row.label),
      };
    })
    .filter((slice) => slice.amount !== 0)
    .sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
}

/**
 * Ein Satz aus den Zahlen, die schon in der Liste darunter stehen: welche
 * Kategoriegruppe am staerksten zugelegt hat und welche am staerksten
 * nachgelassen. Die Karte rechnet NICHTS dazu - sie nennt nur die beiden
 * groessten Abweichungen gegenueber dem Vergleichsmonat beim Namen. Ohne
 * Vergleichswerte (Jahresansicht, erster Monat) entsteht kein Satz.
 */
function describeCategoryInsight(slices: CategorySlice[]): {
  text: string;
  tone: AccentTone;
} | null {
  const comparable = slices
    .filter((slice) => slice.previous !== null && slice.previous !== 0)
    .map((slice) => ({
      label: slice.label,
      amount: Math.abs(slice.amount),
      deltaMinor: Math.abs(slice.amount) - Math.abs(slice.previous as number),
    }));
  if (comparable.length === 0) return null;

  const up = comparable.reduce((best, entry) =>
    entry.deltaMinor > best.deltaMinor ? entry : best,
  );
  const down = comparable.reduce((best, entry) =>
    entry.deltaMinor < best.deltaMinor ? entry : best,
  );

  const parts: string[] = [];
  if (up.deltaMinor > 0) {
    parts.push(
      `${up.label} liegt in diesem Monat bei ${formatMinorUnits(up.amount)} und damit ${formatMinorUnits(up.deltaMinor)} über dem Vormonat.`,
    );
  }
  if (down.deltaMinor < 0 && down.label !== up.label) {
    parts.push(`Bei ${down.label} sind es ${formatMinorUnits(Math.abs(down.deltaMinor))} weniger.`);
  }
  if (parts.length === 0) return null;

  return { text: parts.join(" "), tone: up.deltaMinor > 0 ? "highlight" : "positive" };
}

/** Text und Farbe der Veraenderung zum Vormonat. */
function describeChange(
  current: number,
  previous: number | null,
): { text: string; tone: "warning" | "positive" | "muted" } | null {
  if (previous === null) return null;
  if (previous === 0) return { text: "neu gegenüber Vormonat", tone: "warning" };
  const percent = Math.round(((Math.abs(current) - Math.abs(previous)) / Math.abs(previous)) * 100);
  if (percent === 0) return { text: "wie im Vormonat", tone: "muted" };
  return {
    text: `${percent > 0 ? "+" : ""}${percent} % zum Vormonat`,
    tone: percent > 0 ? "warning" : "positive",
  };
}

function CategoryDonutCard({
  query,
  monthWindow,
  isMonthView,
  today,
  theme,
  onDrilldown,
}: {
  query: QueryLike<AnalyticsResult>;
  monthWindow: MonthWindow;
  isMonthView: boolean;
  today: Date;
  theme: ThemeTokens;
  onDrilldown: OpenDrilldown;
}) {
  const privacy = usePrivacy();
  const [showAll, setShowAll] = useState(false);

  const ranked = useMemo(
    () => (query.data ? rankDimensionResult(query.data, "category_group", undefined, today) : null),
    [query.data, today],
  );
  const slices = useMemo(
    () => (ranked ? buildCategorySlices(ranked, monthWindow, isMonthView) : []),
    [ranked, monthWindow, isMonthView],
  );

  const insight = useMemo(
    () => (isMonthView ? describeCategoryInsight(slices) : null),
    [slices, isMonthView],
  );

  const total = slices.reduce((sum, slice) => sum + slice.amount, 0);
  const magnitude = slices.reduce((sum, slice) => sum + Math.abs(slice.amount), 0);
  const visible = showAll ? slices : slices.slice(0, CATEGORY_PREVIEW_COUNT);
  const hidden = slices.length - visible.length;

  const donutSlices: DonutSlice[] = slices.map((slice) => ({
    label: slice.label,
    value: Math.abs(slice.amount),
    tone: slice.tone,
  }));

  const range = isMonthView
    ? { from: monthWindow.current.from, to: monthWindow.current.to }
    : { from: ranked?.months[0]?.from ?? "", to: ranked?.months.at(-1)?.to ?? "" };

  return (
    <Card testID="berichte-kategorien">
      <View style={styles.cardHeader}>
        <CardLabel>KATEGORIEN-VERTEILUNG</CardLabel>
        {slices[0] ? <Badge label={`Größter Posten: ${slices[0].label}`} tone="info" /> : null}
      </View>

      {query.isLoading && !ranked ? (
        <InlineQueryState
          status="loading"
          skeletonRows={5}
          skeletonRowHeight={40}
          testID="berichte-kategorien-loading"
        />
      ) : null}
      {query.isError ? (
        <InlineQueryState
          status="error"
          title="Die Kategorien konnten nicht geladen werden"
          onRetry={() => void query.refetch()}
          testID="berichte-kategorien-error"
        />
      ) : null}
      {ranked && slices.length === 0 ? (
        <InlineQueryState status="empty" title="Keine Ausgaben im gewählten Zeitraum" />
      ) : null}

      {slices.length > 0 ? (
        <>
          <DonutChart
            slices={donutSlices}
            centerValue={
              privacy.isPrivacyMode ? MASKED_PLAIN : formatMinorUnitsCompact(Math.abs(total))
            }
            centerLabel="Ausgaben"
            testID="berichte-kategorien-ring"
          />

          {visible.map((slice, index) => {
            const change = describeChange(slice.amount, slice.previous);
            const changeColor =
              change?.tone === "warning"
                ? theme.warning
                : change?.tone === "positive"
                  ? theme.positive
                  : theme.textMuted;
            const share =
              magnitude > 0 ? Math.round((Math.abs(slice.amount) / magnitude) * 100) : 0;

            return (
              <DataRow
                key={slice.key}
                leading={
                  <View
                    style={[styles.dot, { backgroundColor: toneColors(theme, slice.tone).fill }]}
                  />
                }
                title={slice.label}
                subtitle={`${share} % der Ausgaben`}
                trailing={
                  <>
                    <Money minorUnits={slice.amount} tone="plain" />
                    {change ? (
                      <Text style={[styles.changeText, { color: changeColor }]} numberOfLines={1}>
                        {change.text}
                      </Text>
                    ) : null}
                  </>
                }
                accessibilityLabel={`${slice.label}, ${formatMinorUnits(slice.amount)}, ${share} Prozent der Ausgaben${
                  change ? `, ${change.text}` : ""
                }. Buchungen anzeigen.`}
                onPress={() =>
                  onDrilldown(slice.label, "Buchungen dieser Kategoriegruppe", {
                    field: "category_group",
                    value: slice.id,
                    from_date: range.from,
                    to_date: range.to,
                    direction: "expense",
                  })
                }
                divider={index < visible.length - 1}
              />
            );
          })}

          {hidden > 0 ? (
            <MoreButton
              label={`Weitere ${hidden} anzeigen`}
              accessibilityLabel={`Alle ${slices.length} Kategoriegruppen anzeigen`}
              onPress={() => setShowAll(true)}
            />
          ) : null}

          {insight ? (
            <Card tone={insight.tone} density="compact">
              <CardLabel>WAS SICH GEÄNDERT HAT</CardLabel>
              <Text style={[styles.insightText, { color: theme.textPrimary }]}>
                {privacy.isPrivacyMode ? INSIGHT_MASKED_TEXT : insight.text}
              </Text>
            </Card>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

function MoreButton({
  label,
  accessibilityLabel,
  onPress,
}: {
  label: string;
  accessibilityLabel: string;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      onPress={onPress}
      style={({ pressed }) => [
        styles.moreButton,
        { backgroundColor: pressed ? theme.surfaceHover : "transparent" },
      ]}
    >
      <Text style={[styles.moreLabel, { color: theme.positive }]}>{label}</Text>
    </Pressable>
  );
}

function RankedRegister({
  title,
  note,
  query,
  dimensionKey,
  groupKey,
  today,
  theme,
  onDrilldown,
}: {
  title: string;
  note: string;
  query: QueryLike<AnalyticsResult>;
  dimensionKey: string;
  groupKey?: string;
  today: Date;
  theme: ThemeTokens;
  onDrilldown: OpenDrilldown;
}) {
  const [showAll, setShowAll] = useState(false);
  const ranked = useMemo(
    () => (query.data ? rankDimensionResult(query.data, dimensionKey, groupKey, today) : null),
    [query.data, dimensionKey, groupKey, today],
  );

  const visible = ranked
    ? showAll
      ? ranked.rows
      : ranked.rows.slice(0, RANKED_PREVIEW_COUNT)
    : [];
  const hidden = ranked ? ranked.rows.length - visible.length : 0;
  const periodText =
    ranked && ranked.months.length === 1
      ? (ranked.months[0]?.label ?? "")
      : `${ranked?.months.length ?? 0} Monate`;

  return (
    <Card>
      <View style={styles.cardHeader}>
        <CardLabel>{title.toUpperCase()}</CardLabel>
        {ranked && ranked.rows.length > 0 ? (
          <Badge label={`${ranked.rows.length} Zeilen`} tone="info" />
        ) : null}
      </View>
      <Text style={[styles.note, { color: theme.textSecondary }]}>{note}</Text>

      {query.isLoading && !ranked ? (
        <InlineQueryState
          status="loading"
          skeletonRows={6}
          skeletonRowHeight={44}
          testID="berichte-rangliste-loading"
        />
      ) : null}
      {query.isError ? (
        <InlineQueryState
          status="error"
          title={`${title} konnten nicht geladen werden`}
          onRetry={() => void query.refetch()}
          testID="berichte-rangliste-error"
        />
      ) : null}
      {ranked && ranked.rows.length === 0 ? (
        <InlineQueryState status="empty" title="Keine Ausgaben im gewählten Zeitraum" />
      ) : null}

      {ranked && ranked.rows.length > 0 ? (
        <View style={styles.totalRow}>
          <Text style={[styles.note, { color: theme.textSecondary }]}>Summe · {periodText}</Text>
          <Money minorUnits={ranked.total} tone="plain" />
        </View>
      ) : null}

      {visible.map((row) => (
        <RankedRowView
          key={row.identityKey}
          row={row}
          theme={theme}
          onPress={() =>
            onDrilldown(row.label, `Buchungen · ${title}`, {
              field: dimensionKey,
              value: row.id,
              from_date: ranked?.months[0]?.from ?? "",
              to_date: ranked?.months.at(-1)?.to ?? "",
              direction: "expense",
            })
          }
        />
      ))}

      {hidden > 0 ? (
        <MoreButton
          label={`Weitere ${hidden} anzeigen`}
          accessibilityLabel={`Alle ${ranked?.rows.length} Zeilen anzeigen`}
          onPress={() => setShowAll(true)}
        />
      ) : null}
    </Card>
  );
}

function RankedRowView({
  row,
  theme,
  onPress,
}: {
  row: RankedRow;
  theme: ThemeTokens;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${row.label}${
        row.groupLabel ? `, ${row.groupLabel}` : ""
      }. Summe ${formatMinorUnits(row.sum)}, Durchschnitt ${formatMinorUnits(
        row.average,
      )} je Monat, ${Math.round(row.share * 100)} Prozent Anteil, ${
        row.count
      } Buchungen. Buchungen anzeigen.`}
      onPress={onPress}
      style={({ pressed }) => [
        styles.rankedRow,
        { backgroundColor: pressed ? theme.surfaceHover : theme.surfaceRaised },
      ]}
    >
      <View style={styles.rankedHead}>
        <View style={styles.rankedLabels}>
          <Text style={[styles.rankedLabel, { color: theme.textPrimary }]} numberOfLines={1}>
            {row.label}
          </Text>
          {row.groupLabel ? (
            <Text style={[styles.rankedGroup, { color: theme.textSecondary }]} numberOfLines={1}>
              {row.groupLabel}
            </Text>
          ) : null}
        </View>
        <Money minorUnits={row.sum} tone="plain" />
      </View>

      <PacingBar
        progress={row.share}
        tone="highlight"
        height={6}
        accessibilityLabel={`Anteil ${row.label}: ${Math.round(row.share * 100)} Prozent`}
      />

      <View style={styles.rankedFooter}>
        <View style={styles.rankedMetaRow}>
          <Text style={[styles.rankedMeta, { color: theme.textSecondary }]}>Ø je Monat</Text>
          <Money minorUnits={row.average} tone="muted" style={styles.rankedMetaValue} />
        </View>
        <Text style={[styles.rankedMeta, { color: theme.textSecondary }]}>
          {Math.round(row.share * 100)} % · {row.count} Buchungen
        </Text>
      </View>

      {row.monthly.length > 1 ? (
        <MonthlyStrip row={row} theme={theme} style={styles.rankedStrip} />
      ) : null}
    </Pressable>
  );
}

function VergleichRegister({
  year,
  onChangeYear,
  periods,
  selected,
  previous,
  theme,
  onDrilldown,
}: {
  year: number;
  onChangeYear: (next: number) => void;
  periods: ReturnType<typeof fairYearPeriods>;
  selected: QueryLike<AnalyticsResult>;
  previous: QueryLike<AnalyticsResult>;
  theme: ThemeTokens;
  onDrilldown: OpenDrilldown;
}) {
  const entries = useMemo(
    () => compareCategoryResults(selected.data, previous.data),
    [selected.data, previous.data],
  );
  const isFuture = year >= new Date().getFullYear();

  if (!periods) {
    return (
      <Card>
        <CardLabel>JAHRESVERGLEICH</CardLabel>
        <InlineQueryState
          status="empty"
          title="Für dieses Jahr gibt es noch keinen fairen Vergleich"
          message="Verglichen werden nur vollständige Monate. Im Januar fehlt dafür die Grundlage."
        />
      </Card>
    );
  }

  const selectedTotal = entries.reduce((sum, entry) => sum + entry.selected, 0);
  const previousTotal = entries.reduce((sum, entry) => sum + entry.previous, 0);
  const delta = selectedTotal - previousTotal;

  return (
    <>
      <Card>
        <CardLabel>ZEITRAUM</CardLabel>
        <View style={styles.yearRow}>
          <IconButton
            icon="chevron-back"
            onPress={() => onChangeYear(year - 1)}
            accessibilityLabel={`Jahr ${year - 1} anzeigen`}
          />
          <Text style={[monoTextStyle, styles.yearCurrent, { color: theme.textPrimary }]}>
            {year}
          </Text>
          <IconButton
            icon="chevron-forward"
            onPress={() => onChangeYear(year + 1)}
            accessibilityLabel={`Jahr ${year + 1} anzeigen`}
            disabled={isFuture}
          />
        </View>
        <Text style={[styles.note, { color: theme.textSecondary }]}>{periods.scopeLabel}</Text>
      </Card>

      <Card accentTop="info">
        <CardLabel>AUSGABEN INSGESAMT</CardLabel>
        {selected.isLoading || previous.isLoading ? (
          <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={40} />
        ) : null}
        {selected.isError || previous.isError ? (
          <InlineQueryState
            status="error"
            title="Der Jahresvergleich konnte nicht geladen werden"
            onRetry={() => {
              void selected.refetch();
              void previous.refetch();
            }}
          />
        ) : null}
        {!selected.isLoading && !selected.isError && entries.length > 0 ? (
          <FigureGrid
            figures={[
              {
                key: "selected",
                label: `${periods.selectedYear}`,
                value: formatMinorUnits(selectedTotal),
                tone: "negative",
                comparison: `${periods.monthCount} Monate`,
                periodHint: "Nur Ausgaben, Transfers aus",
                accessibilityLabel: `Ausgaben ${periods.selectedYear}: ${formatMinorUnits(selectedTotal)}.`,
              },
              {
                key: "previous",
                label: `${periods.previousYear}`,
                value: formatMinorUnits(previousTotal),
                tone: "neutral",
                comparison: "Gleicher Zeitraum im Vorjahr",
                periodHint: "Vergleichsgrundlage",
                accessibilityLabel: `Ausgaben ${periods.previousYear}: ${formatMinorUnits(previousTotal)}.`,
              },
              {
                key: "delta",
                label: "Veränderung",
                value: `${delta > 0 ? "+" : ""}${formatMinorUnits(delta)}`,
                tone: delta > 0 ? "negative" : "positive",
                comparison:
                  previousTotal === 0
                    ? "Kein Vorjahreswert"
                    : `${delta > 0 ? "+" : ""}${((delta / Math.abs(previousTotal)) * 100).toFixed(1)} %`,
                periodHint: delta > 0 ? "Mehr ausgegeben als im Vorjahr" : "Weniger als im Vorjahr",
                accessibilityLabel: `Veränderung: ${delta > 0 ? "plus" : "minus"} ${formatMinorUnits(
                  Math.abs(delta),
                )}.`,
              },
            ]}
          />
        ) : null}
      </Card>

      <Card>
        <CardLabel>GRÖSSTE VERÄNDERUNGEN</CardLabel>
        <Text style={[styles.note, { color: theme.textSecondary }]}>
          Kategorien nach Höhe der Veränderung. Antippen öffnet die Buchungen aus{" "}
          {periods.selectedYear}.
        </Text>
        {!selected.isLoading && entries.length === 0 ? (
          <InlineQueryState status="empty" title="Keine Ausgaben in beiden Zeiträumen" />
        ) : null}
        {entries.slice(0, RANKED_PREVIEW_COUNT).map((entry, index, shown) => (
          <DataRow
            key={entry.identityKey}
            avatarLabel={entry.label}
            avatarTone={toneForLabel(entry.label)}
            title={entry.label}
            subtitle={`${periods.previousYear} → ${periods.selectedYear} · ${entry.deltaPercentLabel}`}
            trailing={
              <ReportValue
                value={entry.deltaLabel}
                tone={entry.delta > 0 ? "negative" : "positive"}
                size={typeScale.medium}
              />
            }
            accessibilityLabel={`${entry.accessibilityLabel} Buchungen anzeigen.`}
            onPress={() =>
              onDrilldown(entry.label, `Buchungen ${periods.selectedYear}`, {
                field: "category",
                value: entry.id,
                from_date: periods.selectedFrom,
                to_date: periods.selectedTo,
                direction: "expense",
              })
            }
            divider={index < shown.length - 1}
          />
        ))}
      </Card>
    </>
  );
}

function FixkostenRegister({
  query,
  theme,
}: {
  query: QueryLike<FixedCostsTrendResponse>;
  theme: ThemeTokens;
}) {
  const mask = useMaskedAmount();
  const overview = useMemo(
    () => (query.data ? describeFixedCosts(query.data.months) : null),
    [query.data],
  );

  if (query.isLoading && !overview) {
    return (
      <Card>
        <CardLabel>FIXKOSTEN</CardLabel>
        <InlineQueryState status="loading" skeletonRows={4} skeletonRowHeight={44} />
      </Card>
    );
  }

  if (query.isError) {
    return (
      <Card>
        <CardLabel>FIXKOSTEN</CardLabel>
        <InlineQueryState
          status="error"
          title="Der Fixkostenverlauf konnte nicht geladen werden"
          onRetry={() => void query.refetch()}
        />
      </Card>
    );
  }

  if (!overview) {
    return (
      <Card>
        <CardLabel>FIXKOSTEN</CardLabel>
        <InlineQueryState
          status="empty"
          title="Noch keine Verträge erfasst"
          message="Sobald Miete, Versicherungen oder Abonnements angelegt sind, steht hier ihre Entwicklung — aus dem vereinbarten Betrag, nicht aus den Buchungen."
        />
      </Card>
    );
  }

  return (
    <>
      <Card accentTop="info">
        <View style={styles.cardHeader}>
          <CardLabel>VEREINBART</CardLabel>
          <Badge label={overview.latestMonthLabel} tone="info" />
        </View>
        <Text style={[styles.note, { color: theme.textSecondary }]}>
          Maßgeblich ist der zum Monatsletzten vereinbarte Preis, nicht die Buchung. Eine
          Beitragserhöhung ist damit sichtbar, bevor die erste höhere Abbuchung ankommt.
        </Text>
        <FigureGrid figures={overview.figures} />
      </Card>

      <Card>
        <CardLabel>NACH VERTRAGSART</CardLabel>
        {overview.types.length === 0 ? (
          <InlineQueryState status="empty" title="Für diesen Monat ist kein Vertrag hinterlegt" />
        ) : null}
        {overview.types.map((entry, index) => (
          <DataRow
            key={entry.key}
            avatarLabel={entry.label}
            avatarTone={toneForLabel(entry.label)}
            title={entry.label}
            subtitle={`${mask(entry.yearlyLabel)} pro Jahr · Anteil ${entry.shareLabel}`}
            trailing={
              <ReportValue value={entry.monthlyLabel} tone="neutral" size={typeScale.medium} />
            }
            accessibilityLabel={`${entry.label}: ${entry.monthlyLabel} pro Monat, ${entry.yearlyLabel} pro Jahr, Anteil ${entry.shareLabel}.`}
            divider={index < overview.types.length - 1}
          />
        ))}
      </Card>

      <Card>
        <CardLabel>MONAT FÜR MONAT</CardLabel>
        <Text style={[styles.note, { color: theme.textSecondary }]}>
          Auch pausierte und beendete Verträge stehen in den Monaten, in denen sie liefen — sonst
          sähe jede Steigerung kleiner aus, als sie war. Hier führt kein Weg zu Buchungen: eine
          Jahresprämie ist in zwölf Monaten vereinbart und in genau einem gebucht.
        </Text>
        {overview.months.map((month, index) => (
          <DataRow
            key={month.key}
            avatarIcon="document-text-outline"
            avatarTone="info"
            title={month.title}
            subtitle={`${month.contractCount} Vertrag/Verträge · ggü. Vorjahr ${month.yoyLabel}`}
            trailing={
              <ReportValue value={month.amountLabel} tone="neutral" size={typeScale.medium} />
            }
            accessibilityLabel={month.accessibilityLabel}
            divider={index < overview.months.length - 1}
          />
        ))}
      </Card>
    </>
  );
}

function VorhabenRegister({
  query,
  theme,
}: {
  query: QueryLike<ProjectReportRow[]>;
  theme: ThemeTokens;
}) {
  const mask = useMaskedAmount();
  const overview = useMemo(
    () => (query.data ? describeProjectReports(query.data) : null),
    [query.data],
  );

  if (query.isLoading && !overview) {
    return (
      <Card>
        <CardLabel>VORHABEN</CardLabel>
        <InlineQueryState status="loading" skeletonRows={3} skeletonRowHeight={44} />
      </Card>
    );
  }

  if (query.isError) {
    return (
      <Card>
        <CardLabel>VORHABEN</CardLabel>
        <InlineQueryState
          status="error"
          title="Die Vorhabenübersicht konnte nicht geladen werden"
          onRetry={() => void query.refetch()}
        />
      </Card>
    );
  }

  if (!overview) {
    return (
      <Card>
        <CardLabel>VORHABEN</CardLabel>
        <InlineQueryState
          status="empty"
          title="Kein aktives Vorhaben mit ausgewähltem Szenario"
          message="Sobald ein Vorhaben angelegt und ein Szenario ausgewählt ist, stehen hier Forecast, Ist, Abweichung und Finanzierungskosten."
        />
      </Card>
    );
  }

  return (
    <>
      <Card accentTop="purple">
        <View style={styles.cardHeader}>
          <CardLabel>ALLE VORHABEN</CardLabel>
          <Badge label={`${overview.projectCount} aktiv`} tone="purple" />
        </View>
        <Text style={[styles.note, { color: theme.textSecondary }]}>
          Getrennt vom Cashflow: eine Bausumme ist keine Buchung, sondern eine Kennzahl aus dem
          Vorhaben-Modul.
        </Text>
        <FigureGrid figures={overview.figures} />
      </Card>

      <Card>
        <CardLabel>IM DETAIL</CardLabel>
        {overview.entries.map((entry, index) => (
          <View key={entry.key} style={styles.projectBlock}>
            <DataRow
              avatarLabel={entry.name}
              avatarTone={toneForLabel(entry.name)}
              title={entry.name}
              subtitle={`Szenario ${entry.scenarioName}`}
              titleBadge={
                entry.warningCount > 0 ? (
                  <Badge label={`${entry.warningCount} Warnung(en)`} tone="warning" size="small" />
                ) : undefined
              }
              trailing={
                <ReportValue
                  value={entry.varianceLabel}
                  tone={entry.varianceIsNegative ? "negative" : "positive"}
                  size={typeScale.medium}
                />
              }
              accessibilityLabel={entry.accessibilityLabel}
              divider={false}
            />
            <View style={styles.projectDetail}>
              <Text style={[styles.rankedMeta, { color: theme.textSecondary }]}>
                Forecast {mask(entry.forecastLabel)}
              </Text>
              <Text style={[styles.rankedMeta, { color: theme.textSecondary }]}>
                Ist {mask(entry.actualLabel)}
              </Text>
              <Text style={[styles.rankedMeta, { color: theme.textSecondary }]}>
                Finanzierung {mask(entry.financingLabel)}
              </Text>
            </View>
            {index < overview.entries.length - 1 ? (
              <View style={[styles.projectDivider, { backgroundColor: theme.border }]} />
            ) : null}
          </View>
        ))}
      </Card>
    </>
  );
}

/**
 * Die Buchungen hinter einer Zahl.
 *
 * Bewusst derselbe Analytics-Endpunkt wie die Summe darueber (siehe
 * drilldownRowsQuery): ein eigener Detailpfad koennte anders filtern, und dann
 * widersprechen sich Summe und Liste, ohne dass es auffaellt.
 */
function DrilldownSheet({
  request,
  theme,
  onClose,
}: {
  request: DrilldownRequest;
  theme: ThemeTokens;
  onClose: () => void;
}) {
  const router = useRouter();
  const query = useReportQuery("drilldown", request.query, true);
  const rows = query.data?.rows ?? [];

  const openTransaction = (row: AnalyticsRow) => {
    const id = row.transaction_id;
    if (typeof id !== "string") return;
    onClose();
    router.push(`/buchung/${id}`);
  };

  const cellText = (row: AnalyticsRow, key: string): string => {
    const label = row[`${key}_label`];
    if (typeof label === "string" && label.trim()) return label;
    const raw = row[key];
    return raw === null || raw === undefined || raw === "" ? "" : String(raw);
  };

  return (
    <SheetModal visible title={request.title} onClose={onClose} size="full">
      <ScrollView contentContainerStyle={styles.sheetContent}>
        <Text style={[styles.note, { color: theme.textSecondary }]}>{request.subtitle}</Text>

        {query.isLoading ? (
          <InlineQueryState
            status="loading"
            skeletonRows={6}
            skeletonRowHeight={44}
            testID="berichte-drilldown-loading"
          />
        ) : null}
        {query.isError ? (
          <InlineQueryState
            status="error"
            title="Die Buchungen konnten nicht geladen werden"
            onRetry={() => void query.refetch()}
            testID="berichte-drilldown-error"
          />
        ) : null}
        {!query.isLoading && !query.isError && rows.length === 0 ? (
          <InlineQueryState status="empty" title="Keine Buchungen für diesen Ausschnitt" />
        ) : null}

        {rows.map((row, index) => {
          const amount = Number(row.amount ?? 0);
          const payee = cellText(row, "payee") || cellText(row, "purpose") || "Ohne Empfänger";
          const date = cellText(row, "date");
          const category = cellText(row, "category") || "Ohne Kategorie";
          return (
            <DataRow
              key={typeof row.split_id === "string" ? row.split_id : `row-${index}`}
              avatarLabel={payee}
              avatarTone={toneForLabel(payee)}
              title={payee}
              subtitle={`${date ? formatShortDate(date) : "—"} · ${category}`}
              trailing={<Money minorUnits={amount} />}
              accessibilityLabel={`${payee}, ${formatMinorUnits(amount)}, ${category}. Buchung öffnen.`}
              onPress={() => openTransaction(row)}
              divider={index < rows.length - 1}
            />
          );
        })}

        {/* Kein stilles Abschneiden: wird die Obergrenze erreicht, steht das da. */}
        {rows.length >= DRILLDOWN_ROW_LIMIT ? (
          <Text style={[styles.note, { color: theme.warning }]}>
            Nur die ersten {DRILLDOWN_ROW_LIMIT} Buchungen. Für den vollständigen Ausschnitt den
            Explorer im Web verwenden.
          </Text>
        ) : null}
      </ScrollView>
    </SheetModal>
  );
}

const styles = StyleSheet.create({
  content: {
    paddingBottom: space.xxxl,
    flexGrow: 1,
  },
  body: {
    paddingHorizontal: space.lg,
    paddingTop: space.sm,
    gap: space.md,
  },
  scope: {
    fontSize: typeScale.micro,
  },
  note: {
    fontSize: typeScale.caption,
    lineHeight: leading(typeScale.caption),
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
    flexWrap: "wrap",
  },
  totalRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  reportValue: {
    fontWeight: "800",
  },
  figureGrid: {
    gap: space.sm,
  },
  tileRow: {
    flexDirection: "row",
    gap: space.sm,
  },
  tileFiller: {
    flex: 1,
  },
  figureHint: {
    fontSize: typeScale.micro,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: radii.pill,
  },
  insightText: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
  changeText: {
    fontSize: typeScale.micro,
    fontWeight: "600",
  },
  moreButton: {
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.md,
  },
  moreLabel: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  rankedRow: {
    borderRadius: radii.md,
    padding: space.md,
    gap: space.sm,
  },
  rankedHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: space.sm,
  },
  rankedLabels: {
    flexShrink: 1,
  },
  rankedLabel: {
    fontSize: typeScale.base,
    fontWeight: "700",
    flexShrink: 1,
  },
  rankedGroup: {
    fontSize: typeScale.micro,
  },
  rankedFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: space.sm,
    flexWrap: "wrap",
  },
  rankedMetaRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.xs,
  },
  rankedMeta: {
    fontSize: typeScale.micro,
    flexShrink: 1,
  },
  rankedMetaValue: {
    fontSize: typeScale.micro,
  },
  rankedStrip: {
    marginTop: space.xs,
  },
  strip: {
    flexDirection: "row",
    alignItems: "flex-end",
    height: 28,
    gap: space.hair,
  },
  stripSlot: {
    flex: 1,
    height: "100%",
    justifyContent: "flex-end",
  },
  stripBar: {
    width: "100%",
    borderRadius: 1,
  },
  yearRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: space.sm,
  },
  yearCurrent: {
    fontSize: typeScale.title,
    fontWeight: "800",
  },
  projectBlock: {
    gap: space.xs,
  },
  projectDetail: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: space.md,
  },
  projectDivider: {
    height: StyleSheet.hairlineWidth,
    marginTop: space.sm,
  },
  sheetContent: {
    paddingHorizontal: space.lg,
    paddingBottom: space.xxxl,
  },
});
